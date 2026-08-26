#!/usr/bin/python3
"""Produce a bounded, typed diagnosis from one retained Foe trajectory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


MAX_VERIFICATION_RESULTS = 8
MAX_VERIFIER_FAILURES = 4
MAX_EVIDENCE_TEXT = 320
MAX_FAILURE_LOCATION = 160
MAX_FAILURE_ASSERTION = 200
MAX_FAILURE_MESSAGE = 200
MAX_FAILURE_STATUS = 64

ANSI_ESCAPE = re.compile(
    r"(?:\x1b\](?:(?!\x07|\x1b\\)[^\r\n])*(?:\x07|\x1b\\|(?=\r?\n|$))"
    r"|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_])"
)
CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MEMORY_ADDRESS = re.compile(r"\b0[xX][0-9a-fA-F]+\b")
VOLATILE_PATH = re.compile(
    r"/(?:tmp|var/tmp|home|root|workspace|workspaces|private/var/folders)"
    r"(?=/|[\s,'\"()\[\]:]|$)(?:/[^\s,'\"()\[\]]*)?"
)
TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
PARAMETER_VALUE = re.compile(r"\[[^\]\r\n]*\]")


def read_event_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Foe episode log does not exist: {path}")
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        events.append(value)
    return events


def read_episode_tree(log_dir: Path) -> list[tuple[Path, list[dict[str, Any]]]]:
    """Read the root log followed by every descendant log in path order."""
    root = log_dir / "episode.jsonl"
    paths = [root, *sorted(path for path in log_dir.rglob("episode.jsonl") if path != root)]
    return [(path, read_event_file(path)) for path in paths]


def data(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("data")
    return value if isinstance(value, dict) else {}


def request_contains_call(request: dict[str, Any], call_id: str) -> bool:
    for message in data(request).get("messages", []):
        if (
            isinstance(message, dict)
            and message.get("role") == "tool"
            and message.get("call_id") == call_id
        ):
            return True
    return False


def bounded_text(value: Any) -> str | None:
    """Return one bounded line."""
    if not isinstance(value, str) or not value.strip():
        return None
    collapsed = " ".join(value.split())
    return collapsed[:MAX_EVIDENCE_TEXT]


def stable_verifier_text(value: Any, limit: int) -> str | None:
    """Return one bounded line without host-specific paths or addresses."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = ANSI_ESCAPE.sub("", value)
    value = CONTROL_CHARACTER.sub("", value)
    value = MEMORY_ADDRESS.sub("<address>", value)
    value = VOLATILE_PATH.sub("<volatile-path>", value)
    value = TIMESTAMP.sub("<timestamp>", value)
    collapsed = " ".join(value.split())
    return collapsed[:limit] if collapsed else None


def stable_test_name(value: Any) -> str | None:
    """Return a bounded test name without parameter values or host state."""
    name = stable_verifier_text(value, MAX_EVIDENCE_TEXT)
    if name is None:
        return None
    return PARAMETER_VALUE.sub("[<parameter>]", name)[:MAX_EVIDENCE_TEXT]


def stable_python_location(path: str, line: str) -> str:
    """Normalize a Python traceback path while preserving its source location."""
    normalized = path.replace("\\", "/").lstrip("./")
    parts = [part for part in normalized.split("/") if part]
    if "tests" in parts:
        parts = parts[parts.index("tests") :]
    elif path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[/\\]", path):
        parts = parts[-1:]
    location = f"{'/'.join(parts)}:{line}"
    return location[:MAX_FAILURE_LOCATION]


def assertion_expression(trace: str) -> tuple[str | None, bool]:
    """Extract one source assertion and report ambiguous traceback content."""
    source = []
    observed = []
    for line in trace.splitlines():
        marked = re.match(r"^\s*>\s*assert\s+(.+?)\s*$", line)
        if marked:
            source.append(marked.group(1))
        rewritten = re.match(r"^\s*E\s+assert\s+(.+?)\s*$", line)
        if rewritten:
            observed.append(rewritten.group(1))
    candidates = source or observed
    if not candidates:
        return None, False
    normalized = []
    for candidate in candidates:
        expression = re.sub(r",\s*(?:\(|[rubfRUBF]*['\"].*)$", "", candidate)
        stable = stable_verifier_text(expression, MAX_FAILURE_ASSERTION)
        if stable is not None and stable not in normalized:
            normalized.append(stable)
    if len(normalized) != 1:
        return None, len(normalized) > 1
    return normalized[0], False


