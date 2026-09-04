#!/usr/bin/python3
"""Run deterministic Foe capability probes in one Terminal-Bench container."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from capability_probe_support import evaluate_probe_episode
from run import HARBOR_VERSION, default_harbor, digest, read_cases


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--source-root", type=Path, required=True)
    answer.add_argument("--agent-module", type=Path, required=True)
    answer.add_argument("--host-driver", type=Path, required=True)
    answer.add_argument("--cases", type=Path, required=True)
    answer.add_argument("--task", default="fix-git")
    answer.add_argument("--harbor", type=Path, default=default_harbor())
    answer.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path("target/terminal-bench-capability-probes"),
    )
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        foe = args.foe.resolve(strict=True)
        workspace = args.source_root.resolve(strict=True).parent
        agent_module = args.agent_module.resolve(strict=True)
        host_driver = args.host_driver.resolve(strict=True)
        harbor = args.harbor.resolve(strict=True)
        dataset, _, tasks, _ = read_cases(args.cases.resolve(strict=True))
        if args.task not in tasks:
            raise ValueError(f"unknown capability-probe task: {args.task}")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"capability probes: {error}", file=sys.stderr)
        return 2

    version = subprocess.run(
        [str(harbor), "--version"], text=True, capture_output=True, check=False
    )
    observed_version = (version.stdout or version.stderr).strip()
    if version.returncode != 0 or HARBOR_VERSION not in observed_version:
        print(
            f"capability probes: expected Harbor {HARBOR_VERSION}; observed "
            f"{observed_version or 'no version'}",
            file=sys.stderr,
        )
        return 2
    if subprocess.run(
        ["/usr/bin/docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode:
        print("capability probes: Docker is unavailable to this shell", file=sys.stderr)
        return 2

    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    jobs_root = (
        (workspace / args.jobs_dir).resolve()
        if not args.jobs_dir.is_absolute()
        else args.jobs_dir.resolve()
    )
    jobs_dir = jobs_root / f"{args.task}-{timestamp}"
    command = [
        "/usr/bin/env",
        f"PYTHONPATH={agent_module.parent}",
        str(harbor),
        "run",
        "--dataset",
        dataset,
        "--include-task-name",
        f"terminal-bench/{args.task}",
        "--agent",
        f"{agent_module.stem}:CapabilityProbeAgent",
        "--model",
        "exec/capability-probes",
        "--n-concurrent",
        "1",
        "--n-attempts",
        "1",
        "--jobs-dir",
        str(jobs_dir),
        "--job-name",
        "capability-probes",
        "--disable-verification",
        "--yes",
        "--agent-kwarg",
        f"foe_binary={foe}",
        "--agent-kwarg",
        f"host_driver_file={host_driver}",
        "--agent-kwarg",
        f"version=sha256:{digest(foe)}",
    ]
    result = subprocess.run(command, cwd=agent_module.parent, check=False)
    episodes = sorted(jobs_dir.glob("capability-probes/*/agent/foe-episode"))
    if result.returncode != 0 or len(episodes) != 1:
        print(f"capability probes: Harbor evidence is incomplete at {jobs_dir}", file=sys.stderr)
        return 1
    report = evaluate_probe_episode(episodes[0])
    output = jobs_dir / "capability-report.json"
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["capabilities"], indent=2, sort_keys=True))
    print(f"Capability evidence: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
