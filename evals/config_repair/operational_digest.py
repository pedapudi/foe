#!/usr/bin/python3
"""Digest operational failures from declared repair-loop attempt directories.

Each attempt directory holds contract.json, plan.json, episode/, and
optionally evaluation.json and candidate.json, as the directory README
states. The digest reads exactly the directories it is given — it never
searches a tree for attempts — and reports only mechanically attributable
signals: every row cites the reconstructable event by episode id and log
sequence, or names plan.json as its source for signals that exist only in
plan output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent / "terminal_bench"))
from trajectory_diagnostics import bounded_text, data, read_episode_tree


SHELL_TOOLS = ("bash", "session")
WRITE_TOOLS = ("edit",)
POSSIBLE_DENIAL_BASIS = (
    "exit status 126 with `Permission denied` on standard error; "
    "a heuristic signal, not an established cause"
)
FIELD_PATTERNS = (
    re.compile(r"required property `([^`]+)`"),
    re.compile(r"unexpected property `([^`]+)`"),
    re.compile(r"^arguments\.([A-Za-z0-9_]+)"),
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_attempt(directory: Path) -> dict[str, Any]:
    """Read one declared attempt directory's retained files."""
    if not directory.is_dir():
        raise FileNotFoundError(f"attempt directory does not exist: {directory}")
    evaluation_path = directory / "evaluation.json"
    return {
        "directory": directory,
        "contract": read_json(directory / "contract.json"),
        "plan": read_json(directory / "plan.json"),
        "tree": read_episode_tree(directory / "episode"),
        "evaluation": read_json(evaluation_path) if evaluation_path.is_file() else None,
    }


def failure_field(failure: dict[str, Any]) -> str | None:
    """The argument field a typed tool failure names, when it names one."""
    details = failure.get("details")
    if isinstance(details, dict) and isinstance(details.get("field"), str):
        return details["field"]
    message = failure.get("message")
    if isinstance(message, str):
        for pattern in FIELD_PATTERNS:
            match = pattern.search(message)
            if match:
                return match.group(1)
    return None


def episode_results(tree: list[tuple[Path, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Every tool result in the tree with its episode id and citation."""
    rows = []
    for path, events in tree:
        starts = [event for event in events if event.get("type") == "episode/start"]
        if len(starts) != 1:
            raise ValueError(f"episode log must contain one episode/start event: {path}")
        episode_id = data(starts[0]).get("id")
        for event in events:
            if event.get("type") != "tool/result":
                continue
            item = data(event)
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            failure = item.get("failure") if isinstance(item.get("failure"), dict) else None
            rows.append(
                {
                    "episode_id": episode_id,
                    "seq": event.get("seq"),
                    "step": item.get("step"),
                    "tool": item.get("name"),
                    "is_error": bool(item.get("is_error")),
                    "failure": failure,
                    "value": value,
                }
            )
    return rows


def enforced_permission_denials(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runtime-originated capability-denied tool failures."""
    rows = []
    for row in results:
        failure = row["failure"]
        if failure is None or failure.get("code") != "capability-denied":
            continue
        rows.append(
            {
                "episode_id": row["episode_id"],
                "seq": row["seq"],
                "tool": row["tool"],
                "message": bounded_text(failure.get("message")),
                "details": failure.get("details"),
            }
        )
    return rows


def possible_permission_denials(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Exit-126 process results, labeled as the heuristic they are."""
    rows = []
    for row in results:
        value = row["value"]
        if value.get("permission_denial") != "possible" and value.get("exit_code") != 126:
            continue
        rows.append(
            {
                "episode_id": row["episode_id"],
                "seq": row["seq"],
                "tool": row["tool"],
                "command": bounded_text(value.get("command")),
                "exit_code": value.get("exit_code"),
            }
        )
    return {"basis": POSSIBLE_DENIAL_BASIS, "rows": rows}


def typed_failure_counts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Typed tool failures keyed by {tool, failure code, field}, with citations.

    The invalid-call rows are the failure code `invalid-call`; the same key
    shape covers every other typed failure a result carries.
    """
    grouped: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for row in results:
        failure = row["failure"]
        if failure is None:
            continue
        key = (row["tool"], failure.get("code"), failure_field(failure))
        grouped.setdefault(key, []).append({"episode_id": row["episode_id"], "seq": row["seq"]})
    return [
        {
            "tool": tool,
            "failure_code": code,
            "field": field,
            "count": len(citations),
            "citations": citations,
        }
        for (tool, code, field), citations in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), str(item[0]))
        )
    ]


def failed_command(row: dict[str, Any]) -> str | None:
    """The shell command of a failed bash or session result, when one ran."""
    if row["tool"] not in SHELL_TOOLS:
        return None
    value = row["value"]
    command = value.get("command")
    if not isinstance(command, str):
        return None
    exit_code = value.get("exit_code")
    failed = row["is_error"] or bool(value.get("timed_out")) or (
        isinstance(exit_code, int) and exit_code != 0
    )
    return command if failed else None


def repeated_failed_commands(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in results:
        command = failed_command(row)
        if command is None:
            continue
        grouped.setdefault((row["tool"], command), []).append(
            {"episode_id": row["episode_id"], "seq": row["seq"]}
        )
    return [
        {
            "tool": tool,
            "command": bounded_text(command),
            "count": len(citations),
            "citations": citations,
        }
        for (tool, command), citations in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0])
        )
        if len(citations) > 1
    ]


