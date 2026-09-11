#!/usr/bin/python3
"""Author a task by removing one committed feature from foe's own tree.

A commit that adds a feature with its tests is a task with a known answer:
the tree before the commit is the fixture, the commit's tests are the hidden
checks, and the commit itself is the oracle. `author` turns one such commit
into a task directory in the layout `protocol.py` fixes.

The commit's diff is split into test files, which are files named `*_test.rs`
or `*_test.py` or held under a `tests/` directory, and implementation files,
which are every other file, including documents. The workspace is the
parent commit's tree, read with `git archive`, so it holds no test file the
commit creates and holds the parent form of every test file the commit
changes. One test file per touched crate is copied into the workspace in
its commit form as the visible subset of the checks; the grader holds the
commit form of every test file.

Before the visible subset is copied in, every identifier the implementation
adds is searched for in the workspace. A hit is a trace of the feature the
fixture is meant to lack. Authoring fails while any hit is unexplained;
`--allow-traces` names identifiers whose hits are accepted, such as a method
name that already exists elsewhere, and the task records them.

The task directory is a recipe: `task.json` records the commit and its
parent under `metadata.source`, and `grader/workspace.patch` is the diff
from the parent's tree to the workspace, so `protocol.materialize` rebuilds
the workspace from the repository. The workspace copy is removed once the
patch is written; `--keep-workspace` keeps it for inspection. When the task
directory lies outside the repository's working tree, `metadata.source.repo`
names the repository.

The grader directory holds the whole commit diff as `oracle.patch`, the
commit form of every changed file under `oracle/workspace/`, the commit form
of every test file under `tests/`, `specification.json`, `grade.py` with its
executable wrapper `grade`, and one corruption, `revert-one-crate`, which
restores the parent form of one touched crate's implementation files. The
grade script copies the workspace into a directory of its own, restores the
hidden tests into the copy, and runs `cargo test` and `cargo clippy -- -D
warnings` for the touched crates and `scripts/loc.sh` there. A Rust test
file under `src/` compiles only through a `mod` declaration in the
implementation, so the script also requires the name of every `#[test]`
function in a hidden test file to appear in the `cargo test` output. It then
requires every specification sentence the commit added under `docs/` to
appear in the workspace's documents after whitespace and markup are
normalized. The grade script reads an optional `grader/host.json` document
naming `cargo` and `build_dir`; without it, it takes `cargo` from PATH and
builds under the user's state directory. Every grade of a task shares one
cargo target directory under the build directory, so dependencies compile
once; each grade keeps its copy and its logs in a directory of its own, so
grades of one task may run at once. A grade runs cargo, so it needs more
than the sixty seconds `protocol.grade` allows; `verify` runs the same
controls as `protocol.check_grader_controls` with a timeout long enough for
a build.

The task text is drafted from the commit message, without its trailers, and
the document changes. It is a draft: `metadata.review` says so, and a person
reads it before the task is used.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol  # noqa: E402
from protocol import COMPLETED, GRADER, WORKSPACE, Reported, Task  # noqa: E402

CLASS_FAMILY: dict[str, str] = {"solvable": "autonomy", "coherent": "teams"}
PROTECTED: tuple[str, ...] = ("scripts/loc.sh", "AGENTS.md", "checks/run.sh")
CHECKS_SCRIPT = "checks/run.sh"
CORRUPTION_NAME = "revert-one-crate"
HIDDEN_TESTS = "tests"
SPECIFICATION_FILE = "specification.json"
ORACLE_PATCH = "oracle.patch"
CRATES = "crates"
DOCS = "docs"
GIT = "/usr/bin/git"
# The commands of one grade (cargo test, cargo clippy, scripts/loc.sh) share
# this budget inside the grade script; the whole grade gets the budget plus
# time for the workspace copy and the sentence checks.
GRADE_COMMAND_SECONDS = 1200
GRADE_TIMEOUT_SECONDS = GRADE_COMMAND_SECONDS + 120
# The shortest normalized sentence the grader requires; a shorter fragment
# is a table cell or a cross reference.
MIN_SENTENCE = 25
# How many hits of one identifier an authoring failure lists.
TRACE_EXAMPLES = 5

ADDED, MODIFIED, DELETED = "added", "modified", "deleted"

_TEST_FILE = re.compile(r"(^|/)tests/|_test\.rs$|_test\.py$")
# A Rust test function: a `#[test]` or `#[<runtime>::test(...)]` attribute,
# further attributes, then the function header.
_RUST_TEST = re.compile(
    r"#\[(?:[A-Za-z_][A-Za-z0-9_]*::)?test(?:\([^)]*\))?\]\s*(?:#\[[^\]]*\]\s*)*"
    r"(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"
)
# One line of a git trailer block, such as `Reviewed-by: name`.
_TRAILER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")
_RUST_DEFINITION = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+|extern\s+\"[^\"]*\"\s+)*"
    r"(?:fn|struct|enum|const|static|mod|trait|type|union)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_RUST_MACRO = re.compile(r"^\s*macro_rules!\s+([A-Za-z_][A-Za-z0-9_]*)")
_PYTHON_DEFINITION = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_EVENT_NAME = re.compile(r"`([a-z][a-z0-9_]*/[a-z][a-z0-9_]*)`")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'`)\]]*\s+(?=[A-Z0-9`\"'(\[*])")
_ISSUE_SUFFIX = re.compile(r"\s*\(#\d+\)\s*$")


@dataclass(frozen=True)
class FileDiff:
    """One file's part of a commit diff, as `git show --no-renames -p` prints it."""

    path: str
    status: str
    # Every hunk line in order: the marker (" ", "+", "-", or "@" for a hunk
    # header) and the line text without the marker.
    lines: tuple[tuple[str, str], ...]
    text: str

    @property
    def added(self) -> list[str]:
        return [text for marker, text in self.lines if marker == "+"]

    @property
    def removed(self) -> list[str]:
        return [text for marker, text in self.lines if marker == "-"]

    @property
    def is_test(self) -> bool:
        return is_test_path(self.path)

    @property
    def crate(self) -> str | None:
        """The crate directory `crates/<name>` the file belongs to, or None."""
        parts = Path(self.path).parts
        if len(parts) >= 3 and parts[0] == CRATES:
            return f"{CRATES}/{parts[1]}"
        return None


