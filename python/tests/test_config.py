"""Execution-contract construction and the document it emits, per docs/config.md."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import foe


@foe.tool
def mutation_usage(mutation_id: str, fs: foe.ReadFS) -> dict[str, int]:
    """Find where a mutation point's value or symbol is referenced."""
    return {"count": 0}


@foe.tool
def validate_patches(candidate: str) -> list[str]:
    """Check a proposal for missing sections."""
    return []


@dataclass
class Experiment:
    """A proposed experiment."""

    title: str
    steps: list[str]
    budget_hours: float | None = None


def make_contract() -> foe.ExecutionContract:
    return foe.ExecutionContract(
        name="zicato-proposer",
        instructions={"20-grounding": "Ground every claim.", "10-charter": "You propose experiments."},
        tools=["read", "grep", mutation_usage],
        grants=foe.Grants(
            read=["/gen/v37/snapshot"],
            write=["/tmp/scratch"],
            execute=["/opt/rust-toolchain"],
        ),
        budget=foe.Budget(model_calls=12, input_tokens=160_000, output_tokens=40_000, seconds=600),
        done_when=foe.Verified(verify=validate_patches, retries=2),
    )


def test_every_runtime_builtin_is_available_to_contract_construction() -> None:
    contract = foe.ExecutionContract(
        name="all-builtins",
        instructions={"role": "Exercise the built-in tool catalogue."},
        tools=sorted(foe.BUILTIN_TOOLS),
        grants=foe.Grants(read=["/tmp"], write=["/tmp"], spawn=["child"]),
        budget=foe.Budget(model_calls=1),
        child_contracts={
            "child": foe.ExecutionContract(
                name="child",
                instructions={"role": "Finish the child task."},
                tools=["read"],
                grants=foe.Grants(read=["/tmp"]),
                budget=foe.Budget(model_calls=1),
            )
        },
    )
    assert set(contract.to_dict()["tools"]) == foe.BUILTIN_TOOLS


EXPECTED = {
    "version": 4,
    "name": "zicato-proposer",
    "instructions": {"10-charter": "You propose experiments.", "20-grounding": "Ground every claim."},
    "tools": ["read", "grep", "mutation_usage", "validate_patches"],
    "host_tools": {
        "mutation_usage": {
            "description": "Find where a mutation point's value or symbol is referenced.",
            "params": {
                "type": "object",
                "properties": {"mutation_id": {"type": "string"}},
                "required": ["mutation_id"],
                "additionalProperties": False,
            },
            "effect": "reads",
        },
        "validate_patches": {
            "description": "Check a proposal for missing sections.",
            "params": {
                "type": "object",
                "properties": {"candidate": {"type": "string"}},
                "required": ["candidate"],
                "additionalProperties": False,
            },
            "effect": "pure",
        },
    },
    "grants": {
        "read": ["/gen/v37/snapshot"],
        "write": ["/tmp/scratch"],
        "execute": ["/opt/rust-toolchain"],
    },
    "budget": {"model_calls": 12, "input_tokens": 160000, "output_tokens": 40000, "seconds": 600},
    "done_when": {"verify": "validate_patches", "retries": 2},
}


def test_to_json_round_trips_to_the_expected_document() -> None:
    assert json.loads(make_contract().to_json()) == EXPECTED


def test_host_tools_entry_has_the_four_specified_fields() -> None:
    @foe.tool(instruction="Call it once per mutation point.")
    def count_refs(symbol: str, fs: foe.ReadFS, limit: int = 20) -> dict[str, int]:
        """Count references to a symbol."""
        return {}

    contract = foe.ExecutionContract(
        name="p",
        instructions={"role": "r"},
        tools=[count_refs],
        grants=foe.Grants(read=["/src"]),
        budget=foe.Budget(model_calls=1),
    )
    entry = contract.to_dict()["host_tools"]["count_refs"]
    assert set(entry) == {"description", "instruction", "params", "effect"}
    assert entry["description"] == "Count references to a symbol."
    assert entry["instruction"] == "Call it once per mutation point."
    assert entry["effect"] == "reads"
    assert entry["params"] == count_refs.spec.params
    assert entry["params"] == {
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["symbol"],
        "additionalProperties": False,
    }
    # Without an instruction the entry has exactly three fields.
    assert set(EXPECTED["host_tools"]["mutation_usage"]) == {"description", "params", "effect"}


def test_verified_callable_generates_a_pure_one_parameter_host_tool() -> None:
    doc = make_contract().to_dict()
    entry = doc["host_tools"]["validate_patches"]
    assert entry["effect"] == "pure"
    assert list(entry["params"]["properties"]) == ["candidate"]
    assert doc["done_when"] == {"verify": "validate_patches", "retries": 2}


def test_host_verifier_requires_one_data_parameter() -> None:
    @foe.tool
    def no_candidate() -> list[str]:
        """Check without receiving a candidate."""
        return []

    @foe.tool
    def split_candidate(summary: str, risks: list[str]) -> list[str]:
        """Check separate candidate fields."""
        return []

    for verifier, count in [(no_candidate, 0), (split_candidate, 2)]:
        with pytest.raises(foe.ConfigError, match=rf"done_when.verify: .*one parameter; found {count}"):
            foe.ExecutionContract(
                name="p",
                instructions={"role": "r"},
                tools=[verifier],
                grants=foe.Grants(read=["/src"]),
                budget=foe.Budget(model_calls=1),
                done_when=foe.Verified(verify=verifier),
            )


