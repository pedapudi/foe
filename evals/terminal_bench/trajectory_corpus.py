#!/usr/bin/python3
"""Snapshot and verify local, content-addressed Terminal-Bench evidence.

The corpus is local-only. It accepts development and capability-search cases,
which prevents protected confirmation and calibration evidence from entering
self-improvement inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

sys.path.append(str(Path(__file__).resolve().parent.parent / "harness_bench"))
from foe_source_identity import evaluated_foe, require_evaluated_foe


SCHEMA_VERSION = 1
KIND = "terminal-bench-trajectory-corpus"
ELIGIBLE_GROUPS = ("development", "capability_search")


def canonical_json(value: Any) -> bytes:
    """Encode one value using the corpus manifest representation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _object_path(corpus_root: Path, digest: str) -> Path:
    return corpus_root / "objects" / "sha256" / digest


def _write_immutable(destination: Path, contents: bytes, label: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if (
            destination.is_symlink()
            or not destination.is_file()
            or destination.read_bytes() != contents
        ):
            raise ValueError(f"{label} already exists with different contents")
    else:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
            staged = Path(temporary.name)
        try:
            try:
                os.link(staged, destination)
            except FileExistsError:
                if destination.read_bytes() != contents:
                    raise ValueError(f"{label} already exists with different contents")
        finally:
            staged.unlink(missing_ok=True)
    destination.chmod(0o444)


def _store_object(corpus_root: Path, contents: bytes) -> dict[str, Any]:
    digest = _digest(contents)
    _write_immutable(
        _object_path(corpus_root, digest),
        contents,
        f"corpus object sha256:{digest}",
    )
    return {"object": f"sha256:{digest}", "bytes": len(contents)}


def _relative_file_paths(run_dir: Path) -> list[Path]:
    paths = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Terminal-Bench run contains a symbolic link: {path}")
        if path.is_file():
            paths.append(path.relative_to(run_dir))
        elif not path.is_dir():
            raise ValueError(f"Terminal-Bench run contains an unsupported file: {path}")
    if not paths:
        raise ValueError(f"Terminal-Bench run contains no files: {run_dir}")
    return paths


def _trial_result_paths(run_dir: Path) -> list[Path]:
    answer = []
    for path in sorted(run_dir.rglob("result.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Terminal-Bench result is invalid JSON: {path}: {error}") from error
        if isinstance(value, dict) and isinstance(value.get("agent_result"), dict):
            answer.append(path)
    if not answer:
        raise ValueError(f"Terminal-Bench run has no trial result metadata: {run_dir}")
    return answer


def _credential_exposed(path: Path) -> bool:
    value = json.loads(path.read_text(encoding="utf-8"))
    agent = value.get("agent_result") if isinstance(value, dict) else None
    metadata = agent.get("metadata") if isinstance(agent, dict) else None
    return isinstance(metadata, dict) and metadata.get("foe_credential_exposed") is True


def _trial_task(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    task = value.get("task_name") if isinstance(value, dict) else None
    if not isinstance(task, str):
        raise ValueError(f"Terminal-Bench trial result has no task name: {path}")
    return task.rsplit("/", 1)[-1]


def _validate_episode_identity(path: Path, runtime_binary: str) -> None:
    starts = []
    with path.open(encoding="utf-8") as lines:
        for line_number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Foe episode log has invalid JSON at {path}:{line_number}: {error}"
                ) from error
            if isinstance(event, dict) and event.get("type") == "episode/start":
                starts.append(event)
    if len(starts) != 1:
        raise ValueError(f"Foe episode log must contain one episode/start event: {path}")
    data = starts[0].get("data")
    runtime = data.get("runtime") if isinstance(data, dict) else None
    if not isinstance(runtime, dict) or runtime.get("build") != runtime_binary:
        raise ValueError(f"Foe episode log has a different runtime identity: {path}")


def _validate_retained_evidence(
    run_dir: Path, tasks: list[str], identity: dict[str, str]
) -> None:
    trial_results = _trial_result_paths(run_dir)
    for path in trial_results:
        if _trial_task(path) not in tasks:
            raise ValueError(f"Terminal-Bench trial result is outside its campaign: {path}")
        if _credential_exposed(path):
            raise ValueError(
                "trajectory corpus refuses a trial whose result reports "
                f"foe_credential_exposed: {path}"
            )
    diagnostics = sorted(run_dir.glob("*/*/agent/foe-diagnostics.json"))
    if not diagnostics:
        raise ValueError(f"Terminal-Bench run has no Foe trajectory diagnostics: {run_dir}")
    for path in diagnostics:
        value = json.loads(path.read_text(encoding="utf-8"))
        evidence = value.get("evidence_identity") if isinstance(value, dict) else None
        if (
            not isinstance(evidence, dict)
            or evidence.get("runtime_build") != identity["runtime_binary"]
        ):
            raise ValueError(f"Foe trajectory diagnostics have a different runtime identity: {path}")
        task = value.get("task")
        if not isinstance(task, str) or task.rsplit("/", 1)[-1] not in tasks:
            raise ValueError(f"Foe trajectory diagnostics are outside their campaign: {path}")
    episodes = sorted(
        path
        for path in run_dir.rglob("episode.jsonl")
        if "foe-episode" in path.parts
    )
    if not episodes:
        raise ValueError(f"Terminal-Bench run has no Foe episode logs: {run_dir}")
    for path in episodes:
        _validate_episode_identity(path, identity["runtime_binary"])


def _case_groups(contents: bytes, context: Path) -> tuple[str, dict[str, str]]:
    value = json.loads(contents)
    dataset = value.get("dataset") if isinstance(value, dict) else None
    groups = value.get("groups") if isinstance(value, dict) else None
    if not isinstance(dataset, str) or not isinstance(groups, dict):
        raise ValueError(f"Terminal-Bench cases file is invalid: {context}")
    membership: dict[str, str] = {}
    for group in ELIGIBLE_GROUPS:
        names = groups.get(group)
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError(f"Terminal-Bench cases file has no {group} group: {context}")
        for name in names:
            if name in membership:
                raise ValueError(f"Terminal-Bench case belongs to two corpus groups: {name}")
            membership[name] = group
    return dataset, membership


def _campaign_tasks(campaign: dict[str, Any], path: Path) -> list[str]:
    rows = campaign.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"Terminal-Bench campaign has no tasks: {path}")
    names = []
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str):
            raise ValueError(f"Terminal-Bench campaign has an invalid task: {path}")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError(f"Terminal-Bench campaign repeats a task: {path}")
    return sorted(names)


