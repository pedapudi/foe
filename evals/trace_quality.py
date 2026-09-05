#!/usr/bin/python3
"""Measure whether foe episode logs prove the runtime's core guarantees."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DIMENSIONS = (
    "declared_permissions",
    "reconstructable_evidence",
    "typed_outcomes",
    "hierarchical_budgets",
    "workflow_provenance",
    "compaction_continuity",
)

BLOCKED_CODES = {
    "looping-tool-call",
    "looping-reasoning",
    "goal-unreachable",
    "ambiguous-task",
    "missing-capability",
    "verification-unsatisfiable",
    "child-blocked",
    "recovery-exhausted",
    "recovery-failed",
}

EXHAUSTED_LIMITS = {
    "model_calls",
    "input_tokens",
    "output_tokens",
    "context_window",
    "seconds",
    "depth",
    "episodes",
    "concurrency",
}


def _abi_key(value: Any) -> str:
    """Name the Landlock ABI observation bucket for one episode.

    A log that records a non-integer ABI already fails its declared_permissions
    check. Its observation bucket still needs a name that orders against the
    integer buckets, so the whole report survives a malformed log.
    """
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "invalid"


def _abi_order(name: str) -> tuple[int, int, str]:
    return (0, int(name), "") if name.isdigit() else (1, 0, name)


@dataclass
class EpisodeLog:
    path: Path
    events: list[dict[str, Any]]
    parse_error: str | None = None

    @property
    def start(self) -> dict[str, Any]:
        if self.events and self.events[0].get("type") == "episode/start":
            data = self.events[0].get("data")
            return data if isinstance(data, dict) else {}
        return {}

    @property
    def episode_id(self) -> str:
        value = self.start.get("id")
        return value if isinstance(value, str) else str(self.path)


class Evaluation:
    def __init__(self) -> None:
        self.stats = {
            name: {"checks": 0, "passed": 0, "applicable_episodes": set()} for name in DIMENSIONS
        }
        self.violations: list[dict[str, Any]] = []
        self.observations = {
            "episodes": 0,
            "sandbox_modes": {},
            "landlock_abis": {},
            "kernel_sandbox_episodes": 0,
            "denied_capability_calls": 0,
            "child_episodes": 0,
            "workflow_episodes": 0,
            "successful_compactions": 0,
        }

    def check(
        self,
        dimension: str,
        condition: bool,
        message: str,
        episode: EpisodeLog,
        seq: int | None = None,
    ) -> None:
        stat = self.stats[dimension]
        stat["checks"] += 1
        stat["applicable_episodes"].add(episode.episode_id)
        if condition:
            stat["passed"] += 1
            return
        violation: dict[str, Any] = {
            "dimension": dimension,
            "episode": episode.episode_id,
            "message": message,
        }
        if seq is not None:
            violation["seq"] = seq
        self.violations.append(violation)

    def report(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for name in DIMENSIONS:
            stat = self.stats[name]
            checks = stat["checks"]
            passed = stat["passed"]
            metrics[name] = {
                "covered_episodes": len(stat["applicable_episodes"]),
                "assertions": checks,
                "passed_assertions": passed,
                "conformant": passed == checks if checks else None,
            }
        observations = dict(self.observations)
        observations["sandbox_modes"] = dict(sorted(observations["sandbox_modes"].items()))
        observations["landlock_abis"] = dict(
            sorted(observations["landlock_abis"].items(), key=lambda item: _abi_order(item[0]))
        )
        return {
            "schema_version": 2,
            "valid": not self.violations,
            "metrics": metrics,
            "observations": observations,
            "violations": self.violations,
        }


def _event_type(event: dict[str, Any]) -> str:
    value = event.get("type")
    return value if isinstance(value, str) else ""


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("data")
    return value if isinstance(value, dict) else {}


def _read_log(path: Path) -> EpisodeLog:
    events: list[dict[str, Any]] = []
    try:
        raw = path.read_bytes()
    except OSError as error:
        return EpisodeLog(path, [], str(error))
    if raw and not raw.endswith(b"\n"):
        return EpisodeLog(path, [], "the final JSON line has no line feed")
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            return EpisodeLog(path, events, f"line {line_number}: {error.msg}")
        if not isinstance(event, dict):
            return EpisodeLog(path, events, f"line {line_number}: the event is not an object")
        events.append(event)
    return EpisodeLog(path, events)


def _discover(paths: Iterable[Path]) -> list[EpisodeLog]:
    files: set[Path] = set()
    for supplied in paths:
        path = supplied.resolve()
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            direct = path / "episode.jsonl"
            if direct.is_file():
                files.add(direct)
            files.update(path.rglob("episode.jsonl"))
        else:
            files.add(path)
    return [_read_log(path) for path in sorted(files)]


def _contract(log: EpisodeLog) -> dict[str, Any]:
    value = log.start.get("contract")
    return value if isinstance(value, dict) else {}


def _messages_for_request(log: EpisodeLog, request_index: int) -> list[dict[str, Any]]:
    request = _event_data(log.events[request_index])
    request_id = request.get("request_id", "")
    if isinstance(request_id, str) and request_id.startswith("cmp_"):
        return request.get("messages", [])

    summary: dict[str, Any] | None = None
    for event in reversed(log.events[:request_index]):
        if _event_type(event) == "compaction/summary":
            summary = _event_data(event)
            break

    messages: list[dict[str, Any]] = []
    inner_call_ids = {
        data.get("call_id")
        for event in log.events[:request_index]
        if _event_type(event) == "tool/inner-call"
        and isinstance((data := _event_data(event)).get("call_id"), str)
    }
    from_seq = 0
    if summary is not None:
        state = summary.get("state", {})
        task = state.get("task", "") if isinstance(state, dict) else ""
        messages.append({"role": "user", "content": [{"type": "text", "text": task}]})
        messages.append(
            {"role": "user", "content": [{"type": "text", "text": _render_continuation(summary)}]}
        )
        first_kept = summary.get("first_kept_seq", 0)
        from_seq = first_kept if isinstance(first_kept, int) else 0

    inbox: dict[int, list[dict[str, Any]]] = {}
    for event in log.events:
        if _event_type(event) != "inbox/item":
            continue
        seq = event.get("seq")
        content = _event_data(event).get("content")
        if isinstance(seq, int) and isinstance(content, list):
            inbox[seq] = content

    def add_consumed(consumed: Any) -> None:
        if not isinstance(consumed, list):
            return
        content: list[dict[str, Any]] = []
        for seq in consumed:
            if isinstance(seq, int):
                content.extend(inbox.get(seq, []))
        if content:
            messages.append({"role": "user", "content": content})

    for event in log.events:
        seq = event.get("seq")
        if not isinstance(seq, int) or seq < from_seq or seq >= request_index:
            continue
        kind = _event_type(event)
        data = _event_data(event)
        if kind == "model/request":
            prior_id = data.get("request_id", "")
            if isinstance(prior_id, str) and not prior_id.startswith("cmp_"):
                add_consumed(data.get("consumed"))
        elif kind == "assistant/message":
            prior_id = data.get("request_id", "")
            if isinstance(prior_id, str) and not prior_id.startswith("cmp_"):
                message = {
                    "role": "assistant",
                    "text": data.get("text", ""),
                    "tool_calls": data.get("tool_calls", []),
                }
                thinking = data.get("thinking")
                if isinstance(thinking, list) and thinking:
                    message["thinking"] = thinking
                messages.append(message)
        elif kind == "tool/result" and data.get("call_id") not in inner_call_ids:
            messages.append(
                {
                    "role": "tool",
                    "call_id": data.get("call_id", ""),
                    "name": data.get("name", ""),
                    "rendered": data.get("rendered", ""),
                    "is_error": data.get("is_error", False),
                }
            )
    add_consumed(request.get("consumed"))
    return messages


def _render_continuation(summary: dict[str, Any]) -> str:
    state = summary.get("state", {})
    state = state if isinstance(state, dict) else {}
    covered = state.get("covered", {})
    covered = covered if isinstance(covered, dict) else {}
    files = state.get("files", {})
    files = files if isinstance(files, dict) else {}
    budget = state.get("budget_remaining", {})
    budget = budget if isinstance(budget, dict) else {}

    def list_value(items: Any) -> str:
        if not isinstance(items, list) or not items:
            return " (none)"
        return "".join(f"\n- {item}" for item in items)

    child_lines: list[str] = []
    children = state.get("children", [])
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            outcome = child.get("outcome", {})
            outcome = outcome if isinstance(outcome, dict) else {}
            detail = outcome.get("code", outcome.get("limit", ""))
            suffix = f" {detail}" if detail else ""
            child_lines.append(
                f"{child.get('id', '')} ({child.get('contract', '')}): {outcome.get('kind', '')}{suffix}"
            )

    def amount(name: str) -> str:
        value = budget.get(name)
        return str(value) if isinstance(value, int) else "unlimited"

    lines = [
        f"covered: seq {covered.get('first_seq', '')} to {covered.get('last_seq', '')}",
        f"done_when: {state.get('done_when', '')}",
        f"outstanding_findings:{list_value(state.get('outstanding_findings'))}",
        f"files_read:{list_value(files.get('read'))}",
        f"files_written:{list_value(files.get('written'))}",
        f"files_edited:{list_value(files.get('edited'))}",
        f"children:{list_value(child_lines)}",
        "budget_remaining: "
        f"model_calls {amount('model_calls')}, input_tokens {amount('input_tokens')}, "
        f"output_tokens {amount('output_tokens')}, seconds {amount('seconds')}",
    ]
    return (
        "## Continuation state\n\n"
        + "\n".join(lines)
        + "\n\n## Summary\n\n"
        + str(summary.get("summary", ""))
    )


def _check_evidence(evaluation: Evaluation, log: EpisodeLog) -> None:
    dimension = "reconstructable_evidence"
    evaluation.check(dimension, log.parse_error is None, log.parse_error or "log parses", log)
    if log.parse_error is not None:
        return
    evaluation.check(dimension, bool(log.events), "the log is empty", log)
    if not log.events:
        return

    for index, event in enumerate(log.events):
        seq = event.get("seq")
        evaluation.check(dimension, seq == index, "seq is not contiguous from zero", log, index)
        evaluation.check(
            dimension,
            isinstance(event.get("time"), int)
            and isinstance(event.get("type"), str)
            and isinstance(event.get("data"), dict),
            "the event envelope does not contain integer time, string type, and object data",
            log,
            index,
        )

    starts = [event for event in log.events if _event_type(event) == "episode/start"]
    ends = [event for event in log.events if _event_type(event) == "episode/end"]
    evaluation.check(
        dimension,
        len(starts) == 1 and _event_type(log.events[0]) == "episode/start",
        "exactly one episode/start must appear first",
        log,
        0,
    )
    evaluation.check(
        dimension,
        len(ends) == 1 and _event_type(log.events[-1]) == "episode/end",
        "exactly one episode/end must appear last",
        log,
        len(log.events) - 1,
    )

    latest_header: int | None = None
    inbox_consumed: set[int] = set()
    requests: dict[str, int] = {}
    calls: dict[str, tuple[int, str]] = {}
    model_issued_calls: set[str] = set()
    inner_indices: dict[str, list[int]] = {}
    results: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, event in enumerate(log.events):
        kind = _event_type(event)
        data = _event_data(event)
        if kind == "request/header":
            latest_header = index
        elif kind == "model/request":
            request_id = data.get("request_id")
            evaluation.check(
                dimension,
                isinstance(request_id, str) and request_id not in requests,
                "request_id is absent or repeated",
                log,
                index,
            )
            if isinstance(request_id, str):
                requests[request_id] = index
            evaluation.check(
                dimension,
                data.get("header_seq") == latest_header,
                "header_seq does not name the request/header in effect",
                log,
                index,
            )
            consumed = data.get("consumed", [])
            consumed_valid = isinstance(consumed, list)
            if consumed_valid:
                for seq in consumed:
                    consumed_valid = (
                        consumed_valid
                        and isinstance(seq, int)
                        and seq < index
                        and seq not in inbox_consumed
                        and _event_type(log.events[seq]) == "inbox/item"
                    )
                    if isinstance(seq, int):
                        inbox_consumed.add(seq)
            evaluation.check(
                dimension,
                consumed_valid,
                "consumed must name earlier, previously unconsumed inbox items",
                log,
                index,
            )
            if isinstance(request_id, str) and not request_id.startswith("cmp_"):
                expected = _messages_for_request(log, index)
                evaluation.check(
                    dimension,
                    data.get("messages") == expected,
                    "model/request.messages cannot be reconstructed from earlier events",
                    log,
                    index,
                )
        elif kind == "assistant/message":
            request_id = data.get("request_id")
            evaluation.check(
                dimension,
                isinstance(request_id, str) and request_id in requests and requests[request_id] < index,
                "assistant/message does not name an earlier model/request",
                log,
                index,
            )
            tool_calls = data.get("tool_calls", [])
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    call_id = call.get("id")
                    name = call.get("name")
                    evaluation.check(
                        dimension,
                        isinstance(call_id, str) and call_id not in calls and isinstance(name, str),
                        "a tool call id is absent or repeated",
                        log,
                        index,
                    )
                    if isinstance(call_id, str) and isinstance(name, str):
                        calls[call_id] = (index, name)
                        model_issued_calls.add(call_id)
        elif kind == "tool/inner-call":
            outer_call_id = data.get("outer_call_id")
            call_id = data.get("call_id")
            name = data.get("name")
            inner_index = data.get("index")
            valid_outer = (
                isinstance(outer_call_id, str)
                and outer_call_id in model_issued_calls
                and outer_call_id not in results
            )
            evaluation.check(
                dimension,
                valid_outer,
                "tool/inner-call.outer_call_id must name one unsettled model-issued call",
                log,
                index,
            )
            valid_call = isinstance(call_id, str) and call_id not in calls and isinstance(name, str)
            evaluation.check(
                dimension,
                valid_call,
                "tool/inner-call must have a unique call_id and a string name",
                log,
                index,
            )
            indices = inner_indices.setdefault(outer_call_id, []) if isinstance(outer_call_id, str) else []
            valid_index = isinstance(inner_index, int) and inner_index == len(indices)
            evaluation.check(
                dimension,
                valid_index,
                "tool/inner-call.index must count from zero within its outer call",
                log,
                index,
            )
            if isinstance(inner_index, int):
                indices.append(inner_index)
            if valid_call:
                calls[call_id] = (index, name)
        elif kind == "assistant/chunk":
            request_id = data.get("request_id")
            evaluation.check(
                dimension,
                isinstance(request_id, str) and request_id in requests and requests[request_id] < index,
                "assistant/chunk does not name an earlier model/request",
                log,
                index,
            )
        elif kind == "tool/result":
            call_id = data.get("call_id")
            if isinstance(call_id, str):
                results.setdefault(call_id, []).append((index, data))

    for call_id, (call_seq, name) in calls.items():
        matched = results.get(call_id, [])
        correct = len(matched) == 1
        if correct:
            result_seq, result = matched[0]
            correct = result_seq > call_seq and result.get("name") == name
        evaluation.check(
            dimension,
            correct,
            f"tool call {call_id} does not have one later result with the same name",
            log,
            call_seq,
        )
    for call_id, matched in results.items():
        evaluation.check(
            dimension,
            call_id in calls and len(matched) == 1,
            f"tool result {call_id} does not settle one earlier call",
            log,
            matched[0][0],
        )


def _check_permissions(evaluation: Evaluation, log: EpisodeLog) -> None:
    dimension = "declared_permissions"
    start = log.start
    contract = _contract(log)
    grants = contract.get("grants")
    evaluation.check(dimension, isinstance(grants, dict), "contract.grants is absent", log, 0)
    grants = grants if isinstance(grants, dict) else {}
    read = grants.get("read")
    evaluation.check(
        dimension,
        isinstance(read, list) and bool(read) and all(isinstance(path, str) and os.path.isabs(path) for path in read),
        "grants.read must contain absolute paths",
        log,
        0,
    )
    for name in ("write", "spawn"):
        values = grants.get(name, [])
        valid = isinstance(values, list) and all(isinstance(value, str) for value in values)
        if name == "write" and valid:
            valid = all(os.path.isabs(value) for value in values)
        evaluation.check(dimension, valid, f"grants.{name} has invalid entries", log, 0)

    tool_defs = contract.get("tool_defs", {})
    valid_tools = isinstance(tool_defs, dict)
    if isinstance(tool_defs, dict):
        for definition in tool_defs.values():
            valid_tools = (
                valid_tools
                and isinstance(definition, dict)
                and isinstance(definition.get("exec"), str)
                and os.path.isabs(definition["exec"])
            )
    evaluation.check(
        dimension,
        valid_tools,
        "every configured executable must have an absolute path",
        log,
        0,
    )

    sandbox = start.get("sandbox")
    sandbox = sandbox if isinstance(sandbox, dict) else {}
    mode = sandbox.get("mode")
    abi = sandbox.get("landlock_abi")
    evaluation.check(
        dimension,
        mode in {"off", "best-effort", "required"},
        "episode/start.sandbox.mode is invalid",
        log,
        0,
    )
    evaluation.check(
        dimension,
        isinstance(abi, int) and abi >= 0,
        "episode/start.sandbox.landlock_abi is invalid",
        log,
        0,
    )
    evaluation.check(
        dimension,
        mode != "off" or abi == 0,
        "sandbox mode off must record Landlock ABI 0",
        log,
        0,
    )
    evaluation.check(
        dimension,
        mode != "required" or isinstance(abi, int) and abi > 0,
        "sandbox mode required must record an enforced Landlock ABI",
        log,
        0,
    )
    modes = evaluation.observations["sandbox_modes"]
    modes[str(mode)] = modes.get(str(mode), 0) + 1
    abis = evaluation.observations["landlock_abis"]
    abi_key = _abi_key(abi)
    abis[abi_key] = abis.get(abi_key, 0) + 1
    if isinstance(abi, int) and abi > 0:
        evaluation.observations["kernel_sandbox_episodes"] += 1

    results = {
        _event_data(event).get("call_id"): _event_data(event)
        for event in log.events
        if _event_type(event) == "tool/result"
    }

    def granted(path: str, roots: Any) -> bool:
        if not isinstance(roots, list) or not roots:
            return False
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(roots[0]) / candidate
        candidate_text = os.path.realpath(candidate)
        for root in roots:
            if not isinstance(root, str):
                continue
            try:
                if os.path.commonpath([candidate_text, os.path.realpath(root)]) == os.path.realpath(root):
                    return True
            except ValueError:
                continue
        return False

    for event in log.events:
        if _event_type(event) != "assistant/message":
            continue
        for call in _event_data(event).get("tool_calls", []):
            if not isinstance(call, dict) or call.get("name") not in {"read", "edit"}:
                continue
            args = call.get("args", {})
            path = args.get("path") if isinstance(args, dict) else None
            if not isinstance(path, str):
                continue
            roots = read if call.get("name") == "read" else grants.get("write", [])
            if granted(path, roots):
                continue
            result = results.get(call.get("id"), {})
            evaluation.check(
                dimension,
                isinstance(result, dict) and result.get("is_error") is True,
                f"{call.get('name')} reached a path outside its declared grant",
                log,
                event.get("seq"),
            )
            if isinstance(result, dict) and result.get("is_error") is True:
                evaluation.observations["denied_capability_calls"] += 1


def _schema_findings(value: Any, schema: Any, location: str = "value") -> list[str]:
    if not isinstance(schema, dict):
        return [f"{location} has a non-object schema"]
    findings: list[str] = []
    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if isinstance(expected, str) and expected in type_checks and not type_checks[expected](value):
        return [f"{location} must have type {expected}"]
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        findings.append(f"{location} is outside its enum")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    findings.append(f"{location}.{key} is required")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value:
                    findings.extend(_schema_findings(value[key], child_schema, f"{location}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            findings.extend(_schema_findings(item, schema["items"], f"{location}[{index}]"))
    return findings


def _check_outcome(evaluation: Evaluation, log: EpisodeLog) -> None:
    dimension = "typed_outcomes"
    end_events = [event for event in log.events if _event_type(event) == "episode/end"]
    evaluation.check(dimension, len(end_events) == 1, "the episode has no unique outcome", log)
    if len(end_events) != 1:
        return
    end = _event_data(end_events[0])
    outcome = end.get("outcome")
    evaluation.check(dimension, isinstance(outcome, dict), "episode/end.outcome is not an object", log)
    if not isinstance(outcome, dict):
        return
    kind = outcome.get("kind")
    valid = False
    if kind == "completed":
        valid = "value" in outcome
    elif kind == "blocked":
        valid = outcome.get("code") in BLOCKED_CODES and isinstance(outcome.get("message"), str)
    elif kind == "exhausted":
        valid = outcome.get("limit") in EXHAUSTED_LIMITS
    elif kind == "failed":
        valid = isinstance(outcome.get("error"), str)
    evaluation.check(dimension, valid, "the outcome does not match its closed variant", log)

    done_when = _contract(log).get("done_when", {})
    returns = done_when.get("returns") if isinstance(done_when, dict) else None
    if returns is not None and kind == "completed":
        findings = _schema_findings(outcome.get("value"), returns)
        evaluation.check(
            dimension,
            not findings,
            "completed value violates done_when.returns: " + "; ".join(findings),
            log,
            end_events[0].get("seq"),
        )


def _subtree_spend(log: EpisodeLog, children_by_parent: dict[str, list[EpisodeLog]]) -> dict[str, int]:
    calls = sum(_event_type(event) == "model/request" for event in log.events)
    input_tokens = 0
    output_tokens = 0
    for event in log.events:
        if _event_type(event) != "assistant/message":
            continue
        usage = _event_data(event).get("usage", {})
        if isinstance(usage, dict):
            if isinstance(usage.get("input"), int):
                input_tokens += usage["input"]
            if isinstance(usage.get("output"), int):
                output_tokens += usage["output"]
    for child in children_by_parent.get(log.episode_id, []):
        spent = _subtree_spend(child, children_by_parent)
        calls += spent["model_calls"]
        input_tokens += spent["input_tokens"]
        output_tokens += spent["output_tokens"]
    return {"model_calls": calls, "input_tokens": input_tokens, "output_tokens": output_tokens}


def _output_allowance_is_enforced(log: EpisodeLog) -> bool:
    """Whether every recorded route accepts the runtime's output cap."""
    for event in log.events:
        if _event_type(event) != "request/header":
            continue
        model = _event_data(event).get("model")
        if isinstance(model, dict) and model.get("provider") == "openai-codex":
            return False
    return True


