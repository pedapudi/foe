#!/usr/bin/python3
"""Harbor agent that runs Foe capability probes without a model provider."""

from __future__ import annotations

import json
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from capability_probe_support import build_probe_program, evaluate_probe_episode
from foe_agent_support import schema_preflight_command


REMOTE_BINARY = "/usr/local/bin/foe"
REMOTE_TRANSPORT = "/tmp/foe-capability-transport.sh"
REMOTE_PROGRAM = "/tmp/foe-capability-program.json"


class CapabilityProbeAgent(BaseInstalledAgent):
    """Run deterministic tool calls in a real Terminal-Bench task container."""

    def __init__(
        self,
        *args: Any,
        foe_binary: str,
        transport_file: str,
        **kwargs: Any,
    ) -> None:
        self._foe_binary = Path(foe_binary)
        self._transport_file = Path(transport_file)
        if not self._foe_binary.is_file():
            raise FileNotFoundError(f"Foe binary does not exist: {self._foe_binary}")
        if not self._transport_file.is_file():
            raise FileNotFoundError(
                f"capability transport does not exist: {self._transport_file}"
            )
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "foe-capability-probes"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await environment.upload_file(self._foe_binary, REMOTE_BINARY)
        await environment.upload_file(self._transport_file, REMOTE_TRANSPORT)
        await self.exec_as_root(
            environment,
            command=(
                f"chmod 755 {shlex.quote(REMOTE_BINARY)} "
                f"{shlex.quote(REMOTE_TRANSPORT)} && "
                f"{schema_preflight_command(REMOTE_BINARY)}"
            ),
        )

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction, context
        pwd = await self.exec_as_agent(environment, command="/bin/pwd")
        working_directory = (pwd.stdout or "").strip()
        program = build_probe_program(
            REMOTE_TRANSPORT,
            working_directory,
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
        result = await self.exec_as_agent(
            environment,
            command=(
                f"{shlex.quote(REMOTE_BINARY)} --config {shlex.quote(REMOTE_PROGRAM)} "
                f"--headless --log-dir {shlex.quote(episode)}"
            ),
            cwd=environment.task_env_config.workdir,
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Foe capability probes exited with status {result.return_code}"
            )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        try:
            report = evaluate_probe_episode(self.logs_dir / "foe-episode")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            metadata = dict(context.metadata or {})
            metadata["foe_capability_probe_error"] = str(error)
            context.metadata = metadata
            return
        (self.logs_dir / "foe-capabilities.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        metadata = dict(context.metadata or {})
        metadata["foe_capabilities"] = report["capabilities"]
        metadata["foe_outcome"] = report["outcome"]
        context.metadata = metadata