def _role(path: Path) -> str:
    parts = path.parts
    if path == Path("campaign.json"):
        return "campaign_manifest"
    if "foe-episode" in parts:
        return "episode"
    if "verifier" in parts or "artifacts" in parts:
        return "verifier_artifact"
    if path.name == "foe-diagnostics.json":
        return "trajectory_diagnostics"
    if path.name in ("config.json", "foe-invocation.json", "foe-program.json"):
        return "adapter_invocation"
    return "adapter_diagnostics"


def _manifest_has_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_manifest_has_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_manifest_has_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    return Path(value).is_absolute() or PurePosixPath(value).is_absolute()


def _validate_reference(reference: Any, context: str) -> tuple[str, int | None]:
    if isinstance(reference, str):
        address, size = reference, None
    elif isinstance(reference, dict):
        address, size = reference.get("object"), reference.get("bytes")
        if type(size) is not int or size < 0:
            raise ValueError(f"{context} has an invalid byte count")
    else:
        raise ValueError(f"{context} has an invalid object reference")
    if not isinstance(address, str) or not address.startswith("sha256:"):
        raise ValueError(f"{context} has an invalid object address")
    digest = address.removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{context} has an invalid object address")
    return digest, size


def _validate_manifest(manifest: Any, context: str) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError(f"{context} is not a JSON object")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("kind") != KIND:
        raise ValueError(f"{context} has an unsupported schema")
    require_evaluated_foe(manifest.get("evaluated_foe"), context)
    _validate_reference(manifest.get("cases"), f"{context} cases")
    if not isinstance(manifest.get("dataset"), str):
        raise ValueError(f"{context} has no dataset")
    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"{context} has no runs")
    if _manifest_has_absolute_path(manifest):
        raise ValueError(f"{context} contains an absolute path")
    seen_runs = set()
    for run_index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ValueError(f"{context} run {run_index} is invalid")
        run_id = run.get("id")
        if (
            not isinstance(run_id, str)
            or len(run_id) != 64
            or any(character not in "0123456789abcdef" for character in run_id)
            or run_id in seen_runs
        ):
            raise ValueError(f"{context} run {run_index} has an invalid id")
        seen_runs.add(run_id)
        tasks = run.get("tasks")
        if not isinstance(tasks, list) or not tasks or not all(
            isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and row.get("group") in ELIGIBLE_GROUPS
            for row in tasks
        ):
            raise ValueError(f"{context} run {run_index} has invalid tasks")
        if tasks != sorted(tasks, key=lambda row: row["name"]) or len(
            {row["name"] for row in tasks}
        ) != len(tasks):
            raise ValueError(f"{context} run {run_index} tasks are not canonical")
        files = run.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"{context} run {run_index} has no files")
        seen_paths = set()
        for file_index, entry in enumerate(files):
            label = f"{context} run {run_index} file {file_index}"
            if not isinstance(entry, dict):
                raise ValueError(f"{label} is invalid")
            path = entry.get("path")
            if (
                not isinstance(path, str)
                or path in seen_paths
                or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts
            ):
                raise ValueError(f"{label} has an invalid path")
            seen_paths.add(path)
            if not isinstance(entry.get("role"), str):
                raise ValueError(f"{label} has no role")
            _validate_reference(entry, label)
        if files != sorted(files, key=lambda entry: entry["path"]):
            raise ValueError(f"{context} run {run_index} files are not canonical")
        expected_id = _digest(canonical_json({"tasks": tasks, "files": files}))
        if run_id != expected_id:
            raise ValueError(f"{context} run {run_index} id does not match its contents")
    if runs != sorted(runs, key=lambda run: run["id"]):
        raise ValueError(f"{context} runs are not canonical")
    return manifest


