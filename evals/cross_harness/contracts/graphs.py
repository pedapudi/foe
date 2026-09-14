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
three calls of its own, one to spawn, one to wait, and one to return. The
same rule gives the worker its `input_tokens` and `output_tokens`, each
half the root's ceiling divided by the workers that run at once, and only
when the root declares that ceiling: the runtime reserves a dimension a
child leaves undeclared as the parent's whole remainder, so under a root
token ceiling a worker without a token share would leave nothing for the
next worker. A delegation whose remainder is below what its concurrent
workers ask sees the later spawns refused as exhausted, and the delegation
report records that outcome per unit.

A node holds `block` when it can act on what it finds: the nodes that write,
and the delegating node whose workers report their own blocks to it. The two
nodes that only read do not hold it. A node that has changed nothing has not
attempted the task, and a block from a node ends the whole workflow here,
because this document disables workflow recovery so that a stop is the arm's
own and not the runtime's retry. The shipped coding workflow withholds the
tool from its assessing node for the same reason; its team workflow gives it
to a read-only surveyor, but that surveyor is a spawned child whose block
reaches a lead that can respond, not a graph node whose block is the end.

The ablated autonomy variant removes the `block` tool from every node and
every verifier declaration, so a run under it can show what those two
mechanisms contribute. The teams variant `undivided` leaves the survey only
the `alone` label and drops the three nodes of the divide path; `sequential`
keeps the graph and caps concurrency at one.

The value a graph ends with is the value its terminal node returned, and a
grader reads that value. A node that changes files returns a change report,
which is the default shape of both terminal nodes of the teams graph. A task
whose grade reads a value of its own, such as one that asks for a list of
findings over the tree, states that shape and `teams` declares it on both
terminal nodes instead, so the value the grader receives is the value the
task asked for whichever branch the survey took.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

CHECK = "check"
# What a check suite writes while it runs, under the workspace: the build
# directory a compiler puts its output in, and the private temporary
# directory the check wrapper makes. A node that runs a check must be able to
# write these or the check cannot finish, whatever the node is allowed to
# change. Granting them is not granting the source: a node without `edit`
# still cannot touch a file the task is about, so a node that only reads can
# still run the tests it is asked to run.
CHECK_WRITES: tuple[str, ...] = ("target", ".check-tmp")
# The tool a node calls to end its episode with a reason instead of a result.
# The nodes that hold it are the ones that can change the workspace: a node
# that only reads has seen no attempt to complete the task, and the shipped
# coding workflow withholds it from its assessing node for the same reason.
BLOCK = "block"
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


# The floor under the check timeout, in seconds. Across the checks that
# returned in a measured run the slowest took 19 seconds, so this is roughly
# five times the slowest real check and leaves room for a cold build.
CHECK_TIMEOUT_FLOOR = 90


