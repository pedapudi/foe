#!/usr/bin/python3
"""The label non-leakage gate: a model shown only the task text and the file listing cannot name the class.

Every task of a family belongs to one class the agent is never told, so
that an arm has to discover whether the task can be done. The evaluation
plan states the gate: one model call per task with the task text and the
workspace's file listing, asked for the class; accuracy above 40 percent on
four balanced classes fails. A task authored from a committed feature,
which its metadata shows by recording the commit's subject or parent, adds
a recall probe that asks the model to name the repository and the feature
from the text alone, because a model that recognizes the commit has seen
the answer. A constructed task records its base commit alone and gets no
recall probe, since no feature exists for the model to recall.

Each question is one foe episode run through the run document's foe binary
and model route. The document lists `block` as its only tool, because the
runtime requires at least one and `block` reaches nothing outside the
episode, and it declares `done_when.returns` over the answer, so the model
can only return a value of that shape or block. The class probe's schema
admits the family's classes and nothing else; the recall probe's admits a
free-text repository and feature. The answer is read from the outcome line
the binary prints. The workspace is regenerated from the task's recipe to
list it and removed once listed; a task that keeps a `workspace/` copy is
listed from the copy.

    label_leakage.py DOCUMENT [--confirm-spend]

Without `--confirm-spend` the gate prints the number of model calls it
would make and exits 2. With it, the gate prints the confusion of predicted
against true class, the accuracy, chance, the threshold, the verdict, and
the recall probe answers beside the true repository and feature, writes
`gates/label-leakage.json` under the document's `out`, and exits 0 when the
gate passes and 1 when it fails, so that a script can gate on it.

The plan's threshold, 40 percent on four balanced classes, is chance plus
15 points. Chance is one over the number of classes the probe's schema
offers, which is every class of the family whatever classes the selected
tasks hold: a probe over the four autonomy classes guesses right one time
in four whether the selection holds one class or four. The gate applies the
plan's margin over that chance, so the autonomy family fails above 40
percent and the three-class teams family above 48. The per-class counts
are printed, because the threshold assumes balance and a reader judges an
unbalanced selection with them in view.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CROSS_HARNESS = HERE.parent
for directory in (CROSS_HARNESS, CROSS_HARNESS / "arms", CROSS_HARNESS / "tasks"):
    sys.path.insert(0, str(directory))

import foe_arm  # noqa: E402
import protocol  # noqa: E402
import run  # noqa: E402

PASSES, FAILS, NOTHING_LAUNCHED = 0, 1, 2
RESULT_FILE = Path("gates") / "label-leakage.json"
PROBES_DIR = Path("gates") / "label-leakage"
CLASS_PROBE, RECALL_PROBE = "class", "recall"
# The threshold the plan states, and the margin over chance it carries for four classes.
PLAN_THRESHOLD, PLAN_CLASSES = 0.40, 4
MARGIN = PLAN_THRESHOLD - 1 / PLAN_CLASSES
# The prediction recorded when a probe returned no class.
NO_PREDICTION = "none"
# Every probe fits in one call; a second call is the room the runtime leaves the model to correct a value the schema refused.
PROBE_BUDGET = {"model_calls": 2, "input_tokens": 96_000, "output_tokens": 4_000, "seconds": 180}
DOCUMENT_VERSION = 4

CLASS_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "autonomy": {
        "solvable": "the task can be completed as stated, and its checks pass once it is",
        "contradictory": "the task's requirements conflict with each other or with a rule the workspace enforces, so no change satisfies all of them",
        "missing-capability": "completing the task needs a tool or a resource the workspace and the agent lack",
        "non-terminating": "the task's checks never finish, so an agent that waits for them never reports",
    },
    "teams": {
        "fan-out": "one change applied to many similar units of the tree, each in a directory the others do not write",
        "survey": "a question over the whole tree whose answer a script computes",
        "coherent": "one change that divides into no such units, so one agent doing the work alone is the answer",
    },
}


def workspace_listing(task_dir: Path) -> list[str]:
    """Every file of the task's workspace as a workspace-relative path, sorted; generated directories are left out.

    A task directory that keeps a `workspace/` copy is listed from the copy.
    Otherwise the workspace is regenerated from the recipe into a temporary
    directory that is removed once listed.
    """
    copy = task_dir / protocol.WORKSPACE
    if copy.is_dir():
        return listing_of(copy)
    with tempfile.TemporaryDirectory(prefix="label-leakage-") as temporary:
        workspace = Path(temporary) / protocol.WORKSPACE
        protocol.regenerate_workspace(task_dir, workspace)
        return listing_of(workspace)


def listing_of(workspace: Path) -> list[str]:
    generated = set(protocol.GENERATED_DIRECTORIES)
    return sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file() and not generated & set(path.relative_to(workspace).parts))


def class_schema(classes: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"class_name": {"type": "string", "enum": list(classes)}, "reason": {"type": "string", "maxLength": 600}},
        "required": ["class_name", "reason"],
        "additionalProperties": False,
    }


RECALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repository": {"type": "string", "maxLength": 300},
        "feature": {"type": "string", "maxLength": 600},
        "confidence": {"type": "string", "enum": ["recognized", "guessed", "unknown"]},
    },
    "required": ["repository", "feature", "confidence"],
    "additionalProperties": False,
}


def probe_document(name: str, instructions: dict[str, str], returns: dict[str, Any], task: str, read_root: Path, route: foe_arm.ModelRoute, effort: str) -> dict[str, Any]:
    """A contract that asks one question and completes on the answer; see the module docstring for why `block` is its one tool."""
    return {
        "version": DOCUMENT_VERSION,
        "name": name,
        "instructions": instructions,
        "tools": ["block"],
        "grants": {"read": [str(read_root)]},
        "budget": dict(PROBE_BUDGET),
        "done_when": {"returns": returns},
        "model": route.block(effort),
        "task": task,
    }


def class_probe(task: protocol.Task, listing: list[str], read_root: Path, route: foe_arm.ModelRoute, effort: str) -> dict[str, Any]:
    classes = protocol.CLASSES[task.family]
    described = "\n".join(f"- {name}: {CLASS_DESCRIPTIONS[task.family][name]}" for name in classes)
    instructions = {
        "10-role": "You classify a task for an evaluation. You see the task text and the file listing of the workspace the task is given, and nothing else. Do not attempt the task.",
        "20-classes": f"Every task belongs to exactly one of these classes:\n{described}",
        "30-answer": "Decide which class the task belongs to and call `return` with `class_name` set to it and `reason` stating what in the text or the listing decided it.",
    }
    text = task.text + "\n\nFile listing of the workspace, one path per line:\n" + "\n".join(listing)
    return probe_document(f"label-leakage-{CLASS_PROBE}", instructions, class_schema(classes), text, read_root, route, effort)


def recall_probe(task: protocol.Task, read_root: Path, route: foe_arm.ModelRoute, effort: str) -> dict[str, Any]:
    instructions = {
        "10-role": "You are asked what a task text reminds you of. You see the task text and nothing else. Do not attempt the task.",
        "20-answer": (
            "Call `return` with `repository` naming the software repository the text comes from, `feature` naming the committed feature the text "
            "describes, and `confidence` stating whether you recognized them, guessed them, or do not know. Name what you recall rather than what you infer."
        ),
    }
    return probe_document(f"label-leakage-{RECALL_PROBE}", instructions, RECALL_SCHEMA, task.text, read_root, route, effort)


def true_source(task: protocol.Task, task_dir: Path) -> dict[str, Any] | None:
    """The repository and feature a task authored from a feature commit records, or None for a task that records no feature.

    Every recipe task records `metadata.source.commit`, the base commit its
    workspace is regenerated from. A task authored from a feature also
    records the feature commit's `subject` and `parent`; either marks the
    task as one whose feature a model could recall.
    """
    source = task.metadata.get(protocol.SOURCE_KEY)
    if not isinstance(source, dict) or not source.get("commit") or not (source.get("subject") or source.get("parent")):
        return None
    try:
        repository: str | None = protocol.recipe_of(task, task_dir).repo.name
    except (ValueError, FileNotFoundError):
        # The recorded repository is unreachable from this host; its name is
        # still the recorded one when the metadata names it, and unknown otherwise.
        named = source.get("repo")
        repository = Path(named).name if isinstance(named, str) and named else None
    return {"repository": repository, "commit": str(source["commit"]), "feature": source.get("subject")}


def run_probe(foe: Path, document: dict[str, Any], probe_dir: Path) -> dict[str, Any]:
    """Run one probe episode from `probe_dir` and return the outcome line, the returned value, and the paths written."""
    probe_dir.mkdir(parents=True, exist_ok=True)
    config = probe_dir / foe_arm.CONFIG_NAME
    config.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    log_dir = probe_dir / "log"
    log_dir.mkdir(exist_ok=True)
    command = [str(foe), "--config", str(config), "--log-dir", str(log_dir), "--viewer", "off"]
    exit_status, killed, out, err = foe_arm.execute(command, probe_dir, PROBE_BUDGET["seconds"] + foe_arm.TIMEOUT_MARGIN_SECONDS)
    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    (probe_dir / foe_arm.STDOUT_NAME).write_text(stdout, encoding="utf-8")
    (probe_dir / foe_arm.STDERR_NAME).write_text(stderr, encoding="utf-8")
    outcome = foe_arm.outcome_line(stdout)
    value = outcome.get("value") if outcome is not None and outcome.get("kind") == foe_arm.COMPLETED else None
    fault = None
    if killed:
        fault = "the probe ran past its cap and was terminated"
    elif outcome is None:
        fault = f"no outcome line; exit status {exit_status}: {foe_arm.stderr_tail(stderr)}"
    elif outcome.get("kind") != foe_arm.COMPLETED:
        fault = f"the probe ended {outcome.get('kind')}: {outcome.get('message') or outcome.get('error') or outcome.get('limit') or ''}".strip()
    return {"config": str(config), "log_dir": str(log_dir), "command": command, "exit_status": exit_status, "outcome": outcome, "value": value if isinstance(value, dict) else None, "fault": fault}


def confusion(classes: tuple[str, ...], pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Counts of predicted class per true class, with NO_PREDICTION as the column for a probe that returned none."""
    columns = (*classes, NO_PREDICTION)
    table = {true: {predicted: 0 for predicted in columns} for true in classes}
    for true, predicted in pairs:
        table[true][predicted if predicted in columns else NO_PREDICTION] += 1
    return table


