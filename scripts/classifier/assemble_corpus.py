#!/usr/bin/env python3
"""Assemble the labeled-classification corpus from episode logs.

Walks read-only trajectory trees, extracts per-episode typed evidence and
scrubbed free text, asks the shipped `foe` binary for each episode's
structural classification (one implementation of the ruleset, never two),
and writes a manifest, corpus statistics, and assembly metadata into a
corpus directory that lives outside every repository because trajectory
data is private.

Evidence histograms mirror `crates/telemetry/src/extract.rs` (extensions,
command heads, tool names, spawn and workflow counts). The classification
itself is never recomputed here: it is read from `foe telemetry --json`.

Usage:
    assemble_corpus.py --out DIR --foe-bin PATH [--source GLOB ...]
"""

import argparse
import collections
import datetime
import hashlib
import glob as globlib
import json
import os
import subprocess
import sys

# --- Mirrors of crates/telemetry/src/extract.rs -------------------------

PATH_ARGS = ("path", "glob")
DISPATCHERS = {"cargo", "go", "npm", "yarn", "pnpm", "bazel", "git", "dotnet", "mvn", "gradle"}
SEGMENT_BREAKS = str.maketrans({c: "\x00" for c in "&|;\n"})


def extension(path):
    """Lower-cased extension of the last path component, when it names a
    file type: 1-12 ASCII letters after a non-empty stem."""
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return None
    stem, ext = last.rsplit(".", 1)
    if stem and 1 <= len(ext) <= 12 and ext.isascii() and ext.isalpha():
        return ext.lower()
    return None


def _is_assignment(token):
    name, sep, _ = token.partition("=")
    return bool(sep) and bool(name) and all(c.isascii() and (c.isalnum() or c == "_") for c in name)


def command_heads(command):
    """Head token of every segment of a shell command line, with the
    subcommand appended for dispatchers whose subcommand carries meaning."""
    heads = []
    for segment in command.translate(SEGMENT_BREAKS).split("\x00"):
        raw = segment.split()
        first = 0
        while first < len(raw) and _is_assignment(raw[first]):
            first += 1
        tokens = (t.strip("()'\"`{}") for t in raw[first:])
        head = next((t for t in tokens if t), None)
        if head is None:
            continue
        head = head.rsplit("/", 1)[-1].lower()
        if head == "cd" or head.startswith("-"):
            continue
        sub = None
        if head in DISPATCHERS:
            sub = next((t for t in tokens if t and not t.startswith("-")), None)
        heads.append(f"{head} {sub.lower()}" if sub else head)
    return heads


def evidence_from_events(events):
    """Typed evidence histograms, mirroring extract.rs exactly."""
    ev = {
        "extensions": collections.Counter(),
        "heads": collections.Counter(),
        "tools": collections.Counter(),
        "spawns": 0,
        "workflow_nodes": 0,
    }
    for event in events:
        kind, data = event.get("type"), event.get("data") or {}
        if kind == "assistant/message":
            for call in data.get("tool_calls") or []:
                name, args = call.get("name", ""), call.get("args") or {}
                ev["tools"][name] += 1
                if name == "bash":
                    for head in command_heads(args.get("command") or ""):
                        ev["heads"][head] += 1
                else:
                    for key in PATH_ARGS:
                        value = args.get(key)
                        if isinstance(value, str) and (ext := extension(value)):
                            ev["extensions"][ext] += 1
        elif kind == "spawn/start":
            ev["spawns"] += 1
        elif kind == "workflow/node-start":
            ev["workflow_nodes"] += 1
    ev["extensions"] = dict(sorted(ev["extensions"].items()))
    ev["heads"] = dict(sorted(ev["heads"].items()))
    ev["tools"] = dict(sorted(ev["tools"].items()))
    return ev


# --- Reading one log ----------------------------------------------------


def parse_layout(path):
    """Source tree, campaign, task, run, and child-episode id from the
    job directory layout, or minimal fields for a bare `.foe` log."""
    parts = path.split("/")
    row = {"source_tree": None, "campaign": None, "task": None, "run": None, "child_episode": None}
    if "terminal-bench-jobs" in parts:
        jobs = parts.index("terminal-bench-jobs")
        row["source_tree"] = parts[jobs - 2]
        agent = parts.index("agent")
        # .../terminal-bench-jobs/<campaign...>/<task>/<task__run>/agent/...
        row["run"] = parts[agent - 1]
        row["task"] = parts[agent - 2]
        row["campaign"] = "/".join(parts[jobs + 1 : agent - 2])
        if "children" in parts:
            row["child_episode"] = parts[parts.index("children") + 1]
    elif ".foe" in parts:
        row["source_tree"] = parts[parts.index(".foe") - 1] + "/.foe"
    return row


