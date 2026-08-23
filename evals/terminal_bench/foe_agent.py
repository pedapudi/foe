#!/usr/bin/python3
"""Harbor installed-agent adapter for a locally built Foe binary."""

from __future__ import annotations

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

from foe_agent_support import build_program, read_episode_summary, replace_credential_state


REMOTE_BINARY = "/usr/local/bin/foe"
REMOTE_CREDENTIAL = "/tmp/foe-openai-codex.json"
REMOTE_PROGRAM = "/tmp/foe-terminal-bench-program.json"


class FoeAgent(BaseInstalledAgent):
    """Run Foe inside a Harbor task container and retain its native episode."""

    def __init__(
        self,
        *args: Any,
        foe_binary: str,
        credential_file: str,
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
        **kwargs: Any,
    ) -> None:
        self._foe_binary = Path(foe_binary)
        self._credential_file = Path(credential_file)
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
        self._exit_code: int | None = None
        if not self._foe_binary.is_file():
            raise FileNotFoundError(f"Foe binary does not exist: {self._foe_binary}")
        if not self._credential_file.is_file():
            raise FileNotFoundError(
                f"Foe credential state does not exist: {self._credential_file}"
            )
        if not self._trace_evaluator.is_file():
            raise FileNotFoundError(
                f"Foe trace evaluator does not exist: {self._trace_evaluator}"
            )
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "foe"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await environment.upload_file(self._foe_binary, REMOTE_BINARY)
        await environment.upload_file(self._credential_file, REMOTE_CREDENTIAL)
        owner = environment.default_user
        ownership = ""
        if owner is not None:
            ownership = f"chown {shlex.quote(str(owner))} {shlex.quote(REMOTE_CREDENTIAL)} && "
        await self.exec_as_root(
            environment,
            command=(
                f"{ownership}chmod 755 {shlex.quote(REMOTE_BINARY)} && "
                f"chmod 600 {shlex.quote(REMOTE_CREDENTIAL)} && "
                f"{shlex.quote(REMOTE_BINARY)} schema >/dev/null"
            ),
        )

    async def _retain_credential(self, environment: BaseEnvironment) -> None:
        state = self._credential_file
        temporary = state.parent / f".{state.name}.{uuid.uuid4().hex}.tmp"
        try:
            await environment.download_file(REMOTE_CREDENTIAL, temporary)
            replace_credential_state(temporary, state)
        finally:
            temporary.unlink(missing_ok=True)
            await environment.exec(
                command=f"rm -f {shlex.quote(REMOTE_CREDENTIAL)}",
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
        working_directory = (pwd.stdout or "").strip()
        program = build_program(
            instruction,
            self.model_name,
            REMOTE_CREDENTIAL,
            working_directory,
            model_calls=self._model_calls,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            seconds=self._seconds,
            reasoning_effort=self._reasoning_effort,
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
            result = await self.exec_as_agent(
                environment,
                command=command,
                cwd=environment.task_env_config.workdir,
            )
            status_line = (result.stdout or "").strip().splitlines()
            self._exit_code = int(status_line[-1]) if status_line else None
        finally:
            await self._retain_credential(environment)

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        episode_dir = self.logs_dir / "foe-episode"
        summary = read_episode_summary(episode_dir, self._pricing)
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
                "foe_estimated_cost_usd": summary["estimated_cost_usd"],
                "foe_outcome": summary["outcome"],
                "foe_episode_path": "agent/foe-episode",
                "foe_trace_exit_code": trace_process.returncode,
                "foe_trace_conformant": trace.get("valid"),
                "foe_trace_violations": len(trace.get("violations", []))
                if isinstance(trace.get("violations"), list)
                else None,
            }
        )
        context.metadata = metadata