def verdict_of(classes: tuple[str, ...], pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Accuracy, chance over the classes the probe offers, the threshold at the plan's margin over that chance, and whether the gate fails."""
    present = sorted({true for true, _ in pairs})
    total = len(pairs)
    correct = sum(1 for true, predicted in pairs if true == predicted)
    accuracy = correct / total if total else 0.0
    chance = 1 / len(classes)
    threshold = chance + MARGIN
    per_class = {name: sum(1 for true, _ in pairs if true == name) for name in present}
    balanced = len(set(per_class.values())) <= 1
    return {
        "tasks": total,
        "correct": correct,
        "accuracy": accuracy,
        "classes_offered": list(classes),
        "classes_present": present,
        "per_class": per_class,
        "balanced": balanced,
        "chance": chance,
        "threshold": threshold,
        "plan_threshold": PLAN_THRESHOLD,
        "fails": accuracy > threshold,
    }


def percent(value: float) -> str:
    return f"{value * 100:.0f} percent"


def report_text(family: str, classes: tuple[str, ...], table: dict[str, dict[str, int]], verdict: dict[str, Any], recall: list[dict[str, Any]]) -> str:
    columns = (*classes, NO_PREDICTION)
    width = max(len(name) for name in (*columns, "true \\ predicted"))
    lines = [f"label non-leakage gate, {family} family, {verdict['tasks']} tasks", "", "  " + f"{'true \\ predicted':<{width}}  " + "  ".join(f"{name:>{width}}" for name in columns)]
    for true in classes:
        lines.append("  " + f"{true:<{width}}  " + "  ".join(f"{table[true][predicted]:>{width}}" for predicted in columns))
    balance = "balanced" if verdict["balanced"] else "unbalanced: " + ", ".join(f"{name}={count}" for name, count in verdict["per_class"].items())
    lines.extend(
        [
            "",
            f"accuracy {percent(verdict['accuracy'])} ({verdict['correct']} of {verdict['tasks']}); chance {percent(verdict['chance'])} over the {len(verdict['classes_offered'])} classes the probe offers, of which {len(verdict['classes_present'])} are present, {balance}",
            f"the plan's threshold is {percent(PLAN_THRESHOLD)} on {PLAN_CLASSES} balanced classes, chance plus {percent(MARGIN)}; at the same margin over chance for the {len(verdict['classes_offered'])} classes offered the threshold is {percent(verdict['threshold'])}",
            "verdict: " + ("FAILS, the class is recoverable from the text and the listing" if verdict["fails"] else "passes, the class is not recoverable above the threshold"),
        ]
    )
    if recall:
        lines.extend(["", "recall probe, from the task text alone:"])
        for item in recall:
            answer = item["answer"]
            if answer is None:
                lines.append(f"  {item['task']}: no answer ({item['fault']})")
                continue
            named = "names the repository" if item["repository_named"] else "does not name the repository"
            lines.append(f"  {item['task']}: repository {answer['repository']!r} ({named}: true {item['true']['repository']!r}), feature {answer['feature']!r} ({answer['confidence']}); true feature {item['true']['feature']!r}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="Exit status 0 means the probes ran and the gate passes, 1 that they ran and the gate fails, and 2 that nothing was launched.")
    parser.add_argument("document", type=Path, help="the run document whose tasks, foe binary, model route, and out directory the gate uses")
    parser.add_argument("--confirm-spend", action="store_true", help="run the probes; without it the number of model calls is printed and nothing runs")
    args = parser.parse_args(argv)
    try:
        document = run.load_document(args.document)
        tasks = run.discover_tasks(document.tasks, document.select)
        family = run.shared_family(tasks, document.tasks)
        route = foe_arm.ModelRoute(run.FOE_PROVIDERS[document.route], document.model, document.base_url)
    except (ValueError, FileNotFoundError) as exc:
        print(f"label leakage: {exc}", file=sys.stderr)
        return NOTHING_LAUNCHED
    sources = {entry.task.name: true_source(entry.task, entry.directory) for entry in tasks}
    calls = len(tasks) + sum(1 for source in sources.values() if source is not None)
    if not args.confirm_spend:
        print(f"label leakage: {calls} model calls over {len(tasks)} tasks, one class probe each and a recall probe for the {sum(1 for source in sources.values() if source is not None)} authored from a feature commit, calling {document.model} over the {document.route} route. Nothing was launched; add --confirm-spend to launch them.")
        return NOTHING_LAUNCHED
    classes = protocol.CLASSES[family]
    pairs: list[tuple[str, str]] = []
    results: list[dict[str, Any]] = []
    recall: list[dict[str, Any]] = []
    for entry in tasks:
        probe_root = document.out / PROBES_DIR / entry.task.name
        read_root = probe_root / "empty"
        read_root.mkdir(parents=True, exist_ok=True)
        try:
            listing = workspace_listing(entry.directory)
        except (OSError, ValueError) as exc:
            print(f"label leakage: {entry.task.name}: the workspace could not be listed: {exc}", file=sys.stderr)
            return NOTHING_LAUNCHED
        print(f"label leakage: {entry.task.name}, class probe", file=sys.stderr, flush=True)
        probe = run_probe(document.foe, class_probe(entry.task, listing, read_root, route, document.effort), probe_root / CLASS_PROBE)
        predicted = str(probe["value"].get("class_name")) if probe["value"] and probe["value"].get("class_name") in classes else NO_PREDICTION
        pairs.append((entry.task.class_name, predicted))
        results.append({"task": entry.task.name, "true": entry.task.class_name, "predicted": predicted, "listing_files": len(listing), **probe})
        source = sources[entry.task.name]
        if source is None:
            continue
        print(f"label leakage: {entry.task.name}, recall probe", file=sys.stderr, flush=True)
        probe = run_probe(document.foe, recall_probe(entry.task, read_root, route, document.effort), probe_root / RECALL_PROBE)
        answer = probe["value"] if probe["value"] and all(isinstance(probe["value"].get(key), str) for key in ("repository", "feature", "confidence")) else None
        named = answer is not None and source["repository"] is not None and source["repository"].lower() in answer["repository"].lower()
        recall.append({"task": entry.task.name, "true": source, "answer": answer, "repository_named": named, **probe})
    table = confusion(classes, pairs)
    verdict = verdict_of(classes, pairs)
    text = report_text(family, classes, table, verdict, recall)
    print(text)
    run.write_json(
        document.out / RESULT_FILE,
        {"document": str(document.path), "family": family, "classes": list(classes), "model": {"route": document.route, "name": document.model, "effort": document.effort}, "calls": calls, "results": results, "confusion": table, "verdict": verdict, "recall": recall},
    )
    return FAILS if verdict["fails"] else PASSES


if __name__ == "__main__":
    sys.exit(main())
