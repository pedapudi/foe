"""The documents the binary carries, as execution contracts.

`builtin` runs `foe plan --config builtin:NAME --json`, which prints the
contract the binary carries under that name, and returns it as an
`ExecutionContract`. The package holds no copy of any such document: the
name, the instructions, the tools, the budgets, and the whole workflow come
from the binary that will run the contract, so the two cannot drift.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from collections import deque
from pathlib import Path
from typing import Any, Iterator, Mapping

from ._capabilities import PathLike
from ._contract import Budget, DoneWhen, ExecutionContract, Grants, Model, Returns, ToolDef, Verified
from ._errors import BinaryError, ConfigError
from ._tools import HostTool

# The name of the configured tool a verifier given as a path takes, and the
# name `foe init` gives the same tool in the document it writes.
_VERIFIER_TOOL = "check"

# What the model is told the verifier is. docs/config.md `done_when` states
# the contract the executable follows.
_VERIFIER_DESCRIPTION = (
    "The task's verifier. It runs in the root directory, prints one finding per line, and exits with status "
    'zero whether or not it found any; printing nothing is acceptance. An ordinary call takes {"args": []}, '
    "and the run that judges completion receives the completion value as JSON on standard input."
)

# docs/config.md `done_when`: the number of times findings are fed back when
# the document states no count.
_DEFAULT_RETRIES = 2

# docs/config.md `budget`: the lifetime episode count a document that states
# none receives.
_DEFAULT_MAX_EPISODES = 8

# docs/workflow.md "Nodes": how many times a node may fire when it declares
# no count.
_DEFAULT_MAX_FIRES = 1

# docs/workflow.md "Nodes": the `follows` entry naming the invocation task,
# which is no node and imposes no ordering.
_TASK_SOURCE = "task"

_CONTRACT_KEYS = frozenset(
    ("name", "instructions", "tools", "tool_defs", "host_tools", "grants", "budget")
    + ("done_when", "sandbox", "child_contracts", "workflow")
)
_GRANT_KEYS = frozenset({"read", "write", "execute", "spawn", "bind", "task_session"})
_BUDGET_KEYS = (
    "model_calls",
    "input_tokens",
    "output_tokens",
    "seconds",
    "max_depth",
    "max_episodes",
    "max_concurrent",
    "loop_threshold",
)
_TOOL_DEF_KEYS = frozenset({"exec", "description", "instruction", "network", "timeout_seconds", "cwd"})


def builtin(
    name: str,
    root: PathLike,
    *,
    binary: PathLike,
    verify: HostTool | PathLike | None = None,
    retries: int = _DEFAULT_RETRIES,
    model: Model | None = None,
) -> ExecutionContract:
    """The document the binary carries under `name`, over `root`.

    `binary` is the `foe` binary, named the way `run_config` names it. The
    binary runs with `root` as its working directory and prints the document
    it carries, which is how the document's read and write roots and its
    description of the environment come to name that directory. `root` is
    added to the execute grant of the document and of every workflow node
    that runs a model, which is the grant `foe init` writes so that a
    subprocess of the episode may run the programs in the directory.

    `verify` gates completion. A host tool becomes the verifier directly. A
    path becomes a configured tool named `check` that runs in `root`, given
    to the document and to every workflow node that runs a model. The gate
    itself is the document's `done_when`, and a return schema the document
    already declares stays beside the verifier.

    `retries` is how many times findings re-fire the work, and applies only
    when `verify` is given. docs/workflow.md "Completion" makes a finding
    re-fire the nearest model ancestor of the node that completed the
    workflow, and "Bounds" makes `max_fires` cap those re-fires, so the
    bounds the printed document carries are raised to admit them: every node
    that can complete a workflow of the document contributes its nearest
    model ancestor, whose `max_fires` becomes at least `retries` plus one,
    and the document's `budget.max_episodes` rises by `retries`, because
    each re-fire runs one further episode. Without the raise a single
    finding ends the run as blocked with `recovery-exhausted`. A document
    that declares no workflow runs one episode and feeds a finding back into
    that episode, so neither bound rises.

    `model` configures the endpoint the binary calls, and is the only model
    block the returned document carries: the blocks the binary printed below
    the document are dropped, so the one block applies throughout. Without
    `model` the document carries no block at all and the host answers every
    model request, which is what `run_config` requires of a document with no
    `model` block.

    Raises `ConfigError` carrying the binary's message when the binary
    carries no document of that name, and `BinaryError` when the binary
    cannot be run.
    """
    if retries < 0:
        raise ConfigError(f"retries: {retries} is negative")
    directory = _directory(root)
    document = _planned_document(name, directory, binary)
    for contract in _contracts(document):
        grants = contract.setdefault("grants", {})
        execute = list(grants.get("execute") or ())
        grants["execute"] = execute + [str(directory)]
        contract.pop("model", None)
    fields = _fields(document, "")
    fields["model"] = model
    if verify is not None:
        _gate(fields, verify, directory, retries)
    return ExecutionContract(**fields)


def _directory(root: PathLike) -> Path:
    """The root as an absolute path with every symbolic link resolved."""
    directory = Path(os.path.realpath(os.fspath(root)))
    if not directory.is_dir():
        raise ConfigError(f"root: {os.fspath(root)!r} is not a directory")
    return directory


def _planned_document(name: str, directory: Path, binary: PathLike) -> dict[str, Any]:
    """The contract `foe plan` prints for the built-in document `name`."""
    command = [os.fspath(binary), "plan", "--config", f"builtin:{name}", "--json"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, cwd=directory)
    except OSError as exc:
        raise BinaryError(f"{os.fspath(binary)}: {exc}") from exc
    if completed.returncode != 0:
        raise ConfigError(completed.stderr.strip() or f"builtin:{name}: foe plan exited with no message")
    try:
        plan = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BinaryError(f"foe plan --json printed something other than JSON: {exc}") from exc
    document = plan.get("contract") if isinstance(plan, dict) else None
    if not isinstance(document, dict):
        raise BinaryError("foe plan --json printed no 'contract' object")
    return document


def _contracts(contract: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The contract and every contract below it: children and workflow nodes."""
    yield contract
    for child in _objects(contract.get("child_contracts")):
        yield from _contracts(child)
    workflow = contract.get("workflow")
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    for node in _objects(nodes):
        node_contract = node.get("model")
        if isinstance(node_contract, dict):
            yield from _contracts(node_contract)


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        for entry in value.values():
            if isinstance(entry, dict):
                yield entry