def snapshot_corpus(
    source_root: Path,
    binary: Path,
    run_dirs: Iterable[Path],
    cases: Path,
    corpus_root: Path,
) -> Path:
    """Snapshot eligible runs and return their canonical corpus manifest."""
    selected = [Path(path).resolve(strict=True) for path in run_dirs]
    if not selected:
        raise ValueError("at least one Terminal-Bench run is required")
    if len(selected) != len(set(selected)):
        raise ValueError("a Terminal-Bench run may be snapshotted only once")
    root = Path(corpus_root).resolve()
    nested = next((run for run in selected if root == run or root.is_relative_to(run)), None)
    if nested is not None:
        raise ValueError(f"trajectory corpus must remain outside its source run: {nested}")
    identity = evaluated_foe(source_root, binary)
    cases_path = Path(cases).resolve(strict=True)
    cases_contents = cases_path.read_bytes()
    dataset, membership = _case_groups(cases_contents, cases_path)
    prepared = []
    for run_dir in selected:
        campaign_path = run_dir / "campaign.json"
        campaign = json.loads(campaign_path.read_bytes())
        if not isinstance(campaign, dict):
            raise ValueError(f"Terminal-Bench campaign is not a JSON object: {campaign_path}")
        campaign_identity = require_evaluated_foe(
            campaign.get("evaluated_foe"), f"Terminal-Bench campaign {campaign_path}"
        )
        if campaign_identity != identity:
            raise ValueError(
                f"Terminal-Bench campaign evaluates a different Foe source or binary: {campaign_path}"
            )
        if campaign.get("dataset") != dataset:
            raise ValueError(f"Terminal-Bench campaign uses a different dataset: {campaign_path}")
        tasks = _campaign_tasks(campaign, campaign_path)
        outside = sorted(set(tasks) - set(membership))
        if outside:
            raise ValueError(
                "trajectory corpus accepts only development or capability-search cases: "
                + ", ".join(outside)
            )
        _validate_retained_evidence(run_dir, tasks, identity)
        prepared.append((run_dir, tasks, _relative_file_paths(run_dir)))

    cases_reference = _store_object(root, cases_contents)
    runs = []
    for run_dir, tasks, relative_paths in prepared:
        entries = []
        for relative in relative_paths:
            contents = (run_dir / relative).read_bytes()
            entry = {
                "path": relative.as_posix(),
                "role": _role(relative),
                **_store_object(root, contents),
            }
            entries.append(entry)
        run_tasks = [
            {"name": name, "group": membership[name]} for name in tasks
        ]
        run_id = _digest(canonical_json({"tasks": run_tasks, "files": entries}))
        runs.append(
            {
                "id": run_id,
                "tasks": run_tasks,
                "files": entries,
            }
        )
    runs.sort(key=lambda run: run["id"])
    if len({run["id"] for run in runs}) != len(runs):
        raise ValueError("two Terminal-Bench runs have the same retained content")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "evaluated_foe": identity,
        "dataset": dataset,
        "cases": cases_reference,
        "runs": runs,
    }
    _validate_manifest(manifest, "trajectory corpus manifest")
    contents = canonical_json(manifest)
    digest = _digest(contents)
    manifest_dir = root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    destination = manifest_dir / f"{digest}.json"
    _write_immutable(destination, contents, f"trajectory corpus manifest {destination}")
    return destination


