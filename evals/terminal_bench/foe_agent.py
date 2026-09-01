#!/usr/bin/python3
"""Harbor installed-agent adapter for a locally built Foe binary."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from foe_agent_support import (
    build_program,
    describe_container_environment,
    fixed_executable_probe_command,
    read_episode_summary,
    replace_credential_state,
    schema_preflight_command,
)


REMOTE_BINARY = "/usr/local/bin/foe"
REMOTE_PROGRAM = "/tmp/foe-terminal-bench-program.json"
REMOTE_COMPLETION_CHECKER = "/tmp/foe-completion-check"
REMOTE_COMPLETION_CHECKER_SETUP = "/tmp/foe-completion-check-setup"


class FoeAgent(BaseInstalledAgent):
    """Run Foe inside a Harbor task container and retain its native episode."""

    def __init__(
        self,
        *args: Any,
        foe_binary: str,
        credential_file: str,
        credential_mode: str = "mutable",
        trace_evaluator: str,
        model_calls: int | str,
        seconds: int | str,
        input_tokens: int | str | None = None,
        output_tokens: int | str | None = None,
        input_per_million: float | str,
        cached_input_per_million: float | str,
        output_per_million: float | str,
        long_context_threshold: int | str,
        long_context_input_multiplier: float | str,
        long_context_output_multiplier: float | str,
        reasoning_effort: str = "low",
        service_tier: str = "priority",
        diagnosis_model: str | None = None,
        diagnosis_reasoning_effort: str = "high",
        diagnosis_model_calls: int | str = 20,
        diagnosis_input_per_million: float | str | None = None,
        diagnosis_cached_input_per_million: float | str | None = None,
        diagnosis_output_per_million: float | str | None = None,
        diagnosis_long_context_threshold: int | str | None = None,
        diagnosis_long_context_input_multiplier: float | str | None = None,
        diagnosis_long_context_output_multiplier: float | str | None = None,
        unresolved_diagnosis_reasoning_effort: str | None = None,
        unresolved_diagnosis_model_calls: int | str = 20,
        escalation_reasoning_effort: str | None = None,
        escalation_model_calls: int | str = 0,
        completion_checker: str | None = None,
        completion_checker_setup: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._foe_binary = Path(foe_binary)
        self._credential_file = Path(credential_file)
        if credential_mode not in ("mutable", "access_only"):
            raise ValueError("credential_mode must be mutable or access_only")
        self._credential_mode = credential_mode
        self._remote_credential = f"/tmp/.foe-credential-{uuid.uuid4().hex}.json"
        self._credential_digest = ""
        self._credential_unchanged: bool | None = None
        self._trace_evaluator = Path(trace_evaluator)
        self._model_calls = int(model_calls)
        self._input_tokens = int(input_tokens) if input_tokens is not None else None
        self._output_tokens = int(output_tokens) if output_tokens is not None else None
        self._seconds = int(seconds)
        self._pricing = {
            "input_per_million": float(input_per_million),
            "cached_input_per_million": float(cached_input_per_million),
            "output_per_million": float(output_per_million),
            "long_context_threshold": int(long_context_threshold),
            "long_context_input_multiplier": float(long_context_input_multiplier),
            "long_context_output_multiplier": float(long_context_output_multiplier),
        }
        self._reasoning_effort = reasoning_effort
        self._service_tier = service_tier
        self._diagnosis_model = diagnosis_model
        self._diagnosis_reasoning_effort = diagnosis_reasoning_effort
        self._diagnosis_model_calls = int(diagnosis_model_calls)
        self._unresolved_diagnosis_reasoning_effort = unresolved_diagnosis_reasoning_effort
        self._unresolved_diagnosis_model_calls = int(unresolved_diagnosis_model_calls)
        self._escalation_reasoning_effort = escalation_reasoning_effort
        self._escalation_model_calls = int(escalation_model_calls)
        self._completion_checker = (
            Path(completion_checker) if completion_checker is not None else None
        )
        self._completion_checker_setup = (
            Path(completion_checker_setup)
            if completion_checker_setup is not None
            else None
        )
        self._completion_checker_digest: str | None = None
        self._completion_checker_setup_digest: str | None = None
        self._observed_completion_checker_digest: str | None = None
        diagnosis_prices = (
            diagnosis_input_per_million,
            diagnosis_cached_input_per_million,
            diagnosis_output_per_million,
            diagnosis_long_context_threshold,
            diagnosis_long_context_input_multiplier,
            diagnosis_long_context_output_multiplier,
        )
        if diagnosis_model is not None and any(value is None for value in diagnosis_prices):
            raise ValueError("diagnosis model pricing must name every rate and long-context rule")
        self._diagnosis_pricing = None
        if diagnosis_model is not None:
            self._diagnosis_pricing = {
                "input_per_million": float(diagnosis_input_per_million),
                "cached_input_per_million": float(diagnosis_cached_input_per_million),
                "output_per_million": float(diagnosis_output_per_million),
                "long_context_threshold": int(diagnosis_long_context_threshold),
                "long_context_input_multiplier": float(diagnosis_long_context_input_multiplier),
                "long_context_output_multiplier": float(diagnosis_long_context_output_multiplier),
            }
        self._exit_code: int | None = None
        if not self._foe_binary.is_file():
            raise FileNotFoundError(f"Foe binary does not exist: {self._foe_binary}")
        if not self._credential_file.is_file():
            raise FileNotFoundError(
                f"Foe credential state does not exist: {self._credential_file}"
            )
        self._credential_digest = hashlib.sha256(
            self._credential_file.read_bytes()
        ).hexdigest()
        if not self._trace_evaluator.is_file():
            raise FileNotFoundError(
                f"Foe trace evaluator does not exist: {self._trace_evaluator}"
            )
        if self._completion_checker is not None and not self._completion_checker.is_file():
            raise FileNotFoundError(
                f"completion checker does not exist: {self._completion_checker}"
            )
        if self._completion_checker_setup is not None:
            if self._completion_checker is None:
                raise ValueError("completion checker setup requires a completion checker")
            if not self._completion_checker_setup.is_file():
                raise FileNotFoundError(
                    "completion checker setup does not exist: "
                    f"{self._completion_checker_setup}"
                )
        if self._completion_checker is not None:
            self._completion_checker_digest = hashlib.sha256(
                self._completion_checker.read_bytes()
            ).hexdigest()
        if self._completion_checker_setup is not None:
            self._completion_checker_setup_digest = hashlib.sha256(
                self._completion_checker_setup.read_bytes()
            ).hexdigest()
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "foe"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await environment.upload_file(self._foe_binary, REMOTE_BINARY)
        await environment.upload_file(self._credential_file, self._remote_credential)
        checker_setup = ""
        if self._completion_checker is not None:
            await environment.upload_file(
                self._completion_checker,
                REMOTE_COMPLETION_CHECKER,
            )
            checker_setup = f"chmod 755 {shlex.quote(REMOTE_COMPLETION_CHECKER)} && "
        if self._completion_checker_setup is not None:
            await environment.upload_file(
                self._completion_checker_setup,
                REMOTE_COMPLETION_CHECKER_SETUP,
            )
            checker_setup += (
                f"chmod 755 {shlex.quote(REMOTE_COMPLETION_CHECKER_SETUP)} && "
            )
        owner = environment.default_user
        ownership = ""
        if owner is not None:
            ownership = (
                f"chown {shlex.quote(str(owner))} "
                f"{shlex.quote(self._remote_credential)} && "
            )
        credential_mode = "400" if self._credential_mode == "access_only" else "600"
        installed = await self.exec_as_root(
            environment,
            command=(
                f"{ownership}{checker_setup}chmod 755 {shlex.quote(REMOTE_BINARY)} && "
                f"chmod {credential_mode} {shlex.quote(self._remote_credential)} && "
                f"{schema_preflight_command(REMOTE_BINARY)}"
            ),
        )
        if installed.return_code != 0:
            detail = (installed.stderr or installed.stdout or "").strip()
            raise RuntimeError(
                "Foe installation preflight exited with status "
                f"{installed.return_code}: {detail}"
            )
        if self._completion_checker_setup is not None:
            prepared = await self.exec_as_root(
                environment,
                command=(
                    f"/usr/bin/env -i {shlex.quote(REMOTE_COMPLETION_CHECKER_SETUP)}"
                ),
            )
            if prepared.return_code != 0:
                detail = (prepared.stderr or prepared.stdout or "").strip()
                raise RuntimeError(
                    "completion checker setup exited with status "
                    f"{prepared.return_code}: {detail}"
                )

    async def _retain_credential(self, environment: BaseEnvironment) -> None:
        state = self._credential_file
        temporary = state.parent / f".{state.name}.{uuid.uuid4().hex}.tmp"
        try:
            await environment.download_file(self._remote_credential, temporary)
            if self._credential_mode == "access_only":
                self._credential_unchanged = (
                    hashlib.sha256(temporary.read_bytes()).hexdigest()
                    == self._credential_digest
                )
            else:
                replace_credential_state(temporary, state)
        finally:
            temporary.unlink(missing_ok=True)
            await environment.exec(
                command=f"rm -f {shlex.quote(self._remote_credential)}",
                user="root",
            )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError("Foe Harbor runs require --model provider/model")
        if not self.model_name.startswith("openai-codex/"):
            raise ValueError("the retained login credential supports only openai-codex models")

        pwd = await self.exec_as_agent(environment, command="/bin/pwd")
        if pwd.return_code != 0:
            raise RuntimeError(f"task working-directory probe exited with status {pwd.return_code}")
        working_directory = (pwd.stdout or "").strip()
        executable_probe = await self.exec_as_agent(
            environment,
            command=fixed_executable_probe_command(),
            cwd=environment.task_env_config.workdir,
        )
        if executable_probe.return_code != 0:
            raise RuntimeError(
                "fixed executable probe exited with status "
                f"{executable_probe.return_code}"
            )
        environment_facts = describe_container_environment(
            working_directory,
            executable_probe.stdout or "",
        )
        program = build_program(
            instruction,
            self.model_name,
            self._remote_credential,
            working_directory,
            model_calls=self._model_calls,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            seconds=self._seconds,
            reasoning_effort=self._reasoning_effort,
            service_tier=self._service_tier,
            environment_facts=environment_facts,
            completion_checker=(
                REMOTE_COMPLETION_CHECKER
                if self._completion_checker is not None
                else None
            ),
            diagnosis_model_name=self._diagnosis_model,
            diagnosis_reasoning_effort=self._diagnosis_reasoning_effort,
            diagnosis_model_calls=self._diagnosis_model_calls,
            unresolved_diagnosis_reasoning_effort=self._unresolved_diagnosis_reasoning_effort,
            unresolved_diagnosis_model_calls=self._unresolved_diagnosis_model_calls,
            escalation_reasoning_effort=self._escalation_reasoning_effort,
            escalation_model_calls=self._escalation_model_calls,
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        local_program = self.logs_dir / "foe-program.json"
        local_program.write_text(
            json.dumps(program, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        await environment.upload_file(local_program, REMOTE_PROGRAM)

        logs = PurePosixPath(self.environment_logs_dir)
        episode = (logs / "foe-episode").as_posix()
        stdout = (logs / "foe.stdout").as_posix()
        stderr = (logs / "foe.stderr").as_posix()
        exit_code = (logs / "foe-exit-code").as_posix()
        command = (
            "set +e; "
            f"{shlex.quote(REMOTE_BINARY)} --config {shlex.quote(REMOTE_PROGRAM)} "
            f"--headless --log-dir {shlex.quote(episode)} "
            f"> {shlex.quote(stdout)} 2> {shlex.quote(stderr)}; "
            "foe_status=$?; "
            f"printf '%s\\n' \"$foe_status\" > {shlex.quote(exit_code)}; "
            "printf '%s\\n' \"$foe_status\""
        )
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    f"{shlex.quote(REMOTE_BINARY)} plan "
                    f"--config {shlex.quote(REMOTE_PROGRAM)} >/dev/null"
                ),
                cwd=environment.task_env_config.workdir,
            )
            result = await self.exec_as_agent(
                environment,
                command=command,
                cwd=environment.task_env_config.workdir,
            )
            status_line = (result.stdout or "").strip().splitlines()
            self._exit_code = int(status_line[-1]) if status_line else None
        finally:
            try:
                if self._completion_checker is not None:
                    retained_checker = self.logs_dir / "foe-completion-checker.after"
                    await environment.download_file(
                        REMOTE_COMPLETION_CHECKER,
                        retained_checker,
                    )
                    self._observed_completion_checker_digest = hashlib.sha256(
                        retained_checker.read_bytes()
                    ).hexdigest()
            finally:
                await self._retain_credential(environment)

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        episode_dir = self.logs_dir / "foe-episode"
        prices = {self.model_name: self._pricing}
        if self._diagnosis_model is not None and self._diagnosis_pricing is not None:
            prices[self._diagnosis_model] = self._diagnosis_pricing
        summary = read_episode_summary(episode_dir, prices)
        trace_process = subprocess.run(
            [sys.executable, str(self._trace_evaluator), str(episode_dir)],
            text=True,
            capture_output=True,
            check=False,
        )
        trace_path = self.logs_dir / "foe-conformance.json"
        trace_path.write_text(trace_process.stdout, encoding="utf-8")
        (self.logs_dir / "foe-conformance.stderr").write_text(
            trace_process.stderr,
            encoding="utf-8",
        )
        try:
            trace = json.loads(trace_process.stdout)
        except json.JSONDecodeError:
            trace = {}
        if summary["usage_reported"]:
            context.n_input_tokens = summary["input_tokens"]
            context.n_output_tokens = summary["output_tokens"]
            context.n_cache_tokens = summary["cache_read_tokens"]
            context.cost_usd = summary["estimated_cost_usd"]
        metadata = dict(context.metadata or {})
        metadata.update(
            {
                "foe_exit_code": self._exit_code,
                "foe_model_calls": summary["model_calls"],
                "foe_tool_calls": summary["tool_calls"],
                "foe_usage_reported": summary["usage_reported"],
                "foe_unreported_model_calls": summary["unreported_model_calls"],
                "foe_estimated_cost_usd": summary["estimated_cost_usd"],
                "foe_outcome": summary["outcome"],
                "foe_episode_path": "agent/foe-episode",
                "foe_trace_exit_code": trace_process.returncode,
                "foe_trace_conformant": trace.get("valid"),
                "foe_trace_violations": len(trace.get("violations", []))
                if isinstance(trace.get("violations"), list)
                else None,
                "foe_credential_mode": self._credential_mode,
                "foe_credential_unchanged": self._credential_unchanged,
            }
        )
        if self._completion_checker_digest is not None:
            metadata.update(
                {
                    "foe_completion_checker_sha256": self._completion_checker_digest,
                    "foe_completion_checker_observed_sha256": self._observed_completion_checker_digest,
                    "foe_completion_checker_unchanged": (
                        self._observed_completion_checker_digest
                        == self._completion_checker_digest
                    ),
                }
            )
        if self._completion_checker_setup_digest is not None:
            metadata["foe_completion_checker_setup_sha256"] = (
                self._completion_checker_setup_digest
            )
        context.metadata = metadata
