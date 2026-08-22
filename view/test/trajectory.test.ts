// Trajectory layout: row placement, the x mapping on both axes, mark
// sizing, connector endpoints, and the axis labels.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import { buildTree, flatten } from "../src/lineage.js";
import {
  DEPTH_INDENT,
  MARK_MIN_WIDTH,
  NODE_LANE,
  ROW_HEIGHT,
  TOOL_LANE,
  TOOL_PITCH,
  fitLabel,
  guidesFor,
  labelWidthFor,
  layoutTrajectory,
  niceStep,
  nodeLaneOrder,
  originsOf,
  tickLabel,
  timeAtSeq,
} from "../src/trajectory.js";
import type { TrajectoryEpisode, TrajectoryInput } from "../src/trajectory.js";
import { fixture } from "./helpers.js";

const WIDTH = 900;
const HEIGHT = 300;

function fold(name: string): EpisodeFold {
  const f = new EpisodeFold(name.replace(".jsonl", ""), { stream: false });
  for (const ev of fixture(name)) f.push(ev);
  return f;
}

/** The fixture episodes as the pane receives them, in tree order. */
function episodes(...names: string[]): TrajectoryEpisode[] {
  const folds = names.map(fold);
  const roots = buildTree(folds.map((f) => f.summary));
  return flatten(roots).map(({ node, depth }) => {
    const s = node.summary;
    return {
      id: s.id,
      name: s.name,
      identity: s.identity,
      depth,
      startTime: s.startTime,
      endTime: s.endTime,
      lastSeq: s.lastSeq,
      outcome: s.outcome,
      parentId: s.parentId,
      forkOrigin: s.forkOrigin,
      marks: s.marks,
      firings: s.firings,
      decisions: s.decisions,
    };
  });
}

function input(list: TrajectoryEpisode[], overrides: Partial<TrajectoryInput> = {}): TrajectoryInput {
  return { episodes: list, axis: "time", width: WIDTH, height: HEIGHT, now: 0, ...overrides };
}

