#!/usr/bin/python3
"""Run one task under Codex CLI and report the outcome it wrote.

This module is the Codex arm of the cross-harness evaluation. It starts
`codex exec` headless in the workspace, under one sandbox mode, one model,
and one reasoning effort, with a JSON output schema that makes the final
message a typed report, and it returns the same `ArmResult` the foe arm
returns.

Codex reports its outcome in the last agent message, which `-o` writes to
`artifacts/last.txt`. The default schema asks for a `status` of `completed`
or `blocked`, a `code` from the three blocked codes a foe model reports
itself, and `evidence` as a list of sentences. A caller may pass another
schema; the arm still reads `status`, `code`, and `evidence` from the
message, and the whole message is the candidate. `schema_for` builds that
other schema for a task whose grade reads a value of its own: the shape the
task states is merged into the default one, so the single final message
carries the reported outcome and the value the grader reads together.

Codex enforces no budget of its own, so the run is placed under the budget
watcher in `codex_budget_watcher.py`, which sums the usage the session files
report and terminates the process group when a limit is crossed. A run the
watcher stopped is reported `exhausted` with the crossed dimension as its
code, which is the fact a foe episode states in the same case. A run that
ended by a signal without the watcher's decision is `killed`, and a run
that ended without a readable last message is `failed`.

Codex locates its credential and session files by `CODEX_HOME` and offers
no flag for it, so the arm creates a fresh directory under the artifacts,
copies the caller's credential file into it as `auth.json`, and passes that
directory as the child's `CODEX_HOME`.

Codex rewrites that copy whenever it refreshes the credential, and a
provider that rotates the refresh token on use leaves the source credential
unusable once one attempt has refreshed it. So once the process has exited
the arm compares the copy with the source and, when the copy differs,
writes the copy back to the source: a temporary file in the source's
directory, flushed to disk, then renamed over the source with mode 0600.
The source path is resolved first, so a source that is a symbolic link
keeps the link and the file it names receives the content. Three cases
refuse the write-back: a copy that does not parse as JSON, a copy that
lacks a key the source holds, and a rewritten copy whose source changed
while the run held it. A copy the run never rewrote is written back in no
case, including against a source another process wrote. A write-back that
fails is recorded and the attempt continues. `write_back` states each case.

The copy is removed once the write-back has settled, before anything reads
the run's output, unless the caller sets `keep_credential`, and the copy is
removed on every path out of the run, an interrupt among them. The record
states what the write-back did and whether a copy remains, which it reads
from the file rather than from the option the caller passed. Nothing in
this module reads, prints, or logs the credential's contents, and the
record carries no part of it.

The child inherits the arm's own environment otherwise, so that the binary
finds its shared libraries and certificates; this module reads no
environment value itself. The record names the directory, every command
line, the event stream, and every session file the run wrote.

A caller may give a config canary, one sentence the arm writes into the
fresh `CODEX_HOME` as `config.toml` under the `developer_instructions` key,
the user configuration file that `--ignore-user-config` states it does not
load. Codex loads `AGENTS.md` from `CODEX_HOME` as global instructions in
every session, and `--ignore-rules` covers execution-policy `.rules` files
alone, so neither is a place Codex must not load. The sentence reaches no
request unless Codex read the file, so the isolation gate of the evaluation
searches the recorded requests for it; the record names the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CROSS_HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CROSS_HARNESS))
sys.path.insert(0, str(CROSS_HARNESS / "arms"))

import codex_budget_watcher  # noqa: E402
from foe_arm import BLOCKED, COMPLETED, EXHAUSTED, FAILED, KILLED, ArmResult, now_ms, reported, stderr_tail  # noqa: E402

HARNESS = "codex"

SANDBOX_MODES: tuple[str, ...] = ("read-only", "workspace-write", "danger-full-access")

# The blocked codes a model reports itself under foe, from
# docs/log-format.md "Blocked codes"; the runtime-detected codes have no
# counterpart a Codex run could report.
BLOCKED_CODES: tuple[str, ...] = ("goal-unreachable", "ambiguous-task", "missing-capability")

# The schema the final message satisfies. Every property is required and
# `code` admits null, which is the form a strict structured output accepts.
DEFAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": [COMPLETED, BLOCKED]},
        "code": {"type": ["string", "null"], "enum": [*BLOCKED_CODES, None]},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "code", "evidence"],
    "additionalProperties": False,
}



def schema_for(returns: Mapping[str, Any] | None) -> dict[str, Any]:
    """The final-message schema for a task that states the shape of the value its grade reads.

    A Codex run has one final message, so the outcome the arm reads and the
    value the grader reads travel in the same object: the returned shape's
    properties are merged into the default schema and required beside
    `status`, `code`, and `evidence`. A shape that renames one of those three
    is refused rather than silently overriding the outcome the arm reads.
    `returns` of None is the default schema.
    """
    if returns is None:
        return DEFAULT_SCHEMA
    properties = returns.get("properties")
    if returns.get("type") != "object" or not isinstance(properties, dict) or not properties:
        raise ValueError(f"the returned shape is {json.dumps(returns)[:200]}; expected an object schema with a non-empty properties")
    taken = sorted(set(properties) & set(DEFAULT_SCHEMA["properties"]))
    if taken:
        raise ValueError(f"the returned shape names {', '.join(taken)}, which the final message already carries as the reported outcome")
    required = [key for key in returns.get("required", sorted(properties)) if key in properties]
    return {
        "type": "object",
        "properties": {**DEFAULT_SCHEMA["properties"], **properties},
        "required": [*DEFAULT_SCHEMA["required"], *required],
        "additionalProperties": False,
    }


CODEX_HOME_NAME, CREDENTIAL_NAME = "codex-home", "auth.json"
# The user configuration file under CODEX_HOME that `--ignore-user-config`
# keeps Codex from loading, and the key of it that would reach a request as
# a developer message if the file were loaded.
CONFIG_CANARY_NAME, CONFIG_CANARY_KEY = "config.toml", "developer_instructions"
SCHEMA_NAME, LAST_NAME, EVENTS_NAME, STDERR_NAME = "schema.json", "last.txt", "events.jsonl", "stderr.txt"

# What the write-back of the credential copy did, as the record states it:
# the copy holds what the run was given and nothing was written; the copy
# was written back to the source; the copy was refused as unusable; the
# copy was gone when the run ended; or the write-back raised.
WRITE_BACK_STATES: tuple[str, ...] = ("unchanged", "written", "refused", "absent", "failed")
WRITE_BACK_UNCHANGED, WRITE_BACK_WRITTEN, WRITE_BACK_REFUSED, WRITE_BACK_ABSENT, WRITE_BACK_FAILED = WRITE_BACK_STATES


@dataclass(frozen=True)
class CodexSpec:
    """Everything one Codex run needs.

    `limits` is what the budget watcher enforces, keyed by its DIMENSIONS.
    `credential_source` is the `auth.json` a Codex login wrote, copied into
    the fresh `CODEX_HOME`. `max_threads` bounds concurrent child agents when
    `agents_enabled` is true and is otherwise unused. `model_providers` maps
    a provider name to its Codex configuration keys, for a run against a
    compatible server; the one name given becomes the run's provider.
    `environment` is the base environment of the child; the arm's own
    environment when None. `keep_credential` leaves the credential copy in
    `CODEX_HOME` after the run; the copy is removed otherwise.
    `config_canary` is the sentence written into `CODEX_HOME` as
    CONFIG_CANARY_NAME before the run; nothing is written when None.
    """

    arm_name: str
    codex: Path
    task: str
    workspace: Path
    artifacts: Path
    sandbox: str
    model: str
    reasoning_effort: str
    credential_source: Path
    limits: Mapping[str, int | float]
    agents_enabled: bool = False
    max_threads: int | None = None
    output_schema: dict[str, Any] | None = None
    model_providers: Mapping[str, Mapping[str, Any]] | None = None
    environment: Mapping[str, str] | None = None
    keep_credential: bool = False
    config_canary: str | None = None

    def __post_init__(self) -> None:
        if self.sandbox not in SANDBOX_MODES:
            raise ValueError(f"spec sandbox is {self.sandbox!r}; expected one of {', '.join(SANDBOX_MODES)}")
        if not self.model:
            raise ValueError("spec model is empty")
        if not self.reasoning_effort:
            raise ValueError("spec reasoning_effort is empty")
        if not self.task.strip():
            raise ValueError("spec task is empty")
        if self.max_threads is not None and (isinstance(self.max_threads, bool) or not isinstance(self.max_threads, int) or self.max_threads <= 0):
            raise ValueError(f"spec max_threads is {self.max_threads!r}; expected a positive integer or None")
        if self.model_providers is not None and len(self.model_providers) != 1:
            raise ValueError(f"spec model_providers names {len(self.model_providers)} providers; a run uses exactly one")
        if self.config_canary is not None and not self.config_canary.strip():
            raise ValueError("spec config_canary is empty; the canary is one sentence, or None to plant none")
        codex_budget_watcher.check_limits(self.limits)

    @property
    def schema(self) -> dict[str, Any]:
        return DEFAULT_SCHEMA if self.output_schema is None else self.output_schema


def toml_value(value: Any, key: str) -> str:
    """The TOML literal a `-c key=value` override carries for a Python value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # A JSON string literal with non-ASCII characters kept literal is a
        # TOML basic string: both escape the quotation mark, the backslash,
        # and the control characters below U+0020 the same way, and TOML
        # rejects the surrogate pairs that ASCII-only JSON would write for a
        # character outside the Basic Multilingual Plane. U+007F is the one
        # character TOML requires escaped and JSON leaves literal.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")
    raise ValueError(f"override {key} is {type(value).__name__}; expected a string, number, or boolean")