def _fields(document: Mapping[str, Any], where: str) -> dict[str, Any]:
    """The `ExecutionContract` arguments the document states.

    Every key of the document is either an argument or a refusal: a key the
    package does not model would otherwise be dropped from the document the
    contract writes, and the contract would no longer be what the binary
    carries.
    """
    for key in sorted(document):
        if key not in _CONTRACT_KEYS:
            raise ConfigError(f"{where}{key}: the package models no contract key of that name")
    if document.get("host_tools"):
        raise ConfigError(
            f"{where}host_tools: a document that declares host tools needs their implementations, "
            "which the package takes from functions decorated with @foe.tool"
        )
    instructions = document.get("instructions")
    if not isinstance(instructions, dict) or not all(isinstance(v, str) for v in instructions.values()):
        raise ConfigError(f"{where}instructions: expected an object of strings")
    tool_defs = document.get("tool_defs") or {}
    children = document.get("child_contracts") or {}
    done_when = document.get("done_when")
    sandbox = document.get("sandbox") or {}
    return {
        "name": document.get("name", ""),
        "instructions": instructions,
        "tools": list(document.get("tools") or ()),
        "tool_defs": {k: _tool_def(v, f"{where}tool_defs.{k}") for k, v in tool_defs.items()},
        "grants": _grants(document.get("grants") or {}, where),
        "budget": _budget(document.get("budget") or {}, where),
        "done_when": None if done_when is None else _done_when(done_when, where),
        "sandbox": sandbox.get("mode"),
        "child_contracts": {
            key: ExecutionContract(**_fields(child, f"{where}child_contracts.{key}."))
            for key, child in children.items()
        },
        "workflow": document.get("workflow"),
    }