def _check_budgets(evaluation: Evaluation, logs: list[EpisodeLog]) -> None:
    by_id = {log.episode_id: log for log in logs if log.start}
    children_by_parent: dict[str, list[EpisodeLog]] = {}
    for log in logs:
        parent_id = log.start.get("parent_id")
        if isinstance(parent_id, str):
            children_by_parent.setdefault(parent_id, []).append(log)
            evaluation.observations["child_episodes"] += 1

    for log in logs:
        contract = _contract(log)
        budget = contract.get("budget")
        has_events = any(
            _event_type(event) in {"budget/reserve", "budget/release", "spawn/start", "spawn/end"}
            for event in log.events
        )
        has_children = bool(children_by_parent.get(log.episode_id))
        if not has_events and not has_children:
            continue
        dimension = "hierarchical_budgets"
        evaluation.check(dimension, isinstance(budget, dict), "contract.budget is absent", log, 0)
        budget = budget if isinstance(budget, dict) else {}
        names = ("model_calls", "input_tokens", "output_tokens")
        totals = {name: budget.get(name) for name in names}
        own_spent = {name: 0 for name in names}
        child_spent = {name: 0 for name in names}
        active: dict[str, dict[str, int]] = {}
        running: set[str] = set()
        seen_children: set[str] = set()

        for index, event in enumerate(log.events):
            kind = _event_type(event)
            data = _event_data(event)
            if kind == "model/request":
                own_spent["model_calls"] += 1
            elif kind == "assistant/message":
                usage = data.get("usage", {})
                if isinstance(usage, dict):
                    if isinstance(usage.get("input"), int):
                        own_spent["input_tokens"] += usage["input"]
                    if isinstance(usage.get("output"), int):
                        own_spent["output_tokens"] += usage["output"]
            elif kind == "budget/reserve":
                child_id = data.get("child_id")
                reserved = data.get("reserved")
                valid = isinstance(child_id, str) and child_id not in active and isinstance(reserved, dict)
                evaluation.check(
                    dimension,
                    valid,
                    "budget/reserve must name one child with an object reservation",
                    log,
                    index,
                )
                if valid:
                    normalized = {
                        name: value
                        for name in names
                        if isinstance((value := reserved.get(name)), int)
                    }
                    active[child_id] = normalized
                    for name, total in totals.items():
                        if not isinstance(total, int):
                            continue
                        active_total = sum(item.get(name, 0) for item in active.values())
                        used = own_spent[name] + child_spent[name] + active_total
                        evaluation.check(
                            dimension,
                            used <= total,
                            f"the active child reservations exceed budget.{name}",
                            log,
                            index,
                        )
            elif kind == "spawn/start":
                child_id = data.get("child_id")
                valid = isinstance(child_id, str) and child_id in active and child_id not in running
                evaluation.check(
                    dimension,
                    valid,
                    "spawn/start has no matching active reservation",
                    log,
                    index,
                )
                if isinstance(child_id, str):
                    running.add(child_id)
                    seen_children.add(child_id)
                cap = budget.get("max_concurrent", 4)
                evaluation.check(
                    dimension,
                    not isinstance(cap, int) or len(running) <= cap,
                    "running children exceed budget.max_concurrent",
                    log,
                    index,
                )
            elif kind == "spawn/end":
                child_id = data.get("child_id")
                evaluation.check(
                    dimension,
                    isinstance(child_id, str) and child_id in running,
                    "spawn/end has no matching running child",
                    log,
                    index,
                )
                if isinstance(child_id, str):
                    running.discard(child_id)
                    child = by_id.get(child_id)
                    evaluation.check(
                        dimension,
                        child is not None and child.start.get("parent_id") == log.episode_id,
                        "spawned child log is absent or names a different parent",
                        log,
                        index,
                    )
                    if child is not None:
                        child_ends = [item for item in child.events if _event_type(item) == "episode/end"]
                        child_outcome = _event_data(child_ends[-1]).get("outcome") if child_ends else None
                        evaluation.check(
                            dimension,
                            data.get("outcome") == child_outcome,
                            "spawn/end outcome differs from the child log",
                            log,
                            index,
                        )
            elif kind == "budget/release":
                child_id = data.get("child_id")
                spent = data.get("spent")
                valid = isinstance(child_id, str) and child_id in active and isinstance(spent, dict)
                evaluation.check(
                    dimension,
                    valid,
                    "budget/release has no matching reservation",
                    log,
                    index,
                )
                if valid:
                    reserved = active.pop(child_id)
                    child = by_id.get(child_id)
                    measured = _subtree_spend(child, children_by_parent) if child is not None else None
                    for name in names:
                        value = spent.get(name)
                        if isinstance(value, int):
                            bounded = name != "input_tokens" and not (
                                name == "output_tokens"
                                and child is not None
                                and not _output_allowance_is_enforced(child)
                            )
                            if bounded:
                                evaluation.check(
                                    dimension,
                                    value <= reserved.get(name, value),
                                    f"released child spend exceeds its {name} reservation",
                                    log,
                                    index,
                                )
                            if measured is not None:
                                evaluation.check(
                                    dimension,
                                    value == measured[name],
                                    f"released {name} spend differs from the child log",
                                    log,
                                    index,
                                )
                            child_spent[name] += value

        evaluation.check(dimension, not active, "a child reservation was not released", log)
        evaluation.check(dimension, not running, "a child did not settle", log)
        evaluation.check(
            dimension,
            seen_children == {child.episode_id for child in children_by_parent.get(log.episode_id, [])},
            "the child log set differs from the spawn trace",
            log,
        )
        cap = budget.get("max_episodes", 8)
        if isinstance(cap, int):
            subtree_count = 1
            stack = list(children_by_parent.get(log.episode_id, []))
            while stack:
                child = stack.pop()
                subtree_count += 1
                stack.extend(children_by_parent.get(child.episode_id, []))
            evaluation.check(
                dimension,
                subtree_count <= cap,
                "the episode tree exceeds budget.max_episodes",
                log,
            )


