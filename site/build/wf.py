# Workflow documents and one recorded run, read from the repository.
#
#  - `coding` is the document the binary carries, crates/cli/src/builtin-coding.json.
#  - `self-improvement` is examples/self-extension/workflow-config.json.
#  - `survey-propose-apply` is the contract recorded in view/fixtures/workflow.jsonl,
#    together with the firings, branches and recovery that log holds.
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def kind_of(spec):
    if "tool" in spec:
        return "tool " + spec["tool"]
    if "model" in spec:
        return "model"
    return "node"


def read_graph(nodes, order):
    """One column of node rows, plus every declared edge: `follows`, the built-in
    `task` source, and each branch label's successors."""
    out_nodes, edges = [], []
    for name in order:
        spec = nodes[name]
        detail = kind_of(spec)
        model = spec.get("model") or {}
        done = model.get("done_when") or {}
        if done.get("verify"):
            detail += " – verify " + done["verify"]
        elif done.get("returns"):
            detail += " – returns"
        out_nodes.append({
            "id": name,
            "kind": detail,
            "terminal": bool(spec.get("terminal")),
            "fires": spec.get("max_fires", 1),
        })
        for src in spec.get("follows", []):
            edges.append({"a": src, "b": name, "label": None})
        for label, succ in sorted((spec.get("branches") or {}).items()):
            if succ:
                for target in succ:
                    edges.append({"a": name, "b": target, "label": label})
            else:
                edges.append({"a": name, "b": None, "label": label})
    # One declared edge may be both a `follows` and a branch successor; the
    # label is the part a reader needs, so the labelled form wins.
    merged = {}
    for e in edges:
        key = (e["a"], e["b"], e["label"] if e["b"] is None else None)
        if key not in merged or (e["label"] and not merged[key]["label"]):
            merged[key] = e
    return out_nodes, list(merged.values())


docs = []

# ---- the document the binary carries -----------------------------------
coding = json.load(open(os.path.join(REPO, "crates/cli/src/builtin-coding.json")))
nodes, edges = read_graph(coding["workflow"]["nodes"],
                          ["implement-task", "assess-task", "repair-task"])
docs.append({
    "id": "builtin:coding",
    "note": "the default document: implements the task, then assesses it, and repairs on findings",
    "nodes": nodes, "edges": edges, "run": [],
})

# ---- foe improving its own read tool -----------------------------------
selfext = json.load(open(os.path.join(REPO, "examples/self-extension/workflow-config.json")))
nodes, edges = read_graph(selfext["workflow"]["nodes"],
                          ["evaluate_read_tool", "improve_read_tool"])
docs.append({
    "id": "self-extension",
    "note": "adds a total_bytes field to foe's own read tool, its regression test and its specification",
    "nodes": nodes, "edges": edges, "run": [],
})

# ---- the recorded run --------------------------------------------------
log = [json.loads(line) for line in open(os.path.join(REPO, "view/fixtures/workflow.jsonl")) if line.strip()]
start = log[0]["data"]
wf = start["contract"]["workflow"]
order = ["manifest", "survey", "propose", "apply", "verify_change", "record_abandonment"]
nodes, edges = read_graph(wf["nodes"], order)

t0 = log[0]["time"]
run, open_fire = [], {}
for e in log:
    d, ty, t = e["data"], e["type"], e["time"] - t0
    if ty == "workflow/node-start":
        open_fire[(d["node"], d["fire"])] = {
            "kind": "fire", "node": d["node"], "fire": d["fire"], "t": t,
            "child": d.get("child_id"), "inputs": d.get("inputs") or [],
        }
    elif ty == "workflow/node-end":
        frame = open_fire.pop((d["node"], d["fire"]), None)
        if frame is None:
            continue
        failed = d.get("failure") or d.get("error")
        frame["ok"] = not failed
        frame["seq"] = e["seq"]
        frame["dur"] = d.get("duration_ms")
        if failed:
            frame["res"] = (d.get("failure") or {}).get("code", "failed")
        else:
            value = d.get("rendered")
            if value in (None, ""):
                value = json.dumps(d.get("value"), ensure_ascii=False)
            one = value.strip().split("\n")[0]
            frame["res"] = one if len(one) <= 68 else one[:68] + "…"
        run.append(frame)
    elif ty == "workflow/branch":
        run.append({"kind": "branch", "node": d["node"], "fire": d["fire"], "t": t,
                    "seq": e["seq"], "label": d["label"],
                    "successors": d.get("successors") or []})
    elif ty == "workflow/recovery":
        run.append({"kind": "recovery", "node": d["node"], "fire": d["fire"], "t": t,
                    "seq": e["seq"], "cause": d["cause"], "action": d["action"],
                    "target": d["target"]})
    elif ty == "episode/end":
        run.append({"kind": "end", "t": t, "seq": e["seq"], "outcome": d["outcome"]["kind"]})
# The log's own order, which is causal: a branch is recorded after the firing
# that chose it and before the firing it opens.
run.sort(key=lambda f: f["seq"])

docs.append({
    "id": start["contract"]["name"],
    "note": "the recorded run: a branch loops the survey, and one recovery re-runs the gate",
    "nodes": nodes, "edges": edges, "run": run,
})

js = json.dumps(docs, separators=(",", ":"), ensure_ascii=False)
open(os.path.join(HERE, "wf.json"), "w").write(js)
sys.stderr.write("%d documents, %d run frames, %d bytes\n" % (len(docs), len(run), len(js)))
