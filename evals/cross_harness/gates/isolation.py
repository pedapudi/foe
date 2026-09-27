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
has nothing to search and is reported as such. The Codex canary is also
reported as planted or unplanted per Codex attempt, since an absence proves
nothing when the file was never written.

    isolation.py DOCUMENT

The gate prints one line per attempt naming whether each canary is absent,
writes `gates/isolation.json` under `out`, and exits 0 when every canary is
absent from every recorded request, 1 when any request carries one, 2 when
the run has no records, and 3 when a run file or a record cannot be read.
It calls no model and spends nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CROSS_HARNESS = HERE.parent
sys.path.insert(0, str(CROSS_HARNESS))

import run  # noqa: E402

ISOLATED, LEAKED, NO_RECORDS, MALFORMED = 0, 1, 2, 3
RESULT_FILE = Path("gates") / "isolation.json"
# The foe log events that carry what the model received: the system prompt
# and tool schemas in effect, and the messages of each call.
REQUEST_EVENTS: tuple[str, ...] = ("request/header", "model/request")
LOG_NAME = "episode.jsonl"


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


def record_files(out: Path) -> list[Path]:
    return sorted((out / run.RECORDS_DIR).rglob("*.json")) if (out / run.RECORDS_DIR).is_dir() else []


def read_log_lines(log: Path) -> list[dict[str, Any]]:
    """Every JSON object line of a log; a line that is not one is skipped, because the gate searches what was recorded rather than validating the log."""
    events: list[dict[str, Any]] = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def search_foe(episode_dir: Path, sentences: dict[str, str]) -> tuple[dict[str, list[str]], int, int]:
    """Where each canary appears in the request events under the episode directory, and how many requests and logs were searched."""
    hits: dict[str, list[str]] = {name: [] for name in sentences}
    requests = logs = 0
    for log in sorted(episode_dir.rglob(LOG_NAME)):
        logs += 1
        for event in read_log_lines(log):
            if event.get("type") not in REQUEST_EVENTS:
                continue
            requests += 1
            text = json.dumps(event.get("data"), ensure_ascii=False)
            for name, sentence in sentences.items():
                if sentence in text:
                    hits[name].append(f"{log} seq {event.get('seq')} ({event.get('type')})")
    return hits, requests, logs


def search_files(paths: list[Path], sentences: dict[str, str]) -> tuple[dict[str, list[str]], int]:
    """Where each canary appears in the files, by path and line number, and how many files were searched."""
    hits: dict[str, list[str]] = {name: [] for name in sentences}
    searched = 0
    for path in paths:
        if not path.is_file():
            continue
        searched += 1
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            for name, sentence in sentences.items():
                if sentence in line:
                    hits[name].append(f"{path}:{number}")
    return hits, searched


def examine(record: dict[str, Any], sentences: dict[str, str]) -> dict[str, Any]:
    """One attempt's isolation result: the hits per canary, what was searched, and whether the Codex canary was planted."""
    label = f"{record['task']['name']} / {record['arm']} / {record['attempt']}"
    result: dict[str, Any] = {"attempt": label, "harness": record["harness"], "searched": None, "hits": {name: [] for name in sentences}, "planted": None}
    arm_record = (record.get("arm_result") or {}).get("record")
    if record.get("not_applicable") is not None:
        result["searched"] = "nothing: the attempt was not applicable and never ran"
        return result
    if not isinstance(arm_record, dict):
        result["searched"] = f"nothing: the arm did not launch ({record.get('infrastructure_error')})"
        return result
    if record["harness"] == "foe":
        hits, requests, logs = search_foe(Path(arm_record["episode_dir"]), sentences)
        result["searched"] = f"{requests} request events in {logs} episode logs"
    else:
        paths = [Path(item) for item in arm_record.get("session_files") or []] + [Path(arm_record["events"])]
        hits, searched = search_files(paths, sentences)
        result["searched"] = f"{searched} files: every session file and the event stream"
        canary_file = arm_record.get("config_canary_file")
        planted = canary_file is not None and Path(canary_file).is_file() and any(sentence in Path(canary_file).read_text(encoding="utf-8") for sentence in sentences.values())
        result["planted"] = planted
    result["hits"] = hits
    return result


def lines_of(results: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for result in results:
        verdicts = ", ".join(f"{name} {'absent' if not hits else 'PRESENT at ' + '; '.join(hits)}" for name, hits in result["hits"].items())
        line = f"  {result['attempt']}: {result['harness']}, searched {result['searched']}: {verdicts}"
        if result["planted"] is not None:
            line += "; the codex canary was " + ("planted in CODEX_HOME" if result["planted"] else "NOT planted, so its absence proves nothing")
        lines.append(line)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="Exit status 0 means every canary is absent from every recorded request, 1 that a request carries one, 2 that the run has no records, and 3 that a run file or a record cannot be read.")
    parser.add_argument("document", type=Path, help="the run document whose out directory holds the run files and records")
    args = parser.parse_args(argv)
    try:
        document = run.load_document(args.document)
    except (ValueError, FileNotFoundError) as exc:
        print(f"isolation gate: {exc}", file=sys.stderr)
        return NO_RECORDS
    files = run_files(document.out)
    records = record_files(document.out)
    if not files or not records:
        print(f"isolation gate: {document.out} holds {len(files)} run files and {len(records)} records; nothing to examine", file=sys.stderr)
        return NO_RECORDS
    try:
        sentences = read_canaries(files)
        results = [examine(json.loads(path.read_text(encoding="utf-8")), sentences) for path in records]
    except (ValueError, FileNotFoundError, KeyError, TypeError) as exc:
        print(f"isolation gate: a run file or record under {document.out} cannot be read: {type(exc).__name__}: {exc}", file=sys.stderr)
        return MALFORMED
    leaked = sum(len(hits) for result in results for hits in result["hits"].values())
    unplanted = [result["attempt"] for result in results if result["planted"] is False]
    verdict = "every canary is absent from every recorded request" if leaked == 0 else f"{leaked} recorded requests carry a canary"
    text = "\n".join([f"isolation gate over {document.out}: {len(records)} records in {len(files)} run files", *lines_of(results), f"verdict: {verdict}"])
    if unplanted:
        text += "\n" + f"the codex canary was not planted for: {', '.join(unplanted)}"
    print(text)
    run.write_json(document.out / RESULT_FILE, {"document": str(document.path), "out": str(document.out), "canaries": sentences, "results": results, "leaked": leaked, "unplanted": unplanted, "verdict": verdict})
    return ISOLATED if leaked == 0 else LEAKED


if __name__ == "__main__":
    sys.exit(main())