def assertion_location(trace: str) -> str | None:
    """Extract the final Python source coordinate from a pytest traceback."""
    locations = []
    pattern = re.compile(
        r"^\s*((?:[A-Za-z]:)?[/\\]?[^:\n]+\.py):(\d+)(?::.*)?$"
    )
    for line in trace.splitlines():
        match = pattern.match(line)
        if match:
            location = stable_python_location(match.group(1), match.group(2))
            if location not in locations:
                locations.append(location)
    return locations[-1] if locations else None


def assertion_message(trace: str, fallback: Any) -> str | None:
    """Extract pytest's concise assertion message without retaining its traceback."""
    messages = []
    for line in trace.splitlines():
        match = re.match(
            r"^\s*E\s+[A-Za-z][A-Za-z0-9_.]*(?:Error|Exception):\s*(.+?)\s*$",
            line,
        )
        if match:
            messages.append(match.group(1))
    value = messages[-1] if messages else fallback
    return stable_verifier_text(value, MAX_FAILURE_MESSAGE)


def failure_locus_evidence(
    test: dict[str, Any], failure_class: str | None
) -> tuple[dict[str, str] | None, bool]:
    """Return one bounded locator and whether the traceback was ambiguous."""
    trace = test.get("trace") if isinstance(test.get("trace"), str) else ""
    assertion, ambiguous = assertion_expression(trace)
    if ambiguous:
        return None, True
    fields = {
        key: value
        for key, value in (
            ("location", assertion_location(trace)),
            ("assertion", assertion),
            ("message", assertion_message(trace, test.get("message"))),
        )
        if value is not None
    }
    if not fields or not any(key in fields for key in ("location", "assertion")):
        return None, False
    identity = {
        "test": stable_test_name(test.get("name")),
        "failure_class": failure_class,
        **fields,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return (
        {
            "locus_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            **fields,
        },
        False,
    )


def failure_locus(test: dict[str, Any], failure_class: str | None) -> dict[str, str] | None:
    """Return one stable, bounded locator for a verifier failure."""
    return failure_locus_evidence(test, failure_class)[0]


def require_confined_regular_file(path: Path, root: Path, label: str) -> Path:
    """Resolve one regular file while rejecting symlinks and escaped paths."""
    root = root.resolve(strict=True)
    absolute = path.absolute()
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} is outside its retained trial: {path}") from error
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = absolute.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label} is not a confined regular file: {path}")
    return resolved