def _grants(block: Mapping[str, Any], where: str) -> Grants:
    for key in sorted(block):
        if key not in _GRANT_KEYS:
            raise ConfigError(f"{where}grants.{key}: the package models no grant of that name")
    for key in ("bind", "task_session"):
        if block.get(key):
            raise ConfigError(f"{where}grants.{key}: the package models no {key} grant")
    return Grants(
        read=list(block.get("read") or ()),
        write=list(block.get("write") or ()),
        execute=list(block.get("execute") or ()),
        spawn=list(block.get("spawn") or ()),
    )


def _budget(block: Mapping[str, Any], where: str) -> Budget:
    for key in sorted(block):
        if key not in _BUDGET_KEYS:
            raise ConfigError(f"{where}budget.{key}: the package models no budget limit of that name")
    if block.get("model_calls") is None:
        raise ConfigError(f"{where}budget.model_calls: required")
    stated = {key: block[key] for key in _BUDGET_KEYS if block.get(key) is not None}
    return Budget(**stated)


def _tool_def(entry: Mapping[str, Any], where: str) -> ToolDef:
    for key in sorted(entry):
        if key not in _TOOL_DEF_KEYS:
            raise ConfigError(f"{where}.{key}: the package models no tool definition key of that name")
    if not isinstance(entry.get("exec"), str) or not isinstance(entry.get("description"), str):
        raise ConfigError(f"{where}: `exec` and `description` are required strings")
    return ToolDef(
        exec=entry["exec"],
        description=entry["description"],
        instruction=entry.get("instruction"),
        network=bool(entry.get("network")),
        timeout_seconds=entry.get("timeout_seconds"),
        cwd=entry.get("cwd"),
    )


def _done_when(block: Mapping[str, Any], where: str) -> DoneWhen:
    for key in sorted(block):
        if key not in ("verify", "retries", "returns"):
            raise ConfigError(f"{where}done_when.{key}: the package models no completion key of that name")
    if "verify" in block:
        return Verified(
            verify=str(block["verify"]),
            retries=int(block.get("retries", _DEFAULT_RETRIES)),
            returns=block.get("returns"),
        )
    if "returns" not in block:
        raise ConfigError(f"{where}done_when: states neither `verify` nor `returns`")
    # A gate that only returns carries no retry count in the document the
    # package writes, so a stated count other than the runtime's default
    # would be lost.
    if block.get("retries", _DEFAULT_RETRIES) != _DEFAULT_RETRIES:
        raise ConfigError(f"{where}done_when.retries: the package writes no retry count beside `returns`")
    return Returns(schema=block["returns"])


def _gate(fields: dict[str, Any], verify: HostTool | PathLike, root: Path, retries: int) -> None:
    """Make `verify` the document's completion gate, as `foe init` does.

    The bounds that cap the re-fires a finding causes are raised first, so
    that the gate the document gains can feed a finding back. A return
    schema the document already declares is kept beside the verifier, which
    docs/config.md `done_when` makes the verifier check: dropping it would
    take the `return` tool and the citation rule away from the episode.
    """
    _admit_refires(fields, retries)
    returns = _declared_returns(fields.get("done_when"))
    if isinstance(verify, HostTool):
        fields["tools"] = [*fields["tools"], verify]
        fields["done_when"] = Verified(verify=verify, retries=retries, returns=returns)
        return
    path = Path(os.fspath(verify))
    if not path.is_absolute():
        raise ConfigError(f"verify: {os.fspath(verify)!r} is not an absolute path")
    definition = ToolDef(exec=path, description=_VERIFIER_DESCRIPTION, cwd=root)
    # The document's own keys hold the typed arguments the contract takes;
    # the workflow is carried as the binary printed it, so its nodes take the
    # definition as the object the document states.
    _add_verifier(fields, definition)
    for contract in _node_contracts(fields.get("workflow")):
        _add_verifier(contract, definition.to_dict(f"workflow.{_VERIFIER_TOOL}"))
    fields["done_when"] = Verified(verify=_VERIFIER_TOOL, retries=retries, returns=returns)


def _declared_returns(done_when: Any) -> type | Mapping[str, Any] | None:
    """The return schema a completion rule states, if it states one."""
    if isinstance(done_when, Returns):
        return done_when.schema
    if isinstance(done_when, Verified):
        return done_when.returns
    return None


