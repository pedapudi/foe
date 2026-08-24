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


REMOTE_CHECKER = "/tmp/foe-completion-check"
REMOTE_ORACLE = "/tmp/foe-completion-oracle"


class VerifierControlAgent(BaseInstalledAgent):
    """Require the checker to reject the initial state and accept an oracle."""

    def __init__(
        self,
        *args: Any,
        checker_file: str,
        oracle_file: str,
        **kwargs: Any,
    ) -> None:
        self._checker = Path(checker_file)
        self._oracle = Path(oracle_file)
        for role, path in (("checker", self._checker), ("oracle", self._oracle)):
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
        await self.exec_as_root(
            environment,
            command=(
                f"chmod 755 {shlex.quote(REMOTE_CHECKER)} "
                f"{shlex.quote(REMOTE_ORACLE)}"
            ),
        )

    async def _check(self, environment: BaseEnvironment) -> tuple[int, list[str], str]:
        result = await self.exec_as_agent(
            environment,
            command=shlex.quote(REMOTE_CHECKER),
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
        self._report = {
            "negative_control": {
                "accepted": False,
                "findings": negative_findings,
            },
            "oracle_control": {"accepted": True, "findings": []},
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
