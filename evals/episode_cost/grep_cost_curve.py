#!/usr/bin/python3
"""Measure what the built-in grep tool costs, with no model and no network.

Every measurement here runs the real tool inside a real episode. The model
route is a host function that returns a scripted tool call, so the search
path, the ignore rules, the collection bounds, and the rendering are the
ones a live episode uses, and the durations come from the `duration_ms`
field of the `tool/result` events the episode wrote.

Two corpora: the working tree this script is run against, and a tree this
script generates from a seed so that anyone can reproduce it. Two page-cache
conditions: warm, where an earlier query has already read the files, and
cold, where the harness releases the corpus from the page cache before each
query. Cold eviction uses `posix_fadvise` on each file, which needs no
privilege and drops file contents while leaving directory metadata cached.

The report gives, per query, the median and 95th-percentile duration and the
files the tool streamed, and, over a fixed query sequence, the cumulative
seconds an episode spends in search after 1, 2, 5, and 20 queries. The
cumulative figure is the denominator any index has to beat: an index pays
for itself only when it removes more episode time than it adds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import host_runtime  # noqa: E402

from log_facts import median, percentile, spilled_value  # noqa: E402

# The working tree this file belongs to, used when no other is named.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent

# Query classes measured on both corpora. Every case names the argument
# object the model would send. `expect` records what the case is for, so a
# corpus change that turns a rare pattern into a frequent one is visible in
# the report rather than silently changing what was measured.
GENERATED_CASES: list[dict[str, Any]] = [
    {"name": "absent-literal", "expect": "no match anywhere", "args": {"pattern": "zzz_absent_token_zzz", "literal": True}},
    {"name": "rare-literal", "expect": "one file", "args": {"pattern": "needle_rare_marker", "literal": True}},
    {"name": "frequent-literal", "expect": "about one file in ten", "args": {"pattern": "needle_frequent_marker", "literal": True}},
    {"name": "unicode-literal", "expect": "about one file in fifty", "args": {"pattern": "néedle_ünicode_marker", "literal": True}},
    {"name": "anchored-regex", "expect": "about one line per file", "args": {"pattern": "^def handler_0\\(state\\)"}},
    {"name": "collection-bound", "expect": "stops at the 10,000-match bound", "args": {"pattern": "^def handler_[0-9]+"}},
    {"name": "alternation-regex", "expect": "three markers in one call", "args": {"pattern": "needle_rare_marker|needle_alpha_marker|needle_beta_marker"}},
    {"name": "batch-member-rare", "expect": "one third of the alternation", "args": {"pattern": "needle_rare_marker", "literal": True}},
    {"name": "batch-member-alpha", "expect": "one third of the alternation", "args": {"pattern": "needle_alpha_marker", "literal": True}},
    {"name": "batch-member-beta", "expect": "one third of the alternation", "args": {"pattern": "needle_beta_marker", "literal": True}},
    {"name": "glob-restricted", "expect": "one file extension of three", "args": {"pattern": "needle_frequent_marker", "literal": True, "glob": "*.py"}},
    {"name": "path-restricted", "expect": "one subtree of the corpus", "args": {"pattern": "needle_frequent_marker", "literal": True, "path": "part_00"}},
    {"name": "case-insensitive", "expect": "the frequent marker, folded", "args": {"pattern": "NEEDLE_FREQUENT_MARKER", "literal": True, "ignore_case": True}},
]

REPOSITORY_CASES: list[dict[str, Any]] = [
    {"name": "absent-literal", "expect": "no match anywhere", "args": {"pattern": "zzz_absent_token_zzz", "literal": True}},
    {"name": "rare-literal", "expect": "few matches", "args": {"pattern": "GREP_HIT_COLLECT_MAX", "literal": True}},
    {"name": "frequent-literal", "expect": "many matches", "args": {"pattern": "let", "literal": True}},
    {"name": "unicode-literal", "expect": "typographic characters in prose", "args": {"pattern": "—", "literal": True}},
    {"name": "anchored-regex", "expect": "Rust function definitions", "args": {"pattern": "^pub fn [a-z_]+"}},
    {"name": "collection-bound", "expect": "every non-empty line", "args": {"pattern": "."}},
    {"name": "alternation-regex", "expect": "three tool names in one call", "args": {"pattern": "GREP_COLLECT_MAX|GREP_DEFAULT_LIMIT|GREP_LINE_MAX_CHARS"}},
    {"name": "batch-member-collect", "expect": "one third of the alternation", "args": {"pattern": "GREP_COLLECT_MAX", "literal": True}},
    {"name": "batch-member-default", "expect": "one third of the alternation", "args": {"pattern": "GREP_DEFAULT_LIMIT", "literal": True}},
    {"name": "batch-member-line", "expect": "one third of the alternation", "args": {"pattern": "GREP_LINE_MAX_CHARS", "literal": True}},
    {"name": "glob-restricted", "expect": "Rust sources only", "args": {"pattern": "^pub fn [a-z_]+", "glob": "*.rs"}},
    {"name": "path-restricted", "expect": "one crate", "args": {"pattern": "^pub fn [a-z_]+", "path": "crates/code"}},
    {"name": "case-insensitive", "expect": "the rare marker, folded", "args": {"pattern": "grep_hit_collect_max", "literal": True, "ignore_case": True}},
]

# The sequence whose cumulative cost the report gives. It is fixed rather
# than random so that two runs of this script measure the same thing, and it
# repeats cases the way an episode repeats searches.
SEQUENCE = [
    "frequent-literal", "rare-literal", "anchored-regex", "absent-literal", "frequent-literal",
    "glob-restricted", "rare-literal", "alternation-regex", "path-restricted", "frequent-literal",
    "case-insensitive", "anchored-regex", "unicode-literal", "rare-literal", "absent-literal",
    "glob-restricted", "frequent-literal", "anchored-regex", "path-restricted", "rare-literal",
]

CUMULATIVE_POINTS = (1, 2, 5, 20)

# Sizes the generated corpus draws file bodies from, in lines. Fixed so that
# the corpus digest depends only on the seed and the file count.
BODY_LINES = (12, 40, 120, 400)


def generate_corpus(root: Path, files: int, seed: int) -> tuple[dict[str, Any], list[Path]]:
    """Write a deterministic tree, and return what identifies it and its files.

    The tree has three file extensions, a hundred directories, and three
    planted markers at fixed frequencies, so a query class means the same
    thing on every machine that generates the corpus from one seed.
    """
    rng = random.Random(seed)
    manifest = hashlib.sha256()
    total_bytes = 0
    written = []
    for index in range(files):
        part = index % 100
        extension = (".py", ".rs", ".md")[index % 3]
        directory = root / f"part_{part:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"file_{index:06d}{extension}"
        lines = []
        for line in range(rng.choice(BODY_LINES)):
            lines.append(f"def handler_{line}(state):" if line % 7 == 0 else f"    value_{line} = compute({line}, state)")
        if index % 10 == 0:
            lines.insert(len(lines) // 2, "    tag = 'needle_frequent_marker'")
        if index % 50 == 0:
            lines.insert(1, "    label = 'néedle_ünicode_marker'")
        if index == files // 2:
            lines.insert(0, "    marker = 'needle_rare_marker'")
        if index == files // 3:
            lines.insert(0, "    marker = 'needle_alpha_marker'")
        if index == (2 * files) // 3:
            lines.insert(0, "    marker = 'needle_beta_marker'")
        body = ("\n".join(lines) + "\n").encode("utf-8")
        path.write_bytes(body)
        total_bytes += len(body)
        written.append(path)
        manifest.update(f"{path.relative_to(root)}\0{len(body)}\0".encode("utf-8"))
        manifest.update(hashlib.sha256(body).digest())
    return {
        "kind": "generated",
        "root": str(root),
        "seed": seed,
        "files": files,
        "bytes": total_bytes,
        "manifest_sha256": manifest.hexdigest(),
        "filesystem": filesystem_of(root),
    }, written


def repository_corpus(root: Path) -> tuple[dict[str, Any], list[Path]]:
    """Identify a working tree and measure the set grep would stream.

    The tool skips every path whose name begins with a dot and honors
    `.gitignore`, so the set reconstructed here is the tracked and untracked
    files git does not ignore, less any with a dotted path component. The
    report states this count beside the count the tool itself recorded, so a
    disagreement is visible rather than assumed away.
    """
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    candidates = [root / name for name in listed if not any(part.startswith(".") for part in Path(name).parts)]
    paths = [path for path in candidates if path.is_file() and not path.is_symlink()]
    total = sum(path.stat().st_size for path in paths)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "kind": "repository",
        "root": str(root),
        "revision": revision,
        "clean": not dirty,
        "files": len(paths),
        "bytes": total,
        "filesystem": filesystem_of(root),
    }, paths


def filesystem_of(path: Path) -> str:
    """The type of the filesystem holding `path`, from `/proc/mounts`.

    A cold-cache measurement is meaningless on a memory-backed filesystem:
    `tmpfs` pages are the file, so releasing them is not possible and a
    cold run measures the same thing as a warm one. Recording the type puts
    that condition in the report rather than leaving it to be guessed.
    """
    resolved = path.resolve()
    best = ("", "unknown")
    for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1]
        if (resolved == Path(point) or point in ("/", *map(str, resolved.parents))) and len(point) >= len(best[0]):
            best = (point, fields[2])
    return best[1]


def evict(paths: list[Path]) -> int:
    """Release each file's contents from the page cache.

    `POSIX_FADV_DONTNEED` drops clean pages and needs no privilege. It does
    not drop directory entries or inodes, so a cold measurement taken this
    way still finds the tree's metadata cached, and it understates what a
    fully cold search costs.
    """
    released = 0
    for path in paths:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
            released += 1
        except OSError:
            pass
        finally:
            os.close(descriptor)
    return released


def config_for(name: str, root: Path, calls: int) -> dict[str, Any]:
    return {
        "version": 4,
        "name": name,
        "instructions": {"role": "Issue the scripted searches and stop."},
        "tools": ["grep"],
        "grants": {"read": [str(root)]},
        # Repeating one query is the point of the measurement, so the
        # consecutive-identical-call limit that would end an ordinary
        # episode as blocked is lifted past the number of calls scripted.
        "budget": {"model_calls": calls + 2, "seconds": 3600, "loop_threshold": calls + 2},
        "sandbox": {"mode": "off"},
        "task": "Run the scripted grep measurements.",
    }


def responder(plan: list[dict[str, Any]], before_each: Callable[[], None]) -> Callable[[dict], list[dict]]:
    """A model backend that issues one scripted grep call per request."""
    issued = {"index": 0}

    def respond(_request: dict) -> list[dict]:
        index = issued["index"]
        if index >= len(plan):
            return [{"kind": "text", "delta": "done"}, {"kind": "done", "stop": "end", "usage": {"input": 0, "output": 0, "cache_read": 0}}]
        issued["index"] = index + 1
        before_each()
        entry = plan[index]
        args = json.dumps(entry["args"])
        return [
            {"kind": "tool_call_start", "id": entry["call_id"], "name": "grep"},
            {"kind": "tool_call_delta", "id": entry["call_id"], "delta": args},
            {"kind": "tool_call_end", "id": entry["call_id"]},
            {"kind": "done", "stop": "tool", "usage": {"input": 0, "output": 0, "cache_read": 0}},
        ]

    return respond


def observed(log_dir: Path) -> dict[str, dict[str, Any]]:
    """Each scripted call's recorded duration and result facts, by call id."""
    results = {}
    for line in (log_dir / "episode.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") != "tool/result":
            continue
        data = event["data"]
        value = spilled_value(log_dir, data)
        results[data["call_id"]] = {
            "duration_ms": int(data.get("duration_ms", 0) or 0),
            "is_error": bool(data.get("is_error")),
            "matches": int(value.get("matches", 0) or 0),
            "files": int(value.get("files", 0) or 0),
            "searched_files": int(value.get("searched_files", 0) or 0),
            "complete": value.get("complete"),
            "rendered_chars": len(data.get("rendered") or ""),
        }
    return results


