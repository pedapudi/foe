#!/usr/bin/python3
"""Deterministic episodes that reach each runtime behavior the evaluation depends on, run against the built binary.

Each test runs one `contracts/graphs.py` autonomy document through
`evals/host_runtime.py`, whose responder scripts every model response, and
then reads the episode logs the binary wrote. No model and no network is
involved. Two of the behaviors are runtime repairs, and a binary built
before them fails the test that reaches each:

- a completion verifier killed at its `timeout_seconds` is a finding that
  re-fires the verified node, and the episode ends `blocked` with
  `verification-unsatisfiable` once the node's retries are spent, where it
  once ended `failed` on the first kill (docs/config.md `done_when`);
- a `bash` call that asks for more than half the seconds remaining runs for
  at most that half, and its result states the limit it received
  (docs/tools.md `bash`).

The third behavior belongs to the evaluation document rather than to the
runtime. A node that holds `check` and no edit tool is granted the
directories a check writes, `graphs.CHECK_WRITES`, and writes them without
a permission denial. The runtime opens every granted directory when the
episode starts and refuses an episode whose grant names an absent one, so
the workspace starts without those directories and the runner's own
`run.make_check_directories` creates them, as it does before every arm.

The tests need `target/debug/foe`. They skip with the reason when it is
absent; continuous integration builds the binary first, so the skip does
not occur there.

    /usr/bin/python3 evals/cross_harness/repairs_test.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "contracts"))
sys.path.insert(0, str(HERE.parent))

import conditions  # noqa: E402
import graphs  # noqa: E402
import run  # noqa: E402
import host_runtime  # noqa: E402
import runtime_responses  # noqa: E402
import trajectory  # noqa: E402

REPO = HERE.parents[1]
FOE = REPO / "target" / "debug" / "foe"

# The phrase that opens each autonomy node's role, which the responder reads
# from the request's system text to tell the nodes apart.
SURVEY, IMPLEMENT, ASSESS, REPAIR = (
    "Read the task and the workspace before anything changes",
    "Implement the task in the workspace",
    "Assess whether the workspace satisfies the task",
    "Repair the assessment's findings",
)
_LEARNED = {"claim": "The notes file holds one line.", "seq": 0}
RETURNS: dict[str, dict[str, Any]] = {
    SURVEY: {"summary": "The notes file is the whole task.", "unresolved_risks": [], "learned": [_LEARNED]},
    IMPLEMENT: {"summary": "No file needed a change.", "changed_paths": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": [_LEARNED]},
    ASSESS: {"summary": "Assessed.", "findings": [], "validation": ["The check ran."], "unresolved_risks": [], "learned": [_LEARNED], "branch": "accept"},
    REPAIR: {"summary": "Nothing to repair.", "changed_paths": [], "validation": ["The notes file was read."], "unresolved_risks": [], "learned": [_LEARNED]},
}
# One tool call a node makes before it returns: its call id, tool, and arguments.
Call = tuple[str, str, dict[str, Any]]

# The line the waiting check prints before it sleeps, in the form the
# non-terminating tasks' suites print it.
MARKER = "checks/run.sh step 2 waiting on socket"
# A wait of a minute through the system interpreter. The scripts avoid
# programs such as `sleep` and `mkdir`, which some hosts link from a
# directory outside the execute roots the documents grant.
SLEEP = "/usr/bin/python3 -c 'import time; time.sleep(60)'"


def events(log: Path) -> list[dict[str, Any]]:
    with log.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def every_event(episode: Path) -> list[dict[str, Any]]:
    """The events of the episode and of every child episode below it, each log in file order."""
    found: list[dict[str, Any]] = []
    for log in sorted(episode.rglob("episode.jsonl")):
        found.extend(events(log))
    return found


def outcome(episode: Path) -> dict[str, Any]:
    return [event for event in events(episode / "episode.jsonl") if event["type"] == "episode/end"][-1]["data"]["outcome"]


def materialize(root: Path, check_script: str) -> tuple[Path, Path]:
    """A workspace with every write root, one readable file, and an executable check script with the given body.

    The check's directories are absent until the runner's step that creates
    them before every arm runs, as they are in a materialized task workspace.
    """
    workspace = root / "workspace"
    for name in graphs.WRITE_ROOTS:
        (workspace / name).mkdir(parents=True)
    for name in graphs.CHECK_WRITES:
        assert not (workspace / name).exists(), name
    run.make_check_directories(workspace)
    (workspace / "docs" / "notes.txt").write_text("The workspace holds nothing the task changes.\n", encoding="utf-8")
    check = workspace / "checks" / "run.sh"
    check.parent.mkdir()
    check.write_text(check_script, encoding="utf-8")
    check.chmod(0o755)
    return workspace, check


def responder(workspace: Path, plans: dict[str, list[Call]]) -> tuple[host_runtime.Responder, list[str]]:
    """Responses that make each node's planned calls in order and then return its value, citing the last result.

    A node without a plan reads the notes file once, so that its value has a
    tool result to cite. The list receives the phrase of the asking node for
    every request that opened a firing, which is a request with no tool
    result in it.
    """
    opened: list[str] = []

    def respond(request: dict[str, Any]) -> list[dict[str, Any]]:
        phrase = next((phrase for phrase in RETURNS if phrase in request["system"]), None)
        if phrase is None:
            return [{"kind": "error", "message": "the request's system text names no autonomy node", "retryable": False}]
        results = [message for message in request["messages"] if message.get("role") == "tool"]
        if not results:
            opened.append(phrase)
        plan = plans.get(phrase) or [(f"read-notes-{phrase[:6]}", "read", {"path": str(workspace / "docs" / "notes.txt")})]
        if len(results) < len(plan):
            call_id, name, args = plan[len(results)]
            return runtime_responses.call(call_id, name, args) + runtime_responses.done("tool")
        cited = re.search(r"\[seq (\d+)\]", json.dumps(results[-1]))
        if cited is None:
            return [{"kind": "error", "message": "the tool result carries no [seq N] prefix", "retryable": False}]
        value = json.loads(json.dumps(RETURNS[phrase]))
        value["learned"][0]["seq"] = int(cited.group(1))
        return runtime_responses.call(f"return-{phrase[:6]}", "return", {"value": value}) + runtime_responses.done("tool")

    return respond, opened


def contracts(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The root and every node contract of an autonomy document."""
    return [document, *(node["model"] for node in document["workflow"]["nodes"].values())]


