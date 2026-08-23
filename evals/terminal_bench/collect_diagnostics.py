#!/usr/bin/python3
"""Collect identity-bound trajectory diagnoses for Foe self-improvement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent / "harness_bench"))
from foe_source_identity import evaluated_foe, require_evaluated_foe


def collect(
    source_root: Path,
    binary: Path,
    run_dirs: list[Path],
) -> dict[str, Any]:
    if not run_dirs:
        raise ValueError("at least one retained Terminal-Bench run is required")
    identity = evaluated_foe(source_root, binary)
    reports = []
    runs = []
    for run_dir in run_dirs:
        manifest_path = run_dir / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_identity = require_evaluated_foe(
            manifest.get("evaluated_foe"), f"Terminal-Bench manifest {manifest_path}"
        )
        if manifest_identity != identity:
            raise ValueError(
                f"Terminal-Bench manifest {manifest_path} evaluates a different Foe source or binary"
            )
        diagnostic_paths = sorted(run_dir.glob("*/*/agent/foe-diagnostics.json"))
        if not diagnostic_paths:
            raise ValueError(f"Terminal-Bench run has no Foe diagnostics: {run_dir}")
        for path in diagnostic_paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            evidence = report.get("evidence_identity")
            if not isinstance(evidence, dict) or evidence.get("runtime_build") != identity["runtime_binary"]:
                raise ValueError(f"trajectory diagnosis has a different runtime identity: {path}")
            reports.append(report)
        runs.append(
            {
                "dataset": manifest.get("dataset"),
                "label": manifest.get("label"),
                "model": manifest.get("model"),
                "reasoning_effort": manifest.get("reasoning_effort"),
                "token_limits": manifest.get("token_limits"),
                "diagnoses": len(diagnostic_paths),
            }
        )
    return {
        "schema_version": 1,
        "evaluated_foe": identity,
        "runs": runs,
        "trajectory_diagnostics": reports,
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("--source-root", type=Path, required=True)
    answer.add_argument("--foe", type=Path, required=True)
    answer.add_argument("--run-dir", type=Path, action="append", required=True)
    answer.add_argument("--output", type=Path, required=True)
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = collect(
            args.source_root.resolve(strict=True),
            args.foe.resolve(strict=True),
            [path.resolve(strict=True) for path in args.run_dir],
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"collect diagnostics: {error}", file=sys.stderr)
        return 2
    print(f"Self-improvement evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
