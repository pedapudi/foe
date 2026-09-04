#!/usr/bin/python3
"""Run one configuration-repair loop against a frozen fixture.

The loop is: a baseline run of the fixture's deliberately broken contract
fails; the operational digest cites the plan warning and the denial
evidence; a repair child returns a corrected contract document, judged
structurally in its own episode and retained in a verified evidence
bundle; foe plan confirms the warning is gone and the resolved
permissions show the granted executable; an unchanged rerun of the task
under the candidate feeds the external evaluator, which alone decides
whether the repair passes.

The host supplies deterministic responses for task episodes. The
--repair-with-file mode also serves a prepared candidate from the host.
The --repair-with-model mode gives the proposal contract a configured
runtime-owned model route.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evaluate
import host_runtime
import prepared_candidate_responses
import task_responses
from operational_digest import digest

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
WARNING_CODE = "external-commands-unavailable"
PROJECT_PLACEHOLDER = "/home/user/project"


class PipelineError(Exception):
    """A step of the loop could not run or contradicted the fixture."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_plan(foe: Path, contract: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(foe), "plan", "--config", str(contract), "--json"],
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise PipelineError(f"foe plan rejected {contract}: {detail}")
    return json.loads(result.stdout)


def run_episode(
    foe: Path,
    contract: Path,
    log_dir: Path,
    responder: host_runtime.Responder | None = None,
) -> int:
    """Run one headless episode and return its outcome status."""
    if responder is not None:
        try:
            status = host_runtime.run(foe, contract, log_dir, responder)
        except (OSError, RuntimeError) as error:
            raise PipelineError(f"the host-owned episode failed: {error}") from error
        if not (log_dir / "episode.jsonl").is_file():
            raise PipelineError("the host-owned episode wrote no log")
        return status
    result = subprocess.run(
        [str(foe), "--config", str(contract), "--log-dir", str(log_dir), "--headless"],
        text=True,
        capture_output=True,
        timeout=1_800,
        check=False,
    )
    if not (log_dir / "episode.jsonl").is_file():
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise PipelineError(f"the episode wrote no log: {detail}")
    return result.returncode


