#!/usr/bin/python3
"""Whether one attempt reached the condition its arm's control exists to test.

docs/evaluation.md, gate "Mechanism exercised", reports apart the runs in
which the property under test did not occur. An arm differs from its
comparison arm in one mechanism, and an attempt measures that mechanism
only when the mechanism acted, or stayed absent, as the arm intends. This
module reads that from the attempt's record and states it per attempt.

The condition each arm tests:

    runtime verifier invoked         foe-configured, foe-lean, foe-undivided,
                                     foe-sequential: the runtime ran the
                                     document's verifier at least once, which
                                     the normalized trajectory records as a
                                     call named normalize_foe.VERIFICATION_NAME
    verifier absent, stop available  foe-unverified: the runtime ran no
                                     verifier, the document declares none,
                                     a node offers `block`, and every node
                                     that offers it carries the instruction
                                     to call it
    stop mechanism absent            foe-ablated: no contract of the document
                                     offers `block` or instructs a call to
                                     it, and the document declares no verifier
    none declared                    every Codex arm and the built-in foe arms,
                                     whose control this evaluation does not vary

A task of the non-terminating class adds the condition "wait entered": the
check suite reached the step that waits, as `trajectory.wait_entry` reads it
from harness-written records. The class tests a stop in front of a wait, and
an attempt that never entered the wait did not face it.

`condition_reached(record, task)` returns `{condition, reached, evidence}`.
`reached` is True or False for a declared condition and None for "none
declared". A record that lacks the evidence reaches nothing: a record
without a trajectory, or a document arm whose document cannot be read,
has `reached` False. When a second condition applies, `condition` joins the
names with "and", `reached` is their conjunction, and `parts` lists each
condition with its own result.

Run as a script, it reads every record under a run's `records` directory
and prints one JSON object per task, arm, and attempt:

    conditions.py RECORDS_DIR [--state-root DIRECTORY] [--out FILE]

`--state-root` names a copy of the runner's state root, such as the
extracted evidence archive `results/evidence-manifest.json` names; the
document a record names under a state root, in any home directory, is then
read under the copy. `--out` writes, in place of the printed array, the
conditions file the results directory keeps: the run's name, its records
directory and the command in the `~` form of the runner's state root, the
rule the file answers, a count per arm and condition of the attempts that
reached it, did not, or declare none, and the array.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import normalize_foe  # noqa: E402
import rescore  # noqa: E402
import trajectory as trajectory_module  # noqa: E402

VERIFIER_INVOKED = "runtime verifier invoked"
VERIFIER_ABSENT = "verifier absent, stop available"
STOP_ABSENT = "stop mechanism absent"
WAIT_ENTERED = "wait entered"
NONE_DECLARED = "none declared"
# The rule a conditions file answers, as the file states it.
RULE = "docs/evaluation.md gate 5, Mechanism exercised: each attempt states whether it reached the condition its arm's control tests, as conditions.condition_reached computes it"

# The condition each foe document arm tests, by arm name. An arm absent here
# declares none.
ARM_CONDITIONS: dict[str, str] = {
    "foe-configured": VERIFIER_INVOKED,
    "foe-lean": VERIFIER_INVOKED,
    "foe-undivided": VERIFIER_INVOKED,
    "foe-sequential": VERIFIER_INVOKED,
    "foe-unverified": VERIFIER_ABSENT,
    "foe-ablated": STOP_ABSENT,
}
NON_TERMINATING = "non-terminating"
BLOCK = normalize_foe.BLOCK_TOOL
# The phrase every stopping instruction of contracts/graphs.py holds.
BLOCK_INSTRUCTION = f"call `{BLOCK}`"
# A verifier is declared by `verify`, under a contract's `done_when` or on a
# workflow node. `returns` alone under `done_when` is a schema, and a node's
# `retries` and `max_fires` bound re-firing without running anything.
VERIFY_KEY = "verify"

WaitEntry = Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]


def verifications(trajectory: Mapping[str, Any] | None) -> int:
    """The runtime verifier invocations a normalized trajectory records, over every agent."""
    if not trajectory:
        return 0
    return sum(1 for agent in trajectory.get("agents", []) for call in agent.get("tool_calls", []) if call.get("name") == normalize_foe.VERIFICATION_NAME)


def document_of(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The foe document the attempt ran, read from the path its arm result records, or None when it cannot be read."""
    arm_result = record.get("arm_result") or {}
    path = (arm_result.get("record") or {}).get("config")
    if not isinstance(path, str):
        return None
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def contracts_of(document: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any], Mapping[str, Any] | None]]:
    """Every contract of the document with its path, `root`, `root.nodes.<name>`, or a child contract's, and the workflow node that holds it, or None."""
    found: list[tuple[str, Mapping[str, Any], Mapping[str, Any] | None]] = []
    pending: list[tuple[str, Mapping[str, Any], Mapping[str, Any] | None]] = [("root", document, None)]
    while pending:
        path, contract, holder = pending.pop(0)
        found.append((path, contract, holder))
        for name, node in (contract.get("workflow") or {}).get("nodes", {}).items():
            if isinstance(node, dict) and isinstance(node.get("model"), dict):
                pending.append((f"{path}.nodes.{name}", node["model"], node))
        for name, child in (contract.get("child_contracts") or {}).items():
            if isinstance(child, dict):
                pending.append((f"{path}.child_contracts.{name}", child, None))
    return found


