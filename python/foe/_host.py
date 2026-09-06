"""The host side of docs/protocol.md.

`start_config` launches the binary on a configuration document, reads its
standard output line by line, and answers `host/tool-call` with the
embedding contract's host tools. It also answers `model/request` with the
contract's model backend when the document leaves the model to the host, which
docs/config.md makes the meaning of a document with no `model` block. Every
line read is a log event; every line written is one of the four host-to-foe
line types.
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
from ._errors import BinaryError, CompatibilityError, ProtocolError
from ._outcome import Event, Failed, Outcome, Runtime, outcome_from_json
from ._tools import Capabilities, HostTool, ToolResult
from ._versions import LOG_FORMAT_VERSION, PROTOCOL_VERSION, UNSTATED_LOG_FORMAT_VERSION, protocol_agrees

ModelBackend = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]
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
    for child in (doc.get("child_contracts") or {}).values():
        names |= _collect_host_tool_names(child)
    return names


def _descendant_contracts_with_a_model_block(doc: Mapping[str, Any], prefix: str = "") -> list[str]:
    """The contract paths below `doc` that declare a `model` block."""
    found: list[str] = []
    for key, child in sorted((doc.get("child_contracts") or {}).items()):
        name = f"{prefix}child_contracts.{key}"
        if "model" in child:
            found.append(name)
        found.extend(_descendant_contracts_with_a_model_block(child, f"{name}."))
    found.extend(_workflow_contracts_with_a_model_block(doc.get("workflow"), f"{prefix}workflow."))
    return found


def _workflow_contracts_with_a_model_block(workflow: Any, prefix: str) -> list[str]:
    """The model contracts in one workflow tree that select their own model."""
    if not isinstance(workflow, Mapping):
        return []
    found: list[str] = []
    nodes = workflow.get("nodes")
    if not isinstance(nodes, Mapping):
        return found
    for key, node in sorted(nodes.items()):
        if not isinstance(node, Mapping):
            continue
        model_contract = node.get("model")
        if isinstance(model_contract, Mapping):
            name = f"{prefix}nodes.{key}.model"
            if "model" in model_contract:
                found.append(name)
            found.extend(_descendant_contracts_with_a_model_block(model_contract, f"{name}."))
        found.extend(_workflow_contracts_with_a_model_block(node.get("workflow"), f"{prefix}nodes.{key}.workflow."))
    return found


def _pairing_error(first: Event) -> str | None:
    """Why the binary that wrote `first` does not pair with this package, or None.

    docs/protocol.md "Versioning" gives the host this duty: recognize the
    version the binary states, and cancel the episode when it does not.
    """
    if first.type != "episode/start":
        return f"protocol: the first line is {first.type!r}, and docs/protocol.md makes it episode/start"
    stated = UNSTATED_LOG_FORMAT_VERSION if first.version is None else first.version
    if stated != LOG_FORMAT_VERSION:
        return f"log format: the binary writes version {stated} and this package reads version {LOG_FORMAT_VERSION}"
    version = str((first.data.get("runtime") or {}).get("version", ""))
    if not protocol_agrees(version):
        return (
            f"protocol: the binary states runtime version {version!r} and this package speaks "
            f"the protocol of runtime {PROTOCOL_VERSION}"
        )
    return None


class Handle:
    """A running episode.

    `wait` returns the outcome. `steer` appends to the episode's inbox with
    source `parent`. `cancel` asks the runtime to stop and returns the
    outcome it records, which is `Failed("cancelled")`.

    `pid` is the process id of the binary, and `runtime` is the build
    identity that binary stated. A handle is returned only after
    `episode/start`, so a supervisor holds both before the episode's first
    request and first tool call.

    `log_dir` is the directory the binary created for this episode, which is
    the episode id under the directory the caller named. It holds
    `episode.jsonl` and every descendant's log.
    """

    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        config: Mapping[str, Any],
        config_dir: str,
        log_parent: Path,
        model_backend: ModelBackend | None,
        tools: Mapping[str, HostTool],
        on_event: EventCallback | None,
        max_output_tokens: int | None,
    ) -> None:
        self._process = process
        self._config = config
        self._config_dir = config_dir
        self._log_parent = log_parent
        self.log_dir: Path | None = None
        self._model_backend = model_backend
        self._tools = tools
        self._on_event = on_event
        self._max_output_tokens = max_output_tokens
        self._headers: dict[tuple[str | None, int], dict[str, Any]] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._write_lock = asyncio.Lock()
        self._outcome: Outcome | None = None
        self._pairing_error: str | None = None
        # Set when `episode/start` has been read, and again when the reader
        # stops, so that a wait for the start cannot outlive the process.
        self._start_known = asyncio.Event()
        self.episode_id: str | None = None
        self.runtime: Runtime | None = None
        self._reader = asyncio.create_task(self._read_loop(), name="foe-host-reader")

    # ---- public -------------------------------------------------------------

    @property
    def pid(self) -> int:
        """The process id of the binary running this episode."""
        return self._process.pid

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

    async def _await_start(self) -> str | None:
        """Wait for `episode/start`, and report why the binary does not pair.

        Returns None once `episode_id` and `runtime` hold what the binary
        stated, and otherwise the reason the two versions disagree, after
        the cancelled episode has settled.
        """
        await self._start_known.wait()
        if self._pairing_error is not None:
            await self.wait()
        return self._pairing_error

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
                if line_number == 1:
                    self._pairing_error = _pairing_error(event)
                    if self._pairing_error is not None:
                        self._outcome = Failed(self._pairing_error)
                        await self._write({"type": "cancel"})
                        break
                if self._on_event is not None:
                    self._on_event(event)
                if self._dispatch(event):
                    break
        finally:
            await self._shutdown()

    def _dispatch(self, event: Event) -> bool:
        """Handle one event. Returns True when the root episode has ended.

        A `model/request` is answered only when this host supplies the model
        backend. When the document has a `model` block, foe calls the
        configured endpoint and writes the event for the record, which
        docs/protocol.md "Launch" states.
        """
        tag = event.episode_id
        if event.type == "episode/start" and tag is None:
            self.episode_id = str(event.data.get("id"))
            self.log_dir = self._log_parent / self.episode_id
            runtime = event.data.get("runtime") or {}
            self.runtime = Runtime(str(runtime.get("version", "")), str(runtime.get("build", "")))
            self._start_known.set()
        elif event.type == "request/header":
            self._headers[(tag, event.seq)] = event.data
        elif event.type == "model/request" and self._model_backend is not None:
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
        assert self._model_backend is not None
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
            async for chunk in self._model_backend(body):
                if terminal:
                    raise ProtocolError("model backend yielded a chunk after done or error")
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
            message = "model backend ended without a done or error chunk"
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
        self._start_known.set()
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
    model_backend: ModelBackend | None = None,
    binary: PathLike,
    log_dir: PathLike,
    tools: Iterable[HostTool] = (),
    on_event: EventCallback | None = None,
    max_output_tokens: int | None = None,
) -> Handle:
    """Launch the binary on a complete configuration document.

    `log_dir` is the directory the episode's own directory is created
    under, which docs/design.md "The command line" states for `--log-dir`.
    The handle names the created directory once the episode has started.

    `config` is a document as a dict or the path of a JSON file, and must
    carry `task`. The document's `model` block decides who calls the model,
    which docs/config.md `model` states: a document without one leaves the
    model to the host and requires `model_backend`. A document with one
    directs foe to call the configured endpoint, so `model_backend` must be
    absent.
    The host registers and services the document's `host_tools` either way.
    `tools` supplies the implementation of every name in `host_tools`.

    Returns once the binary has written `episode/start`, so the handle
    carries the process id and the runtime build before the first request.
    Raises `CompatibilityError` when the binary does not pair with this
    package.
    """
    doc = _load_config(config)
    if "task" not in doc:
        raise ValueError("config: `task` is required")
    host_calls_the_model = "model" not in doc
    if host_calls_the_model and model_backend is None:
        raise ValueError(
            "config: a document with no `model` block leaves the model to this host, which needs a model backend"
        )
    if not host_calls_the_model and model_backend is not None:
        raise ValueError(
            "config: a document with a `model` block directs foe to call the configured endpoint, "
            "which takes no model backend"
        )
    nested = _descendant_contracts_with_a_model_block(doc) if host_calls_the_model else []
    if nested:
        raise ValueError(
            f"model: {', '.join(nested)} declares a `model` block while the document leaves the "
            "model to this host; one owner must serve model calls throughout the contract tree"
        )
    by_name = {t.name: t for t in tools}
    missing = sorted(_collect_host_tool_names(doc) - set(by_name))
    if missing:
        raise ValueError(f"host_tools: no implementation was supplied for {', '.join(missing)}")

    log_parent = Path(os.fspath(log_dir))
    log_parent.mkdir(parents=True, exist_ok=True)
    config_dir = tempfile.mkdtemp(prefix="foe-contract-")
    config_path = Path(config_dir) / "config.json"
    config_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    try:
        process = await asyncio.create_subprocess_exec(
            os.fspath(binary),
            "--config",
            str(config_path),
            "--host",
            "--log-dir",
            str(log_parent),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=_LINE_LIMIT,
        )
    except OSError as exc:
        shutil.rmtree(config_dir, ignore_errors=True)
        raise BinaryError(f"{os.fspath(binary)}: {exc}") from exc
    handle = Handle(
        process=process,
        config=doc,
        config_dir=config_dir,
        log_parent=log_parent,
        model_backend=model_backend,
        tools=by_name,
        on_event=on_event,
        max_output_tokens=max_output_tokens,
    )
    pairing_error = await handle._await_start()
    if pairing_error is not None:
        raise CompatibilityError(pairing_error)
    return handle


async def run_config(
    config: Mapping[str, Any] | PathLike,
    *,
    model_backend: ModelBackend | None = None,
    binary: PathLike,
    log_dir: PathLike,
    tools: Iterable[HostTool] = (),
    on_event: EventCallback | None = None,
    max_output_tokens: int | None = None,
) -> Outcome:
    """Run a complete configuration document to its outcome."""
    handle = await start_config(
        config,
        model_backend=model_backend,
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
