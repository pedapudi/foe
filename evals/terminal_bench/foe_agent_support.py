#!/usr/bin/python3
"""Pure helpers for the Foe Harbor adapter."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path, PurePosixPath
from typing import Any


CODING_INSTRUCTION = (
    "You are a coding agent working in the current directory, which is the root "
    "of every relative path. Make the requested change, then verify it by running "
    "the relevant build or tests before you finish. For a task that defines a "
    "program interface, completion requires at least two materially different "
    "behavioral inputs, including one that stresses parsing, length, or state."
)
EVALUATION_LOOP_THRESHOLD = 8
MIN_AUXILIARY_MODEL_CALLS = 6
COMPLETION_CHECK_RETRIES = 12
SEPARATE_ASSESSMENT_CORRECTIONS = 3
BUILTIN_WORKFLOW_REQUIRED_OPTIONS = (
    "--model",
    "--service-tier",
    "--key-file",
    "--verify",
    "--sandbox",
    "--headless",
    "--log-dir",
)
FIXED_EXECUTABLE_PATHS = (
    ("sh", "/bin/sh"),
    ("bash", "/bin/bash"),
    ("git", "/usr/bin/git"),
    ("python3", "/usr/bin/python3"),
    ("file", "/usr/bin/file"),
    ("xxd", "/usr/bin/xxd"),
    ("od", "/usr/bin/od"),
    ("awk", "/usr/bin/awk"),
    ("strings", "/usr/bin/strings"),
    ("gcc", "/usr/bin/gcc"),
    ("clang", "/usr/bin/clang"),
    ("make", "/usr/bin/make"),
    ("cmake", "/usr/bin/cmake"),
    ("cargo", "/usr/bin/cargo"),
    ("node", "/usr/bin/node"),
    ("go", "/usr/bin/go"),
)


def normalized_plan(plan: dict[str, Any], task: str) -> dict[str, Any]:
    """Bind a retained plan to the exact controller-observed task."""
    if not isinstance(plan, dict):
        raise ValueError("installed Foe plan is an object")
    if plan.get("task", task) != task:
        raise ValueError("installed Foe reported a task different from the controller instruction")
    if not isinstance(plan.get("program"), dict) or "task" in plan["program"]:
        raise ValueError("installed Foe plan program must omit task")
    return {**plan, "task": task}


def replace_json(path: Path, value: Any) -> None:
    """Replace a retained container-owned JSON file through its writable directory."""
    temporary = path.with_name(f".{path.name}.normalized")
    temporary.unlink(missing_ok=True)
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


COMPLETION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        "changed_paths": {
            "type": "array",
            "items": {"type": "string", "maxLength": 512},
            "maxItems": 64,
        },
        "validation": {
            "type": "array",
            "items": {"type": "string", "maxLength": 1000},
            "minItems": 1,
            "maxItems": 32,
        },
        "unresolved_risks": {
            "type": "array",
            "items": {"type": "string", "maxLength": 1000},
            "maxItems": 16,
        },
    },
    "required": ["summary", "changed_paths", "validation", "unresolved_risks"],
    "additionalProperties": False,
}


def parse_boolean(value: bool | str, name: str) -> bool:
    """Parse one Harbor agent keyword without accepting ambiguous values."""
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise ValueError(f"{name} must be true or false")


def missing_builtin_workflow_options(help_text: str) -> list[str]:
    """Return built-in invocation options absent from a Foe help page."""
    declared = {
        fields[0]
        for line in help_text.splitlines()
        if (fields := line.split()) and fields[0].startswith("--")
    }
    return [
        option
        for option in BUILTIN_WORKFLOW_REQUIRED_OPTIONS
        if option not in declared
    ]


def builtin_workflow_arguments(
    instruction: str,
    model_name: str,
    credential_path: str,
    completion_checker: str | None,
    episode_directory: str,
    service_tier: str,
    binary: str = "/usr/local/bin/foe",
) -> tuple[str, ...]:
    """Return the direct built-in coding workflow invocation for one trial."""
    if "/" not in model_name or model_name.startswith("/") or model_name.endswith("/"):
        raise ValueError("model must have the form provider/model")
    if service_tier not in ("default", "priority"):
        raise ValueError("service tier must be default or priority")
    paths = {
        "binary": binary,
        "credential": credential_path,
        "episode directory": episode_directory,
    }
    if completion_checker is not None:
        paths["completion checker"] = completion_checker
    for name, value in paths.items():
        if not PurePosixPath(value).is_absolute():
            raise ValueError(f"{name} path must be absolute")
    arguments = [
        binary,
        instruction,
        "--model",
        model_name,
        "--service-tier",
        service_tier,
        "--key-file",
        credential_path,
    ]
    if completion_checker is not None:
        arguments.extend(("--verify", completion_checker))
    arguments.extend(("--sandbox", "off", "--headless", "--log-dir", episode_directory))
    return tuple(arguments)


def program_document_from_episode_start(
    episode: Path,
    task: str,
) -> tuple[dict[str, Any], str]:
    """Rebuild a plan input from the resolved program that a root recorded."""
    try:
        with episode.open(encoding="utf-8") as stream:
            first = json.loads(stream.readline())
    except json.JSONDecodeError as error:
        raise ValueError(f"root episode start is not valid JSON: {episode}") from error
    data = first.get("data") if isinstance(first, dict) else None
    if (
        not isinstance(first, dict)
        or first.get("seq") != 0
        or first.get("type") != "episode/start"
        or not isinstance(data, dict)
    ):
        raise ValueError("root episode must begin with episode/start at sequence zero")
    if data.get("task") != task:
        raise ValueError("root episode task differs from the controller instruction")
    program = data.get("program")
    if not isinstance(program, dict) or "task" in program or "version" in program:
        raise ValueError("root episode program must omit task and format version")
    identity = data.get("identity")
    encoded_identity = identity.removeprefix("sha256:") if isinstance(identity, str) else ""
    if (
        not isinstance(identity, str)
        or len(encoded_identity) != 64
        or any(character not in "0123456789abcdef" for character in encoded_identity)
    ):
        raise ValueError("root episode program identity is invalid")
    return {"version": 3, "task": task, **program}, identity


def normalized_episode_plan(
    plan: dict[str, Any],
    task: str,
    program: dict[str, Any],
    identity: str,
) -> dict[str, Any]:
    """Require a reconstructed plan to describe the recorded root program."""
    plan = normalized_plan(plan, task)
    expected_program = {key: value for key, value in program.items() if key not in ("version", "task")}
    if plan.get("program") != expected_program:
        raise ValueError("reconstructed plan program differs from the root episode start")
    if plan.get("identity") != identity:
        raise ValueError("reconstructed plan identity differs from the root episode start")
    return plan


def missing_episode_diagnostic(
    logs_dir: Path,
    exit_code: int | None,
    sensitive_values: frozenset[str] = frozenset(),
) -> str | None:
    """Describe an early Foe exit when no root episode log was retained."""
    if (logs_dir / "foe-episode" / "episode.jsonl").is_file():
        return None
    stderr_path = logs_dir / "foe.stderr"
    detail = (
        stderr_path.read_text(encoding="utf-8", errors="replace").strip()
        if stderr_path.is_file()
        else "standard error was not retained"
    )
    for value in sensitive_values:
        detail = detail.replace(value, "[credential redacted]")
    return (
        f"Foe exited with status {exit_code} before creating an episode log: "
        f"{detail[-2000:]}"
    )


def fixed_executable_probe_command() -> str:
    """Return one fixed-path probe that works in a minimal POSIX shell."""
    commands = []
    for name, path in FIXED_EXECUTABLE_PATHS:
        commands.append(
            f"if test -x {path}; then echo '{name}={path}'; "
            f"else echo '{name}=not found at {path}'; fi"
        )
    return "; ".join(commands)


def schema_probe_command(binary: str = "/usr/local/bin/foe") -> str:
    """Return the provider-free command that validates an installed Foe binary."""
    if not PurePosixPath(binary).is_absolute():
        raise ValueError("binary path must be absolute")
    return f"{shlex.quote(binary)} plan --schema >/dev/null"


def describe_container_environment(working_directory: str, probe_output: str) -> str:
    """Validate fixed-path observations and render one model instruction."""
    if not working_directory.startswith("/"):
        raise ValueError("working directory must be an absolute path")
    observations = probe_output.splitlines()
    if len(observations) != len(FIXED_EXECUTABLE_PATHS):
        raise ValueError("fixed executable probe returned an incomplete observation set")
    for observation, (name, path) in zip(observations, FIXED_EXECUTABLE_PATHS, strict=True):
        allowed = (f"{name}={path}", f"{name}=not found at {path}")
        if observation not in allowed:
            raise ValueError(f"fixed executable probe returned an invalid {name} observation")
    return (
        f"Working directory: {working_directory}. Fixed-path executable probe: "
        f"{', '.join(observations)}. A not-found result covers only the listed "
        "standard location; project-local tools may still exist."
    )


def build_program(
    instruction: str,
    model_name: str,
    credential_path: str,
    working_directory: str,
    *,
    model_calls: int,
    input_tokens: int | None,
    output_tokens: int | None,
    seconds: int,
    reasoning_effort: str,
    service_tier: str = "default",
    environment_facts: str | None = None,
    completion_checker: str | None = None,
    diagnosis_model_name: str | None = None,
    diagnosis_reasoning_effort: str = "high",
    diagnosis_model_calls: int = 20,
    unresolved_diagnosis_reasoning_effort: str | None = None,
    unresolved_diagnosis_model_calls: int = 20,
    escalation_reasoning_effort: str | None = None,
    escalation_model_calls: int = 0,
    separate_audit_and_repair: bool = False,
) -> dict[str, Any]:
    """Build the recorded Foe program used for one Terminal-Bench trial."""
    if "/" not in model_name:
        raise ValueError("model must have the form provider/model")
    provider, model = model_name.split("/", 1)
    if not provider or not model:
        raise ValueError("model must have the form provider/model")
    limits = {
        "model_calls": model_calls,
        "seconds": seconds,
        "loop_threshold": EVALUATION_LOOP_THRESHOLD,
    }
    optional_limits = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if any(not isinstance(value, int) or value <= 0 for value in limits.values()):
        raise ValueError("model call and time allowances must be positive integers")
    if any(
        value is not None and (not isinstance(value, int) or value <= 0)
        for value in optional_limits.values()
    ):
        raise ValueError("token allowances must be positive integers when present")
    if not working_directory.startswith("/"):
        raise ValueError("working directory must be an absolute path")
    if completion_checker is not None and not completion_checker.startswith("/"):
        raise ValueError("completion checker must be an absolute path")
    environment_facts = environment_facts or (
        f"Working directory: {working_directory}. Fixed-path executable availability "
        "was not observed."
    )
    limits.update({key: value for key, value in optional_limits.items() if value is not None})
    coding_tools = ["read", "grep", "edit", "bash"]
    check_tool_defs: dict[str, Any] = {}
    typed_completion: dict[str, Any] = {"returns": COMPLETION_SCHEMA}
    completion_contract = typed_completion
    if completion_checker is not None:
        coding_tools.append("check")
        check_tool_defs["check"] = {
            "exec": completion_checker,
            "description": (
                "Runs the task's read-only completion checker. An empty standard output "
                "means the public completion conditions passed. Each output line is a "
                "finding to repair."
            ),
            "timeout_seconds": 300,
        }
        completion_contract = {
            **typed_completion,
            "verify": "check",
            "retries": COMPLETION_CHECK_RETRIES,
        }
    program = {
        "version": 3,
        "name": "terminal-bench-coding",
        "instructions": {"environment": environment_facts, "role": CODING_INSTRUCTION},
        "tools": coding_tools,
        "tool_defs": check_tool_defs,
        "grants": {"read": [working_directory, "/"], "write": ["/"]},
        "budget": limits,
        "model": {
            "provider": provider,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "service_tier": service_tier,
            "token_file": credential_path,
        },
        "sandbox": {"mode": "off"},
        "task": instruction,
    }
    if completion_checker is not None:
        program["done_when"] = completion_contract
    if (
        diagnosis_model_name is None
        and unresolved_diagnosis_reasoning_effort is None
        and escalation_reasoning_effort is None
        and not separate_audit_and_repair
    ):
        return program
    diagnosis_provider = diagnosis_model = None
    if diagnosis_model_name is not None:
        if "/" not in diagnosis_model_name:
            raise ValueError("diagnosis model must have the form provider/model")
        diagnosis_provider, diagnosis_model = diagnosis_model_name.split("/", 1)
        if not diagnosis_provider or not diagnosis_model:
            raise ValueError("diagnosis model must have the form provider/model")
        if diagnosis_model_calls < MIN_AUXILIARY_MODEL_CALLS:
            raise ValueError(
                f"diagnosis model calls must be at least {MIN_AUXILIARY_MODEL_CALLS}"
            )
    if unresolved_diagnosis_reasoning_effort is not None:
        if diagnosis_model_name is None:
            raise ValueError("unresolved diagnosis requires a diagnosis model")
        if unresolved_diagnosis_model_calls < MIN_AUXILIARY_MODEL_CALLS:
            raise ValueError(
                "unresolved diagnosis model calls must be at least "
                f"{MIN_AUXILIARY_MODEL_CALLS}"
            )
        if escalation_reasoning_effort is not None:
            raise ValueError(
                "unresolved diagnosis and post-implementation escalation cannot be combined"
            )
    if escalation_reasoning_effort is None and escalation_model_calls != 0:
        raise ValueError("escalation model calls require an escalation reasoning effort")
    if separate_audit_and_repair and escalation_reasoning_effort is None:
        raise ValueError("separate audit and repair requires an escalation reasoning effort")
    if (
        escalation_reasoning_effort is not None
        and escalation_model_calls < MIN_AUXILIARY_MODEL_CALLS
    ):
        raise ValueError(
            f"escalation model calls must be at least {MIN_AUXILIARY_MODEL_CALLS}"
        )
    diagnosis_calls = diagnosis_model_calls if diagnosis_model_name is not None else 0
    diagnosis_seconds = seconds if diagnosis_model_name is not None else 0
    unresolved_diagnosis_calls = (
        unresolved_diagnosis_model_calls
        if unresolved_diagnosis_reasoning_effort is not None
        else 0
    )
    unresolved_diagnosis_seconds = seconds if unresolved_diagnosis_reasoning_effort is not None else 0
    escalation_seconds = seconds if escalation_reasoning_effort is not None else 0
    escalation_stages = (
        2 if separate_audit_and_repair else int(escalation_reasoning_effort is not None)
    )
    implementation_seconds = seconds
    implementation_calls = model_calls
    shared_grants = {"read": [working_directory, "/"], "write": ["/"]}
    diagnosis_schema = {
        "type": "object",
        "properties": {
            "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "implementation_steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "verification_steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["facts", "implementation_steps", "verification_steps"],
        "additionalProperties": False,
    }
    program["budget"].update(
        {
            "model_calls": (
                model_calls
                + diagnosis_calls
                + unresolved_diagnosis_calls
                + escalation_model_calls * escalation_stages
            ),
            "seconds": (
                seconds
                + diagnosis_seconds
                + unresolved_diagnosis_seconds
                + escalation_seconds * escalation_stages
            ),
            "max_episodes": 2
            + int(diagnosis_model_name is not None)
            + int(unresolved_diagnosis_reasoning_effort is not None)
            + escalation_stages,
            "max_concurrent": 1,
        }
    )
    if completion_checker is not None and separate_audit_and_repair:
        program["budget"]["max_episodes"] = 1 + 3 * (
            SEPARATE_ASSESSMENT_CORRECTIONS + 1
        )
    implementation_role = (
        "Implement the task using the typed diagnosis as advice. Confirm its claims against "
        "the repository. Make the requested change, run the strongest available verification "
        "after the final change, and leave files and services in the state the task requires. "
        "For a program interface, exercise at least two materially different behavioral inputs, "
        "including one that stresses parsing, length, or state."
        if diagnosis_model_name is not None
        else "Implement the task. Inspect the current workspace, make the requested change, run "
        "the strongest available verification after the final change, and leave files and "
        "services in the state the task requires. For a program interface, exercise at least two "
        "materially different behavioral inputs, including one that stresses parsing, length, "
        "or state."
    )
    implementation_role += (
        " In the completion value, report changed artifacts, commands and observed results, "
        "and unresolved risks for an independent audit."
    )
    def implementation_node(name: str, follows: list[str], terminal: bool) -> dict[str, Any]:
        return {
            "model": {
                "name": name,
                "instructions": {"environment": environment_facts, "role": implementation_role},
                "tools": coding_tools,
                "tool_defs": check_tool_defs,
                "grants": shared_grants,
                "budget": {
                    "model_calls": implementation_calls,
                    "seconds": implementation_seconds,
                    "loop_threshold": EVALUATION_LOOP_THRESHOLD,
                },
                "done_when": (
                    typed_completion
                    if completion_checker is not None
                    and escalation_reasoning_effort is not None
                    else completion_contract
                ),
                "model": {
                    "provider": provider,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "service_tier": service_tier,
                    "token_file": credential_path,
                },
            },
            "follows": follows,
            "terminal": terminal,
        }

    program["workflow"] = {
        "nodes": {
            "implement-task": implementation_node(
                (
                    "implement-diagnosed-task"
                    if diagnosis_model_name is not None
                    else "implement-task"
                ),
                ["task"] + (["diagnose-task"] if diagnosis_model_name is not None else []),
                escalation_reasoning_effort is None,
            ),
        },
        "recovery": {"enabled": False},
    }
    if unresolved_diagnosis_reasoning_effort is not None:
        del program["workflow"]["nodes"]["implement-task"]
        program["workflow"]["nodes"].update(
            {
                "implement-resolved-task": implementation_node(
                    "implement-resolved-task",
                    ["task", "diagnose-task"],
                    True,
                ),
                "implement-after-unresolved-diagnosis": implementation_node(
                    "implement-after-unresolved-diagnosis",
                    ["task", "diagnose-unresolved-task"],
                    True,
                ),
            }
        )
    if diagnosis_model_name is not None:
        program["workflow"]["nodes"]["diagnose-task"] = {
            "model": {
                "name": "diagnose-coding-task",
                "instructions": {
                    "environment": environment_facts,
                    "role": (
                        "Analyze the task and repository without implementing the task. "
                        "Use read, grep, and bash for focused static and runtime evidence. "
                        "Report observed constraints, evidence, and uncertainty together as facts. "
                        "Give implementation steps and verification steps. "
                        "Use four model requests as a planning target. Return earlier when the "
                        "branch decision and useful next steps are supported. Continue past the "
                        "target only when a named implementation-critical fact prevents choosing "
                        "a branch. "
                        "The model-call allowance is a loop backstop rather than an inspection "
                        "target. Keep the return concise."
                    )
                },
                "tools": ["read", "grep", "bash"],
                "grants": {"read": [working_directory, "/"]},
                "budget": {
                    "model_calls": diagnosis_model_calls,
                    "seconds": diagnosis_seconds,
                    "loop_threshold": EVALUATION_LOOP_THRESHOLD,
                },
                "done_when": {"returns": diagnosis_schema},
                "model": {
                    "provider": diagnosis_provider,
                    "model": diagnosis_model,
                    "reasoning_effort": diagnosis_reasoning_effort,
                    "service_tier": service_tier,
                    "token_file": credential_path,
                },
            },
            "follows": ["task"],
        }
        if unresolved_diagnosis_reasoning_effort is not None:
            program["workflow"]["nodes"]["diagnose-task"]["branches"] = {
                "implement": ["implement-resolved-task"],
                "investigate-unresolved-facts": ["diagnose-unresolved-task"],
            }
            program["workflow"]["nodes"]["diagnose-task"]["model"]["instructions"]["role"] += (
                " Choose branch `implement` only when every fact required to implement and "
                "verify the task is resolved by evidence. Choose branch "
                "`investigate-unresolved-facts` when any implementation-critical fact remains "
                "uncertain."
            )
            program["workflow"]["nodes"]["diagnose-unresolved-task"] = {
                "model": {
                    "name": "diagnose-unresolved-task",
                    "instructions": {
                        "environment": environment_facts,
                        "role": (
                            "Resolve the implementation-critical uncertainty in the earlier "
                            "diagnosis. Analyze the task and repository without implementing the "
                            "task. Use read, grep, and bash for focused static and runtime evidence. "
                            "Return a consolidated set of facts, implementation steps, and "
                            "verification steps for a fresh coding episode. State remaining "
                            "uncertainty explicitly. Use six model requests as a planning target. "
                            "Return earlier when the facts needed to begin a viable implementation "
                            "are supported. Continue past the target only while a named unresolved "
                            "fact prevents implementation. The coding episode owns end-to-end "
                            "validation and repair, so avoid exhaustive validation and avoid "
                            "building the task solution here. The larger model-call allowance is a "
                            "loop backstop."
                        )
                    },
                    "tools": ["read", "grep", "bash"],
                    "grants": {"read": [working_directory, "/"]},
                    "budget": {
                        "model_calls": unresolved_diagnosis_model_calls,
                        "seconds": unresolved_diagnosis_seconds,
                        "loop_threshold": EVALUATION_LOOP_THRESHOLD,
                    },
                    "done_when": {"returns": diagnosis_schema},
                    "model": {
                        "provider": provider,
                        "model": model,
                        "reasoning_effort": unresolved_diagnosis_reasoning_effort,
                        "service_tier": service_tier,
                        "token_file": credential_path,
                    },
                },
                "follows": ["task", "diagnose-task"],
            }
    if escalation_reasoning_effort is not None and not separate_audit_and_repair:
        program["workflow"]["nodes"]["audit-and-repair-task"] = {
            "model": {
                "name": "audit-and-repair-task",
                "instructions": {
                    "environment": environment_facts,
                    "role": (
                        "Independently determine whether the shared workspace satisfies the original task. "
                        "Treat the implementation episode's completion claim as unverified. Inspect the "
                        "artifacts and run checks that distinguish plausible incorrect implementations. "
                        "For a program interface, test at least two materially different valid inputs. "
                        "Generate a second valid fixture when the workspace supplies only one. "
                        "Repair every defect you find. After the final edit, run the strongest available "
                        "task-relevant checks. Complete with the workspace in the state the task requires. "
                        "Report every path changed by either episode, including valid implementation changes "
                        "that required no audit edit."
                    )
                },
                "tools": coding_tools,
                "tool_defs": check_tool_defs,
                "grants": shared_grants,
                "budget": {
                    "model_calls": escalation_model_calls,
                    "seconds": escalation_seconds,
                    "loop_threshold": EVALUATION_LOOP_THRESHOLD,
                },
                "done_when": completion_contract,
                "model": {
                    "provider": provider,
                    "model": model,
                    "reasoning_effort": escalation_reasoning_effort,
                    "service_tier": service_tier,
                    "token_file": credential_path,
                },
            },
            "follows": ["task", "implement-task"],
            "terminal": True,
        }
    if escalation_reasoning_effort is not None and separate_audit_and_repair:
        repair_completion_schema = {
            **COMPLETION_SCHEMA,
            "properties": {
                **COMPLETION_SCHEMA["properties"],
                "unresolved_risks": {
                    **COMPLETION_SCHEMA["properties"]["unresolved_risks"],
                    "maxItems": 0,
                },
            },
        }
        repair_completion_contract: dict[str, Any] = {
            "returns": repair_completion_schema,
        }
        assessment_schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
                "findings": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 1000},
                    "maxItems": 32,
                },
                "validation": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 1000},
                    "minItems": 1,
                    "maxItems": 32,
                },
                "unresolved_risks": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 1000},
                    "maxItems": 16,
                },
            },
            "required": ["summary", "findings", "validation", "unresolved_risks"],
            "additionalProperties": False,
        }
        assessment_tools = ["read", "grep", "bash"]
        if completion_checker is not None:
            assessment_tools.append("check")
            # Either branch can complete the workflow with a different typed
            # value. The workspace verifier governs both values at the root.
            # Each finding deterministically re-fires the nearest model node.
            # An accepted assessment is reconsidered with the finding, while
            # a failed repaired value returns directly to the repair node.
            program["done_when"] = {
                "verify": "check",
                "retries": SEPARATE_ASSESSMENT_CORRECTIONS,
            }
        program["workflow"]["nodes"].update(
            {
                "assess-task": {
                    "model": {
                        "name": "assess-task",
                        "instructions": {
                            "environment": environment_facts,
                            "role": (
                                "Independently assess whether the shared workspace satisfies every "
                                "requirement in the original task. Treat the implementation episode's "
                                "completion claim as unverified. Inspect artifacts and run checks, but "
                                "do not change the workspace. Distinguish the exact contract from a "
                                "nearby interpretation. Evaluate the supplied artifacts and inputs "
                                "under the task's stated interface. Do not require compatibility with "
                                "absent formats or input variants unless the task requires them. "
                                "Prioritize checks of the required observable behavior. Preserve "
                                "baseline identities, allowed "
                                "transformation sets, and stated structural constraints. For a program "
                                "interface, test at least two materially different valid inputs and "
                                "generate a second fixture when the workspace supplies only one. Choose "
                                "`accept` only when current observations support every requirement and "
                                "there is no unresolved risk. Otherwise choose `repair` and return "
                                "precise findings that a fresh coding episode can reproduce."
                            ),
                        },
                        "tools": assessment_tools,
                        "tool_defs": check_tool_defs,
                        "grants": {"read": [working_directory, "/"]},
                        "budget": {
                            "model_calls": escalation_model_calls,
                            "seconds": escalation_seconds,
                            "loop_threshold": EVALUATION_LOOP_THRESHOLD,
                        },
                        "done_when": {"returns": assessment_schema},
                        "model": {
                            "provider": provider,
                            "model": model,
                            "reasoning_effort": escalation_reasoning_effort,
                            "service_tier": service_tier,
                            "token_file": credential_path,
                        },
                    },
                    "follows": ["task", "implement-task"],
                    "branches": {"accept": [], "repair": ["repair-task"]},
                },
                "repair-task": {
                    "model": {
                        "name": "repair-task",
                        "instructions": {
                            "environment": environment_facts,
                            "role": (
                                "Repair the independent assessment's findings in the shared "
                                "workspace. Treat every finding and unresolved risk as an obligation. "
                                "Reproduce each one before changing an artifact, then resolve it or "
                                "show with evidence that it cannot affect an original task requirement. "
                                "Prioritize the task's required observable behavior over compatibility "
                                "with absent inputs. Make "
                                "the smallest change that satisfies the original task while preserving "
                                "unrelated artifacts, baseline identities, allowed transformations, and "
                                "structural constraints. After the final change, run the strongest "
                                "available task-relevant checks. Complete only after no task-critical "
                                "risk remains. Return an empty `unresolved_risks` array with every "
                                "changed path and observed validation result."
                            ),
                        },
                        "tools": coding_tools,
                        "tool_defs": check_tool_defs,
                        "grants": shared_grants,
                        "budget": {
                            "model_calls": escalation_model_calls,
                            "seconds": escalation_seconds,
                            "loop_threshold": EVALUATION_LOOP_THRESHOLD,
                        },
                        "done_when": repair_completion_contract,
                        "model": {
                            "provider": provider,
                            "model": model,
                            "reasoning_effort": escalation_reasoning_effort,
                            "service_tier": service_tier,
                            "token_file": credential_path,
                        },
                    },
                    "follows": ["task", "implement-task", "assess-task"],
                    "terminal": True,
                },
            }
        )
        if completion_checker is not None:
            for node in ("implement-task", "assess-task", "repair-task"):
                program["workflow"]["nodes"][node]["max_fires"] = (
                    SEPARATE_ASSESSMENT_CORRECTIONS + 1
                )
    return program


def estimate_usage_cost(
    usages: list[dict[str, int]],
    *,
    input_per_million: float,
    cached_input_per_million: float,
    output_per_million: float,
    long_context_threshold: int,
    long_context_input_multiplier: float,
    long_context_output_multiplier: float,
) -> float:
    """Estimate route cost request by request from provider-reported usage."""
    total = 0.0
    for usage in usages:
        input_tokens = usage["input"]
        cached_tokens = max(0, min(usage["cache_read"], input_tokens))
        uncached_tokens = input_tokens - cached_tokens
        long_request = input_tokens > long_context_threshold
        input_multiplier = long_context_input_multiplier if long_request else 1.0
        output_multiplier = long_context_output_multiplier if long_request else 1.0
        total += input_multiplier * (
            uncached_tokens * input_per_million
            + cached_tokens * cached_input_per_million
        ) / 1_000_000
        total += (
            output_multiplier * usage["output"] * output_per_million / 1_000_000
        )
    return total


def credential_values(path: Path) -> frozenset[str]:
    """Return secret token values for exact-match exposure detection."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"credential file must contain a JSON object: {path}")
    return frozenset(
        item
        for key in ("access", "refresh", "api_key")
        if isinstance((item := value.get(key)), str) and item
    )


