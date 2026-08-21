"""A transport over `litellm.acompletion(stream=True)` with tool calling.

`litellm_transport(model=..., api_key=...)` returns an async callable that
receives the request dict docs/sdk.md describes and yields `model/chunk`
chunk objects as docs/protocol.md defines them. The `litellm` package is
imported when the transport is constructed; install it with
`pip install foe[litellm]`.

Credentials are passed explicitly. The adapter reads no environment
variable; whether the provider library falls back to one is the embedding
program's decision.
"""

from __future__ import annotations

import importlib
import json
from typing import Any, AsyncIterator, Mapping

from .._host import Transport

_RETRYABLE_ERRORS = (
    "RateLimitError",
    "Timeout",
    "APIConnectionError",
    "ServiceUnavailableError",
    "InternalServerError",
)


def _to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        role = message.get("role")
        if role == "user":
            content: list[dict[str, Any]] = []
            for block in message.get("content") or []:
                if block.get("type") == "text":
                    content.append({"type": "text", "text": block.get("text", "")})
                elif block.get("type") == "image":
                    url = f"data:{block.get('media_type', 'image/png')};base64,{block.get('data', '')}"
                    content.append({"type": "image_url", "image_url": {"url": url}})
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.get("text") or None}
            calls = message.get("tool_calls") or []
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": json.dumps(call.get("args") or {})},
                    }
                    for call in calls
                ]
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message["call_id"],
                    "name": message.get("name"),
                    "content": message.get("rendered", ""),
                }
            )
    return out


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def _stop_reason(finish_reason: str | None, saw_tool_call: bool) -> str:
    if finish_reason == "length":
        return "length"
    if finish_reason == "tool_calls" or saw_tool_call:
        return "tool"
    return "end"


def _field(obj: Any, key: str) -> Any:
    """A field of a provider object, whether it is a mapping or an attribute bag."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _usage(raw: Any) -> dict[str, int]:
    details = _field(raw, "prompt_tokens_details")
    return {
        "input": int(_field(raw, "prompt_tokens") or 0),
        "output": int(_field(raw, "completion_tokens") or 0),
        "cache_read": int(_field(details, "cached_tokens") or 0),
    }


def litellm_transport(
    model: str,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    **completion_kwargs: Any,
) -> Transport:
    """Build a transport that calls `model` through litellm.

    `api_key` and `api_base` are passed to every completion call. Further
    keyword arguments are forwarded unchanged, so provider options such as
    `temperature` or `extra_headers` are available.
    """
    litellm: Any = importlib.import_module("litellm")

    async def transport(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        kwargs: dict[str, Any] = dict(completion_kwargs)
        kwargs.update(
            model=model,
            messages=_to_openai_messages(request.get("system", ""), list(request.get("messages") or [])),
            stream=True,
            stream_options={"include_usage": True},
        )
        tools = _to_openai_tools(list(request.get("tools") or []))
        if tools:
            kwargs["tools"] = tools
        if request.get("max_output_tokens") is not None:
            kwargs["max_tokens"] = request["max_output_tokens"]
        if api_key is not None:
            kwargs["api_key"] = api_key
        if api_base is not None:
            kwargs["api_base"] = api_base

        open_calls: dict[int, str] = {}
        finish_reason: str | None = None
        usage: Any = None
        try:
            stream = await litellm.acompletion(**kwargs)
            async for part in stream:
                if getattr(part, "usage", None) is not None:
                    usage = part.usage
                choices = getattr(part, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield {"kind": "thinking", "delta": reasoning}
                text = getattr(delta, "content", None)
                if text:
                    yield {"kind": "text", "delta": text}
                for call in getattr(delta, "tool_calls", None) or []:
                    index = int(getattr(call, "index", 0) or 0)
                    function = getattr(call, "function", None)
                    if index not in open_calls:
                        call_id = getattr(call, "id", None) or f"call_{index}"
                        open_calls[index] = call_id
                        yield {
                            "kind": "tool_call_start",
                            "id": call_id,
                            "name": getattr(function, "name", None) or "",
                        }
                    arguments = getattr(function, "arguments", None)
                    if arguments:
                        yield {"kind": "tool_call_delta", "id": open_calls[index], "delta": arguments}
        except Exception as exc:  # noqa: BLE001 - every provider failure becomes an error chunk
            retryable = type(exc).__name__ in _RETRYABLE_ERRORS
            yield {"kind": "error", "message": f"{type(exc).__name__}: {exc}", "retryable": retryable}
            return
        for call_id in open_calls.values():
            yield {"kind": "tool_call_end", "id": call_id}
        yield {
            "kind": "done",
            "stop": _stop_reason(finish_reason, bool(open_calls)),
            "usage": _usage(usage),
        }

    return transport
