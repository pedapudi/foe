#!/usr/bin/python3
"""The foe documents the cross-harness evaluation runs, generated per workspace.

Two graphs are declared here as version 4 file documents with recovery
disabled. `autonomy` is a four-node graph in which one model surveys the
workspace, one implements the task, one assesses the result without editing,
and one repairs what the assessment found. `teams` is a five-node graph in
which a survey decides whether the task divides into units, a node writes
the shared elements those units touch, a delegating node without an edit
tool gives each unit to a worker under a narrowed write grant, and an
integrating node reconciles what the workers returned; a task that does not
divide goes to one implementing node instead.

A file document cannot take `--verify`, so every document declares a `check`
tool that runs the workspace's check script and names it as the verifier at
the root and on every node that changes files. The workspace root and the
check script are parameters, because the arm that runs a task materializes
the workspace and fills them. Every write root a document names must exist
when the document is planned or run, because the runtime opens each root
when the episode starts.

The root's `budget.model_calls` is the whole allowance and the one model-call
ceiling a document states as a number. Every model node declares
`model_calls` as `"unlimited"`. When a node fires, the runtime reserves the
node's declared `model_calls` from the root's remainder: it refuses a firing
that asks for more than remains, and it grants a firing that declares no
limit the whole remainder. A node declared `"unlimited"` therefore draws on
whatever the earlier firings left unspent, and the `budget/reserve` event of
each firing records the remainder it received. The runtime refuses a
contract that omits the key, and a node declared with a fixed count is
refused once the remainder falls below that count, so `"unlimited"` is the
one declaration under which every node on a path can fire until the root
itself is spent.

The worker keeps a fixed share, because concurrent children reserve from
one remainder: a first worker declared `"unlimited"` takes the delegating
node's whole remainder and the runtime refuses the second. The share is half
the root allowance divided by the number of workers that run at once. The
other half is what the survey, the interface node, and the delegating node
spend before and after the workers run; the delegating node needs at least
three calls of its own, one to spawn, one to wait, and one to return. A
delegation whose remainder is below what its concurrent workers ask sees the
later spawns refused as exhausted, and the delegation report records that
outcome per unit.

The ablated autonomy variant removes the `block` tool from every node and
every verifier declaration, so a run under it can show what those two
mechanisms contribute. The teams variant `undivided` leaves the survey only
the `alone` label and drops the three nodes of the divide path; `sequential`
keeps the graph and caps concurrency at one.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

CHECK = "check"
WRITE_ROOTS: tuple[str, ...] = ("crates", "docs", "examples")
EXECUTE_ROOTS: tuple[str, ...] = ("/bin", "/usr/bin", "/usr/local/bin")
TEAMS_VARIANTS: tuple[str, ...] = ("configured", "undivided", "sequential")
WORKER = "worker"

# How many times findings from the root verifier re-fire the completing
# node, and how many times a node's own verifier re-fires that node.
ROOT_RETRIES = 6
NODE_RETRIES = 2
# The firings a terminal node admits: its first firing and one per root retry.
TERMINAL_FIRES = ROOT_RETRIES + 1
# The firings a verified node admits: its first firing and one per node retry.
VERIFIED_FIRES = NODE_RETRIES + 1
# The `model_calls` every model node declares: no limit of its own, so each
# firing reserves the root's whole remainder. The runtime requires the key on
# every contract and reads this word as the absence of a ceiling.
NODE_CALLS = "unlimited"
# The worker episodes one delegation may hold, which also bounds the units a
# survey may name.
WORKER_EPISODES = 8
# The task text a document carries until the run that uses it supplies one.
PLACEHOLDER_TASK = "Complete the task the run supplies in place of this sentence."

_BUDGET_KEYS = ("model_calls", "seconds", "input_tokens", "output_tokens")

_CONTRACT = (
    "Treat the task text and the workspace checks as the source of truth. "
    "Limit changes to the files the task requires and preserve everything else, "
    "including history, archives, encodings, and any file the task names as protected. "
    "Validate through the strongest interface the task permits. "
    "Each tool result begins with its log sequence as `[seq N]`; "
    "cite a successful tool result for every completion-critical claim."
)
_BLOCK = (
    "When the task cannot be completed as stated, when it is ambiguous in a way the workspace does not settle, "
    "or when the goal is unreachable, call `block` with the code that names the reason rather than reporting completion."
)
_CHECK_INSTRUCTION = "Run `check` with no arguments after the final change and before reporting; report what it printed."


def _string_list(max_length: int, max_items: int, min_items: int = 0) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": {"type": "string", "maxLength": max_length}, "maxItems": max_items}
    if min_items:
        schema["minItems"] = min_items
    return schema


def _cited(properties: dict[str, Any], required: list[str], min_items: int, max_items: int) -> dict[str, Any]:
    """An array of objects each citing a log sequence, the shape docs/config.md names for exported observations."""
    return {
        "type": "array",
        "minItems": min_items,
        "maxItems": max_items,
        "items": {
            "type": "object",
            "properties": {**properties, "seq": {"type": "integer", "minimum": 0}},
            "required": [*required, "seq"],
            "additionalProperties": False,
        },
    }


_SUMMARY = {"type": "string", "minLength": 1, "maxLength": 1000}
_LEARNED = _cited({"claim": {"type": "string", "minLength": 1, "maxLength": 300}}, ["claim"], 1, 8)
_CHANGED_PATHS = _string_list(512, 64)
_VALIDATION = _string_list(1000, 32, min_items=1)


def _report(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def change_report(unresolved_max: int = 16) -> dict[str, Any]:
    """The shape a node that changes files returns; the built-in coding document's implementing node returns it."""
    return _report(
        {
            "summary": _SUMMARY,
            "changed_paths": _CHANGED_PATHS,
            "validation": _VALIDATION,
            "unresolved_risks": _string_list(1000, unresolved_max),
            "learned": _LEARNED,
        }
    )


