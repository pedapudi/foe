#!/usr/bin/python3
"""Exercise the live Git deployment service and restore its prior state."""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


MAIN_CONTENT = "foe public completion probe: main\n"
DEV_CONTENT = "foe public completion probe: development\n"
STATE_PATHS = (
    Path("/git/project"),
    Path("/var/www"),
)


class CheckFailure(Exception):
    """A completion condition that the implementation can repair."""


def path_exists(path: Path) -> bool:
    """Include broken symbolic links when determining whether a path exists."""
    return os.path.lexists(path)


def state_manifest(path: Path) -> tuple[tuple[object, ...], ...]:
    """Describe state that a later Git client or HTTPS request can observe."""
    if not path_exists(path):
        return ((".", "absent"),)

    entries: list[tuple[object, ...]] = []

    def visit(entry: Path, relative: str) -> None:
        metadata = entry.lstat()
        common: tuple[object, ...] = (
            relative,
            stat.S_IFMT(metadata.st_mode),
            stat.S_IMODE(metadata.st_mode),
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_mtime_ns,
        )
        if stat.S_ISLNK(metadata.st_mode):
            entries.append(common + ("link", os.readlink(entry)))
            return
        if stat.S_ISREG(metadata.st_mode):
            entries.append(
                common + ("file", hashlib.sha256(entry.read_bytes()).hexdigest())
            )
            return
        entries.append(common + ("directory" if stat.S_ISDIR(metadata.st_mode) else "other",))
        if stat.S_ISDIR(metadata.st_mode):
            for child in sorted(entry.iterdir(), key=lambda item: item.name):
                child_relative = child.name if relative == "." else f"{relative}/{child.name}"
                visit(child, child_relative)

    visit(path, ".")
    return tuple(entries)


class StateSnapshot:
    """Archive the repository and publication trees before a destructive probe."""

    def __init__(self, path: Path, archive: Path) -> None:
        self.path = path
        self.archive = archive
        self.existed = path_exists(path)
        self.manifest = state_manifest(path)
        if self.existed:
            run(
                [
                    "/usr/bin/tar",
                    "--acls",
                    "--xattrs",
                    "--numeric-owner",
                    "--format=posix",
                    "-cpf",
                    str(archive),
                    "-C",
                    str(path.parent),
                    "--",
                    path.name,
                ],
                timeout=30,
            )

    def restore(self) -> None:
        if path_exists(self.path):
            if self.path.is_dir() and not self.path.is_symlink():
                shutil.rmtree(self.path)
            else:
                self.path.unlink()
        if self.existed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            run(
                [
                    "/usr/bin/tar",
                    "--acls",
                    "--xattrs",
                    "--numeric-owner",
                    "--same-owner",
                    "--same-permissions",
                    "-xpf",
                    str(self.archive),
                    "-C",
                    str(self.path.parent),
                ],
                timeout=30,
            )
        if state_manifest(self.path) != self.manifest:
            raise CheckFailure(f"restoring {self.path} did not reproduce its observed state")


class StateGuard:
    """Restore every path after the probe, including when the probe fails."""

    def __init__(self, temporary: Path) -> None:
        self.snapshots = [
            StateSnapshot(path, temporary / f"state-{index}.tar")
            for index, path in enumerate(STATE_PATHS)
        ]

    def restore(self) -> None:
        failures = []
        for snapshot in reversed(self.snapshots):
            try:
                snapshot.restore()
            except (CheckFailure, OSError, subprocess.TimeoutExpired) as error:
                failures.append(str(error).replace("\n", " ").strip())
        if failures:
            raise CheckFailure("; ".join(failures))


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


def check_effective_authentication() -> None:
    effective = run(["/usr/sbin/sshd", "-T"]).stdout.splitlines()
    settings = {
        line.split(maxsplit=1)[0]: line.split(maxsplit=1)[1]
        for line in effective
        if len(line.split(maxsplit=1)) == 2
    }
    if settings.get("passwordauthentication") != "yes":
        raise CheckFailure("the effective SSH configuration disables password authentication")
    if settings.get("kbdinteractiveauthentication") != "no":
        raise CheckFailure(
            "the effective SSH configuration must disable keyboard-interactive authentication"
        )


def check() -> None:
    with tempfile.TemporaryDirectory(prefix="foe-git-multibranch-") as directory:
        temporary = Path(directory)
        state = StateGuard(temporary)
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
        probe_error: BaseException | None = None
        try:
            check_effective_authentication()
            client = temporary / "client"
            run(
                [
                    "/usr/bin/git",
                    "clone",
                    "-q",
                    "git@localhost:/git/project",
                    str(client),
                ],
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
                raise CheckFailure(
                    f"main push and deployment took {main_seconds:.2f} seconds"
                )
            if dev_seconds >= 3:
                raise CheckFailure(
                    f"dev push and deployment took {dev_seconds:.2f} seconds"
                )
            if fetch("/index.html") != MAIN_CONTENT:
                raise CheckFailure(
                    "the main HTTPS endpoint did not serve the pushed public content"
                )
            if fetch("/dev/index.html") != DEV_CONTENT:
                raise CheckFailure(
                    "the dev HTTPS endpoint did not serve the pushed public content"
                )
        except BaseException as error:
            probe_error = error
        try:
            state.restore()
        except BaseException as restore_error:
            if probe_error is None:
                raise
            raise CheckFailure(
                f"{probe_error}; restoring the pre-check state failed: {restore_error}"
            ) from restore_error
        if probe_error is not None:
            raise probe_error


def main() -> None:
    try:
        check()
    except (CheckFailure, OSError, subprocess.TimeoutExpired) as error:
        detail = str(error).replace("\n", " ").strip()
        print(f"the live Git deployment check failed: {detail}")


if __name__ == "__main__":
    main()
