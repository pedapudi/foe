#!/usr/bin/python3
"""The harness isolation gate: no recorded request carries either canary of a run.

The evaluation plan requires that a canary instruction planted where Codex
must not load it, and one planted in foe's configuration directory, are
absent from every recorded request. The runner plants both: the Codex arm
writes the Codex canary into each attempt's fresh `CODEX_HOME` as
`config.toml` under the `developer_instructions` key, the user
configuration file that `--ignore-user-config` states it does not load,
and the runner writes the foe canary into foe's configuration directory
as `AGENTS.md`, a file foe never reads by design, and removes it once the
attempts have ended. The run file records both sentences.

This module reads a run document, finds the run files and the records under
the document's `out`, and searches every attempt's recorded requests for
both sentences: the `request/header` and `model/request` events of every
episode log a foe attempt wrote, its child episodes included, and every
session file and the event stream a Codex attempt wrote. A record that ran
no harness, because the attempt was not applicable or could not launch,
has nothing to search and is reported as such.

An absence proves isolation only when the requests were recorded and the
canaries were planted, so the gate also qualifies the evidence of every
attempt that launched a harness. Each of these is a qualification failure,
named by the attempt and the rule:

- a foe attempt whose episode directory holds no `episode.jsonl`, whose
  logs hold no request event, or whose record carries no integer
  `totals.model_calls`;
- a foe attempt whose logs hold fewer `request/header` and
  `model/request` events together than that `totals.model_calls`;
- a foe request event whose `data` is not a nonempty JSON object, or a
  line of a foe `episode.jsonl` that is not a JSON object;
- a foe attempt whose record does not state `foe_canary_present` as true,
  which the runner records just before the attempt starts, so a record
  written before that key existed fails as well;
- a Codex attempt whose record lists no session file, a listed session
  file or the event stream that is missing or unreadable, a line of either
  that is not a JSON object, or session files that hold no record of a
  type in CODEX_REQUEST_RECORDS;
- a Codex request record without an object `payload`;
- a Codex attempt whose record carries an integer `totals.model_calls`
  larger than the number of its `response_item` records, since every
  model call returns at least one item;
- a Codex attempt whose `config.toml` canary file is absent or does not
  hold the Codex canary sentence;
- a run file without evidence that the foe canary was planted.

The evidence of the foe canary's planting is the run-file key
`canaries.foe_config.planted`, an object holding `path`, the file the
runner wrote, and `sha256`, the hexadecimal digest of that file's content
as the runner read it back after writing. The runner writes the recorded
sentence followed by one newline, so the gate recomputes the digest of
that text, encoded as UTF-8, and requires `path` to equal
`canaries.foe_config.path` and the digests to be equal.

    isolation.py DOCUMENT [--out DIRECTORY] [--state-root DIRECTORY]

`--out` replaces the document's `out`, so that a run directory whose
document is not at hand can be examined under any loadable document.
`--state-root` names a copy of the runner's state root, such as the
extracted evidence archive `results/evidence-manifest.json` names. Every
path a record names under a state root, in any home directory, is then
read under the copy, and the run directory defaults to the document's
`out` moved under it. The gate prints one line per attempt naming whether
each canary is absent and every qualification failure, and writes
`gates/isolation.json` under the run directory. That file names the
document relative to the repository when the repository holds it, and the
run directory in the `~` form of the runner's state root when it lies
under the state root, so the file is the same on every host that holds
the same evidence. It exits 0 when every canary is absent from every recorded
request and no qualification failure was found, 1 when any request carries
a canary, 2 when the run has no records or no record launched a harness,
3 when a run file or a record cannot be read, and 4 when no request
carries a canary but a qualification failure leaves the absence unproven.
It calls no model and spends nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
CROSS_HARNESS = HERE.parent
sys.path.insert(0, str(CROSS_HARNESS))

import rescore  # noqa: E402
import run  # noqa: E402

ISOLATED, LEAKED, NO_RECORDS, MALFORMED, UNQUALIFIED = 0, 1, 2, 3, 4
RESULT_FILE = Path("gates") / "isolation.json"
# The foe log events that carry what the model received: the system prompt
# and tool schemas in effect, and the messages of each call.
REQUEST_EVENTS: tuple[str, ...] = ("request/header", "model/request")
LOG_NAME = "episode.jsonl"
# The Codex session record types that carry what the model received: the
# turn's context, which `normalize_codex.identity` reads, and the items of
# the conversation the model is sent.
CODEX_REQUEST_RECORDS: tuple[str, ...] = ("turn_context", "response_item")
PLANTED_KEY = "planted"
# The record key the runner sets before a foe attempt: whether the planted
# foe canary file still held its sentence when the attempt started.
PRESENT_KEY = "foe_canary_present"
REPOSITORY = CROSS_HARNESS.parent.parent
# Where a recorded path is read: as it stands, or under a copy of the state root.
Locate = Callable[[str], Path]


def run_files(out: Path) -> list[Path]:
    """Every run file under `out`: `run.json` and the numbered files later runs into the same directory wrote."""
    first = out / run.RUN_FILE
    stem, suffix = first.stem, first.suffix
    return sorted(path for path in out.glob(f"{stem}*{suffix}") if path.name == first.name or path.name[len(stem) :].startswith("-"))


def read_canaries(files: list[Path]) -> dict[str, str]:
    """The canary sentences the run files record, keyed by canary name; two files recording different sentences for one name are both kept under numbered keys."""
    found: dict[str, str] = {}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        recorded = data.get("canaries")
        if not isinstance(recorded, dict):
            raise ValueError(f"{path}: key canaries is absent; the run was written by a runner that planted no canary")
        for name in run.CANARY_NAMES:
            item = recorded.get(name)
            if not isinstance(item, dict) or not isinstance(item.get("sentence"), str) or not item["sentence"]:
                raise ValueError(f"{path}: key canaries.{name}.sentence is absent or empty")
            sentence = item["sentence"]
            if found.get(name) in (None, sentence):
                found[name] = sentence
            else:
                found[f"{name}:{path.stem}"] = sentence
    return found


def planted_text(sentence: str) -> str:
    """The content `run.plant_foe_canary` writes for a sentence, which the recorded digest covers."""
    return sentence + "\n"


def foe_planting_failure(path: Path) -> str | None:
    """The rule a run file breaks when it lacks evidence that the foe canary was planted, or None when the evidence holds."""
    item = json.loads(path.read_text(encoding="utf-8"))["canaries"][run.FOE_CONFIG_CANARY]
    key = f"canaries.{run.FOE_CONFIG_CANARY}.{PLANTED_KEY}"
    planted = item.get(PLANTED_KEY)
    if not isinstance(planted, dict) or not isinstance(planted.get("sha256"), str) or not isinstance(planted.get("path"), str):
        return f"key {key} is absent or lacks sha256 and path, so nothing shows that the foe canary was planted"
    if planted["path"] != item.get("path"):
        return f"key {key}.path is {planted['path']!r}, and canaries.{run.FOE_CONFIG_CANARY}.path is {item.get('path')!r}"
    expected = hashlib.sha256(planted_text(item["sentence"]).encode("utf-8")).hexdigest()
    if planted["sha256"] != expected:
        return f"key {key}.sha256 is {planted['sha256']}, and the recorded sentence followed by a newline digests to {expected}, so the planted file did not hold the sentence"
    return None


def record_files(out: Path) -> list[Path]:
    return sorted((out / run.RECORDS_DIR).rglob("*.json")) if (out / run.RECORDS_DIR).is_dir() else []


def read_json_lines(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
    """Every JSON object line of a file with its line number, and one description per nonblank line that is not a JSON object."""
    objects: list[tuple[int, dict[str, Any]]] = []
    malformed: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            malformed.append(f"{path}:{number} is not JSON")
            continue
        if isinstance(value, dict):
            objects.append((number, value))
        else:
            malformed.append(f"{path}:{number} is not a JSON object")
    return objects, malformed


def read_log_lines(log: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Every JSON object line of a log, and one description per line that is not one."""
    objects, malformed = read_json_lines(log)
    return [value for _, value in objects], malformed