test("rows follow the tree order and sit one row height apart", () => {
  const list = episodes("overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list));
  assert.deepEqual(out.rows.map((r) => r.id), ["ep_over_parent", "ep_over_child"]);
  assert.deepEqual(out.rows.map((r) => r.depth), [0, 1]);
  assert.equal(out.rows[0]!.top, out.plot.top);
  assert.equal(out.rows[0]!.y, out.plot.top + ROW_HEIGHT / 2);
  // The parent's three calls overlap in time and take three heights of the
  // tool lane; the child's one call takes one, so its row is the plain
  // height and its lifetime line sits below everything the parent drew.
  assert.deepEqual(out.rows.map((r) => r.height), [ROW_HEIGHT + 2 * TOOL_PITCH, ROW_HEIGHT]);
  assert.equal(out.rows[1]!.top, out.rows[0]!.top + out.rows[0]!.height);
});

test("the plot spans the pane to the right of the label column", () => {
  const out = layoutTrajectory(input(episodes("overlap-parent.jsonl")));
  assert.equal(out.labelWidth, labelWidthFor(WIDTH));
  assert.equal(out.plot.left, out.labelWidth + 10);
  assert.ok(out.plot.right < WIDTH);
  assert.ok(out.plot.right > out.plot.left);
});

test("the label column follows the pane width between its limits", () => {
  assert.equal(labelWidthFor(2000), 230, "a wide pane stops at the widest column");
  assert.equal(labelWidthFor(200), 90, "a narrow pane keeps the plot most of itself");
  assert.equal(labelWidthFor(700), Math.round(700 * 0.26));
});

test("the label column widens by one indent per level of the deepest row", () => {
  // 26 percent of 400 is under the narrowest column, so the floor decides,
  // and each level of depth raises that floor by one indent.
  assert.equal(labelWidthFor(400, 0), 116);
  assert.equal(labelWidthFor(400, 2), 116 + 2 * DEPTH_INDENT);
  assert.equal(labelWidthFor(2000, 9), 230, "the widest column still bounds it");
  const deep = layoutTrajectory(input(workflowTree()));
  assert.equal(deep.labelWidth, labelWidthFor(WIDTH, 1));
});

test("a label too long for its column is shortened rather than run into the plot", () => {
  assert.equal(fitLabel("survey", 100, 6.6), "survey", "a name that fits is left whole");
  assert.equal(fitLabel("survey-propose-apply", 40, 6.6), "surve\u2026");
  assert.equal(fitLabel("survey", 4, 6.6), "", "a column with room for nothing sets nothing");
  const narrow = layoutTrajectory(input(workflowTree(), { width: 320 }));
  for (const row of narrow.rows) {
    assert.ok(row.labelX + row.label.length * 6.6 <= narrow.plot.left, `${row.label} stays out of the plot`);
    for (const lane of row.lanes) {
      assert.ok(lane.labelX + lane.label.length * 5.4 <= narrow.plot.left, `${lane.label} stays out of the plot`);
    }
  }
  assert.ok(narrow.rows.some((r) => r.label.endsWith("\u2026")), "the widest name was shortened");
});

test("on the time axis a lifetime bar spans the episode's own clock", () => {
  const list = episodes("overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list));
  const [parent, child] = out.rows as [(typeof out.rows)[number], (typeof out.rows)[number]];
  // The parent opens the window, so its bar starts at the plot's left edge
  // and ends at the right edge; the child lies strictly inside it.
  assert.equal(parent.x1, out.plot.left);
  assert.equal(Math.round(parent.x2), Math.round(out.plot.right));
  assert.ok(child.x1 > parent.x1, "the child starts after its parent");
  assert.ok(child.x2 < parent.x2, "the child ends before its parent");
});

test("the same episodes on the sequence axis map log positions instead", () => {
  const list = episodes("overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list, { axis: "sequence" }));
  assert.equal(out.domain[0], 0);
  assert.equal(out.domain[1], Math.max(...list.map((e) => e.lastSeq)));
  // Every episode's log starts at seq 0, so every bar starts at the left edge.
  for (const row of out.rows) assert.equal(row.x1, out.plot.left);
  const longest = out.rows.reduce((a, b) => (a.x2 > b.x2 ? a : b));
  assert.equal(Math.round(longest.x2), Math.round(out.plot.right));
});

test("on the elapsed axis every root starts at the left edge of the plot", () => {
  // Two independent runs 67 days apart. On the wall clock the later one is
  // a sliver at the right edge; measured from each root's own start they
  // are read against each other.
  const list = episodes("compact.jsonl", "overlap-parent.jsonl", "overlap-child.jsonl");
  const wall = layoutTrajectory(input(list));
  const slivers = wall.rows.filter((r) => r.x2 - r.x1 < (wall.plot.right - wall.plot.left) / 100);
  assert.equal(slivers.length, wall.rows.length, "on the wall clock the gap between the runs is the whole axis");

  const out = layoutTrajectory(input(list, { axis: "elapsed" }));
  assert.equal(out.domain[0], 0, "the elapsed axis starts at zero");
  for (const row of out.rows.filter((r) => r.depth === 0)) {
    assert.equal(row.x1, out.plot.left, `${row.id} starts at the left edge`);
  }
  const longest = out.rows.reduce((a, b) => (a.x2 > b.x2 ? a : b));
  assert.equal(Math.round(longest.x2), Math.round(out.plot.right), "the longest run reaches the right edge");
  // Every row is now a readable length rather than a sliver.
  for (const row of out.rows) assert.ok(row.x2 - row.x1 > 1, `${row.id} has a visible length`);
});

test("the elapsed axis keeps a child at its offset from its own root", () => {
  const list = episodes("overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list, { axis: "elapsed" }));
  const [parent, child] = out.rows as [(typeof out.rows)[number], (typeof out.rows)[number]];
  const [, high] = out.domain;
  const perMs = (out.plot.right - out.plot.left) / high;
  const offset = list[1]!.startTime - list[0]!.startTime;
  assert.ok(offset > 0);
  assert.equal(parent.x1, out.plot.left, "the root of the tree is the origin");
  assert.ok(Math.abs(child.x1 - (out.plot.left + offset * perMs)) < 1e-6, "the child keeps its offset");
  assert.ok(child.x2 < parent.x2, "the child still ends inside its parent");
});

test("origins are the start of each row's own root", () => {
  const list = episodes("compact.jsonl", "overlap-parent.jsonl", "overlap-child.jsonl");
  const origins = originsOf(list);
  const byId = new Map(list.map((e) => [e.id, e]));
  assert.equal(origins.get("ep_compact"), byId.get("ep_compact")!.startTime);
  assert.equal(origins.get("ep_over_parent"), byId.get("ep_over_parent")!.startTime);
  assert.equal(
    origins.get("ep_over_child"),
    byId.get("ep_over_parent")!.startTime,
    "a child measures from its root rather than from itself",
  );
});

test("a tool call keeps the length of its duration on the elapsed axis", () => {
  const list = episodes("overlap-parent.jsonl");
  const out = layoutTrajectory(input(list, { axis: "elapsed" }));
  const [, high] = out.domain;
  const perMs = (out.plot.right - out.plot.left) / high;
  const tools = out.rows[0]!.marks.filter((m) => m.kind === "tool" && m.durationMs > 0);
  assert.ok(tools.length > 0, "the fixture calls tools");
  for (const mark of tools) {
    assert.ok(Math.abs(mark.w - mark.durationMs * perMs) < 1e-6, `${mark.label} is as wide as it is long`);
  }
});

test("brackets group the roots that ran one program and nothing else", () => {
  const list = episodes("compact.jsonl", "root.jsonl", "overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list, { axis: "elapsed" }));
  assert.equal(out.groups.length, 1);
  const group = out.groups[0]!;
  assert.equal(group.identity, "sha256:cccc");
  assert.equal(group.runs, 2);
  assert.ok(group.x < out.plot.left, "the bracket stands in the gutter beside the plot");
  const first = out.rows.find((r) => r.id === "ep_compact")!;
  const last = out.rows.find((r) => r.id === "ep_over_child")!;
  assert.ok(group.y1 >= first.top && group.y1 < first.top + first.height);
  assert.ok(group.y2 > last.top && group.y2 <= last.top + last.height);
  assert.equal(layoutTrajectory(input(episodes("root.jsonl", "compact.jsonl"))).groups.length, 0);
});

test("the x mapping is linear and puts the domain ends on the plot edges", () => {
  const list = episodes("overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list));
  const [low, high] = out.domain;
  const span = out.plot.right - out.plot.left;
  const at = (v: number) => out.plot.left + ((v - low) / (high - low)) * span;
  const requests = out.rows[0]!.marks.filter((m) => m.kind === "request");
  for (const mark of requests) assert.ok(Math.abs(mark.x - at(mark.time)) < 1e-6);
  assert.ok(requests.every((m) => m.x >= out.plot.left && m.x <= out.plot.right));
});

test("a single instant gives a degenerate domain and no division by zero", () => {
  const one: TrajectoryEpisode = {
    id: "ep_one", name: "one", identity: "sha256:one", depth: 0, startTime: 5, endTime: 5, lastSeq: 0,
    outcome: { kind: "completed" }, parentId: null, forkOrigin: null,
    marks: [{ kind: "request", seq: 0, time: 5, durationMs: 0, span: null, label: "", detail: "" }],
    firings: [], decisions: [],
  };
  const out = layoutTrajectory(input([one]));
  assert.ok(Number.isFinite(out.rows[0]!.x1));
  assert.ok(Number.isFinite(out.rows[0]!.marks[0]!.x));
});

test("a tool segment on the time axis runs back from its result by its duration", () => {
  const list = episodes("overlap-parent.jsonl");
  const out = layoutTrajectory(input(list));
  const segment = out.rows[0]!.marks.find((m) => m.kind === "tool" && m.label === "read")!;
  assert.equal(segment.durationMs, 1400);
  const [low, high] = out.domain;
  const span = out.plot.right - out.plot.left;
  const at = (v: number) => out.plot.left + ((v - low) / (high - low)) * span;
  assert.ok(Math.abs(segment.x - at(segment.time - segment.durationMs)) < 1e-6);
  assert.ok(Math.abs(segment.x + segment.w - at(segment.time)) < 1e-6);
});

test("a tool segment on the sequence axis is a tick at its own position", () => {
  const out = layoutTrajectory(input(episodes("overlap-parent.jsonl"), { axis: "sequence" }));
  const segment = out.rows[0]!.marks.find((m) => m.kind === "tool")!;
  assert.equal(segment.w, 0);
});

test("tool marks take a lane below the lifetime line and every other mark stays on it", () => {
  for (const axis of ["time", "sequence"] as const) {
    const out = layoutTrajectory(input(episodes("overlap-parent.jsonl", "overlap-child.jsonl"), { axis }));
    for (const row of out.rows) {
      for (const mark of row.marks) {
        if (mark.kind === "tool") {
          assert.ok(mark.y >= row.y + TOOL_LANE, `${axis}: tool at seq ${mark.seq} is below the line`);
          continue;
        }
        assert.equal(mark.y, row.y, `${axis}: ${mark.kind} at seq ${mark.seq}`);
      }
    }
  }
});

test("the lane keeps a hairline tool call apart from a request tick at the same instant", () => {
  // A run bound by the model: every tool call is milliseconds against a
  // span of tens of seconds, so no segment has a visible length.
  const start = 1_700_000_000_000;
  const one: TrajectoryEpisode = {
    id: "ep_bound", name: "bound", identity: "sha256:bound", depth: 0, startTime: start, endTime: start + 43_000, lastSeq: 3,
    outcome: { kind: "completed" }, parentId: null, forkOrigin: null,
    marks: [
      { kind: "request", seq: 1, time: start + 10_000, durationMs: 0, span: null, label: "step 1", detail: "" },
      { kind: "tool", seq: 2, time: start + 10_000, durationMs: 4, span: null, label: "read", detail: "" },
    ],
    firings: [], decisions: [],
  };
  const out = layoutTrajectory(input([one]));
  const [request, tool] = out.rows[0]!.marks as [(typeof out.rows)[0]["marks"][0], (typeof out.rows)[0]["marks"][0]];
  assert.ok(Math.abs(request.x - tool.x) < 0.2, "the two marks fall at the same position on the axis");
  assert.equal(tool.w, MARK_MIN_WIDTH, "a call of no measurable length still draws");
  assert.equal(tool.y - request.y, TOOL_LANE, "position is what separates them");
  assert.ok(TOOL_LANE > 0 && TOOL_LANE < ROW_HEIGHT / 2, "the lane stays inside its own row");
});

test("a running episode is drawn to the clock reading given", () => {
  const list = episodes("overlap-parent.jsonl");
  const running = { ...list[0]!, endTime: null };
  const now = running.startTime + 30_000;
  const out = layoutTrajectory(input([running], { now }));
  assert.equal(out.rows[0]!.running, true);
  assert.equal(out.domain[1], now);
  assert.equal(Math.round(out.rows[0]!.x2), Math.round(out.plot.right));
});

test("a spawn connector runs from the parent's spawn mark to the child's start", () => {
  const list = episodes("overlap-parent.jsonl", "overlap-child.jsonl");
  const out = layoutTrajectory(input(list));
  assert.equal(out.connectors.length, 1);
  const edge = out.connectors[0]!;
  assert.equal(edge.fork, false);
  assert.equal(edge.childId, "ep_over_child");
  const spawn = out.rows[0]!.marks.find((m) => m.kind === "spawn" && m.label === "ep_over_child")!;
  assert.equal(edge.from.x, spawn.x);
  assert.equal(edge.from.y, out.rows[0]!.y);
  assert.equal(edge.to.x, out.rows[1]!.x1);
  assert.equal(edge.to.y, out.rows[1]!.y);
});

test("a fork connector is dashed and starts at the origin's position at the fork seq", () => {
  const list = episodes("root.jsonl", "child.jsonl", "fork.jsonl");
  const out = layoutTrajectory(input(list, { axis: "sequence" }));
  const forkEdge = out.connectors.find((c) => c.childId === "ep_fork")!;
  assert.equal(forkEdge.fork, true);
  const origin = out.rows.find((r) => r.id === "ep_root")!;
  const child = out.rows.find((r) => r.id === "ep_fork")!;
  assert.equal(forkEdge.from.y, origin.y);
  assert.equal(forkEdge.to.y, child.y);
  assert.equal(forkEdge.to.x, child.x1);
  // The origin end sits at seq 12, the boundary the fork was seeded at.
  const span = out.plot.right - out.plot.left;
  const expected = out.plot.left + (12 / out.domain[1]) * span;
  assert.ok(Math.abs(forkEdge.from.x - expected) < 1e-6);
});

test("an episode whose parent is absent draws no connector", () => {
  const out = layoutTrajectory(input(episodes("overlap-child.jsonl")));
  assert.equal(out.connectors.length, 0);
});

test("the time of a seq comes from the nearest mark at or below it", () => {
  const parent = episodes("overlap-parent.jsonl")[0]!;
  assert.equal(timeAtSeq(parent, 0), parent.startTime);
  const spawn = parent.marks.find((m) => m.kind === "spawn")!;
  assert.equal(timeAtSeq(parent, spawn.seq), spawn.time);
  assert.equal(timeAtSeq(parent, spawn.seq - 1) <= spawn.time, true);
});

test("the figure grows past the pane when the rows do not fit", () => {
  const one = episodes("overlap-parent.jsonl")[0]!;
  const many = Array.from({ length: 40 }, (_, i) => ({ ...one, id: `ep_${i}`, parentId: null }));
  const out = layoutTrajectory(input(many, { height: 200 }));
  assert.equal(out.height, out.plot.bottom + 8);
  assert.ok(out.height > 200);
  const few = layoutTrajectory(input([one], { height: 200 }));
  assert.equal(few.height, 200);
});

test("axis ticks fall inside the plot and carry short labels", () => {
  const out = layoutTrajectory(input(episodes("overlap-parent.jsonl", "overlap-child.jsonl")));
  assert.ok(out.ticks.length >= 2 && out.ticks.length <= 24);
  for (const tick of out.ticks) {
    assert.ok(tick.x >= out.plot.left - 1e-6 && tick.x <= out.plot.right + 1e-6);
    assert.ok(tick.label.length <= 6, tick.label);
  }
});

test("tick labels read as elapsed time or as a log position", () => {
  assert.equal(tickLabel("time", 1000, 0, 1000), "1.0 s");
  assert.equal(tickLabel("time", 30_000, 0, 10_000), "30 s");
  assert.equal(tickLabel("time", 125_000, 0, 30_000), "2:05");
  assert.equal(tickLabel("sequence", 42, 0, 10), "42");
});

test("a step below one second labels in milliseconds, so no two labels repeat", () => {
  assert.equal(tickLabel("time", 50, 0, 50), "50 ms");
  assert.equal(tickLabel("time", 0, 0, 50), "0 ms");
  const short = layoutTrajectory(input(episodes("root.jsonl", "child.jsonl", "fork.jsonl")));
  const labels = short.ticks.map((t) => t.label);
  assert.equal(new Set(labels).size, labels.length, labels.join(" "));
});

test("a run of many seconds still labels every tick distinctly", () => {
  const long = layoutTrajectory(input(episodes("overlap-parent.jsonl", "overlap-child.jsonl")));
  const labels = long.ticks.map((t) => t.label);
  assert.equal(new Set(labels).size, labels.length, labels.join(" "));
});

test("a tick step rounds up to one, two, or five times a power of ten", () => {
  assert.equal(niceStep(1.1, "time"), 2);
  assert.equal(niceStep(3, "time"), 5);
  assert.equal(niceStep(7, "time"), 10);
  assert.equal(niceStep(230, "sequence"), 500);
  assert.equal(niceStep(0, "sequence"), 1);
});

test("an empty pane lays out without rows or connectors", () => {
  const out = layoutTrajectory(input([]));
  assert.deepEqual(out.rows, []);
  assert.deepEqual(out.connectors, []);
  assert.deepEqual(out.domain, [0, 1]);
});

test("the fold records one mark per event the trajectory draws", () => {
  const s = fold("overlap-child.jsonl").summary;
  const kinds = s.marks.map((m) => m.kind);
  assert.deepEqual(kinds, ["request", "tool", "compaction", "request", "retry", "request"]);
  const tool = s.marks.find((m) => m.kind === "tool")!;
  assert.equal(tool.label, "bash");
  assert.equal(tool.durationMs, 2600);
  assert.equal(tool.detail, '{"cmd":"ls crates"}');
  const retry = s.marks.find((m) => m.kind === "retry")!;
  assert.equal(retry.label, "rate-limit");
});

test("a request's span runs from its call to the message that answers it", () => {
  const marks = fold("root.jsonl").summary.marks.filter((m) => m.kind === "request");
  const first = marks[0]!;
  assert.equal(first.seq, 3);
  assert.equal(first.span!.endSeq, 9, "the span ends at the assistant/message");
  assert.equal(first.span!.endTime - first.time, 60);
  assert.equal(first.durationMs, 60, "the duration is the length of the span");
  assert.equal(first.span!.firstTokenTime! - first.time, 10, "the wait before the first token");
  assert.equal(first.span!.firstTokenSeq, 4);
});

test("a request no message answered spans nothing", () => {
  const marks = fold("root.jsonl").summary.marks.filter((m) => m.kind === "request");
  const unanswered = marks.find((m) => m.seq === 12)!;
  assert.equal(unanswered.span!.endSeq, 12);
  assert.equal(unanswered.durationMs, 0);
  assert.equal(unanswered.span!.firstTokenTime, null);
});

test("a request answered only by an error has a span and no first token", () => {
  const marks = fold("retries-exhausted.jsonl").summary.marks.filter((m) => m.kind === "request");
  assert.equal(marks.length, 5);
  for (const mark of marks) {
    assert.equal(mark.span!.firstTokenTime, null, `seq ${mark.seq} produced no token`);
    assert.ok(mark.span!.endSeq > mark.seq, "the error chunk still ends the span");
  }
});

test("a request draws as a span whose parts are the wait and the answer", () => {
  const out = layoutTrajectory(input(episodes("root.jsonl")));
  const request = out.rows[0]!.marks.find((m) => m.kind === "request" && m.seq === 3)!;
  const [low, high] = out.domain;
  const span = out.plot.right - out.plot.left;
  const at = (v: number) => out.plot.left + ((v - low) / (high - low)) * span;
  assert.ok(Math.abs(request.x - at(request.time)) < 1e-6, "the bar starts at the call");
  assert.ok(Math.abs(request.x + request.w - at(request.span!.endTime)) < 1e-6, "and ends at the answer");
  assert.ok(Math.abs(request.head - (at(request.span!.firstTokenTime!) - at(request.time))) < 1e-6);
  assert.ok(request.head < request.w, "the wait is part of the answer's span");
});

test("a longer request draws a longer bar", () => {
  const out = layoutTrajectory(input(episodes("root.jsonl")));
  const marks = out.rows[0]!.marks.filter((m) => m.kind === "request");
  const byDuration = [...marks].sort((a, b) => a.durationMs - b.durationMs);
  const byWidth = [...marks].sort((a, b) => a.w - b.w);
  assert.deepEqual(byWidth.map((m) => m.seq), byDuration.map((m) => m.seq));
});

test("a request keeps its length on the sequence axis, where its chunks are events", () => {
  const out = layoutTrajectory(input(episodes("root.jsonl"), { axis: "sequence" }));
  const request = out.rows[0]!.marks.find((m) => m.kind === "request" && m.seq === 3)!;
  const [low, high] = out.domain;
  const span = out.plot.right - out.plot.left;
  const at = (v: number) => out.plot.left + ((v - low) / (high - low)) * span;
  assert.ok(Math.abs(request.x - at(3)) < 1e-6);
  assert.ok(Math.abs(request.x + request.w - at(9)) < 1e-6);
  assert.ok(Math.abs(request.head - (at(4) - at(3))) < 1e-6);
});

test("a mark that is not a request has no span and no head", () => {
  const out = layoutTrajectory(input(episodes("root.jsonl")));
  for (const mark of out.rows[0]!.marks) {
    if (mark.kind === "request") continue;
    assert.equal(mark.span, null, `${mark.kind} at seq ${mark.seq}`);
    assert.equal(mark.head, 0, `${mark.kind} at seq ${mark.seq}`);
  }
});

test("the domain reaches the end of the last span", () => {
  const list = episodes("root.jsonl");
  const out = layoutTrajectory(input(list));
  const ends = list.flatMap((e) => e.marks.map((m) => (m.span ? m.span.endTime : m.time)));
  assert.ok(out.domain[1] >= Math.max(...ends));
});

// ---- the node band of a workflow episode ----

/** The workflow fixture and the three child logs its model nodes ran. */
function workflowTree(): TrajectoryEpisode[] {
  return episodes("workflow.jsonl", "workflow-apply-1.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl");
}

test("a lane stands for each node that fired, ordered by its first firing", () => {
  const out = layoutTrajectory(input(workflowTree()));
  assert.deepEqual(
    out.rows[0]!.lanes.map((l) => l.node),
    ["manifest", "survey", "propose", "apply", "verify_change"],
    "the order is the order the run entered the nodes",
  );
  assert.equal(nodeLaneOrder([]).length, 0, "an episode with no firing has no lane");
});

test("a firing's width is the interval between its two events, to scale", () => {
  const out = layoutTrajectory(input(workflowTree()));
  const row = out.rows[0]!;
  const [low, high] = out.domain;
  const perMs = (out.plot.right - out.plot.left) / (high - low);
  for (const firing of row.firings) {
    if (firing.endTime === null) continue;
    const observed = firing.endTime - firing.startTime;
    const expected = Math.max(MARK_MIN_WIDTH, observed * perMs);
    assert.ok(Math.abs(firing.w - expected) < 1e-6, `${firing.node} fire ${firing.fire}: ${firing.w} for ${observed} ms`);
  }
  // The eight firings the fixture records, in the order they started.
  assert.deepEqual(
    row.firings.map((f) => f.node),
    ["manifest", "survey", "propose", "survey", "propose", "apply", "verify_change", "verify_change"],
  );
});

test("a workflow row is taller by one lane per node that fired", () => {
  const out = layoutTrajectory(input(workflowTree()));
  assert.equal(out.rows[0]!.height, ROW_HEIGHT + 5 * NODE_LANE);
  assert.deepEqual(out.rows.slice(1).map((r) => r.height), [ROW_HEIGHT, ROW_HEIGHT, ROW_HEIGHT]);
  assert.equal(out.rowsHeight, out.rows.reduce((sum, r) => sum + r.height, 0));
});

test("the node band sits under the lifetime line and over the tool lane", () => {
  const row = layoutTrajectory(input(workflowTree())).rows[0]!;
  for (const lane of row.lanes) assert.ok(lane.y > row.y, `${lane.node} is under the line`);
  const tools = row.marks.filter((m) => m.kind === "tool");
  assert.ok(tools.length > 0, "the fixture has a call to place");
  for (const mark of tools) {
    for (const lane of row.lanes) assert.ok(mark.y > lane.y, "a tool call is under every lane");
  }
});

test("a firing's colour direction comes from how it ended", () => {
  const row = layoutTrajectory(input(workflowTree())).rows[0]!;
  const failed = row.firings.filter((f) => f.error !== "");
  assert.equal(failed.length, 1, "one firing of the fixture failed");
  assert.equal(failed[0]!.node, "verify_change");
  assert.equal(failed[0]!.fire, 1);
});

test("a model node's firing names the child it ran, and no other firing does", () => {
  const row = layoutTrajectory(input(workflowTree())).rows[0]!;
  const withChild = row.firings.filter((f) => f.childId !== null);
  assert.deepEqual(withChild.map((f) => f.node), ["propose", "propose", "apply"]);
});

test("a child's connector leaves the firing that ran it rather than the spawn ring", () => {
  const out = layoutTrajectory(input(workflowTree()));
  const row = out.rows[0]!;
  assert.equal(out.connectors.length, 3);
  for (const edge of out.connectors) {
    const firing = row.firings.find((f) => f.childId === edge.childId);
    assert.ok(firing, `${edge.childId} was run by a firing`);
    assert.equal(edge.from.x, firing!.x, "the connector starts where the firing did");
    assert.equal(edge.from.y, firing!.y, "and on that firing's own lane");
  }
});

test("a decision sits on the lane of the node it names", () => {
  const row = layoutTrajectory(input(workflowTree())).rows[0]!;
  assert.deepEqual(row.decisions.map((d) => `${d.kind}/${d.node}/${d.label}`), [
    "branch/propose/widen",
    "branch/propose/apply",
    "recovery/verify_change/retry",
  ]);
  for (const decision of row.decisions) {
    const lane = row.lanes.find((l) => l.node === decision.node)!;
    assert.equal(decision.y, lane.y);
  }
});

test("a decision label is dropped where a firing of its node would run into it", () => {
  const row = layoutTrajectory(input(workflowTree())).rows[0]!;
  const shown = row.decisions.filter((d) => d.showLabel).map((d) => d.label);
  // `widen` re-fires survey and then propose, whose second firing starts
  // inside the label; `retry` re-fires verify_change at the same instant.
  assert.deepEqual(shown, ["apply"]);
});

test("an episode with no graph has no lane, no firing, and no decision", () => {
  const out = layoutTrajectory(input(episodes("root.jsonl")));
  assert.deepEqual(out.rows[0]!.lanes, []);
  assert.deepEqual(out.rows[0]!.firings, []);
  assert.deepEqual(out.rows[0]!.decisions, []);
});

// ---- co-timed tool calls ----

/** One episode whose tool calls fall `gap` milliseconds apart. */
function batch(count: number, gap: number): TrajectoryEpisode {
  const start = 1_700_000_000_000;
  return {
    id: "ep_batch", name: "batch", identity: "sha256:batch", depth: 0, startTime: start, endTime: start + 40_000, lastSeq: count,
    outcome: { kind: "completed" }, parentId: null, forkOrigin: null,
    marks: Array.from({ length: count }, (_, i) => ({
      kind: "tool" as const, seq: i + 1, time: start + 10_000 + i * gap, durationMs: 0, span: null,
      label: `read ${i}`, detail: "",
    })),
    firings: [], decisions: [],
  };
}

test("calls issued together take successive heights and keep their own x", () => {
  const out = layoutTrajectory(input([batch(6, 0)]));
  const row = out.rows[0]!;
  const tools = row.marks.filter((m) => m.kind === "tool");
  assert.equal(tools.length, 6);
  assert.equal(new Set(tools.map((m) => m.x)).size, 1, "every call is at the one instant it happened");
  assert.deepEqual(
    tools.map((m) => m.y - row.y - TOOL_LANE),
    [0, TOOL_PITCH, 2 * TOOL_PITCH, 3 * TOOL_PITCH, 4 * TOOL_PITCH, 5 * TOOL_PITCH],
  );
  assert.equal(row.height, ROW_HEIGHT + 5 * TOOL_PITCH, "the row grows to hold the stack");
});

test("calls spread far enough apart share one height", () => {
  // Five seconds between calls is hundreds of pixels at this width.
  const out = layoutTrajectory(input([batch(6, 5_000)]));
  const row = out.rows[0]!;
  const tools = row.marks.filter((m) => m.kind === "tool");
  assert.equal(new Set(tools.map((m) => m.y)).size, 1);
  assert.equal(row.height, ROW_HEIGHT);
});

// ---- the backoff a retry imposes ----

test("a retry's mark runs forward from it by the backoff it imposes", () => {
  const out = layoutTrajectory(input(episodes("retries-exhausted.jsonl")));
  const retries = out.rows[0]!.marks.filter((m) => m.kind === "retry");
  assert.deepEqual(retries.map((r) => r.durationMs), [500, 1000, 2000, 4000, 8000], "the backoff doubles");
  const [low, high] = out.domain;
  const perMs = (out.plot.right - out.plot.left) / (high - low);
  for (const retry of retries) {
    assert.ok(Math.abs(retry.w - retry.durationMs * perMs) < 1e-6, `${retry.durationMs} ms`);
  }
});

test("a delay has no length in log positions, so a retry is a tick on the sequence axis", () => {
  const out = layoutTrajectory(input(episodes("retries-exhausted.jsonl"), { axis: "sequence" }));
  for (const retry of out.rows[0]!.marks.filter((m) => m.kind === "retry")) assert.equal(retry.w, 0);
});

test("the domain holds the last backoff, which reaches past the last event", () => {
  const list = episodes("retries-exhausted.jsonl");
  const out = layoutTrajectory(input(list));
  const last = list[0]!.marks.filter((m) => m.kind === "retry").at(-1)!;
  assert.ok(out.domain[1] >= last.time + last.durationMs);
});

// ---- the label column ----

test("the row label names the program, whose id the sidebar states beside it", () => {
  const out = layoutTrajectory(input(workflowTree()));
  assert.deepEqual(out.rows.map((r) => r.name), ["survey-propose-apply", "propose", "propose", "apply"]);
  assert.deepEqual(out.rows.map((r) => r.labelX), [6, 22, 22, 22]);
  assert.deepEqual(out.rows.map((r) => r.lanes.map((l) => l.labelX)[0] ?? null), [22, null, null, null]);
});

test("a rail carries depth, and its last segment turns into the row's own label", () => {
  const out = layoutTrajectory(input(workflowTree()));
  assert.deepEqual(out.rows[0]!.guides, [], "a root row has no ancestor to rail to");
  for (const row of out.rows.slice(1)) {
    assert.equal(row.guides.length, 1, "one ancestor, one segment");
    assert.equal(row.guides[0]!.elbow, true);
    assert.equal(row.guides[0]!.y1, row.top);
  }
  const middle = out.rows[1]!;
  const last = out.rows[3]!;
  assert.equal(middle.guides[0]!.y2, middle.top + middle.height, "a sibling follows, so the rail passes through");
  assert.equal(last.guides[0]!.y2, last.y, "the last child's rail stops at its own label");
});

test("a rail passes through a row when a deeper row follows it", () => {
  assert.deepEqual(guidesFor(0, 100, 24, 112, 0), [], "a root row rails to nothing");
  const [outer, inner] = guidesFor(2, 100, 24, 112, 2);
  assert.equal(outer!.elbow, false);
  assert.equal(outer!.y2, 124, "a row at the same depth follows, so the outer rail passes through");
  assert.equal(inner!.elbow, true);
  assert.equal(inner!.y2, 124);
  assert.equal(inner!.x - outer!.x, DEPTH_INDENT);
  assert.equal(guidesFor(1, 100, 24, 112, -1)[0]!.y2, 112, "the last row of all stops at its label");
});

// ---- a run that has not finished ----

/** One workflow episode with no end, whose last firing is still open. */
function openRun(now: number): TrajectoryEpisode {
  const start = 1_700_000_000_000;
  return {
    id: "ep_open", name: "grapher", identity: "sha256:grapher", depth: 0, startTime: start, endTime: null, lastSeq: 4,
    outcome: null, parentId: null, forkOrigin: null, marks: [], decisions: [],
    firings: [
      { node: "survey", fire: 1, startSeq: 1, startTime: start + 1_000, endSeq: 2, endTime: start + 3_000, durationMs: 1_900, error: "", childId: null },
      { node: "draft", fire: 1, startSeq: 3, startTime: start + 3_000, endSeq: null, endTime: null, durationMs: null, error: "", childId: "ep_child" },
    ],
  };
}

test("a firing still running is drawn to the clock reading given", () => {
  const now = 1_700_000_000_000 + 9_000;
  const out = layoutTrajectory(input([openRun(now)], { now }));
  const row = out.rows[0]!;
  assert.equal(row.running, true);
  const [open] = row.firings.filter((f) => f.endSeq === null);
  assert.equal(open!.node, "draft");
  assert.equal(Math.round(open!.x + open!.w), Math.round(row.x2), "it reaches the end of the lifetime bar");
  assert.equal(open!.durationMs, null, "a length no node reported stays absent rather than zero");
});

test("an open firing lengthens as the clock moves and its lane stays put", () => {
  const start = 1_700_000_000_000;
  const early = layoutTrajectory(input([openRun(start + 5_000)], { now: start + 5_000 }));
  const later = layoutTrajectory(input([openRun(start + 20_000)], { now: start + 20_000 }));
  const laneOf = (out: typeof early) => out.rows[0]!.lanes.map((l) => l.node);
  assert.deepEqual(laneOf(early), ["survey", "draft"]);
  assert.deepEqual(laneOf(later), laneOf(early));
  assert.equal(early.rows[0]!.height, later.rows[0]!.height);
});