def run_episode(
    binary: Path,
    corpus: dict[str, Any],
    corpus_files: list[Path],
    plan: list[dict[str, Any]],
    cold: bool,
    work: Path,
    tag: str,
) -> tuple[dict[str, dict[str, Any]], Path]:
    """Run one episode that issues `plan` in order and return its results.

    One episode per query class keeps every request's message list short.
    The list grows with each result, and the log records it in full on every
    request, so a single episode holding every call would spend more time
    writing its own log than searching.
    """
    root = Path(corpus["root"])
    # One discarded call first. Without it the first measured call of a warm
    # episode would be the one that brought the corpus into the page cache,
    # and its duration would belong to the cold condition.
    plan = [dict(plan[0], call_id="warm-up")] + plan
    config_path = work / f"{tag}.json"
    config_path.write_text(json.dumps(config_for(tag, root, len(plan))), encoding="utf-8")
    log_parent = work / f"{tag}-logs"
    log_parent.mkdir(parents=True, exist_ok=True)
    status, log_dir = host_runtime.run(
        binary,
        config_path,
        log_parent,
        responder(plan, (lambda: evict(corpus_files)) if cold else (lambda: None)),
    )
    if status != 0:
        raise RuntimeError(f"grep_cost_curve: episode {log_dir} ended with status {status}")
    return observed(log_dir), log_dir