def search_foe(episode_dir: Path, sentences: dict[str, str]) -> tuple[dict[str, list[str]], int, int, list[str]]:
    """Where each canary appears in the request events under the episode directory, how many well-formed requests and logs were searched, and the lines and events that fail qualification."""
    hits: dict[str, list[str]] = {name: [] for name in sentences}
    requests = logs = 0
    malformed: list[str] = []
    for log in sorted(episode_dir.rglob(LOG_NAME)):
        logs += 1
        events, bad = read_log_lines(log)
        malformed.extend(bad)
        for event in events:
            if event.get("type") not in REQUEST_EVENTS:
                continue
            text = json.dumps(event.get("data"), ensure_ascii=False)
            if isinstance(event.get("data"), dict) and event["data"]:
                requests += 1
            else:
                malformed.append(f"{log} seq {event.get('seq')} ({event.get('type')}) carries no request: its data is not a nonempty object")
            for name, sentence in sentences.items():
                if sentence in text:
                    hits[name].append(f"{log} seq {event.get('seq')} ({event.get('type')})")
    return hits, requests, logs, malformed


def search_codex(sessions: list[Path], events: Path, sentences: dict[str, str]) -> tuple[dict[str, list[str]], int, int, list[str]]:
    """Where each canary appears in the session files and the event stream, how many files were searched, how many session records of each request type carry a payload, and the files, lines, and records that fail qualification."""
    hits: dict[str, list[str]] = {name: [] for name in sentences}
    searched = 0
    request_records = {name: 0 for name in CODEX_REQUEST_RECORDS}
    problems: list[str] = []
    for path in [*sessions, events]:
        if not path.is_file():
            problems.append(f"{path} is missing")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path} is unreadable: {exc}")
            continue
        searched += 1
        for number, line in enumerate(text.splitlines(), start=1):
            for name, sentence in sentences.items():
                if sentence in line:
                    hits[name].append(f"{path}:{number}")
        objects, malformed = read_json_lines(path)
        problems.extend(malformed)
        if path == events:
            continue
        for number, value in objects:
            if value.get("type") not in CODEX_REQUEST_RECORDS:
                continue
            if isinstance(value.get("payload"), dict) and value["payload"]:
                request_records[value["type"]] += 1
            else:
                problems.append(f"{path}:{number} is a {value['type']} record without a payload")
    return hits, searched, request_records, problems


