"""`foe.builtin` over the documents the built binary carries.

Every test here runs the binary, so the module is skipped when
`target/debug/foe` is absent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

import foe

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "target" / "debug" / "foe"

pytestmark = pytest.mark.skipif(not BINARY.is_file(), reason="target/debug/foe has not been built")

USAGE = {"input": 10, "output": 5, "cache_read": 0}


def _plan(config: str, cwd: Path) -> dict[str, Any]:
    """What `foe plan --json` prints for a document, from the directory `cwd`."""
    completed = subprocess.run(
        [str(BINARY), "plan", "--config", config, "--json"], capture_output=True, text=True, check=True, cwd=cwd
    )
    parsed: dict[str, Any] = json.loads(completed.stdout)
    return parsed


def _root(tmp_path: Path) -> Path:
    root = Path(os.path.realpath(tmp_path)) / "project"
    root.mkdir()
    (root / "notes.txt").write_text("A repository that needs no change.\n", encoding="utf-8")
    return root


def _contracts(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The document and every contract a workflow node runs a model under."""
    nodes = (document.get("workflow") or {}).get("nodes") or {}
    return [document] + [node["model"] for node in nodes.values() if "model" in node]


def _verifier(root: Path, findings: int) -> Path:
    """An executable verifier that reports a finding on its first `findings` runs.

    It follows the verifier contract of docs/config.md `done_when`: one
    finding per line on standard output and exit status zero either way. It
    counts its runs in a file beside itself, so that a fixed number of
    findings is followed by acceptance. The verifier runs under the
    document's execute grant, which admits a fixed set of programs and
    excludes `cat`, so the script counts with shell builtins alone.
    """
    verifier = root / "verify"
    counter = root / "runs"
    script = [
        "#!/bin/sh",
        "while read -r _; do :; done",
        "count=0",
        f'if [ -f "{counter}" ]; then read -r count < "{counter}"; fi',
        f'echo $((count + 1)) > "{counter}"',
        f'if [ "$count" -lt {findings} ]; then echo "the work is not finished"; fi',
        "exit 0",
        "",
    ]
    verifier.write_text("\n".join(script), encoding="utf-8")
    os.chmod(verifier, 0o755)
    return verifier


def _run(contract: foe.ExecutionContract, task: str, backend: foe.ModelBackend, log_dir: Path) -> tuple[foe.Outcome, Path]:
    """Run one document and return its outcome and the episode directory.

    `log_dir` is the directory the binary creates the episode's own directory
    under, which docs/design.md "The command line" states for `--log-dir`.
    """

    async def scenario() -> tuple[foe.Outcome, Path]:
        handle = await foe.start_config(
            contract.to_dict(task), model_backend=backend, binary=BINARY, log_dir=log_dir
        )
        outcome = await handle.wait()
        assert handle.log_dir is not None
        return outcome, handle.log_dir

    return asyncio.run(scenario())