def measure(
    binary: Path,
    corpus: dict[str, Any],
    corpus_files: list[Path],
    cases: list[dict[str, Any]],
    repeats: int,
    cold: bool,
    work: Path,
) -> dict[str, Any]:
    """Run every case `repeats` times, then one fixed sequence, and fold."""
    condition = "cold" if cold else "warm"
    if cold:
        # `POSIX_FADV_DONTNEED` releases only clean pages. A corpus this
        # script has just written is still dirty until writeback, so the
        # eviction would do nothing without this.
        os.sync()
    prefix = f"grep-{corpus['kind']}-{condition}"
    by_case: dict[str, list[dict[str, Any]]] = {}
    logs = []
    calls = 0
    for case in cases:
        plan = [
            {"call_id": f"{case['name']}-{repeat}", "case": case["name"], "args": case["args"]}
            for repeat in range(repeats)
        ]
        results, log_dir = run_episode(
            binary, corpus, corpus_files, plan, cold, work, f"{prefix}-{case['name']}"
        )
        logs.append(str(log_dir))
        calls += len(plan)
        by_case[case["name"]] = [results[entry["call_id"]] for entry in plan]
    sequence_plan = []
    for step, name in enumerate(SEQUENCE):
        case = next(entry for entry in cases if entry["name"] == name)
        sequence_plan.append({"call_id": f"sequence-{step:02d}", "case": name, "args": case["args"]})
    sequence_results, sequence_log = run_episode(
        binary, corpus, corpus_files, sequence_plan, cold, work, f"{prefix}-sequence"
    )
    logs.append(str(sequence_log))
    calls += len(sequence_plan)
    cases_report = []
    for case in cases:
        samples = by_case.get(case["name"], [])
        durations = [sample["duration_ms"] for sample in samples]
        first = samples[0] if samples else {}
        middle = median(durations)
        # Throughput is stated only for a call that streamed the whole
        # corpus and stopped at no bound, because only then is the number
        # of bytes it read exactly the corpus size.
        whole = first.get("complete") is True and first.get("searched_files") == corpus["files"]
        throughput = (corpus["bytes"] / middle / 1000.0) if whole and middle else None
        cases_report.append(
            {
                "case": case["name"],
                "expect": case["expect"],
                "args": case["args"],
                "repeats": len(samples),
                "median_ms": middle,
                "megabytes_per_second": throughput,
                "p95_ms": percentile(durations, 0.95),
                "min_ms": min(durations) if durations else 0,
                "max_ms": max(durations) if durations else 0,
                "matches": first.get("matches", 0),
                "match_files": first.get("files", 0),
                "searched_files": first.get("searched_files", 0),
                "complete": first.get("complete"),
                "rendered_chars": first.get("rendered_chars", 0),
                "is_error": first.get("is_error", False),
            }
        )
    sequence = [sequence_results[f"sequence-{step:02d}"]["duration_ms"] for step in range(len(SEQUENCE))]
    cumulative = {str(point): sum(sequence[:point]) for point in CUMULATIVE_POINTS if point <= len(sequence)}
    return {
        "corpus": corpus,
        "cache": condition,
        "logs": logs,
        "grep_calls": calls,
        "cases": cases_report,
        "sequence_ms": sequence,
        "cumulative_ms": cumulative,
    }