def _check_workflow(evaluation: Evaluation, log: EpisodeLog) -> None:
    contract = _contract(log)
    workflow = contract.get("workflow")
    has_events = any(_event_type(event).startswith("workflow/") for event in log.events)
    if not isinstance(workflow, dict) and not has_events:
        return
    dimension = "workflow_provenance"
    evaluation.observations["workflow_episodes"] += 1
    evaluation.check(dimension, isinstance(workflow, dict), "workflow events lack a declared graph", log, 0)
    workflow = workflow if isinstance(workflow, dict) else {}
    nodes = workflow.get("nodes", {})
    nodes = nodes if isinstance(nodes, dict) else {}
    starts: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    ends: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    fires: dict[str, int] = {}

    for index, event in enumerate(log.events):
        kind = _event_type(event)
        data = _event_data(event)
        if kind == "workflow/node-start":
            node = data.get("node")
            fire = data.get("fire")
            key = (node, fire)
            valid = isinstance(node, str) and isinstance(fire, int) and node in nodes and key not in starts
            evaluation.check(
                dimension,
                valid,
                "workflow/node-start does not name a declared, unique firing",
                log,
                index,
            )
            if valid:
                starts[key] = (index, data)
                fires[node] = max(fires.get(node, 0), fire)
            inputs = data.get("inputs", [])
            inputs_valid = isinstance(inputs, list)
            if inputs_valid:
                for seq in inputs:
                    inputs_valid = (
                        inputs_valid
                        and isinstance(seq, int)
                        and seq < index
                        and _event_type(log.events[seq])
                        in {"inbox/item", "workflow/node-end", "workflow/recovery"}
                    )
            evaluation.check(
                dimension,
                inputs_valid,
                "workflow firing inputs do not name earlier producer events",
                log,
                index,
            )
            child_id = data.get("child_id")
            if child_id is not None:
                child_exists = any(
                    _event_type(item) == "spawn/start" and _event_data(item).get("child_id") == child_id
                    for item in log.events
                )
                evaluation.check(
                    dimension,
                    child_exists,
                    "a model-node firing does not name a spawned child",
                    log,
                    index,
                )
        elif kind == "workflow/node-end":
            node = data.get("node")
            fire = data.get("fire")
            key = (node, fire)
            evaluation.check(
                dimension,
                key in starts and starts[key][0] < index and key not in ends,
                "workflow/node-end does not match one earlier firing",
                log,
                index,
            )
            if key in starts:
                ends[key] = (index, data)
        elif kind == "workflow/branch":
            node = data.get("node")
            fire = data.get("fire")
            label = data.get("label")
            key = (node, fire)
            declaration = nodes.get(node, {}) if isinstance(node, str) else {}
            branches = declaration.get("branches", {}) if isinstance(declaration, dict) else {}
            expected = branches.get(label) if isinstance(branches, dict) else None
            evaluation.check(
                dimension,
                key in ends and isinstance(expected, list) and data.get("successors") == expected,
                "workflow/branch differs from the declared choice",
                log,
                index,
            )
        elif kind == "workflow/recovery":
            action = data.get("action")
            evaluation.check(
                dimension,
                action in {"retry", "amend", "skip", "abort"},
                "workflow/recovery action is outside the closed action set",
                log,
                index,
            )

    evaluation.check(dimension, starts.keys() == ends.keys(), "workflow firings do not all settle", log)
    for node, maximum in fires.items():
        declaration = nodes.get(node, {})
        cap = declaration.get("max_fires", 1) if isinstance(declaration, dict) else 1
        evaluation.check(
            dimension,
            not isinstance(cap, int) or maximum <= cap,
            f"workflow node {node} exceeds max_fires",
            log,
        )


