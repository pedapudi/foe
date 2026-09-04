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

from capability_probe_support import build_probe_contract, evaluate_probe_episode
from foe_agent_support import schema_preflight_command


REMOTE_BINARY = "/usr/local/bin/foe"
REMOTE_HOST_DRIVER = "/tmp/foe-host-capability-probe.sh"
REMOTE_PROGRAM = "/tmp/foe-capability-contract.json"


class CapabilityProbeAgent(BaseInstalledAgent):
    """Run deterministic tool calls in a real Terminal-Bench task container."""

    def __init__(
        self,
        *args: Any,
        foe_binary: str,
        host_driver_file: str,
        **kwargs: Any,
    ) -> None:
        self._foe_binary = Path(foe_binary)
        self._host_driver_file = Path(host_driver_file)
        if not self._foe_binary.is_file():
            raise FileNotFoundError(f"Foe binary does not exist: {self._foe_binary}")
        if not self._host_driver_file.is_file():
            raise FileNotFoundError(
                f"capability host driver does not exist: {self._host_driver_file}"
            )
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "foe-capability-probes"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await environment.upload_file(self._foe_binary, REMOTE_BINARY)
        await environment.upload_file(self._host_driver_file, REMOTE_HOST_DRIVER)
        await self.exec_as_root(
            environment,
            command=(
                f"chmod 755 {shlex.quote(REMOTE_BINARY)} "
                f"{shlex.quote(REMOTE_HOST_DRIVER)} && "
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
        contract = build_probe_contract(working_directory)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        local_contract = self.logs_dir / "foe-contract.json"
        local_contract.write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        await environment.upload_file(local_contract, REMOTE_PROGRAM)
        logs = PurePosixPath(self.environment_logs_dir)
        episode = (logs / "foe-episode").as_posix()
        result = await self.exec_as_agent(
            environment,
            command=(
                f"{shlex.quote(REMOTE_HOST_DRIVER)} {shlex.quote(REMOTE_BINARY)} "
                f"{shlex.quote(REMOTE_PROGRAM)} {shlex.quote(episode)}"
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
