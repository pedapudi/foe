# A port of crates/view/src/terminal.rs: lanes, connector cells, blocks, rows.
# Wrapping is left to the page, which knows its own column count.
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "view/fixtures")
RUNS = {
    "term": [("overlap-parent.jsonl", "ep_over_parent"),
             ("overlap-child.jsonl", "ep_over_child")],
    "term-wf": [("workflow.jsonl", "ep_c5785a1e"),
                ("workflow-propose-1.jsonl", "ep_8936e375"),
                ("workflow-propose-2.jsonl", "ep_b0f26af9"),
                ("workflow-apply-1.jsonl", "ep_d82415c7")],
}
NAME = os.environ.get("FOE_RUN", "term")
FILES = RUNS[NAME]

# What separates the fields of a heading, read out of the display this file
# ports rather than restated here. The two carried different characters for a
# while, and nothing failed, because a hand-written port is free to drift.
SOURCE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "crates/view/src/terminal.rs")
FIELD = re.search(r'const FIELD: &str = "(.*)";', open(SOURCE).read())
assert FIELD, "crates/view/src/terminal.rs no longer names FIELD"
FIELD = FIELD.group(1)

def load():
    ev = []
    for fname, eid in FILES:
        for line in open(os.path.join(SRC, fname)):
            line = line.strip()
            if line:
                o = json.loads(line); o["_ep"] = eid; ev.append(o)
    t0 = min(e["time"] for e in ev)
    for e in ev:
        e["time"] -= t0
    ev.sort(key=lambda e: (e["time"], e["_ep"], e["seq"]))
    return ev

def clean(t): return "".join(c for c in t if c in "\n\t" or ord(c) >= 32)
def title(key):
    s = key.replace("_", " ");  return s[:1].upper() + s[1:]
def empty(v): return v is None or v == "" or v == [] or v == {}
def scalar(v): return v if isinstance(v, str) else json.dumps(v)

def lines_of(key, value, indent):
    pad = " " * indent
    if isinstance(value, list):
        out = []
        for item in value: out += bullet(key, item, indent)
        return out
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if empty(v): continue
            if isinstance(v, (list, dict)):
                out.append("%s%s:" % (pad, k)); out += lines_of(k, v, indent + 2)
            else:
                out.append("%s%s: %s" % (pad, k, scalar(v)))
        return out
    return [pad + ln for ln in scalar(value).split("\n")]

def bullet(key, item, indent):
    pad = " " * indent
    if isinstance(item, dict) and key == "learned" and "claim" in item and "seq" in item:
        body = ["%s (seq %s)" % (scalar(item["claim"]), item["seq"])]
    elif isinstance(item, dict):
        sc = [(k, v) for k, v in item.items() if not empty(v) and not isinstance(v, (list, dict))]
        ne = [(k, v) for k, v in item.items() if not empty(v) and isinstance(v, (list, dict))]
        body = [", ".join("%s: %s" % (k, scalar(v)) for k, v in sc)]
        for k, v in ne:
            body.append("%s:" % k); body += lines_of(k, v, 2)
    else:
        body = lines_of(key, item, 0)
    return ["%s%s %s" % (pad, "-" if i == 0 else " ", ln) for i, ln in enumerate(body)]

def display_value(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)): return display_value(parsed)
        except Exception:
            pass
    if not isinstance(value, dict):
        return [("t", ln) for ln in lines_of("", value, 0)]
    summary = value.get("summary") if isinstance(value.get("summary"), str) else None
    rows = [("t", summary)] if summary else []
    for k, v in value.items():
        if (k == "summary" and summary is not None) or empty(v): continue
        if rows: rows.append(("t", ""))
        rows.append(("T", title(k)))
        rows += [("t", ln) for ln in lines_of(k, v, 0)]
    return rows

def identity(name):
    """The colour an agent's name hashes to: FNV-1a over the name's UTF-16
    code units, reduced to one of eight. crates/view/src/terminal.rs and
    view/src/identity.ts write the same function, so one role keeps one
    colour in the terminal, the viewer, and this page."""
    h = 0x811C9DC5
    for ch in name:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return h % 8


def result_text(o):
    k = o["kind"]
    if k == "completed": return "Completed", display_value(o["value"])
    if k == "blocked": return "Blocked", [("t", "%s: %s" % (o["code"], o["message"]))]
    if k == "exhausted": return "Exhausted", [("t", "Budget exhausted: %s" % o["limit"])]
    return "Failed", [("t", o["error"])]

