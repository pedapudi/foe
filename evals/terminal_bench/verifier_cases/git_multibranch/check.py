#!/usr/bin/python3
"""Exercise the live Git deployment service with public probe content."""

from __future__ import annotations

import ssl
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


MAIN_CONTENT = "foe public completion probe: main\n"
DEV_CONTENT = "foe public completion probe: development\n"


class CheckFailure(Exception):
    """A completion condition that the implementation can repair."""


def run(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        command = Path(arguments[0]).name
        raise CheckFailure(f"{command} failed: {detail}")
    return result


def checkout_branch(client: Path, branch: str) -> None:
    remote = subprocess.run(
        ["/usr/bin/git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        cwd=client,
        text=True,
        capture_output=True,
        check=False,
    )
    if remote.returncode not in (0, 1):
        detail = remote.stderr.strip() or "no diagnostic"
        raise CheckFailure(f"git could not inspect origin/{branch}: {detail}")
    if remote.returncode == 0:
        run(["/usr/bin/git", "checkout", "-q", "-B", branch, f"origin/{branch}"], cwd=client)
    else:
        run(["/usr/bin/git", "checkout", "-q", "--orphan", branch], cwd=client)
        run(["/usr/bin/git", "rm", "-q", "-rf", "--ignore-unmatch", "."], cwd=client)


def push_probe(
    client: Path,
    branch: str,
    content: str,
    environment: dict[str, str],
    *,
    force: bool = False,
) -> float:
    (client / "index.html").write_text(content, encoding="utf-8")
    run(["/usr/bin/git", "add", "index.html"], cwd=client)
    run(
        [
            "/usr/bin/git",
            "-c",
            "user.name=Foe completion checker",
            "-c",
            "user.email=foe-checker@invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            f"public completion probe for {branch}",
        ],
        cwd=client,
    )
    started = time.monotonic()
    arguments = ["/usr/bin/git", "push", "-q"]
    if force:
        arguments.append("--force")
    arguments.extend(["origin", f"HEAD:{branch}"])
    run(
        arguments,
        cwd=client,
        environment=environment,
    )
    return time.monotonic() - started


def fetch(path: str) -> str:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        f"https://localhost:8443{path}",
        headers={"User-Agent": "foe-public-completion-checker"},
    )
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        if response.status != 200:
            raise CheckFailure(f"HTTPS {path} returned status {response.status}")
        return response.read().decode("utf-8")


def check() -> None:
    with tempfile.TemporaryDirectory(prefix="foe-git-multibranch-") as directory:
        temporary = Path(directory)
        askpass = temporary / "askpass"
        askpass.write_text("#!/bin/sh\nprintf '%s\\n' password\n", encoding="utf-8")
        askpass.chmod(0o700)
        environment = {
            "DISPLAY": "foe-completion-checker",
            "GIT_SSH_COMMAND": (
                "/usr/bin/ssh -o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null "
                "-o PreferredAuthentications=password "
                "-o PubkeyAuthentication=no"
            ),
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(temporary),
            "SSH_ASKPASS": str(askpass),
            "SSH_ASKPASS_REQUIRE": "force",
        }
        client = temporary / "client"
        run(
            ["/usr/bin/git", "clone", "-q", "git@localhost:/git/project", str(client)],
            environment=environment,
        )
        checkout_branch(client, "dev")
        dev_seconds = push_probe(client, "dev", DEV_CONTENT, environment)
        run(["/usr/bin/git", "checkout", "-q", "-B", "main", "dev"], cwd=client)
        main_seconds = push_probe(
            client,
            "main",
            MAIN_CONTENT,
            environment,
            force=True,
        )
        if main_seconds >= 3:
            raise CheckFailure(f"main push and deployment took {main_seconds:.2f} seconds")
        if dev_seconds >= 3:
            raise CheckFailure(f"dev push and deployment took {dev_seconds:.2f} seconds")
        if fetch("/index.html") != MAIN_CONTENT:
            raise CheckFailure("the main HTTPS endpoint did not serve the pushed public content")
        if fetch("/dev/index.html") != DEV_CONTENT:
            raise CheckFailure("the dev HTTPS endpoint did not serve the pushed public content")


def main() -> None:
    try:
        check()
    except (CheckFailure, OSError, subprocess.TimeoutExpired) as error:
        detail = str(error).replace("\n", " ").strip()
        print(f"the live Git deployment check failed: {detail}")


if __name__ == "__main__":
    main()