def test_to_json_with_task_appends_task_and_omits_model() -> None:
    doc = json.loads(make_contract().to_json("Propose the next experiment."))
    assert doc["task"] == "Propose the next experiment."
    assert "model" not in doc


def test_returns_derives_schema_from_a_dataclass() -> None:
    contract = foe.ExecutionContract(
        name="p",
        instructions={"role": "r"},
        tools=["read"],
        grants=foe.Grants(read=["/src"]),
        budget=foe.Budget(model_calls=1),
        done_when=foe.Returns(Experiment),
    )
    assert contract.to_dict()["done_when"] == {
        "returns": {
            "type": "object",
            "description": "A proposed experiment.",
            "properties": {
                "title": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "budget_hours": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            },
            "required": ["title", "steps"],
            "additionalProperties": False,
        }
    }


def test_verified_accepts_a_tool_name_and_combines_with_returns() -> None:
    contract = foe.ExecutionContract(
        name="p",
        instructions={"role": "r"},
        tools=["read", validate_patches],
        grants=foe.Grants(read=["/src"]),
        budget=foe.Budget(model_calls=1),
        done_when=foe.Verified(verify="validate_patches", retries=1, returns=Experiment),
    )
    done_when = contract.to_dict()["done_when"]
    assert done_when["verify"] == "validate_patches"
    assert done_when["retries"] == 1
    assert done_when["returns"]["required"] == ["title", "steps"]


def test_host_tool_colliding_with_a_builtin_is_an_error() -> None:
    @foe.tool
    def read(path: str) -> str:
        """Read a file."""
        return ""

    with pytest.raises(foe.ConfigError, match="tools: host tool 'read' collides with the built-in tool"):
        foe.ExecutionContract(
            name="p",
            instructions={"role": "r"},
            tools=[read],
            grants=foe.Grants(read=["/src"]),
            budget=foe.Budget(model_calls=1),
        )


def test_unknown_tool_name_is_an_error() -> None:
    with pytest.raises(foe.ConfigError, match="tools: 'ruff' names no built-in tool"):
        foe.ExecutionContract(
            name="p",
            instructions={"role": "r"},
            tools=["ruff"],
            grants=foe.Grants(read=["/src"]),
            budget=foe.Budget(model_calls=1),
        )


def test_effect_beyond_grants_is_an_error() -> None:
    @foe.tool
    def write_note(text: str, out: foe.WriteFS) -> None:
        """Write a note."""

    with pytest.raises(foe.ConfigError, match="declares effect writes and grants.write is empty"):
        foe.ExecutionContract(
            name="p",
            instructions={"role": "r"},
            tools=[write_note],
            grants=foe.Grants(read=["/src"]),
            budget=foe.Budget(model_calls=1),
        )
    with pytest.raises(foe.ConfigError, match="'edit' declares effect writes"):
        foe.ExecutionContract(
            name="p",
            instructions={"role": "r"},
            tools=["edit"],
            grants=foe.Grants(read=["/src"]),
            budget=foe.Budget(model_calls=1),
        )


def test_relative_grant_path_is_an_error() -> None:
    contract = foe.ExecutionContract(
        name="p",
        instructions={"role": "r"},
        tools=["read"],
        grants=foe.Grants(read=["src"]),
        budget=foe.Budget(model_calls=1),
    )
    with pytest.raises(foe.ConfigError, match="grants.read: 'src' is not an absolute path"):
        contract.to_dict()


def test_tool_defs_and_child_contracts_serialize() -> None:
    child = foe.ExecutionContract(
        name="survey",
        instructions={"role": "You survey."},
        tools=["read"],
        grants=foe.Grants(read=["/src"]),
        budget=foe.Budget(model_calls=3),
    )
    contract = foe.ExecutionContract(
        name="lead",
        instructions={"role": "You lead."},
        tools=["read", "ruff", "spawn"],
        tool_defs={"ruff": foe.ToolDef(exec="/usr/bin/ruff", description="Lint.", network=False, timeout_seconds=30)},
        grants=foe.Grants(read=["/src"], spawn=["survey"]),
        budget=foe.Budget(model_calls=10, max_depth=2),
        child_contracts={"survey": child},
        sandbox="off",
    )
    doc = contract.to_dict()
    assert doc["tool_defs"] == {"ruff": {"exec": "/usr/bin/ruff", "description": "Lint.", "timeout_seconds": 30}}
    assert doc["grants"] == {"read": ["/src"], "spawn": ["survey"]}
    assert doc["budget"] == {"model_calls": 10, "max_depth": 2}
    assert doc["sandbox"] == {"mode": "off"}
    assert doc["child_contracts"] == {
        "survey": {
            "name": "survey",
            "instructions": {"role": "You survey."},
            "tools": ["read"],
            "grants": {"read": ["/src"]},
            "budget": {"model_calls": 3},
        }
    }


def test_fingerprint_runs_plan_and_returns_the_hash(fake_binary: Path) -> None:
    contract = make_contract()
    fingerprint = contract.fingerprint(fake_binary)
    assert fingerprint.startswith("sha256:")
    assert len(fingerprint) == len("sha256:") + 64
    assert contract.fingerprint(fake_binary) == fingerprint