def _done_when_line(contract: dict[str, Any]) -> str:
    done_when = contract.get("done_when")
    done_when = done_when if isinstance(done_when, dict) else {}
    finish = (
        "a call to `return` with a value conforming to its schema"
        if isinstance(done_when.get("returns"), dict)
        else "a turn with no tool calls"
    )
    verify = done_when.get("verify")
    return f"{finish}, then `{verify}` reports no findings" if isinstance(verify, str) else finish


def _compacted_files(
    log: EpisodeLog, covered: dict[str, Any], carried: dict[str, Any]
) -> dict[str, list[str]]:
    failed = {
        _event_data(event).get("call_id")
        for event in log.events
        if _event_type(event) == "tool/result" and _event_data(event).get("is_error") is True
    }
    values = {
        "read": list(carried.get("read", [])) if isinstance(carried.get("read"), list) else [],
        "written": list(carried.get("written", [])) if isinstance(carried.get("written"), list) else [],
        "edited": list(carried.get("edited", [])) if isinstance(carried.get("edited"), list) else [],
    }
    first = covered.get("first_seq", 0)
    last = covered.get("last_seq", -1)
    for event in log.events:
        seq = event.get("seq")
        if not isinstance(seq, int) or seq < first or seq > last or _event_type(event) != "assistant/message":
            continue
        calls = _event_data(event).get("tool_calls", [])
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict) or call.get("id") in failed:
                continue
            target = {"read": "read", "write": "written", "edit": "edited"}.get(call.get("name"))
            args = call.get("args", {})
            path = args.get("path") if isinstance(args, dict) else None
            if target is not None and isinstance(path, str):
                values[target].append(path)
    return {name: sorted(set(paths)) for name, paths in values.items()}


