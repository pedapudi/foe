#!/usr/bin/python3
"""Harbor agent that validates one public completion checker without a model."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from run_verifier_controls import checker_control_command


REMOTE_CHECKER = "/tmp/foe-completion-check"
REMOTE_SETUP = "/tmp/foe-completion-check-setup"
REMOTE_ORACLE = "/tmp/foe-completion-oracle"
REMOTE_STATE_CONTROL = "/tmp/foe-completion-state-control"


class VerifierControlAgent(BaseInstalledAgent):
    """Require the checker to reject the initial state and accept an oracle."""

    def __init__(
        self,
        *args: Any,
        checker_file: str,
        setup_file: str | None = None,
        oracle_file: str,
        state_control_file: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._checker = Path(checker_file)
        self._setup = Path(setup_file) if setup_file is not None else None
        self._oracle = Path(oracle_file)
        self._state_control = (
            Path(state_control_file) if state_control_file is not None else None
        )
        files = [("checker", self._checker), ("oracle", self._oracle)]
        if self._setup is not None:
            files.append(("setup", self._setup))
        if self._state_control is not None:
            files.append(("state control", self._state_control))
        for role, path in files:
            if not path.is_file():
                raise FileNotFoundError(f"{role} does not exist: {path}")
        self._report: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "foe-verifier-controls"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await environment.upload_file(self._checker, REMOTE_CHECKER)
        await environment.upload_file(self._oracle, REMOTE_ORACLE)
        remote_paths = [REMOTE_CHECKER, REMOTE_ORACLE]
        if self._setup is not None:
            await environment.upload_file(self._setup, REMOTE_SETUP)
            remote_paths.append(REMOTE_SETUP)
        if self._state_control is not None:
            await environment.upload_file(self._state_control, REMOTE_STATE_CONTROL)
            remote_paths.append(REMOTE_STATE_CONTROL)
        await self.exec_as_root(
            environment,
            command="chmod 755 " + " ".join(map(shlex.quote, remote_paths)),
        )
        if self._setup is not None:
            prepared = await self.exec_as_root(
                environment,
                command=f"/usr/bin/env -i {shlex.quote(REMOTE_SETUP)}",
            )
            if prepared.return_code != 0:
                raise RuntimeError(
                    "completion checker setup failed: "
                    f"{(prepared.stderr or prepared.stdout or '').strip()}"
                )

    async def _check(self, environment: BaseEnvironment) -> tuple[int, list[str], str]:
        result = await self.exec_as_agent(
            environment,
            command=checker_control_command(REMOTE_CHECKER),
            cwd=environment.task_env_config.workdir,
        )
        findings = [line for line in (result.stdout or "").splitlines() if line.strip()]
        return result.return_code, findings, result.stderr or ""

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction, context
        negative_status, negative_findings, negative_stderr = await self._check(environment)
        if negative_status != 0:
            raise RuntimeError(
                "completion checker failed during the negative control: "
                f"exit {negative_status}: {negative_stderr.strip()}"
            )
        if not negative_findings:
            raise RuntimeError("completion checker accepted the untouched task state")

        oracle = await self.exec_as_agent(
            environment,
            command=shlex.quote(REMOTE_ORACLE),
            cwd=environment.task_env_config.workdir,
        )
        if oracle.return_code != 0:
            raise RuntimeError(
                f"completion oracle exited with status {oracle.return_code}: "
                f"{(oracle.stderr or oracle.stdout or '').strip()}"
            )

        oracle_status, oracle_findings, oracle_stderr = await self._check(environment)
        if oracle_status != 0:
            raise RuntimeError(
                "completion checker failed during the oracle control: "
                f"exit {oracle_status}: {oracle_stderr.strip()}"
            )
        if oracle_findings:
            raise RuntimeError(
                "completion checker rejected the oracle state: "
                + "; ".join(oracle_findings)
            )
        state_control_report = None
        if self._state_control is not None:
            state_control = await self.exec_as_agent(
                environment,
                command=(
                    f"{shlex.quote(REMOTE_STATE_CONTROL)} "
                    f"{shlex.quote(REMOTE_CHECKER)}"
                ),
                cwd=environment.task_env_config.workdir,
            )
            if state_control.return_code != 0:
                raise RuntimeError(
                    "completion checker changed the oracle state or blocked "
                    "task-like pushes: "
                    f"{(state_control.stderr or state_control.stdout or '').strip()}"
                )
            state_control_report = {"accepted": True}
        self._report = {
            "negative_control": {
                "accepted": False,
                "findings": negative_findings,
            },
            "oracle_control": {"accepted": True, "findings": []},
            "state_control": state_control_report,
        }
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "foe-verifier-controls.json").write_text(
            json.dumps(self._report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        metadata = dict(context.metadata or {})
        metadata["foe_verifier_controls"] = self._report
        context.metadata = metadata