def verifier_feedback_from_bytes(
    encoded: bytes, label: str = "verifier/ctrf.json"
) -> dict[str, Any]:
    """Return bounded failure classes from one retained CTRF document."""
    value = json.loads(encoded)
    results = value.get("results") if isinstance(value, dict) else None
    if not isinstance(results, dict):
        raise ValueError(f"verifier report has no object `results`: {label}")
    summary = results.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    tests = results.get("tests")
    tests = tests if isinstance(tests, list) else []
    failed_tests = [
        test
        for test in tests
        if isinstance(test, dict) and test.get("status") not in ("passed", "skipped")
    ]
    failures = []
    failure_classes = set()
    locus_counts: Counter[str] = Counter()
    unlocated = 0
    ambiguous = 0
    for test in failed_tests:
        trace = test.get("trace") if isinstance(test.get("trace"), str) else ""
        classes = re.findall(r"\b([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception))\b", trace)
        failure_class = (
            stable_verifier_text(classes[-1], MAX_FAILURE_STATUS) if classes else None
        )
        retain = len(failures) < MAX_VERIFIER_FAILURES
        if retain and failure_class:
            failure_classes.add(failure_class)
        locus, locus_ambiguous = failure_locus_evidence(test, failure_class)
        ambiguous += int(locus_ambiguous)
        unlocated += int(locus is None and not locus_ambiguous)
        if locus is not None:
            locus_counts[locus["locus_sha256"]] += 1
        if retain:
            failures.append(
                {
                    "name": stable_test_name(test.get("name")),
                    "status": stable_verifier_text(
                        test.get("status"), MAX_FAILURE_STATUS
                    ),
                    "raw_status": stable_verifier_text(
                        test.get("raw_status"), MAX_FAILURE_STATUS
                    ),
                    "failure_class": failure_class,
                    "message": stable_verifier_text(
                        test.get("message"), MAX_EVIDENCE_TEXT
                    ),
                    "locus": locus,
                    "locus_ambiguous": locus_ambiguous,
                }
            )
    ambiguous += sum(count for count in locus_counts.values() if count > 1)
    for row in failures:
        locus = row["locus"]
        if (
            isinstance(locus, dict)
            and locus_counts[locus["locus_sha256"]] > 1
        ):
            row["locus_ambiguous"] = True
    declared_failures = summary.get("failed")
    total_failures = max(
        len(failed_tests),
        declared_failures
        if type(declared_failures) is int and declared_failures >= 0
        else 0,
    )
    counts = {
        key: summary.get(key)
        for key in ("tests", "passed", "failed", "skipped", "pending", "other")
        if isinstance(summary.get(key), int)
    }
    return {
        "source": "verifier/ctrf.json",
        "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "summary": counts,
        "failure_classes": sorted(failure_classes),
        "failures": failures,
        "failure_evidence_counts": {
            "total_failed_tests": total_failures,
            "retained_failed_tests": len(failures),
            "omitted_failed_tests": max(0, total_failures - len(failures)),
            "unlocated_failed_tests": unlocated,
            "ambiguous_failed_tests": ambiguous,
        },
    }


def verifier_feedback(
    path: Path | None, *, artifact_root: Path | None = None
) -> dict[str, Any] | None:
    """Return bounded failure classes from Harbor's structured verifier report."""
    if path is None:
        return None
    root = artifact_root if artifact_root is not None else path.parent
    path = require_confined_regular_file(path, root, "Terminal-Bench result")
    report_path = path.parent / "verifier" / "ctrf.json"
    if not report_path.is_file():
        return None
    report_path = require_confined_regular_file(
        report_path, root, "Terminal-Bench verifier report"
    )
    return verifier_feedback_from_bytes(report_path.read_bytes(), str(report_path))


def trial_facts(
    path: Path | None, *, artifact_root: Path | None = None
) -> dict[str, Any]:
    if path is None:
        return {
            "task": None,
            "task_checksum": None,
            "reward": None,
            "error": None,
            "estimated_cost_usd": None,
            "verifier_feedback": None,
        }
    root = artifact_root if artifact_root is not None else path.parent
    path = require_confined_regular_file(path, root, "Terminal-Bench result")
    value = json.loads(path.read_text(encoding="utf-8"))
    verifier = value.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    error = value.get("exception_info")
    agent_result = value.get("agent_result")
    estimated_cost = (
        agent_result.get("cost_usd") if isinstance(agent_result, dict) else None
    )
    return {
        "task": value.get("task_name"),
        "task_checksum": value.get("task_checksum"),
        "reward": reward if isinstance(reward, (int, float)) else None,
        "error": error,
        "estimated_cost_usd": (
            estimated_cost if isinstance(estimated_cost, (int, float)) else None
        ),
        "verifier_feedback": verifier_feedback(path, artifact_root=root),
    }


