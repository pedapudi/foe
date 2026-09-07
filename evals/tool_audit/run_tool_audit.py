#!/usr/bin/python3
"""Exercise every built-in coding tool with scripted malformed calls.

Each case submits one common mistake — a wrong field name, a missing required
field, a wrong type, an out-of-range or otherwise unusable value, or a call
that needs a capability the contract does not grant — through a real foe
episode, so the failure the model would see is produced by the actual
validation and dispatch path. The suite then asserts the typed failure the
log recorded: the failure code, whether another attempt can succeed, and the
exact message. A change to any of those fails the suite, so an error-message
regression is loud.

The report keys the mistake inventory by {tool, failure code, field}: a
shared code alone can cover different mistakes, so the field at fault is part
of the identity of a case.

Exit status 0 means every case produced its expected failure, 1 means at
least one expectation did not hold, and 2 means the suite could not run and
states nothing about the runtime.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import host_runtime  # noqa: E402
import responses  # noqa: E402

CONFORMANT = 0
VIOLATION = 1
HARNESS_FAILED = 2

SAMPLE = "alpha\nbeta\ngamma\n"
DUPLICATE = "twin\ntwin\n"

# Substitution markers usable in an expected message. Literal braces appear
# in some library messages, so replacement does not use str.format.
WS = "<WS>"
DENIED = "<DENIED>"
SAMPLE_VERSION = "<SAMPLE_VERSION>"


class HarnessError(Exception):
    """The suite could not run, so it states nothing about the runtime."""


def invalid(tool: str, reason: str) -> str:
    return f"The arguments for `{tool}` are invalid: {reason}"


def case(
    name: str,
    tool: str,
    args: Any,
    code: str,
    field: str | None,
    message: str,
    retryable: bool = True,
    call_name: str | None = None,
) -> dict[str, Any]:
    return {
        "case": name,
        "tool": tool,
        "call": {"id": name, "name": call_name or tool, "args": args},
        "code": code,
        "field": field,
        "message": message,
        "retryable": retryable,
    }


# One entry per scripted mistake. `message` is the complete expected message;
# a change to runtime behavior must change this table in the same commit.
CASES: list[dict[str, Any]] = [
    # ---- dispatch validation: the shared schema check --------------------
    case(
        "unknown-tool-name",
        "reed",
        {"path": "sample.txt"},
        "invalid-call",
        None,
        "No tool named `reed` is available to this contract.",
    ),
    case(
        "read-args-not-an-object",
        "read",
        ["sample.txt"],
        "invalid-call",
        None,
        invalid("read", "arguments must be one JSON object"),
    ),
    case(
        "read-wrong-field-name",
        "read",
        {"path": "sample.txt", "file": "sample.txt"},
        "invalid-call",
        "file",
        invalid("read", "arguments: has unexpected property `file`; the properties are limit, offset, path"),
    ),
    case(
        "read-missing-required-path",
        "read",
        {},
        "invalid-call",
        "path",
        invalid("read", "arguments: lacks required property `path`"),
    ),
    case(
        "read-path-wrong-type",
        "read",
        {"path": 7},
        "invalid-call",
        "path",
        invalid("read", "path: expected type string, found integer"),
    ),
    case(
        "read-offset-below-minimum",
        "read",
        {"path": "sample.txt", "offset": 0},
        "invalid-call",
        "offset",
        invalid("read", "offset: is 0, outside `minimum` 1"),
    ),
    case(
        "grep-missing-required-pattern",
        "grep",
        {},
        "invalid-call",
        "pattern",
        invalid("grep", "arguments: lacks required property `pattern`"),
    ),
    case(
        "grep-wrong-field-name",
        "grep",
        {"pattern": "alpha", "regex": "alpha"},
        "invalid-call",
        "regex",
        invalid(
            "grep",
            "arguments: has unexpected property `regex`; the properties are "
            "context, glob, ignore_case, limit, literal, path, pattern",
        ),
    ),
    case(
        "grep-context-below-minimum",
        "grep",
        {"pattern": "alpha", "context": -1},
        "invalid-call",
        "context",
        invalid("grep", "context: is -1, outside `minimum` 0"),
    ),
    case(
        "edit-missing-required-edits",
        "edit",
        {"path": "sample.txt"},
        "invalid-call",
        "edits",
        invalid("edit", "arguments: lacks required property `edits`"),
    ),
    case(
        "edit-empty-edits-list",
        "edit",
        {"path": "sample.txt", "edits": []},
        "invalid-call",
        "edits",
        invalid("edit", "edits: is 0 items long, outside `minItems` 1"),
    ),
    case(
        "edit-entry-missing-new-text",
        "edit",
        {"path": "sample.txt", "edits": [{"old_text": "alpha"}]},
        "invalid-call",
        "new_text",
        invalid("edit", "edits[0]: lacks required property `new_text`"),
    ),
    case(
        "bash-missing-required-command",
        "bash",
        {},
        "invalid-call",
        "command",
        invalid("bash", "arguments: lacks required property `command`"),
    ),
    case(
        "bash-timeout-wrong-type",
        "bash",
        {"command": "true", "timeout_seconds": "soon"},
        "invalid-call",
        "timeout_seconds",
        invalid("bash", "timeout_seconds: expected type integer, found string"),
    ),
    case(
        "bash-timeout-below-minimum",
        "bash",
        {"command": "true", "timeout_seconds": 0},
        "invalid-call",
        "timeout_seconds",
        invalid("bash", "timeout_seconds: is 0, outside `minimum` 1"),
    ),
    case(
        "session-missing-required-action",
        "session",
        {},
        "invalid-call",
        "action",
        invalid("session", "arguments: lacks required property `action`"),
    ),
    case(
        "session-unknown-action",
        "session",
        {"action": "restart"},
        "invalid-call",
        "action",
        invalid("session", 'action: is not one of ["start","poll","write","signal","stop"]'),
    ),
    case(
        "session-lifetime-outside-enum",
        "session",
        {"action": "start", "command": "true", "lifetime": "forever"},
        "invalid-call",
        "lifetime",
        invalid("session", 'lifetime: is not one of ["episode","task"]'),
    ),
    case(
        "python-missing-required-source",
        "compose_tools",
        {},
        "invalid-call",
        "source",
        invalid("compose_tools", "arguments: lacks required property `source`"),
    ),
    case(
        "python-wrong-field-name",
        "compose_tools",
        {"source": "def main():\n    return 1", "code": "x"},
        "invalid-call",
        "code",
        invalid(
            "compose_tools",
            "arguments: has unexpected property `code`; the properties are source, timeout_seconds",
        ),
    ),
    case(
        "retrieve-missing-required-cursor",
        "retrieve",
        {},
        "invalid-call",
        "cursor",
        invalid("retrieve", "arguments: lacks required property `cursor`"),
    ),
    # ---- tool-level validation: values the schema cannot judge -----------
    case(
        "read-offset-past-end",
        "read",
        {"path": "sample.txt", "offset": 999},
        "operation-failed",
        "offset",
        "read: offset 999 is past the end of sample.txt, which has 3 lines",
    ),
    case(
        "read-binary-file",
        "read",
        {"path": "binary.bin"},
        "operation-failed",
        "path",
        "read: binary.bin is a binary file (4 bytes, contains a NUL byte)",
    ),
    case(
        "read-absent-file",
        "read",
        {"path": "absent.txt"},
        "operation-failed",
        "path",
        "read: absent.txt: No such file or directory (os error 2)",
    ),
    case(
        "grep-invalid-regex",
        "grep",
        {"pattern": "("},
        "invalid-call",
        "pattern",
        "grep: invalid pattern: regex parse error:\n    (?:()\n    ^\nerror: unclosed group"
        "; set literal to true to match it as a fixed string",
    ),
    case(
        "grep-invalid-glob",
        "grep",
        {"pattern": "alpha", "glob": "["},
        "invalid-call",
        "glob",
        'grep: invalid glob "[": error parsing glob \'[\': unclosed character class; missing \']\'',
    ),
    case(
        "edit-old-text-not-found",
        "edit",
        {"path": "sample.txt", "edits": [{"old_text": "delta", "new_text": "epsilon"}]},
        "operation-failed",
        "old_text",
        "edits[0]: old_text occurs 0 times in sample.txt; it must occur exactly once "
        "(the text must match the file exactly, including whitespace)",
    ),
    case(
        "edit-old-text-ambiguous",
        "edit",
        {"path": "dup.txt", "edits": [{"old_text": "twin", "new_text": "one"}]},
        "operation-failed",
        "old_text",
        "edits[0]: old_text occurs 2 times in dup.txt; it must occur exactly once "
        "(include more surrounding lines to make it unique)",
    ),
    case(
        "edit-stale-expected-version",
        "edit",
        {"path": "sample.txt", "expected_version": "sha256:0", "edits": [{"old_text": "alpha", "new_text": "a"}]},
        "operation-failed",
        "expected_version",
        f"edit: sample.txt has version {SAMPLE_VERSION}, which differs from expected_version sha256:0",
    ),
    case(
        "edit-create-on-nonempty-file",
        "edit",
        {"path": "sample.txt", "edits": [{"old_text": "", "new_text": "fresh\n"}]},
        "operation-failed",
        "old_text",
        "edits[0]: empty old_text requires a missing or empty file; sample.txt contains text",
    ),
    case(
        "bash-command-with-nul",
        "bash",
        {"command": "\x00ls"},
        "invalid-call",
        "command",
        "bash: command contains U+0000; process arguments cannot contain NUL. "
        "Use shell syntax such as printf '\\0' to create a NUL byte in a process stream.",
    ),
    case(
        "session-start-missing-command",
        "session",
        {"action": "start"},
        "invalid-call",
        "command",
        "session: `start` requires `command`",
    ),
    case(
        "session-poll-missing-id",
        "session",
        {"action": "poll"},
        "invalid-call",
        "session",
        "session: this action requires `session`, the id `start` returned",
    ),
    case(
        "session-poll-unknown-id",
        "session",
        {"action": "poll", "session": 99},
        "operation-failed",
        "session",
        "session: session 99: no session has this id",
    ),
    case(
        "session-write-missing-input",
        "session",
        {"action": "write", "session": 1},
        "invalid-call",
        "input",
        "session: `write` requires `input`",
    ),
    case(
        "session-signal-missing-signal",
        "session",
        {"action": "signal", "session": 1},
        "invalid-call",
        "signal",
        "session: `signal` requires `signal`",
    ),
    case(
        "session-lifetime-on-poll",
        "session",
        {"action": "poll", "session": 1, "lifetime": "episode"},
        "invalid-call",
        "lifetime",
        "session: `lifetime` applies only to `start`",
    ),
    case(
        "python-source-over-bound",
        "compose_tools",
        {"source": {"$bytes": 66000}},
        "limit-exceeded",
        "source",
        "compose_tools: the source is 66000 bytes; the bound is 65536",
    ),
    case(
        "retrieve-malformed-cursor",
        "retrieve",
        {"cursor": "bogus"},
        "invalid-call",
        "cursor",
        "retrieve: cursor syntax, version, or checksum is invalid; "
        "copy the whole cursor from its notice unchanged",
    ),
    # ---- unavailable capability: distinct from malformed input -----------
    case(
        "read-outside-read-grants",
        "read",
        {"path": DENIED},
        "capability-denied",
        "path",
        f"read: {DENIED}: {DENIED} is outside this tool's filesystem permissions; "
        "review grants.read and grants.write",
        retryable=False,
    ),
    case(
        "edit-outside-write-grants",
        "edit",
        {"path": DENIED, "edits": [{"old_text": "denied", "new_text": "x"}]},
        "capability-denied",
        "path",
        f"edit: {DENIED}: {DENIED} is outside this tool's filesystem permissions; "
        "review grants.read and grants.write",
        retryable=False,
    ),
    case(
        "session-task-lifetime-without-grant",
        "session",
        {"action": "start", "command": "true", "lifetime": "task"},
        "capability-denied",
        "lifetime",
        "session: capability denied: grants.task_session does not authorize a task-lifetime session",
        retryable=False,
    ),
]


def substitute(message: str, values: dict[str, str]) -> str:
    for marker, value in values.items():
        message = message.replace(marker, value)
    return message


def config(workspace: Path) -> dict[str, Any]:
    return {
        "version": 4,
        "name": "tool-mistake-audit",
        "instructions": {"role": "Submit the scripted mistake calls."},
        "tools": ["read", "grep", "edit", "bash", "session", "compose_tools", "retrieve"],
        "grants": {"read": [str(workspace)], "write": [str(workspace)]},
        "budget": {"model_calls": 4, "input_tokens": 40000, "output_tokens": 8000, "seconds": 120},
        "sandbox": {"mode": "off"},
        "task": "Submit every scripted mistake call and report completion.",
    }


def sandbox_config(workspace: Path) -> dict[str, Any]:
    shaped = config(workspace)
    shaped["tools"] = ["bash"]
    shaped["sandbox"] = {"mode": "required"}
    return shaped


def run_episode(
    binary: Path,
    case_dir: Path,
    shaped: dict[str, Any],
    responder: host_runtime.Responder,
) -> list[dict[str, Any]]:
    config_path = case_dir / "config.json"
    log_parent = case_dir / "episode"
    config_path.write_text(json.dumps(shaped, indent=2) + "\n", encoding="utf-8")
    try:
        status, log_dir = host_runtime.run(binary, config_path, log_parent, responder)
    except (OSError, RuntimeError) as error:
        raise HarnessError(f"foe could not run the mistake episode: {error}") from error
    if status != 0:
        raise HarnessError(f"the mistake episode exited {status}")
    log_path = log_dir / "episode.jsonl"
    if not log_path.is_file():
        raise HarnessError(f"foe wrote no episode log under {log_dir}")
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tool_results(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        event["data"]["call_id"]: event["data"]
        for event in events
        if event.get("type") == "tool/result"
    }


def check_case(expected: dict[str, Any], results: dict[str, dict[str, Any]], values: dict[str, str]) -> list[str]:
    """Mismatches between one case's expectation and the recorded result."""
    name = expected["case"]
    result = results.get(name)
    if result is None:
        return [f"{name}: no tool/result was recorded"]
    findings = []
    if result.get("is_error") is not True:
        findings.append(f"{name}: the call did not fail")
    failure = result.get("failure") or {}
    if failure.get("code") != expected["code"]:
        findings.append(f"{name}: failure code is {failure.get('code')!r}, expected {expected['code']!r}")
    if failure.get("retryable") is not expected["retryable"]:
        findings.append(f"{name}: retryable is {failure.get('retryable')!r}, expected {expected['retryable']!r}")
    message = substitute(expected["message"], values)
    if failure.get("message") != message:
        findings.append(
            f"{name}: message differs\n  expected: {message!r}\n  recorded: {failure.get('message')!r}"
        )
    field = expected["field"]
    if field is not None:
        args = expected["call"]["args"]
        value = args.get(field) if isinstance(args, dict) else None
        shown = substitute(str(value), values) if isinstance(value, str) else None
        recorded = str(failure.get("message"))
        if field not in recorded and (shown is None or shown not in recorded):
            findings.append(f"{name}: the message names neither the field `{field}` nor its value")
    return findings


