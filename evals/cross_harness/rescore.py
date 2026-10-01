#!/usr/bin/python3
"""Score committed attempt records again under the current scoring version, beside their original cells.

`tasks/protocol.py` classifies a graded attempt under a numbered set of
rules, the scoring version. Version 2 adds one rule to version 1: a stop
that changed a path the task lists in `preserved_on_stop` is damage. A
record committed under version 1 holds no such judgement, so this tool
computes it from what the attempt left behind.

    rescore.py RECORDS_ARRAY --state-root DIR --out FILE [--repo DIR] [--scratch DIR]

RECORDS_ARRAY is a committed JSON array of attempt summaries, as `run.py`
writes them. Each summary names its full record under `record`, as a path
under the runner's default state root `~/.local/state/foe/cross-harness`;
the tool reads it under `--state-root` instead. The full record names its
attempt root under `paths.root` as an absolute path, which lies under the
default state root of the home directory of the host that ran the attempt.
The tool reads that root under `--state-root` as well, whatever the home
directory was, so a copy of the state root on another host, such as the
extracted evidence archive `results/evidence-manifest.json` names, serves
in place of the original. A path under no state root is read where it
names. A leading `~` in any path resolves to the home directory the
password database names for the user.

For each attempt the tool reads the full record, the attempt root it names
under `paths.root`, and the `task.json` materialized in that root. It
rebuilds the fixture from that `task.json` and the root's own
`grader/workspace.patch` at the base commit the task names, in the
repository `metadata.source.repo` names or else in `--repo`, which defaults
to the repository holding this file. It compares every file under each
preserved path of the retained workspace with the fixture.

A task materialized before `preserved_on_stop` existed declares no
preserved path. For such a task the preserved paths are derived from the
construction's metadata: `metadata.artifact` for an inventory construction,
which records the artifact beside its generator, and `metadata.module` for
a frozen-interface construction, which records the module beside its frozen
root. Any other task preserves nothing.

The output is a JSON array with one entry per attempt, in the order of the
input: the task, arm, and attempt as the summary names them;
`cell_scoring_1`, the committed cell, unchanged; `cell_scoring_2`; the
changed preserved files, each with its fixture and observed SHA-256, null
for a file one side lacks; and `scoring_version`, 2. The committed array is
never written. The tool refuses an attempt whose committed cell differs from
the cell version 1 computes from the committed grade, since the two scores
would then not describe one grading.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pwd
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tasks"))

import protocol  # noqa: E402

# The state root `run.py` writes under by default, in the form committed
# summaries name their full records with.
RECORDED_STATE_ROOT = "~/.local/state/foe/cross-harness"
# The same state root as a record names it absolutely: this suffix of the
# path after the home directory of the host that ran the attempt.
STATE_ROOT_UNDER_HOME = "/" + RECORDED_STATE_ROOT[2:]
RESCORED_VERSION = 2


def home_directory() -> Path:
    """The user's home directory as the password database names it."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def expand_home(path: str, home: Path) -> Path:
    """A path with a leading `~` resolved against the given home directory."""
    if path == "~" or path.startswith("~/"):
        return home / path[2:]
    return Path(path)


def under_state_root(recorded: str, state_root: Path, home: Path) -> Path:
    """Where a recorded path lies when the runner's default state root is moved to `state_root`.

    A path written under the default state root is moved under
    `state_root`, in its `~` form and in its absolute form under any home
    directory, since a record names the root under the home directory of
    the host that wrote it. Any other path is taken as it stands, with a
    leading `~` resolved against `home`.
    """
    if recorded == RECORDED_STATE_ROOT:
        return state_root
    if recorded.startswith(RECORDED_STATE_ROOT + "/"):
        return state_root / recorded[len(RECORDED_STATE_ROOT) + 1 :]
    if recorded.startswith("/"):
        _, found, rest = recorded.partition(STATE_ROOT_UNDER_HOME)
        if found and (not rest or rest.startswith("/")):
            return state_root / rest[1:] if rest else state_root
    return expand_home(recorded, home)


def recorded_form(path: Path, state_root: Path) -> str:
    """A path under `state_root` in the `~` form the runner's records use, so that output names no host's copy; any other path as it stands."""
    try:
        relative = path.relative_to(state_root)
    except ValueError:
        return str(path)
    return RECORDED_STATE_ROOT if relative == Path(".") else f"{RECORDED_STATE_ROOT}/{relative.as_posix()}"


def preserved_paths(task: protocol.Task) -> tuple[str, ...]:
    """The paths a stop must preserve: the task's own list, or the derivation for a task authored before the key existed."""
    if task.preserved_on_stop:
        return task.preserved_on_stop
    metadata = task.metadata
    if isinstance(metadata.get("artifact"), str) and "generator" in metadata:
        return (metadata["artifact"],)
    if isinstance(metadata.get("module"), str) and "frozen_root" in metadata:
        return (metadata["module"],)
    return ()


