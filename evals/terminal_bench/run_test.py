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
    HostResources,
    access_only_lease_requirement_ms,
    campaign_execution_complete,
    credential_supports_parallel_tasks,
    execution_groups,
    harbor_command,
    issue_access_only_lease,
    lock_credential_state,
    parallel_host_admission,
    read_cases,
    read_job_integrity,
    read_job_result,
    run_commands,
    run_host_admission,
    source_tree,
)


class CasesTest(unittest.TestCase):
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
        self.assertEqual(len(groups["development"]), 6)
        self.assertEqual(len(groups["capability_search"]), 11)
        self.assertEqual(len(groups["confirmation"]), 4)
        self.assertEqual(len(groups["calibration"]), 12)
        self.assertEqual(len(groups["calibration_holdout"]), 6)
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
        self.assertIn("service_tier=priority", command)
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
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
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
        self.assertEqual(
            integrity["incomplete_resource_measurements"],
            ["task__attempt: 1 model call(s) lack provider usage"],
        )

    def test_job_integrity_rejects_a_changed_completion_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
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

    def test_job_integrity_rejects_a_changed_access_only_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            trial = job / "task__attempt"
            trial.mkdir()
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": "task__attempt",
                        "exception_info": None,
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

    def test_runtime_diagnostics_do_not_block_a_complete_quality_run(self):
        records = [
            {
                "task": "one",
                "harbor_exit_code": 1,
                "n_errored_trials": 1,
                "infrastructure_failures": ["one: Foe runtime failed"],
            },
            {
                "task": "two",
                "harbor_exit_code": 0,
                "n_errored_trials": 0,
                "infrastructure_failures": [],
            },
        ]
        self.assertTrue(campaign_execution_complete(records, 2))
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
