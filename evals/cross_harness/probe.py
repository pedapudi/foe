#!/usr/bin/python3
"""Whether the host can run the cross-harness evaluation, checked without a model.

The evaluation compares foe with Codex CLI on the same tasks. Its results
mean something only when both harnesses run on this host the way their
documents describe, so this probe establishes that once, deterministically,
before any model spend. It runs a fixed list of checks and prints one JSON
report on the first line of standard output followed by a table.

The checks:

- `foe-kernel-sandbox`: one scripted episode in `required` sandbox mode,
  served through the host runtime under `evals/`, whose only bash call
  writes outside its write grant. The check passes when the kernel refused
  the write, the file does not exist afterwards, and the `episode/start`
  event records a Landlock ABI above 0. A refusal is a `Permission denied`,
  `Operation not permitted`, or `Read-only file system` diagnostic in the
  call's standard error, or a `capability-denied` failure on the result. A
  result that is an error for any other reason, such as a timeout, leaves
  the sandbox unmeasured and the check not run.
- `codex-sandbox`: `codex sandbox -P :read-only` runs one bash command that
  writes a scratch file. The check passes when the sandbox refused the
  write and the file does not exist afterwards.
- `codex-decoy-home` (optional): a decoy Codex home directory holds an
  `AGENTS.md` with a sentence that occurs nowhere else, and `codex sandbox`
  runs `/usr/bin/env` under that home. The check passes when the command's
  environment names the decoy as `CODEX_HOME` and the sentence is absent
  from the environment and from everything the sandbox printed. `codex
  sandbox` runs no model and reads no instructions file, so this establishes
  that the decoy reaches a sandboxed command and nothing about model
  requests. Whether a model request carries the sentence needs a model and
  is the `codex-isolation-canary-model-request` row, reported here as not
  run.
- `versions`: the foe runtime version and build hash, read from the
  `episode/start` event of the sandbox episode because the binary takes no
  version flag, and the output of `codex --version`.
- `route-models`: when `--route` names the model route both harnesses
  share, `GET <route>/v1/models` answers within five seconds.
- `cargo-test-wall-time`: the wall time of the Rust fixture's test suite,
  which the task runner measures because it needs the fixture; reported
  here as not run.

The exit status is 0 when every required check passed, 1 when a required
check failed, and 2 when no required check failed and a required check
could not run. A required failure sets status 1 whichever other checks did
not run. The sandbox checks run only under `--live`, because they need the
built foe binary, the kernel's Landlock, and Codex's sandbox on this host.

`--out` receives the fixtures, the logs, `probe.json`, and `probe.md`. The
entries a previous run wrote there are removed before the checks run. A
directory holding anything else is refused, so the probe never deletes
files it did not write.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

EVALS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVALS))

import host_runtime  # noqa: E402
from runtime_responses import call, done  # noqa: E402

PASSED, FAILED, NOT_RUN = "passed", "failed", "not_run"

# Diagnostics a refused write produces: the kernel's EACCES and EPERM texts
# and a read-only bind mount.
DENIAL = re.compile(r"Permission denied|Operation not permitted|Read-only file system")

# The typed failure code a foe tool result carries when a grant refused the call.
CAPABILITY_DENIED = "capability-denied"

ROUTE_TIMEOUT_SECONDS = 5

# How long one `codex sandbox` or `codex --version` call may take before the
# check is reported as not run.
CODEX_TIMEOUT_SECONDS = 120

FOE_CALL_ID = "probe-write-outside"

# Every entry the probe writes under `--out`. The output directory is
# cleared entry by entry from this list, so a directory holding anything
# else is refused rather than removed.
OUTPUT_ENTRIES = ("foe", "codex", "decoy-home", "probe.json", "probe.md")

# The signature of `host_runtime.run`: the binary, the contract document, the
# directory the episode directory is created under, and the response
# function; it returns the command status and the episode directory.
EpisodeRunner = Callable[[Path, Path, Path, host_runtime.Responder], tuple[int, Path]]


@dataclass(frozen=True)
class Check:
    name: str
    # Whether the exit status depends on this check.
    required: bool
    status: str
    # One sentence a reader of the table can act on.
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required, "status": self.status, "detail": self.detail, "evidence": self.evidence}


def write_command(target: Path) -> str:
    """One shell command that writes `target`, with the path quoted for the shell."""
    return f"echo x > {shlex.quote(str(target))}"


def foe_document(workspace: Path) -> dict[str, Any]:
    """A contract document that grants the workspace alone and requires the kernel sandbox."""
    return {
        "version": 4,
        "name": "environment-probe",
        "instructions": {"role": "Run the one bash command the host supplies, then report."},
        "tools": ["bash"],
        "grants": {"read": [str(workspace)], "write": [str(workspace)], "execute": ["/bin", "/usr/bin"]},
        "budget": {"model_calls": 3, "seconds": 300},
        "sandbox": {"mode": "required"},
        "task": "Run the probe.",
    }


def foe_responder(target: Path) -> host_runtime.Responder:
    """One bash call that writes `target` in the first turn, then a closing message."""

    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        if any(message["role"] == "tool" for message in request["messages"]):
            return [{"kind": "text", "delta": "The probe ran."}, *done("end")]
        return [*call(FOE_CALL_ID, "bash", {"command": write_command(target), "timeout_seconds": 30}), *done("tool")]

    return respond


def result_value(log: Path, data: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    """The canonical value of a `tool/result`, read from its spill file when the log holds a locator.

    The locator names a single-component file under `spill/` beside the
    log. A locator whose file is missing or is not a JSON object raises
    `ValueError` naming the file.
    """
    value = data.get("value")
    if not isinstance(value, dict):
        return {}, None
    if "spill" not in value:
        return value, None
    name = str(value["spill"])
    if Path(name).name != name:
        raise ValueError(f"the result of {data.get('call_id')} in {log} names the spill {name!r}, which is not a single-component filename")
    spill = log.parent / "spill" / name
    if not spill.is_file():
        raise ValueError(f"the result of {data.get('call_id')} in {log} names the spill {spill}, which does not exist")
    try:
        loaded = json.loads(spill.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"the spill {spill} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"the spill {spill} holds a JSON {type(loaded).__name__} rather than the bash result object")
    return loaded, spill


def foe_log_evidence(log: Path) -> dict[str, Any]:
    """What the episode log records about the sandbox, the runtime, and the probe call.

    The result carries `sandbox` and `runtime` from `episode/start`, the
    `tool/result` of the probe call as `result`, and the terminal outcome.
    A key is absent when the log does not record it. A line that is not
    valid JSON, such as the tail of a log the runtime died while writing,
    raises `ValueError` naming the log and the line number.
    """
    evidence: dict[str, Any] = {"log": str(log)}
    for number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{log} line {number} is not valid JSON: {exc}") from exc
        data = event.get("data") or {}
        if event.get("type") == "episode/start":
            evidence["sandbox"] = {"mode": (data.get("sandbox") or {}).get("mode"), "landlock_abi": (data.get("sandbox") or {}).get("landlock_abi")}
            evidence["runtime"] = data.get("runtime")
        elif event.get("type") == "tool/result" and data.get("call_id") == FOE_CALL_ID:
            value, spill = result_value(log, data)
            evidence["result"] = {
                "is_error": bool(data.get("is_error")),
                "exit_code": value.get("exit_code"),
                "timed_out": bool(value.get("timed_out")),
                "stderr": str(value.get("stderr") or ""),
                "permission_denial": value.get("permission_denial"),
                "failure": data.get("failure"),
                "spill": None if spill is None else str(spill),
            }
        elif event.get("type") == "episode/end":
            evidence["outcome"] = data.get("outcome")
    return evidence


def check_foe_sandbox(foe: Path, out: Path, run_episode: EpisodeRunner) -> Check:
    """Whether a required-mode episode denies a write outside its grant under Landlock."""
    name = "foe-kernel-sandbox"
    root = out / "foe"
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    outside = root / "outside"
    outside.mkdir(exist_ok=True)
    target = outside / "written.txt"
    # A file a previous run left behind would count as this run's write.
    target.unlink(missing_ok=True)
    config = root / "config.json"
    config.write_text(json.dumps(foe_document(workspace), indent=2), encoding="utf-8")
    try:
        status, episode = run_episode(foe, config, root / "log", foe_responder(target))
    except Exception as exc:  # noqa: BLE001 - the host runtime raises whatever the binary or the protocol raised.
        return Check(name, True, NOT_RUN, f"the scripted episode could not be served: {exc}", {"config": str(config)})
    log = episode / "episode.jsonl"
    if not log.is_file():
        return Check(name, True, NOT_RUN, f"the episode ended with status {status} and wrote no log at {log}", {"status": status})
    try:
        evidence = foe_log_evidence(log)
    except ValueError as exc:
        return Check(name, True, NOT_RUN, f"the episode ended with status {status} and its log could not be read: {exc}", {"log": str(log), "status": status})
    evidence["status"] = status
    evidence["target"] = str(target)
    evidence["target_exists"] = target.exists()
    if status != 0 or "result" not in evidence:
        return Check(name, True, NOT_RUN, f"the episode ended with status {status} and recorded no result for {FOE_CALL_ID}; log {log}", evidence)
    sandbox = evidence.get("sandbox") or {}
    abi = sandbox.get("landlock_abi")
    result = evidence["result"]
    failure_code = (result["failure"] or {}).get("code")
    denied = DENIAL.search(result["stderr"]) is not None or failure_code == CAPABILITY_DENIED
    if evidence["target_exists"]:
        return Check(name, True, FAILED, f"the bash call wrote {target}, which lies outside the write grant", evidence)
    if sandbox.get("mode") != "required":
        return Check(name, True, FAILED, f"episode/start records sandbox mode {sandbox.get('mode')!r} rather than 'required'; log {log}", evidence)
    if not isinstance(abi, int) or abi <= 0:
        return Check(name, True, FAILED, f"episode/start records landlock_abi {abi!r}, so the kernel provided no Landlock; log {log}", evidence)
    if not denied and (result["is_error"] or result["timed_out"]):
        reason = f"failure code {failure_code}" if failure_code else "timed out" if result["timed_out"] else "an error result without a failure code"
        return Check(name, True, NOT_RUN, f"the bash call did not run to completion ({reason}), so the sandbox was not measured; log {log}", evidence)
    if not denied:
        return Check(name, True, FAILED, f"the bash call ended with exit code {result['exit_code']} and no denial diagnostic; log {log}", evidence)
    return Check(name, True, PASSED, f"the write outside the grant was denied under Landlock ABI {abi}", evidence)


def codex_sandbox(codex: Path, workspace: Path, command: list[str], codex_home: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run `command` under the read-only Codex sandbox from `workspace`.

    Codex locates its credential, configuration, and session files by the
    CODEX_HOME environment variable and offers no flag for it, so a decoy
    home reaches the child process that way. The parent's environment is
    copied whole; this module consults no variable of it.
    """
    environment = None if codex_home is None else dict(os.environ, CODEX_HOME=str(codex_home))
    return subprocess.run(
        [str(codex), "sandbox", "-P", ":read-only", "-C", str(workspace), "--", *command],
        capture_output=True,
        text=True,
        timeout=CODEX_TIMEOUT_SECONDS,
        cwd=workspace,
        env=environment,
        check=False,
    )