def retained_artifacts_contain_credential(
    log_dir: Path,
    values: frozenset[str],
) -> bool:
    """Detect an exact credential value in any retained regular file."""
    encoded = tuple(value.encode("utf-8") for value in values)
    if not encoded:
        return False
    overlap = max(map(len, encoded)) - 1
    for path in log_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        tail = b""
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                observed = tail + chunk
                if any(value in observed for value in encoded):
                    return True
                tail = observed[-overlap:] if overlap else b""
    return False


def read_episode_summary(
    log_dir: Path,
    pricing: dict[str, float | int] | dict[str, dict[str, float | int]] | None = None,
) -> dict[str, Any]:
    """Measure usage and read the root outcome from a retained episode tree."""
    root_path = log_dir / "episode.jsonl"
    if not root_path.is_file():
        raise FileNotFoundError(f"Foe episode log does not exist: {root_path}")
    paths = sorted(log_dir.rglob("episode.jsonl"))
    calls = 0
    tool_calls = 0
    messages: list[tuple[str | None, dict[str, Any]]] = []
    outcome: dict[str, Any] | None = None
    for path in paths:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        starts = [event for event in events if event.get("type") == "episode/start"]
        route = None
        if starts:
            start_data = starts[0].get("data") if isinstance(starts[0].get("data"), dict) else {}
            program = start_data.get("program") if isinstance(start_data.get("program"), dict) else {}
            model = program.get("model") if isinstance(program.get("model"), dict) else {}
            if isinstance(model.get("provider"), str) and isinstance(model.get("model"), str):
                route = f"{model['provider']}/{model['model']}"
        for event in events:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            event_type = event.get("type")
            if event_type == "model/request":
                calls += 1
            elif event_type == "tool/result":
                tool_calls += 1
            elif event_type == "assistant/message":
                messages.append((route, data))
            elif path == root_path and event_type == "episode/end":
                value = data.get("outcome")
                if isinstance(value, dict):
                    outcome = value

    totals = {"input": 0, "output": 0, "cache_read": 0}
    usages: list[dict[str, int]] = []
    measured = 0
    accounted_calls = 0
    priced_usages: list[tuple[str | None, dict[str, int]]] = []
    for route, message in messages:
        item = message.get("usage")
        if not isinstance(item, dict) or not all(isinstance(item.get(key), int) for key in totals):
            continue
        measured += 1
        if not message.get("interrupted"):
            accounted_calls += 1
        usage = {key: item[key] for key in totals}
        usages.append(usage)
        priced_usages.append((route, usage))
        for key in totals:
            totals[key] += item[key]
    complete = bool(messages) and measured == len(messages) and accounted_calls == calls
    estimated_cost = None
    if complete and pricing is not None:
        if "input_per_million" in pricing:
            estimated_cost = estimate_usage_cost(usages, **pricing)
        elif all(route in pricing for route, _ in priced_usages):
            estimated_cost = sum(
                estimate_usage_cost([usage], **pricing[route])
                for route, usage in priced_usages
                if route is not None
            )
    return {
        "model_calls": calls,
        "tool_calls": tool_calls,
        "model_responses": len(messages),
        "responses_with_usage": measured,
        "unreported_model_calls": max(0, calls - accounted_calls),
        "usage_reported": complete,
        "input_tokens": totals["input"] if complete else None,
        "output_tokens": totals["output"] if complete else None,
        "cache_read_tokens": totals["cache_read"] if complete else None,
        "estimated_cost_usd": estimated_cost,
        "outcome": outcome,
    }


def replace_credential_state(downloaded: Path, state: Path) -> None:
    """Validate and atomically install a refreshed private credential copy."""
    value = json.loads(downloaded.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise ValueError("refreshed credential must be a non-empty JSON object")
    os.chmod(downloaded, 0o600)
    os.replace(downloaded, state)