def _check_compaction(evaluation: Evaluation, log: EpisodeLog) -> None:
    compaction_events = [event for event in log.events if _event_type(event).startswith("compaction/")]
    if not compaction_events:
        return
    dimension = "compaction_continuity"
    context = _contract(log).get("context", {})
    evaluation.check(
        dimension,
        isinstance(context, dict) and context.get("compact") is True,
        "compaction events appear while context.compact is disabled",
        log,
        0,
    )
    starts = [index for index, event in enumerate(log.events) if _event_type(event) == "compaction/start"]
    previous_summary: dict[str, Any] | None = None
    for position, start_index in enumerate(starts):
        next_start = starts[position + 1] if position + 1 < len(starts) else len(log.events)
        span = log.events[start_index:next_start]
        start = _event_data(log.events[start_index])
        summaries = [event for event in span if _event_type(event) == "compaction/summary"]
        ends = [event for event in span if _event_type(event) == "compaction/end"]
        evaluation.check(dimension, len(ends) == 1, "compaction/start has no unique end", log, start_index)
        if len(ends) != 1:
            continue
        end = _event_data(ends[0])
        ok = end.get("ok") is True
        evaluation.check(
            dimension,
            len(summaries) == (1 if ok else 0),
            "compaction summary presence differs from compaction/end.ok",
            log,
            start_index,
        )
        if not ok or len(summaries) != 1:
            continue
        evaluation.observations["successful_compactions"] += 1
        summary_event = summaries[0]
        summary = _event_data(summary_event)
        state = summary.get("state", {})
        state = state if isinstance(state, dict) else {}
        covered = start.get("covered", {})
        covered = covered if isinstance(covered, dict) else {}
        expected_first = previous_summary.get("first_kept_seq", 1) if previous_summary else 1
        evaluation.check(
            dimension,
            covered.get("first_seq") == expected_first,
            "a compaction re-reads or skips the prior covered boundary",
            log,
            start_index,
        )
        evaluation.check(
            dimension,
            state.get("covered") == covered,
            "typed continuation state has a different covered span",
            log,
            summary_event.get("seq"),
        )
        evaluation.check(
            dimension,
            state.get("task") == log.start.get("task"),
            "typed continuation state does not preserve the task verbatim",
            log,
            summary_event.get("seq"),
        )
        evaluation.check(
            dimension,
            state.get("done_when") == _done_when_line(_contract(log)),
            "typed continuation state does not preserve the completion condition",
            log,
            summary_event.get("seq"),
        )
        evaluation.check(
            dimension,
            summary.get("first_kept_seq") == covered.get("last_seq", -2) + 1,
            "first_kept_seq is not the first event after the covered span",
            log,
            summary_event.get("seq"),
        )
        request_seq = summary.get("summary_request_seq")
        request = log.events[request_seq] if isinstance(request_seq, int) and request_seq < len(log.events) else {}
        request_data = _event_data(request)
        request_id = request_data.get("request_id")
        evaluation.check(
            dimension,
            _event_type(request) == "model/request"
            and isinstance(request_id, str)
            and request_id.startswith("cmp_")
            and start_index < request_seq < summary_event.get("seq", -1),
            "summary_request_seq does not name the recorded summarization request",
            log,
            summary_event.get("seq"),
        )
        answers = [
            _event_data(event)
            for event in span
            if _event_type(event) == "assistant/message" and _event_data(event).get("request_id") == request_id
        ]
        evaluation.check(
            dimension,
            len(answers) == 1 and answers[0].get("text") == summary.get("summary"),
            "compaction summary differs from its recorded assistant response",
            log,
            summary_event.get("seq"),
        )
        carried = {}
        if previous_summary is not None:
            prior_state = previous_summary.get("state", {})
            carried = prior_state.get("files", {}) if isinstance(prior_state, dict) else {}
        carried = carried if isinstance(carried, dict) else {}
        expected_files = _compacted_files(log, covered, carried)
        evaluation.check(
            dimension,
            state.get("files") == expected_files,
            "typed continuation file lists differ from successful tool calls",
            log,
            summary_event.get("seq"),
        )
        previous_summary = summary


def evaluate(paths: Iterable[Path]) -> dict[str, Any]:
    logs = _discover(paths)
    evaluation = Evaluation()
    evaluation.observations["episodes"] = len(logs)
    if not logs:
        missing = EpisodeLog(Path("<input>"), [])
        evaluation.check(
            "reconstructable_evidence", False, "no episode.jsonl files were found", missing
        )
        return evaluation.report()
    for log in logs:
        _check_evidence(evaluation, log)
        if not log.events:
            continue
        _check_permissions(evaluation, log)
        _check_outcome(evaluation, log)
        _check_workflow(evaluation, log)
        _check_compaction(evaluation, log)
    _check_budgets(evaluation, logs)
    return evaluation.report()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report whether one or more foe episode log trees satisfy the runtime's declared "
            "guarantees. The exit status is 0 when every applicable check passed and 1 when any "
            "check found a violation."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Episode directories or episode.jsonl files.")
    parser.add_argument(
        "--pretty", action="store_true", help="Indent the JSON report for human inspection."
    )
    args = parser.parse_args()
    report = evaluate(args.paths)
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
