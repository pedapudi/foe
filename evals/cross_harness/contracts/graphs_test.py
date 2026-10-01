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


def materialize(root: Path) -> tuple[Path, Path]:
    """A workspace with every default write root, one readable file, and an executable check script that accepts everything."""
    workspace = root / "workspace"
    for name in graphs.WRITE_ROOTS:
        (workspace / name).mkdir(parents=True)
    # The directories a check writes into exist before an arm starts, because
    # a grant names a directory and the runtime refuses one that is absent.
    # The runner creates them for a real attempt; this stands in for that.
    for name in graphs.CHECK_WRITES:
        (workspace / name).mkdir(parents=True, exist_ok=True)
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


def verifications(episode: Path) -> int:
    """The `verification/result` events in the episode's log and in every child episode's log beneath it."""
    count = 0
    for log in sorted(episode.rglob("episode.jsonl")):
        with log.open(encoding="utf-8") as handle:
            count += sum(1 for line in handle if line.strip() and json.loads(line)["type"] == "verification/result")
    return count


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


# The phrase in each node's role that identifies it in a request, and the value the node returns.
_LEARNED = {"claim": "The notes file holds one line.", "seq": 0}
_CHANGE = {"summary": "No file needed a change.", "changed_paths": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": [_LEARNED]}
NODE_RETURNS = {
    "Read the task and the workspace before anything changes": {"summary": "Nothing to change.", "unresolved_risks": [], "learned": [_LEARNED]},
    "Implement the task in the workspace": _CHANGE,
    "Assess whether the workspace satisfies the task": {"summary": "Assessed.", "findings": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": [_LEARNED]},
    "Repair the assessment's findings": _CHANGE,
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
        reads = survey_reads if phrase.startswith("Read the task") else 1
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
    return {
        "autonomy": graphs.autonomy(workspace, check, BUDGET, task="Probe."),
        "autonomy-ablated": graphs.autonomy(workspace, check, BUDGET, "ablated", task="Probe."),
        "autonomy-unverified": graphs.autonomy(workspace, check, BUDGET, "unverified", task="Probe."),
        "autonomy-lean": graphs.autonomy(workspace, check, BUDGET, "lean", task="Probe."),
        "autonomy-root-files": graphs.autonomy(workspace, check, BUDGET, root_files=True, task="Probe."),
    }


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

    def test_the_survey_instructs_nothing_its_tools_cannot_do(self) -> None:
        survey = nodes(graphs.autonomy(self.workspace, self.check, BUDGET))["survey"]["model"]
        self.assertNotIn("check", survey["tools"])
        self.assertNotIn("Run the checks", survey["instructions"]["10-role"])

    def test_the_ablated_variant_holds_no_block_and_no_verifier(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET, "ablated")
        self.assertEqual(document["name"], "autonomy-ablated")
        self.assertNotIn("done_when", document)
        for path, contract in contracts(document):
            self.assertNotIn("block", contract["tools"], path)
            self.assertNotIn("block", json.dumps(contract["instructions"]), path)
            self.assertNotIn("verify", contract.get("done_when", {}), path)
        for name, node in nodes(document).items():
            for key in graphs.NODE_VERIFIER_KEYS:
                self.assertNotIn(key, node, f"{name}: {key}")
            self.assertIn("returns", node["model"]["done_when"], name)
        self.assertEqual({name: node["model"]["tools"] for name, node in nodes(document).items()}, {name: [t for t in tools if t != "block"] for name, tools in AUTONOMY_TOOLS.items()})
        self.assertEqual(nodes(document)["assess"]["branches"], {"accept": [], "repair": ["repair"]})
        self.assertEqual(document["budget"]["max_episodes"], 1 + len(nodes(document)), "without a verifier each node fires once")

    def test_the_unverified_variant_differs_from_the_configured_one_in_the_verifier_keys_alone(self) -> None:
        """docs/evaluation.md "Arms": testing the verifier alone keeps the available outcomes and the stopping instructions identical.

        With the keys `verify`, `retries`, and `max_fires` removed from every
        node, `done_when` from the root, and the name and
        `budget.max_episodes` from both, the two documents are equal; the
        tools of every contract and every instruction string are identical
        before any removal.
        """
        configured = graphs.autonomy(self.workspace, self.check, BUDGET, task="Probe.")
        unverified = graphs.autonomy(self.workspace, self.check, BUDGET, "unverified", task="Probe.")
        self.assertEqual(unverified["name"], "autonomy-unverified")
        self.assertIn("done_when", configured)
        self.assertNotIn("done_when", unverified)
        self.assertEqual(dict(contracts(configured)).keys(), dict(contracts(unverified)).keys())
        for (path, ours), (_, theirs) in zip(sorted(contracts(configured), key=lambda item: item[0]), sorted(contracts(unverified), key=lambda item: item[0])):
            self.assertEqual(ours["tools"], theirs["tools"], path)
            self.assertEqual(ours["instructions"], theirs["instructions"], path)
        self.assertIn(graphs.BLOCK, nodes(unverified)["implement"]["model"]["tools"])
        self.assertIn("call `block`", nodes(unverified)["implement"]["model"]["instructions"]["20-contract"])

        def stripped(document: dict[str, Any]) -> dict[str, Any]:
            document = json.loads(json.dumps(document))
            for node in nodes(document).values():
                for key in graphs.NODE_VERIFIER_KEYS:
                    node.pop(key, None)
            document.pop("done_when", None)
            document.pop("name")
            document["budget"].pop("max_episodes")
            return document

        self.assertEqual(stripped(configured), stripped(unverified))
        for name, node in nodes(unverified).items():
            for key in graphs.NODE_VERIFIER_KEYS:
                self.assertNotIn(key, node, f"{name}: {key}")
        # The episode ceiling counts the firings that remain: one per node.
        self.assertEqual(unverified["budget"]["max_episodes"], 1 + len(nodes(unverified)))
        self.assertEqual(configured["budget"]["max_episodes"], 1 + 1 + graphs.VERIFIED_FIRES + 2 * graphs.TERMINAL_FIRES)

    def test_an_unknown_autonomy_variant_is_refused_by_name(self) -> None:
        with self.assertRaises(ValueError) as caught:
            graphs.autonomy(self.workspace, self.check, BUDGET, "parallel")
        self.assertIn("variant is 'parallel'", str(caught.exception))
        self.assertIn("unverified", str(caught.exception))

    def test_the_lean_variant_drops_the_survey_and_keeps_the_verifiers(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET, "lean")
        graph = nodes(document)
        self.assertEqual(document["name"], "autonomy-lean")
        self.assertEqual(list(graph), ["implement", "assess", "repair"])
        self.assertEqual(graph["implement"]["follows"], ["task"])
        self.assertEqual({name: node["model"]["tools"] for name, node in graph.items()}, {name: tools for name, tools in AUTONOMY_TOOLS.items() if name != "survey"})
        self.assertIn("Read the task and the workspace before anything changes", graph["implement"]["model"]["instructions"]["10-role"])
        for name in ("implement", "repair"):
            self.assertEqual((graph[name]["verify"], graph[name]["retries"]), ("check", 2), name)
        self.assertEqual(document["done_when"], {"verify": "check", "retries": 6})
        self.assertEqual(document["budget"]["max_episodes"], graphs.autonomy(self.workspace, self.check, BUDGET)["budget"]["max_episodes"] - 1)

    def test_the_instruction_to_stop_reaches_the_nodes_that_hold_the_tool(self) -> None:
        """A node is told to stop with `block` exactly when it can: a node that
        only reads holds neither the tool nor the sentence about it."""
        for document in every_document(self.workspace, self.check).values():
            for path, contract in contracts(document):
                if path == "root":
                    continue
                named = "`block`" in json.dumps(contract["instructions"])
                self.assertEqual(named, "block" in contract["tools"], path)

    def test_write_grants_are_the_three_roots_or_the_workspace(self) -> None:
        document = graphs.autonomy(self.workspace, self.check, BUDGET)
        self.assertEqual(document["grants"]["write"], ["/w/crates", "/w/docs", "/w/examples", "/w/target", "/w/.check-tmp"])
        self.assertEqual(document["grants"]["read"], ["/w"])
        self.assertEqual(document["grants"]["execute"], list(graphs.EXECUTE_ROOTS))
        graph = nodes(document)
        self.assertEqual(graph["implement"]["model"]["grants"]["write"], document["grants"]["write"])
        self.assertNotIn("write", graph["survey"]["model"]["grants"])
        # The assessing node runs the check and must be able to write what a
        # check writes, and nothing else: it holds no source root, so it can
        # run the tests it is asked to run without touching a file the task
        # is about.
        self.assertEqual(graph["assess"]["model"]["grants"]["write"], ["/w/target", "/w/.check-tmp"])
        # A task that writes the workspace needs nothing added: the check's
        # directories already lie inside it, and the runtime refuses a grant
        # inside another grant of the same contract.
        root_files = graphs.autonomy(self.workspace, self.check, BUDGET, root_files=True)
        self.assertEqual(root_files["grants"]["write"], ["/w"])
        self.assertEqual(nodes(root_files)["repair"]["model"]["grants"]["write"], ["/w"])
        narrowed = graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=["src", "tools/gen"], execute=["/usr/bin"])
        self.assertEqual(narrowed["grants"]["write"], ["/w/src", "/w/tools/gen", "/w/target", "/w/.check-tmp"])
        self.assertEqual(nodes(narrowed)["implement"]["model"]["grants"]["execute"], ["/usr/bin"])

    def test_a_write_root_outside_or_covering_the_workspace_is_refused(self) -> None:
        for root in ("/etc", "../outside", "", ".", "crates/../..", "crates/..", "/w/crates"):
            with self.assertRaises(ValueError, msg=root) as caught:
                graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=[root])
            self.assertIn(f"write_roots entry {root!r} is not a relative path", str(caught.exception), root)
            with self.assertRaises(ValueError, msg=root):
                graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=["crates", root])
        with self.assertRaises(ValueError) as caught:
            graphs.autonomy(self.workspace, self.check, BUDGET, write_roots=["crates", "docs", "crates/"])
        self.assertIn("write_roots entry 'crates/' repeats an earlier entry", str(caught.exception))
        for document in every_document(self.workspace, self.check).values():
            for path, contract in contracts(document):
                for root in contract["grants"].get("write", []):
                    self.assertTrue(root == "/w" or root.startswith("/w/"), f"{path}: {root}")

    def test_every_node_on_every_path_declares_no_limit(self) -> None:
        """The reservation rule of the runtime: a firing declared without a limit receives the root's whole remainder."""
        self.assertEqual(graphs.NODE_CALLS, "unlimited")
        for name, document in every_document(self.workspace, self.check).items():
            graph = nodes(document)
            walked = 0
            for path in paths(document):
                walked += 1
                for node in path:
                    self.assertEqual(graph[node]["model"]["budget"]["model_calls"], graphs.NODE_CALLS, f"{name}: {' > '.join(path)} at {node}")
            self.assertGreater(walked, 0, name)

    def test_the_budget_check_refuses_a_root_too_short_for_its_check(self) -> None:
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
                    granted = root["grants"].get(key, [])
                    for entry in contract["grants"].get(key, []):
                        # A child's grant lies within the root's when it names
                        # the same path or one beneath it. A node that only
                        # runs a check is granted the check's own directories,
                        # which sit under the root's grant rather than equal it.
                        within = entry in granted or any(Path(entry).is_relative_to(Path(root_entry)) for root_entry in granted)
                        self.assertTrue(within, f"{path}: grants.{key}: {entry}")
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

    def test_the_task_reaches_the_document(self) -> None:
        self.assertEqual(graphs.autonomy(self.workspace, self.check, BUDGET, task="Fix it.")["task"], "Fix it.")
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

    def test_the_cited_members_carry_a_sequence_and_a_minimum(self) -> None:
        for schema in (graphs.survey_report(), graphs.change_report(), graphs.assessment_report()):
            items = schema["properties"]["learned"]
            self.assertIn("learned", schema["required"])
            self.assertGreaterEqual(items["minItems"], 1)
            self.assertEqual(items["items"]["properties"]["seq"], {"type": "integer", "minimum": 0})
            self.assertIn("seq", items["items"]["required"])

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

        for _, contract in contracts(graphs.autonomy(Path("/w"), Path("/w/checks/run.sh"), BUDGET)):
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

    def test_the_accept_and_repair_paths_complete_with_scripted_returns(self) -> None:
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

    def test_the_configured_document_invokes_the_runtime_verifier_and_the_unverified_one_never_does_while_block_stays_callable(self) -> None:
        """docs/evaluation.md "Arms" and gate "Mechanism exercised": the control reaches the condition it tests.

        Under `configured` the accept path records at least one
        `verification/result` event. Under `unverified` the same scripted
        path records none, and an implementing node that calls `block` is
        offered the tool and ends the workflow blocked.
        """
        if not os.access(FOE, os.X_OK):
            self.skipTest(f"{FOE} is not built")
        with tempfile.TemporaryDirectory() as tmp:
            workspace, check = materialize(Path(tmp))
            configured = graphs.autonomy(workspace, check, BUDGET, task="Probe.")
            episode, _ = self.run_document(Path(tmp), "configured", configured, "accept")
            self.assertGreaterEqual(verifications(episode), 1, "the runtime verifier ran under configured")
            unverified = graphs.autonomy(workspace, check, BUDGET, "unverified", task="Probe.")
            episode, _ = self.run_document(Path(tmp), "unverified", unverified, "accept")
            self.assertEqual(fired(episode), ["survey", "implement", "assess"])
            self.assertEqual(verifications(episode), 0, "no verifier is declared under unverified")
            offered: list[list[str]] = []
            accept, _ = scripted(workspace, "accept")

            def block_on_implement(request: dict[str, Any]) -> list[dict[str, Any]]:
                if "Implement the task in the workspace" not in request["system"]:
                    return accept(request)
                offered.append([tool["name"] for tool in request["tools"]])
                return runtime_responses.call("stop", "block", {"code": "goal-unreachable", "message": "The goal cannot be reached."}) + runtime_responses.done("tool")

            path = graphs.write(unverified, Path(tmp) / "documents" / "unverified-block.json")
            status, episode = host_runtime.run(FOE, path, Path(tmp) / "logs" / "unverified-block", block_on_implement)
            self.assertEqual(status, 2, outcome(episode))
            self.assertEqual(outcome(episode), {"kind": "blocked", "code": "goal-unreachable", "message": "The goal cannot be reached."})
            self.assertEqual(len(offered), 1)
            self.assertIn("block", offered[0])
            self.assertEqual(verifications(episode), 0)


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



if __name__ == "__main__":
    unittest.main()