def read_events(log_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (log_dir / "episode.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def event_data(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == kind]


def episode_outcome(events: list[dict[str, Any]]) -> dict[str, Any]:
    ends = event_data(events, "episode/end")
    if not ends:
        raise PipelineError("the episode log has no episode/end event")
    outcome = ends[-1].get("data", {}).get("outcome")
    if not isinstance(outcome, dict):
        raise PipelineError("the episode/end event carries no outcome object")
    return outcome


def materialize_fixture(fixture_dir: Path, output: Path) -> tuple[Path, Path]:
    """Copy the fixture repository and materialize its project path."""
    project = output / "project"
    shutil.copytree(fixture_dir / "repo", project)
    text = (fixture_dir / "contract.json").read_text(encoding="utf-8")
    text = text.replace(PROJECT_PLACEHOLDER, str(project))
    contract = output / "baseline-contract.json"
    contract.write_text(text, encoding="utf-8")
    return contract, project


def plan_warning_codes(plan: dict[str, Any]) -> list[str]:
    return [warning.get("code") for warning in plan.get("warnings", []) if isinstance(warning, dict)]


def granted_execute_paths(plan: dict[str, Any]) -> list[str]:
    """The resolved execute permissions that exist because the contract
    declares them in grants.execute."""
    paths = []
    for row in plan.get("resolved_permissions", []):
        for entry in row.get("permissions", {}).get("execute", []):
            reason = str(entry.get("reason", ""))
            if "grants.execute" in reason and "loader" not in reason:
                paths.append(entry.get("path"))
    return paths


def proposal_contract(
    workspace: Path,
    check_exec: Path,
    model_block: dict[str, Any] | None,
    model_calls: int,
) -> dict[str, Any]:
    """The repair child's execution contract. The task hands it the failure
    evidence; it never names the repair."""
    contract = {
        "version": 4,
        "name": "config-repair-proposal",
        "instructions": {
            "role": (
                "You repair execution-contract configuration defects. Read the retained "
                "failure evidence in the working directory, then call return with the "
                "complete corrected contract document as one JSON object. Change only "
                "what the evidence justifies, and grant nothing the evidence does not "
                "call for."
            )
        },
        "tools": ["read", "grep", "check"],
        "tool_defs": {
            "check": {
                "exec": str(check_exec),
                "description": (
                    "Verifies a candidate contract document. Reads the candidate as JSON "
                    "on standard input and prints one finding per line; no findings means "
                    "the shape is acceptable."
                ),
            }
        },
        "grants": {"read": [str(workspace)]},
        "budget": {"model_calls": model_calls, "seconds": 900},
        "done_when": {"verify": "check", "returns": {"type": "object"}},
        "task": (
            f"The directory {workspace} holds the evidence of a failed run: "
            "broken-contract.json is the execution contract that ran, plan-warnings.json "
            "holds the static configuration warnings foe plan reported for it, and "
            "digest.json is the operational-failure digest of the attempt. Diagnose why "
            "the run failed and return the corrected contract document."
        ),
    }
    if model_block is not None:
        contract["model"] = model_block
    return contract


def prepare_repair(
    args: argparse.Namespace, workspace: Path, replacements: dict[str, str] | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return either a runtime-owned model route or a prepared candidate."""
    if args.repair_with_file:
        prepared = workspace / "prepared-candidate.json"
        text = args.repair_with_file.read_text(encoding="utf-8")
        for placeholder, actual in (replacements or {}).items():
            text = text.replace(placeholder, actual)
        prepared.write_text(text, encoding="utf-8")
        candidate = json.loads(text)
        if not isinstance(candidate, dict):
            raise PipelineError("--repair-with-file must contain one contract document object")
        return None, candidate
    provider, slash, model = args.repair_with_model.partition("/")
    if not slash or not provider or not model:
        raise PipelineError("--repair-with-model takes the form provider/model")
    block: dict[str, Any] = {"provider": provider, "model": model}
    if args.repair_api_key_file:
        block["api_key_file"] = str(args.repair_api_key_file)
    if args.repair_reasoning_effort:
        block["reasoning_effort"] = args.repair_reasoning_effort
    return block, None


def build_bundle(
    args: argparse.Namespace,
    output: Path,
    proposal_episode: Path,
    candidate: dict[str, Any],
    candidate_plan: dict[str, Any],
) -> tuple[Path, dict[str, Any], str]:
    """Assemble, complete, and verify the evidence bundle for the accepted
    candidate; returns the bundle directory, the verified result, and the
    proposal contract fingerprint the record names as predecessor."""
    events = read_events(proposal_episode)
    starts = event_data(events, "episode/start")
    predecessor = starts[0].get("data", {}).get("contract_fingerprint")
    accepted = [
        event
        for event in events
        if event.get("type") == "verification/result"
        and event.get("data", {}).get("status") == "accepted"
        and event.get("data", {}).get("tool") == "check"
    ]
    if not accepted:
        raise PipelineError("the proposal episode records no accepted check verification")
    build = output / "evidence" / "bundle-build"
    shutil.copytree(proposal_episode, build / "episode")
    (build / "candidate.json").write_bytes(canonical_json(candidate))
    (build / "fingerprint-document.json").write_bytes(canonical_json(candidate_plan["fingerprint_document"]))
    artifacts = [
        {
            "path": "candidate.json",
            "sha256": "sha256:" + hashlib.sha256((build / "candidate.json").read_bytes()).hexdigest(),
        }
    ]
    (build / "artifact-manifest.json").write_bytes(canonical_json(artifacts))
    result = subprocess.run(
        [
            str(args.bundle_builder),
            str(build),
            "episode/episode.jsonl",
            "fingerprint-document.json",
            "artifact-manifest.json",
            "episode/episode.jsonl",
            str(accepted[-1]["seq"]),
            predecessor,
        ],
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    address = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", address):
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise PipelineError(f"evidence bundle build failed: {detail}")
    bundle_dir = output / "evidence" / "bundles" / address.removeprefix("sha256:")
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    build.rename(bundle_dir)
    verified = evaluate.verify_bundle(args.bundle_verifier, bundle_dir, predecessor)
    return bundle_dir, verified, predecessor


def run_loop(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True)
    fixture_dir = args.fixture.resolve()
    fixture = evaluate.load_fixture(fixture_dir)
    report: dict[str, Any] = {"fixture": fixture["name"]}

    # Baseline: the broken contract is planned, warned about, and fails.
    baseline = output / "attempt-baseline"
    baseline.mkdir()
    contract_path, project = materialize_fixture(fixture_dir, output)
    replacements = {PROJECT_PLACEHOLDER: str(project)}
    task_responder = functools.partial(task_responses.respond, command=fixture["required_command"])
    shutil.copyfile(contract_path, baseline / "contract.json")
    baseline_plan = run_plan(args.foe, baseline / "contract.json")
    write_json(baseline / "plan.json", baseline_plan)
    if WARNING_CODE not in plan_warning_codes(baseline_plan):
        raise PipelineError(f"the fixture contract does not produce the {WARNING_CODE} warning")
    run_episode(args.foe, baseline / "contract.json", baseline / "episode", task_responder)
    baseline_events = read_events(baseline / "episode")
    baseline_outcome = episode_outcome(baseline_events)
    report["baseline_outcome"] = baseline_outcome
    if baseline_outcome.get("kind") == "completed":
        raise PipelineError("the baseline run completed; the fixture defect did not reproduce")
    baseline_evaluation = evaluate.evaluate_task(fixture, fixture_dir, baseline_events, project)
    write_json(baseline / "evaluation.json", baseline_evaluation)
    if baseline_evaluation["verdict"] == "pass":
        raise PipelineError("the baseline task evaluation passed; the fixture defect did not reproduce")

    # Digest: the declared baseline attempt must yield the warning and
    # denial evidence the diagnosis cites.
    digest_report = digest([baseline])
    write_json(output / "digest.json", digest_report)
    attempt_digest = digest_report["attempts"][0]
    denials = len(attempt_digest["enforced_permission_denials"]) + len(
        attempt_digest["possible_permission_denials"]["rows"]
    )
    report["digest_evidence"] = {
        "configuration_warning_codes": [row["code"] for row in attempt_digest["configuration_warnings"]],
        "enforced_permission_denials": len(attempt_digest["enforced_permission_denials"]),
        "possible_permission_denials": len(attempt_digest["possible_permission_denials"]["rows"]),
    }
    if WARNING_CODE not in report["digest_evidence"]["configuration_warning_codes"] or denials == 0:
        raise PipelineError("the digest lacks the warning or denial evidence the diagnosis needs")

    # The proposal either uses a runtime-owned route or receives a prepared
    # candidate from the host.
    workspace = output / "proposal"
    workspace.mkdir()
    shutil.copyfile(baseline / "contract.json", workspace / "broken-contract.json")
    write_json(workspace / "plan-warnings.json", baseline_plan.get("warnings", []))
    write_json(workspace / "digest.json", digest_report)
    check_exec = workspace / "candidate-check.py"
    shutil.copyfile(SCRIPT_DIR / "candidate_check.py", check_exec)
    check_exec.chmod(0o755)
    observed_check = hashlib.sha256(check_exec.read_bytes()).hexdigest()
    if observed_check != fixture["candidate_check_sha256"]:
        raise PipelineError(
            f"proposal verifier digest {observed_check} differs from the frozen digest "
            f"{fixture['candidate_check_sha256']}"
        )
    repair = output / "attempt-repair"
    repair.mkdir()
    model_calls = 3 if args.repair_with_file else args.repair_model_calls
    model_block, prepared_candidate = prepare_repair(args, workspace, replacements)
    write_json(
        repair / "contract.json",
        proposal_contract(workspace, check_exec, model_block, model_calls),
    )
    write_json(repair / "plan.json", run_plan(args.foe, repair / "contract.json"))
    repair_responder = (
        functools.partial(prepared_candidate_responses.respond, candidate=prepared_candidate)
        if prepared_candidate is not None
        else None
    )
    run_episode(args.foe, repair / "contract.json", repair / "episode", repair_responder)
    proposal_outcome = episode_outcome(read_events(repair / "episode"))
    report["proposal_outcome_kind"] = proposal_outcome.get("kind")
    if proposal_outcome.get("kind") != "completed":
        raise PipelineError(f"the repair child did not return an accepted candidate: {proposal_outcome}")
    candidate = proposal_outcome.get("value")
    if not isinstance(candidate, dict):
        raise PipelineError("the repair child's outcome value is not a contract document object")
    (repair / "candidate.json").write_bytes(canonical_json(candidate))
    candidate_path = output / "candidate-contract.json"
    write_json(candidate_path, candidate)

    # Plan on the candidate: the warning must be gone, and the resolved
    # permissions record what the repair actually grants.
    candidate_plan = run_plan(args.foe, candidate_path)
    report["candidate_warning_codes"] = plan_warning_codes(candidate_plan)
    report["candidate_execute_grants"] = granted_execute_paths(candidate_plan)
    if WARNING_CODE in report["candidate_warning_codes"]:
        raise PipelineError("the candidate contract still produces the configuration warning")

    # Evidence bundle: portable, standalone-verified evidence for the
    # accepted candidate, attesting the exact judged value.
    bundle_dir, bundle_result, predecessor = build_bundle(
        args, output, repair / "episode", candidate, candidate_plan
    )
    report["bundle_address"] = bundle_result.get("bundle_address")
    report["proposal_contract_fingerprint"] = predecessor

    # Rerun: the unchanged task under the candidate contract.
    rerun = output / "attempt-rerun"
    rerun.mkdir()
    shutil.copyfile(candidate_path, rerun / "contract.json")
    write_json(rerun / "plan.json", candidate_plan)
    artifact = project / fixture["artifact"]
    if artifact.exists():
        artifact.unlink()
    run_episode(args.foe, rerun / "contract.json", rerun / "episode", task_responder)
    rerun_events = read_events(rerun / "episode")
    report["rerun_outcome"] = episode_outcome(rerun_events)
    write_json(
        rerun / "evaluation.json", evaluate.evaluate_task(fixture, fixture_dir, rerun_events, project)
    )

    # Final verdict: the external evaluator alone decides.
    final = evaluate.evaluate_candidate(
        fixture,
        fixture_dir,
        json.loads((baseline / "contract.json").read_text(encoding="utf-8")),
        candidate,
        rerun_events,
        project,
        bundle_result,
        (bundle_dir / "candidate.json").read_bytes(),
    )
    write_json(repair / "evaluation.json", final)
    report["verdict"] = final["verdict"]
    report["failed_checks"] = [check["name"] for check in final["checks"] if not check["passed"]]
    write_json(output / "pipeline-report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if final["verdict"] == "pass" else 1


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    answer.add_argument("fixture", type=Path, help="frozen fixture directory")
    answer.add_argument("--output", type=Path, required=True, help="directory the loop creates and writes")
    answer.add_argument("--foe", type=Path, default=REPO_ROOT / "target" / "release" / "foe")
    answer.add_argument(
        "--bundle-builder",
        type=Path,
        default=REPO_ROOT / "target" / "release" / "build-evidence-bundle",
    )
    answer.add_argument(
        "--bundle-verifier",
        type=Path,
        default=REPO_ROOT / "target" / "release" / "verify-evidence-bundle",
    )
    repair_source = answer.add_mutually_exclusive_group(required=True)
    repair_source.add_argument(
        "--repair-with-file",
        type=Path,
        help="a prepared candidate contract document; runs the loop without a model",
    )
    repair_source.add_argument(
        "--repair-with-model",
        help="provider/model route for the repair child",
    )
    answer.add_argument("--repair-api-key-file", type=Path, help="credential file for the repair model route")
    answer.add_argument("--repair-reasoning-effort", help="reasoning effort for the repair model route")
    answer.add_argument("--repair-model-calls", type=int, default=12)
    return answer


def main() -> int:
    args = parser().parse_args()
    try:
        return run_loop(args)
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"configuration-repair loop: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