def check_codex_sandbox(codex: Path, out: Path) -> Check:
    """Whether the read-only Codex sandbox refuses a write."""
    name = "codex-sandbox"
    root = out / "codex"
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = root / "written.txt"
    target.unlink(missing_ok=True)
    command = ["/bin/bash", "-c", write_command(target)]
    try:
        completed = codex_sandbox(codex, workspace, command)
    except subprocess.TimeoutExpired:
        return Check(name, True, NOT_RUN, f"codex sandbox did not finish within {CODEX_TIMEOUT_SECONDS} seconds", {"command": command})
    evidence = {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "target": str(target),
        "target_exists": target.exists(),
    }
    if target.exists():
        return Check(name, True, FAILED, f"the command wrote {target} under codex sandbox -P :read-only", evidence)
    if DENIAL.search(completed.stderr) is None and DENIAL.search(completed.stdout) is None:
        return Check(name, True, NOT_RUN, f"codex sandbox ended with exit code {completed.returncode} and no denial diagnostic", evidence)
    return Check(name, True, PASSED, "the read-only sandbox refused the write", evidence)


def check_codex_decoy_home(codex: Path, out: Path) -> Check:
    """Whether a sandboxed command runs under a decoy Codex home without seeing its sentence.

    The check is optional: `codex sandbox` runs no model and reads no
    instructions file, so a pass shows that the decoy home reaches the
    command and nothing about what a model request carries.
    """
    name = "codex-decoy-home"
    root = out / "decoy-home"
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    codex_home = root / "codex-home"
    codex_home.mkdir(exist_ok=True)
    sentence = f"The canary sentence of this probe is {uuid.uuid4().hex}."
    (codex_home / "AGENTS.md").write_text(f"# Instructions\n\n{sentence}\n", encoding="utf-8")
    command = ["/usr/bin/env"]
    try:
        completed = codex_sandbox(codex, workspace, command, codex_home)
    except subprocess.TimeoutExpired:
        return Check(name, False, NOT_RUN, f"codex sandbox did not finish within {CODEX_TIMEOUT_SECONDS} seconds", {"codex_home": str(codex_home)})
    environment = completed.stdout
    evidence = {
        "codex_home": str(codex_home),
        "sentence": sentence,
        "command": command,
        "exit_code": completed.returncode,
        "environment_names_codex_home": f"CODEX_HOME={codex_home}" in environment.splitlines(),
        "stderr": completed.stderr,
    }
    if sentence in environment:
        return Check(name, False, FAILED, "the decoy home's sentence appears in the sandboxed command's environment", evidence)
    if sentence in completed.stderr:
        return Check(name, False, FAILED, "the decoy home's sentence appears in the sandbox's own output", evidence)
    if not evidence["environment_names_codex_home"]:
        return Check(name, False, NOT_RUN, f"the command's environment listing does not name CODEX_HOME={codex_home}, so the command did not run under the decoy", evidence)
    return Check(
        name,
        False,
        PASSED,
        "the command ran under the decoy CODEX_HOME and neither its environment nor the sandbox's output carries the sentence; "
        "whether a model request carries it is the codex-isolation-canary-model-request row",
        evidence,
    )


