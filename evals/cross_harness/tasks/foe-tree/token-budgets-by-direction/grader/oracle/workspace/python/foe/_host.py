"""The host side of docs/protocol.md.

`start_config` launches the binary on a configuration document, reads its
standard output line by line, and answers `model/request` with the
embedding program's transport and `host/tool-call` with its host tools.
Every line read is a log event; every line written is one of the four
host-to-foe line types.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Mapping

from ._capabilities import PathLike
from ._errors import BinaryError, ProtocolError
from ._outcome import Event, Failed, Outcome, outcome_from_json
from ._tools import Capabilities, HostTool, ToolResult

Transport = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]
EventCallback = Callable[[Event], None]

# Upper bound on one protocol line. A `model/request` carries the whole
# derived conversation, so lines grow with the episode.
_LINE_LIMIT = 1 << 30


def _load_config(config: Mapping[str, Any] | PathLike) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    path = Path(os.fspath(config))
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: the configuration document must be a JSON object")
    return loaded


def _collect_host_tool_names(doc: Mapping[str, Any]) -> set[str]:
    names = set(doc.get("host_tools") or {})
    for child in (doc.get("programs") or {}).values():
        names |= _collect_host_tool_names(child)
    return names


class Handle:
    """A running episode.

    `wait` returns the outcome. `steer` appends to the episode's inbox with
    source `parent`. `cancel` asks the runtime to stop and returns the
    outcome it records, which is `Failed("cancelled")`.
    """

    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        config: Mapping[str, Any],
        config_dir: str,
        log_dir: Path,
        transport: Transport,
        tools: Mapping[str, HostTool],
        on_event: EventCallback | None,
        max_output_tokens: int | None,
    ) -> None:
        self._process = process
        self._config = config
        self._config_dir = config_dir
        self.log_dir = log_dir
        self._transport = transport
        self._tools = tools
        self._on_event = on_event
        self._max_output_tokens = max_output_tokens
        self._headers: dict[tuple[str | None, int], dict[str, Any]] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._write_lock = asyncio.Lock()
        self._outcome: Outcome | None = None
        self.episode_id: str | None = None
        self.runtime_version: str | None = None
        self._reader = asyncio.create_task(self._read_loop(), name="foe-host-reader")

    # ---- public -------------------------------------------------------------

    @property
    def outcome(self) -> Outcome | None:
        return self._outcome

    @property
    def done(self) -> bool:
        return self._reader.done()

    async def wait(self) -> Outcome:
        await asyncio.shield(self._reader)
        assert self._outcome is not None
        return self._outcome

    async def steer(self, text: str) -> None:
        """Append a message with source `parent`; it enters the next request."""
        await self._write(
            {
                "type": "inbox/item",
                "source": "parent",
                "content": [{"type": "text", "text": text}],
                "from": None,
                "message_id": None,
            }
        )

    async def cancel(self) -> Outcome:
        """Stop the episode and return the outcome the runtime records."""
        if not self.done:
            await self._write({"type": "cancel"})
        return await self.wait()

    # ---- protocol ----------------------------------------------------------------

    async def _write(self, obj: Mapping[str, Any]) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            return
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            try:
                stdin.write(line.encode("utf-8"))
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                return

    async def _read_loop(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        line_number = 0
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                line_number += 1
                try:
                    obj = json.loads(raw)
                    event = Event.from_json(obj)
                except (ValueError, KeyError, TypeError) as exc:
                    self._outcome = Failed(f"protocol: line {line_number} from foe is not a log event: {exc}")
                    await self._write({"type": "cancel"})
                    break
                if self._on_event is not None:
                    self._on_event(event)
                if self._dispatch(event):
                    break
        finally:
            await self._shutdown()

    def _dispatch(self, event: Event) -> bool:
        """Handle one event. Returns True when the root episode has ended."""
        tag = event.episode_id
        if event.type == "episode/start" and tag is None:
            self.episode_id = str(event.data.get("id"))
            runtime = event.data.get("runtime") or {}
            self.runtime_version = str(runtime.get("version", ""))
        elif event.type == "request/header":
            self._headers[(tag, event.seq)] = event.data
        elif event.type == "model/request":
            self._spawn(self._serve_model(event))
        elif event.type == "host/tool-call":
            self._spawn(self._serve_tool(event))
        elif event.type == "episode/end" and tag is None:
            self._outcome = outcome_from_json(event.data.get("outcome") or {})
            return True
        return False

    def _spawn(self, coro: Any) -> None:
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _serve_model(self, event: Event) -> None:
        data = event.data
        request_id = str(data["request_id"])
        tag = event.episode_id

        def envelope(chunk: Mapping[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {"type": "model/chunk", "request_id": request_id}
            if tag is not None:
                out["episode_id"] = tag
            out["chunk"] = dict(chunk)
            return out

        header = self._headers.get((tag, int(data["header_seq"])))
        if header is None:
            message = f"model/request {request_id}: request/header {data['header_seq']} was never received"
            await self._write(envelope({"kind": "error", "message": message, "retryable": False}))
            return
        caps = [cap for cap in (self._max_output_tokens, data.get("max_output_tokens")) if isinstance(cap, int)]
        body: dict[str, Any] = {
            "request_id": request_id,
            "system": header.get("system", ""),
            "tools": list(header.get("tools") or []),
            "messages": list(data.get("messages") or []),
            "max_output_tokens": min(caps) if caps else None,
        }
        terminal = False
        try:
            async for chunk in self._transport(body):
                if terminal:
                    raise ProtocolError("transport yielded a chunk after done or error")
                kind = chunk.get("kind")
                if kind in ("done", "error"):
                    terminal = True
                await self._write(envelope(chunk))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the runtime decides what a failed request means
            if not terminal:
                message = f"{type(exc).__name__}: {exc}"
                await self._write(envelope({"kind": "error", "message": message, "retryable": False}))
            return
        if not terminal:
            message = "transport ended without a done or error chunk"
            await self._write(envelope({"kind": "error", "message": message, "retryable": False}))

    async def _serve_tool(self, event: Event) -> None:
        data = event.data
        call_id = str(data["call_id"])
        name = str(data["name"])
        args = data.get("args") or {}
        tool = self._tools.get(name)
        if tool is None:
            result = ToolResult(
                value={"error": f"host tool {name!r} is not registered"},
                rendered=f"host tool {name!r} is not registered",
                is_error=True,
            )
        else:
            result = await tool.invoke(args, self._capabilities())
        answer: dict[str, Any] = {"type": "tool/result", "call_id": call_id}
        if event.episode_id is not None:
            answer["episode_id"] = event.episode_id
        answer["value"] = result.value
        if result.rendered is not None:
            answer["rendered"] = result.rendered
        answer["is_error"] = result.is_error
        await self._write(answer)

    def _capabilities(self) -> Capabilities:
        grants = self._config.get("grants") or {}
        tool_defs = self._config.get("tool_defs") or {}
        return Capabilities(
            read_roots=list(grants.get("read") or []),
            write_roots=list(grants.get("write") or []),
            executables=[d["exec"] for d in tool_defs.values()],
        )

    async def _shutdown(self) -> None:
        for task in list(self._pending):
            task.cancel()
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        stdin = self._process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()
        try:
            code = await asyncio.wait_for(self._process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self._process.kill()
            code = await self._process.wait()
        if self._outcome is None:
            self._outcome = Failed(f"foe exited with code {code} before episode/end")
        shutil.rmtree(self._config_dir, ignore_errors=True)


async def start_config(
    config: Mapping[str, Any] | PathLike,
    *,
    transport: Transport,
    binary: PathLike,
    log_dir: PathLike,
    tools: Iterable[HostTool] = (),
    on_event: EventCallback | None = None,
    max_output_tokens: int | None = None,
) -> Handle:
    """Launch the binary on a complete configuration document.

    `config` is a document as a dict or the path of a JSON file. It must
    carry `task` and no `model` block, since this host supplies the
    transport. `tools` supplies the implementation of every name in the
    document's `host_tools`.
    """
    doc = _load_config(config)
    if "model" in doc:
        raise ValueError("config: a document run through this host must have no `model` block")
    if "task" not in doc:
        raise ValueError("config: `task` is required")
    by_name = {t.name: t for t in tools}
    missing = sorted(_collect_host_tool_names(doc) - set(by_name))
    if missing:
        raise ValueError(f"host_tools: no implementation was supplied for {', '.join(missing)}")

    log_path = Path(os.fspath(log_dir))
    log_path.mkdir(parents=True, exist_ok=True)
    config_dir = tempfile.mkdtemp(prefix="foe-config-")
    config_path = Path(config_dir) / "config.json"
    config_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    try:
        process = await asyncio.create_subprocess_exec(
            os.fspath(binary),
            "--config",
            str(config_path),
            "--host",
            "--log-dir",
            str(log_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=_LINE_LIMIT,
        )
    except OSError as exc:
        shutil.rmtree(config_dir, ignore_errors=True)
        raise BinaryError(f"{os.fspath(binary)}: {exc}") from exc
    return Handle(
        process=process,
        config=doc,
        config_dir=config_dir,
        log_dir=log_path,
        transport=transport,
        tools=by_name,
        on_event=on_event,
        max_output_tokens=max_output_tokens,
    )


async def run_config(
    config: Mapping[str, Any] | PathLike,
    *,
    transport: Transport,
    binary: PathLike,
    log_dir: PathLike,
    tools: Iterable[HostTool] = (),
    on_event: EventCallback | None = None,
    max_output_tokens: int | None = None,
) -> Outcome:
    """Run a complete configuration document to its outcome."""
    handle = await start_config(
        config,
        transport=transport,
        binary=binary,
        log_dir=log_dir,
        tools=tools,
        on_event=on_event,
        max_output_tokens=max_output_tokens,
    )
    return await handle.wait()


@dataclass(slots=True)
class Viewer:
    """A running `foe view --serve` process. `url` is what it printed."""

    url: str
    process: asyncio.subprocess.Process

    def __str__(self) -> str:
        return self.url

    async def close(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


async def serve(log_dir: PathLike, *, binary: PathLike) -> Viewer:
    """Serve a log directory through the binary's viewer, `foe view DIR --serve`.

    The binary prints the URL as its first line of standard output, either
    bare or as a JSON object with a `url` key.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            os.fspath(binary),
            "view",
            os.fspath(log_dir),
            "--serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise BinaryError(f"{os.fspath(binary)}: {exc}") from exc
    assert process.stdout is not None
    first = (await process.stdout.readline()).decode("utf-8").strip()
    if not first:
        code = await process.wait()
        raise BinaryError(f"foe view --serve exited with code {code} before printing a URL")
    url = first
    if first.startswith("{"):
        try:
            parsed = json.loads(first)
            url = str(parsed["url"])
        except (ValueError, KeyError, TypeError) as exc:
            raise BinaryError(f"foe view --serve printed an object without a url: {first}") from exc
    return Viewer(url=url, process=process)