def load_manifest(path: Path) -> tuple[dict[str, Any], Path]:
    """Load a canonical corpus manifest and return it with the corpus root."""
    candidate = Path(path).resolve(strict=True)
    if candidate.parent.name != "manifests":
        raise ValueError(f"trajectory corpus manifest is outside manifests/: {candidate}")
    contents = candidate.read_bytes()
    expected_name = f"{_digest(contents)}.json"
    if candidate.name != expected_name:
        raise ValueError(f"trajectory corpus manifest name does not match its contents: {candidate}")
    manifest = _validate_manifest(
        json.loads(contents), f"trajectory corpus manifest {candidate}"
    )
    if canonical_json(manifest) != contents:
        raise ValueError(f"trajectory corpus manifest is not canonical: {candidate}")
    return manifest, candidate.parent.parent


def read_object(corpus_root: Path, reference: Any) -> bytes:
    """Read one corpus object after verifying its address and byte count."""
    digest, expected_size = _validate_reference(reference, "trajectory corpus reference")
    path = _object_path(Path(corpus_root), digest)
    if path.is_symlink():
        raise ValueError(f"corpus object sha256:{digest} is a symbolic link")
    contents = path.read_bytes()
    if expected_size is not None and len(contents) != expected_size:
        raise ValueError(f"corpus object sha256:{digest} has an unexpected byte count")
    if _digest(contents) != digest:
        raise ValueError(f"corpus object sha256:{digest} does not match its address")
    return contents


def corpus_run_files(
    manifest: dict[str, Any], corpus_root: Path, run_index: int
) -> dict[str, bytes]:
    """Reconstruct one run's retained files in memory."""
    _validate_manifest(manifest, "trajectory corpus manifest")
    runs = manifest["runs"]
    if not 0 <= run_index < len(runs):
        raise IndexError(f"trajectory corpus run index is out of range: {run_index}")
    return {
        entry["path"]: read_object(corpus_root, entry)
        for entry in runs[run_index]["files"]
    }


def verify_manifest(path: Path) -> dict[str, int]:
    """Verify every object referenced by one manifest."""
    manifest, root = load_manifest(path)
    cases_contents = read_object(root, manifest["cases"])
    dataset, membership = _case_groups(cases_contents, Path("cases.json"))
    if dataset != manifest["dataset"]:
        raise ValueError("trajectory corpus cases object has a different dataset")
    references = {manifest["cases"]["object"]: len(cases_contents)}
    total_bytes = len(cases_contents)
    for run in manifest["runs"]:
        for task in run["tasks"]:
            if membership.get(task["name"]) != task["group"]:
                raise ValueError(
                    "trajectory corpus task differs from its cases object: "
                    f"{task['name']}"
                )
        for entry in run["files"]:
            contents = read_object(root, entry)
            references[entry["object"]] = len(contents)
            total_bytes += len(contents)
    return {
        "runs": len(manifest["runs"]),
        "files": sum(len(run["files"]) for run in manifest["runs"]),
        "objects": len(references),
        "referenced_bytes": total_bytes,
        "stored_bytes": sum(references.values()),
    }


def inspect_manifest(path: Path) -> dict[str, Any]:
    """Return a compact description of one verified corpus manifest."""
    manifest, _ = load_manifest(path)
    verification = verify_manifest(path)
    return {
        "schema_version": manifest["schema_version"],
        "evaluated_foe": manifest["evaluated_foe"],
        "dataset": manifest["dataset"],
        "tasks": sorted(
            {
                task["name"]
                for run in manifest["runs"]
                for task in run["tasks"]
            }
        ),
        **verification,
    }


def parser() -> argparse.ArgumentParser:
    answer = argparse.ArgumentParser(description=__doc__)
    commands = answer.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="store completed run evidence")
    snapshot.add_argument("--source-root", type=Path, required=True)
    snapshot.add_argument("--binary", type=Path, required=True)
    snapshot.add_argument("--cases", type=Path, required=True)
    snapshot.add_argument("--corpus", type=Path, required=True)
    snapshot.add_argument("run", type=Path, nargs="+")
    for command in ("verify", "inspect"):
        subcommand = commands.add_parser(command, help=f"{command} a corpus manifest")
        subcommand.add_argument("manifest", type=Path)
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            print(
                snapshot_corpus(
                    args.source_root,
                    args.binary,
                    args.run,
                    args.cases,
                    args.corpus,
                )
            )
        elif args.command == "verify":
            print(json.dumps(verify_manifest(args.manifest), sort_keys=True))
        else:
            print(json.dumps(inspect_manifest(args.manifest), indent=2, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trajectory corpus: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
