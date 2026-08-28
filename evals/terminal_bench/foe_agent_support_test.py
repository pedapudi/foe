#!/usr/bin/python3

import json
import shlex
import tempfile
import unittest
from pathlib import Path

from foe_agent_support import (
    COMPLETION_SCHEMA,
    FIXED_EXECUTABLE_PATHS,
    builtin_workflow_arguments,
    build_program,
    credential_values,
    describe_container_environment,
    estimate_usage_cost,
    fixed_executable_probe_command,
    missing_builtin_workflow_options,
    missing_episode_diagnostic,
    normalized_plan,
    normalized_episode_plan,
    parse_boolean,
    program_document_from_episode_start,
    read_episode_summary,
    replace_json,
    retained_artifacts_contain_credential,
    schema_probe_command,
)


class ProgramTest(unittest.TestCase):
    def test_normalized_plan_replaces_a_read_only_container_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "foe-plan.json"
            path.write_text('{"program":{"name":"before"}}\n', encoding="utf-8")
            path.chmod(0o444)
            replace_json(path, {"program": {"name": "after"}, "task": "repair it"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"program": {"name": "after"}, "task": "repair it"},
            )
            self.assertFalse(path.with_name(".foe-plan.json.normalized").exists())

    def test_retained_plan_adds_or_checks_a_task_outside_program(self):
        old = normalized_plan({"program": {"name": "p"}}, "full instruction")
        self.assertEqual(old["task"], "full instruction")
        current = normalized_plan(
            {"program": {"name": "p"}, "task": "full instruction"},
            "full instruction",
        )
        self.assertEqual(current, old)
        with self.assertRaisesRegex(ValueError, "different from the controller instruction"):
            normalized_plan({"program": {"name": "p"}, "task": "other"}, "full instruction")
        with self.assertRaisesRegex(ValueError, "program must omit task"):
            normalized_plan({"program": {"name": "p", "task": "full instruction"}}, "full instruction")

    def test_builtin_workflow_invocation_uses_the_command_line_surface(self):
        arguments = builtin_workflow_arguments(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/tmp/completion-check",
            "/logs/episode",
            "priority",
        )
        self.assertEqual(
            arguments,
            (
                "/usr/local/bin/foe",
                "repair it",
                "--model",
                "openai-codex/gpt-5.6-sol",
                "--service-tier",
                "priority",
                "--key-file",
                "/tmp/private.json",
                "--verify",
                "/tmp/completion-check",
                "--sandbox",
                "off",
                "--headless",
                "--log-dir",
                "/logs/episode",
            ),
        )

    def test_builtin_workflow_invocation_can_remain_closed_book(self):
        arguments = builtin_workflow_arguments(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            None,
            "/logs/episode",
            "priority",
        )
        self.assertNotIn("--verify", arguments)
        self.assertEqual(arguments[0:2], ("/usr/local/bin/foe", "repair it"))
        self.assertEqual(arguments[-2:], ("--log-dir", "/logs/episode"))

    def test_builtin_workflow_plan_is_reconstructed_from_the_root_start(self):
        with tempfile.TemporaryDirectory() as directory:
            episode = Path(directory) / "episode.jsonl"
            program = {"name": "coding", "budget": {"model_calls": 120}}
            episode.write_text(
                json.dumps(
                    {
                        "seq": 0,
                        "type": "episode/start",
                        "data": {
                            "task": "repair it",
                            "program": program,
                            "identity": "sha256:" + "1" * 64,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            document, identity = program_document_from_episode_start(
                episode, "repair it"
            )
        self.assertEqual(
            document,
            {"version": 3, "task": "repair it", **program},
        )
        planned = normalized_episode_plan(
            {
                "program": program,
                "identity": identity,
            },
            "repair it",
            document,
            identity,
        )
        self.assertEqual(planned["task"], "repair it")

    def test_builtin_workflow_reconstruction_rejects_a_different_plan(self):
        with self.assertRaisesRegex(ValueError, "program differs"):
            normalized_episode_plan(
                {"program": {"name": "other"}, "identity": "sha256:" + "1" * 64},
                "repair it",
                {"version": 3, "task": "repair it", "name": "coding"},
                "sha256:" + "1" * 64,
            )
        with self.assertRaisesRegex(ValueError, "identity differs"):
            normalized_episode_plan(
                {"program": {"name": "coding"}, "identity": "sha256:" + "2" * 64},
                "repair it",
                {"version": 3, "task": "repair it", "name": "coding"},
                "sha256:" + "1" * 64,
            )

    def test_builtin_workflow_reconstruction_rejects_an_invalid_root_start(self):
        valid = {
            "seq": 0,
            "type": "episode/start",
            "data": {
                "task": "repair it",
                "program": {"name": "coding"},
                "identity": "sha256:" + "1" * 64,
            },
        }
        cases = (
            ({**valid, "seq": 1}, "sequence zero"),
            ({**valid, "type": "tool/result"}, "sequence zero"),
            ({**valid, "data": {**valid["data"], "task": "other"}}, "task differs"),
            (
                {
                    **valid,
                    "data": {
                        **valid["data"],
                        "program": {"version": 3, "name": "coding"},
                    },
                },
                "omit task and format version",
            ),
            ({**valid, "data": {**valid["data"], "identity": "sha256:BAD"}}, "identity is invalid"),
        )
        with tempfile.TemporaryDirectory() as directory:
            episode = Path(directory) / "episode.jsonl"
            for event, message in cases:
                episode.write_text(json.dumps(event) + "\n", encoding="utf-8")
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError, message):
                        program_document_from_episode_start(episode, "repair it")

    def test_builtin_workflow_arguments_survive_shell_quoting(self):
        instruction = "line one\n'$(touch /tmp/forbidden)' \"$HOME\""
        arguments = builtin_workflow_arguments(
            instruction,
            "openai-codex/gpt-5.6-sol",
            "/tmp/private path.json",
            "/tmp/check path",
            "/logs/episode path",
            "priority",
        )
        self.assertEqual(shlex.split(shlex.join(arguments)), list(arguments))

    def test_builtin_workflow_option_probe_requires_the_verifier_lane(self):
        help_text = """options:
  --model PROVIDER/MODEL
  --key-file PATH
  --headless
  --log-dir DIR
  --service-tier TIER
  --sandbox MODE
"""
        self.assertEqual(
            missing_builtin_workflow_options(help_text),
            ["--verify"],
        )
        help_text += "  --verify PATH\n"
        self.assertEqual(
            missing_builtin_workflow_options(help_text),
            [],
        )

    def test_harbor_boolean_parser_rejects_ambiguous_values(self):
        self.assertTrue(parse_boolean(True, "built_in_workflow"))
        self.assertTrue(parse_boolean("true", "built_in_workflow"))
        self.assertFalse(parse_boolean("0", "built_in_workflow"))
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            parse_boolean("enabled", "built_in_workflow")

    def test_missing_episode_diagnostic_retains_status_and_bounded_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            (logs / "foe.stderr").write_text("prefix-" + "x" * 2100, encoding="utf-8")
            diagnostic = missing_episode_diagnostic(
                logs,
                2,
                frozenset({"secret-value"}),
            )
            self.assertIsNotNone(diagnostic)
            self.assertIn("status 2", diagnostic)
            self.assertNotIn("prefix", diagnostic)
            (logs / "foe.stderr").write_text(
                "failure: secret-value",
                encoding="utf-8",
            )
            diagnostic = missing_episode_diagnostic(
                logs,
                2,
                frozenset({"secret-value"}),
            )
            self.assertNotIn("secret-value", diagnostic)
            self.assertIn("[credential redacted]", diagnostic)
            episode = logs / "foe-episode"
            episode.mkdir()
            (episode / "episode.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertIsNone(missing_episode_diagnostic(logs, 0))

    def test_installers_share_the_supported_schema_probe(self):
        self.assertEqual(
            schema_probe_command("/opt/Foe Binary"),
            "'/opt/Foe Binary' plan --schema >/dev/null",
        )
        with self.assertRaisesRegex(ValueError, "binary path must be absolute"):
            schema_probe_command("foe")

    def test_fixed_path_probe_produces_validated_environment_facts(self):
        command = fixed_executable_probe_command()
        self.assertIn("test -x /usr/bin/python3", command)
        observations = "\n".join(
            f"{name}={path}" if name in {"sh", "gcc"} else f"{name}=not found at {path}"
            for name, path in FIXED_EXECUTABLE_PATHS
        )
        facts = describe_container_environment("/app", observations)
        self.assertIn("Working directory: /app", facts)
        self.assertIn("gcc=/usr/bin/gcc", facts)
        self.assertIn("python3=not found at /usr/bin/python3", facts)
        with self.assertRaisesRegex(ValueError, "incomplete observation set"):
            describe_container_environment("/app", "sh=/bin/sh")

    def test_program_declares_container_authority_and_split_allowances(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=12,
            input_tokens=120_000,
            output_tokens=20_000,
            seconds=600,
            reasoning_effort="low",
        )
        self.assertEqual(
            program["grants"],
            {"read": ["/workspace", "/"], "write": ["/"]},
        )
        self.assertEqual(
            program["budget"],
            {
                "model_calls": 12,
                "input_tokens": 120_000,
                "output_tokens": 20_000,
                "seconds": 600,
                "loop_threshold": 8,
            },
        )
        self.assertEqual(program["sandbox"], {"mode": "off"})
        self.assertEqual(program["model"]["reasoning_effort"], "low")
        self.assertEqual(program["model"]["service_tier"], "default")
        self.assertEqual(program["model"]["token_file"], "/tmp/private.json")
        self.assertNotIn("api_key_file", program["model"])
        self.assertEqual(program["task"], "repair it")
        self.assertIn(
            "two materially different behavioral inputs",
            program["instructions"]["role"],
        )

    def test_program_omits_soft_token_measurements_from_the_allowance(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-luna",
            "/tmp/private.json",
            "/workspace",
            model_calls=20,
            input_tokens=None,
            output_tokens=None,
            seconds=600,
            reasoning_effort="low",
        )
        self.assertEqual(
            program["budget"],
            {"model_calls": 20, "seconds": 600, "loop_threshold": 8},
        )

    def test_terminal_audit_owns_completion_check_after_escalation(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=20,
            input_tokens=None,
            output_tokens=None,
            seconds=600,
            reasoning_effort="low",
            completion_checker="/tmp/completion-check",
            escalation_reasoning_effort="high",
            escalation_model_calls=10,
        )
        self.assertEqual(program["tool_defs"]["check"]["exec"], "/tmp/completion-check")
        self.assertEqual(program["done_when"]["verify"], "check")
        self.assertEqual(program["done_when"]["retries"], 12)
        self.assertIn("check", program["tools"])
        implementation = program["workflow"]["nodes"]["implement-task"]["model"]
        audit = program["workflow"]["nodes"]["audit-and-repair-task"]["model"]
        self.assertEqual(
            implementation["done_when"],
            {"returns": program["done_when"]["returns"]},
        )
        self.assertEqual(audit["done_when"], program["done_when"])
        for node in (implementation, audit):
            self.assertEqual(node["tool_defs"], program["tool_defs"])
            self.assertIn("check", node["tools"])

    def test_completion_checker_requires_an_absolute_path(self):
        with self.assertRaisesRegex(ValueError, "completion checker must be an absolute path"):
            build_program(
                "repair it",
                "openai-codex/gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=20,
                input_tokens=None,
                output_tokens=None,
                seconds=600,
                reasoning_effort="low",
                completion_checker="relative-check",
            )

    def test_program_rejects_unqualified_model(self):
        with self.assertRaisesRegex(ValueError, "provider/model"):
            build_program(
                "repair it",
                "gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=1,
                input_tokens=1,
                output_tokens=1,
                seconds=1,
                reasoning_effort="low",
            )

    def test_program_can_diagnose_then_implement_in_separate_model_episodes(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=20,
            input_tokens=None,
            output_tokens=None,
            seconds=600,
            reasoning_effort="low",
            diagnosis_model_name="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
        )
        nodes = program["workflow"]["nodes"]
        diagnosis = nodes["diagnose-task"]["model"]
        implementation = nodes["implement-task"]["model"]
        self.assertEqual(diagnosis["model"]["model"], "gpt-5.6-luna")
        self.assertEqual(diagnosis["budget"]["model_calls"], 6)
        self.assertEqual(diagnosis["budget"]["seconds"], 600)
        self.assertEqual(diagnosis["budget"]["loop_threshold"], 8)
        self.assertEqual(diagnosis["tools"], ["read", "grep", "bash"])
        self.assertNotIn("sandbox", diagnosis)
        self.assertIn("four model requests as a planning target", diagnosis["instructions"]["role"])
        self.assertIn("loop backstop", diagnosis["instructions"]["role"])
        self.assertNotIn("final request", diagnosis["instructions"]["role"])
        self.assertIn("returns", diagnosis["done_when"])
        self.assertEqual(
            diagnosis["done_when"]["returns"]["required"],
            ["facts", "implementation_steps", "verification_steps"],
        )
        self.assertEqual(implementation["model"]["model"], "gpt-5.6-sol")
        self.assertIn(
            "two materially different behavioral inputs",
            implementation["instructions"]["role"],
        )
        self.assertEqual(implementation["budget"]["model_calls"], 20)
        self.assertEqual(implementation["budget"]["seconds"], 600)
        self.assertEqual(implementation["budget"]["loop_threshold"], 8)
        self.assertEqual(program["budget"]["model_calls"], 26)
        self.assertEqual(program["budget"]["seconds"], 1200)
        self.assertEqual(nodes["implement-task"]["follows"], ["task", "diagnose-task"])
        self.assertTrue(nodes["implement-task"]["terminal"])

    def test_default_diagnosis_allowance_is_a_backstop(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=60,
            input_tokens=None,
            output_tokens=None,
            seconds=1800,
            reasoning_effort="low",
            diagnosis_model_name="openai-codex/gpt-5.6-luna",
        )
        diagnosis = program["workflow"]["nodes"]["diagnose-task"]["model"]
        self.assertEqual(diagnosis["budget"]["model_calls"], 20)
        self.assertEqual(program["budget"]["model_calls"], 80)

    def test_program_can_escalate_to_a_fresh_repair_episode(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=60,
            input_tokens=None,
            output_tokens=None,
            seconds=900,
            reasoning_effort="low",
            diagnosis_model_name="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="low",
            diagnosis_model_calls=6,
            escalation_reasoning_effort="xhigh",
            escalation_model_calls=18,
            environment_facts="Working directory: /workspace. gcc=/usr/bin/gcc.",
        )
        nodes = program["workflow"]["nodes"]
        self.assertFalse(nodes["implement-task"]["terminal"])
        self.assertEqual(nodes["implement-task"]["model"]["budget"]["model_calls"], 60)
        self.assertEqual(nodes["diagnose-task"]["model"]["budget"]["seconds"], 900)
        self.assertEqual(nodes["implement-task"]["model"]["budget"]["seconds"], 900)
        repair = nodes["audit-and-repair-task"]
        self.assertEqual(repair["model"]["model"]["reasoning_effort"], "xhigh")
        self.assertEqual(repair["model"]["budget"]["model_calls"], 18)
        self.assertEqual(repair["model"]["budget"]["seconds"], 900)
        self.assertEqual(
            repair["model"]["instructions"]["environment"],
            "Working directory: /workspace. gcc=/usr/bin/gcc.",
        )
        self.assertIn(
            "Generate a second valid fixture when the workspace supplies only one",
            repair["model"]["instructions"]["role"],
        )
        self.assertEqual(
            repair["model"]["done_when"]["returns"]["required"],
            ["summary", "changed_paths", "validation", "unresolved_risks"],
        )
        self.assertEqual(
            nodes["implement-task"]["model"]["done_when"]["returns"],
            repair["model"]["done_when"]["returns"],
        )
        self.assertEqual(repair["follows"], ["task", "implement-task"])
        self.assertTrue(repair["terminal"])
        self.assertEqual(program["budget"]["max_episodes"], 4)
        self.assertEqual(program["budget"]["model_calls"], 84)
        self.assertEqual(program["budget"]["seconds"], 2700)

    def test_program_routes_unresolved_cheap_diagnosis_to_deeper_diagnosis(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=60,
            input_tokens=None,
            output_tokens=None,
            seconds=1800,
            reasoning_effort="low",
            diagnosis_model_name="openai-codex/gpt-5.6-luna",
            diagnosis_reasoning_effort="high",
            diagnosis_model_calls=6,
            unresolved_diagnosis_reasoning_effort="xhigh",
            unresolved_diagnosis_model_calls=20,
        )
        nodes = program["workflow"]["nodes"]
        self.assertNotIn("implement-task", nodes)
        self.assertEqual(
            nodes["diagnose-task"]["branches"],
            {
                "implement": ["implement-resolved-task"],
                "investigate-unresolved-facts": ["diagnose-unresolved-task"],
            },
        )
        self.assertIn("implementation-critical fact", nodes["diagnose-task"]["model"]["instructions"]["role"])
        deeper = nodes["diagnose-unresolved-task"]
        self.assertEqual(deeper["follows"], ["task", "diagnose-task"])
        self.assertEqual(deeper["model"]["model"]["model"], "gpt-5.6-sol")
        self.assertEqual(deeper["model"]["model"]["reasoning_effort"], "xhigh")
        self.assertEqual(deeper["model"]["budget"]["model_calls"], 20)
        self.assertEqual(deeper["model"]["budget"]["seconds"], 1800)
        self.assertIn("six model requests as a planning target", deeper["model"]["instructions"]["role"])
        self.assertIn("coding episode owns end-to-end validation", deeper["model"]["instructions"]["role"])
        self.assertIn("loop backstop", deeper["model"]["instructions"]["role"])
        self.assertNotIn("final request", deeper["model"]["instructions"]["role"])
        self.assertEqual(
            deeper["model"]["done_when"]["returns"]["required"],
            ["facts", "implementation_steps", "verification_steps"],
        )
        self.assertEqual(
            nodes["implement-resolved-task"]["follows"],
            ["task", "diagnose-task"],
        )
        self.assertEqual(
            nodes["implement-after-unresolved-diagnosis"]["follows"],
            ["task", "diagnose-unresolved-task"],
        )
        self.assertTrue(nodes["implement-resolved-task"]["terminal"])
        self.assertTrue(nodes["implement-after-unresolved-diagnosis"]["terminal"])
        self.assertEqual(program["budget"]["max_episodes"], 4)
        self.assertEqual(program["budget"]["model_calls"], 86)
        self.assertEqual(program["budget"]["seconds"], 5400)

    def test_program_rejects_tight_auxiliary_backstops(self):
        with self.assertRaisesRegex(ValueError, "diagnosis model calls must be at least 6"):
            build_program(
                "repair it",
                "openai-codex/gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=40,
                input_tokens=None,
                output_tokens=None,
                seconds=900,
                reasoning_effort="low",
                diagnosis_model_name="openai-codex/gpt-5.6-luna",
                diagnosis_model_calls=5,
            )
        with self.assertRaisesRegex(ValueError, "escalation model calls must be at least 6"):
            build_program(
                "repair it",
                "openai-codex/gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=40,
                input_tokens=None,
                output_tokens=None,
                seconds=900,
                reasoning_effort="low",
                escalation_reasoning_effort="xhigh",
                escalation_model_calls=5,
            )
        with self.assertRaisesRegex(
            ValueError,
            "unresolved diagnosis model calls must be at least 6",
        ):
            build_program(
                "repair it",
                "openai-codex/gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=60,
                input_tokens=None,
                output_tokens=None,
                seconds=1800,
                reasoning_effort="low",
                diagnosis_model_name="openai-codex/gpt-5.6-luna",
                unresolved_diagnosis_reasoning_effort="xhigh",
                unresolved_diagnosis_model_calls=5,
            )

    def test_unresolved_diagnosis_requires_cheap_diagnosis_and_excludes_repair(self):
        arguments = {
            "instruction": "repair it",
            "model_name": "openai-codex/gpt-5.6-sol",
            "credential_path": "/tmp/private.json",
            "working_directory": "/workspace",
            "model_calls": 60,
            "input_tokens": None,
            "output_tokens": None,
            "seconds": 1800,
            "reasoning_effort": "low",
            "unresolved_diagnosis_reasoning_effort": "xhigh",
        }
        with self.assertRaisesRegex(ValueError, "requires a diagnosis model"):
            build_program(**arguments)
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            build_program(
                **arguments,
                diagnosis_model_name="openai-codex/gpt-5.6-luna",
                escalation_reasoning_effort="xhigh",
                escalation_model_calls=6,
            )

    def test_program_can_escalate_without_a_diagnosis_episode(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=60,
            input_tokens=None,
            output_tokens=None,
            seconds=900,
            reasoning_effort="low",
            escalation_reasoning_effort="xhigh",
            escalation_model_calls=25,
        )
        nodes = program["workflow"]["nodes"]
        self.assertNotIn("diagnose-task", nodes)
        self.assertEqual(nodes["implement-task"]["model"]["name"], "implement-task")
        self.assertEqual(nodes["implement-task"]["follows"], ["task"])
        self.assertEqual(
            nodes["implement-task"]["model"]["budget"],
            {"model_calls": 60, "seconds": 900, "loop_threshold": 8},
        )
        self.assertEqual(
            nodes["audit-and-repair-task"]["model"]["budget"],
            {"model_calls": 25, "seconds": 900, "loop_threshold": 8},
        )
        self.assertEqual(program["budget"]["max_episodes"], 3)
        self.assertEqual(program["budget"]["model_calls"], 85)
        self.assertEqual(program["budget"]["seconds"], 1800)

    def test_program_can_assess_before_conditionally_repairing(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=60,
            input_tokens=None,
            output_tokens=None,
            seconds=900,
            reasoning_effort="low",
            escalation_reasoning_effort="xhigh",
            escalation_model_calls=25,
            separate_audit_and_repair=True,
        )
        nodes = program["workflow"]["nodes"]
        self.assertFalse(nodes["implement-task"]["terminal"])
        assessment = nodes["assess-task"]
        self.assertEqual(assessment["follows"], ["task", "implement-task"])
        self.assertEqual(
            assessment["branches"],
            {"accept": [], "repair": ["repair-task"]},
        )
        self.assertEqual(assessment["model"]["tools"], ["read", "grep", "bash"])
        self.assertNotIn("write", assessment["model"]["grants"])
        self.assertIn(
            "do not change the workspace",
            assessment["model"]["instructions"]["role"],
        )
        self.assertIn(
            "Do not require compatibility with absent formats",
            assessment["model"]["instructions"]["role"],
        )
        self.assertEqual(
            assessment["model"]["done_when"]["returns"]["required"],
            ["summary", "findings", "validation", "unresolved_risks"],
        )
        repair = nodes["repair-task"]
        self.assertEqual(
            repair["follows"],
            ["task", "implement-task", "assess-task"],
        )
        self.assertIn("smallest change", repair["model"]["instructions"]["role"])
        self.assertIn(
            "Treat every finding and unresolved risk as an obligation",
            repair["model"]["instructions"]["role"],
        )
        self.assertEqual(
            repair["model"]["done_when"]["returns"]["properties"][
                "unresolved_risks"
            ]["maxItems"],
            0,
        )
        self.assertTrue(repair["terminal"])
        self.assertEqual(program["budget"]["max_episodes"], 4)
        self.assertEqual(program["budget"]["model_calls"], 110)
        self.assertEqual(program["budget"]["seconds"], 2700)

    def test_separate_assessment_preserves_episode_level_verification(self):
        program = build_program(
            "repair it",
            "openai-codex/gpt-5.6-sol",
            "/tmp/private.json",
            "/workspace",
            model_calls=60,
            input_tokens=None,
            output_tokens=None,
            seconds=900,
            reasoning_effort="low",
            completion_checker="/tmp/completion-check",
            escalation_reasoning_effort="high",
            escalation_model_calls=25,
            separate_audit_and_repair=True,
        )
        nodes = program["workflow"]["nodes"]
        self.assertEqual(
            program["done_when"],
            {"verify": "check", "retries": 12},
        )
        self.assertIn("check", nodes["assess-task"]["model"]["tools"])
        self.assertEqual(
            nodes["repair-task"]["model"]["done_when"]["verify"],
            "check",
        )
        self.assertEqual(
            nodes["repair-task"]["model"]["done_when"]["retries"],
            12,
        )
        self.assertEqual(
            nodes["repair-task"]["model"]["done_when"]["returns"]["properties"][
                "unresolved_risks"
            ]["maxItems"],
            0,
        )

    def test_separate_assessment_requires_an_escalation_model(self):
        with self.assertRaisesRegex(
            ValueError,
            "separate audit and repair requires an escalation reasoning effort",
        ):
            build_program(
                "repair it",
                "openai-codex/gpt-5.6-sol",
                "/tmp/private.json",
                "/workspace",
                model_calls=60,
                input_tokens=None,
                output_tokens=None,
                seconds=900,
                reasoning_effort="low",
                separate_audit_and_repair=True,
            )


class EpisodeSummaryTest(unittest.TestCase):
    def test_credential_exposure_detection_checks_every_retained_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root / "credential.json"
            credential.write_text(
                json.dumps(
                    {"access": "access-secret", "refresh": "refresh-secret"}
                ),
                encoding="utf-8",
            )
            artifacts = root / "artifacts"
            artifacts.mkdir()
            log = artifacts / "foe.stderr"
            log.write_text(
                json.dumps(
                    {
                        "type": "tool/result",
                        "data": {"rendered": "access-secret"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            values = credential_values(credential)
            self.assertTrue(retained_artifacts_contain_credential(artifacts, values))
            log.write_text(
                json.dumps({"type": "tool/result", "data": {"rendered": "safe"}})
                + "\n",
                encoding="utf-8",
            )
            self.assertFalse(retained_artifacts_contain_credential(artifacts, values))

    def test_summary_requires_a_root_episode_log(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "episode log does not exist"):
                read_episode_summary(Path(directory))

    def test_summary_includes_child_usage_and_root_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "children" / "ep_child"
            child.mkdir(parents=True)
            (root / "episode.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "model/request", "data": {}}),
                        json.dumps(
                            {
                                "type": "assistant/message",
                                "data": {"usage": {"input": 10, "output": 2, "cache_read": 4}},
                            }
                        ),
                        json.dumps({"type": "episode/end", "data": {"outcome": {"kind": "accepted"}}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (child / "episode.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "model/request", "data": {}}),
                        json.dumps({"type": "tool/result", "data": {}}),
                        json.dumps(
                            {
                                "type": "assistant/message",
                                "data": {"usage": {"input": 7, "output": 3, "cache_read": 1}},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            summary = read_episode_summary(root)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["tool_calls"], 1)
        self.assertEqual(summary["input_tokens"], 17)
        self.assertEqual(summary["output_tokens"], 5)
        self.assertEqual(summary["cache_read_tokens"], 5)
        self.assertEqual(summary["unreported_model_calls"], 0)
        self.assertEqual(summary["outcome"], {"kind": "accepted"})

    def test_summary_rejects_exact_usage_when_a_request_has_no_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                {"type": "model/request", "data": {}},
                {
                    "type": "assistant/message",
                    "data": {"usage": {"input": 10, "output": 2, "cache_read": 4}},
                },
                {"type": "model/request", "data": {}},
                {
                    "type": "episode/end",
                    "data": {"outcome": {"kind": "failed", "error": "provider failed"}},
                },
            ]
            (root / "episode.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            summary = read_episode_summary(root)
        self.assertFalse(summary["usage_reported"])
        self.assertEqual(summary["unreported_model_calls"], 1)
        self.assertIsNone(summary["input_tokens"])

    def test_cost_uses_cached_rate_and_request_level_long_context_multiplier(self):
        cost = estimate_usage_cost(
            [
                {"input": 100_000, "output": 1_000, "cache_read": 80_000},
                {"input": 300_000, "output": 2_000, "cache_read": 100_000},
            ],
            input_per_million=4.0,
            cached_input_per_million=0.4,
            output_per_million=20.0,
            long_context_threshold=272_000,
            long_context_input_multiplier=2.0,
            long_context_output_multiplier=1.5,
        )
        self.assertAlmostEqual(cost, 1.872)

    def test_summary_prices_each_child_with_its_recorded_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "children" / "ep_child"
            child.mkdir(parents=True)
            for path, model, usage in [
                (root / "episode.jsonl", "gpt-5.6-sol", {"input": 1000, "output": 100, "cache_read": 0}),
                (child / "episode.jsonl", "gpt-5.6-luna", {"input": 1000, "output": 100, "cache_read": 0}),
            ]:
                events = [
                    {
                        "type": "episode/start",
                        "data": {"program": {"model": {"provider": "openai-codex", "model": model}}},
                    },
                    {"type": "model/request", "data": {}},
                    {"type": "assistant/message", "data": {"usage": usage}},
                ]
                if path == root / "episode.jsonl":
                    events.append({"type": "episode/end", "data": {"outcome": {"kind": "completed"}}})
                path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            common = {
                "cached_input_per_million": 0.0,
                "long_context_threshold": 272_000,
                "long_context_input_multiplier": 1.0,
                "long_context_output_multiplier": 1.0,
            }
            summary = read_episode_summary(
                root,
                {
                    "openai-codex/gpt-5.6-sol": {**common, "input_per_million": 4.0, "output_per_million": 20.0},
                    "openai-codex/gpt-5.6-luna": {**common, "input_per_million": 0.2, "output_per_million": 1.2},
                },
            )
        self.assertAlmostEqual(summary["estimated_cost_usd"], 0.00632)


if __name__ == "__main__":
    unittest.main()
