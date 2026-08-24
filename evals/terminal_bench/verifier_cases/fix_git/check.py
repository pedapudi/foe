#!/usr/local/bin/python3
"""Check that the lost personal-site commit is reachable from master."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPOSITORY = Path("/app/personal-site")


def git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(REPOSITORY), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> list[str]:
    if not (REPOSITORY / ".git").is_dir():
        return ["/app/personal-site is not a Git repository"]
    findings = []
    branch = git("branch", "--show-current")
    if branch.returncode != 0:
        return [f"git could not inspect the current branch: {branch.stderr.strip()}"]
    if branch.stdout.strip() != "master":
        findings.append("the personal-site repository is not on branch master")
    status = git("status", "--porcelain")
    if status.returncode != 0:
        return [f"git could not inspect the worktree: {status.stderr.strip()}"]
    if status.stdout.strip():
        findings.append("the personal-site worktree has uncommitted changes")

    reflog = git("reflog", "--all", "--format=%H%x09%gs")
    if reflog.returncode != 0:
        return [f"git could not inspect the reflog: {reflog.stderr.strip()}"]
    candidates = [
        line.split("\t", 1)[0]
        for line in reflog.stdout.splitlines()
        if "Move to Stanford" in line and "\t" in line
    ]
    if not candidates:
        findings.append("the reflog contains no commit named 'Move to Stanford'")
        return findings
    reachable = False
    for commit in candidates:
        ancestor = git("merge-base", "--is-ancestor", commit, "master")
        if ancestor.returncode == 0:
            reachable = True
            break
        if ancestor.returncode not in (0, 1):
            return [
                "git could not test whether the recovered commit reaches master: "
                + ancestor.stderr.strip()
            ]
    if not reachable:
        findings.append("the 'Move to Stanford' commit is not reachable from master")
    return findings


if __name__ == "__main__":
    for finding in main():
        print(finding)