def check_timeout(seconds: int) -> int:
    """The wall-clock limit of one check run: a tenth of the episode's seconds, and never below CHECK_TIMEOUT_FLOOR.

    A check that hangs costs the arm this much before it learns anything, so
    the limit sets what discovering a hang is worth. Half the episode, which
    this was, means an arm that verifies after doing some work has almost
    nothing left to diagnose and report with: in a measured run a check
    timed out at 300 seconds of a 600 second episode and the episode ended
    120 seconds later, before the arm could say what it had found. A tenth
    leaves the rest of the budget for the report. The floor keeps a real
    check from being cut off on a run whose budget is small, and the result
    is held below the episode's own seconds so a check can never outlast it.
    """
    return max(1, min(seconds - 1, max(CHECK_TIMEOUT_FLOOR, seconds // 10)))


def worker_calls(model_calls: int, max_concurrent: int) -> int:
    """The `model_calls` one worker declares: half the root's allowance divided among the workers that run at once.

    The other half is left to the three nodes on the divide path, which
    spend from the remainder before the workers reserve and after they
    settle; the delegating node alone needs three calls, to spawn, to wait,
    and to return. At least 1, so a worker can return.
    """
    return max(1, model_calls // (2 * max_concurrent))


# The root ceilings a worker takes a share of; the runtime reserves any other dimension as the parent's remainder.
WORKER_SHARED_KEYS: tuple[str, ...] = ("model_calls", "input_tokens", "output_tokens")


def worker_budget(limits: Mapping[str, int], max_concurrent: int) -> dict[str, int]:
    """The ceilings one worker declares: the share of `worker_calls` for `model_calls` and for each token ceiling the root declares.

    A dimension a child leaves undeclared is reserved as the parent's whole
    remainder, so a worker without a token share under a root token ceiling
    would leave the next worker nothing to reserve.
    """
    return {key: worker_calls(limits[key], max_concurrent) for key in WORKER_SHARED_KEYS if key in limits}


def check_budget(budget: Mapping[str, Any]) -> dict[str, int]:
    """The root spend limits a document accepts, or a `ValueError` naming the key; the runner checks a budget with this before any spend."""
    return _budget(budget)


def _joined_paths(paths: Sequence[str]) -> str:
    """Paths as an instruction names them: each in backticks, joined with commas and `and`."""
    quoted = [f"`{path}`" for path in paths]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


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

    def root_write(self) -> list[str]:
        """The document's write grant: the task's write roots and the directories a check writes into."""
        return self.with_check_writes(list(self.write))

    def with_check_writes(self, roots: list[str]) -> list[str]:
        """`roots` plus each directory a check writes that no root already covers.

        The runtime refuses a grant that lies inside another grant of the same
        contract or of its parent, so a task whose write root is the workspace
        needs nothing added: the check's directories are already inside it.
        """
        covered = [Path(root) for root in roots]
        extra = []
        for name in CHECK_WRITES:
            path = self.workspace / name
            if str(path) in roots or any(path.is_relative_to(root) for root in covered):
                continue
            extra.append(str(path))
        return roots + extra

    def tools(self, names: Sequence[str]) -> list[str]:
        return [name for name in names if self.block or name != BLOCK]

    def contract(self, name: str, role: str, tools: Sequence[str], returns: dict[str, Any], **budget: int) -> dict[str, Any]:
        """A child contract in the sense of docs/config.md `child_contracts`, within this document's ceiling.

        `budget` holds the contract's further limits; a `model_calls` entry
        replaces the unlimited declaration a model node carries.
        """
        selected = self.tools(tools)
        grants: dict[str, Any] = {"read": [str(self.workspace)], "execute": list(self.execute)}
        write: list[str] = list(self.write) if "edit" in selected else []
        if CHECK in selected:
            write = self.with_check_writes(write)
        if write:
            grants["write"] = write
        contract: dict[str, Any] = {
            "name": name,
            # The instruction to stop belongs to the nodes that hold the tool,
            # not to every node of a document that offers it anywhere.
            "instructions": _instructions(role, BLOCK in selected),
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
            # The root grants what a check writes as well, because a child
            # contract may not grant more than the document it sits in.
            "grants": {"read": [str(self.workspace)], "write": self.root_write(), "execute": list(self.execute)},
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
    lean: bool = False,
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
    declaration, including the root's. `lean` drops the survey node: the
    implementing node reads the workspace itself, which saves one episode's
    cold start at the price of the survey's separate report. `task` is the
    task text; the run that uses the document supplies it, so the default is
    a placeholder that states as much.
    """
    shape = _shape(workspace, check, budget, root_files, write_roots, execute, block=not ablated)
    survey = shape.contract(
        "survey",
        "Read the task and the workspace before anything changes. Find the files the task names, the checks that cover them, "
        "and the conventions the workspace states. Report what you found and what stays uncertain. Change nothing.",
        ("read", "grep", "bash"),
        survey_report(),
    )
    implement = shape.contract(
        "implement",
        (
            "Read the task and the workspace before anything changes: the files the task names, the checks that cover them, "
            "and the conventions the workspace states. Then implement the task. Make the smallest sufficient change. "
            if lean
            else "Implement the task in the workspace, using the survey. Make the smallest sufficient change. "
        )
        + "Report every changed path, the validation you observed, unresolved risks, and cited evidence.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        change_report(),
    )
    assess = shape.contract(
        "assess",
        "Assess whether the workspace satisfies the task. Treat the implementation report as unverified. "
        "Inspect and test without editing any file. For behavior the task parameterizes, test materially different valid inputs "
        "through the same public interface. Choose `accept` only with evidence for every requirement and no unresolved risk. "
        "Otherwise choose `repair` and return findings a reader can reproduce.",
        ("read", "grep", "bash", CHECK),
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
        "implement": shape.node(implement, ["task"] if lean else ["task", "survey"], verified=True),
        # The root verifier re-fires the node that completed the workflow, which on the accept path is the assessing node.
        "assess": shape.node(assess, ["task", "implement"], branches={"accept": [], "repair": ["repair"]}, max_fires=TERMINAL_FIRES),
        "repair": shape.node(repair, ["task", "implement", "assess"], verified=True, terminal=True, max_fires=TERMINAL_FIRES),
    }
    if not lean:
        nodes = {"survey": shape.node(survey, ["task"]), **nodes}
    return shape.document(
        "autonomy-ablated" if ablated else "autonomy-lean" if lean else "autonomy",
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
    returns: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The survey, interface, delegate, integrate graph with an implement-alone branch, over one workspace.

    `variant` is `configured` for the graph as declared, `undivided` to leave
    the survey only the `alone` label and drop the divide path's three nodes,
    or `sequential` to cap concurrency at one worker. `max_concurrent` caps the workers one delegation runs at once
    and the model nodes the root runs at once; `sequential` sets both to 1.
    `returns` is the shape the task requires of the value the workflow ends
    with; the two nodes that can end it declare it in place of the change
    report, and their instructions then ask for that value rather than for a
    change report. A task that states no shape leaves both on the change
    report. The other keyword parameters mean what they mean for `autonomy`.
    """
    if variant not in TEAMS_VARIANTS:
        raise ValueError(f"variant is {variant!r}; expected one of {', '.join(TEAMS_VARIANTS)}")
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent <= 0:
        raise ValueError(f"max_concurrent is {max_concurrent!r}; expected a positive integer")
    if variant == "sequential":
        max_concurrent = 1
    shape = _shape(workspace, check, budget, root_files, write_roots, execute, block=True)
    # The task's own shape reaches only the nodes that can end the workflow:
    # the value the run grades is the one the terminal node returned, and a
    # node in the middle of the graph still reports its work as a change.
    def terminal_returns() -> dict[str, Any]:
        return change_report() if returns is None else copy.deepcopy(dict(returns))

    terminal_report = (
        "Report every changed path, the validation you observed, unresolved risks, and cited evidence."
        if returns is None
        else "Return the value the task's text states, in the shape this node's contract declares."
    )
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
        **worker_budget(shape.limits, max_concurrent),
    )
    survey = shape.contract(
        "survey",
        "Survey the task and the workspace, then decide how the work divides. A unit is a part of the task one worker can finish "
        "alone inside directories no other unit writes. Name each unit, the crates it covers, the directories it writes, and the "
        "shared elements two units would both touch. Choose `divide` when at least two independent units exist and each costs "
        "more than a few tool calls. Choose `alone` otherwise. Change nothing.",
        ("read", "grep", "bash"),
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
    check_dirs = _joined_paths(shape.with_check_writes([]))
    delegate = shape.contract(
        "delegate",
        "Give each unit the survey named to one worker with `spawn`, with `fresh` context and `write` limited to the directories "
        "that unit writes, which must exist and must not overlap another unit's, plus the directories the check writes, "
        f"{check_dirs}, which every worker needs to run it. Add every unit before waiting; `wait` with no "
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
    # the node itself holds no tool that writes. It carries what a check
    # writes because its workers run checks, and a child may not be granted
    # what its parent lacks.
    delegate["grants"]["write"] = shape.with_check_writes(list(shape.write))
    delegate["grants"]["spawn"] = [WORKER]
    delegate["child_contracts"] = {WORKER: worker}
    integrate = shape.contract(
        "integrate",
        "Integrate what the workers returned. Apply every change a worker reported as needed outside its grant, reconcile the "
        "units where they meet, and account for every unit whose board task did not complete. " + terminal_report,
        ("read", "grep", "edit", "bash", CHECK, "block"),
        terminal_returns(),
    )
    alone = shape.contract(
        "implement-alone",
        "Implement the whole task yourself, using the survey. Make the smallest sufficient change. " + terminal_report,
        ("read", "grep", "edit", "bash", CHECK, "block"),
        terminal_returns(),
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