def with_check_timeout(document: dict[str, Any], seconds: int) -> dict[str, Any]:
    """The document with every declaration of the check tool limited to `seconds`."""
    for contract in contracts(document):
        if graphs.CHECK in contract.get("tool_defs", {}):
            contract["tool_defs"][graphs.CHECK]["timeout_seconds"] = seconds
    return document


class Case(unittest.TestCase):
    def setUp(self) -> None:
        if not os.access(FOE, os.X_OK):
            self.skipTest(f"{FOE} is not built; build it with `cargo build -p foe-cli` before these tests")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_episode(self, name: str, document: dict[str, Any], respond: host_runtime.Responder) -> tuple[int, Path]:
        path = graphs.write(document, self.root / "documents" / f"{name}.json")
        return host_runtime.run(FOE, path, self.root / "logs" / name, respond)


class VerifierTimeout(Case):
    """docs/config.md `done_when`: a verifier killed at its timeout is one finding, the node re-fires, and spent retries end the episode blocked."""

    CHECK_SECONDS = 2

    def test_a_check_that_outlives_its_timeout_re_fires_the_node_and_ends_blocked(self) -> None:
        # The check prints the wait marker, as the non-terminating suites do, then waits past its limit.
        workspace, check = materialize(self.root, f'#!/bin/sh\necho "{MARKER}" >&2\nexec {SLEEP}\n')
        document = with_check_timeout(graphs.autonomy(workspace, check, {"model_calls": 40, "seconds": 300}, task="Probe."), self.CHECK_SECONDS)
        respond, opened = responder(workspace, {})
        status, episode = self.run_episode("verifier-timeout", document, respond)
        # Event `episode/end`: the episode ended blocked by the spent retries rather than failed by the first kill.
        end = outcome(episode)
        self.assertEqual((end["kind"], end.get("code"), status), ("blocked", "verification-unsatisfiable", 2), end)
        # Event `verification/result`: one per firing of the verified node, each a finding that names the timeout.
        verifications = [event["data"] for event in every_event(episode) if event["type"] == "verification/result"]
        self.assertEqual(len(verifications), graphs.NODE_RETRIES + 1, verifications)
        for verification in verifications:
            self.assertEqual((verification["tool"], verification["status"]), (graphs.CHECK, "findings"), verification)
            self.assertEqual(len(verification["findings"]), 1, verification)
            self.assertIn(f"did not finish within {self.CHECK_SECONDS} seconds and was killed", verification["findings"][0])
            self.assertNotIn("error", verification)
        # The implementing node opened one firing for its first run and one per retry.
        self.assertEqual(opened, [SURVEY] + [IMPLEMENT] * (graphs.NODE_RETRIES + 1))
        # The wait-entry reader requires the marker in recorded output. The
        # check printed it, and the runtime's timeout finding states the
        # bound and none of the output, so entry is not established.
        outputs = conditions.foe_suite_outputs(episode)
        self.assertEqual(len(outputs), graphs.NODE_RETRIES + 1, outputs)
        self.assertTrue(all(item["source"].endswith("runtime verification") for item in outputs), outputs)
        entered = trajectory.wait_entry(outputs, MARKER)
        self.assertEqual((entered["entered"], entered["status"]), (False, trajectory.NOT_ESTABLISHED), entered["evidence"])


