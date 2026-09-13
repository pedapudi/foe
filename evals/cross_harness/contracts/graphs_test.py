#!/usr/bin/python3
"""Unit tests for the generated foe documents: no model, no network; `foe plan` and scripted runs when the binary is built."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import graphs  # noqa: E402
import host_runtime  # noqa: E402
import runtime_responses  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
FOE = REPO / "target" / "debug" / "foe"
BUILTIN_CODING = REPO / "crates" / "cli" / "src" / "builtin-coding.json"
BUILTIN_TEAM = REPO / "crates" / "cli" / "src" / "builtin-team.json"

BUDGET = {"model_calls": 40, "seconds": 600, "input_tokens": 100000, "output_tokens": 20000}

# The tools each node holds, as the module docstring of graphs.py describes the nodes.
# The nodes that can change the workspace hold `block`; the two that only
# read do not, as the shipped coding workflow has it.
AUTONOMY_TOOLS = {
    "survey": ["read", "grep", "bash"],
    "implement": ["read", "grep", "edit", "bash", "check", "block"],
    "assess": ["read", "grep", "bash", "check"],
    "repair": ["read", "grep", "edit", "bash", "check", "block"],
}
TEAMS_TOOLS = {
    "survey": ["read", "grep", "bash"],
    "interface": ["read", "grep", "edit", "bash", "check", "block"],
    "delegate": ["read", "grep", "bash", "block", "spawn", "wait", "steer", "cancel", "send", "team"],
    "integrate": ["read", "grep", "edit", "bash", "check", "block"],
    "implement-alone": ["read", "grep", "edit", "bash", "check", "block"],
}
WORKER_TOOLS = ["read", "grep", "edit", "bash", "check", "block", "ask", "notify", "wait"]


def materialize(root: Path) -> tuple[Path, Path]:
    """A workspace with every default write root, one readable file, and an executable check script that accepts everything."""
    workspace = root / "workspace"
    for name in graphs.WRITE_ROOTS:
        (workspace / name).mkdir(parents=True)
    (workspace / "docs" / "notes.txt").write_text("The workspace holds nothing the task changes.\n", encoding="utf-8")
    check = workspace / "checks" / "run.sh"
    check.parent.mkdir()
    check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    check.chmod(0o755)
    return workspace, check


def successors(document: dict[str, Any]) -> dict[str, set[str]]:
    """The model-node successors of each model node: those that follow it and those a branch label lists."""
    graph = nodes(document)
    edges: dict[str, set[str]] = {name: set() for name in graph}
    for name, node in graph.items():
        for source in node["follows"]:
            if source in edges:
                edges[source].add(name)
        for targets in node.get("branches", {}).values():
            edges[name].update(targets)
    return edges


def paths(document: dict[str, Any]) -> Iterator[list[str]]:
    """Every simple path of model nodes from a node that follows only the task to a node with no further successor."""
    graph = nodes(document)
    edges = successors(document)

    def extend(path: list[str]) -> Iterator[list[str]]:
        further = [name for name in edges[path[-1]] if name not in path]
        if not further:
            yield path
        for name in further:
            yield from extend([*path, name])

    for name, node in graph.items():
        if node["follows"] == ["task"]:
            yield from extend([name])


def events(episode: Path) -> list[dict[str, Any]]:
    with (episode / "episode.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def outcome(episode: Path) -> dict[str, Any]:
    ends = [event for event in events(episode) if event["type"] == "episode/end"]
    return ends[-1]["data"]["outcome"]


def fired(episode: Path) -> list[str]:
    """The node of every completed firing, in order."""
    return [event["data"]["node"] for event in events(episode) if event["type"] == "workflow/node-end" and event["data"].get("error") is None]


def reservations(episode: Path, key: str = "model_calls") -> list[int]:
    """The amount of `key` each `budget/reserve` event granted, in order; the event leaves out a dimension it granted without limit."""
    return [event["data"]["reserved"][key] for event in events(episode) if event["type"] == "budget/reserve"]


def spent(episode: Path) -> list[int]:
    """The `model_calls` each `budget/release` event debited, in order."""
    return [event["data"]["spent"]["model_calls"] for event in events(episode) if event["type"] == "budget/release"]


def node_errors(episode: Path) -> dict[str, str]:
    """The error of every firing that ended without a value, by node."""
    return {event["data"]["node"]: event["data"]["error"] for event in events(episode) if event["type"] == "workflow/node-end" and event["data"].get("error") is not None}


def lead_document(workspace: Path, worker_calls: Any, model_calls: int, tokens: dict[str, int] | None = None, worker_tokens: dict[str, int] | None = None) -> dict[str, Any]:
    """A root that spawns workers under `child_contracts`, for a trial of how concurrent children reserve from one remainder.

    `tokens` adds token ceilings to the root's budget, and `worker_tokens`
    token shares to the worker's; a worker without them reserves each token
    dimension as the root's remainder.
    """
    returns = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}
    return {
        "version": 4,
        "name": "lead",
        "instructions": {"10-role": "Lead: add two worker tasks, wait for both, then return."},
        "tools": ["read", "spawn", "wait", "block"],
        "grants": {"read": [str(workspace)], "spawn": [graphs.WORKER]},
        "budget": {"model_calls": model_calls, "seconds": 120, "max_depth": 1, "max_concurrent": 4, "max_episodes": 4, **(tokens or {})},
        "child_contracts": {
            graphs.WORKER: {
                "name": graphs.WORKER,
                "instructions": {"10-role": "Worker: read the notes file, then return."},
                "tools": ["read", "block"],
                "grants": {"read": [str(workspace)]},
                "budget": {"model_calls": worker_calls, "max_depth": 0, **(worker_tokens or {})},
                "done_when": {"returns": returns},
            }
        },
        "done_when": {"returns": returns},
        "task": "Probe.",
    }


def lead_responder(workspace: Path) -> host_runtime.Responder:
    """The lead adds two worker tasks in one turn, waits, and returns; each worker reads once and returns."""

    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        results = [message for message in request["messages"] if message.get("role") == "tool"]
        if "Worker:" in request["system"]:
            if not results:
                return runtime_responses.call("read-notes", "read", {"path": str(workspace / "docs" / "notes.txt")}) + runtime_responses.done("tool")
            return runtime_responses.call("worker-return", "return", {"value": {"summary": "The notes file was read."}}) + runtime_responses.done("tool")
        if not results:
            return (
                runtime_responses.call("spawn-one", "spawn", {"contract": graphs.WORKER, "task": "Unit one.", "context": "fresh"})
                + runtime_responses.call("spawn-two", "spawn", {"contract": graphs.WORKER, "task": "Unit two.", "context": "fresh"})
                + runtime_responses.done("tool")
            )
        if len(results) == 2:
            return runtime_responses.call("wait-all", "wait", {}) + runtime_responses.done("tool")
        return runtime_responses.call("lead-return", "return", {"value": {"summary": "Both units settled."}}) + runtime_responses.done("tool")

    return respond


# The units the scripted divide path names; each is a crate directory under the workspace.
UNITS = ("alpha", "beta")
# The spawn tool's rendered result: the board task, the roster name, and the owning episode.
_SPAWN_RESULT = re.compile(r"\[seq (\d+)\]\n(task_\d+) \S+ as \S+ for (ep_\w+)")
# The inbox line that carries a settled worker's report to the delegating node: the roster name, the episode, and the returned value.
_WORKER_ENDED = re.compile(r"^\S+ \((ep_\w+)\) ended: completed with (\{.*\})$", re.MULTILINE)


def divide_responder(workspace: Path) -> tuple[host_runtime.Responder, list[str]]:
    """The teams graph's divide path with two units; the list receives the node, or worker unit, of each first request.

    The survey chooses `divide` with one unit per entry of `UNITS`, the
    interface node edits the notes file, the delegating node spawns one
    fresh worker per unit with `write` narrowed to that unit's crate
    directory and waits for both, each worker creates one file in its own
    crate, and the integrating node reads the notes file and returns. The
    delegation report cites, per unit, the board task the spawn result
    named and the worker's episode with the sequence the worker's own
    report cited, because a cited sequence under an `episode` names a
    result in that child's log.
    """
    served: list[str] = []

    def seq_of(results: list[dict[str, Any]]) -> int:
        cited = re.search(r"\[seq (\d+)\]", json.dumps(results[-1]))
        return int(cited.group(1)) if cited else 0

    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        system = request["system"]
        results = [message for message in request["messages"] if message.get("role") == "tool"]
        task = runtime_responses.message_text(request["messages"])
        learned = [{"claim": "The notes file was read.", "seq": seq_of(results) if results else 0}]
        if "Survey the task and the workspace, then decide how the work divides" in system:
            if not results:
                served.append("survey")
                return runtime_responses.call("read-notes", "read", {"path": str(workspace / "docs" / "notes.txt")}) + runtime_responses.done("tool")
            units = [{"name": unit, "crates": [unit], "write_roots": [f"crates/{unit}"], "shared": ["docs/notes.txt"]} for unit in UNITS]
            value = {"summary": "Two units.", "units": units, "unresolved_risks": [], "learned": learned, "branch": "divide"}
            return runtime_responses.call("node-return", "return", {"value": value}) + runtime_responses.done("tool")
        if "Write the shared elements the survey named" in system:
            if not results:
                served.append("interface")
                edits = [{"old_text": "nothing the task changes", "new_text": "the interface every unit builds against"}]
                return runtime_responses.call("edit-notes", "edit", {"path": str(workspace / "docs" / "notes.txt"), "edits": edits}) + runtime_responses.done("tool")
            value = {"summary": "The interface is written.", "changed_paths": ["docs/notes.txt"], "validation": ["The notes file was edited."], "unresolved_risks": [], "learned": learned}
            return runtime_responses.call("node-return", "return", {"value": value}) + runtime_responses.done("tool")
        if "Give each unit the survey named to one worker" in system:
            if not results:
                served.append("delegate")
                calls: list[dict[str, Any]] = []
                for unit in UNITS:
                    calls += runtime_responses.call(f"spawn-{unit}", "spawn", {"contract": graphs.WORKER, "task": f"Unit {unit}.", "context": "fresh", "write": [str(workspace / "crates" / unit)]})
                return calls + runtime_responses.done("tool")
            if len(results) == len(UNITS):
                return runtime_responses.call("wait-all", "wait", {}) + runtime_responses.done("tool")
            reports = {}
            for episode_id, value in _WORKER_ENDED.findall(task):
                report = json.loads(value)
                reports[report["unit"]] = (episode_id, report["evidence"][0]["seq"])
            units = []
            for unit, result in zip(UNITS, results):
                spawned = _SPAWN_RESULT.search(str(result.get("rendered", "")))
                if spawned is None or unit not in reports:
                    return [{"kind": "error", "message": f"unit {unit} has no spawn result or no worker report", "retryable": False}]
                episode_id, cited = reports[unit]
                units.append({"unit": unit, "task_id": spawned.group(2), "outcome": "completed", "episode": episode_id, "finding": "The worker wrote its file.", "seq": cited})
            value = {"summary": "Both units settled.", "units": units, "changed_paths": [], "unresolved_risks": []}
            return runtime_responses.call("node-return", "return", {"value": value}) + runtime_responses.done("tool")
        if "Do the one unit your task names" in system:
            unit = next((unit for unit in UNITS if f"Unit {unit}." in task), None)
            if unit is None:
                return [{"kind": "error", "message": "the worker's task names no unit", "retryable": False}]
            if not results:
                served.append(f"worker {unit}")
                edits = [{"old_text": "", "new_text": f"// {unit}\n"}]
                return runtime_responses.call("create-lib", "edit", {"path": str(workspace / "crates" / unit / "lib.rs"), "edits": edits}) + runtime_responses.done("tool")
            value = {"unit": unit, "finding": "The file is written.", "changed_paths": [f"crates/{unit}/lib.rs"], "evidence": [{"claim": "The file was created.", "seq": seq_of(results)}], "needs_from_lead": []}
            return runtime_responses.call("worker-return", "return", {"value": value}) + runtime_responses.done("tool")
        if "Integrate what the workers returned" in system:
            if not results:
                served.append("integrate")
                return runtime_responses.call("read-notes", "read", {"path": str(workspace / "docs" / "notes.txt")}) + runtime_responses.done("tool")
            value = {"summary": "Integrated.", "changed_paths": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": learned}
            return runtime_responses.call("node-return", "return", {"value": value}) + runtime_responses.done("tool")
        return [{"kind": "error", "message": "the request's system text names no known node", "retryable": False}]

    return respond, served


# The phrase in each node's role that identifies it in a request, and the value the node returns.
_LEARNED = {"claim": "The notes file holds one line.", "seq": 0}
_CHANGE = {"summary": "No file needed a change.", "changed_paths": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": [_LEARNED]}
NODE_RETURNS = {
    "Read the task and the workspace before anything changes": {"summary": "Nothing to change.", "unresolved_risks": [], "learned": [_LEARNED]},
    "Implement the task in the workspace": _CHANGE,
    "Assess whether the workspace satisfies the task": {"summary": "Assessed.", "findings": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": [_LEARNED]},
    "Repair the assessment's findings": _CHANGE,
    "Survey the task and the workspace, then decide how the work divides": {"summary": "One unit.", "units": [], "unresolved_risks": [], "learned": [_LEARNED], "branch": "alone"},
    "Implement the whole task yourself": _CHANGE,
}


def scripted(workspace: Path, assessment: str, survey_reads: int = 1) -> tuple[host_runtime.Responder, list[str]]:
    """A responder that reads one file, then returns the asking node's value; the list receives each node's identifying phrase.

    `survey_reads` is how many reads the survey makes before it returns, so a
    trial can have the first node spend most of the root's allowance.
    """
    served: list[str] = []

    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        phrase = next((phrase for phrase in NODE_RETURNS if phrase in request["system"]), None)
        if phrase is None:
            return [{"kind": "error", "message": "the request's system text names no known node", "retryable": False}]
        results = [message for message in request["messages"] if message.get("role") == "tool"]
        if not results:
            served.append(phrase)
        reads = survey_reads if phrase.startswith(("Read the task", "Survey the task")) else 1
        if len(results) < reads:
            return runtime_responses.call(f"read-notes-{len(results)}", "read", {"path": str(workspace / "docs" / "notes.txt")}) + runtime_responses.done("tool")
        cited = re.search(r"\[seq (\d+)\]", json.dumps(results[-1]))
        if cited is None:
            return [{"kind": "error", "message": "the tool result carries no [seq N] prefix", "retryable": False}]
        value = json.loads(json.dumps(NODE_RETURNS[phrase]))
        value["learned"][0]["seq"] = int(cited.group(1))
        if phrase.startswith("Assess"):
            value["branch"] = assessment
            if assessment == "repair":
                value["findings"] = ["The notes file needs a second line."]
        return runtime_responses.call("node-return", "return", {"value": value}) + runtime_responses.done("tool")

    return respond, served


def every_document(workspace: Path, check: Path) -> dict[str, dict[str, Any]]:
    documents = {
        "autonomy": graphs.autonomy(workspace, check, BUDGET, task="Probe."),
        "autonomy-ablated": graphs.autonomy(workspace, check, BUDGET, ablated=True, task="Probe."),
        "autonomy-root-files": graphs.autonomy(workspace, check, BUDGET, root_files=True, task="Probe."),
    }
    for variant in graphs.TEAMS_VARIANTS:
        documents[f"teams-{variant}"] = graphs.teams(workspace, check, BUDGET, variant, task="Probe.")
    return documents


def nodes(document: dict[str, Any]) -> dict[str, Any]:
    return document["workflow"]["nodes"]


def contracts(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every contract in a document: the root, each model node, and each child contract at any depth."""
    pending: list[tuple[str, dict[str, Any]]] = [("root", document)]
    while pending:
        path, contract = pending.pop()
        yield path, contract
        for name, node in contract.get("workflow", {}).get("nodes", {}).items():
            pending.append((f"{path}.nodes.{name}", node["model"]))
        for name, child in contract.get("child_contracts", {}).items():
            pending.append((f"{path}.child_contracts.{name}", child))