def verification_timeline(
    episode_id: str,
    results: list[dict[str, Any]],
    outcome: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep the final edit and bounded results that followed it."""
    last_edit = max(
        (index for index, row in enumerate(results) if row.get("tool") == "edit"),
        default=None,
    )
    if last_edit is None:
        selected = results[-MAX_VERIFICATION_RESULTS:]
        omitted = max(0, len(results) - len(selected))
        last_edit_seq = None
    else:
        after_edit = results[last_edit:]
        selected = (
            after_edit
            if len(after_edit) <= MAX_VERIFICATION_RESULTS
            else [after_edit[0], *after_edit[-(MAX_VERIFICATION_RESULTS - 1) :]]
        )
        omitted = max(0, len(after_edit) - len(selected))
        last_edit_seq = results[last_edit].get("seq")
    fields = (
        "seq",
        "step",
        "call_id",
        "tool",
        "subject",
        "is_error",
        "exit_code",
        "timed_out",
        "truncated",
    )
    return {
        "episode_id": episode_id,
        "last_edit_seq": last_edit_seq,
        "results": [{key: row.get(key) for key in fields} for row in selected],
        "omitted_results": omitted,
        "outcome": outcome,
    }


def diagnose_episode(
    log_dir: Path,
    *,
    trial_result: Path | None = None,
    top_results: int = 8,
) -> dict[str, Any]:
    """Return evidence that lets an optimizer locate costly or failed behavior."""
    if top_results <= 0:
        raise ValueError("top_results must be positive")
    tree = read_episode_tree(log_dir)
    root_events = tree[0][1]
    starts = [event for event in root_events if event.get("type") == "episode/start"]
    if len(starts) != 1:
        raise ValueError("root episode log must contain one episode/start event")
    start = data(starts[0])
    episode_rows = []
    requests_by_episode: dict[str, list[dict[str, Any]]] = {}
    messages_by_episode: dict[str, list[dict[str, Any]]] = {}
    results_by_episode: dict[str, list[dict[str, Any]]] = {}
    outcomes_by_episode: dict[str, dict[str, Any] | None] = {}
    for path, events in tree:
        episode_starts = [event for event in events if event.get("type") == "episode/start"]
        if len(episode_starts) != 1:
            raise ValueError(f"episode log must contain one episode/start event: {path}")
        episode_start = data(episode_starts[0])
        episode_id = episode_start.get("id")
        if not isinstance(episode_id, str):
            raise ValueError(f"episode/start has no string id: {path}")
        requests = [event for event in events if event.get("type") == "model/request"]
        messages = [event for event in events if event.get("type") == "assistant/message"]
        results = [event for event in events if event.get("type") == "tool/result"]
        requests_by_episode[episode_id] = requests
        messages_by_episode[episode_id] = messages
        results_by_episode[episode_id] = results
        ends = [event for event in events if event.get("type") == "episode/end"]
        episode_outcome = data(ends[-1]).get("outcome") if ends else None
        outcomes_by_episode[episode_id] = (
            episode_outcome if isinstance(episode_outcome, dict) else None
        )
        program = episode_start.get("program") if isinstance(episode_start.get("program"), dict) else {}
        model = program.get("model") if isinstance(program.get("model"), dict) else {}
        episode_rows.append(
            {
                "episode_id": episode_id,
                "parent_id": episode_start.get("parent_id"),
                "program": program.get("name"),
                "model": (
                    f"{model.get('provider')}/{model.get('model')}"
                    if isinstance(model.get("provider"), str) and isinstance(model.get("model"), str)
                    else None
                ),
                "model_calls": len(requests),
                "tool_results": len(results),
                "outcome": episode_outcome,
            }
        )

    usage_staircase = []
    for episode_id, messages in messages_by_episode.items():
        for event in messages:
            item = data(event)
            usage = item.get("usage")
            if not isinstance(usage, dict):
                continue
            if not all(isinstance(usage.get(key), int) for key in ("input", "output", "cache_read")):
                continue
            usage_staircase.append(
                {
                    "episode_id": episode_id,
                    "seq": event.get("seq"),
                    "step": item.get("step"),
                    "input_tokens": usage["input"],
                    "cache_read_tokens": usage["cache_read"],
                    "output_tokens": usage["output"],
                }
            )

    call_counts: Counter[tuple[str, str]] = Counter()
    for messages in messages_by_episode.values():
        for event in messages:
            for call in data(event).get("tool_calls", []):
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    continue
                key = (
                    str(call.get("name")),
                    json.dumps(call.get("args"), sort_keys=True, separators=(",", ":")),
                )
                call_counts[key] += 1

    result_rows = []
    failures = []
    for episode_id, results in results_by_episode.items():
        requests = requests_by_episode[episode_id]
        for event in results:
            item = data(event)
            rendered = item.get("rendered") if isinstance(item.get("rendered"), str) else ""
            call_id = item.get("call_id")
            replayed = (
                sum(request_contains_call(request, call_id) for request in requests)
                if isinstance(call_id, str)
                else 0
            )
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            row = {
                "episode_id": episode_id,
                "seq": event.get("seq"),
                "step": item.get("step"),
                "call_id": call_id,
                "tool": item.get("name"),
                "subject": bounded_text(item.get("subject")),
                "rendered_characters": len(rendered),
                "canonical_characters": len(json.dumps(item.get("value"), sort_keys=True)),
                "replayed_requests": replayed,
                "replayed_characters": replayed * len(rendered),
                "is_error": bool(item.get("is_error")),
                "exit_code": value.get("exit_code"),
                "timed_out": bool(value.get("timed_out")),
                "truncated": bool(value.get("truncated")),
            }
            result_rows.append(row)
            if row["is_error"] or row["timed_out"] or (
                isinstance(row["exit_code"], int) and row["exit_code"] != 0
            ):
                failures.append(row)

    ends = [event for event in root_events if event.get("type") == "episode/end"]
    outcome = data(ends[-1]).get("outcome") if ends else None
    facts = trial_facts(trial_result)
    outcome_kind = outcome.get("kind") if isinstance(outcome, dict) else None
    reward = facts["reward"]
    mismatch = None
    if reward is not None and outcome_kind is not None:
        mismatch = (reward == 1.0) != (outcome_kind == "completed")

    repeated_calls = [
        {"tool": key[0], "args": json.loads(key[1]), "count": count}
        for key, count in call_counts.most_common()
        if count > 1
    ][:top_results]
    timeline = [
        verification_timeline(
            episode_id,
            [row for row in result_rows if row["episode_id"] == episode_id],
            outcomes_by_episode[episode_id],
        )
        for episode_id in results_by_episode
    ]
    runtime = start.get("runtime") if isinstance(start.get("runtime"), dict) else {}
    return {
        "schema_version": 5,
        "evidence_identity": {
            "program_identity": start.get("identity"),
            "runtime_build": runtime.get("build"),
            "episode_id": start.get("id"),
            "task_checksum": facts["task_checksum"],
        },
        "task": facts["task"],
        "outcome": outcome,
        "verifier_reward": reward,
        "trial_error": facts["error"],
        "artifact_outcome_mismatch": mismatch,
        "verifier_feedback": facts["verifier_feedback"],
        "episodes": episode_rows,
        "verification_timeline": timeline,
        "usage": {
            "model_calls": sum(len(requests) for requests in requests_by_episode.values()),
            "tool_results": sum(len(results) for results in results_by_episode.values()),
            "input_tokens": sum(row["input_tokens"] for row in usage_staircase),
            "cache_read_tokens": sum(row["cache_read_tokens"] for row in usage_staircase),
            "output_tokens": sum(row["output_tokens"] for row in usage_staircase),
            "estimated_cost_usd": facts["estimated_cost_usd"],
            "per_request": usage_staircase,
        },
        "largest_replayed_results": sorted(
            result_rows,
            key=lambda row: (row["replayed_characters"], row["rendered_characters"]),
            reverse=True,
        )[:top_results],
        "tool_failures": failures[:top_results],
        "repeated_calls": repeated_calls,
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("episode", type=Path)
    answer.add_argument("--trial-result", type=Path)
    answer.add_argument("--top-results", type=int, default=8)
    return answer


def main() -> int:
    args = parser().parse_args()
    try:
        report = diagnose_episode(
            args.episode,
            trial_result=args.trial_result,
            top_results=args.top_results,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trajectory diagnostics: {error}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