def first_productive_event(events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    """The first successful write, execution, or verification, and the
    number of tool results before it."""
    calls_before = 0
    for event in events:
        if event.get("type") == "verification/result" and data(event).get("status") == "accepted":
            return {"seq": event.get("seq"), "kind": "verification-accepted"}, calls_before
        if event.get("type") != "tool/result":
            continue
        item = data(event)
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        if not item.get("is_error"):
            if item.get("name") in WRITE_TOOLS:
                return {"seq": event.get("seq"), "kind": "successful-write"}, calls_before
            if item.get("name") in SHELL_TOOLS and value.get("exit_code") == 0:
                return {"seq": event.get("seq"), "kind": "successful-execution"}, calls_before
        calls_before += 1
    return None, calls_before


def calls_before_first_productive(
    tree: list[tuple[Path, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    rows = []
    for _, events in tree:
        starts = [event for event in events if event.get("type") == "episode/start"]
        episode_id = data(starts[0]).get("id") if starts else None
        first, calls_before = first_productive_event(events)
        rows.append(
            {
                "episode_id": episode_id,
                "first_productive": first,
                "calls_before": calls_before,
            }
        )
    return rows


def configuration_warnings(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Static warnings from the retained plan.json; they appear only in plan
    output, never in episode logs."""
    warnings = plan.get("warnings")
    if not isinstance(warnings, list):
        raise ValueError("plan.json has no warnings list")
    return [{**warning, "source": "plan.json"} for warning in warnings if isinstance(warning, dict)]


def root_outcome(tree: list[tuple[Path, list[dict[str, Any]]]]) -> dict[str, Any] | None:
    ends = [event for event in tree[0][1] if event.get("type") == "episode/end"]
    if not ends:
        return None
    outcome = data(ends[-1]).get("outcome")
    return {**outcome, "end_seq": ends[-1].get("seq")} if isinstance(outcome, dict) else None


def completed_artifacts_with_noncompleted_outcomes(
    evaluation: dict[str, Any] | None,
    outcome: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attempts whose retained evaluation passed the task-artifact check
    while the episode outcome is not `completed`."""
    if evaluation is None or outcome is None or outcome.get("kind") == "completed":
        return []
    checks = evaluation.get("checks")
    if not isinstance(checks, list):
        return []
    return [
        {"check": check.get("name"), "outcome": outcome}
        for check in checks
        if isinstance(check, dict)
        and check.get("name") == "task-artifact"
        and check.get("passed") is True
    ]


def digest_attempt(directory: Path) -> dict[str, Any]:
    attempt = read_attempt(directory)
    results = episode_results(attempt["tree"])
    outcome = root_outcome(attempt["tree"])
    return {
        "attempt": str(directory),
        "contract_fingerprint": attempt["plan"].get("contract_fingerprint"),
        "outcome": outcome,
        "configuration_warnings": configuration_warnings(attempt["plan"]),
        "enforced_permission_denials": enforced_permission_denials(results),
        "possible_permission_denials": possible_permission_denials(results),
        "typed_failure_counts": typed_failure_counts(results),
        "repeated_failed_commands": repeated_failed_commands(results),
        "calls_before_first_productive": calls_before_first_productive(attempt["tree"]),
        "completed_artifacts_with_noncompleted_outcomes": (
            completed_artifacts_with_noncompleted_outcomes(attempt["evaluation"], outcome)
        ),
    }


def cross_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-attempt rows over every declared attempt."""
    failure_totals: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    command_totals: dict[tuple[str, str], dict[str, Any]] = {}
    warning_totals: dict[tuple[Any, Any], dict[str, Any]] = {}
    denial_totals = []
    for attempt in attempts:
        name = attempt["attempt"]
        for row in attempt["typed_failure_counts"]:
            key = (row["tool"], row["failure_code"], row["field"])
            entry = failure_totals.setdefault(
                key,
                {
                    "tool": row["tool"],
                    "failure_code": row["failure_code"],
                    "field": row["field"],
                    "count": 0,
                    "attempts": [],
                },
            )
            entry["count"] += row["count"]
            entry["attempts"].append(name)
        for row in attempt["repeated_failed_commands"]:
            key = (row["tool"], row["command"])
            entry = command_totals.setdefault(
                key,
                {"tool": row["tool"], "command": row["command"], "count": 0, "attempts": []},
            )
            entry["count"] += row["count"]
            entry["attempts"].append(name)
        for row in attempt["configuration_warnings"]:
            key = (row.get("code"), row.get("configuration_key"))
            entry = warning_totals.setdefault(
                key,
                {
                    "code": row.get("code"),
                    "configuration_key": row.get("configuration_key"),
                    "attempts": [],
                },
            )
            entry["attempts"].append(name)
        denial_totals.append(
            {
                "attempt": name,
                "enforced": len(attempt["enforced_permission_denials"]),
                "possible": len(attempt["possible_permission_denials"]["rows"]),
            }
        )
    return {
        "typed_failure_counts": sorted(
            failure_totals.values(), key=lambda row: (-row["count"], str(row["tool"]))
        ),
        "repeated_failed_commands": sorted(
            command_totals.values(), key=lambda row: (-row["count"], row["command"] or "")
        ),
        "configuration_warnings": sorted(
            warning_totals.values(), key=lambda row: (str(row["code"]), str(row["configuration_key"]))
        ),
        "permission_denial_totals": denial_totals,
    }


def digest(directories: list[Path]) -> dict[str, Any]:
    attempts = [digest_attempt(directory) for directory in directories]
    return {
        "schema_version": 1,
        "attempts": attempts,
        "cross_attempt": cross_attempt(attempts),
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("attempts", nargs="+", type=Path, metavar="ATTEMPT_DIR")
    return answer


def main() -> int:
    args = parser().parse_args()
    try:
        report = digest(args.attempts)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"operational digest: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