def is_test_path(path: str) -> bool:
    return _TEST_FILE.search(path) is not None


def test_names(source: str) -> list[str]:
    """The names of the test functions a Rust source file declares, in file order."""
    return _RUST_TEST.findall(source)


def parse_diff(text: str) -> list[FileDiff]:
    """The per-file parts of a unified diff over a repository, in diff order."""
    diffs: list[FileDiff] = []
    blocks = re.split(r"^(?=diff --git )", text, flags=re.MULTILINE)
    for block in blocks:
        if not block.startswith("diff --git "):
            continue
        header, _, body = block.partition("\n")
        match = re.match(r'diff --git a/(.*?) b/(.*)$', header)
        if match is None:
            raise ValueError(f"diff header {header!r} names no path pair")
        old_path, new_path = match.group(1), match.group(2)
        if old_path != new_path:
            raise ValueError(f"diff of {old_path!r} renames it to {new_path!r}; the oracle overlay cannot rename")
        status = MODIFIED
        lines: list[tuple[str, str]] = []
        in_hunks = False
        for line in body.split("\n"):
            if not in_hunks:
                if line.startswith("new file mode"):
                    status = ADDED
                elif line.startswith("deleted file mode"):
                    status = DELETED
                elif line.startswith("@@"):
                    in_hunks = True
                    lines.append(("@", line))
                continue
            if line.startswith("@@"):
                lines.append(("@", line))
            elif line.startswith("\\ No newline") or line == "":
                continue
            elif line[:1] in (" ", "+", "-"):
                lines.append((line[0], line[1:]))
        diffs.append(FileDiff(new_path, status, tuple(lines), block))
    return diffs


def partition(diffs: list[FileDiff]) -> tuple[list[FileDiff], list[FileDiff]]:
    """The test files and the implementation files of a diff, each in diff order."""
    tests = [diff for diff in diffs if diff.is_test]
    implementation = [diff for diff in diffs if not diff.is_test]
    return tests, implementation


def crates_touched(diffs: list[FileDiff]) -> list[str]:
    """The crate directories the diff touches, in sorted order."""
    return sorted({diff.crate for diff in diffs if diff.crate is not None})


def added_identifiers(implementation: list[FileDiff]) -> list[str]:
    """Names the implementation defines on added lines and no removed line defines.

    A Rust or Python definition on an added line counts; so does a log event
    name a document mentions in backticks on an added line. A name a removed
    line also defines or mentions existed before the commit and is left out.
    """

    def names(line: str, path: str) -> set[str]:
        found: set[str] = set()
        if path.endswith(".rs"):
            for pattern in (_RUST_DEFINITION, _RUST_MACRO):
                match = pattern.match(line)
                if match:
                    found.add(match.group(1))
        elif path.endswith(".py"):
            match = _PYTHON_DEFINITION.match(line)
            if match:
                found.add(match.group(1))
        elif path.startswith(f"{DOCS}/") and path.endswith(".md"):
            found.update(_EVENT_NAME.findall(line))
        return found

    added: set[str] = set()
    removed: set[str] = set()
    for diff in implementation:
        for line in diff.added:
            added |= names(line, diff.path)
        for line in diff.removed:
            removed |= names(line, diff.path)
    return sorted(added - removed)