class Shape(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path("/w")
        self.check = Path("/w/checks/run.sh")

    def test_the_autonomy_nodes_hold_the_tools_the_table_states(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET)
        self.assertEqual({name: node["model"]["tools"] for name, node in nodes(document).items()}, AUTONOMY_TOOLS)
        self.assertEqual(document["version"], 4)
        self.assertEqual(document["workflow"]["recovery"], {"enabled": False})
        self.assertEqual(document["done_when"], {"verify": "check", "retries": 6})
        self.assertEqual(document["tool_defs"]["check"]["exec"], "/w/checks/run.sh")

    def test_the_autonomy_edges_verifiers_and_bounds(self) -> None:
        graph = nodes(graphs.autonomy(self.workspace, self.check, BUDGET))
        self.assertEqual(graph["survey"]["follows"], ["task"])
        self.assertEqual(graph["implement"]["follows"], ["task", "survey"])
        self.assertEqual(graph["assess"]["follows"], ["task", "implement"])
        self.assertEqual(graph["repair"]["follows"], ["task", "implement", "assess"])
        self.assertEqual(graph["assess"]["branches"], {"accept": [], "repair": ["repair"]})
        for name in ("implement", "repair"):
            self.assertEqual((graph[name]["verify"], graph[name]["retries"]), ("check", 2), name)
        self.assertEqual(graph["implement"]["max_fires"], 3)
        self.assertTrue(graph["repair"]["terminal"])
        self.assertEqual(graph["repair"]["max_fires"], 7)
        self.assertNotIn("verify", graph["survey"])
        self.assertNotIn("verify", graph["assess"])

    def test_the_teams_nodes_hold_the_tools_the_table_states(self) -> None:
        document = graphs.teams(self.workspace, self.check, BUDGET)
        graph = nodes(document)
        self.assertEqual({name: node["model"]["tools"] for name, node in graph.items()}, TEAMS_TOOLS)
        delegate = graph["delegate"]["model"]
        self.assertNotIn("edit", delegate["tools"])
        self.assertEqual(delegate["grants"]["spawn"], ["worker"])
        self.assertEqual(delegate["grants"]["write"], ["/w/crates", "/w/docs", "/w/examples"], "the ceiling a spawn narrows")
        worker = delegate["child_contracts"]["worker"]
        self.assertEqual(worker["tools"], WORKER_TOOLS)
        self.assertEqual(worker["budget"]["model_calls"], graphs.worker_calls(document["budget"]["model_calls"], 4))
        self.assertEqual(worker["budget"]["model_calls"], 5, "half of 40 calls divided among four concurrent workers")
        self.assertEqual(worker["grants"]["write"], delegate["grants"]["write"])
        self.assertEqual(document["child_contracts"]["worker"], worker, "the root carries the worker as the node's ceiling")
        self.assertIsNot(document["child_contracts"]["worker"], worker)
        self.assertEqual(document["grants"]["spawn"], ["worker"])

    def test_the_teams_edges_verifiers_and_bounds(self) -> None:
        graph = nodes(graphs.teams(self.workspace, self.check, BUDGET))
        self.assertEqual(graph["survey"]["branches"], {"divide": ["interface"], "alone": ["implement-alone"]})
        self.assertEqual(graph["interface"]["follows"], ["task", "survey"])
        self.assertEqual(graph["delegate"]["follows"], ["task", "survey", "interface"])
        self.assertEqual(graph["integrate"]["follows"], ["task", "survey", "interface", "delegate"])
        self.assertEqual(graph["implement-alone"]["follows"], ["task", "survey"])
        for name in ("interface", "integrate", "implement-alone"):
            self.assertEqual((graph[name]["verify"], graph[name]["retries"]), ("check", 2), name)
        for name in ("integrate", "implement-alone"):
            self.assertTrue(graph[name]["terminal"], name)
            self.assertEqual(graph[name]["max_fires"], 7, name)
        self.assertNotIn("verify", graph["delegate"])

    def test_the_task_shape_reaches_the_two_nodes_that_can_end_the_workflow(self) -> None:
        items = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {"type": "object"}}}}
        graph = nodes(graphs.teams(self.workspace, self.check, BUDGET, returns=items))
        for name in ("integrate", "implement-alone"):
            self.assertEqual(graph[name]["model"]["done_when"]["returns"], items, name)
            self.assertIn("Return the value the task's text states", graph[name]["model"]["instructions"]["10-role"], name)
        # The nodes in the middle of the graph still report their work as a change.
        self.assertEqual(graph["interface"]["model"]["done_when"]["returns"], graphs.change_report())
        # The two declarations are separate objects, so a caller editing one leaves the other alone.
        graph["integrate"]["model"]["done_when"]["returns"]["properties"]["items"]["type"] = "string"
        self.assertEqual(graph["implement-alone"]["model"]["done_when"]["returns"]["properties"]["items"]["type"], "array")
        without = nodes(graphs.teams(self.workspace, self.check, BUDGET))
        for name in ("integrate", "implement-alone"):
            self.assertEqual(without[name]["model"]["done_when"]["returns"], graphs.change_report(), name)
            self.assertIn("Report every changed path", without[name]["model"]["instructions"]["10-role"], name)

    def test_the_teams_variants_change_one_declaration_each(self) -> None:
        configured = graphs.teams(self.workspace, self.check, BUDGET, "configured")
        undivided = graphs.teams(self.workspace, self.check, BUDGET, "undivided")
        sequential = graphs.teams(self.workspace, self.check, BUDGET, "sequential", max_concurrent=6)
        self.assertEqual(configured["budget"]["max_concurrent"], 4)
        self.assertEqual(nodes(configured)["delegate"]["model"]["budget"]["max_concurrent"], 4)
        self.assertEqual(graphs.teams(self.workspace, self.check, BUDGET, max_concurrent=2)["budget"]["max_concurrent"], 2)
        self.assertEqual(nodes(undivided)["survey"]["branches"], {"alone": ["implement-alone"]})
        self.assertEqual(set(nodes(undivided)), {"survey", "implement-alone"})
        self.assertEqual(sequential["budget"]["max_concurrent"], 1)
        self.assertEqual(nodes(sequential)["delegate"]["model"]["budget"]["max_concurrent"], 1)
        self.assertEqual(nodes(sequential)["delegate"]["model"]["child_contracts"]["worker"]["budget"]["model_calls"], 20)
        self.assertEqual(nodes(sequential)["survey"], nodes(configured)["survey"])

    def test_the_survey_instructs_nothing_its_tools_cannot_do(self) -> None:
        for document in (graphs.autonomy(self.workspace, self.check, BUDGET), graphs.teams(self.workspace, self.check, BUDGET)):
            survey = nodes(document)["survey"]["model"]
            self.assertNotIn("check", survey["tools"])
            self.assertNotIn("Run the checks", survey["instructions"]["10-role"], document["name"])

    def test_the_unit_bounds_equal_the_worker_episodes(self) -> None:
        delegate = nodes(graphs.teams(self.workspace, self.check, BUDGET))["delegate"]["model"]
        self.assertEqual(delegate["budget"]["max_episodes"], 1 + graphs.WORKER_EPISODES)
        self.assertEqual(graphs.units_report()["properties"]["units"]["maxItems"], graphs.WORKER_EPISODES)
        self.assertEqual(graphs.delegation_report()["properties"]["units"]["maxItems"], graphs.WORKER_EPISODES)

    def test_the_ablated_variant_holds_no_block_and_no_verifier(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET, ablated=True)
        self.assertNotIn("done_when", document)
        for path, contract in contracts(document):
            self.assertNotIn("block", contract["tools"], path)
            self.assertNotIn("block", json.dumps(contract["instructions"]), path)
            self.assertNotIn("verify", contract.get("done_when", {}), path)
        for name, node in nodes(document).items():
            self.assertNotIn("verify", node, name)
            self.assertNotIn("retries", node, name)
            self.assertIn("returns", node["model"]["done_when"], name)
        self.assertEqual({name: node["model"]["tools"] for name, node in nodes(document).items()}, {name: [t for t in tools if t != "block"] for name, tools in AUTONOMY_TOOLS.items()})
        self.assertEqual(nodes(document)["assess"]["branches"], {"accept": [], "repair": ["repair"]})

    def test_the_instruction_to_stop_reaches_the_nodes_that_hold_the_tool(self) -> None:
        """A node is told to stop with `block` exactly when it can: a node that
        only reads holds neither the tool nor the sentence about it."""
        for document in (graphs.autonomy(self.workspace, self.check, BUDGET), graphs.teams(self.workspace, self.check, BUDGET)):
            for path, contract in contracts(document):
                if path == "root":
                    continue
                named = "`block`" in json.dumps(contract["instructions"])
                self.assertEqual(named, "block" in contract["tools"], path)

    def test_write_grants_are_the_three_roots_or_the_workspace(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET)
        self.assertEqual(document["grants"]["write"], ["/w/crates", "/w/docs", "/w/examples"])
        self.assertEqual(document["grants"]["read"], ["/w"])
        self.assertEqual(document["grants"]["execute"], list(graphs.EXECUTE_ROOTS))
        graph = nodes(document)
        self.assertEqual(graph["implement"]["model"]["grants"]["write"], document["grants"]["write"])
        self.assertNotIn("write", graph["survey"]["model"]["grants"])
        self.assertNotIn("write", graph["assess"]["model"]["grants"])
        root_files = graphs.autonomy(self.workspace, self.check, BUDGET, root_files=True)
        self.assertEqual(root_files["grants"]["write"], ["/w"])
        self.assertEqual(nodes(root_files)["repair"]["model"]["grants"]["write"], ["/w"])
        narrowed = graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=["src", "tools/gen"], execute=["/usr/bin"])
        self.assertEqual(narrowed["grants"]["write"], ["/w/src", "/w/tools/gen"])
        self.assertEqual(nodes(narrowed)["implement"]["model"]["grants"]["execute"], ["/usr/bin"])

    def test_a_write_root_outside_or_covering_the_workspace_is_refused(self) -> None:
        for root in ("/etc", "../outside", "", ".", "crates/../..", "crates/..", "/w/crates"):
            with self.assertRaises(ValueError, msg=root) as caught:
                graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=[root])
            self.assertIn(f"write_roots entry {root!r} is not a relative path", str(caught.exception), root)
            with self.assertRaises(ValueError, msg=root):
                graphs.teams(self.workspace, self.check, BUDGET, write_roots=["crates", root])
        with self.assertRaises(ValueError) as caught:
            graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=["crates", "docs", "crates/"])
        self.assertIn("write_roots entry 'crates/' repeats an earlier entry", str(caught.exception))
        for document in every_document(self.workspace, self.check).values():
            for path, contract in contracts(document):
                for root in contract["grants"].get("write", []):
                    self.assertTrue(root == "/w" or root.startswith("/w/"), f"{path}: {root}")

    def test_every_node_on_every_path_declares_no_limit_and_the_workers_fit_beside_the_delegating_node(self) -> None:
        """The reservation rule of the runtime: a firing declared without a limit receives the root's whole remainder."""
        self.assertEqual(graphs.NODE_CALLS, "unlimited")
        for name, document in every_document(self.workspace, self.check).items():
            root = document["budget"]["model_calls"]
            graph = nodes(document)
            walked = 0
            for path in paths(document):
                walked += 1
                for node in path:
                    self.assertEqual(graph[node]["model"]["budget"]["model_calls"], graphs.NODE_CALLS, f"{name}: {' > '.join(path)} at {node}")
            self.assertGreater(walked, 0, name)
            delegate = graph.get("delegate")
            if delegate is not None:
                worker = delegate["model"]["child_contracts"]["worker"]["budget"]["model_calls"]
                self.assertIsInstance(worker, int, f"{name}: concurrent workers reserve from one remainder, so each declares a count")
                self.assertEqual(worker, graphs.worker_calls(root, delegate["model"]["budget"]["max_concurrent"]), name)
                workers = delegate["model"]["budget"]["max_concurrent"] * worker
                self.assertLessEqual(1 + workers, root, f"{name}: the delegating node spawns after one call of its own")
        self.assertEqual(graphs.worker_calls(40, 4), 5)
        self.assertEqual(graphs.worker_calls(40, 1), 20)
        self.assertEqual(graphs.worker_calls(12, 4), 1)
        self.assertEqual(graphs.worker_calls(1, 4), 1)

    def test_the_worker_declares_a_token_share_only_under_a_root_token_ceiling(self) -> None:
        """A dimension a child leaves undeclared is reserved as the parent's remainder, so each token ceiling the root declares reaches the worker as a share."""
        for name, document in every_document(self.workspace, self.check).items():
            delegate = nodes(document).get("delegate")
            if delegate is None:
                continue
            concurrent = delegate["model"]["budget"]["max_concurrent"]
            root = document["budget"]
            for worker in (delegate["model"]["child_contracts"]["worker"], document["child_contracts"]["worker"]):
                budget = worker["budget"]
                for key in ("input_tokens", "output_tokens"):
                    self.assertEqual(budget[key], graphs.worker_calls(root[key], concurrent), f"{name}: {key}")
                self.assertLessEqual(concurrent * budget["input_tokens"], root["input_tokens"] // 2, name)
        without = graphs.teams(self.workspace, self.check, {"model_calls": 40, "seconds": 600}, max_concurrent=4)
        worker = nodes(without)["delegate"]["model"]["child_contracts"]["worker"]["budget"]
        self.assertEqual(worker, {"model_calls": graphs.worker_calls(40, 4), "max_depth": 0})
        self.assertEqual(graphs.worker_budget({"model_calls": 40, "seconds": 600, "input_tokens": 100000}, 4), {"model_calls": 5, "input_tokens": 12500})
        self.assertEqual(graphs.worker_budget({"model_calls": 40, "seconds": 600, "input_tokens": 100000, "output_tokens": 20000}, 1), {"model_calls": 20, "input_tokens": 50000, "output_tokens": 10000})
        self.assertEqual(graphs.worker_budget({"model_calls": 4, "seconds": 600, "output_tokens": 7}, 4), {"model_calls": 1, "output_tokens": 1})
        self.assertEqual(graphs.check_budget({"model_calls": 4, "seconds": 2}), {"model_calls": 4, "seconds": 2})
        with self.assertRaises(ValueError) as refused:
            graphs.check_budget({"model_calls": 4, "seconds": 1})
        self.assertIn("budget.seconds is 1", str(refused.exception))

    def test_every_contract_lies_within_the_root_ceiling(self) -> None:
        for document in every_document(self.workspace, self.check).values():
            root = document
            for path, contract in contracts(document):
                self.assertTrue(set(contract["tools"]) <= set(root["tools"]), path)
                calls = contract["budget"]["model_calls"]
                self.assertTrue(calls == graphs.NODE_CALLS or calls <= root["budget"]["model_calls"], f"{path}: {calls!r}")
                for key in ("read", "write", "execute", "spawn"):
                    self.assertTrue(set(contract["grants"].get(key, [])) <= set(root["grants"].get(key, [])), f"{path}: grants.{key}")
                if "check" in contract["tools"]:
                    self.assertEqual(contract["tool_defs"]["check"], root["tool_defs"]["check"], path)
                else:
                    self.assertNotIn("tool_defs", contract, path)

    def test_the_budget_reaches_the_root_and_the_check_timeout_fits_below_it(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET)
        for key, value in BUDGET.items():
            self.assertEqual(document["budget"][key], value, key)
        self.assertLess(document["tool_defs"]["check"]["timeout_seconds"], BUDGET["seconds"])
        # A tenth of the episode, so a hanging check leaves the rest of the
        # budget to diagnose and report; never below the floor, so a real
        # check is not cut off; never at or above the episode's own seconds.
        self.assertEqual(graphs.check_timeout(1800), 180)
        self.assertEqual(graphs.check_timeout(600), 90)
        self.assertEqual(graphs.check_timeout(300), 90)
        self.assertEqual(graphs.check_timeout(60), 59)
        self.assertEqual(graphs.check_timeout(2), 1)
        minimal = graphs.autonomy(self.workspace, self.check, {"model_calls": 3, "seconds": 2})
        self.assertEqual(minimal["budget"], {"model_calls": 3, "seconds": 2, "max_episodes": 1 + 1 + 3 + 7 + 7, "max_depth": 1})
        self.assertEqual(minimal["tool_defs"]["check"]["timeout_seconds"], 1)
        teams = graphs.teams(self.workspace, self.check, BUDGET)
        self.assertEqual(teams["budget"]["max_episodes"], 1 + 1 + 3 + 1 + 7 + 7 + graphs.WORKER_EPISODES)
        self.assertEqual(teams["budget"]["max_depth"], 2)
        self.assertEqual(nodes(teams)["delegate"]["model"]["budget"]["max_depth"], 1)
        self.assertEqual(nodes(teams)["delegate"]["model"]["child_contracts"]["worker"]["budget"]["max_depth"], 0)

    def test_the_task_reaches_the_document(self) -> None:
        self.assertEqual(graphs.autonomy(self.workspace, self.check, BUDGET, task="Fix it.")["task"], "Fix it.")
        self.assertEqual(graphs.teams(self.workspace, self.check, BUDGET, task="Fix it.")["task"], "Fix it.")
        self.assertEqual(graphs.autonomy(self.workspace, self.check, BUDGET)["task"], graphs.PLACEHOLDER_TASK)
        self.assertTrue(graphs.PLACEHOLDER_TASK.strip(), "the binary refuses an empty task")

    def test_errors_name_the_key(self) -> None:
        cases = [
            ({"model_calls": 3}, "budget lacks seconds"),
            ({"seconds": 30}, "budget lacks model_calls"),
            ({"model_calls": 3, "seconds": 1}, "budget.seconds"),
            ({"model_calls": 0, "seconds": 30}, "budget.model_calls"),
            ({"model_calls": True, "seconds": 30}, "budget.model_calls"),
            ({"model_calls": 3, "seconds": 30, "max_depth": 2}, "budget.max_depth"),
        ]
        for budget, expected in cases:
            with self.assertRaises(ValueError, msg=expected) as caught:
                graphs.autonomy(self.workspace, self.check, budget)
            self.assertIn(expected, str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            graphs.teams(self.workspace, self.check, BUDGET, "parallel")
        self.assertIn("variant is 'parallel'", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            graphs.teams(self.workspace, self.check, BUDGET, max_concurrent=0)
        self.assertIn("max_concurrent is 0", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            graphs.autonomy(Path("relative"), self.check, BUDGET)
        self.assertIn("workspace is 'relative'", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            graphs.autonomy(self.workspace, Path("checks/run.sh"), BUDGET)
        self.assertIn("check is 'checks/run.sh'", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=[])
        self.assertIn("write_roots is empty", str(caught.exception))


class Schemas(unittest.TestCase):
    def test_the_change_and_assessment_shapes_equal_the_built_in_coding_document(self) -> None:
        builtin = json.loads(BUILTIN_CODING.read_text(encoding="utf-8"))["workflow"]["nodes"]
        graph = nodes(graphs.autonomy(Path("/w"), Path("/w/checks/run.sh"), BUDGET))
        pairs = {"implement": "implement-task", "assess": "assess-task", "repair": "repair-task"}
        for ours, theirs in pairs.items():
            self.assertEqual(graph[ours]["model"]["done_when"]["returns"], builtin[theirs]["model"]["done_when"]["returns"], ours)

    def test_the_worker_shape_equals_the_built_in_team_document(self) -> None:
        builtin = json.loads(BUILTIN_TEAM.read_text(encoding="utf-8"))["child_contracts"]["worker"]
        self.assertEqual(graphs.worker_report(), builtin["done_when"]["returns"])

    def test_the_cited_members_carry_a_sequence_and_a_minimum(self) -> None:
        for schema, member in (
            (graphs.survey_report(), "learned"),
            (graphs.units_report(), "learned"),
            (graphs.delegation_report(), "units"),
            (graphs.worker_report(), "evidence"),
        ):
            items = schema["properties"][member]
            self.assertIn(member, schema["required"])
            self.assertGreaterEqual(items["minItems"], 1, member)
            self.assertEqual(items["items"]["properties"]["seq"], {"type": "integer", "minimum": 0}, member)
            self.assertIn("seq", items["items"]["required"], member)
        unit = graphs.units_report()["properties"]["units"]["items"]
        self.assertEqual(unit["required"], ["name", "crates", "write_roots", "shared"])
        delegated = graphs.delegation_report()["properties"]["units"]["items"]
        self.assertEqual(set(delegated["properties"]), {"unit", "task_id", "outcome", "episode", "finding", "seq"})
        self.assertNotIn("branch", graphs.units_report()["properties"], "the runtime adds the branch enum itself")

    def test_the_schemas_stay_within_the_runtime_subset(self) -> None:
        allowed = {
            "type", "enum", "const", "anyOf", "required", "properties", "additionalProperties", "items",
            "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "description",
        }

        def keywords(schema: Any) -> Iterator[str]:
            if isinstance(schema, dict):
                yield from schema
                for key, value in schema.items():
                    if key in ("properties",):
                        for sub in value.values():
                            yield from keywords(sub)
                    elif key in ("items", "additionalProperties"):
                        yield from keywords(value)

        for _, contract in contracts(graphs.teams(Path("/w"), Path("/w/checks/run.sh"), BUDGET)):
            returns = contract.get("done_when", {}).get("returns")
            if returns is not None:
                self.assertTrue(set(keywords(returns)) <= allowed, contract["name"])


class Written(unittest.TestCase):
    def test_write_creates_the_parent_and_round_trips(self) -> None:
        document = graphs.autonomy(Path("/w"), Path("/w/checks/run.sh"), BUDGET, task="Probe.")
        with tempfile.TemporaryDirectory() as tmp:
            path = graphs.write(document, Path(tmp) / "out" / "autonomy.json")
            self.assertEqual(path, Path(tmp) / "out" / "autonomy.json")
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("}\n"))
            self.assertEqual(json.loads(text), document)


class Planned(unittest.TestCase):
    """Every variant is accepted by `foe plan`, run from the scratch workspace it names."""

    def test_foe_plan_accepts_every_variant(self) -> None:
        if not os.access(FOE, os.X_OK):
            self.skipTest(f"{FOE} is not built")
        with tempfile.TemporaryDirectory() as tmp:
            workspace, check = materialize(Path(tmp))
            documents = every_document(workspace, check)
            documents["autonomy-default-task"] = graphs.autonomy(workspace, check, BUDGET)
            for name, document in documents.items():
                path = graphs.write(document, Path(tmp) / "documents" / f"{name}.json")
                completed = subprocess.run([str(FOE), "plan", "--config", str(path)], capture_output=True, text=True, cwd=workspace, check=False, timeout=120)
                self.assertEqual(completed.returncode, 0, f"{name}: {completed.stderr.strip()}")
                lines = completed.stdout.splitlines()
                expected_nodes = len(nodes(document))
                self.assertIn(f"execution   workflow: {expected_nodes} nodes", "\n".join(lines), name)
                self.assertIn("warnings    none", lines, name)
                if "done_when" in document:
                    self.assertIn("completion  verifier check (retries 6)", lines, name)
                else:
                    self.assertTrue(any(line.startswith("completion  none declared") for line in lines), name)


class Fired(unittest.TestCase):
    """A document run with scripted responses reaches every node on its path; the binary and the check script are the only externals."""

    def run_document(self, tmp: Path, name: str, document: dict[str, Any], assessment: str) -> tuple[Path, list[str]]:
        path = graphs.write(document, tmp / "documents" / f"{name}.json")
        respond, served = scripted(Path(document["grants"]["read"][0]), assessment)
        status, episode = host_runtime.run(FOE, path, tmp / "logs" / name, respond)
        self.assertEqual(status, 0, f"{name}: {outcome(episode)}")
        self.assertEqual(outcome(episode)["kind"], "completed", name)
        return episode, served

    def test_the_accept_and_repair_paths_and_the_alone_path_complete_with_scripted_returns(self) -> None:
        if not os.access(FOE, os.X_OK):
            self.skipTest(f"{FOE} is not built")
        with tempfile.TemporaryDirectory() as tmp:
            workspace, check = materialize(Path(tmp))
            autonomy = graphs.autonomy(workspace, check, BUDGET, task="Probe.")
            episode, served = self.run_document(Path(tmp), "accept", autonomy, "accept")
            self.assertEqual(fired(episode), ["survey", "implement", "assess"])
            self.assertEqual(len(served), 3, "each node asked once, then returned")
            episode, served = self.run_document(Path(tmp), "repair", autonomy, "repair")
            self.assertEqual(fired(episode), ["survey", "implement", "assess", "repair"])
            self.assertTrue(any(event["type"] == "verification/result" for event in events(episode)), "the root verifier ran")
            teams = graphs.teams(workspace, check, BUDGET, task="Probe.")
            episode, served = self.run_document(Path(tmp), "alone", teams, "accept")
            self.assertEqual(fired(episode), ["survey", "implement-alone"])
            self.assertEqual(reservations(episode), [40, 38], "each firing reserved the root's whole remainder")


class Divided(unittest.TestCase):
    """The teams graph's divide path under scripted responses: two fresh workers under the delegating node, each in its own write root.

    The budget names `model_calls` and `seconds` alone, because a worker
    declares a share of the model calls and no share of the tokens, so
    under a token allowance the first worker reserves the whole remainder
    and the second spawn is refused as exhausted.
    """

    def test_the_divide_path_spawns_two_workers_under_distinct_write_roots_and_completes(self) -> None:
        if not os.access(FOE, os.X_OK):
            self.skipTest(f"{FOE} is not built")
        with tempfile.TemporaryDirectory() as tmp:
            workspace, check = materialize(Path(tmp))
            for unit in UNITS:
                (workspace / "crates" / unit).mkdir()
            document = graphs.teams(workspace, check, {"model_calls": 40, "seconds": 600}, task="Probe.")
            path = graphs.write(document, Path(tmp) / "documents" / "divide.json")
            respond, served = divide_responder(workspace)
            status, episode = host_runtime.run(FOE, path, Path(tmp) / "logs" / "divide", respond)
            self.assertEqual(status, 0, outcome(episode))
            self.assertEqual(outcome(episode)["kind"], "completed")
            self.assertEqual(fired(episode), ["survey", "interface", "delegate", "integrate"])
            self.assertEqual(sorted(served), sorted(["survey", "interface", "delegate", "worker alpha", "worker beta", "integrate"]))
            # The delegating node is the root's child whose spawn names the delegate contract; its workers are its own children.
            delegate_id = next(event["data"]["child_id"] for event in events(episode) if event["type"] == "spawn/start" and event["data"]["contract"] == "delegate")
            delegate = episode / "children" / delegate_id
            spawns = [event["data"] for event in events(delegate) if event["type"] == "spawn/start"]
            self.assertEqual([spawn["contract"] for spawn in spawns], [graphs.WORKER] * 2)
            self.assertEqual([spawn["context"] for spawn in spawns], ["fresh"] * 2)
            for spawn in spawns:
                self.assertTrue((delegate / "children" / spawn["child_id"] / "episode.jsonl").is_file(), spawn)
            # The spawn tool's result records the write roots the board task holds; the spawn event names the call.
            roots = {event["data"]["call_id"]: event["data"]["value"]["write"] for event in events(delegate) if event["type"] == "tool/result" and event["data"]["name"] == "spawn"}
            by_child = {spawn["child_id"]: roots[spawn["call_id"]] for spawn in spawns}
            self.assertEqual(sorted(root for write in by_child.values() for root in write), sorted(str(workspace / "crates" / unit) for unit in UNITS))
            first, second = by_child.values()
            self.assertNotEqual(first, second)
            # Each worker's log records the narrowed grant, and its one edit lies inside it.
            for child_id, write in by_child.items():
                worker_events = events(delegate / "children" / child_id)
                self.assertEqual(worker_events[0]["data"]["contract"]["grants"]["write"], write)
                edited = [event["data"]["value"]["path"] for event in worker_events if event["type"] == "tool/result" and event["data"]["name"] == "edit"]
                self.assertEqual(len(edited), 1, child_id)
                self.assertTrue(str(workspace / edited[0]).startswith(write[0] + "/"), (edited, write))
            self.assertEqual([(workspace / "crates" / unit / "lib.rs").read_text(encoding="utf-8") for unit in UNITS], [f"// {unit}\n" for unit in UNITS])
            self.assertIn("the interface every unit builds against", (workspace / "docs" / "notes.txt").read_text(encoding="utf-8"))


class Reserved(unittest.TestCase):
    """How the runtime reserves a node's budget, observed through scripted runs; these trials fix the declarations graphs.py makes.

    A survey spends most of a small root allowance on reads before it
    returns. A node declared `"unlimited"` then receives whatever remains,
    a node declared with the root's own count is refused, and a node
    without the key is refused at planning.
    """

    ROOT_CALLS = 12
    # Reads before the survey returns; the return is one call more.
    SURVEY_READS = 7

    def setUp(self) -> None:
        if not os.access(FOE, os.X_OK):
            self.skipTest(f"{FOE} is not built")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace, self.check = materialize(self.root)

    def autonomy(self, name: str, model_calls: Any) -> Path:
        """The autonomy document with every model node's `model_calls` set to `model_calls`, or the key removed for None."""
        document = graphs.autonomy(self.workspace, self.check, {"model_calls": self.ROOT_CALLS, "seconds": 120}, task="Probe.")
        for node in nodes(document).values():
            budget = node["model"]["budget"]
            self.assertEqual(budget["model_calls"], graphs.NODE_CALLS)
            if model_calls is None:
                del budget["model_calls"]
            else:
                budget["model_calls"] = model_calls
        return graphs.write(document, self.root / "documents" / f"{name}.json")

    def plan(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(FOE), "plan", "--config", str(path)], capture_output=True, text=True, cwd=self.workspace, check=False, timeout=120)

    def run_autonomy(self, name: str, path: Path) -> tuple[int, Path]:
        respond, _ = scripted(self.workspace, "accept", survey_reads=self.SURVEY_READS)
        return host_runtime.run(FOE, path, self.root / "logs" / name, respond)

    def test_a_node_without_the_key_is_refused_at_planning(self) -> None:
        completed = self.plan(self.autonomy("omitted", None))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing field `model_calls`", completed.stderr)

    def test_a_node_declared_unlimited_receives_the_remainder_and_the_path_completes(self) -> None:
        path = self.autonomy("unlimited", graphs.NODE_CALLS)
        self.assertEqual(self.plan(path).returncode, 0)
        status, episode = self.run_autonomy("unlimited", path)
        self.assertEqual(status, 0, outcome(episode))
        self.assertEqual(fired(episode), ["survey", "implement", "assess"])
        survey_spent = self.SURVEY_READS + 1
        self.assertEqual(spent(episode), [survey_spent, 2, 2])
        self.assertEqual(reservations(episode), [self.ROOT_CALLS, self.ROOT_CALLS - survey_spent, self.ROOT_CALLS - survey_spent - 2])

    def test_a_node_declared_with_the_root_count_is_refused_once_less_remains(self) -> None:
        path = self.autonomy("whole", self.ROOT_CALLS)
        self.assertEqual(self.plan(path).returncode, 0)
        status, episode = self.run_autonomy("whole", path)
        self.assertEqual(status, 3, "the run ended exhausted")
        self.assertEqual(outcome(episode), {"kind": "exhausted", "limit": "model_calls"})
        self.assertEqual(fired(episode), ["survey"])
        self.assertEqual(reservations(episode), [self.ROOT_CALLS], "the second firing asked for more than remained and reserved nothing")
        self.assertIn("model_calls", node_errors(episode)["implement"])

    def test_concurrent_workers_declared_unlimited_starve_each_other_and_a_share_lets_both_run(self) -> None:
        unlimited = graphs.write(lead_document(self.workspace, graphs.NODE_CALLS, self.ROOT_CALLS), self.root / "documents" / "workers-unlimited.json")
        self.assertEqual(self.plan(unlimited).returncode, 0)
        status, episode = host_runtime.run(FOE, unlimited, self.root / "logs" / "workers-unlimited", lead_responder(self.workspace))
        self.assertEqual(status, 3, "the second worker found nothing left to reserve")
        self.assertEqual(outcome(episode), {"kind": "exhausted", "limit": "model_calls"})
        self.assertEqual(reservations(episode), [self.ROOT_CALLS - 1], "the first worker took everything after the lead's one call")
        shared = graphs.write(lead_document(self.workspace, graphs.worker_calls(self.ROOT_CALLS, 2), self.ROOT_CALLS), self.root / "documents" / "workers-shared.json")
        self.assertEqual(self.plan(shared).returncode, 0)
        status, episode = host_runtime.run(FOE, shared, self.root / "logs" / "workers-shared", lead_responder(self.workspace))
        self.assertEqual(status, 0, outcome(episode))
        self.assertEqual(reservations(episode), [graphs.worker_calls(self.ROOT_CALLS, 2)] * 2, "both shared workers reserve their share")
        self.assertEqual(spent(episode), [2, 2])

    def test_concurrent_workers_without_a_token_share_starve_each_other_under_a_token_ceiling_and_a_share_lets_both_run(self) -> None:
        tokens = {"input_tokens": 4000, "output_tokens": 1000}
        calls = graphs.worker_calls(self.ROOT_CALLS, 2)
        unshared = graphs.write(lead_document(self.workspace, calls, self.ROOT_CALLS, tokens), self.root / "documents" / "workers-unshared-tokens.json")
        self.assertEqual(self.plan(unshared).returncode, 0)
        status, episode = host_runtime.run(FOE, unshared, self.root / "logs" / "workers-unshared-tokens", lead_responder(self.workspace))
        self.assertEqual(status, 3, "the second worker found no input tokens left to reserve")
        self.assertEqual(outcome(episode), {"kind": "exhausted", "limit": "input_tokens"})
        self.assertEqual(reservations(episode), [calls], "the model-call share alone did not keep the first worker from taking every token")
        lead_input = runtime_responses.SMALL_USAGE["input"]
        self.assertEqual(reservations(episode, "input_tokens"), [tokens["input_tokens"] - lead_input], "the first worker took every input token left after the lead's one call")
        shares = graphs.worker_budget({"model_calls": self.ROOT_CALLS, "seconds": 120, **tokens}, 2)
        shared = graphs.write(lead_document(self.workspace, shares["model_calls"], self.ROOT_CALLS, tokens, {key: shares[key] for key in tokens}), self.root / "documents" / "workers-shared-tokens.json")
        self.assertEqual(self.plan(shared).returncode, 0)
        status, episode = host_runtime.run(FOE, shared, self.root / "logs" / "workers-shared-tokens", lead_responder(self.workspace))
        self.assertEqual(status, 0, outcome(episode))
        self.assertEqual(reservations(episode, "input_tokens"), [shares["input_tokens"]] * 2, "both shared workers reserve their input share")
        self.assertEqual(reservations(episode, "output_tokens"), [shares["output_tokens"]] * 2, "both shared workers reserve their output share")
        self.assertEqual(spent(episode), [2, 2])


if __name__ == "__main__":
    unittest.main()
