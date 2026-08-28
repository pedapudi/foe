#!/usr/bin/python3

import json
import signal
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import run as terminal_bench_run
from run import (
    AUTHORIZED_BENCHMARK_CONTEXT,
    BUILTIN_WORKFLOW_MODEL_CALLS,
    CampaignCancellation,
    HostResources,
    access_only_lease_requirement_ms,
    built_in_program_failures,
    campaign_execution_complete,
    campaign_signal_handlers,
    credential_supports_parallel_tasks,
    execution_groups,
    harbor_command,
    issue_access_only_lease,
    lock_credential_state,
    model_stage_count,
    parallel_host_admission,
    prepare_campaign_credential,
    read_cases,
    read_job_integrity,
    read_job_result,
    run_commands,
    run_host_admission,
    source_tree,
    write_json_atomic,
    write_provider_free_credential,
)


def retain_empty_verifier_report(trial: Path) -> None:
    verifier = trial / "verifier"
    verifier.mkdir()
    (verifier / "ctrf.json").write_text(
        json.dumps(
            {
                "results": {
                    "summary": {
                        "tests": 0,
                        "passed": 0,
                        "failed": 0,
                        "skipped": 0,
                    },
                    "tests": [],
                }
            }
        ),
        encoding="utf-8",
    )


class CasesTest(unittest.TestCase):
    def test_installation_credentials_do_not_open_campaign_oauth_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            login = root / "missing-login.json"
            state = root / "credential-state" / "openai-codex.json"
            lock = prepare_campaign_credential(
                login,
                state,
                install_only=True,
            )
            self.assertIsNone(lock)
            self.assertFalse(state.parent.exists())
            first = root / "worker-one.json"
            second = root / "worker-two.json"
            write_provider_free_credential(first)
            write_provider_free_credential(second)
            self.assertEqual(first.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(first.stat().st_mode & 0o777, 0o400)
            self.assertNotEqual(first, second)

    def test_built_in_program_integrity_requires_terminal_audit_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "trial"
            episode = trial / "agent" / "foe-episode"
            episode.mkdir(parents=True)
            retain_empty_verifier_report(trial)
            result = trial / "result.json"
            program = {
                "name": "coding",
                "budget": {"model_calls": 160},
                "sandbox": {"mode": "off"},
                "model": {
                    "provider": "openai-codex",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "low",
                    "service_tier": "priority",
                },
                "workflow": {
                    "nodes": {
                        "implement-task": {
                            "follows": ["task"],
                            "terminal": False,
                            "model": {
                                "name": "implement-task",
                                "budget": {"model_calls": 60},
                                "done_when": {"returns": {}},
                            },
                        },
                        "audit-and-repair-task": {
                            "follows": ["task", "implement-task"],
                            "terminal": True,
                            "model": {
                                "name": "audit-and-repair-task",
                                "budget": {"model_calls": 100},
                                "model": {
                                    "provider": "openai-codex",
                                    "model": "gpt-5.6-sol",
                                    "reasoning_effort": "high",
                                    "service_tier": "priority",
                                },
                                "done_when": {"returns": {}, "verify": "check"},
                            },
                        },
                    }
                },
            }
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": program},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                built_in_program_failures(
                    result,
                    completion_checker=True,
                    service_tier="priority",
                ),
                [],
            )
            candidate = json.loads(json.dumps(program))
            candidate["budget"]["model_calls"] = 126
            candidate_audit = candidate["workflow"]["nodes"][
                "audit-and-repair-task"
            ]
            candidate_audit["terminal"] = False
            del candidate_audit["model"]["done_when"]["verify"]
            candidate["workflow"]["nodes"]["falsify-completion"] = {
                "follows": ["task", "audit-and-repair-task"],
                "terminal": True,
                "model": {
                    "name": "falsify-completion",
                    "budget": {"model_calls": 6},
                    "model": {
                        "provider": "openai-codex",
                        "model": "gpt-5.6-sol",
                        "reasoning_effort": "high",
                        "service_tier": "priority",
                    },
                    "done_when": {"returns": {}, "verify": "check"},
                },
            }
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": candidate},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                built_in_program_failures(
                    result,
                    completion_checker=True,
                    service_tier="priority",
                    source_candidate=True,
                ),
                [],
            )
            nested_candidate = json.loads(json.dumps(candidate))
            gate = nested_candidate["workflow"]["nodes"].pop(
                "falsify-completion"
            )
            gate["follows"] = ["task"]
            nested_candidate["workflow"]["nodes"]["completion-workflow"] = {
                "follows": ["task", "audit-and-repair-task"],
                "terminal": True,
                "workflow": {"nodes": {"falsify-completion": gate}},
            }
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": nested_candidate},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                built_in_program_failures(
                    result,
                    completion_checker=True,
                    service_tier="priority",
                    source_candidate=True,
                ),
                [],
            )
            nested_candidate["workflow"]["nodes"]["completion-workflow"][
                "workflow"
            ]["nodes"]["falsify-completion"]["model"][
                "model"
            ]["service_tier"] = "default"
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": nested_candidate},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            candidate_failures = built_in_program_failures(
                result,
                completion_checker=True,
                service_tier="priority",
                source_candidate=True,
            )
            self.assertTrue(
                any("recorded disallowed profile" in item for item in candidate_failures)
            )
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": program},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            program["workflow"]["nodes"]["audit-and-repair-task"]["model"][
                "model"
            ]["reasoning_effort"] = "xhigh"
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": program},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result.write_text(
                json.dumps(
                    {
                        "trial_name": "trial",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 1.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {"kind": "completed", "value": "done"},
                                "foe_trace_conformant": True,
                                "foe_usage_reported": True,
                                "foe_built_in_workflow": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(
                Path(directory),
                built_in_workflow=True,
                completion_checker=True,
                service_tier="priority",
            )
            self.assertTrue(integrity["configuration_claim_valid"])
            self.assertEqual(integrity["built_in_audit_reasoning_effort"], "xhigh")
            program["workflow"]["nodes"]["audit-and-repair-task"]["model"][
                "model"
            ]["reasoning_effort"] = "high"
            program["workflow"]["nodes"]["implement-task"]["model"]["done_when"][
                "verify"
            ] = "check"
            (episode / "episode.jsonl").write_text(
                json.dumps(
                    {
                        "type": "episode/start",
                        "data": {"program": program},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertIn(
                "built-in profile implementation.verify: expected None, recorded 'check'",
                built_in_program_failures(
                    result,
                    completion_checker=True,
                    service_tier="priority",
                ),
            )
            result.write_text(
                json.dumps(
                    {
                        "trial_name": "trial",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 1.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {"kind": "completed", "value": "done"},
                                "foe_trace_conformant": True,
                                "foe_usage_reported": True,
                                "foe_built_in_workflow": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(
                Path(directory),
                built_in_workflow=True,
                completion_checker=True,
                service_tier="priority",
            )
            self.assertFalse(integrity["configuration_claim_valid"])
            record = {
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_total_trials": 1,
                **integrity,
            }
            self.assertFalse(campaign_execution_complete([record], 1))

    def test_cases_pin_revision_and_separate_task_sets(self):
        dataset, groups, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        self.assertEqual(dataset, "terminal-bench/terminal-bench-2-1@6")
        protected = (
            "development",
            "capability_search",
            "confirmation",
            "calibration",
            "calibration_holdout",
            "provider_policy_incompatible",
        )
        for index, left in enumerate(protected):
            for right in protected[index + 1 :]:
                self.assertFalse(set(groups[left]) & set(groups[right]))
        self.assertEqual(len(groups["development"]), 12)
        self.assertEqual(len(groups["capability_search"]), 5)
        self.assertEqual(len(groups["confirmation"]), 8)
        self.assertEqual(len(groups["calibration"]), 20)
        self.assertEqual(len(groups["calibration_holdout"]), 8)
        self.assertEqual(
            groups["self_improvement_evidence"],
            groups["development"]
            + groups["capability_search"]
            + groups["confirmation"],
        )
        held_back = set(groups["calibration"]) | set(groups["calibration_holdout"])
        self.assertFalse(set(groups["self_improvement_evidence"]) & held_back)
        self.assertEqual(groups["provider_policy_incompatible"], ("vulnerable-secret",))
        self.assertEqual(groups["smoke"], ("fix-git",))
        self.assertGreater(
            tasks["fix-git"].expected_input_tokens,
            tasks["fix-git"].expected_output_tokens,
        )
        self.assertGreaterEqual(min(task.model_calls for task in tasks.values()), 60)
        self.assertGreaterEqual(min(task.seconds for task in tasks.values()), 1800)
        self.assertEqual(tasks["gpt2-codegolf"].harbor_agent_seconds, 900)
        self.assertEqual(tasks["fix-git"].memory_mb, 2048)
        self.assertEqual(tasks["compile-compcert"].cpus, 2)
        self.assertEqual(tasks["gpt2-codegolf"].memory_mb, 8192)
        self.assertEqual(tasks["mcmc-sampling-stan"].cpus, 4)
        self.assertEqual(pricing["openai-codex/gpt-5.6-sol"].output_per_million, 20.0)

    def test_case_resources_require_positive_integer_values(self):
        source = Path(__file__).with_name("cases.json")
        value = json.loads(source.read_text(encoding="utf-8"))
        value["tasks"]["fix-git"]["memory_mb"] = True
        with tempfile.TemporaryDirectory() as directory:
            cases = Path(directory) / "cases.json"
            cases.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fix-git resources"):
                read_cases(cases)

    def test_harbor_command_runs_one_task_at_a_time(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = harbor_command(
                harbor=Path("/tools/harbor"),
                dataset="terminal-bench/terminal-bench-2-1@6",
                task=tasks["fix-git"],
                attempts=2,
                jobs_dir=root / "jobs",
                agent_module=root / "foe_agent.py",
                trace_evaluator=root / "trace_quality.py",
                foe=root / "foe",
                credential_state=root / "private.json",
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="low",
                diagnosis_model=None,
                diagnosis_reasoning_effort="high",
                diagnosis_model_calls=6,
                diagnosis_pricing=None,
                unresolved_diagnosis_reasoning_effort=None,
                unresolved_diagnosis_model_calls=6,
                escalation_reasoning_effort=None,
                escalation_model_calls=0,
                runtime_digest="abc123",
                pricing=pricing["openai-codex/gpt-5.6-sol"],
                install_only=True,
            )
        self.assertEqual(command[command.index("--n-concurrent") + 1], "1")
        self.assertEqual(command[command.index("--n-attempts") + 1], "2")
        self.assertAlmostEqual(
            float(command[command.index("--agent-timeout-multiplier") + 1]),
            (tasks["fix-git"].seconds + 300) / tasks["fix-git"].harbor_agent_seconds,
        )
        self.assertIn("terminal-bench/terminal-bench-2-1@6", command)
        self.assertIn("terminal-bench/fix-git", command)
        self.assertIn("foe_agent:FoeAgent", command)
        self.assertIn(f"trace_evaluator={root / 'trace_quality.py'}", command)
        self.assertIn(f"PYTHONPATH={root}", command)
        self.assertNotIn("input_tokens=120000", command)
        self.assertNotIn("output_tokens=20000", command)
        self.assertIn("input_per_million=4.0", command)
        self.assertIn("service_tier=default", command)
        self.assertIn("credential_mode=mutable", command)
        self.assertIn("--install-only", command)

    def test_harbor_commands_isolate_job_names_and_access_only_credentials(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        shared = {
            "harbor": Path("/tools/harbor"),
            "dataset": "terminal-bench/terminal-bench-2-1@6",
            "attempts": 1,
            "jobs_dir": Path("/tmp/jobs"),
            "agent_module": Path("/tmp/foe_agent.py"),
            "trace_evaluator": Path("/tmp/score-trace"),
            "foe": Path("/tmp/foe"),
            "credential_mode": "access_only",
            "model": "openai-codex/gpt-5.6-sol",
            "reasoning_effort": "low",
            "diagnosis_model": None,
            "diagnosis_reasoning_effort": "high",
            "diagnosis_model_calls": 6,
            "diagnosis_pricing": None,
            "unresolved_diagnosis_reasoning_effort": None,
            "unresolved_diagnosis_model_calls": 6,
            "escalation_reasoning_effort": None,
            "escalation_model_calls": 0,
            "runtime_digest": "abc123",
            "pricing": pricing["openai-codex/gpt-5.6-sol"],
        }
        commands = [
            harbor_command(
                **shared,
                task=tasks[name],
                credential_state=Path(f"/tmp/{name}.json"),
            )
            for name in ("fix-git", "cancel-async-tasks")
        ]
        self.assertEqual(
            [command[command.index("--job-name") + 1] for command in commands],
            ["fix-git", "cancel-async-tasks"],
        )
        self.assertIn("credential_file=/tmp/fix-git.json", commands[0])
        self.assertIn("credential_file=/tmp/cancel-async-tasks.json", commands[1])
        self.assertTrue(all("credential_mode=access_only" in command for command in commands))

    def test_harbor_command_runs_the_built_in_two_episode_workflow(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            completion_checker=Path("/tmp/completion-check"),
            built_in_workflow=True,
        )
        self.assertIn("built_in_workflow=true", command)
        self.assertIn(f"model_calls={BUILTIN_WORKFLOW_MODEL_CALLS}", command)
        self.assertIn("completion_checker=/tmp/completion-check", command)
        self.assertAlmostEqual(
            float(command[command.index("--agent-timeout-multiplier") + 1]),
            (tasks["fix-git"].seconds * 2 + 300)
            / tasks["fix-git"].harbor_agent_seconds,
        )
        self.assertEqual(model_stage_count(None, None, None, True), 2)

    def test_harbor_command_runs_the_closed_book_built_in_workflow(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            built_in_workflow=True,
        )
        self.assertIn("built_in_workflow=true", command)
        self.assertFalse(
            any(value.startswith("completion_checker=") for value in command)
        )

    def test_harbor_command_can_append_recorded_benchmark_authorization(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["model-extraction-relu-logits"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            built_in_workflow=True,
            authorized_benchmark_context=True,
        )
        index = command.index("--extra-instruction")
        self.assertEqual(command[index + 1], AUTHORIZED_BENCHMARK_CONTEXT)

    def test_harbor_command_rejects_external_stages_for_the_built_in_workflow(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        with self.assertRaisesRegex(ValueError, "owns its implementation and audit"):
            harbor_command(
                harbor=Path("/tools/harbor"),
                dataset="terminal-bench/terminal-bench-2-1@6",
                task=tasks["fix-git"],
                attempts=1,
                jobs_dir=Path("/tmp/jobs"),
                agent_module=Path("/tmp/foe_agent.py"),
                trace_evaluator=Path("/tmp/score-trace"),
                foe=Path("/tmp/foe"),
                credential_state=Path("/tmp/private.json"),
                model="openai-codex/gpt-5.6-sol",
                reasoning_effort="low",
                diagnosis_model=None,
                diagnosis_reasoning_effort="high",
                diagnosis_model_calls=6,
                diagnosis_pricing=None,
                unresolved_diagnosis_reasoning_effort=None,
                unresolved_diagnosis_model_calls=6,
                escalation_reasoning_effort="xhigh",
                escalation_model_calls=60,
                runtime_digest="abc123",
                pricing=pricing["openai-codex/gpt-5.6-sol"],
                built_in_workflow=True,
            )

    def test_harbor_command_rejects_a_different_built_in_model(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        with self.assertRaisesRegex(ValueError, "requires model openai-codex/gpt-5.6-sol"):
            harbor_command(
                harbor=Path("/tools/harbor"),
                dataset="terminal-bench/terminal-bench-2-1@6",
                task=tasks["fix-git"],
                attempts=1,
                jobs_dir=Path("/tmp/jobs"),
                agent_module=Path("/tmp/foe_agent.py"),
                trace_evaluator=Path("/tmp/score-trace"),
                foe=Path("/tmp/foe"),
                credential_state=Path("/tmp/private.json"),
                model="openai-codex/gpt-5.6-luna",
                reasoning_effort="low",
                diagnosis_model=None,
                diagnosis_reasoning_effort="high",
                diagnosis_model_calls=6,
                diagnosis_pricing=None,
                unresolved_diagnosis_reasoning_effort=None,
                unresolved_diagnosis_model_calls=6,
                escalation_reasoning_effort=None,
                escalation_model_calls=0,
                runtime_digest="abc123",
                pricing=pricing["openai-codex/gpt-5.6-luna"],
                built_in_workflow=True,
            )

    def test_access_only_leases_omit_refresh_and_use_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "worker.json"
            issue_access_only_lease(
                {
                    "access": "access-value",
                    "refresh": "rotating-refresh-value",
                    "expires": 9_000_000,
                    "account_id": "account-value",
                },
                lease,
            )
            value = json.loads(lease.read_text(encoding="utf-8"))
            mode = lease.stat().st_mode & 0o777
        self.assertEqual(
            value,
            {
                "access": "access-value",
                "expires": 9_000_000,
                "account_id": "account-value",
            },
        )
        self.assertEqual(mode, 0o400)

    def test_access_only_lease_rechecks_expiry_when_it_is_issued(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "worker.json"
            state = {
                "access": "access-value",
                "refresh": "refresh-value",
                "expires": 10_000,
            }
            with self.assertRaisesRegex(ValueError, "required execution window"):
                issue_access_only_lease(
                    state,
                    lease,
                    required_expiry_ms=10_000,
                )
            self.assertFalse(lease.exists())

    def test_parallel_lease_requires_the_complete_execution_window(self):
        _, _, tasks, _ = read_cases(Path(__file__).with_name("cases.json"))
        selected = [tasks["fix-git"], tasks["cancel-async-tasks"]]
        required = access_only_lease_requirement_ms(
            selected,
            attempts=2,
            stages=3,
            now_ms=1_000_000,
        )
        self.assertFalse(
            credential_supports_parallel_tasks(
                {"expires": required},
                selected,
                attempts=2,
                stages=3,
                now_ms=1_000_000,
            )
        )
        self.assertTrue(
            credential_supports_parallel_tasks(
                {"expires": required + 1},
                selected,
                attempts=2,
                stages=3,
                now_ms=1_000_000,
            )
        )

    def test_execution_groups_keep_eight_gibibyte_tasks_serial(self):
        _, _, tasks, _ = read_cases(Path(__file__).with_name("cases.json"))
        selected = [
            tasks["fix-git"],
            tasks["cancel-async-tasks"],
            tasks["gpt2-codegolf"],
            tasks["git-multibranch"],
            tasks["sqlite-db-truncate"],
        ]
        self.assertEqual(
            [[task.name for task in group] for group in execution_groups(selected, 2)],
            [
                ["fix-git", "cancel-async-tasks"],
                ["gpt2-codegolf"],
                ["git-multibranch", "sqlite-db-truncate"],
            ],
        )
        self.assertTrue(all(len(group) == 1 for group in execution_groups(selected, 1)))

    def test_required_parallel_plan_refuses_every_serial_fallback(self):
        _, _, tasks, _ = read_cases(Path(__file__).with_name("cases.json"))
        selected = [tasks["fix-git"], tasks["cancel-async-tasks"]]
        terminal_bench_run.validate_parallel_plan(selected, 2, True)
        terminal_bench_run.validate_parallel_plan(selected, 1, False)
        with self.assertRaisesRegex(ValueError, "requires --workers 2"):
            terminal_bench_run.validate_parallel_plan(selected, 1, True)
        with self.assertRaisesRegex(ValueError, "serial tasks: gpt2-codegolf"):
            terminal_bench_run.validate_parallel_plan(
                [tasks["gpt2-codegolf"]], 2, True
            )

    def test_required_parallel_stops_instead_of_running_a_serial_fallback(self):
        _, _, tasks, _ = read_cases(Path(__file__).with_name("cases.json"))
        group = (tasks["fix-git"], tasks["cancel-async-tasks"])
        self.assertEqual(
            terminal_bench_run.required_parallel_stop(
                True, group, False, "available memory is below 14 GiB"
            ),
            "required two-worker cohort cannot start: available memory is below 14 GiB",
        )
        self.assertIsNone(
            terminal_bench_run.required_parallel_stop(
                False, group, False, "available memory is below 14 GiB"
            )
        )
        self.assertIsNone(
            terminal_bench_run.required_parallel_stop(True, group, True, None)
        )

    def test_host_admission_falls_back_on_pressure_and_stops_on_low_capacity(self):
        healthy = HostResources(
            available_memory_mb=16 * 1024,
            free_disk_bytes=120 * 1024**3,
            swap_out_pages=10,
            memory_pressure_avg10=0.0,
        )
        self.assertEqual(parallel_host_admission(healthy, None), (True, None))
        swapped = HostResources(
            available_memory_mb=healthy.available_memory_mb,
            free_disk_bytes=healthy.free_disk_bytes,
            swap_out_pages=11,
            memory_pressure_avg10=healthy.memory_pressure_avg10,
        )
        self.assertEqual(
            parallel_host_admission(swapped, healthy),
            (False, "the host swapped pages out after the preceding cohort"),
        )
        low_memory = HostResources(
            available_memory_mb=9 * 1024,
            free_disk_bytes=healthy.free_disk_bytes,
            swap_out_pages=healthy.swap_out_pages,
            memory_pressure_avg10=healthy.memory_pressure_avg10,
        )
        self.assertEqual(
            run_host_admission(low_memory),
            (False, "available memory is below 10 GiB"),
        )

    def test_commands_start_together_and_retain_partial_failure_codes(self):
        created = []
        codes = iter((1, 0))

        class Process:
            def __init__(self, code):
                self.code = code

            def wait(self):
                self.assert_all_started()
                return self.code

            @staticmethod
            def assert_all_started():
                if len(created) != 2:
                    raise AssertionError("a worker waited before its peer started")

        def start(command, *, cwd, start_new_session):
            self.assertEqual(cwd, Path("/workspace"))
            self.assertTrue(start_new_session)
            process = Process(next(codes))
            created.append((command, process))
            return process

        started_counts = []
        exit_codes = run_commands(
            [["harbor", "first"], ["harbor", "second"]],
            cwd=Path("/workspace"),
            popen_factory=start,
            process_started=started_counts.append,
        )
        self.assertEqual(exit_codes, [1, 0])
        self.assertEqual(started_counts, [1, 2])

    def test_command_start_failure_cancels_every_started_worker(self):
        first = object()
        calls = 0
        started_counts = []

        def start(_command, *, cwd, start_new_session):
            nonlocal calls
            self.assertEqual(cwd, Path("/workspace"))
            self.assertTrue(start_new_session)
            calls += 1
            if calls == 1:
                return first
            raise OSError("process table full")

        with mock.patch.object(terminal_bench_run, "terminate_processes") as terminate:
            with self.assertRaisesRegex(OSError, "process table full"):
                run_commands(
                    [["harbor", "first"], ["harbor", "second"]],
                    cwd=Path("/workspace"),
                    popen_factory=start,
                    process_started=started_counts.append,
                )
        terminate.assert_called_once_with([first])
        self.assertEqual(started_counts, [1])

    def test_keyboard_interrupt_cancels_every_worker(self):
        class Process:
            def wait(self):
                raise KeyboardInterrupt

        first = Process()
        second = Process()
        started = iter((first, second))

        def start_process(_command, *, cwd, start_new_session):
            self.assertEqual(cwd, Path("/workspace"))
            self.assertTrue(start_new_session)
            return next(started)

        with mock.patch.object(terminal_bench_run, "terminate_processes") as terminate:
            with self.assertRaises(KeyboardInterrupt):
                run_commands(
                    [["harbor", "first"], ["harbor", "second"]],
                    cwd=Path("/workspace"),
                    popen_factory=start_process,
                )
        terminate.assert_called_once_with([first, second])

    def test_terminal_signal_unwinds_the_lease_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            lease = None
            previous_sigint = signal.getsignal(signal.SIGINT)
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            with self.assertRaises(CampaignCancellation):
                with campaign_signal_handlers():
                    self.assertTrue(callable(signal.getsignal(signal.SIGINT)))
                    with tempfile.TemporaryDirectory(dir=parent) as lease_text:
                        lease = Path(lease_text)
                        (lease / "credential.json").write_text(
                            "secret",
                            encoding="utf-8",
                        )
                        handler = signal.getsignal(signal.SIGTERM)
                        self.assertTrue(callable(handler))
                        handler(signal.SIGTERM, None)
            self.assertIsNotNone(lease)
            self.assertFalse(lease.exists())
            self.assertEqual(signal.getsignal(signal.SIGINT), previous_sigint)
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous_sigterm)

    def test_atomic_manifest_checkpoint_replaces_a_complete_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            write_json_atomic(path, {"jobs": [{"task": "first"}]})
            write_json_atomic(
                path,
                {"jobs": [{"task": "first"}, {"task": "second"}]},
            )
            retained = json.loads(path.read_text(encoding="utf-8"))
            leftovers = list(path.parent.glob(".campaign.json.*"))
        self.assertEqual(
            retained,
            {"jobs": [{"task": "first"}, {"task": "second"}]},
        )
        self.assertEqual(leftovers, [])

    def test_process_termination_escalates_after_the_grace_period(self):
        class Process:
            pid = 101

            @staticmethod
            def poll():
                return None

            @staticmethod
            def wait(timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("harbor", timeout)
                return -signal.SIGKILL

        with (
            mock.patch.object(terminal_bench_run.os, "killpg") as killpg,
            mock.patch.object(
                terminal_bench_run.time,
                "monotonic",
                side_effect=(100.0, 100.0),
            ),
        ):
            terminal_bench_run.terminate_processes([Process()])
        self.assertEqual(
            killpg.call_args_list,
            [mock.call(101, signal.SIGTERM), mock.call(101, signal.SIGKILL)],
        )

    def test_process_termination_does_not_signal_a_process_that_exited(self):
        class Process:
            pid = 101

            def __init__(self):
                self.exited = False

            def poll(self):
                return 0 if self.exited else None

            def wait(self, timeout=None):
                self.exited = True
                return 0

        process = Process()
        with mock.patch.object(terminal_bench_run.os, "killpg") as killpg:
            terminal_bench_run.terminate_processes([process])
        self.assertEqual(killpg.call_args_list, [mock.call(101, signal.SIGTERM)])

    def test_hard_token_limits_are_an_explicit_runner_option(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            hard_token_limits=True,
        )
        self.assertIn("input_tokens=120000", command)
        self.assertIn("output_tokens=20000", command)

    def test_harbor_command_records_the_diagnosis_model_and_its_pricing(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=pricing["openai-codex/gpt-5.6-luna"],
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort="xhigh",
            escalation_model_calls=18,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
        )
        self.assertIn("diagnosis_model=openai-codex/gpt-5.6-luna", command)
        self.assertIn("diagnosis_model_calls=6", command)
        self.assertIn("diagnosis_input_per_million=0.2", command)
        self.assertIn("escalation_reasoning_effort=xhigh", command)
        self.assertIn("escalation_model_calls=18", command)
        self.assertAlmostEqual(
            float(command[command.index("--agent-timeout-multiplier") + 1]),
            (tasks["fix-git"].seconds * 3 + 300)
            / tasks["fix-git"].harbor_agent_seconds,
        )

    def test_harbor_command_records_the_completion_checker(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["fix-git"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            completion_checker=Path("/tmp/completion-check"),
        )
        self.assertIn("completion_checker=/tmp/completion-check", command)

    def test_harbor_command_records_separate_assessment_and_repair(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        task = tasks["fix-git"]
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=task,
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model=None,
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=None,
            unresolved_diagnosis_reasoning_effort=None,
            unresolved_diagnosis_model_calls=6,
            escalation_reasoning_effort="xhigh",
            escalation_model_calls=25,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
            separate_audit_and_repair=True,
        )
        self.assertIn("separate_audit_and_repair=true", command)
        self.assertAlmostEqual(
            float(command[command.index("--agent-timeout-multiplier") + 1]),
            (task.seconds * 3 + 300) / task.harbor_agent_seconds,
        )

    def test_harbor_command_records_conditional_unresolved_diagnosis(self):
        _, _, tasks, pricing = read_cases(Path(__file__).with_name("cases.json"))
        command = harbor_command(
            harbor=Path("/tools/harbor"),
            dataset="terminal-bench/terminal-bench-2-1@6",
            task=tasks["gpt2-codegolf"],
            attempts=1,
            jobs_dir=Path("/tmp/jobs"),
            agent_module=Path("/tmp/foe_agent.py"),
            trace_evaluator=Path("/tmp/score-trace"),
            foe=Path("/tmp/foe"),
            credential_state=Path("/tmp/private.json"),
            model="openai-codex/gpt-5.6-sol",
            reasoning_effort="low",
            diagnosis_model="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            diagnosis_pricing=pricing["openai-codex/gpt-5.6-luna"],
            unresolved_diagnosis_reasoning_effort="xhigh",
            unresolved_diagnosis_model_calls=20,
            escalation_reasoning_effort=None,
            escalation_model_calls=0,
            runtime_digest="abc123",
            pricing=pricing["openai-codex/gpt-5.6-sol"],
        )
        self.assertIn("unresolved_diagnosis_reasoning_effort=xhigh", command)
        self.assertIn("unresolved_diagnosis_model_calls=20", command)

    def test_job_result_reports_trial_exceptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(
                json.dumps(
                    {
                        "n_total_trials": 2,
                        "stats": {
                            "n_completed_trials": 2,
                            "n_errored_trials": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = read_job_result(path)
        self.assertEqual(result["n_errored_trials"], 1)

    def test_job_integrity_separates_runtime_failure_from_missing_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            retain_empty_verifier_report(trial)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {
                                    "kind": "failed",
                                    "error": "provider response ended",
                                },
                                "foe_trace_conformant": True,
                                "foe_usage_reported": False,
                                "foe_unreported_model_calls": 1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(job)
        self.assertEqual(
            integrity["infrastructure_failures"],
            ["task__attempt: Foe runtime failed: provider response ended"],
        )
        self.assertFalse(integrity["configuration_claim_valid"])
        self.assertEqual(
            integrity["incomplete_resource_measurements"],
            ["task__attempt: 1 model call(s) lack provider usage"],
        )

    def test_job_integrity_rejects_a_changed_completion_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            retain_empty_verifier_report(trial)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {"kind": "completed", "value": "done"},
                                "foe_trace_conformant": True,
                                "foe_usage_reported": True,
                                "foe_completion_checker_unchanged": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(job)
        self.assertEqual(
            integrity["infrastructure_failures"],
            ["task__attempt: the completion checker changed during the trial"],
        )

    def test_job_integrity_rejects_a_missing_structured_verifier_report(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {"kind": "completed", "value": "done"},
                                "foe_trace_conformant": True,
                                "foe_usage_reported": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(job)
        self.assertEqual(
            integrity["infrastructure_failures"],
            ["task__attempt: the task verifier produced no structured report"],
        )
        self.assertFalse(integrity["configuration_claim_valid"])

    def test_job_integrity_rejects_a_changed_access_only_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            retain_empty_verifier_report(trial)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {"kind": "completed", "value": "done"},
                                "foe_trace_conformant": True,
                                "foe_usage_reported": True,
                                "foe_credential_mode": "access_only",
                                "foe_credential_unchanged": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(job)
        self.assertEqual(
            integrity["infrastructure_failures"],
            ["task__attempt: the access-only credential changed during the trial"],
        )

    def test_job_integrity_rejects_model_visible_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            retain_empty_verifier_report(trial)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "agent_result": {
                            "metadata": {
                                "foe_outcome": {
                                    "kind": "completed",
                                    "value": "done",
                                },
                                "foe_trace_conformant": True,
                                "foe_usage_reported": True,
                                "foe_credential_exposed": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            integrity = read_job_integrity(job)
        self.assertEqual(
            integrity["infrastructure_failures"],
            [
                "task__attempt: retained Foe artifacts contain a provider credential"
            ],
        )

    def test_configuration_failures_preserve_quality_but_invalidate_the_claim(self):
        records = [
            {
                "task": "one",
                "harbor_exit_code": 1,
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_total_trials": 1,
                "infrastructure_failures": ["one: Foe runtime failed"],
                "configuration_claim_valid": False,
            },
            {
                "task": "two",
                "harbor_exit_code": 0,
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_total_trials": 1,
                "infrastructure_failures": [],
                "configuration_claim_valid": True,
            },
        ]
        self.assertEqual(records[0]["n_completed_trials"], 1)
        self.assertFalse(campaign_execution_complete(records, 2))
        records[0]["configuration_claim_valid"] = True
        self.assertTrue(campaign_execution_complete(records, 2))
        records[0]["n_completed_trials"] = 0
        records[0]["n_errored_trials"] = 1
        self.assertFalse(campaign_execution_complete(records, 2))
        records[0]["n_completed_trials"] = 1
        records[0]["n_errored_trials"] = 0
        records[1]["result_error"] = "Harbor result has no stats object"
        self.assertFalse(campaign_execution_complete(records, 2))

    def test_source_tree_requires_a_clean_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Cargo.toml"
            source.write_text("[workspace]\n", encoding="utf-8")
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(root), "add", "Cargo.toml"], check=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Foe Test",
                    "-c",
                    "user.email=foe@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "Create source tree",
                ],
                check=True,
            )
            self.assertRegex(source_tree(source), r"^git-tree-sha1:[0-9a-f]{40}$")
            (root / "change").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source tree is not clean"):
                source_tree(source)

    def test_credential_state_lock_rejects_a_second_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "credential.json"
            first = lock_credential_state(state)
            try:
                with self.assertRaisesRegex(ValueError, "another Terminal-Bench run"):
                    lock_credential_state(state)
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
