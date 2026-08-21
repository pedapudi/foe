// Trajectory layout: row placement, the x mapping on both axes, mark
// sizing, connector endpoints, and the axis labels.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import { buildTree, flatten } from "../src/lineage.js";
import { ROW_HEIGHT, labelWidthFor, layoutTrajectory, niceStep, tickLabel, timeAtSeq } from "../src/trajectory.js";
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
      depth,
      startTime: s.startTime,
      endTime: s.endTime,
      lastSeq: s.lastSeq,
      outcome: s.outcome,
      parentId: s.parentId,
      forkOrigin: s.forkOrigin,
      marks: s.marks,
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
  assert.equal(out.rows[1]!.y - out.rows[0]!.y, ROW_HEIGHT);
  assert.equal(out.rows[0]!.y, out.plot.top + ROW_HEIGHT / 2);
});

test("the plot spans the pane to the right of the label column", () => {
  const out = layoutTrajectory(input(episodes("overlap-parent.jsonl")));
  assert.equal(out.labelWidth, labelWidthFor(WIDTH));
  assert.equal(out.plot.left, out.labelWidth + 10);
  assert.ok(out.plot.right < WIDTH);
  assert.ok(out.plot.right > out.plot.left);
});

test("the label column follows the pane width between its limits", () => {
  assert.equal(labelWidthFor(2000), 230);
  assert.equal(labelWidthFor(200), 116);
  assert.equal(labelWidthFor(700), Math.round(700 * 0.26));
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
    id: "ep_one", name: "one", depth: 0, startTime: 5, endTime: 5, lastSeq: 0,
    outcome: { kind: "completed" }, parentId: null, forkOrigin: null,
    marks: [{ kind: "request", seq: 0, time: 5, durationMs: 0, label: "", detail: "" }],
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
  assert.equal(tickLabel("time", 1000, 0), "1.0 s");
  assert.equal(tickLabel("time", 30_000, 0), "30 s");
  assert.equal(tickLabel("time", 125_000, 0), "2:05");
  assert.equal(tickLabel("sequence", 42, 0), "42");
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
