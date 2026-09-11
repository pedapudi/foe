#!/usr/bin/python3
"""Report what repository search cost in a directory of recorded episodes.

The report answers three questions from the logs alone, with no model call
and no re-execution of any tool.

Cost: how many search calls an episode makes, how much wall clock they
take, and what share of the episode's tool time that is.

Repetition: how often one episode, and how often the episodes of one run,
issue a search that an earlier search in the same scope already answered.
An index can reuse file inventory and content postings across distinct queries.
Repeated arguments alone do not establish that file contents stayed unchanged.

Follow-through: two behavioural proxies for result quality. Both are weak
and the report labels them so. A search followed by a search whose pattern
contains it or is contained by it suggests the first did not answer the
question. A search none of whose matched paths is named by any later call
suggests the result went unused.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from log_facts import (
    SEARCH_TOOLS,
    Episode,
    is_refinement,
    load_episodes,
    median,
    normalized_grep,
    pattern_shape,
    percentile,
    rendered_sizes,
    run_of,
    search_command_heads,
)

# How many model steps later a second search still counts as a refinement
# of the first. Beyond a few steps the episode has done other work and the
# relation between the two patterns is no longer evidence about the first.
REFINEMENT_WINDOW_STEPS = 3


def search_calls(episode: Episode) -> list[dict[str, Any]]:
    """Every call in one episode that traverses the repository.

    A `bash` call counts when its command line names a search program. Its
    duration includes the whole command line, so it is reported apart from
    the in-process tools rather than added to them.
    """
    found = []
    for call in episode.calls:
        if call["synthetic"]:
            continue
        if call["name"] in SEARCH_TOOLS:
            found.append(dict(call, kind=call["name"]))
        elif call["name"] == "bash":
            heads = search_command_heads(str(call["args"].get("command", "")))
            if heads:
                found.append(dict(call, kind="bash-search", commands=heads))
    return found


def grep_repeats(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Identical and near-identical grep calls in one ordered sequence.

    A call is an identical repeat when every argument that affects its
    result matches an earlier call. It is a near repeat when the pattern
    matches an earlier pattern but the scope, glob, or flags differ, so the
    two traverse overlapping trees for the same string.
    """
    seen_identity: set[str] = set()
    seen_pattern: set[str] = set()
    identical = 0
    near = 0
    for call in calls:
        identity = normalized_grep(call["args"])
        pattern = str(call["args"].get("pattern", ""))
        if identity in seen_identity:
            identical += 1
        elif pattern in seen_pattern:
            near += 1
        seen_identity.add(identity)
        seen_pattern.add(pattern)
    return {"calls": len(calls), "identical_repeats": identical, "near_repeats": near}