def check_bash_denial(results: dict[str, dict[str, Any]]) -> list[str]:
    """The sandbox-refused external command keeps the guidance shape.

    A kernel-sandbox denial is exit 126 with `Permission denied` on standard
    error; the result is not a tool error, `permission_denial` is `possible`,
    and the rendering names grants.execute as the key to change.
    """
    result = results.get("bash-denied-executable")
    if result is None:
        return ["bash-denied-executable: no tool/result was recorded"]
    findings = []
    value = result.get("value", {})
    if result.get("is_error") is not False:
        findings.append("bash-denied-executable: the denial was reported as a tool error")
    if value.get("exit_code") != 126:
        findings.append(f"bash-denied-executable: exit code is {value.get('exit_code')!r}, expected 126")
    if value.get("permission_denial") != "possible":
        findings.append("bash-denied-executable: permission_denial is not `possible`")
    if "grants.execute" not in result.get("rendered", ""):
        findings.append("bash-denied-executable: the rendering does not name grants.execute")
    return findings


def inventory(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    keyed = []
    for entry in cases:
        by_code[entry["code"]] = by_code.get(entry["code"], 0) + 1
        keyed.append(
            {"tool": entry["tool"], "code": entry["code"], "field": entry["field"], "case": entry["case"]}
        )
    return {"cases": len(cases), "by_code": dict(sorted(by_code.items())), "by_tool_code_field": keyed}


def run(args: argparse.Namespace, run_root: Path) -> int:
    binary = args.foe.resolve()
    if not binary.is_file():
        raise HarnessError(f"foe binary does not exist: {binary}")
    workspace = run_root / "workspace"
    denied = run_root / "denied" / "secret.txt"
    try:
        workspace.mkdir(parents=True)
        denied.parent.mkdir()
        (workspace / "sample.txt").write_text(SAMPLE, encoding="utf-8")
        (workspace / "dup.txt").write_text(DUPLICATE, encoding="utf-8")
        (workspace / "binary.bin").write_bytes(b"a\x00b\n")
        denied.write_text("denied secret\n", encoding="utf-8")
    except OSError as error:
        raise HarnessError(f"the case fixtures could not be created under {run_root}: {error}") from error
    values = {
        WS: str(workspace),
        DENIED: str(denied),
        SAMPLE_VERSION: "sha256:" + hashlib.sha256(SAMPLE.encode()).hexdigest(),
    }

    mistake_dir = run_root / "mistakes"
    mistake_dir.mkdir()
    shaped = [dict(entry["call"], args=json.loads(substitute(json.dumps(entry["call"]["args"]), values)))
              for entry in CASES]
    responder = functools.partial(responses.respond, calls=shaped)
    events = run_episode(binary, mistake_dir, config(workspace), responder)
    results = tool_results(events)

    findings: list[str] = []
    for entry in CASES:
        findings.extend(check_case(entry, results, values))

    if args.include_kernel_sandbox:
        sandbox_dir = run_root / "sandbox"
        sandbox_dir.mkdir()
        sandbox_calls = [
            {"id": "bash-denied-executable", "name": "bash", "args": {"command": "/usr/bin/id"}}
        ]
        sandbox_events = run_episode(
            binary,
            sandbox_dir,
            sandbox_config(workspace),
            functools.partial(responses.respond, calls=sandbox_calls),
        )
        findings.extend(check_bash_denial(tool_results(sandbox_events)))

    report = {
        "suite": "tool-mistake-audit",
        "inventory": inventory(CASES),
        "kernel_sandbox_denial_probe": "checked" if args.include_kernel_sandbox else "not_requested",
        "findings": findings,
    }
    print(json.dumps(report, indent=2))
    for finding in findings:
        print(f"tool audit finding: {finding}", file=sys.stderr)
    return CONFORMANT if not findings else VIOLATION


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Exit status 0 means every scripted mistake produced its expected typed failure, "
            "1 means at least one did not, and 2 means the suite could not run."
        ),
    )
    parser.add_argument("--foe", type=Path, required=True, help="Path to the built foe binary.")
    parser.add_argument(
        "--include-kernel-sandbox",
        action="store_true",
        help="Require Landlock and check the shape of a sandbox-refused external command.",
    )
    parser.add_argument("--keep", type=Path, help="Keep configurations, fixtures, and logs in this directory.")
    args = parser.parse_args()
    try:
        if args.keep is None:
            with tempfile.TemporaryDirectory(prefix="foe-tool-audit-") as temporary:
                return run(args, Path(temporary))
        run_root = args.keep.resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        return run(args, run_root)
    except (HarnessError, OSError) as error:
        print(f"the tool mistake audit could not run: {error}", file=sys.stderr)
        return HARNESS_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
