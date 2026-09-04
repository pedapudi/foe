"""A deterministic streaming HTTP endpoint for examples and binary tests."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Sequence

USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
StreamResponse = list[dict[str, Any]]


@dataclass(frozen=True)
class Request:
    """One request received by the loopback endpoint."""

    path: str
    headers: dict[str, str]
    body: dict[str, Any]


def _choice(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


def tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> StreamResponse:
    """A streamed tool call followed by its usage."""
    encoded = json.dumps(arguments, separators=(",", ":"))
    split = max(1, len(encoded) // 2)
    start = {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": encoded[:split]},
    }
    continuation = {"index": 0, "function": {"arguments": encoded[split:]}}
    return [
        _choice({"tool_calls": [start]}),
        _choice({"tool_calls": [continuation]}),
        _choice({}, "tool_calls"),
        {"choices": [], "usage": USAGE},
    ]


def text_response(text: str) -> StreamResponse:
    """A streamed text response followed by its usage."""
    return [
        _choice({"content": text}),
        _choice({}, "stop"),
        {"choices": [], "usage": USAGE},
    ]


class ScriptedHttpEndpoint:
    """Serves one streaming response for each request and records the requests."""

    def __init__(self, responses: Sequence[StreamResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[Request] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - the base class defines this name
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length))
                endpoint.requests.append(
                    Request(
                        path=self.path,
                        headers={name.lower(): value for name, value in self.headers.items()},
                        body=body,
                    )
                )
                if not endpoint._responses:
                    self._send(400, "application/json", json.dumps({"error": {"message": "script exhausted"}}))
                    return
                events = "".join(
                    f"data: {json.dumps(event, separators=(',', ':'))}\n\n" for event in endpoint._responses.pop(0)
                )
                self._send(200, "text/event-stream", events + "data: [DONE]\n\n")

            def _send(self, status: int, content_type: str, body: str) -> None:
                encoded = body.encode()
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(encoded)))
                self.send_header("connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: Any) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    @property
    def base_url(self) -> str:
        """The endpoint origin and version prefix."""
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    @property
    def remaining(self) -> int:
        """The number of responses that have not been served."""
        return len(self._responses)

    def __enter__(self) -> ScriptedHttpEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