def _fires(log_dir: Path, node: str) -> int:
    """How many times a workflow node started, from the episode log."""
    started = 0
    for line in (log_dir / "episode.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "workflow/node-start" and (event.get("data") or {}).get("node") == node:
            started += 1
    return started


def test_the_contract_is_the_document_the_binary_carries_over_the_root(tmp_path: Path) -> None:
    """The package adds the root to the execute grants and changes nothing else.

    docs/config.md "Fingerprint summary" states that the counts in `grants`
    participate in the fingerprint while the concrete paths and the `model`
    block do not. The document `foe plan` prints therefore fingerprints
    differently once the root joins the execute grant of the document and of
    every workflow node, which is the grant `foe init` writes. Applying that
    one change to the printed document reproduces the fingerprint of the
    contract the package returns, so every instruction, tool, budget, and
    workflow node came from the binary unchanged.
    """
    root = _root(tmp_path)
    contract = foe.builtin("coding", root, binary=BINARY)
    printed = _plan("builtin:coding", root)

    document = printed["contract"]
    for part in _contracts(document):
        part["grants"]["execute"] = [*part["grants"]["execute"], str(root)]
    document["version"] = foe.CONFIG_VERSION
    document["task"] = "fingerprint"
    rooted = tmp_path / "rooted.json"
    rooted.write_text(json.dumps(document), encoding="utf-8")

    assert contract.fingerprint(BINARY) == _plan(str(rooted), root)["contract_fingerprint"]
    assert contract.fingerprint(BINARY) != printed["contract_fingerprint"]


def test_the_document_names_the_root_and_carries_the_workflow(tmp_path: Path) -> None:
    root = _root(tmp_path)
    document = foe.builtin("coding", root, binary=BINARY).to_dict()
    assert document["name"] == "coding"
    for part in _contracts(document):
        assert part["grants"]["read"] == [str(root)]
        assert part["grants"]["write"] == [str(root)]
        assert part["grants"]["execute"][-1] == str(root)
    assert sorted(document["workflow"]["nodes"]) == ["assess-task", "implement-task", "repair-task"]


def test_a_name_the_binary_does_not_carry_is_refused_with_its_message(tmp_path: Path) -> None:
    with pytest.raises(foe.ConfigError) as caught:
        foe.builtin("proofreading", _root(tmp_path), binary=BINARY)
    assert "builtin:proofreading" in str(caught.value)
    assert "builtin:coding" in str(caught.value)


def test_the_model_block_is_the_hosts_to_choose(tmp_path: Path) -> None:
    """Without a model the host answers every request; with one, the block stands alone.

    docs/config.md makes a contract that declares no `model` block one whose
    model requests reach the host, and makes a contract that omits the block
    inherit the nearest ancestor's, so the single block at the top of the
    document serves every workflow node.
    """
    root = _root(tmp_path)
    without = foe.builtin("coding", root, binary=BINARY).to_dict()
    assert all("model" not in part for part in _contracts(without))

    endpoint = foe.Model(provider="compatible-http", model="fixture-model", options={"base_url": "http://127.0.0.1:1"})
    with_endpoint = foe.builtin("coding", root, binary=BINARY, model=endpoint).to_dict()
    stated = {"provider": "compatible-http", "model": "fixture-model", "base_url": "http://127.0.0.1:1"}
    assert with_endpoint["model"] == stated
    assert all("model" not in part for part in _contracts(with_endpoint)[1:])


def test_a_verifier_path_gates_the_document_and_reaches_every_node(tmp_path: Path) -> None:
    root = _root(tmp_path)
    verifier = _verifier(root, findings=0)

    document = foe.builtin("coding", root, binary=BINARY, verify=verifier).to_dict()
    assert document["done_when"] == {"verify": "check", "retries": 2}
    for part in _contracts(document):
        assert part["tools"][-1] == "check"
        assert part["tool_defs"]["check"]["exec"] == str(verifier)
        assert part["tool_defs"]["check"]["cwd"] == str(root)
    assert foe.builtin("coding", root, binary=BINARY, verify=verifier).fingerprint(BINARY).startswith("sha256:")


def test_the_bounds_admit_every_re_fire_a_finding_causes(tmp_path: Path) -> None:
    """The gated document raises the bounds that would otherwise stop a re-fire.

    docs/workflow.md "Completion" re-fires the nearest model ancestor of the
    node that completed the workflow, and "Bounds" makes `max_fires` cap
    those re-fires. Both coding nodes that can complete the workflow run a
    model: `assess-task` carries a branch label with no successors, and
    `repair-task` is terminal. Each re-fire runs one further episode, so the
    episode count rises by the same number.
    """
    root = _root(tmp_path)
    verifier = _verifier(root, findings=0)
    printed = _plan("builtin:coding", root)["contract"]

    for retries in (0, 2, 7):
        document = foe.builtin("coding", root, binary=BINARY, verify=verifier, retries=retries).to_dict()
        nodes = document["workflow"]["nodes"]
        assert document["done_when"] == {"verify": "check", "retries": retries}
        assert nodes["assess-task"]["max_fires"] == retries + 1
        assert nodes["repair-task"]["max_fires"] == retries + 1
        assert "max_fires" not in nodes["implement-task"]
        assert document["budget"]["max_episodes"] == printed["budget"]["max_episodes"] + retries

    ungated = foe.builtin("coding", root, binary=BINARY).to_dict()
    assert ungated["budget"]["max_episodes"] == printed["budget"]["max_episodes"]
    assert all("max_fires" not in node for node in ungated["workflow"]["nodes"].values())


def test_a_finding_that_the_next_attempt_resolves_completes_the_run(tmp_path: Path) -> None:
    """One finding re-fires the completing node, and the second attempt is accepted."""
    root = _root(tmp_path)
    contract = foe.builtin("coding", root, binary=BINARY, verify=_verifier(root, findings=1))
    task = "Report that the repository needs no change."
    outcome, episode = _run(contract, task, _reads_then_returns(root / "notes.txt"), tmp_path / "episodes")
    assert isinstance(outcome, foe.Completed), outcome
    assert _fires(episode, "assess-task") == 2


def test_a_finding_that_never_clears_blocks_after_the_stated_retries(tmp_path: Path) -> None:
    """Findings that outlast `retries` end the episode as docs/log-format.md states.

    `verification-unsatisfiable` is the blocked code for a gate whose
    retries were spent with findings still present. The completing node
    fires once for the first completion and once per retry.
    """
    root = _root(tmp_path)
    retries = 3
    contract = foe.builtin("coding", root, binary=BINARY, verify=_verifier(root, findings=99), retries=retries)
    task = "Report that the repository needs no change."
    outcome, episode = _run(contract, task, _reads_then_returns(root / "notes.txt"), tmp_path / "episodes")
    assert isinstance(outcome, foe.Blocked), outcome
    assert outcome.code == "verification-unsatisfiable"
    assert _fires(episode, "assess-task") == retries + 1


def test_a_host_tool_verifier_gates_the_document(tmp_path: Path) -> None:
    @foe.tool
    def check_candidate(candidate: str) -> list[str]:
        """Judge a completion candidate and return one finding per problem."""
        return []

    document = foe.builtin("coding", _root(tmp_path), binary=BINARY, verify=check_candidate).to_dict()
    assert document["done_when"] == {"verify": "check_candidate", "retries": 2}
    assert document["tools"][-1] == "check_candidate"
    assert document["host_tools"]["check_candidate"]["effect"] == "pure"


def test_an_absent_root_and_a_relative_verifier_are_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    with pytest.raises(foe.ConfigError):
        foe.builtin("coding", root / "absent", binary=BINARY)
    with pytest.raises(foe.ConfigError):
        foe.builtin("coding", root, binary=BINARY, verify="verify")


def test_the_returned_contract_runs_under_run_config(tmp_path: Path) -> None:
    """The document the package returns runs, with this host answering the model."""
    root = _root(tmp_path)
    contract = foe.builtin("coding", root, binary=BINARY)
    task = "Report that the repository needs no change."
    outcome, episode = _run(contract, task, _reads_then_returns(root / "notes.txt"), tmp_path / "episodes")
    assert isinstance(outcome, foe.Completed), outcome
    events = (episode / "episode.jsonl").read_text(encoding="utf-8")
    assert '"type":"workflow/node-end"' in events


def _reads_then_returns(path: Path) -> foe.ModelBackend:
    """A backend that reads one file and then completes the node it is serving.

    Every node of the coding workflow completes through the synthesized
    `return` tool, whose schema travels with the request, so one backend
    answers every node without naming any of them. The schema requires each
    claim to cite the seq of a successful tool result, which the runtime
    prefixes to every rendered result, so the backend reads a file first and
    cites the result it produced.
    """

    async def backend(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        cited = _cited_seq(request["messages"])
        if cited is None:
            async for chunk in _call("read", {"path": str(path)}):
                yield chunk
            return
        schema = next((tool["parameters"] for tool in request["tools"] if tool["name"] == "return"), None)
        if schema is None:
            yield {"kind": "error", "message": "the request offered no `return` tool", "retryable": False}
            return
        async for chunk in _call("return", _smallest(schema, cited)):
            yield chunk

    return backend


async def _call(name: str, arguments: Any) -> AsyncIterator[dict[str, Any]]:
    yield {"kind": "tool_call_start", "id": "tc_1", "name": name}
    yield {"kind": "tool_call_delta", "id": "tc_1", "delta": json.dumps(arguments)}
    yield {"kind": "tool_call_end", "id": "tc_1"}
    yield {"kind": "done", "stop": "tool", "usage": USAGE}


def _cited_seq(messages: list[dict[str, Any]]) -> int | None:
    """The seq of the last successful `read` result, which the runtime renders."""
    found = None
    for message in messages:
        if message.get("role") != "tool" or message.get("name") != "read" or message.get("is_error"):
            continue
        stated = re.match(r"\[seq (\d+)\]", str(message.get("rendered", "")))
        if stated is not None:
            found = int(stated.group(1))
    return found


def _smallest(schema: dict[str, Any], cited: int) -> Any:
    """The smallest value a schema in the docs/config.md subset accepts.

    Every integer these schemas declare cites the seq of a tool result, so
    `cited` fills each one.
    """
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or list(properties)
        return {key: _smallest(value, cited) for key, value in properties.items() if key in required}
    if kind == "array":
        return [_smallest(schema["items"], cited) for _ in range(max(1, int(schema.get("minItems", 0))))]
    if kind in ("integer", "number"):
        return cited
    if kind == "boolean":
        return False
    return "The repository needs no change."