def codex_version(codex: Path) -> tuple[str | None, str | None]:
    """The line `codex --version` prints, or the reason it printed none, as `(version, problem)`."""
    try:
        completed = subprocess.run([str(codex), "--version"], capture_output=True, text=True, timeout=CODEX_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return None, f"codex --version did not finish within {CODEX_TIMEOUT_SECONDS} seconds"
    if completed.returncode != 0:
        printed = (completed.stderr or completed.stdout).strip()
        return None, f"codex --version ended with exit code {completed.returncode}: {printed}"
    version = completed.stdout.strip()
    if not version:
        return None, "codex --version ended with exit code 0 and printed nothing"
    return version, None


def check_versions(codex: Path | None, foe_check: Check) -> Check:
    """The foe runtime from the sandbox episode's log and the codex binary's own report."""
    name = "versions"
    evidence: dict[str, Any] = {"foe": foe_check.evidence.get("runtime"), "foe_version_source": "episode/start.runtime; the binary takes no version flag"}
    if codex is None:
        evidence["codex"], problem = None, "no codex binary; pass --codex PATH"
    else:
        evidence["codex"], problem = codex_version(codex)
    if evidence["foe"] is None:
        return Check(name, True, NOT_RUN, f"no episode/start event was recorded, so the foe runtime is unknown ({foe_check.name} is {foe_check.status})", evidence)
    if problem is not None:
        return Check(name, True, NOT_RUN, problem, evidence)
    return Check(name, True, PASSED, f"foe {evidence['foe'].get('version')} build {evidence['foe'].get('build')}; {evidence['codex']}", evidence)


def check_route(route: str | None) -> Check:
    """Whether the shared model route answers `GET /v1/models`."""
    name = "route-models"
    if route is None:
        return Check(name, False, NOT_RUN, "no --route given")
    url = route.rstrip("/") + "/v1/models"
    evidence: dict[str, Any] = {"url": url, "timeout_seconds": ROUTE_TIMEOUT_SECONDS}
    try:
        with urllib.request.urlopen(url, timeout=ROUTE_TIMEOUT_SECONDS) as response:  # noqa: S310 - the route is the operator's own argument.
            evidence["http_status"] = response.status
    except urllib.error.HTTPError as exc:
        evidence["http_status"] = exc.code
        exc.close()
        return Check(name, True, FAILED, f"{url} answered HTTP {exc.code}", evidence)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        evidence["error"] = str(exc)
        return Check(name, True, FAILED, f"{url} was not reachable within {ROUTE_TIMEOUT_SECONDS} seconds: {exc}", evidence)
    return Check(name, True, PASSED, f"{url} answered HTTP {response.status}", evidence)


def run_checks(foe: Path, codex: Path | None, out: Path, route: str | None, live: bool, run_episode: EpisodeRunner = host_runtime.run) -> list[Check]:
    """Every check, in table order, each run or reported as not run."""
    if not live:
        skipped = "the sandbox checks run only under --live"
        foe_check = Check("foe-kernel-sandbox", True, NOT_RUN, skipped)
        codex_checks = [Check("codex-sandbox", True, NOT_RUN, skipped), Check("codex-decoy-home", False, NOT_RUN, skipped)]
    elif codex is None:
        missing = "no codex binary; pass --codex PATH"
        foe_check = check_foe_sandbox(foe, out, run_episode)
        codex_checks = [Check("codex-sandbox", True, NOT_RUN, missing), Check("codex-decoy-home", False, NOT_RUN, missing)]
    else:
        foe_check = check_foe_sandbox(foe, out, run_episode)
        codex_checks = [check_codex_sandbox(codex, out), check_codex_decoy_home(codex, out)]
    return [
        foe_check,
        *codex_checks,
        Check("codex-isolation-canary-model-request", False, NOT_RUN, "whether a model request carries the decoy sentence needs a model and is outside this probe"),
        check_versions(codex, foe_check),
        check_route(route),
        Check("cargo-test-wall-time", False, NOT_RUN, "the Rust fixture's test wall time is measured by the task runner, which holds the fixture"),
    ]


def exit_status(checks: list[Check]) -> int:
    if any(check.required and check.status == FAILED for check in checks):
        return 1
    if any(check.required and check.status == NOT_RUN for check in checks):
        return 2
    return 0


def cell(text: str) -> str:
    """`text` as one Markdown table cell: line breaks become spaces and pipes are escaped."""
    return " ".join(text.splitlines()).replace("|", "\\|")


def table(checks: list[Check]) -> str:
    rows = ["| check | required | status | detail |", "|---|---|---|---|"]
    for check in checks:
        rows.append(f"| `{check.name}` | {'yes' if check.required else 'no'} | {check.status} | {cell(check.detail)} |")
    return "\n".join(rows)


def report(checks: list[Check], host: dict[str, Any]) -> dict[str, Any]:
    return {"checks": [check.to_dict() for check in checks], "host": host, "exit_status": exit_status(checks)}


def clear_output(out: Path) -> str | None:
    """Remove a previous run's entries from `out`; the reason when the directory is refused.

    Only the entries in `OUTPUT_ENTRIES` are removed. A directory holding
    any other entry is refused unchanged, so a checkout or a home directory
    passed as `--out` loses nothing.
    """
    if not out.exists():
        return None
    if not out.is_dir():
        return f"{out} is not a directory"
    strangers = sorted(entry.name for entry in out.iterdir() if entry.name not in OUTPUT_ENTRIES)
    if strangers:
        return f"{out} holds entries this probe did not write ({', '.join(strangers)}); pass an empty directory or one holding only a previous probe run"
    for entry_name in OUTPUT_ENTRIES:
        entry = out / entry_name
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        elif entry.is_symlink() or entry.exists():
            entry.unlink()
    return None


def main(argv: list[str] | None = None, run_episode: EpisodeRunner = host_runtime.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--foe", required=True, type=Path, help="the foe binary")
    parser.add_argument("--codex", type=Path, default=None, help="the codex binary; the one on PATH when omitted")
    parser.add_argument("--out", type=Path, default=None, help="where the fixtures, logs, probe.json, and probe.md are written; a previous run's entries are removed and any other content is refused")
    parser.add_argument("--route", default=None, help="the base URL of the model route both harnesses share; GET <URL>/v1/models is checked")
    parser.add_argument("--live", action="store_true", help="run the sandbox checks, which need the built binaries and this host's kernel")
    args = parser.parse_args(argv)

    foe = args.foe.resolve()
    if not os.access(foe, os.X_OK):
        print(f"environment probe: {foe} is not executable", file=sys.stderr)
        return 2
    found = str(args.codex) if args.codex else shutil.which("codex")
    codex = Path(found).resolve() if found and os.access(found, os.X_OK) else None
    if args.codex and codex is None:
        print(f"environment probe: {args.codex} is not executable", file=sys.stderr)
        return 2
    # Codex refuses to place its helper binaries under /tmp, so the default
    # output directory lives under the user's state directory.
    default_out = Path.home() / ".local" / "state" / "foe" / "cross-harness" / "probe"
    out = (args.out or default_out).resolve()
    refusal = clear_output(out)
    if refusal is not None:
        print(f"environment probe: {refusal}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    checks = run_checks(foe, codex, out, args.route, args.live, run_episode)
    host = {"foe": str(foe), "codex": None if codex is None else str(codex), "route": args.route, "out": str(out), "live": args.live}
    document = report(checks, host)
    (out / "probe.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    rendered = table(checks)
    (out / "probe.md").write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps(document))
    print()
    print(rendered)
    return document["exit_status"]


if __name__ == "__main__":
    sys.exit(main())