def _regular_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def traces(workspace: Path, identifiers: list[str]) -> dict[str, list[str]]:
    """Every `path:line` in the workspace that holds one of the identifiers as a whole word."""
    patterns = {name: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])") for name in identifiers}
    hits: dict[str, list[str]] = {name: [] for name in identifiers}
    for file in _regular_files(workspace):
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = file.relative_to(workspace).as_posix()
        for number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in patterns.items():
                if pattern.search(line):
                    hits[name].append(f"{relative}:{number}")
    return {name: found for name, found in hits.items() if found}


def normalize(text: str) -> str:
    """Prose with markup removed and whitespace collapsed, in lower case."""
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return " ".join(text.split()).lower()


def _blocks(diff: FileDiff) -> list[tuple[str, bool, bool]]:
    """Runs of added lines in a document: the text, and whether each end is complete.

    An end is complete when no unchanged line continues the sentence across
    it. A table row is split into cells, and a cell is complete at both ends.
    """
    blocks: list[tuple[str, bool, bool]] = []
    lines = diff.lines

    def neighbour(candidates: Any) -> str | None:
        """The nearest unchanged line, or None at a hunk boundary."""
        for marker, text in candidates:
            if marker == "@":
                return None
            if marker == " ":
                return text
        return None

    index = 0
    while index < len(lines):
        marker, text = lines[index]
        if marker != "+":
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index][0] == "+":
            index += 1
        run = [text for _, text in lines[start:index]]
        before = neighbour(reversed(lines[:start]))
        after = neighbour(lines[index:])
        head_complete = before is None or before.strip() == "" or before.rstrip().endswith((".", "!", "?", ":", "|")) or before.startswith("#")
        tail_complete = after is None or after.strip() == "" or run[-1].rstrip().endswith((".", "!", "?", ":", "|")) or after.startswith("#")
        # Table rows split into cells, in document order with the prose around them.
        segments: list[list[str]] = []
        for line in run:
            if line.lstrip().startswith("|"):
                for cell in line.strip().strip("|").split("|"):
                    segments.append([cell.strip(), "|"])
            elif segments and segments[-1][-1] != "|":
                segments[-1].append(line)
            else:
                segments.append([line])
        for position, segment in enumerate(segments):
            if segment[-1] == "|":
                blocks.append((segment[0], True, True))
                continue
            first, last = position == 0, position == len(segments) - 1
            blocks.append((" ".join(line.strip() for line in segment), head_complete or not first, tail_complete or not last))
    return blocks


def specification_sentences(implementation: list[FileDiff]) -> dict[str, list[str]]:
    """The complete sentences the commit adds to each document under docs/, normalized.

    A sentence that the removed lines of the same document already hold,
    with or without its final period, existed before the commit and is
    left out: the commit re-wrapped or punctuated it.
    """
    sentences: dict[str, list[str]] = {}
    for diff in implementation:
        if not (diff.path.startswith(f"{DOCS}/") and diff.path.endswith(".md")) or diff.status == DELETED:
            continue
        removed = normalize(" ".join(diff.removed))
        found: list[str] = []
        for text, head_complete, tail_complete in _blocks(diff):
            if text.startswith("#") or text.startswith("```"):
                continue
            parts = _SENTENCE_END.split(text)
            if not head_complete:
                parts = parts[1:]
            if not tail_complete:
                parts = parts[:-1]
            for part in parts:
                normalized = normalize(part)
                if len(normalized) < MIN_SENTENCE or normalized in found or normalized.rstrip(".") in removed:
                    continue
                found.append(normalized)
        if found:
            sentences[diff.path] = found
    return sentences


def missing_sentences(workspace: Path, sentences: dict[str, list[str]]) -> list[str]:
    """Findings for every specification sentence a workspace document lacks."""
    findings: list[str] = []
    for path, expected in sentences.items():
        document = workspace / path
        if not document.is_file():
            findings.append(f"{path} is absent from the workspace")
            continue
        text = normalize(document.read_text(encoding="utf-8"))
        for sentence in expected:
            if sentence not in text:
                findings.append(f"{path} lacks the sentence: {sentence}")
    return findings


def git(repo: Path, *args: str, binary: bool = False) -> Any:
    """Run one git command in the repository and return its standard output."""
    result = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo} exited {result.returncode}: {result.stderr.decode('utf-8', 'replace').strip()}")
    if binary:
        return result.stdout
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"git {' '.join(args)} in {repo} printed text that is not UTF-8 at byte {error.start}") from None


