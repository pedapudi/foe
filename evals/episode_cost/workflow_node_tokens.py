#!/usr/bin/python3
"""Attribute a workflow run's tokens and repository reads to its nodes.

The built-in coding workflow fires three model nodes, each a child episode
with fresh context: one implements the task, one assesses the result, and
one repairs what the assessment rejected. `docs/design.md` specifies the
structure. Each firing's `workflow/node-start` names its child episode, and
that child's `assistant/message` events carry provider usage, so the split
between implementing and checking is a fold over stored logs.

The report gives, per node: input, output, and cache-read tokens, model
calls, tool calls, and wall clock. It gives, per run: the ratio of
assessment and repair input tokens to implementation input tokens, and the
files an assessing or repairing node read that the implementing node had
already read.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

from log_facts import Episode, load_episodes

# Substrings that place a node in the coding workflow's three roles. The
# built-in document names them `implement-task`, `assess-task`, and
# `repair-task`; matching on the verb keeps the report working for a
# document that renames a node without changing what it does.
ROLES = (("implement", "implement"), ("assess", "assess"), ("repair", "repair"))


def role_of(node: str) -> str:
    for needle, role in ROLES:
        if needle in node:
            return role
    return "other"


def read_paths(episode: Episode) -> list[str]:
    return [str(call["args"].get("path", "")) for call in episode.tool_calls("read")]


def searched_scopes(episode: Episode) -> list[str]:
    return [str(call["args"].get("path", "")) for call in episode.tool_calls("grep")]


def bash_commands(episode: Episode) -> list[str]:
    return [str(call["args"].get("command", "")).strip() for call in episode.tool_calls("bash")]


def rendered_by_tool(episode: Episode) -> dict[str, int]:
    """Characters of tool output the node put into its own context, by tool.

    A node that rediscovers the repository pays for it here first: every
    rendered character reaches the model on the step that produced it and
    on every later step of the same episode.
    """
    totals: dict[str, int] = collections.defaultdict(int)
    for call in episode.calls:
        if not call["synthetic"]:
            totals[call["name"]] += call["rendered_chars"]
    return dict(sorted(totals.items()))


def node_report(firing: dict[str, Any], child: Episode | None) -> dict[str, Any]:
    usage = child.usage if child else {"input": 0, "output": 0, "cache_read": 0}
    return {
        "node": firing["node"],
        "role": role_of(firing["node"]),
        "fire": firing["fire"],
        "child_id": firing["child_id"],
        "child_log": str(child.path) if child else None,
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "cache_read_tokens": usage["cache_read"],
        "model_calls": child.model_calls if child else 0,
        "tool_calls": len([call for call in child.calls if not call["synthetic"]]) if child else 0,
        "read_calls": len(child.tool_calls("read")) if child else 0,
        "grep_calls": len(child.tool_calls("grep")) if child else 0,
        "bash_calls": len(child.tool_calls("bash")) if child else 0,
        "tool_rendered_chars": sum(call["rendered_chars"] for call in child.calls) if child else 0,
        "duration_ms": firing["duration_ms"],
        "handoff_rendered_chars": firing["rendered_chars"],
        "error": firing["error"],
        "rendered_by_tool": rendered_by_tool(child) if child else {},
        "read_paths": read_paths(child) if child else [],
        "grep_scopes": searched_scopes(child) if child else [],
        "bash_commands": bash_commands(child) if child else [],
    }


def run_report(root_episode: Episode, children: dict[str, Episode]) -> dict[str, Any]:
    nodes = [node_report(firing, children.get(firing["child_id"] or "")) for firing in root_episode.node_firings]
    by_role: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for node in nodes:
        by_role[node["role"]].append(node)

    def total(role: str, field: str) -> int:
        return sum(node[field] for node in by_role.get(role, []))

    implementation_input = total("implement", "input_tokens")
    checking_input = total("assess", "input_tokens") + total("repair", "input_tokens")
    implementation_output = total("implement", "output_tokens")
    checking_output = total("assess", "output_tokens") + total("repair", "output_tokens")
    implemented_paths = {path for node in by_role.get("implement", []) for path in node["read_paths"]}
    implemented_commands = {command for node in by_role.get("implement", []) for command in node["bash_commands"]}
    rereads = []
    recommands = []
    for role in ("assess", "repair"):
        for node in by_role.get(role, []):
            for path in node["read_paths"]:
                if path in implemented_paths:
                    rereads.append({"node": node["node"], "path": path})
            for command in node["bash_commands"]:
                if command in implemented_commands:
                    recommands.append({"node": node["node"], "command": command})
    checking_reads = total("assess", "read_calls") + total("repair", "read_calls")
    checking_bash = total("assess", "bash_calls") + total("repair", "bash_calls")
    return {
        "root_episode": root_episode.id,
        "root_log": str(root_episode.path),
        "outcome": root_episode.outcome.get("kind", "unfinished"),
        "branches": root_episode.branches,
        "nodes": nodes,
        "input_tokens": sum(node["input_tokens"] for node in nodes),
        "output_tokens": sum(node["output_tokens"] for node in nodes),
        "cache_read_tokens": sum(node["cache_read_tokens"] for node in nodes),
        "implementation_input_tokens": implementation_input,
        "checking_input_tokens": checking_input,
        "implementation_output_tokens": implementation_output,
        "checking_output_tokens": checking_output,
        "checking_input_ratio": checking_input / implementation_input if implementation_input else None,
        "checking_output_ratio": checking_output / implementation_output if implementation_output else None,
        "checking_read_calls": checking_reads,
        "checking_bash_calls": checking_bash,
        "checking_rereads_of_implemented_paths": len(rereads),
        "checking_repeated_commands": len(recommands),
        "reread_paths": sorted({entry["path"] for entry in rereads}),
        "repeated_commands": sorted({entry["command"] for entry in recommands}),
    }


def corpus_report(root: Path) -> dict[str, Any]:
    episodes = load_episodes(root)
    by_id = {episode.id: episode for episode in episodes if episode.id}
    runs = [run_report(episode, by_id) for episode in episodes if episode.node_firings]
    role_totals: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"firings": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "model_calls": 0,
                 "tool_calls": 0, "read_calls": 0, "grep_calls": 0, "bash_calls": 0, "duration_ms": 0,
                 "tool_rendered_chars": 0}
    )
    role_rendered: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for run in runs:
        for node in run["nodes"]:
            bucket = role_totals[node["role"]]
            bucket["firings"] += 1
            for field in list(bucket):
                if field != "firings":
                    bucket[field] += node[field]
            for tool, chars in node["rendered_by_tool"].items():
                role_rendered[node["role"]][tool] += chars
    implementation_input = role_totals["implement"]["input_tokens"]
    checking_input = role_totals["assess"]["input_tokens"] + role_totals["repair"]["input_tokens"]
    implementation_output = role_totals["implement"]["output_tokens"]
    checking_output = role_totals["assess"]["output_tokens"] + role_totals["repair"]["output_tokens"]
    return {
        "corpus_root": str(root),
        "workflow_runs": len(runs),
        "node_firings": sum(len(run["nodes"]) for run in runs),
        "roles": {role: dict(bucket) for role, bucket in sorted(role_totals.items())},
        "rendered_by_tool": {role: dict(sorted(tools.items())) for role, tools in sorted(role_rendered.items())},
        "checking_input_ratio": checking_input / implementation_input if implementation_input else None,
        "checking_output_ratio": checking_output / implementation_output if implementation_output else None,
        "checking_input_share": checking_input / (checking_input + implementation_input)
        if checking_input + implementation_input
        else None,
        "checking_rereads_of_implemented_paths": sum(run["checking_rereads_of_implemented_paths"] for run in runs),
        "checking_read_calls": sum(run["checking_read_calls"] for run in runs),
        "checking_repeated_commands": sum(run["checking_repeated_commands"] for run in runs),
        "checking_bash_calls": sum(run["checking_bash_calls"] for run in runs),
        "runs": runs,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"corpus {report['corpus_root']}",
        f"  {report['workflow_runs']} workflow runs, {report['node_firings']} model-node firings",
        "",
        "per role, summed over every firing in the corpus",
        "  role        fires   input tokens  cache read   output   model calls   tools   read   grep   bash",
    ]
    for role, bucket in report["roles"].items():
        lines.append(
            f"  {role:10s}  {bucket['firings']:5d}  {bucket['input_tokens']:13d}"
            f"  {bucket['cache_read_tokens']:10d}  {bucket['output_tokens']:7d}"
            f"  {bucket['model_calls']:11d}  {bucket['tool_calls']:6d}"
            f"  {bucket['read_calls']:5d}  {bucket['grep_calls']:5d}  {bucket['bash_calls']:5d}"
        )
    ratio = report["checking_input_ratio"]
    share = report["checking_input_share"]
    output_ratio = report["checking_output_ratio"]
    lines.append("")
    if ratio is None:
        lines.append("  no implementation input tokens recorded")
    else:
        lines.append(
            f"  assessment plus repair input tokens are {ratio:.2f} times implementation input tokens"
        )
        lines.append(f"  assessment plus repair take {100.0 * share:.1f}% of all model-node input tokens")
    if output_ratio is not None:
        lines.append(
            f"  assessment plus repair output tokens are {output_ratio:.2f} times"
            f" implementation output tokens"
        )
    lines.append(
        f"  reads by an assessing or repairing node of a path the implementer had read:"
        f" {report['checking_rereads_of_implemented_paths']} of {report['checking_read_calls']} such read calls"
    )
    lines.append(
        f"  shell commands an assessing or repairing node reran exactly as the implementer had:"
        f" {report['checking_repeated_commands']} of {report['checking_bash_calls']} such bash calls"
    )
    lines.append("")
    lines.append("rendered tool output each role put into its own context, in characters")
    for role, tools in report["rendered_by_tool"].items():
        listed = ", ".join(f"{tool} {chars}" for tool, chars in tools.items())
        lines.append(f"  {role:10s}  {sum(tools.values()):8d}  ({listed})")
    lines.append("")
    lines.append("per run")
    lines.append("  root episode   outcome    branch   implement in   check in   ratio   check out   rereads")
    for run in report["runs"]:
        branch = run["branches"][0]["label"] if run["branches"] else "-"
        run_ratio = run["checking_input_ratio"]
        shown = f"{run_ratio:5.2f}" if run_ratio is not None else "    -"
        lines.append(
            f"  {run['root_episode']:13s}  {run['outcome']:9s}  {branch:7s}"
            f"  {run['implementation_input_tokens']:12d}  {run['checking_input_tokens']:9d}"
            f"  {shown}   {run['checking_output_tokens']:9d}"
            f"   {run['checking_rereads_of_implemented_paths']:7d}"
        )
    lines.append("")
    lines.append("per node firing")
    lines.append("  root episode   node             fire   input   cache read   output   tools   read   grep   ms")
    for run in report["runs"]:
        for node in run["nodes"]:
            lines.append(
                f"  {run['root_episode']:13s}  {node['node']:15s}  {node['fire']:4d}"
                f"  {node['input_tokens']:6d}  {node['cache_read_tokens']:10d}  {node['output_tokens']:7d}"
                f"  {node['tool_calls']:5d}  {node['read_calls']:5d}  {node['grep_calls']:5d}"
                f"  {node['duration_ms']:6d}"
            )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", required=True, type=Path, help="directory holding episode logs, read only")
    parser.add_argument("--json", type=Path, help="write the full report as JSON to this path")
    args = parser.parse_args(argv)
    if not args.logs.is_dir():
        print(f"workflow_node_tokens: {args.logs} is not a directory", file=sys.stderr)
        return 2
    report = corpus_report(args.logs)
    if not report["workflow_runs"]:
        print(f"workflow_node_tokens: no workflow node events under {args.logs}", file=sys.stderr)
        return 2
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