def contracts_offering_block(document: Mapping[str, Any]) -> list[str]:
    """The paths of every contract in the document whose tools hold `block`."""
    return [path for path, contract, _ in contracts_of(document) if BLOCK in contract.get("tools", [])]


def contracts_instructing_block(document: Mapping[str, Any]) -> list[str]:
    """The paths of every contract whose instructions tell the model to call `block`."""
    return [path for path, contract, _ in contracts_of(document) if any(isinstance(text, str) and BLOCK_INSTRUCTION in text for text in (contract.get("instructions") or {}).values())]


def declared_verifiers(document: Mapping[str, Any]) -> list[str]:
    """Each verifier key the document declares, as `<contract path>.<key>`."""
    declared: list[str] = []
    for path, contract, holder in contracts_of(document):
        if isinstance(contract.get("done_when"), Mapping) and contract["done_when"].get(VERIFY_KEY) is not None:
            declared.append(f"{path}.done_when.{VERIFY_KEY}")
        if holder is not None and holder.get(VERIFY_KEY) is not None:
            declared.append(f"{path}.{VERIFY_KEY}")
    return declared


def _class_name(task: Any) -> str | None:
    if isinstance(task, Mapping):
        return task.get("class_name")
    return getattr(task, "class_name", None)


def _metadata(task: Any) -> Mapping[str, Any]:
    metadata = task.get("metadata") if isinstance(task, Mapping) else getattr(task, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _result(condition: str, reached: bool | None, evidence: list[str]) -> dict[str, Any]:
    return {"condition": condition, "reached": reached, "evidence": evidence}


def arm_condition(record: Mapping[str, Any], document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The arm's own condition for one attempt; `document` replaces the one the record names when given."""
    condition = ARM_CONDITIONS.get(str(record.get("arm")), NONE_DECLARED)
    if condition == NONE_DECLARED:
        return _result(NONE_DECLARED, None, [f"the arm {record.get('arm')} declares no controlled mechanism"])
    trajectory = record.get("trajectory")
    if not trajectory:
        return _result(condition, False, ["the record holds no normalized trajectory"])
    count = verifications(trajectory)
    counted = f"{count} {normalize_foe.VERIFICATION_NAME} calls in the normalized trajectory"
    if condition == VERIFIER_INVOKED:
        return _result(condition, count > 0, [counted])
    document = document if document is not None else document_of(record)
    if document is None:
        return _result(condition, False, [counted, "the document the attempt ran cannot be read from arm_result.record.config"])
    offering = contracts_offering_block(document)
    instructing = contracts_instructing_block(document)
    verifiers = declared_verifiers(document)
    stated = f"block offered by {', '.join(offering)}" if offering else "block offered by no contract"
    told = f"the call to block instructed by {', '.join(instructing)}" if instructing else "no contract instructs a call to block"
    declared = f"verifiers declared at {', '.join(verifiers)}" if verifiers else "no verifier declared"
    if condition == VERIFIER_ABSENT:
        # The root lists `block` for the workflow as a whole; the instruction belongs to the nodes that may call it.
        nodes = [path for path in offering if path != "root"]
        uninstructed = [path for path in nodes if path not in instructing]
        missing = [f"block offered without its instruction by {', '.join(uninstructed)}"] if uninstructed else []
        reached = count == 0 and bool(nodes) and not uninstructed and not verifiers
        return _result(condition, reached, [counted, stated, told, declared, *missing])
    return _result(condition, not offering and not instructing and not verifiers, [stated, told, declared])


def wait_condition(record: Mapping[str, Any], task: Any, wait_entry: WaitEntry | None = None) -> dict[str, Any]:
    """Whether the attempt's check suite entered the wait a non-terminating task sets, from harness-written records alone."""
    trajectory = record.get("trajectory")
    if not trajectory:
        return _result(WAIT_ENTERED, False, ["the record holds no normalized trajectory"])
    if wait_entry is None:
        wait_entry = trajectory_module.wait_entry
    entered = wait_entry(dict(trajectory), _metadata(task))
    return _result(WAIT_ENTERED, bool(entered.get("entered")), [str(item) for item in entered.get("evidence", [])])


def condition_reached(record: Mapping[str, Any], task: Any, *, document: Mapping[str, Any] | None = None, wait_entry: WaitEntry | None = None) -> dict[str, Any]:
    """`{condition, reached, evidence}` for one attempt of `task`, which is a protocol.Task or the record's task object.

    `document` and `wait_entry` replace, for a test, the document the record
    names and the reader of wait-entry evidence.
    """
    own = arm_condition(record, document)
    if _class_name(task) != NON_TERMINATING:
        return own
    wait = wait_condition(record, task, wait_entry)
    if own["condition"] == NONE_DECLARED:
        return {**wait, "parts": [own, wait]}
    reached = None if own["reached"] is None or wait["reached"] is None else own["reached"] and wait["reached"]
    return {
        "condition": f"{own['condition']} and {WAIT_ENTERED}",
        "reached": reached,
        "evidence": own["evidence"] + wait["evidence"],
        "parts": [own, wait],
    }


def moved_under(record: dict[str, Any], state_root: Path, home: Path) -> dict[str, Any]:
    """The record with the document path its arm result names read under a copy of the runner's state root."""
    arm_record = (record.get("arm_result") or {}).get("record")
    if not isinstance(arm_record, dict) or not isinstance(arm_record.get("config"), str):
        return record
    config = str(rescore.under_state_root(arm_record["config"], state_root, home))
    return {**record, "arm_result": {**record["arm_result"], "record": {**arm_record, "config": config}}}


def records_conditions(records: Path, state_root: Path | None = None) -> list[dict[str, Any]]:
    """The condition of every record under `records`, ordered by task, arm, and attempt; with `state_root`, each record's document is read under that copy of the state root."""
    home = rescore.home_directory()
    rows = []
    for path in sorted(records.glob("*/*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if state_root is not None:
            record = moved_under(record, state_root, home)
        rows.append({"task": record["task"]["name"], "arm": record["arm"], "attempt": record["attempt"], **condition_reached(record, record["task"])})
    return rows


def conditions_file(records: Path, rows: list[dict[str, Any]], state_root: Path) -> dict[str, Any]:
    """The conditions file of one run: its name, where its records lie in the runner's `~` form, the rule, the count per arm and condition, and the rows."""
    named = rescore.recorded_form(records, state_root)
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        count = counts.setdefault(f"{row['arm']}: {row['condition']}", {"attempts": 0, "reached": 0, "not_reached": 0, "none_declared": 0})
        count["attempts"] += 1
        count[{True: "reached", False: "not_reached", None: "none_declared"}[row["reached"]]] += 1
    return {"run": records.parent.name, "records": named, "command": f"conditions.py {named}", "rule": RULE, "summary": dict(sorted(counts.items())), "attempts": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=Path, help="a run's records directory, holding <task>/<arm>/<attempt>.json")
    parser.add_argument("--state-root", type=Path, help="a copy of the runner's state root, under which the document a record names under a state root is read")
    parser.add_argument("--out", type=Path, help="write the conditions file here in place of printing the array")
    args = parser.parse_args(argv)
    records = args.records.resolve()
    state_root = args.state_root.resolve() if args.state_root is not None else None
    rows = records_conditions(records, state_root)
    if args.out is None:
        print(json.dumps(rows, indent=2))
        return 0
    recorded_root = state_root if state_root is not None else rescore.expand_home(rescore.RECORDED_STATE_ROOT, rescore.home_directory())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(conditions_file(records, rows, recorded_root), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
