"""A stand-in for the foe binary that speaks docs/protocol.md.

The script accepts the three command lines the package issues:

    fake_foe --config FILE --host --log-dir DIR    run one episode
    fake_foe plan --json --config FILE             print the identity
    fake_foe view DIR --serve                      print a URL and wait

The episode loop is the smallest one that exercises every host-side path:
it writes `episode/start`, the task `inbox/item`, `request/header`, and
`model/request`; consumes `model/chunk` lines; holds `inbox/item` lines
until the next step assembles, as the runtime does; writes `assistant/chunk`
events and the `assistant/message`; routes `host_tools` names through
`host/tool-call` and waits for `tool/result`; answers built-in tools with a
fixed value; honors `inbox/item` and `cancel` at any time; and ends with
`episode/end`. Budget accounting covers `model_calls` alone.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO

BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    name: {"name": name, "description": f"built-in {name}", "parameters": {"type": "object"}}
    for name in ("read", "grep", "edit", "bash", "block", "spawn", "wait", "steer", "notify", "send", "team")
}


class Log:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.file: TextIO = (log_dir / "episode.jsonl").open("w", encoding="utf-8")
        self.seq = 0
        self.events: list[dict[str, Any]] = []

    def emit(self, type_: str, data: dict[str, Any]) -> int:
        event = {"seq": self.seq, "time": int(time.time() * 1000), "type": type_, "data": data}
        line = json.dumps(event, ensure_ascii=False)
        self.file.write(line + "\n")
        self.file.flush()
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        self.events.append(event)
        self.seq += 1
        return event["seq"]


class Cancelled(Exception):
    pass


class Episode:
    def __init__(self, config: dict[str, Any], log_dir: Path) -> None:
        self.config = config
        self.log = Log(log_dir)
        self.host_tools: dict[str, Any] = config.get("host_tools") or {}
        # Items received over the protocol are held until the next step
        # assembles, so that their `seq` follows the previous step's results.
        self.held: list[dict[str, Any]] = []
        self.pending_inbox: list[int] = []
        self.stdin = sys.stdin

    # ---- stdin ----------------------------------------------------------------

    def read_line(self) -> dict[str, Any]:
        """The next host line, after handling inbox items and cancel inline."""
        while True:
            raw = self.stdin.readline()
            if not raw:
                self.end({"kind": "failed", "error": "host closed standard input"})
                sys.exit(1)
            try:
                obj = json.loads(raw)
            except ValueError:
                self.end({"kind": "failed", "error": "protocol: host line is not JSON"})
                sys.exit(1)
            kind = obj.get("type")
            if kind == "inbox/item":
                self.held.append(
                    {
                        "source": obj["source"],
                        "content": obj["content"],
                        "from": obj.get("from"),
                        "message_id": obj.get("message_id"),
                    }
                )
                continue
            if kind == "cancel":
                raise Cancelled()
            if kind not in ("model/chunk", "tool/result"):
                self.end({"kind": "failed", "error": f"protocol: unknown host line type {kind!r}"})
                sys.exit(1)
            return obj

    # ---- derived messages ---------------------------------------------------

    def messages(self, consumed: list[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        consumed_set = set(consumed)
        for event in self.log.events:
            data = event["data"]
            if event["type"] == "inbox/item" and (event["seq"] in consumed_set or event["seq"] in self.consumed_before):
                if out and out[-1]["role"] == "user":
                    out[-1]["content"].extend(data["content"])
                else:
                    out.append({"role": "user", "content": list(data["content"])})
            elif event["type"] == "assistant/message":
                out.append({"role": "assistant", "text": data["text"], "tool_calls": data["tool_calls"]})
            elif event["type"] == "tool/result":
                out.append(
                    {
                        "role": "tool",
                        "call_id": data["call_id"],
                        "name": data["name"],
                        "rendered": data["rendered"],
                        "is_error": data["is_error"],
                    }
                )
        return out

    # ---- the loop --------------------------------------------------------------

    def end(self, outcome: dict[str, Any]) -> None:
        self.log.emit("episode/end", {"outcome": outcome})

    def run(self) -> int:
        config = self.config
        program = {k: v for k, v in config.items() if k != "task"}
        self.log.emit(
            "episode/start",
            {
                "id": "ep_fake",
                "parent_id": None,
                "fork_origin": None,
                "team_id": None,
                "program": program,
                "identity": identity_of(program),
                "task": config["task"],
                "runtime": {"version": "0.1.0", "build": "unknown"},
                "sandbox": {"mode": (config.get("sandbox") or {}).get("mode", "best-effort"), "landlock_abi": 0},
            },
        )
        task_seq = self.log.emit(
            "inbox/item",
            {"source": "task", "content": [{"type": "text", "text": config["task"]}], "from": None, "message_id": None},
        )
        self.pending_inbox.append(task_seq)
        self.consumed_before: set[int] = set()

        tool_schemas: list[dict[str, Any]] = []
        instructions = "\n\n".join(config["instructions"][k] for k in sorted(config["instructions"]))
        for name in config["tools"]:
            if name in self.host_tools:
                spec = self.host_tools[name]
                tool_schemas.append({"name": name, "description": spec["description"], "parameters": spec["params"]})
                if spec.get("instruction"):
                    instructions += "\n\n" + spec["instruction"]
            elif name in (config.get("tool_defs") or {}):
                tool_schemas.append(
                    {
                        "name": name,
                        "description": config["tool_defs"][name]["description"],
                        "parameters": {"type": "object", "properties": {"args": {"type": "array"}}},
                    }
                )
            else:
                tool_schemas.append(BUILTIN_SCHEMAS[name])
        done_when = config.get("done_when") or {}
        if "returns" in done_when:
            # The runtime nests the declared schema under a required `value`;
            # see crates/core/src/registry.rs::return_spec. A double that
            # advertises the bare schema teaches callers the wrong call.
            wrapped = {"type": "object", "properties": {"value": done_when["returns"]}, "required": ["value"]}
            tool_schemas.append({"name": "return", "description": "Return the result.", "parameters": wrapped})
        header_seq = self.log.emit(
            "request/header",
            {"reason": "initial", "system": instructions, "tools": tool_schemas, "model": {"provider": "host", "model": "host"}},
        )

        budget_calls = int(config["budget"]["model_calls"])
        calls = 0
        step = 0
        try:
            while True:
                step += 1
                calls += 1
                request_id = f"rq_{step:02d}"
                for item in self.held:
                    self.pending_inbox.append(self.log.emit("inbox/item", item))
                self.held.clear()
                consumed = list(self.pending_inbox)
                self.pending_inbox.clear()
                messages = self.messages(consumed)
                self.consumed_before.update(consumed)
                self.log.emit(
                    "model/request",
                    {
                        "step": step,
                        "attempt": 1,
                        "request_id": request_id,
                        "header_seq": header_seq,
                        "consumed": consumed,
                        "messages": messages,
                    },
                )
                text, calls_made, stop, usage, error = self.collect_response(step, request_id)
                if error is not None:
                    self.end({"kind": "failed", "error": error})
                    return 1
                self.log.emit(
                    "assistant/message",
                    {
                        "step": step,
                        "request_id": request_id,
                        "text": text,
                        "tool_calls": calls_made,
                        "stop": stop,
                        "usage": usage,
                        "interrupted": False,
                    },
                )
                blocked: dict[str, Any] | None = None
                returned: tuple[Any] | None = None
                for call in calls_made:
                    if stop == "length":
                        self.tool_result(step, call, {"error": "response truncated"}, "response truncated", True)
                        continue
                    name = call["name"]
                    if name in self.host_tools:
                        self.host_call(step, call)
                    elif name == "block":
                        blocked = {"kind": "blocked", "code": call["args"]["code"], "message": call["args"].get("message", "")}
                        self.tool_result(step, call, {"ok": True}, "blocked", False)
                    elif name == "return":
                        returned = (call["args"]["value"],)
                        self.tool_result(step, call, {"ok": True}, "returned", False)
                    else:
                        self.tool_result(step, call, {"ok": True}, "ok", False)
                if blocked is not None:
                    self.end(blocked)
                    return 2
                if returned is not None or (not calls_made and "returns" not in done_when):
                    value: Any = returned[0] if returned is not None else text
                    if "verify" in done_when:
                        findings = self.verify(step, done_when["verify"], value)
                        if findings:
                            self.held.append(
                                {
                                    "source": "verify",
                                    "content": [{"type": "text", "text": "\n".join(findings)}],
                                    "from": None,
                                    "message_id": None,
                                }
                            )
                            if calls >= budget_calls:
                                self.end({"kind": "exhausted", "limit": "model_calls"})
                                return 3
                            continue
                    self.end({"kind": "completed", "value": value})
                    return 0
                if calls >= budget_calls:
                    self.end({"kind": "exhausted", "limit": "model_calls"})
                    return 3
        except Cancelled:
            self.end({"kind": "failed", "error": "cancelled"})
            return 1

    def collect_response(
        self, step: int, request_id: str
    ) -> tuple[str, list[dict[str, Any]], str, dict[str, int], str | None]:
        text = ""
        partial: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        while True:
            line = self.read_line()
            if line.get("type") != "model/chunk" or line.get("request_id") != request_id:
                self.end({"kind": "failed", "error": "protocol: chunk for an unknown request"})
                sys.exit(1)
            chunk = line["chunk"]
            self.log.emit("assistant/chunk", {"step": step, "request_id": request_id, "chunk": chunk})
            kind = chunk["kind"]
            if kind == "text":
                text += chunk["delta"]
            elif kind == "tool_call_start":
                partial[chunk["id"]] = {"id": chunk["id"], "name": chunk["name"], "buffer": ""}
                order.append(chunk["id"])
            elif kind == "tool_call_delta":
                partial[chunk["id"]]["buffer"] += chunk["delta"]
            elif kind == "done":
                calls = []
                for call_id in order:
                    raw = partial[call_id]["buffer"]
                    calls.append({"id": call_id, "name": partial[call_id]["name"], "args": json.loads(raw or "{}")})
                return text, calls, chunk["stop"], chunk["usage"], None
            elif kind == "error":
                return text, [], "end", {"input": 0, "output": 0, "cache_read": 0}, chunk["message"]

    def tool_result(self, step: int, call: dict[str, Any], value: Any, rendered: str, is_error: bool) -> None:
        self.log.emit(
            "tool/result",
            {
                "step": step,
                "call_id": call["id"],
                "name": call["name"],
                "value": value,
                "rendered": rendered,
                "is_error": is_error,
                "spill": None,
                "duration_ms": 0,
                "synthetic": False,
            },
        )

    def host_call(self, step: int, call: dict[str, Any]) -> dict[str, Any]:
        self.log.emit("host/tool-call", {"step": step, "call_id": call["id"], "name": call["name"], "args": call["args"]})
        while True:
            line = self.read_line()
            if line.get("type") == "tool/result" and line.get("call_id") == call["id"]:
                break
            self.end({"kind": "failed", "error": "protocol: result for an unknown call"})
            sys.exit(1)
        value = line["value"]
        rendered = line.get("rendered")
        if rendered is None:
            rendered = json.dumps(value, separators=(",", ":"))
        self.tool_result(step, call, value, rendered, bool(line.get("is_error", False)))
        return line

    def verify(self, step: int, verifier: str, candidate: Any) -> list[str]:
        """Run the verifier as a host tool call whose single argument is the candidate."""
        spec = self.host_tools.get(verifier)
        if spec is None:
            return []
        properties = list((spec["params"].get("properties") or {}).keys())
        arg_name = properties[0] if properties else "candidate"
        call = {"id": f"tc_verify_{step}", "name": verifier, "args": {arg_name: candidate}}
        line = self.host_call(step, call)
        value = line["value"]
        return [str(f) for f in value] if isinstance(value, list) else []


def identity_of(program: dict[str, Any]) -> str:
    hashed = {k: v for k, v in program.items() if k not in ("model", "sandbox")}
    if "grants" in hashed:
        hashed["grants"] = {k: len(v) for k, v in hashed["grants"].items()}
    canonical = json.dumps(hashed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def main(argv: list[str]) -> int:
    if argv[:1] == ["plan"]:
        config = json.loads(Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8"))
        program = {k: v for k, v in config.items() if k != "task"}
        print(json.dumps({"identity": identity_of(program), "program": program}))
        return 0
    if argv[:1] == ["view"] and "--serve" in argv:
        print("http://127.0.0.1:34567/", flush=True)
        sys.stdin.read()
        return 0
    if "--host" not in argv:
        print("fake_foe: only the --host form runs an episode", file=sys.stderr)
        return 1
    config_path = Path(argv[argv.index("--config") + 1])
    log_dir = Path(argv[argv.index("--log-dir") + 1])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if "task" not in config:
        print("task: required", file=sys.stderr)
        return 1
    return Episode(config, log_dir).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