def _add_verifier(contract: dict[str, Any], definition: ToolDef | dict[str, Any]) -> None:
    tools = list(contract.get("tools") or ())
    if _VERIFIER_TOOL in tools:
        raise ConfigError(f"verify: a contract of the document already carries a tool named {_VERIFIER_TOOL!r}")
    contract["tools"] = [*tools, _VERIFIER_TOOL]
    contract["tool_defs"] = {**(contract.get("tool_defs") or {}), _VERIFIER_TOOL: definition}


def _node_contracts(workflow: Any) -> list[dict[str, Any]]:
    """Every contract a workflow node runs a model under, at every depth."""
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    found: list[dict[str, Any]] = []
    for node in _objects(nodes):
        contract = node.get("model")
        if isinstance(contract, dict):
            found.append(contract)
            found.extend(_node_contracts(contract.get("workflow")))
    return found


def _admit_refires(fields: dict[str, Any], retries: int) -> None:
    """Raise the bounds that would stop a finding from being fed back.

    docs/workflow.md "Completion" re-fires the nearest model ancestor of the
    node that completed the workflow, and "Bounds" makes `max_fires` cap
    those re-fires and the episode budget cap everything. A node keeps a
    bound already wide enough. A document that declares no workflow runs one
    episode and feeds a finding back into that episode, so its bounds admit
    every re-fire as they stand.
    """
    workflows = list(_workflows(fields.get("workflow")))
    if not workflows:
        return
    for workflow in workflows:
        for name in _refired(workflow):
            node = workflow["nodes"][name]
            stated = node.get("max_fires")
            current = stated if isinstance(stated, int) else _DEFAULT_MAX_FIRES
            node["max_fires"] = max(current, retries + 1)
    budget = fields["budget"]
    stated_episodes = budget.max_episodes
    episodes = _DEFAULT_MAX_EPISODES if stated_episodes is None else stated_episodes
    fields["budget"] = dataclasses.replace(budget, max_episodes=episodes + retries)


def _workflows(workflow: Any) -> Iterator[dict[str, Any]]:
    """A workflow graph and every graph nested below it, at every depth."""
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if not isinstance(nodes, dict):
        return
    yield workflow
    for node in _objects(nodes):
        yield from _workflows(node.get("workflow"))
        contract = node.get("model")
        if isinstance(contract, dict):
            yield from _workflows(contract.get("workflow"))


def _refired(workflow: Mapping[str, Any]) -> Iterator[str]:
    """The nodes a finding can re-fire, each named once.

    docs/workflow.md "Completion": a workflow completes when a terminal node
    completes or when a chosen branch label has no successors, and findings
    re-fire the nearest model ancestor of the node that completed it. A node
    that runs a model is its own nearest model ancestor.
    """
    nodes = {name: node for name, node in workflow["nodes"].items() if isinstance(node, dict)}
    predecessors = _predecessors(nodes)
    seen: set[str] = set()
    for name in sorted(nodes):
        node = nodes[name]
        branches = node.get("branches")
        ends = bool(node.get("terminal")) or (
            isinstance(branches, dict) and any(not successors for successors in branches.values())
        )
        target = _nearest_model(nodes, predecessors, name) if ends else None
        if target is not None and target not in seen:
            seen.add(target)
            yield target


def _predecessors(nodes: Mapping[str, dict[str, Any]]) -> dict[str, set[str]]:
    """Every edge source of every node: data inputs and branch sources alike.

    The invocation task is no node and is left out, as docs/workflow.md
    "Nodes" states that it imposes no ordering.
    """
    found: dict[str, set[str]] = {name: set() for name in nodes}
    for name, node in nodes.items():
        for source in node.get("follows") or ():
            if source != _TASK_SOURCE and source in found:
                found[name].add(str(source))
        for successors in (node.get("branches") or {}).values():
            for target in successors or ():
                if target in found:
                    found[str(target)].add(name)
    return found


def _nearest_model(nodes: Mapping[str, dict[str, Any]], predecessors: Mapping[str, set[str]], name: str) -> str | None:
    """The node running a model that is nearest to `name` walking edges back."""
    queue = deque([name])
    seen: set[str] = set()
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        if isinstance(nodes[current].get("model"), dict):
            return current
        queue.extend(sorted(predecessors.get(current, ())))
    return None