def command_line(spec: CodexSpec, schema: Path, last: Path) -> list[str]:
    """The `codex exec` command the run executes; see the module docstring."""
    command = [
        str(spec.codex),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--ignore-user-config",
        "-s",
        spec.sandbox,
        "-m",
        spec.model,
        "-c",
        f"model_reasoning_effort={toml_value(spec.reasoning_effort, 'model_reasoning_effort')}",
        "-c",
        'approval_policy="never"',
        "-C",
        str(spec.workspace),
        "-o",
        str(last),
        "--output-schema",
        str(schema),
    ]
    if not spec.agents_enabled:
        command += ["-c", "agents.enabled=false"]
    elif spec.max_threads is not None:
        command += ["-c", f"agents.max_concurrent_threads_per_session={spec.max_threads}"]
    if spec.model_providers:
        ((name, options),) = spec.model_providers.items()
        for key, value in options.items():
            command += ["-c", f"model_providers.{name}.{key}={toml_value(value, f'model_providers.{name}.{key}')}"]
        command += ["-c", f"model_provider={toml_value(name, 'model_provider')}"]
    # The separator keeps a task text that starts with a dash from being
    # read as an option.
    command += ["--", spec.task]
    return command


def parse_events(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """The JSON Lines events `--json` wrote, and the lines that were not events."""
    events: list[dict[str, Any]] = []
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            problems.append(f"{EVENTS_NAME}:{number}: {exc}")
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
        else:
            problems.append(f"{EVENTS_NAME}:{number}: not an event object")
    return events, problems


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The thread, the turn count, the summed usage, every error the event stream reports, and the problems reading it.

    `errors` are what the stream itself reported; `problems` are events of a
    documented type whose content the arm could not read, such as a usage
    field that is not an integer. A problem leaves the summary partial and
    is recorded, because the run has finished by the time it is found.
    """
    usage = codex_budget_watcher.empty_usage()
    thread_id = None
    turns = 0
    errors: list[str] = []
    problems: list[str] = []
    items: dict[str, int] = {}
    for event in events:
        kind = event["type"]
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        elif kind == "turn.completed":
            turns += 1
            if isinstance(event.get("usage"), dict):
                try:
                    codex_budget_watcher.add_usage(usage, codex_budget_watcher.parse_usage(event["usage"], f"{EVENTS_NAME} turn.completed"))
                except ValueError as exc:
                    problems.append(str(exc))
        elif kind == "turn.failed":
            turns += 1
            error = event.get("error")
            errors.append(str(error.get("message") if isinstance(error, dict) else error))
        elif kind == "error":
            # A transport failure the stream reports outside any item, such
            # as a reconnection attempt.
            errors.append(str(event.get("message") or event))
        elif kind == "item.completed" and isinstance(event.get("item"), dict):
            item_type = str(event["item"].get("type"))
            items[item_type] = items.get(item_type, 0) + 1
            if item_type == "error":
                errors.append(str(event["item"].get("message") or event["item"]))
    return {"thread_id": thread_id, "turns": turns, "usage": usage, "errors": errors, "items": items, "problems": problems}


def read_last_message(last: Path, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, Any, list[str]]:
    """The reported outcome and candidate the last message holds, and the problems found reading it.

    The reported outcome is None when the file is absent or is not a JSON
    object. A status outside the schema's enumeration is reported as
    `failed`, whatever word the message used, because the statuses the
    schema omits are decided by the watcher or the arm. A code outside a
    blocked status, or outside the schema's enumeration of codes, is
    dropped. Each such case is recorded as a problem, and the message
    itself is the candidate, so that a grader sees what the model said.
    """
    if not last.is_file():
        return None, None, [f"{last} is absent; the run wrote no last message"]
    text = last.read_text(encoding="utf-8")
    try:
        message = json.loads(text)
    except ValueError as exc:
        return None, text, [f"{last}: the last message is not JSON: {exc}"]
    if not isinstance(message, dict):
        return None, message, [f"{last}: the last message is a {type(message).__name__} rather than an object"]
    problems: list[str] = []
    status = message.get("status")
    allowed = schema.get("properties", {}).get("status", {}).get("enum") or [COMPLETED, BLOCKED]
    if status not in allowed:
        problems.append(f"{last}: status is {status!r}; the schema allows {', '.join(map(str, allowed))}")
        status = FAILED
    code = message.get("code")
    codes = schema.get("properties", {}).get("code", {}).get("enum")
    if code is not None and status != BLOCKED:
        problems.append(f"{last}: code {code!r} is set while the status is {status!r} rather than {BLOCKED!r}")
        code = None
    elif code is not None and codes is not None and code not in codes:
        problems.append(f"{last}: code {code!r} is outside the codes the schema allows: {', '.join(map(str, codes))}")
        code = None
    if code is not None:
        code = str(code)
    raw = message.get("evidence")
    if isinstance(raw, list):
        evidence = [str(item) for item in raw]
    elif raw is None:
        evidence = []
    else:
        problems.append(f"{last}: evidence is a {type(raw).__name__} rather than a list")
        evidence = [str(raw)]
    return reported(str(status), code, evidence), message, problems


def interpret(
    stop: codex_budget_watcher.Stop | None,
    exit_status: int | None,
    last: Path,
    schema: dict[str, Any],
    summary: dict[str, Any],
    stderr: str,
) -> tuple[dict[str, Any], Any, list[str]]:
    """The reported outcome, the candidate, and the problems, for how the run ended.

    The watcher's stop takes precedence over anything the run wrote, because
    a message written after the budget was crossed is not an outcome the
    budget allowed. Then a signal is `killed`, a readable last message is
    what it says, and anything else is `failed` with the stream's errors
    and the end of standard error as evidence.
    """
    if stop is not None:
        return reported(EXHAUSTED, stop.dimension, [stop.reason]), None, []
    if exit_status is not None and exit_status < 0:
        return reported(KILLED, None, [f"the run ended by signal {-exit_status}"]), None, []
    outcome, candidate, problems = read_last_message(last, schema)
    if outcome is not None:
        return outcome, candidate, problems
    evidence = list(problems)
    evidence.append(f"exit status {exit_status}")
    evidence.extend(summary["errors"])
    tail = stderr_tail(stderr)
    if tail:
        evidence.append(tail)
    return reported(FAILED, None, evidence), candidate, problems


@dataclass(frozen=True)
class CredentialCopy:
    """The credential copy one run holds, and what the source held when the copy was made.

    `source` is the credential file the copy came from, with symbolic links
    resolved, so that a write-back reaches the file a link names rather than
    replacing the link. `path` is the copy under `CODEX_HOME`.
    `source_digest` is the SHA-256 of the source's bytes at the moment of the
    copy, which shows whether another process wrote the source while the run
    held the copy. A digest identifies the content and carries none of it.
    """

    source: Path
    path: Path
    source_digest: str


def content_digest(data: bytes) -> str:
    """The SHA-256 of a file's bytes."""
    return hashlib.sha256(data).hexdigest()


def key_paths(value: Mapping[str, Any], prefix: str = "") -> set[str]:
    """Every key of a JSON object as a dotted path, descending into the objects it holds."""
    paths: set[str] = set()
    for key, inner in value.items():
        path = f"{prefix}{key}"
        paths.add(path)
        if isinstance(inner, dict):
            paths |= key_paths(inner, f"{path}.")
    return paths


def prepare_home(spec: CodexSpec) -> tuple[Path, CredentialCopy]:
    """A fresh `CODEX_HOME` under the artifacts holding the credential copy, the copy itself, and the config canary when the spec gives one.

    The copy is created with mode 0600 and never widens, so no other user of
    the host reads it while the run holds it.
    """
    source = Path(spec.credential_source)
    if not source.is_file():
        raise FileNotFoundError(f"credential source {source} is not a file")
    home = spec.artifacts / CODEX_HOME_NAME
    if home.exists():
        raise FileExistsError(f"{home} exists; a run needs a fresh CODEX_HOME, so give each run its own artifacts directory")
    home.mkdir(parents=True)
    # The source is resolved once here, and the write-back after the run
    # uses the resolved path, so a source that is a symbolic link is read
    # and written through to the file it names.
    resolved = source.resolve()
    data = resolved.read_bytes()
    copy = home / CREDENTIAL_NAME
    descriptor = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    copy.chmod(0o600)
    if spec.config_canary is not None:
        (home / CONFIG_CANARY_NAME).write_text(config_canary_text(spec.config_canary), encoding="utf-8")
    return home, CredentialCopy(resolved, copy, content_digest(data))


def write_back_record(state: str, reason: str, source: Path) -> dict[str, Any]:
    """What the record states about one write-back: the state, whether the source was written, the reason, and the source path."""
    if state not in WRITE_BACK_STATES:
        raise ValueError(f"write-back state is {state!r}; expected one of {', '.join(WRITE_BACK_STATES)}")
    return {"state": state, "written": state == WRITE_BACK_WRITTEN, "reason": reason, "source": str(source)}


def write_back(credential: CredentialCopy) -> dict[str, Any]:
    """Write a copy that Codex rewrote back to the source credential, and state what was done.

    Codex refreshes the credential inside `CODEX_HOME` and writes the new
    tokens to the copy. A provider that rotates the refresh token on use
    leaves the source credential unusable once one attempt has refreshed
    it, so a rewritten copy returns to the source before the copy is
    removed.

    The write is atomic: a temporary file in the source's directory,
    flushed to disk, then renamed over the source with mode 0600, so a
    concurrent reader sees the whole old file or the whole new one. Three
    cases refuse the write and name what was found. A copy that does not
    parse as JSON and a copy that lacks a key the source holds are what a
    process killed during a write leaves behind. A source whose bytes
    changed while the run held the copy belongs to another process holding
    the same credential, whose content a rewritten copy would discard; a
    copy the run never rewrote holds nothing that source needs and is
    recorded as unchanged.

    The returned mapping carries no part of the credential.
    """
    if not credential.path.is_file():
        return write_back_record(WRITE_BACK_ABSENT, f"the credential copy {credential.path} is absent after the run, so nothing is written back", credential.source)
    copy_bytes = credential.path.read_bytes()
    source_bytes = credential.source.read_bytes()
    if copy_bytes == source_bytes:
        return write_back_record(WRITE_BACK_UNCHANGED, f"the credential copy {credential.path} holds what {credential.source} holds, so nothing is written back", credential.source)
    if content_digest(source_bytes) != credential.source_digest:
        if content_digest(copy_bytes) == credential.source_digest:
            # The copy still holds what it was given, so the run refreshed
            # nothing and the source another process wrote stands.
            return write_back_record(
                WRITE_BACK_UNCHANGED,
                f"the credential copy {credential.path} holds what {credential.source} held when the copy was made, and another process wrote {credential.source} while the run held the copy, so nothing is written back",
                credential.source,
            )
        return write_back_record(
            WRITE_BACK_REFUSED,
            f"the credential source {credential.source} changed while the run held the copy {credential.path}, so the rewritten copy is not written back; another process holds the same credential",
            credential.source,
        )
    values: dict[str, Any] = {}
    for name, path, data in (("source", credential.source, source_bytes), ("copy", credential.path, copy_bytes)):
        try:
            values[name] = json.loads(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            # The message of a decoding error names the byte it stopped on,
            # which is one byte of the credential, so the reason states the
            # offset alone.
            return write_back_record(
                WRITE_BACK_REFUSED,
                f"the credential {name} {path} is not valid UTF-8 at byte offset {exc.start}, so the copy is not written back",
                credential.source,
            )
        except ValueError as exc:
            # The message of a JSON error states a position and names no
            # content, so the reason carries no part of the file.
            return write_back_record(WRITE_BACK_REFUSED, f"the credential {name} {path} does not parse as JSON ({exc}), so the copy is not written back", credential.source)
        if not isinstance(values[name], dict):
            return write_back_record(
                WRITE_BACK_REFUSED,
                f"the credential {name} {path} is a {type(values[name]).__name__} rather than a JSON object, so the copy is not written back",
                credential.source,
            )
    missing = sorted(key_paths(values["source"]) - key_paths(values["copy"]))
    if missing:
        return write_back_record(
            WRITE_BACK_REFUSED,
            f"the credential copy {credential.path} lacks the keys {', '.join(missing)} that {credential.source} holds, so the copy is not written back",
            credential.source,
        )
    descriptor, temporary_name = tempfile.mkstemp(dir=credential.source.parent, prefix=f".{credential.source.name}.", suffix=".write-back")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(copy_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, credential.source)
    except BaseException:
        # The temporary holds the refreshed credential at mode 0600, so it
        # is removed before any failure continues, an interrupt included.
        temporary.unlink(missing_ok=True)
        raise
    # The rename reaches the disk with the directory, so a host that loses
    # power after the run still holds the refreshed credential. A directory
    # that refuses the flush leaves the rename in place.
    try:
        directory = os.open(credential.source.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass
    return write_back_record(WRITE_BACK_WRITTEN, f"the credential copy {credential.path} differs from {credential.source} and was written back to it", credential.source)


def settle_credential(credential: CredentialCopy, keep_credential: bool) -> dict[str, Any]:
    """Write a rewritten credential copy back to the source, remove the copy unless the caller keeps it, and state what the write-back did.

    Every failure of the write-back is recorded and the attempt continues:
    an attempt whose refreshed credential stayed behind costs one attempt,
    and a run whose credential source is dead costs every attempt after the
    first, so the reader sees the failure in the record rather than in a
    raised error. A removal that fails is recorded the same way, under
    `copy_removal_failure`, so that a source the write-back just refreshed
    is still reported.

    The removal runs on every path out of the write-back, an interrupt
    included, so no path leaves the copy under the artifacts.
    """
    removal_failure: str | None = None
    try:
        try:
            state = write_back(credential)
        except Exception as exc:  # noqa: BLE001
            state = write_back_record(WRITE_BACK_FAILED, f"the credential copy {credential.path} could not be written back to {credential.source}: {exc}", credential.source)
    finally:
        if not keep_credential:
            try:
                remove_credential(credential.path.parent)
            except OSError as exc:
                removal_failure = str(exc)
    return {**state, "copy_removal_failure": removal_failure}


def config_canary_text(sentence: str) -> str:
    """The `config.toml` that carries the canary sentence as developer instructions."""
    return (
        "# Planted by the cross-harness evaluation. The run passes --ignore-user-config, so this file must never be loaded;\n"
        "# a model request that carries the sentence below shows that it was.\n"
        f"{CONFIG_CANARY_KEY} = {toml_value(sentence.strip(), CONFIG_CANARY_KEY)}\n"
    )


def remove_credential(home: Path) -> Path:
    """Remove the credential copy from `CODEX_HOME` and return its path.

    A copy Codex rewrote during the run, as a token refresh does, reaches
    this function after `write_back` has returned it to the source and is
    removed like any other; a copy already absent leaves nothing to remove.
    A copy that cannot be removed is an error naming the path, because a
    credential left under the artifacts stays readable by whoever reads
    them; `settle_credential` records that error in the attempt rather than
    losing the attempt to it.
    """
    path = home / CREDENTIAL_NAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OSError(f"the credential copy {path} could not be removed: {exc}") from exc
    return path


def session_files(home: Path) -> list[str]:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return []
    return [str(path) for path in sorted(sessions.rglob("*.jsonl")) if path.is_file()]


def run(spec: CodexSpec) -> ArmResult:
    """Run the task under Codex CLI and return what it reported; see the module docstring."""
    codex = Path(spec.codex)
    if not os.access(codex, os.X_OK):
        raise FileNotFoundError(f"codex binary {codex} is not an executable file")
    if not Path(spec.workspace).is_dir():
        raise FileNotFoundError(f"workspace {spec.workspace} is not a directory")
    spec.artifacts.mkdir(parents=True, exist_ok=True)
    home, credential = prepare_home(spec)
    # The credential copy is on disk from here on, so everything that
    # follows runs under the write-back and the removal.
    try:
        schema = spec.artifacts / SCHEMA_NAME
        schema.write_text(json.dumps(spec.schema, indent=2) + "\n", encoding="utf-8")
        last = spec.artifacts / LAST_NAME
        # A last message from an earlier run in the same artifacts directory
        # would otherwise be read as this run's outcome when this run writes none.
        last.unlink(missing_ok=True)
        command = command_line(spec, schema, last)
        # The child receives the arm's environment unchanged except for
        # CODEX_HOME; the arm reads none of its values.
        environment = dict(os.environ if spec.environment is None else spec.environment)
        environment["CODEX_HOME"] = str(home)

        started_ms = now_ms()
        exit_status, stop, out, err = codex_budget_watcher.run_with_watcher(command, home, spec.limits, cwd=spec.workspace, env=environment)
    finally:
        # The process has exited, the watcher failed and terminated it, or
        # the run never started; either way a credential Codex refreshed
        # returns to the source and the copy is removed before anything else.
        settled = settle_credential(credential, spec.keep_credential)
    ended_ms = now_ms()

    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    (spec.artifacts / EVENTS_NAME).write_text(stdout, encoding="utf-8")
    (spec.artifacts / STDERR_NAME).write_text(stderr, encoding="utf-8")
    events, event_problems = parse_events(stdout)
    summary = summarize_events(events)
    result, candidate, problems = interpret(stop, exit_status, last, spec.schema, summary, stderr)
    record = {
        "harness": HARNESS,
        "commands": [command],
        "cwd": str(spec.workspace),
        "codex_home": str(home),
        "credential_source": str(spec.credential_source),
        "credential_copy": str(credential.path),
        "credential_removed": not credential.path.exists(),
        "credential_written_back": settled["written"],
        "credential_write_back": settled,
        "config_canary_file": None if spec.config_canary is None else str(home / CONFIG_CANARY_NAME),
        "schema": str(schema),
        "last_message": str(last),
        "events": str(spec.artifacts / EVENTS_NAME),
        "stderr": str(spec.artifacts / STDERR_NAME),
        "session_files": session_files(home),
        "sandbox": spec.sandbox,
        "model": spec.model,
        "reasoning_effort": spec.reasoning_effort,
        "agents_enabled": spec.agents_enabled,
        "max_threads": spec.max_threads if spec.agents_enabled else None,
        "limits": dict(spec.limits),
        "stop": None if stop is None else stop.to_dict(),
        "thread_id": summary["thread_id"],
        "turns": summary["turns"],
        "usage": summary["usage"],
        "items": summary["items"],
        "errors": summary["errors"],
        "problems": event_problems + summary["problems"] + problems,
        "exit_status": exit_status,
    }
    return ArmResult(spec.arm_name, HARNESS, started_ms, ended_ms, exit_status, result, candidate, spec.artifacts, record)