class CheckWrites(Case):
    """graphs.CHECK_WRITES: a node that holds `check` and no edit tool can write the directories a check writes."""

    # Creates a directory and a file under each of the two, and rewrites the
    # lock file when it exists, as a build does; prints a finding with the
    # refusal otherwise. `{workspace}` is filled with the absolute path.
    CHECK = """#!/usr/bin/python3
import pathlib

workspace = pathlib.Path("{workspace}")
try:
    (workspace / "target" / "debug").mkdir(exist_ok=True)
    (workspace / "target" / "debug" / ".cargo-lock").write_text("lock\\n", encoding="utf-8")
    (workspace / ".check-tmp" / "build").mkdir(exist_ok=True)
    (workspace / ".check-tmp" / "build" / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\\n", encoding="utf-8")
except OSError as error:
    print(f"the check could not write its directories: {{error}}")
"""

    def assessing_check(self, name: str, document: dict[str, Any], workspace: Path) -> dict[str, Any]:
        """Run the document with the assessing node calling `check` once; the `tool/result` event of that call."""
        respond, _ = responder(workspace, {ASSESS: [("assess-check", graphs.CHECK, {"args": []})]})
        status, episode = self.run_episode(name, document, respond)
        results = [event["data"] for event in every_event(episode) if event["type"] == "tool/result" and event["data"]["call_id"] == "assess-check"]
        self.assertEqual(len(results), 1, f"{name}: {outcome(episode)}")
        return results[0]

    def check_writing(self) -> tuple[Path, Path]:
        workspace, check = materialize(self.root, "")
        check.write_text(self.CHECK.format(workspace=workspace), encoding="utf-8")
        return workspace, check

    def test_the_assessing_node_writes_the_check_directories_without_a_denial(self) -> None:
        # The runner creates exactly the directories the document grants.
        self.assertEqual(set(run.CHECK_DIRECTORIES), set(graphs.CHECK_WRITES))
        workspace, check = self.check_writing()
        document = graphs.autonomy(workspace, check, {"model_calls": 40, "seconds": 300}, task="Probe.")
        assess = document["workflow"]["nodes"]["assess"]["model"]
        self.assertNotIn("edit", assess["tools"])
        self.assertEqual(assess["grants"]["write"], [str(workspace / name) for name in graphs.CHECK_WRITES])
        result = self.assessing_check("check-writes", document, workspace)
        # Event `tool/result` of the assessing node's `check` call: exit 0, no finding, no refusal.
        self.assertFalse(result["is_error"], result)
        self.assertEqual((result["value"]["exit_code"], result["value"]["stdout"], result["value"]["stderr"]), (0, "", ""), result)
        self.assertNotIn("Permission denied", result.get("rendered") or "")
        self.assertEqual((workspace / "target" / "debug" / ".cargo-lock").read_text(encoding="utf-8"), "lock\n")
        self.assertTrue((workspace / ".check-tmp" / "build" / "CACHEDIR.TAG").is_file())

    def test_without_the_check_directories_the_same_call_is_refused(self) -> None:
        """The control: the grant is what makes the write succeed, so removing it from the assessing node brings the refusal back."""
        workspace, check = self.check_writing()
        document = graphs.autonomy(workspace, check, {"model_calls": 40, "seconds": 300}, task="Probe.")
        del document["workflow"]["nodes"]["assess"]["model"]["grants"]["write"]
        result = self.assessing_check("check-writes-withheld", document, workspace)
        # Event `tool/result`: the check ran and reported the refused write as its finding.
        self.assertEqual(result["value"]["exit_code"], 0, result)
        self.assertIn("the check could not write its directories", result["value"]["stdout"])
        self.assertRegex(result["value"]["stdout"], r"Permission denied|Read-only file system|Operation not permitted")


class CommandTimeout(Case):
    """docs/tools.md `bash`: a command receives at most half the seconds remaining when it starts, and its result states the limit."""

    SECONDS = 12
    REQUESTED = 60

    def test_a_command_asking_for_more_than_half_the_remaining_seconds_is_limited_and_says_so(self) -> None:
        workspace, check = materialize(self.root, "#!/bin/sh\nexit 0\n")
        document = graphs.autonomy(workspace, check, {"model_calls": 40, "seconds": self.SECONDS}, task="Probe.")
        call: Call = ("long-sleep", "bash", {"command": SLEEP, "timeout_seconds": self.REQUESTED})
        respond, _ = responder(workspace, {SURVEY: [call]})
        _, episode = self.run_episode("command-timeout", document, respond)
        results = [event for event in every_event(episode) if event["type"] == "tool/result" and event["data"]["call_id"] == "long-sleep"]
        self.assertEqual(len(results), 1, outcome(episode))
        data = results[0]["data"]
        # Event `tool/result` of the `bash` call: killed at the limit, which the rendering states beside the request.
        self.assertTrue(data["value"]["timed_out"], data)
        stated = re.search(r"\[command timeout: requested (\d+)s; (\d+)s remained at launch; limited to (\d+)s\]", data["rendered"])
        self.assertIsNotNone(stated, data["rendered"])
        requested, remained, given = (int(group) for group in stated.groups())
        self.assertEqual(requested, self.REQUESTED)
        self.assertLessEqual(remained, self.SECONDS)
        self.assertEqual(given, remained // 2)
        self.assertLess(data["duration_ms"], self.SECONDS * 1000 / 2 + 1000, data)


if __name__ == "__main__":
    unittest.main()