class Term:
    def __init__(self):
        self.lanes = []; self.active = ""; self.calls = 0; self.out = []

    def lane(self, i):
        for k, (key, _, _) in enumerate(self.lanes):
            if key == i: return k
        self.lanes.append([i, i, 0])
        self.rename(len(self.lanes) - 1, i)
        return len(self.lanes) - 1

    def rename(self, i, name):
        """Names lane i and gives the name a colour: the one it hashes to, or
        the next free one when another open lane already holds that, so two
        lanes on screen together never wear one colour."""
        taken = [lane[2] for k, lane in enumerate(self.lanes) if k != i]
        first = identity(name)
        free = [(first + n) % 8 for n in range(8) if (first + n) % 8 not in taken]
        self.lanes[i][1] = name
        self.lanes[i][2] = free[0] if free else first

    def cls(self, i):
        return "id id-%d" % (self.lanes[i][2] + 1)

    def prefix(self):
        return ["│ " if key else "  " for key, _, _ in self.lanes]

    def emit(self, t, hp, label, bp, rows, tail=None, nobody=False, ep=None, child=None):
        # `label` is the heading as segments: the class each part is drawn in
        # and its text, so an agent's name carries its own identity colour.
        b = {"t": t, "hp": hp, "label": label, "bp": bp, "rows": [[k, clean(v)] for k, v in rows]}
        if ep: b["ep"] = ep
        if child: b["child"] = child
        if tail: b["tail"] = tail
        if nobody: b["nobody"] = 1
        self.out.append(b)

    def block(self, t, lane, label, rows, ep=None):
        p = self.prefix(); p[lane] = "● "
        name = self.lanes[lane][1]
        head = [[self.cls(lane), name], ["hd", FIELD + label]]
        self.emit(t, "".join(p), head, "".join(self.prefix()), rows, ep=ep)

    def edge_prefix(self, parent, child, end):
        p = self.prefix()
        for k in range(min(parent, child), max(parent, child)):
            p[k] = "┼─" if p[k] == "│ " else "──"
        p[parent] = "├─"; p[child] = end
        return "".join(p)

    def event(self, e):
        t, eid, ty, d = e["time"], e["_ep"], e["type"], e["data"]
        for key, label, _ in self.lanes:
            if key == eid: self.active = label
        if ty == "assistant/message":
            if d["text"].strip(): self.calls = 0
            self.calls += len(d.get("tool_calls", []))
        if ty == "episode/start":
            i = self.lane(eid)
            self.rename(i, d["contract"]["name"]); self.active = self.lanes[i][1]
        elif ty == "inbox/item" and (d["source"] in ("parent", "peer")
                                     or (d["source"] == "task" and self.lanes and self.lanes[0][0] == eid)):
            i = self.lane(eid)
            body = "\n\n".join(b.get("text", "[image]") for b in d["content"])
            self.block(t, i, d.get("from") or "You", [("t", body)], ep=eid)
        elif ty == "assistant/message" and d["text"].strip():
            i = self.lane(eid)
            self.block(t, i, "Assistant (interrupted)" if d.get("interrupted") else "Assistant",
                       display_value(d["text"]), ep=eid)
        elif ty == "spawn/start":
            parent, child = self.lane(eid), self.lane(d["child_id"])
            self.rename(child, d["contract"])
            hp = self.edge_prefix(parent, child, "╮ ")
            head = [["hd", "Branch: "], [self.cls(child), d["contract"]]]
            self.emit(t, hp, head, "".join(self.prefix()), [], nobody=True, ep=eid, child=d["child_id"])
        elif ty == "spawn/end":
            parent, child = self.lane(eid), self.lane(d["child_id"])
            status, body = result_text(d["outcome"])
            # The elbow closing the child's column into its parent's tee
            # already says which parent took the result, so the label names
            # the child alone, matching the Branch line that opened it.
            head = [[self.cls(child), self.lanes[child][1]], ["hd", FIELD],
                    ["hd oc-%s" % status.lower(), status]]
            hp = self.edge_prefix(parent, child, "╯ ")
            self.lanes[child] = ["", "", 0]
            while self.lanes and self.lanes[-1][0] == "": self.lanes.pop()
            self.emit(t, hp, head, "".join(self.prefix()), body, ep=eid, child=d["child_id"])
        return {"t": t, "active": self.active, "calls": self.calls}

    def finish(self, t, outcome, path):
        status, body = result_text(outcome)
        self.lanes = []
        # A returned object is the last node's own tool payload, which the
        # figure has already drawn as that node's result. Redrawing its
        # fields under "Final" tells a reader nothing the run did not
        # already show, so the closing line carries the outcome alone.
        if outcome["kind"] == "completed" and not isinstance(outcome["value"], str):
            body = []
        head = [["hd", "Final" + FIELD], ["hd oc-%s" % status.lower(), status]]
        self.emit(t, "● ", head, "", body, ep="final")

events = load()
term = Term()
status = [term.event(e) for e in events]
ROOT = FILES[0][1]
final = [e for e in events if e["type"] == "episode/end" and e["_ep"] == ROOT][0]
term.finish(final["time"] + 40, final["data"]["outcome"], ".foe/" + ROOT)
js = json.dumps({"blocks": term.out, "status": status}, separators=(",", ":"), ensure_ascii=False)
open(os.path.join(HERE, NAME + ".json"), "w").write(js)
sys.stderr.write("%d blocks, %d bytes\n" % (len(term.out), len(js)))