def read_repeats(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Files read more than once in one ordered sequence of `read` calls.

    Two counts, because they mean different things. A repeated path with a
    different window may request additional content. The same window may
    contain edits. Reuse requires content identity and access checks as well
    as matching arguments.
    """
    seen_paths: set[str] = set()
    seen_windows: set[str] = set()
    repeats = 0
    identical = 0
    repeated_paths: set[str] = set()
    for call in calls:
        path = str(call["args"].get("path", ""))
        window = json.dumps(call["args"], sort_keys=True)
        if path in seen_paths:
            repeats += 1
            repeated_paths.add(path)
        if window in seen_windows:
            identical += 1
        seen_paths.add(path)
        seen_windows.add(window)
    return {
        "calls": len(calls),
        "repeat_calls": repeats,
        "identical_calls": identical,
        "distinct_paths": len(seen_paths),
        "repeated_paths": sorted(repeated_paths),
    }


def refinement_proxy(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Grep calls followed soon after by a containing or contained pattern.

    Weak proxy for a result that did not answer the question. It cannot
    distinguish a deliberate narrowing from a failed first attempt.
    """
    refined = 0
    pairs = []
    for index, call in enumerate(calls):
        pattern = str(call["args"].get("pattern", ""))
        for later in calls[index + 1 :]:
            if later["step"] - call["step"] > REFINEMENT_WINDOW_STEPS:
                break
            later_pattern = str(later["args"].get("pattern", ""))
            if is_refinement(pattern, later_pattern):
                refined += 1
                pairs.append({"seq": call["seq"], "pattern": pattern, "then": later_pattern})
                break
    return {"grep_calls": len(calls), "followed_by_refinement": refined, "pairs": pairs}


def follow_through_proxy(episode: Episode, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Grep calls none of whose matched paths a later call names.

    Weak proxy for a result the episode did not use. The model can act on a
    search by reading nothing, and a path can reach a later call by a route
    this check does not see, so the count is an upper bound on unused
    results rather than a measurement of them.
    """
    unused = 0
    with_matches = 0
    for call in calls:
        hits = call["value"].get("hits") or []
        paths = {str(hit.get("path", "")) for hit in hits if isinstance(hit, dict)}
        paths = {path for path in paths if path}
        if not paths:
            continue
        with_matches += 1
        later_text = json.dumps(
            [other["args"] for other in episode.calls if other["seq"] > call["seq"]],
            sort_keys=True,
        )
        if not any(path in later_text or Path(path).name in later_text for path in paths):
            unused += 1
    return {"grep_calls_with_matches": with_matches, "no_named_path_used_later": unused}


def episode_report(episode: Episode, root: Path) -> dict[str, Any]:
    calls = search_calls(episode)
    grep = [call for call in calls if call["kind"] == "grep"]
    read = [call for call in calls if call["kind"] == "read"]
    shell = [call for call in calls if call["kind"] == "bash-search"]
    tool_ms = sum(call["duration_ms"] for call in episode.calls if not call["synthetic"])
    return {
        "log": str(episode.path),
        "run": run_of(episode.path, root),
        "episode_id": episode.id,
        "parent_id": episode.parent_id,
        "outcome": episode.outcome.get("kind", "unfinished"),
        "model_calls": episode.model_calls,
        "tool_calls": len([call for call in episode.calls if not call["synthetic"]]),
        "tool_ms": tool_ms,
        "episode_wall_ms": episode.wall_ms,
        "grep": {
            "calls": len(grep),
            "ms": sum(call["duration_ms"] for call in grep),
            "rendered_chars": sum(call["rendered_chars"] for call in grep),
            "matches": sum(int(call["value"].get("matches", 0) or 0) for call in grep),
            "searched_files": sum(int(call["value"].get("searched_files", 0) or 0) for call in grep),
            "incomplete": sum(1 for call in grep if call["value"].get("complete") is False),
            **grep_repeats(grep),
        },
        "read": {
            "calls": len(read),
            "ms": sum(call["duration_ms"] for call in read),
            "rendered_chars": sum(call["rendered_chars"] for call in read),
            **read_repeats(read),
            "paths": [str(call["args"].get("path", "")) for call in read],
            "windows": [json.dumps(call["args"], sort_keys=True) for call in read],
        },
        "bash_search": {
            "calls": len(shell),
            "ms": sum(call["duration_ms"] for call in shell),
            "commands": sorted(collections.Counter(head for call in shell for head in call["commands"]).items()),
        },
        "refinement_proxy": refinement_proxy(grep),
        "follow_through_proxy": follow_through_proxy(episode, grep),
        "grep_shapes": [pattern_shape(call["args"]) for call in grep],
    }


def run_reports(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repetition measured over one run rather than one episode.

    A workflow run is several episodes with fresh context over one working
    directory. A pattern one episode already searched and another searches
    again is work a cache that outlived an episode could remove, and no
    per-episode count sees it.
    """
    by_run: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for report in episodes:
        by_run[report["run"]].append(report)
    reports = []
    for run, members in sorted(by_run.items()):
        identities: collections.Counter[str] = collections.Counter()
        patterns: collections.Counter[str] = collections.Counter()
        paths: collections.Counter[str] = collections.Counter()
        for member in members:
            for shape in member["grep_shapes"]:
                fields = ("pattern", "scope", "glob", "ignore_case", "declared_literal", "context")
                identities["\x1f".join(str(shape[field]) for field in fields)] += 1
                patterns[shape["pattern"]] += 1
        windows: collections.Counter[str] = collections.Counter()
        for member in members:
            for path in member["read"]["paths"]:
                paths[path] += 1
            for window in member["read"]["windows"]:
                windows[window] += 1
        grep_calls = sum(member["grep"]["calls"] for member in members)
        read_calls = sum(member["read"]["calls"] for member in members)
        reports.append(
            {
                "run": run,
                "episodes": len(members),
                "grep_calls": grep_calls,
                "distinct_grep_identities": len(identities),
                "repeat_grep_calls": grep_calls - len(identities),
                "distinct_grep_patterns": len(patterns),
                "repeat_pattern_calls": grep_calls - len(patterns),
                "read_calls": read_calls,
                "distinct_read_paths": len(paths),
                "repeat_read_calls": read_calls - len(paths),
                "identical_read_calls": read_calls - len(windows),
                "grep_ms": sum(member["grep"]["ms"] for member in members),
                "read_ms": sum(member["read"]["ms"] for member in members),
                "bash_search_calls": sum(member["bash_search"]["calls"] for member in members),
                "bash_search_ms": sum(member["bash_search"]["ms"] for member in members),
            }
        )
    return reports


def corpus_report(root: Path) -> dict[str, Any]:
    episodes = load_episodes(root)
    reports = [episode_report(episode, root) for episode in episodes]
    per_call_ms = []
    for episode in episodes:
        per_call_ms.extend(call["duration_ms"] for call in episode.tool_calls("grep"))
    read_ms = []
    for episode in episodes:
        read_ms.extend(call["duration_ms"] for call in episode.tool_calls("read"))
    shapes = [shape for report in reports for shape in report["grep_shapes"]]
    # What one call renders, by tool, over every episode in the corpus. A
    # search that returns more candidates costs the episode here, on the step
    # that ran it and on every later step, whatever it saved in milliseconds.
    by_tool: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for episode in episodes:
        for call in episode.calls:
            if not call["synthetic"]:
                by_tool[call["name"] or "unnamed"].append(call)
    rendered_per_call = {tool: rendered_sizes(calls) for tool, calls in sorted(by_tool.items())}
    totals = {
        "episodes": len(reports),
        "episodes_with_grep": sum(1 for report in reports if report["grep"]["calls"]),
        "tool_calls": sum(report["tool_calls"] for report in reports),
        "grep_calls": sum(report["grep"]["calls"] for report in reports),
        "read_calls": sum(report["read"]["calls"] for report in reports),
        "bash_search_calls": sum(report["bash_search"]["calls"] for report in reports),
        "tool_ms": sum(report["tool_ms"] for report in reports),
        "grep_ms": sum(report["grep"]["ms"] for report in reports),
        "read_ms": sum(report["read"]["ms"] for report in reports),
        "bash_search_ms": sum(report["bash_search"]["ms"] for report in reports),
        "grep_rendered_chars": sum(report["grep"]["rendered_chars"] for report in reports),
        "read_rendered_chars": sum(report["read"]["rendered_chars"] for report in reports),
        "grep_ms_median": median(per_call_ms),
        "grep_ms_p95": percentile(per_call_ms, 0.95),
        "grep_ms_max": max(per_call_ms) if per_call_ms else 0,
        "read_ms_median": median(read_ms),
        "read_ms_p95": percentile(read_ms, 0.95),
        "identical_grep_repeats": sum(report["grep"]["identical_repeats"] for report in reports),
        "near_grep_repeats": sum(report["grep"]["near_repeats"] for report in reports),
        "read_repeat_calls": sum(report["read"]["repeat_calls"] for report in reports),
        "read_identical_calls": sum(report["read"]["identical_calls"] for report in reports),
        "grep_followed_by_refinement": sum(report["refinement_proxy"]["followed_by_refinement"] for report in reports),
        "grep_with_matches": sum(report["follow_through_proxy"]["grep_calls_with_matches"] for report in reports),
        "grep_no_named_path_used_later": sum(
            report["follow_through_proxy"]["no_named_path_used_later"] for report in reports
        ),
    }
    shape_counts = {
        "literal": sum(1 for shape in shapes if shape["literal"]),
        "regex": sum(1 for shape in shapes if not shape["literal"]),
        "declared_literal": sum(1 for shape in shapes if shape["declared_literal"]),
        "ignore_case": sum(1 for shape in shapes if shape["ignore_case"]),
        "path_restricted": sum(1 for shape in shapes if shape["path_restricted"]),
        "glob_restricted": sum(1 for shape in shapes if shape["glob_restricted"]),
        "with_context": sum(1 for shape in shapes if shape["context"]),
        "pattern_length_median": median(shape["length"] for shape in shapes),
        "pattern_length_max": max((shape["length"] for shape in shapes), default=0),
    }
    return {
        "corpus_root": str(root),
        "totals": totals,
        "rendered_per_call": rendered_per_call,
        "grep_pattern_shapes": shape_counts,
        "runs": run_reports(reports),
        "episodes": reports,
    }


def share(part: float, whole: float) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def render(report: dict[str, Any]) -> str:
    totals = report["totals"]
    shapes = report["grep_pattern_shapes"]
    lines = [
        f"corpus {report['corpus_root']}",
        f"  {totals['episodes']} episode logs, {totals['tool_calls']} non-synthetic tool results,"
        f" {totals['tool_ms']} ms of tool time",
        "",
        "search cost",
        f"  grep         {totals['grep_calls']:5d} calls  {totals['grep_ms']:7d} ms"
        f"  ({share(totals['grep_ms'], totals['tool_ms'])} of tool time)"
        f"  median {totals['grep_ms_median']:.0f} ms, p95 {totals['grep_ms_p95']:.0f} ms,"
        f" max {totals['grep_ms_max']} ms",
        f"  read         {totals['read_calls']:5d} calls  {totals['read_ms']:7d} ms"
        f"  ({share(totals['read_ms'], totals['tool_ms'])} of tool time)"
        f"  median {totals['read_ms_median']:.0f} ms, p95 {totals['read_ms_p95']:.0f} ms",
        f"  bash search  {totals['bash_search_calls']:5d} calls  {totals['bash_search_ms']:7d} ms"
        f"  ({share(totals['bash_search_ms'], totals['tool_ms'])} of tool time; whole command line)",
        f"  grep produced {totals['grep_rendered_chars']} rendered characters,"
        f" read {totals['read_rendered_chars']}",
        f"  episodes that called grep at all: {totals['episodes_with_grep']} of {totals['episodes']}",
        "",
        "repetition",
        f"  identical grep repeats within an episode: {totals['identical_grep_repeats']}"
        f" of {totals['grep_calls']} grep calls",
        f"  same pattern, different scope or flags:   {totals['near_grep_repeats']}",
        f"  read of a path already read, different window: {totals['read_repeat_calls']}"
        f" of {totals['read_calls']} read calls",
        f"  read of a path already read, same window:      {totals['read_identical_calls']}",
        "",
        "rendered characters one call put into the model's context",
        "  tool          calls   median      p95      max      total",
    ]
    for tool, sizes in report["rendered_per_call"].items():
        lines.append(
            f"  {tool:12s}  {sizes['calls']:5d}  {sizes['median']:7.0f}  {sizes['p95']:7.0f}"
            f"  {sizes['max']:7d}  {sizes['total']:9d}"
        )
    lines += [
        "",
        "grep pattern shapes",
        f"  literal {shapes['literal']}, regex {shapes['regex']},"
        f" declared literal {shapes['declared_literal']}, ignore_case {shapes['ignore_case']}",
        f"  path-restricted {shapes['path_restricted']}, glob-restricted {shapes['glob_restricted']},"
        f" with context {shapes['with_context']}",
        f"  pattern length median {shapes['pattern_length_median']:.0f},"
        f" max {shapes['pattern_length_max']}",
        "",
        "follow-through proxies (weak; see this script's docstring)",
        f"  grep followed within {REFINEMENT_WINDOW_STEPS} steps by a containing or contained pattern:"
        f" {totals['grep_followed_by_refinement']} of {totals['grep_calls']}",
        f"  grep with matches, none of whose paths a later call names:"
        f" {totals['grep_no_named_path_used_later']} of {totals['grep_with_matches']}",
        "",
        "per run (a run is one root episode and its descendants)",
    ]
    header = (
        "  run                 eps  grep  repeat  read  repeat  grep ms  read ms  bash search"
    )
    lines.append(header)
    for run in report["runs"]:
        lines.append(
            f"  {run['run'][:18]:18s}  {run['episodes']:3d}  {run['grep_calls']:4d}"
            f"  {run['repeat_grep_calls']:6d}  {run['read_calls']:4d}  {run['repeat_read_calls']:6d}"
            f"  {run['grep_ms']:7d}  {run['read_ms']:7d}"
            f"  {run['bash_search_calls']:3d} calls / {run['bash_search_ms']} ms"
        )
    totals_runs = report["runs"]
    lines.append(
        f"  across all runs: {sum(run['repeat_grep_calls'] for run in totals_runs)} repeated grep calls,"
        f" {sum(run['repeat_read_calls'] for run in totals_runs)} reads of an already-read path,"
        f" {sum(run['identical_read_calls'] for run in totals_runs)} of them the same window"
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", required=True, type=Path, help="directory holding episode logs, read only")
    parser.add_argument("--json", type=Path, help="write the full report as JSON to this path")
    args = parser.parse_args(argv)
    if not args.logs.is_dir():
        print(f"search_in_logs: {args.logs} is not a directory", file=sys.stderr)
        return 2
    report = corpus_report(args.logs)
    if not report["totals"]["episodes"]:
        print(f"search_in_logs: no episode.jsonl under {args.logs}", file=sys.stderr)
        return 2
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