def commit_diff(repo: Path, commit: str) -> str:
    """The commit's diff with the `a/` and `b/` path prefixes `parse_diff` reads, whatever the host's git configuration says."""
    data = git(
        repo, "show", "--format=", "--no-color", "--no-renames", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "-p", commit, binary=True
    )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        for block in re.split(rb"^(?=diff --git )", data, flags=re.MULTILINE):
            try:
                block.decode("utf-8")
            except UnicodeDecodeError:
                header = block.split(b"\n", 1)[0].decode("utf-8", "replace")
                raise ValueError(f"commit {commit} in {repo}: the diff block {header!r} is not UTF-8 text, and the oracle patch is a UTF-8 document") from None
        raise


def commit_message(repo: Path, commit: str) -> tuple[str, str]:
    """The subject without an issue suffix, and the body without its trailer block."""
    subject = git(repo, "log", "-1", "--format=%s", commit).strip()
    body = git(repo, "log", "-1", "--format=%b", commit).strip()
    paragraphs = re.split(r"\n\s*\n", body)
    if len(paragraphs) > 1 and all(_TRAILER_LINE.match(line) for line in paragraphs[-1].splitlines()):
        body = "\n\n".join(paragraphs[:-1]).strip()
    return _ISSUE_SUFFIX.sub("", subject), body


def parent_of(repo: Path, commit: str) -> str:
    parents = git(repo, "log", "-1", "--format=%P", commit).split()
    if len(parents) != 1:
        raise ValueError(f"commit {commit} in {repo} has {len(parents)} parents; a feature commit has one")
    return parents[0]


def show_file(repo: Path, tree: str, path: str) -> bytes:
    return git(repo, "show", f"{tree}:{path}", binary=True)


