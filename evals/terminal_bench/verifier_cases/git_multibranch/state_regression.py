#!/usr/bin/python3
"""Prove that repeated completion checks preserve an unborn repository."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType


MAIN_CONTENT = "main branch content\n"
DEV_CONTENT = "dev branch content\n"


def load_checker(path: Path) -> ModuleType:
    name = "foe_git_multibranch_checker"
    loader = SourceFileLoader(name, str(path))
    specification = importlib.util.spec_from_loader(
        name,
        loader,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load completion checker: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def assert_unborn_state(checker: ModuleType) -> None:
    repository = Path("/git/project")
    if not repository.is_dir():
        raise RuntimeError("the oracle did not create /git/project")
    head = checker.run(
        ["/usr/bin/git", "--git-dir=/git/project", "symbolic-ref", "HEAD"]
    ).stdout.strip()
    if head != "refs/heads/main":
        raise RuntimeError(f"the repository HEAD targets {head or 'no reference'}")
    refs = checker.run(
        ["/usr/bin/git", "--git-dir=/git/project", "for-each-ref", "--format=%(refname)"]
    ).stdout.splitlines()
    if refs:
        raise RuntimeError("the repository has refs: " + ", ".join(refs))
    published = [
        path
        for path in (Path("/var/www/html/index.html"), Path("/var/www/dev/index.html"))
        if os.path.lexists(path)
    ]
    if published:
        raise RuntimeError(
            "the repository has published files: " + ", ".join(map(str, published))
        )


def manifests(checker: ModuleType) -> tuple[object, ...]:
    return tuple(checker.state_manifest(path) for path in checker.STATE_PATHS)


def checker_findings(path: Path) -> list[str]:
    result = subprocess.run(
        ["/usr/bin/env", "-i", str(path)],
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"completion checker exited with status {result.returncode}: "
            f"{(result.stderr or result.stdout or 'no diagnostic').strip()}"
        )
    return [line for line in result.stdout.splitlines() if line.strip()]


def run_checker(path: Path) -> None:
    findings = checker_findings(path)
    if findings:
        raise RuntimeError("completion checker rejected the oracle: " + "; ".join(findings))


def exercise_failed_probe_restoration(
    checker: ModuleType,
    checker_path: Path,
    temporary: Path,
) -> None:
    oracle_state = checker.StateGuard(temporary)
    error: BaseException | None = None
    try:
        hook = Path("/git/project/hooks/post-receive")
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        failing_state = manifests(checker)
        findings = checker_findings(checker_path)
        if not findings:
            raise RuntimeError("completion checker accepted a hook that publishes nothing")
        if manifests(checker) != failing_state:
            raise RuntimeError("a failed completion check changed the observed state")
    except BaseException as caught:
        error = caught
    try:
        oracle_state.restore()
    except BaseException as restore_error:
        if error is None:
            raise
        raise RuntimeError(
            f"{error}; restoring the failed-probe fixture failed: {restore_error}"
        ) from restore_error
    if error is not None:
        raise error


def task_like_pushes(checker: ModuleType, temporary: Path) -> None:
    askpass = temporary / "askpass"
    askpass.write_text("#!/bin/sh\nprintf '%s\\n' password\n", encoding="utf-8")
    askpass.chmod(0o700)
    environment = {
        "DISPLAY": "foe-verifier-state-control",
        "GIT_SSH_COMMAND": (
            "/usr/bin/ssh -o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            "-o PubkeyAuthentication=no"
        ),
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(temporary),
        "SSH_ASKPASS": str(askpass),
        "SSH_ASKPASS_REQUIRE": "force",
    }
    client = temporary / "task-client"
    checker.run(
        ["/usr/bin/git", "clone", "-q", "git@localhost:/git/project", str(client)],
        environment=environment,
    )
    checker.push_probe(client, "main", MAIN_CONTENT, environment)
    checker.run(["/usr/bin/git", "checkout", "-q", "-b", "dev"], cwd=client)
    checker.push_probe(client, "dev", DEV_CONTENT, environment)
    if checker.fetch("/index.html") != MAIN_CONTENT:
        raise RuntimeError("a task-like main push did not publish its exact content")
    if checker.fetch("/dev/index.html") != DEV_CONTENT:
        raise RuntimeError("a task-like dev push did not publish its exact content")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: state_regression.py CHECKER")
    checker_path = Path(sys.argv[1]).resolve(strict=True)
    checker = load_checker(checker_path)
    assert_unborn_state(checker)
    before = manifests(checker)
    for attempt in range(2):
        run_checker(checker_path)
        assert_unborn_state(checker)
        if manifests(checker) != before:
            raise RuntimeError(
                f"completion check {attempt + 1} changed repository or publication state"
            )

    with tempfile.TemporaryDirectory(prefix="foe-git-state-control-") as directory:
        temporary = Path(directory)
        exercise_failed_probe_restoration(checker, checker_path, temporary)
        assert_unborn_state(checker)
        if manifests(checker) != before:
            raise RuntimeError("the failed-probe fixture changed the oracle state")

        state = checker.StateGuard(temporary)
        error: BaseException | None = None
        try:
            task_like_pushes(checker, temporary)
            populated = manifests(checker)
            for attempt in range(2):
                run_checker(checker_path)
                if manifests(checker) != populated:
                    raise RuntimeError(
                        "completion check "
                        f"{attempt + 1} changed populated repository or publication state"
                    )
        except BaseException as caught:
            error = caught
        try:
            state.restore()
        except BaseException as restore_error:
            if error is None:
                raise
            raise RuntimeError(
                f"{error}; restoring the state-control fixture failed: {restore_error}"
            ) from restore_error
        if error is not None:
            raise error

    assert_unborn_state(checker)
    if manifests(checker) != before:
        raise RuntimeError("task-like push cleanup changed the oracle state")


if __name__ == "__main__":
    main()