def read_emission(foe_bin, path):
    """Classification, scrubbed subjects, and scrubbed outcome fields from
    the shipped binary's telemetry preview."""
    out = subprocess.run(
        [foe_bin, "telemetry", path, "--json"], capture_output=True, text=True, check=True
    )
    doc = json.loads(out.stdout)
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]

    def attrs(span):
        flat = {}
        for a in span["attributes"]:
            v = a["value"]
            flat[a["key"]] = next(iter(v.values()))
        return flat

    episode = attrs(next(s for s in spans if s["name"] == "episode"))
    subjects = [
        attrs(s).get("foe.tool.subject", "")
        for s in spans
        if s["name"].startswith("tool ")
    ]
    votes = [v["stringValue"] for v in episode["foe.evidence"]["values"]]
    counts = dict(
        v["stringValue"].rsplit("=", 1) for v in episode["foe.category.counts"]["values"]
    )
    return {
        "outcome": {
            "kind": episode["foe.outcome.kind"],
            "exit_class": episode["foe.outcome.exit_class"],
        },
        "outcome_detail": episode["foe.outcome.detail"],
        "subjects": subjects,
        "v1": {
            "bucket": episode["foe.category"],
            "top_level": episode["foe.category.top_level"],
            "counts": {k: int(n) for k, n in counts.items()},
            "votes": votes,
        },
        "model": episode.get("foe.model.model", ""),
        "provider": episode.get("foe.model.provider", ""),
        "episode_id": episode.get("foe.episode.id", ""),
    }


def read_log(foe_bin, path):
    raw = open(path, "rb").read()
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    task = ""
    for event in events:
        if event.get("type") == "episode/start":
            task = (event.get("data") or {}).get("task") or ""
            break
    row = {"path": path, **parse_layout(path)}
    row.update(read_emission(foe_bin, path))
    row["task_text"] = task
    row["evidence"] = evidence_from_events(events)
    row["content_hash"] = hashlib.sha256(raw).hexdigest()
    return row


# --- Corpus outputs -----------------------------------------------------

def stats_markdown(rows, dropped):
    def table(title, key):
        counts = collections.Counter(key(r) for r in rows)
        lines = [f"## {title}", "", "| value | episodes |", "| --- | --- |"]
        lines += [f"| {v} | {n} |" for v, n in counts.most_common()]
        return "\n".join(lines) + "\n"

    total = len(rows)
    tb = sum(1 for r in rows if r["campaign"] is not None)
    return "\n".join(
        [
            "# Corpus statistics",
            "",
            f"{total} episodes after dropping {dropped} exact duplicates "
            "(identical file content).",
            "",
            table("By source tree", lambda r: r["source_tree"]),
            table("By task", lambda r: r["task"] or "(no benchmark task name)"),
            table("By outcome kind", lambda r: r["outcome"]["kind"]),
            table("By structural-classifier bucket (taxonomy 1, ruleset 1)", lambda r: r["v1"]["bucket"]),
            "## Skew",
            "",
            f"{tb} of {total} episodes come from terminal-bench jobs: every one is a",
            "programming task run in a benchmark harness, most against a handful of",
            "task definitions repeated across campaigns. The remainder are local",
            "repository-description episodes. Almost nothing here exercises the",
            "non-programming reaches of the taxonomy (translation, legal, finance,",
            "health, and the rest), so an evaluation on this corpus can show how the",
            "classifier behaves on programming-shaped work — subcategory choice,",
            "unclassified rate, confusion between programming subcategories — and",
            "cannot say anything about precision or recall on categories the corpus",
            "never contains.",
            "",
        ]
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="corpus directory (outside any repository)")
    ap.add_argument("--foe-bin", required=True, help="path to the built foe binary")
    ap.add_argument("--source", action="append", required=True, help="episode.jsonl glob (repeatable)")
    args = ap.parse_args()

    sources = args.source
    paths = sorted({p for g in sources for p in globlib.glob(g, recursive=True)})
    if not paths:
        sys.exit("no episode logs matched the source globs")

    rows, dropped, seen = [], 0, {}
    for path in paths:
        row = read_log(args.foe_bin, path)
        if row["content_hash"] in seen:
            dropped += 1
            continue
        seen[row["content_hash"]] = path
        rows.append(row)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "manifest.jsonl"), "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, "corpus-stats.md"), "w") as f:
        f.write(stats_markdown(rows, dropped))
    metadata = {
        "assembled": datetime.date.today().isoformat(),
        "source_globs": sources,
        "logs_found": len(paths),
        "episodes": len(rows),
        "duplicates_dropped": dropped,
    }
    with open(os.path.join(args.out, "metadata.json"), "w") as f:
        f.write(json.dumps(metadata, indent=2) + "\n")
    print(f"{len(rows)} episodes ({dropped} duplicates dropped) -> {args.out}")


if __name__ == "__main__":
    main()
