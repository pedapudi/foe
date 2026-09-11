#!/usr/bin/python3
"""Measure direct scanning across distinct queries, concurrent episodes, and edits."""

import argparse
import asyncio
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import grep_cost_curve as curve

sys.path.insert(0, str(curve.REPOSITORY_ROOT / "python"))
import foe


async def batch(binary: Path, root: Path, destination: Path, plans: list[list[dict]], before=None) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    pids: set[int] = set()
    peak = {"rss_kib": 0, "threads": 0, "fds": 0}
    sampling = True

    async def sample():
        while sampling:
            total = {key: 0 for key in peak}
            for pid in list(pids):
                try:
                    lines = (Path("/proc") / str(pid) / "status").read_text().splitlines()
                    fields = dict(line.split(":", 1) for line in lines)
                    total["rss_kib"] += int(fields.get("VmRSS", "0").split()[0])
                    total["threads"] += int(fields.get("Threads", "0"))
                    try:
                        count = len(list((Path("/proc") / str(pid) / "fd").iterdir()))
                        if total["fds"] is not None:
                            total["fds"] += count
                    except PermissionError:
                        total["fds"] = None
                except (FileNotFoundError, ProcessLookupError):
                    pass
            for key, value in total.items():
                peak[key] = None if value is None or peak[key] is None else max(peak[key], value)
            await asyncio.sleep(0.005)

    async def episode(index, plan):
        cursor = 0
        async def model(request):
            nonlocal cursor
            if cursor == len(plan):
                chunks = [{"kind": "text", "delta": "done"}, {"kind": "done", "stop": "end", "usage": {"input": 0, "output": 0, "cache_read": 0}}]
            else:
                if before:
                    before(cursor)
                name = f"search_{cursor}"
                args = plan[cursor]
                chunks = [{"kind": "tool_call_start", "id": name, "name": "grep"},
                          {"kind": "tool_call_delta", "id": name, "delta": json.dumps(args)},
                          {"kind": "tool_call_end", "id": name},
                          {"kind": "done", "stop": "tool", "usage": {"input": 0, "output": 0, "cache_read": 0}}]
                cursor += 1
            for chunk in chunks:
                yield chunk
        config = curve.config_for(f"scan-{index}", root, len(plan))
        handle = await foe.start_config(config, binary=binary, log_dir=destination / str(index), model_backend=model,
                                       on_spawn=lambda handle: pids.add(handle.pid))
        outcome = await handle.wait()
        pids.discard(handle.pid)
        assert isinstance(outcome, foe.Completed), outcome
        assert handle.log_dir is not None
        results = curve.observed(handle.log_dir)
        assert all(not result["is_error"] for result in results.values()), results
        return {"log": str(handle.log_dir), "queries": plan, "results": results}

    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    sampler = asyncio.create_task(sample())
    try:
        episodes = await asyncio.gather(*(episode(index, plan) for index, plan in enumerate(plans)))
    finally:
        sampling = False
        await sampler
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    report = {"episodes": episodes, "wall_ms": (time.perf_counter() - started) * 1000,
            "child_cpu_seconds": after.ru_utime + after.ru_stime - usage_before.ru_utime - usage_before.ru_stime,
            "sampled_aggregate_peak": peak, "sampling_interval_ms": 5}
    (destination / "observations.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


async def measure(binary: Path, root: Path, destination: Path) -> dict:
    cases = {case["name"]: case["args"] for case in curve.GENERATED_CASES}
    mixed = [cases[name] for name in curve.SEQUENCE] * 5
    distinct = [{"pattern": f"absent_distinct_marker_{index}", "literal": True} for index in range(100)]
    runs = {}
    for name, plan in (("mixed", mixed), ("distinct", distinct)):
        for count in (1, 2, 5, 20, 100):
            runs[f"{name}-{count}"] = await batch(binary, root, destination / f"{name}-{count}", [plan[:count]])
        runs[f"{name}-concurrent"] = await batch(binary, root, destination / f"{name}-concurrent", [plan, plan])
    changed = root / "mutation.txt"
    ignored = root / ".gitignore"
    assert not changed.exists() and not ignored.exists()
    history = []
    def edit(step):
        edits = {1: (changed, "changed_content_marker\n"), 2: (changed, "replacement\n"),
                 3: (changed, "changed_content_marker\n"), 4: (ignored, "mutation.txt\n"), 5: (ignored, "")}
        if step in edits:
            path, value = edits[step]
            previous = path.read_text() if path.exists() else None
            history.append({"step": step, "path": path.name, "before": previous, "after": value})
            path.write_text(value)
    mutations = await batch(
        binary, root, destination / "edits-and-ignore", [[{"pattern": "changed_content_marker", "literal": True}] * 6], edit)
    observed = [item["matches"] for item in mutations["episodes"][0]["results"].values()]
    assert observed == [0, 1, 0, 1, 0, 1], observed
    return {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "corpus": str(root),
            "cache_condition": "operating-system cache retained across batches; no index",
            "resource_scope": "episode processes; host sampler excluded; sampled peaks can miss shorter spikes; null means inspection was denied",
            "runs": runs, "mutations": mutations, "mutation_history": history}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foe", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True, help="generated corpus from grep_cost_curve.py")
    parser.add_argument("--keep", type=Path, required=True, help="unused directory for logs and observations")
    args = parser.parse_args()
    args.keep.mkdir(parents=True, exist_ok=False)
    report = asyncio.run(measure(args.foe.resolve(), args.corpus.resolve(), args.keep.resolve()))
    (args.keep / "observations.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: {key: value for key, value in run.items() if key != "episodes"} for name, run in report["runs"].items()}, indent=2))