def render(report: dict[str, Any]) -> str:
    lines = []
    for run in report["runs"]:
        corpus = run["corpus"]
        identity = (
            f"revision {corpus['revision']}, clean {corpus['clean']}"
            if corpus["kind"] == "repository"
            else f"seed {corpus['seed']}, manifest sha256 {corpus['manifest_sha256'][:16]}"
        )
        lines += [
            f"corpus {corpus['kind']}: {corpus['files']} files, {corpus['bytes']} bytes,"
            f" on {corpus['filesystem']}, {identity}",
            f"  page cache {run['cache']}, {run['grep_calls']} grep calls across {len(run['logs'])} episodes",
            "  case                   median ms   p95 ms   min   max   matches   files streamed"
            "   rendered   complete   MB/s",
        ]
        for case in run["cases"]:
            rate = f"{case['megabytes_per_second']:6.1f}" if case["megabytes_per_second"] else "     -"
            lines.append(
                f"  {case['case']:20s}  {case['median_ms']:9.0f}  {case['p95_ms']:7.0f}"
                f"  {case['min_ms']:4d}  {case['max_ms']:4d}  {case['matches']:8d}"
                f"  {case['searched_files']:14d}  {case['rendered_chars']:8d}"
                f"   {str(case['complete']):8s}  {rate}"
            )
        points = ", ".join(f"{count} queries {ms / 1000.0:.3f} s" for count, ms in run["cumulative_ms"].items())
        lines.append(f"  fixed 20-query sequence, cumulative time in search: {points}")
        if run["cache"] == "cold" and corpus["filesystem"] == "tmpfs":
            lines.append(
                "  this corpus is on a memory-backed filesystem, so its pages cannot be released;"
                " the cold row measures the warm condition"
            )
        batch = {case["case"]: case["median_ms"] for case in run["cases"]}
        members = [name for name in batch if name.startswith("batch-member-")]
        if "alternation-regex" in batch and len(members) == 3:
            separate = sum(batch[name] for name in members)
            lines.append(
                f"  one alternation call {batch['alternation-regex']:.0f} ms against"
                f" {separate:.0f} ms for the same three patterns searched separately"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foe", required=True, type=Path, help="the foe binary to run the episodes with")
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT, help="working tree to search")
    parser.add_argument("--generated-files", type=int, default=10000, help="files in the generated corpus")
    parser.add_argument("--seed", type=int, default=1, help="generated corpus seed")
    parser.add_argument("--repeats", type=int, default=5, help="repetitions of each query class")
    parser.add_argument("--keep", type=Path, help="retain configurations, corpus, and logs under this directory")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPOSITORY_ROOT / "target",
        help="where the generated corpus and the episode logs are written; must be on a disk-backed"
        " filesystem for the cold-cache condition to differ from the warm one",
    )
    parser.add_argument("--json", type=Path, help="write the full report as JSON to this path")
    args = parser.parse_args(argv)
    if not os.access(args.foe, os.X_OK):
        print(f"grep_cost_curve: {args.foe} is not executable", file=sys.stderr)
        return 2
    args.work_dir.mkdir(parents=True, exist_ok=True)
    holder = (
        tempfile.TemporaryDirectory(prefix="grep-cost-curve.", dir=args.work_dir) if args.keep is None else None
    )
    work = Path(holder.name) if holder else args.keep
    work.mkdir(parents=True, exist_ok=True)
    try:
        generated_root = work / "generated"
        generated_root.mkdir(parents=True, exist_ok=True)
        repository, repository_paths = repository_corpus(args.repository.resolve())
        generated, generated_paths = generate_corpus(generated_root, args.generated_files, args.seed)
        corpora = [
            (repository, repository_paths, REPOSITORY_CASES),
            (generated, generated_paths, GENERATED_CASES),
        ]
        runs = []
        for corpus, paths, cases in corpora:
            for cold in (False, True):
                runs.append(measure(args.foe.resolve(), corpus, paths, cases, args.repeats, cold, work))
        report = {"runs": runs, "repeats": args.repeats, "sequence": SEQUENCE}
        if args.json:
            args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(render(report))
    finally:
        if holder:
            holder.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