def uses_identifier(repo: Path, tree: str, directory: str, identifier: str) -> bool:
    result = subprocess.run(
        [GIT, "-C", str(repo), "grep", "-l", "-w", "-e", identifier, tree, "--", directory],
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep for {identifier!r} in {tree}:{directory} exited {result.returncode}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.returncode == 0


def coupling(repo: Path, commit: str, crates: list[str], identifiers: list[str]) -> int:
    """How many added identifiers two or more of the touched crates use at the commit."""
    count = 0
    for identifier in identifiers:
        users = sum(1 for crate in crates if uses_identifier(repo, commit, crate, identifier))
        if users >= 2:
            count += 1
    return count


def package_name(workspace: Path, crate: str) -> str:
    """The `[package] name` of a crate directory's Cargo.toml."""
    manifest = workspace / crate / "Cargo.toml"
    if not manifest.is_file():
        raise FileNotFoundError(f"{manifest} is absent; every touched crate has a manifest")
    in_package = False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_package = stripped == "[package]"
            continue
        match = re.match(r'name\s*=\s*"([^"]+)"', stripped)
        if in_package and match:
            return match.group(1)
    raise ValueError(f"{manifest}: key package.name is absent")


def budget_for(crate_count: int) -> dict[str, int]:
    """The default budget, which grows with the number of crates a task touches."""
    return {
        "model_calls": 30 + 30 * crate_count,
        "input_tokens": 1_000_000 + 1_000_000 * crate_count,
        "output_tokens": 60_000 + 40_000 * crate_count,
        "seconds": 1800 + 900 * crate_count,
    }


def draft_text(subject: str, body: str, sentences: dict[str, list[str]], packages: list[str]) -> str:
    """The specification paragraph of the task, drafted from the commit and its document changes."""
    parts = [subject.rstrip(".") + "."]
    if body:
        parts.append(body)
    if sentences:
        lines = ["The documents under docs/ describe the behavior. Each sentence below belongs in the document named before it, in the same words:"]
        for path, found in sentences.items():
            for sentence in found:
                lines.append(f"- {path}: {sentence}")
        parts.append("\n".join(lines))
    parts.append(
        f"{CHECKS_SCRIPT} runs the checks this task is judged on: the tests of {', '.join(packages)}, "
        "clippy with warnings denied on the same crates, and scripts/loc.sh."
    )
    return protocol.autonomy_text("\n\n".join(parts))


GRADE_WRAPPER = '#!/bin/sh\nexec /usr/bin/python3 -B "$(dirname "$0")/grade.py"\n'

_GRADE_TEMPLATE = r'''#!/usr/bin/python3
"""Hidden checks for a feature-removal task: the commit's tests, clippy, the line budgets, and the specification sentences.

The script runs from the workspace, which holds `Cargo.toml` and `crates/`.
It copies the workspace without its build directory, restores the commit
form of every test file into the copy, and runs `cargo test` and `cargo
clippy -- -D warnings` for the touched crates and `scripts/loc.sh` there, so
the agent's own test files never decide the grade. A Rust test file under
`src/` compiles only through a `mod` declaration in the implementation, so
the script requires the name of every test function in a hidden test file to
appear in the `cargo test` output. It then requires every specification
sentence the commit added to a document to appear in the workspace's
document.

`specification.json` beside this script names the crates, the hidden test
names, and the sentences. An optional `host.json` beside it names `cargo`
and `build_dir`; otherwise cargo comes from PATH and the build lives under
the user's state directory, read from the passwd database so that no
environment variable decides it. Under the build directory each task has a
cargo target directory that every grade of the task shares, and each grade
has a directory of its own for the copy and the command logs.
"""

import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GRADER = Path(__file__).resolve().parent
WORKSPACE = Path.cwd()
# The three commands share this budget; the first to reach it ends the grade.
COMMAND_SECONDS = __COMMAND_SECONDS__
TAIL_LINES = 12
_TEST_LINE = re.compile(r"^test (?:[A-Za-z0-9_:]+::)?([A-Za-z_][A-Za-z0-9_]*)(?: - should panic)? \.\.\. ")

specification = json.loads((GRADER / "specification.json").read_text(encoding="utf-8"))
host = {}
if (GRADER / "host.json").is_file():
    host = json.loads((GRADER / "host.json").read_text(encoding="utf-8"))
findings = []
arm = "unknown-arm"
try:
    arm = str(json.load(sys.stdin).get("arm") or arm)
except Exception as error:  # noqa: BLE001
    findings.append(f"the grader input is not JSON: {error}")

cargo = host.get("cargo") or shutil.which("cargo")
if cargo is None:
    findings.append("cargo is absent from PATH and host.json names none")
if not (WORKSPACE / "Cargo.toml").is_file() or not (WORKSPACE / "crates").is_dir():
    findings.append(f"the grade script runs from the workspace, and {WORKSPACE} holds no Cargo.toml and crates/ directory")
    cargo = None
build_dir = Path(host.get("build_dir") or Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "foe" / "cross-harness" / "build")
build = build_dir / specification["task"]
# Each task builds in its own target directory. Cargo names a workspace
# member's artifacts without the workspace path, so two tasks sharing one
# directory would serve each other stale crates. Every grade of one task
# shares the directory, so its dependencies compile once; cargo serializes
# grades that run at once on its lock for the directory.
target = build / "target"
deadline = time.monotonic() + COMMAND_SECONDS


def normalize(text):
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return " ".join(text.split()).lower()


def run(name, command, cwd, logs):
    """Run one command in its own process group and log its output; its output, or None when it failed."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        findings.append(f"{name}: `{' '.join(command)}` did not start; the {COMMAND_SECONDS}s the commands share is spent")
        return None
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        # The group holds cargo's own children, which would otherwise keep
        # the target directory's lock after cargo is gone.
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        findings.append(f"{name}: `{' '.join(command)}` ran past the {COMMAND_SECONDS}s the commands share and was killed")
        return None
    elapsed = time.monotonic() - started
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{name}.log").write_text(output, encoding="utf-8")
    print(f"{name}: exit {process.returncode} in {elapsed:.0f}s", file=sys.stderr)
    if process.returncode != 0:
        tail = [line for line in output.splitlines() if line.strip()][-TAIL_LINES:]
        findings.append(f"{name}: `{' '.join(command)}` exited {process.returncode}: " + " | ".join(tail))
        return None
    return output


if cargo is not None:
    grades = build / "grades" / re.sub(r"[^A-Za-z0-9_.-]+", "-", arm)
    grades.mkdir(parents=True, exist_ok=True)
    grade = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S") + "-", dir=grades))
    copy = grade / "copy"
    logs = grade / "logs"
    # The copies keep permission bits and take fresh modification times, so
    # cargo sees every source as newer than the last build and rebuilds the
    # workspace crates instead of reusing an artifact of an earlier grade.
    shutil.copytree(WORKSPACE, copy, symlinks=True, copy_function=shutil.copy, ignore=shutil.ignore_patterns("target"))
    hidden = GRADER / "tests"
    for file in sorted(path for path in hidden.rglob("*") if path.is_file()):
        destination = copy / file.relative_to(hidden)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(file, destination)
    packages = []
    for name in specification["packages"]:
        packages += ["-p", name]
    output = run("test", [cargo, "test", "--target-dir", str(target), *packages], copy, logs)
    if output is not None:
        ran = {match.group(1) for match in (_TEST_LINE.match(line) for line in output.splitlines()) if match}
        for path, names in specification["hidden_test_names"].items():
            absent = [name for name in names if name not in ran]
            if absent:
                findings.append(f"hidden test {path}: {', '.join(absent)} did not run; a test file under src/ compiles only through its mod declaration")
    run("clippy", [cargo, "clippy", "--target-dir", str(target), *packages, "--", "-D", "warnings"], copy, logs)
    loc = copy / "scripts" / "loc.sh"
    if loc.is_file():
        run("loc", [str(loc)], copy, logs)
    else:
        findings.append("scripts/loc.sh is absent from the workspace")
    # The logs stay for inspection; the copy has served its purpose.
    shutil.rmtree(copy, ignore_errors=True)

for path, sentences in specification["sentences"].items():
    document = WORKSPACE / path
    if not document.is_file():
        findings.append(f"{path} is absent from the workspace")
        continue
    text = normalize(document.read_text(encoding="utf-8"))
    for sentence in sentences:
        if sentence not in text:
            findings.append(f"{path} lacks the sentence: {sentence}")

print("\n".join(findings))
'''

assert _GRADE_TEMPLATE.count("__COMMAND_SECONDS__") == 1, "the grade template names the command budget once"
GRADE_SCRIPT = _GRADE_TEMPLATE.replace("__COMMAND_SECONDS__", str(GRADE_COMMAND_SECONDS))

CORRUPTION_SCRIPT = r'''#!/usr/bin/python3
"""Restore the parent form of one crate's implementation files: the tests of that crate must then fail."""

import json
import pathlib
import shutil
import sys

here = pathlib.Path(__file__).resolve().parent
workspace = pathlib.Path(sys.argv[1])
manifest = json.loads((here / "revert.json").read_text(encoding="utf-8"))
for relative in manifest["restore"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to revert")
    shutil.copy2(here / "parent" / relative, target)
for relative in manifest["remove"]:
    target = workspace / relative
    if not target.is_file():
        raise SystemExit(f"{target}: absent, so the corruption has nothing to remove")
    target.unlink()
'''


def _write(path: Path, data: bytes | str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def checks_script(packages: list[str]) -> str:
    flags = " ".join(f"-p {name}" for name in packages)
    return (
        "#!/bin/sh\n"
        "# The visible subset of the checks this task is judged on.\n"
        "set -eu\n"
        'cd "$(dirname "$0")/.."\n'
        f"cargo test {flags}\n"
        f"cargo clippy {flags} -- -D warnings\n"
        "scripts/loc.sh\n"
    )


def _protected_record(out: Path, task: Task) -> dict[str, dict[str, str]]:
    workspace = out / WORKSPACE
    record: dict[str, dict[str, str]] = {"workspace": {}, "outside": {}}
    for entry in task.protected:
        path = workspace / entry
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for file in files:
            record["workspace"][file.relative_to(workspace).as_posix()] = protocol.sha256_file(file)
    for file in _regular_files(out):
        relative = file.relative_to(out)
        if relative.parts[0] == WORKSPACE or relative == Path(GRADER) / protocol.PROTECTED_FILE:
            continue
        record["outside"][relative.as_posix()] = protocol.sha256_file(file)
    return record


@dataclass(frozen=True)
class Authored:
    task: Task
    directory: Path
    crates: tuple[str, ...]
    packages: tuple[str, ...]
    identifiers: tuple[str, ...]
    visible_tests: tuple[str, ...]
    hidden_tests: tuple[str, ...]
    sentences: dict[str, list[str]] = field(default_factory=dict)


def author(
    repo: Path, commit: str, out: Path, name: str, class_name: str, allow_traces: list[str] | None = None, keep_workspace: bool = False
) -> Authored:
    """Write the task directory for one commit; see the module docstring for what it holds.

    The workspace copy is removed once `grader/workspace.patch` records it,
    unless `keep_workspace` is set.
    """
    if class_name not in CLASS_FAMILY:
        raise ValueError(f"class {class_name!r} is not one of {', '.join(CLASS_FAMILY)}")
    if out.exists():
        raise FileExistsError(f"{out} exists; author writes a fresh task directory")
    allowed = sorted(set(allow_traces or []))
    commit = git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    parent = parent_of(repo, commit)
    diff_text = commit_diff(repo, commit)
    diffs = parse_diff(diff_text)
    if not diffs:
        raise ValueError(f"commit {commit} in {repo} changes no file")
    deleted = [diff.path for diff in diffs if diff.status == DELETED]
    if deleted:
        raise ValueError(f"commit {commit} deletes {', '.join(deleted)}; the oracle overlay cannot delete a file")
    tests, implementation = partition(diffs)
    if not tests:
        raise ValueError(f"commit {commit} touches no test file; a feature-removal task needs the commit's tests")
    crates = crates_touched(diffs)
    if not crates:
        raise ValueError(f"commit {commit} touches no crate under {CRATES}/")
    out.mkdir(parents=True)
    try:
        return _write_task(repo, commit, parent, diff_text, out, name, class_name, allowed, keep_workspace)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise


def _write_task(
    repo: Path, commit: str, parent: str, diff_text: str, out: Path, name: str, class_name: str, allowed: list[str], keep_workspace: bool
) -> Authored:
    diffs = parse_diff(diff_text)
    tests, implementation = partition(diffs)
    crates = crates_touched(diffs)
    workspace = out / WORKSPACE
    protocol.archive(repo, parent, workspace)
    for diff in tests:
        if diff.status == ADDED and (workspace / diff.path).exists():
            (workspace / diff.path).unlink()

    identifiers = added_identifiers(implementation)
    hits = traces(workspace, identifiers)
    unexplained = {name: found for name, found in hits.items() if name not in allowed}
    if unexplained:
        described = "; ".join(
            f"{name} ({len(found)} lines, e.g. {', '.join(found[:TRACE_EXAMPLES])})" for name, found in sorted(unexplained.items())
        )
        raise ValueError(f"traces of the feature remain in the workspace: {described}; pass --allow-traces to accept a name")

    packages = [package_name(workspace, crate) for crate in crates]
    sentences = specification_sentences(implementation)
    subject, body = commit_message(repo, commit)

    grader = out / GRADER
    _write(grader / ORACLE_PATCH, diff_text)
    for diff in diffs:
        _write(grader / protocol.ORACLE / WORKSPACE / diff.path, show_file(repo, commit, diff.path))
    for diff in tests:
        _write(grader / HIDDEN_TESTS / diff.path, show_file(repo, commit, diff.path))
    visible: list[str] = []
    for crate in crates:
        candidates = sorted(diff.path for diff in tests if diff.crate == crate)
        if candidates:
            visible.append(candidates[0])
            _write(workspace / candidates[0], show_file(repo, commit, candidates[0]))
    _write(workspace / CHECKS_SCRIPT, checks_script(packages), executable=True)

    corruption = grader / protocol.CORRUPTIONS / CORRUPTION_NAME
    reverted = crates[0]
    restore = [diff.path for diff in implementation if diff.crate == reverted and diff.status == MODIFIED]
    remove = [diff.path for diff in implementation if diff.crate == reverted and diff.status == ADDED]
    if not restore and not remove:
        raise ValueError(f"commit {commit} changes no implementation file in {reverted}, so no crate can be reverted")
    for path in restore:
        _write(corruption / "parent" / path, show_file(repo, parent, path))
    _write(corruption / "revert.json", json.dumps({"crate": reverted, "restore": restore, "remove": remove}, indent=2) + "\n")
    _write(corruption / "apply.py", CORRUPTION_SCRIPT, executable=True)

    hidden_test_names = {
        diff.path: test_names(show_file(repo, commit, diff.path).decode("utf-8", "replace")) for diff in tests if diff.path.endswith(".rs")
    }
    specification = {"task": name, "packages": packages, "hidden_test_names": hidden_test_names, "sentences": sentences}
    _write(grader / SPECIFICATION_FILE, json.dumps(specification, indent=2) + "\n")
    _write(grader / "grade.py", GRADE_SCRIPT, executable=True)
    _write(grader / protocol.GRADE_SCRIPT, GRADE_WRAPPER, executable=True)

    task = Task(
        name=name,
        family=CLASS_FAMILY[class_name],
        class_name=class_name,
        text=draft_text(subject, body, sentences, packages),
        correct_statuses=frozenset({COMPLETED}),
        correct_codes=frozenset(),
        budget=budget_for(len(crates)),
        protected=PROTECTED,
        metadata={
            protocol.SOURCE_KEY: protocol.source_record(repo, out, commit=commit, parent=parent, subject=subject),
            "crates": crates,
            "packages": packages,
            "crate_count": len(crates),
            "coupling": coupling(repo, commit, crates, identifiers),
            "identifiers": identifiers,
            "allowed_traces": [name for name in allowed if name in hits],
            "visible_tests": visible,
            "hidden_tests": [diff.path for diff in tests],
            "review": "pending: the text is drafted from the commit message and the document changes and has not been read by a person",
        },
    )
    protocol.save(task, out)
    protocol.record_workspace_patch(out, repo)
    _write(grader / protocol.PROTECTED_FILE, json.dumps(_protected_record(out, task), indent=2, sort_keys=True) + "\n")
    if not keep_workspace:
        shutil.rmtree(workspace)
    return Authored(task, out, tuple(crates), tuple(packages), tuple(identifiers), tuple(visible), tuple(diff.path for diff in tests), sentences)


@dataclass(frozen=True)
class TimedControl:
    control: protocol.ControlResult
    seconds: float


def grade_with_timeout(root: Path, reported: Reported, candidate: Any, arm: str, timeout: int) -> protocol.GradeResult:
    """`protocol.grade` with a timeout long enough for a cargo build.

    The protocol allows a grade script sixty seconds, which a build cannot
    meet; the damage judgement and the input object are the protocol's.
    """
    found = protocol.damage(root)
    script = root / GRADER / protocol.GRADE_SCRIPT
    payload = json.dumps({"reported": reported.to_dict(), "candidate": candidate, "arm": arm})
    # The script runs in its own process group so that a timeout ends cargo
    # and rustc with it; a lingering rustc would hold the target directory's
    # lock against the next grade of the task.
    try:
        process = subprocess.Popen(
            [str(script)], cwd=root / WORKSPACE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
        )
    except OSError as error:
        return protocol.GradeResult(False, [f"the grade script {script} failed: {error}"], found)
    try:
        stdout, stderr = process.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return protocol.GradeResult(False, [f"the grade script {script} failed: it ran past {timeout}s and its process group was killed"], found)
    findings = [line for line in stdout.splitlines() if line.strip()]
    if process.returncode != 0:
        findings.append(f"the grade script {script} exited {process.returncode}: {stderr.strip()}")
    return protocol.GradeResult(not findings and process.returncode == 0, findings, found)


def verify(task_dir: Path, scratch: Path, timeout: int = GRADE_TIMEOUT_SECONDS) -> list[TimedControl]:
    """The controls of `protocol.check_grader_controls`, timed, with a build-length timeout."""
    results: list[TimedControl] = []

    def run(name: str, expected_pass: bool, prepare: Any) -> None:
        root = scratch / name
        if root.exists():
            shutil.rmtree(root)
        protocol.materialize(task_dir, root)
        reported, candidate = prepare(root)
        started = time.monotonic()
        result = grade_with_timeout(root, reported, candidate, f"control:{name}", timeout)
        elapsed = time.monotonic() - started
        findings = list(result.findings) + [f"damage: {path}" for path in result.damage]
        results.append(TimedControl(protocol.ControlResult(name, expected_pass, result.passed and not result.damage, findings), elapsed))

    run("untouched", False, lambda root: (Reported(COMPLETED, None, "untouched fixture"), None))
    run("oracle", True, protocol.apply_oracle)
    for corruption in protocol.corruptions(task_dir):

        def corrupted(root: Path, corruption: Path = corruption) -> tuple[Reported, Any]:
            reported, candidate = protocol.apply_oracle(root)
            protocol.apply_corruption(corruption, root / WORKSPACE)
            return reported, candidate

        run(f"corruption:{corruption.name}", False, corrupted)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    authoring = commands.add_parser("author", help="write a task directory from one commit")
    authoring.add_argument("--repo", required=True, type=Path, help="the repository the commit lives in")
    authoring.add_argument("--commit", required=True, help="the feature commit")
    authoring.add_argument("--out", required=True, type=Path, help="the task directory to create")
    authoring.add_argument("--name", required=True, help="the task's name")
    authoring.add_argument("--class", dest="class_name", default="solvable", choices=sorted(CLASS_FAMILY), help="the task's class")
    authoring.add_argument("--allow-traces", nargs="*", default=[], metavar="IDENTIFIER", help="added identifiers whose hits in the workspace are accepted")
    authoring.add_argument("--keep-workspace", action="store_true", help="keep the workspace copy beside the recipe, for inspection")
    verifying = commands.add_parser("verify", help="run the grader controls with a build-length timeout")
    verifying.add_argument("--task", required=True, type=Path, help="the task directory")
    verifying.add_argument("--scratch", required=True, type=Path, help="where each control's root is materialized")
    verifying.add_argument("--timeout", type=int, default=GRADE_TIMEOUT_SECONDS, help="seconds one grade may take")
    args = parser.parse_args(argv)

    if args.command == "author":
        try:
            authored = author(args.repo.resolve(), args.commit, args.out.resolve(), args.name, args.class_name, args.allow_traces, args.keep_workspace)
        except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as error:
            print(f"feature removal: {error}", file=sys.stderr)
            return 2
        metadata = authored.task.metadata
        print(f"task {authored.task.name} written to {authored.directory}")
        print(f"crates: {', '.join(authored.crates)} (count={metadata['crate_count']}, coupling={metadata['coupling']})")
        print(f"identifiers: {', '.join(authored.identifiers) or 'none'}")
        print(f"visible tests: {', '.join(authored.visible_tests)}")
        print(f"hidden tests: {', '.join(authored.hidden_tests)}")
        print(f"specification sentences: {sum(len(found) for found in authored.sentences.values())}")
        return 0

    started = time.monotonic()
    try:
        results = verify(args.task.resolve(), args.scratch.resolve(), args.timeout)
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as error:
        print(f"feature removal: {error}", file=sys.stderr)
        return 2
    held = True
    for timed in results:
        control = timed.control
        verdict = "held" if control.held else "FAILED"
        expected = "pass" if control.expected_pass else "fail"
        observed = "passed" if control.passed else "failed"
        print(f"{control.name}: {verdict} (expected to {expected}, {observed}) in {timed.seconds:.0f}s")
        for finding in control.findings:
            print(f"  {finding[:400]}")
        held = held and control.held
    print(f"wall time: {time.monotonic() - started:.0f}s")
    return 0 if held else 1


if __name__ == "__main__":
    sys.exit(main())