def assessment_report() -> dict[str, Any]:
    """The shape the assessing node returns; the built-in coding document's assessing node returns it."""
    return _report(
        {
            "summary": _SUMMARY,
            "findings": _string_list(1000, 32),
            "validation": _VALIDATION,
            "unresolved_risks": _string_list(1000, 16),
            "learned": _LEARNED,
        }
    )


def survey_report() -> dict[str, Any]:
    """The shape a survey returns: a summary, what stays uncertain, and cited observations."""
    return _report({"summary": _SUMMARY, "unresolved_risks": _string_list(1000, 16), "learned": _LEARNED})


def units_report() -> dict[str, Any]:
    """The teams survey's shape: the units the task divides into, each with its crates, write roots, and shared elements."""
    unit = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 120},
            "crates": _string_list(120, 16),
            "write_roots": _string_list(512, 16, min_items=1),
            "shared": _string_list(512, 32),
        },
        "required": ["name", "crates", "write_roots", "shared"],
        "additionalProperties": False,
    }
    return _report(
        {
            "summary": _SUMMARY,
            "units": {"type": "array", "maxItems": WORKER_EPISODES, "items": unit},
            "unresolved_risks": _string_list(1000, 16),
            "learned": _LEARNED,
        }
    )


def delegation_report() -> dict[str, Any]:
    """The delegating node's shape: per unit, the board task, its outcome, the worker's episode, and the cited result."""
    units = _cited(
        {
            "unit": {"type": "string", "minLength": 1, "maxLength": 300},
            "task_id": {"type": "string", "minLength": 1, "maxLength": 120},
            "outcome": {"type": "string", "enum": ["completed", "blocked", "exhausted", "failed"]},
            "episode": {"type": "string", "maxLength": 64},
            "finding": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        ["unit", "task_id", "outcome", "finding"],
        1,
        WORKER_EPISODES,
    )
    return _report(
        {
            "summary": _SUMMARY,
            "units": units,
            "changed_paths": _CHANGED_PATHS,
            "unresolved_risks": _string_list(1000, 16),
        }
    )


def worker_report() -> dict[str, Any]:
    """The worker's shape, equal to the built-in team document's worker return schema."""
    evidence = _cited(
        {"claim": {"type": "string", "minLength": 1, "maxLength": 300}, "episode": {"type": "string", "maxLength": 64}},
        ["claim"],
        1,
        16,
    )
    return {
        "type": "object",
        "properties": {
            "unit": {"type": "string", "minLength": 1, "maxLength": 300},
            "finding": {"type": "string", "minLength": 1, "maxLength": 2000},
            "evidence": evidence,
            "open_questions": _string_list(1000, 8),
            "changed_paths": _CHANGED_PATHS,
            "needs_from_lead": _string_list(1000, 16),
        },
        "required": ["unit", "finding", "changed_paths", "evidence", "needs_from_lead"],
        "additionalProperties": False,
    }


def _positive(budget: Mapping[str, Any], key: str) -> int:
    value = budget[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"budget.{key} is {value!r}; expected a positive integer")
    return value


def _budget(budget: Mapping[str, Any]) -> dict[str, int]:
    """The root spend limits: `model_calls` and `seconds` are required, the token allowances optional."""
    unknown = sorted(set(budget) - set(_BUDGET_KEYS))
    if unknown:
        raise ValueError(f"budget.{unknown[0]} is not one of {', '.join(_BUDGET_KEYS)}")
    for key in ("model_calls", "seconds"):
        if key not in budget:
            raise ValueError(f"budget lacks {key}")
    limits = {key: _positive(budget, key) for key in _BUDGET_KEYS if key in budget}
    if limits["seconds"] < 2:
        raise ValueError(f"budget.seconds is {limits['seconds']}; the check timeout must fit below it, so at least 2 is required")
    return limits


def check_timeout(seconds: int) -> int:
    """The wall-clock limit of one check run: half the episode's seconds, so a check that hangs leaves time to report."""
    return max(1, seconds // 2)


def worker_calls(model_calls: int, max_concurrent: int) -> int:
    """The `model_calls` one worker declares: half the root's allowance divided among the workers that run at once.

    The other half is left to the three nodes on the divide path, which
    spend from the remainder before the workers reserve and after they
    settle; the delegating node alone needs three calls, to spawn, to wait,
    and to return. At least 1, so a worker can return.
    """
    return max(1, model_calls // (2 * max_concurrent))


def check_budget(budget: Mapping[str, Any]) -> dict[str, int]:
    """The root spend limits a document accepts, or a `ValueError` naming the key; the runner checks a budget with this before any spend."""
    return _budget(budget)


def _absolute(path: Path, key: str) -> Path:
    if not Path(path).is_absolute():
        raise ValueError(f"{key} is {str(path)!r}; expected an absolute path")
    return Path(path)


def _write_root(workspace: Path, root: str) -> str:
    """The absolute write root `root` names below `workspace`; a root that would leave the workspace or cover it is refused."""
    parts = Path(root).parts
    if not root or Path(root).is_absolute() or not parts or ".." in parts:
        raise ValueError(f"write_roots entry {root!r} is not a relative path to a directory below the workspace")
    return str(workspace / root)


def _check_def(check: Path, seconds: int) -> dict[str, Any]:
    return {
        "exec": str(check),
        "description": (
            "Runs the workspace checks with no arguments. Prints one finding per line and nothing when every check passes; "
            "the exit status is zero either way."
        ),
        "instruction": _CHECK_INSTRUCTION,
        "timeout_seconds": check_timeout(seconds),
    }


def _instructions(role: str, block: bool) -> dict[str, str]:
    contract = f"{_CONTRACT} {_BLOCK}" if block else _CONTRACT
    return {"10-role": role, "20-contract": contract}


class _Shape:
    """What every node of one document shares: the workspace, the check, the grants, and whether `block` is present."""

    def __init__(
        self,
        workspace: Path,
        check: Path,
        limits: dict[str, int],
        write: list[str],
        execute: Sequence[str],
        block: bool,
    ) -> None:
        self.workspace = workspace
        self.check = check
        self.limits = limits
        self.write = write
        self.execute = [str(root) for root in execute]
        self.block = block

    def tools(self, names: Sequence[str]) -> list[str]:
        return [name for name in names if self.block or name != "block"]

    def contract(self, name: str, role: str, tools: Sequence[str], returns: dict[str, Any], **budget: int) -> dict[str, Any]:
        """A child contract in the sense of docs/config.md `child_contracts`, within this document's ceiling.

        `budget` holds the contract's further limits; a `model_calls` entry
        replaces the unlimited declaration a model node carries.
        """
        selected = self.tools(tools)
        grants: dict[str, Any] = {"read": [str(self.workspace)], "execute": list(self.execute)}
        if "edit" in selected:
            grants["write"] = list(self.write)
        contract: dict[str, Any] = {
            "name": name,
            "instructions": _instructions(role, self.block),
            "tools": selected,
            "grants": grants,
            "budget": {"model_calls": NODE_CALLS, **budget},
            "done_when": {"returns": returns},
        }
        if CHECK in selected:
            contract["tool_defs"] = {CHECK: _check_def(self.check, self.limits["seconds"])}
        return contract

    def node(self, contract: dict[str, Any], follows: Sequence[str], *, verified: bool = False, **keys: Any) -> dict[str, Any]:
        """A model node; `verified` names the check as the node's verifier and admits the re-fires its findings cause."""
        node: dict[str, Any] = {"model": contract, "follows": list(follows)}
        if verified and self.block:
            node["verify"] = CHECK
            node["retries"] = NODE_RETRIES
            node.setdefault("max_fires", VERIFIED_FIRES)
        node.update(keys)
        return node

    def document(self, name: str, role: str, tools: Sequence[str], nodes: dict[str, Any], task: str, **budget: Any) -> dict[str, Any]:
        selected = self.tools(tools)
        fires = sum(node.get("max_fires", 1) for node in nodes.values())
        document: dict[str, Any] = {
            "version": 4,
            "name": name,
            "instructions": {"10-role": role},
            "tools": selected,
            "tool_defs": {CHECK: _check_def(self.check, self.limits["seconds"])},
            "grants": {"read": [str(self.workspace)], "write": list(self.write), "execute": list(self.execute)},
            "budget": {**self.limits, "max_episodes": 1 + fires, **budget},
        }
        if self.block:
            document["done_when"] = {"verify": CHECK, "retries": ROOT_RETRIES}
        document["workflow"] = {"nodes": nodes, "recovery": {"enabled": False}}
        document["task"] = task
        return document


def _shape(
    workspace: Path, check: Path, budget: Mapping[str, Any], root_files: bool, write_roots: Sequence[str], execute: Sequence[str], block: bool
) -> _Shape:
    workspace = _absolute(workspace, "workspace")
    check = _absolute(check, "check")
    write = [str(workspace)] if root_files else [_write_root(workspace, root) for root in write_roots]
    if not write:
        raise ValueError("write_roots is empty; a node that edits needs at least one write root")
    for index, root in enumerate(write):
        if root in write[:index]:
            raise ValueError(f"write_roots entry {write_roots[index]!r} repeats an earlier entry")
    return _Shape(workspace, check, _budget(budget), write, execute, block)


def autonomy(
    workspace: Path,
    check: Path,
    budget: Mapping[str, Any],
    ablated: bool = False,
    *,
    root_files: bool = False,
    write_roots: Sequence[str] = WRITE_ROOTS,
    execute: Sequence[str] = EXECUTE_ROOTS,
    task: str = PLACEHOLDER_TASK,
) -> dict[str, Any]:
    """The survey, implement, assess, repair graph over one workspace.

    `budget` carries the root spend limits under the keys `model_calls` and
    `seconds`, with `input_tokens` and `output_tokens` optional. `root_files`
    widens the write grant from the listed `write_roots`, each a relative
    path to a directory below the workspace, to the workspace itself.
    `ablated` removes `block` from every node and every verifier
    declaration, including the root's. `task` is the task text; the run that
    uses the document supplies it, so the default is a placeholder that
    states as much.
    """
    shape = _shape(workspace, check, budget, root_files, write_roots, execute, block=not ablated)
    survey = shape.contract(
        "survey",
        "Read the task and the workspace before anything changes. Find the files the task names, the checks that cover them, "
        "and the conventions the workspace states. Report what you found and what stays uncertain. Change nothing.",
        ("read", "grep", "bash", "block"),
        survey_report(),
    )
    implement = shape.contract(
        "implement",
        "Implement the task in the workspace, using the survey. Make the smallest sufficient change. "
        "Report every changed path, the validation you observed, unresolved risks, and cited evidence.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        change_report(),
    )
    assess = shape.contract(
        "assess",
        "Assess whether the workspace satisfies the task. Treat the implementation report as unverified. "
        "Inspect and test without editing any file. For behavior the task parameterizes, test materially different valid inputs "
        "through the same public interface. Choose `accept` only with evidence for every requirement and no unresolved risk. "
        "Otherwise choose `repair` and return findings a reader can reproduce.",
        ("read", "grep", "bash", CHECK, "block"),
        assessment_report(),
    )
    repair = shape.contract(
        "repair",
        "Repair the assessment's findings. Treat every finding and unresolved risk as an obligation. "
        "Reproduce each finding before changing a file, then resolve it with the smallest sufficient change or show that it "
        "cannot affect a task requirement. Return no unresolved risks, every changed path, the validation you observed, "
        "and cited evidence.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        change_report(unresolved_max=0),
    )
    nodes = {
        "survey": shape.node(survey, ["task"]),
        "implement": shape.node(implement, ["task", "survey"], verified=True),
        # The root verifier re-fires the node that completed the workflow, which on the accept path is the assessing node.
        "assess": shape.node(assess, ["task", "implement"], branches={"accept": [], "repair": ["repair"]}, max_fires=TERMINAL_FIRES),
        "repair": shape.node(repair, ["task", "implement", "assess"], verified=True, terminal=True, max_fires=TERMINAL_FIRES),
    }
    return shape.document(
        "autonomy-ablated" if ablated else "autonomy",
        "Run the declared workflow against the task in the workspace.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        nodes,
        task,
        max_depth=1,
    )


def teams(
    workspace: Path,
    check: Path,
    budget: Mapping[str, Any],
    variant: str = "configured",
    *,
    max_concurrent: int = 4,
    root_files: bool = False,
    write_roots: Sequence[str] = WRITE_ROOTS,
    execute: Sequence[str] = EXECUTE_ROOTS,
    task: str = PLACEHOLDER_TASK,
) -> dict[str, Any]:
    """The survey, interface, delegate, integrate graph with an implement-alone branch, over one workspace.

    `variant` is `configured` for the graph as declared, `undivided` to leave
    the survey only the `alone` label and drop the divide path's three nodes,
    or `sequential` to cap concurrency at one worker. `max_concurrent` caps the workers one delegation runs at once
    and the model nodes the root runs at once; `sequential` sets both to 1.
    The other keyword parameters mean what they mean for `autonomy`.
    """
    if variant not in TEAMS_VARIANTS:
        raise ValueError(f"variant is {variant!r}; expected one of {', '.join(TEAMS_VARIANTS)}")
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent <= 0:
        raise ValueError(f"max_concurrent is {max_concurrent!r}; expected a positive integer")
    if variant == "sequential":
        max_concurrent = 1
    shape = _shape(workspace, check, budget, root_files, write_roots, execute, block=True)
    worker = shape.contract(
        WORKER,
        "Do the one unit your task names, inside the directories you were granted and nowhere else. Read and search anywhere. "
        "A change the unit needs outside your grant is something you report under `needs_from_lead`; the lead applies it. "
        "Run the checks that cover your unit. When the unit turns on a decision only the lead can make, `ask` the lead, "
        "addressed as `lead`, with a deadline you can afford and a default that is safe to act on, and `wait` on the reply. "
        "Report your own episode identifier beside your evidence so the lead can cite it.",
        ("read", "grep", "edit", "bash", CHECK, "block", "ask", "notify", "wait"),
        worker_report(),
        max_depth=0,
        model_calls=worker_calls(shape.limits["model_calls"], max_concurrent),
    )
    survey = shape.contract(
        "survey",
        "Survey the task and the workspace, then decide how the work divides. A unit is a part of the task one worker can finish "
        "alone inside directories no other unit writes. Name each unit, the crates it covers, the directories it writes, and the "
        "shared elements two units would both touch. Choose `divide` when at least two independent units exist and each costs "
        "more than a few tool calls. Choose `alone` otherwise. Change nothing.",
        ("read", "grep", "bash", "block"),
        units_report(),
    )
    interface = shape.contract(
        "interface",
        "Write the shared elements the survey named before any unit starts: interfaces, schemas, manifest entries, and any file "
        "two units would both touch. Make each one complete enough that a worker can build against it without asking. "
        "Report every changed path, the validation you observed, unresolved risks, and cited evidence.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        change_report(),
    )
    delegate = shape.contract(
        "delegate",
        "Give each unit the survey named to one worker with `spawn`, with `fresh` context and `write` limited to the directories "
        "that unit writes, which must exist and must not overlap another unit's. Add every unit before waiting; `wait` with no "
        "arguments returns when all have settled. Answer a worker's question with `send`, carrying the question's `message_id` "
        "in `reply_to`. You hold no edit tool: a change a worker reports as needed outside its grant goes into your report for "
        "the integrating node. Return each unit's board task identifier, its outcome, the worker's episode, and the sequence of "
        "the result that carries the worker's report.",
        ("read", "grep", "bash", "block", "spawn", "wait", "steer", "cancel", "send", "team"),
        delegation_report(),
        max_depth=1,
        max_episodes=1 + WORKER_EPISODES,
        max_concurrent=max_concurrent,
    )
    # The delegating node's write grant is the ceiling each spawn narrows;
    # the node itself holds no tool that writes.
    delegate["grants"]["write"] = list(shape.write)
    delegate["grants"]["spawn"] = [WORKER]
    delegate["child_contracts"] = {WORKER: worker}
    integrate = shape.contract(
        "integrate",
        "Integrate what the workers returned. Apply every change a worker reported as needed outside its grant, reconcile the "
        "units where they meet, and account for every unit whose board task did not complete. Report every changed path, "
        "the validation you observed, unresolved risks, and cited evidence.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        change_report(),
    )
    alone = shape.contract(
        "implement-alone",
        "Implement the whole task yourself, using the survey. Make the smallest sufficient change. "
        "Report every changed path, the validation you observed, unresolved risks, and cited evidence.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        change_report(),
    )
    nodes = {
        "survey": shape.node(survey, ["task"], branches={"divide": ["interface"], "alone": ["implement-alone"]}),
        "interface": shape.node(interface, ["task", "survey"], verified=True),
        "delegate": shape.node(delegate, ["task", "survey", "interface"]),
        "integrate": shape.node(integrate, ["task", "survey", "interface", "delegate"], verified=True, terminal=True, max_fires=TERMINAL_FIRES),
        "implement-alone": shape.node(alone, ["task", "survey"], verified=True, terminal=True, max_fires=TERMINAL_FIRES),
    }
    if variant == "undivided":
        # An edge from a branching node to a target no label lists is fresh
        # after every firing, so the divide path's nodes leave with the label.
        nodes["survey"]["branches"] = {"alone": ["implement-alone"]}
        for name in ("interface", "delegate", "integrate"):
            del nodes[name]
    document = shape.document(
        f"teams-{variant}",
        "Run the declared workflow against the task in the workspace.",
        ("read", "grep", "edit", "bash", CHECK, "block", "spawn", "wait", "steer", "cancel", "send", "team", "ask", "notify"),
        nodes,
        task,
        max_depth=2,
        max_concurrent=max_concurrent,
    )
    document["budget"]["max_episodes"] += WORKER_EPISODES
    # The root carries the worker as the ceiling the delegating node's own copy lies within.
    document["grants"]["spawn"] = [WORKER]
    document["child_contracts"] = {WORKER: copy.deepcopy(worker)}
    return document


def write(document: dict[str, Any], path: Path) -> Path:
    """Write a document as indented JSON, creating the parent directory, and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path
