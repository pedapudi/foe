import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "view/fixtures")
RUNS = {
    "run": [("overlap-parent.jsonl", 0), ("overlap-child.jsonl", 1)],
    "run-wf": [("workflow.jsonl", 0), ("workflow-propose-1.jsonl", 1),
               ("workflow-propose-2.jsonl", 2), ("workflow-apply-1.jsonl", 3)],
}
NAME = os.environ.get("FOE_RUN", "run")
FILES = RUNS[NAME]
RAW_CAP = 240

def trim(ty, data):
    d = dict(data)
    if ty == "episode/start":
        c = d.get("contract", {})
        return {"id": d["id"], "parent_id": d.get("parent_id"), "name": c.get("name"),
                "tools": c.get("tools"), "budget": c.get("budget"), "task": d.get("task"),
                "sandbox_mode": d.get("sandbox", {}).get("mode"),
                "landlock_abi": d.get("sandbox", {}).get("landlock_abi")}
    if ty == "inbox/item":
        return {"source": d.get("source"), "text": "".join(p.get("text", "") for p in d.get("content", []))}
    if ty == "request/header":
        return {"reason": d.get("reason"), "system": d.get("system"),
                "tools": [t["name"] for t in d.get("tools", [])], "model": d.get("model")}
    if ty == "model/request":
        return {"step": d.get("step"), "attempt": d.get("attempt"), "request_id": d.get("request_id")}
    if ty == "assistant/message":
        return {"step": d.get("step"), "text": d.get("text"),
                "tool_calls": [{"id": t["id"], "name": t["name"], "args": t.get("args")} for t in d.get("tool_calls", [])],
                "stop": d.get("stop"), "usage": d.get("usage")}
    if ty == "tool/result":
        return {"step": d.get("step"), "call_id": d.get("call_id"), "name": d.get("name"),
                "rendered": d.get("rendered"), "is_error": d.get("is_error"),
                "subject": d.get("subject"), "duration_ms": d.get("duration_ms")}
    return d

out = []
for fname, ep in FILES:
    for line in open(os.path.join(SRC, fname)):
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        raw, cut = (line, False) if len(line) <= RAW_CAP else (line[:RAW_CAP], True)
        rec = {"s": o["seq"], "ep": ep, "t": o["time"], "ty": o["type"],
               "d": trim(o["type"], o["data"]), "raw": raw}
        if cut:
            rec["cut"] = 1
        out.append(rec)

t0 = min(r["t"] for r in out)
for r in out:
    r["t"] -= t0
out.sort(key=lambda r: (r["t"], r["ep"], r["s"]))
# The runtime writes a middle dot into some tool subjects; the page uses an
# en dash throughout, so the recorded text follows the page.
js = json.dumps(out, separators=(",", ":"), ensure_ascii=False).replace("\u00b7", "\u2013")
open(os.path.join(HERE, NAME + ".json"), "w").write(js)
sys.stderr.write("%d events, %d bytes\n" % (len(out), len(js)))