def codex_canary_planted(canary_file: Any, sentences: dict[str, str], locate: Locate = Path) -> bool:
    """Whether the recorded canary file exists and holds a Codex canary sentence of the run."""
    if not isinstance(canary_file, str) or not locate(canary_file).is_file():
        return False
    try:
        text = locate(canary_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return any(sentence in text for name, sentence in sentences.items() if name.split(":")[0] == run.CODEX_CONFIG_CANARY)


def examine(record: dict[str, Any], sentences: dict[str, str], locate: Locate = Path) -> dict[str, Any]:
    """One attempt's isolation result: the hits per canary, what was searched, whether the Codex canary was planted, and the qualification failures; `locate` maps each recorded path to where it is read."""
    label = f"{record['task']['name']} / {record['arm']} / {record['attempt']}"
    result: dict[str, Any] = {"attempt": label, "harness": record["harness"], "launched": False, "searched": None, "hits": {name: [] for name in sentences}, "planted": None, "qualification": []}
    arm_record = (record.get("arm_result") or {}).get("record")
    if record.get("not_applicable") is not None:
        result["searched"] = "nothing: the attempt was not applicable and never ran"
        return result
    if not isinstance(arm_record, dict):
        result["searched"] = f"nothing: the arm did not launch ({record.get('infrastructure_error')})"
        return result
    result["launched"] = True
    failures: list[str] = result["qualification"]
    if record["harness"] == "foe":
        hits, requests, logs, malformed = search_foe(locate(arm_record["episode_dir"]), sentences)
        result["searched"] = f"{requests} request events in {logs} episode logs"
        model_calls = (record.get("totals") or {}).get("model_calls")
        if logs == 0:
            failures.append(f"the episode directory {arm_record['episode_dir']} holds no {LOG_NAME}, so no request was recorded")
        elif requests == 0:
            failures.append(f"the episode logs hold no well-formed {' or '.join(REQUEST_EVENTS)} event, so no request was recorded")
        if not isinstance(model_calls, int) or isinstance(model_calls, bool):
            failures.append(f"the record's totals.model_calls is {model_calls!r}, not an integer, so the recorded requests cannot be counted against the calls made")
        elif logs and requests and requests < model_calls:
            failures.append(f"the episode logs hold {requests} {' and '.join(REQUEST_EVENTS)} events, fewer than the {model_calls} totals.model_calls of the normalized trajectory")
        failures.extend(f"episode log {line}" for line in malformed)
        if record.get(PRESENT_KEY) is not True:
            failures.append(f"the record's {PRESENT_KEY} is {record.get(PRESENT_KEY)!r}, so nothing shows the foe canary was in place when the attempt started")
    else:
        session_files = [locate(item) for item in arm_record.get("session_files") or []]
        hits, searched, request_records, problems = search_codex(session_files, locate(arm_record["events"]), sentences)
        result["searched"] = f"{searched} files: every session file and the event stream"
        if not session_files:
            failures.append("the arm record lists no session file, so no request was recorded")
        elif sum(request_records.values()) == 0:
            failures.append(f"no session file holds a {' or '.join(CODEX_REQUEST_RECORDS)} record with a payload, so no request was recorded")
        model_calls = (record.get("totals") or {}).get("model_calls")
        items = request_records["response_item"]
        if isinstance(model_calls, int) and not isinstance(model_calls, bool) and items < model_calls:
            failures.append(f"the session files hold {items} response_item records, fewer than the {model_calls} totals.model_calls of the normalized trajectory, and every call returns at least one")
        failures.extend(f"codex record file {problem}" for problem in problems)
        result["planted"] = codex_canary_planted(arm_record.get("config_canary_file"), sentences, locate)
        if not result["planted"]:
            failures.append(f"the codex canary file {arm_record.get('config_canary_file')} is absent or does not hold the codex canary sentence, so the canary was not planted")
    result["hits"] = hits
    return result


def repository_relative(path: Path) -> str:
    """A path relative to the repository that holds this gate when the repository holds it; otherwise as it stands."""
    try:
        return path.relative_to(REPOSITORY).as_posix()
    except ValueError:
        return str(path)


def lines_of(results: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for result in results:
        verdicts = ", ".join(f"{name} {'absent' if not hits else 'PRESENT at ' + '; '.join(hits)}" for name, hits in result["hits"].items())
        line = f"  {result['attempt']}: {result['harness']}, searched {result['searched']}: {verdicts}"
        if result["planted"] is not None:
            line += "; the codex canary was " + ("planted in CODEX_HOME" if result["planted"] else "NOT planted, so its absence proves nothing")
        lines.append(line)
        lines.extend(f"    qualification failure: {rule}" for rule in result["qualification"])
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Exit status 0 means every canary is absent from every recorded request and the request evidence and canary planting of every launched attempt qualify, 1 that a request carries a canary, 2 that the run has no records or no record launched a harness, 3 that a run file or a record cannot be read, and 4 that no request carries a canary but a qualification failure leaves the absence unproven.",
    )
    parser.add_argument("document", type=Path, help="the run document whose out directory holds the run files and records")
    parser.add_argument("--out", type=Path, help="the run directory to examine in place of the document's out")
    parser.add_argument("--state-root", type=Path, help="a copy of the runner's state root, under which every path a record names under a state root is read")
    args = parser.parse_args(argv)
    try:
        document = run.load_document(args.document)
    except (ValueError, FileNotFoundError) as exc:
        print(f"isolation gate: {exc}", file=sys.stderr)
        return NO_RECORDS
    home = rescore.home_directory()
    state_root = args.state_root.resolve() if args.state_root is not None else rescore.expand_home(rescore.RECORDED_STATE_ROOT, home)

    def locate(recorded: str) -> Path:
        return rescore.under_state_root(recorded, state_root, home) if args.state_root is not None else Path(recorded)

    if args.out is not None:
        out = args.out.expanduser().resolve()
    else:
        out = locate(str(document.out))
    files = run_files(out)
    records = record_files(out)
    if not files or not records:
        print(f"isolation gate: {out} holds {len(files)} run files and {len(records)} records; nothing to examine", file=sys.stderr)
        return NO_RECORDS
    try:
        sentences = read_canaries(files)
        planting = [(path, foe_planting_failure(path)) for path in files]
        results = [examine(json.loads(path.read_text(encoding="utf-8")), sentences, locate) for path in records]
    except (ValueError, FileNotFoundError, KeyError, TypeError) as exc:
        print(f"isolation gate: a run file or record under {out} cannot be read: {type(exc).__name__}: {exc}", file=sys.stderr)
        return MALFORMED
    launched = sum(1 for result in results if result["launched"])
    failures = [{"attempt": result["attempt"], "rule": rule} for result in results for rule in result["qualification"]]
    if launched:
        failures = [{"attempt": f"run file {path.name}", "rule": rule} for path, rule in planting if rule is not None] + failures
    leaked = sum(len(hits) for result in results for hits in result["hits"].values())
    unplanted = [result["attempt"] for result in results if result["planted"] is False]
    if leaked:
        status, verdict = LEAKED, f"leaked: {leaked} recorded requests carry a canary"
    elif not launched:
        status, verdict = NO_RECORDS, "no records: no record launched a harness, so no request was recorded"
    elif failures:
        status, verdict = UNQUALIFIED, f"unqualified: no recorded request carries a canary, and {len(failures)} qualification failures leave the absence unproven"
    else:
        status, verdict = ISOLATED, "isolated: every canary is absent from every recorded request, and every launched attempt's evidence qualifies"
    run_lines = [f"  run file {path.name}: foe canary planting {'evidenced' if rule is None else 'NOT evidenced: ' + rule}" for path, rule in planting]
    text = "\n".join([f"isolation gate over {out}: {len(records)} records in {len(files)} run files", *run_lines, *lines_of(results), f"verdict: {verdict}"])
    print(text)
    run.write_json(
        out / RESULT_FILE,
        {"document": repository_relative(document.path), "out": rescore.recorded_form(out, state_root), "canaries": sentences, "results": results, "leaked": leaked, "unplanted": unplanted, "qualification_failures": failures, "status": status, "verdict": verdict},
    )
    return status


if __name__ == "__main__":
    sys.exit(main())