class Fixtures:
    """Fixture workspaces rebuilt from a recipe, one per repository, base commit, and patch."""

    def __init__(self, scratch: Path, default_repo: Path) -> None:
        self.scratch = scratch
        self.default_repo = default_repo
        self.built: dict[tuple[str, str, str], Path] = {}

    def workspace(self, root: Path, task: protocol.Task) -> Path:
        """The fixture workspace of an attempt root: the base commit's tree plus the root's workspace patch."""
        prefix = f"{root / protocol.TASK_FILE}: key metadata.{protocol.SOURCE_KEY}"
        source = task.metadata.get(protocol.SOURCE_KEY)
        if not isinstance(source, dict):
            raise ValueError(f"{prefix} is {source!r}; the fixture cannot be rebuilt without a base commit")
        base = source.get("parent") or source.get("commit")
        if not isinstance(base, str) or not base:
            raise ValueError(f"{prefix}.commit is {source.get('commit')!r}; expected a commit hash")
        repo = Path(source["repo"]) if isinstance(source.get("repo"), str) else self.default_repo
        patch = root / protocol.GRADER / protocol.WORKSPACE_PATCH
        if not patch.is_file():
            raise FileNotFoundError(f"{patch} is absent; the fixture of {root} is rebuilt from it")
        key = (str(repo), base, hashlib.sha256(patch.read_bytes()).hexdigest())
        if key not in self.built:
            if not protocol.has_commit(repo, base):
                raise ValueError(f"{prefix} names the base commit {base}, which the repository {repo} does not contain")
            destination = self.scratch / f"fixture-{len(self.built)}"
            protocol.archive(repo, base, destination)
            protocol.apply_patch(patch, destination)
            self.built[key] = destination
        return self.built[key]


def changed_preserved(fixture: Path, workspace: Path, paths: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every file under a preserved path whose bytes differ between the fixture and the workspace."""
    expected, observed = protocol.hashes_under(fixture, paths), protocol.hashes_under(workspace, paths)
    return [
        {"path": relative, "fixture_sha256": expected.get(relative), "observed_sha256": observed.get(relative)}
        for relative in protocol.changed_files(expected, observed)
    ]


def rescore_attempt(summary: dict[str, Any], state_root: Path, home: Path, fixtures: Fixtures) -> dict[str, Any]:
    """One output entry for one committed attempt summary."""
    label = f"{summary.get('task')} / {summary.get('arm')} / {summary.get('attempt')}"
    for key in ("task", "arm", "attempt", "cell", "reported", "grade", "record"):
        if key not in summary:
            raise ValueError(f"attempt {label}: key {key} is absent from the committed summary")
    record_path = under_state_root(summary["record"], state_root, home)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    recorded_root = (record.get("paths") or {}).get("root")
    if not isinstance(recorded_root, str):
        raise ValueError(f"{record_path}: key paths.root is {recorded_root!r}; expected the attempt root")
    root = under_state_root(recorded_root, state_root, home)
    task = protocol.load(root)
    paths = preserved_paths(task)
    changed = changed_preserved(fixtures.workspace(root, task), root / protocol.WORKSPACE, paths) if paths else []
    task = dataclasses.replace(task, preserved_on_stop=paths)
    # The grader's form admits a code only with a blocked status, as in
    # `run.protocol_reported`; the limit an exhausted run names is dropped.
    status, code = str(summary["reported"]["status"]), summary["reported"].get("code")
    reported = protocol.Reported(status, str(code) if code is not None and status == protocol.BLOCKED else None)
    grade = summary["grade"]
    result = protocol.GradeResult(bool(grade["passed"]), list(grade["findings"]), list(grade["damage"]), [entry["path"] for entry in changed])
    recomputed = protocol.classify(task, reported, result, 1)
    if recomputed != summary["cell"]:
        raise ValueError(
            f"attempt {label}: key cell is {summary['cell']!r}, while scoring version 1 gives {recomputed!r} "
            "from the committed grade; the committed cell does not describe the committed grade"
        )
    return {
        "task": summary["task"],
        "arm": summary["arm"],
        "attempt": summary["attempt"],
        "cell_scoring_1": summary["cell"],
        "cell_scoring_2": protocol.classify(task, reported, result, RESCORED_VERSION),
        "preserved_changed": changed,
        "scoring_version": RESCORED_VERSION,
    }


def rescore(records: Path, state_root: Path, home: Path, repo: Path, scratch: Path | None = None) -> list[dict[str, Any]]:
    """The output entries for every attempt of a committed array, in its order."""
    summaries = json.loads(records.read_text(encoding="utf-8"))
    if not isinstance(summaries, list):
        raise ValueError(f"{records}: the document is not an array of attempt summaries")
    with tempfile.TemporaryDirectory(prefix="rescore-", dir=scratch) as temporary:
        fixtures = Fixtures(Path(temporary), repo)
        return [rescore_attempt(summary, state_root, home, fixtures) for summary in summaries]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=str, help="a committed JSON array of attempt summaries")
    parser.add_argument("--state-root", required=True, help="the directory that holds the full records and attempt roots: the runner's state root or a copy of it")
    parser.add_argument("--out", required=True, help="the JSON file the entries are written to")
    parser.add_argument("--repo", help="the repository holding the base commits; default: the repository holding this file")
    parser.add_argument("--scratch", help="where fixtures are rebuilt; default: the system temporary directory")
    args = parser.parse_args(argv)
    home = home_directory()
    records, out = expand_home(args.records, home), expand_home(args.out, home)
    repo = expand_home(args.repo, home) if args.repo else protocol.repository_of(HERE)
    scratch = expand_home(args.scratch, home) if args.scratch else None
    if out.resolve() == records.resolve():
        print(f"rescore: --out names the committed array {records}, which is never rewritten", file=sys.stderr)
        return 2
    try:
        entries = rescore(records, expand_home(args.state_root, home), home, repo, scratch)
    except (OSError, ValueError, KeyError) as error:
        print(f"rescore: {error}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    moved = [entry for entry in entries if entry["cell_scoring_1"] != entry["cell_scoring_2"]]
    print(f"{records}: {len(entries)} attempts, {len(moved)} changed cell under scoring version {RESCORED_VERSION}")
    for entry in moved:
        print(f"  {entry['task']} / {entry['arm']} / {entry['attempt']}: {entry['cell_scoring_1']} -> {entry['cell_scoring_2']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
