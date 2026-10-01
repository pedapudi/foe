#!/usr/bin/python3
"""The foe documents the cross-harness evaluation runs, generated per workspace.

One graph is declared here as a version 4 file document with recovery
disabled. `autonomy` is a four-node graph in which one model surveys the
workspace, one implements the task, one assesses the result without editing,
and one repairs what the assessment found.

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

A node holds `block` when it can act on what it finds: the nodes that write.
The two nodes that only read do not hold it. A node that has changed nothing
has not attempted the task, and a block from a node ends the whole workflow
here, because this document disables workflow recovery so that a stop is the
arm's own rather than the runtime's retry. The shipped coding workflow
withholds the tool from its assessing node for the same reason.

A document holds two mechanisms that can be switched off one at a time.
The stopping mechanism is the `block` tool together with the instruction
that tells a node when to call it. The verifying mechanism is the `check`
named under `verify` on a node, with the `retries` and `max_fires` that its
findings admit, and under `done_when` at the root. The autonomy variants
differ only in these switches and in the survey node:

    configured   both mechanisms and the survey node
    unverified   the stopping mechanism without the verifying one; tools,
                 instructions, branches, and budgets equal `configured`
                 except `budget.max_episodes`, which counts only the
                 firings that remain
    ablated      neither mechanism
    lean         both mechanisms without the survey node
"""

from __future__ import annotations

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
AUTONOMY_VARIANTS: tuple[str, ...] = ("configured", "ablated", "unverified", "lean")
# The keys the verifying mechanism adds to a model node; at the root it adds `done_when`.
NODE_VERIFIER_KEYS: tuple[str, ...] = ("verify", "retries", "max_fires")

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


def _instructions(role: str, stopping: bool) -> dict[str, str]:
    contract = f"{_CONTRACT} {_BLOCK}" if stopping else _CONTRACT
    return {"10-role": role, "20-contract": contract}


class _Shape:
    """What every node of one document shares: the workspace, the check, the grants, and the two mechanisms.

    `stopping` places the `block` tool and the instruction to call it on
    every node that can change the workspace. `verified` names the check as
    the verifier on every node declared verified and at the root.
    """

    def __init__(
        self,
        workspace: Path,
        check: Path,
        limits: dict[str, int],
        write: list[str],
        execute: Sequence[str],
        stopping: bool,
        verified: bool,
    ) -> None:
        self.workspace = workspace
        self.check = check
        self.limits = limits
        self.write = write
        self.execute = [str(root) for root in execute]
        self.stopping = stopping
        self.verified = verified

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
        return [name for name in names if self.stopping or name != BLOCK]

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
        if verified and self.verified:
            node["verify"] = CHECK
            node["retries"] = NODE_RETRIES
            node.setdefault("max_fires", VERIFIED_FIRES)
        node.update(keys)
        if not self.verified:
            # Every firing past the first is one a verifier's findings cause:
            # a node's own verifier, or the root's re-firing the completing
            # node. Without a verifier no node fires twice, so no node
            # carries a `max_fires` and the episode ceiling counts one firing each.
            node.pop("max_fires", None)
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
        if self.verified:
            document["done_when"] = {"verify": CHECK, "retries": ROOT_RETRIES}
        document["workflow"] = {"nodes": nodes, "recovery": {"enabled": False}}
        document["task"] = task
        return document


def _shape(
    workspace: Path,
    check: Path,
    budget: Mapping[str, Any],
    root_files: bool,
    write_roots: Sequence[str],
    execute: Sequence[str],
    stopping: bool,
    verified: bool,
) -> _Shape:
    workspace = _absolute(workspace, "workspace")
    check = _absolute(check, "check")
    write = [str(workspace)] if root_files else [_write_root(workspace, root) for root in write_roots]
    if not write:
        raise ValueError("write_roots is empty; a node that edits needs at least one write root")
    for index, root in enumerate(write):
        if root in write[:index]:
            raise ValueError(f"write_roots entry {write_roots[index]!r} repeats an earlier entry")
    return _Shape(workspace, check, _budget(budget), write, execute, stopping, verified)


def autonomy(
    workspace: Path,
    check: Path,
    budget: Mapping[str, Any],
    variant: str = "configured",
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
    `task` is the task text; the run that uses the document supplies it, so
    the default is a placeholder that states as much.

    `variant` is one of AUTONOMY_VARIANTS. `configured` holds both
    mechanisms. `unverified` removes the verifier keys from every node and
    the root's `done_when`, and changes nothing else apart from the episode
    ceiling, which counts the firings that remain; the `block` tool and the
    instruction to call it stay, so a model under it can still reach every
    outcome the configured document offers. `ablated` removes the `block`
    tool, the instruction to call it, and every verifier declaration
    together. A score under `ablated` therefore cannot attribute an effect
    to the verifier, because the blocked outcome a task may require is
    unavailable to the model; `unverified` is the arm that isolates the
    verifier. `lean` drops the survey node: the implementing node reads the
    workspace itself, which saves one episode's cold start at the price of
    the survey's separate report.
    """
    if variant not in AUTONOMY_VARIANTS:
        raise ValueError(f"variant is {variant!r}; expected one of {', '.join(AUTONOMY_VARIANTS)}")
    lean = variant == "lean"
    shape = _shape(workspace, check, budget, root_files, write_roots, execute, stopping=variant != "ablated", verified=variant not in ("ablated", "unverified"))
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
        "autonomy" if variant == "configured" else f"autonomy-{variant}",
        "Run the declared workflow against the task in the workspace.",
        ("read", "grep", "edit", "bash", CHECK, "block"),
        nodes,
        task,
        max_depth=1,
    )


def write(document: dict[str, Any], path: Path) -> Path:
    """Write a document as indented JSON, creating the parent directory, and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path
