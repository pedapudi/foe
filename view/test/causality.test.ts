// The causality figure's layout. The assertions name what the log carries
// — an episode, a workflow node, a call, an obligation pair — rather than
// any pixel, except where a pixel is the rule: a curve that ends in empty
// space is the bug this file exists to catch.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ELBOW,
  LANE_PITCH,
  OUTCOME_TAIL,
  causalityOutline,
  layoutLanes,
  ROW_PITCH,
  STUB,
  callTarget,
  composeLabel,
  layoutCausality,
  readCausality,
  scopeFor,
  shortenPath,
} from "../src/causality.js";
import type { CausalityEpisode, CausalityLayout } from "../src/causality.js";
import { EpisodeFold } from "../src/fold.js";
import { buildTree, flatten } from "../src/lineage.js";
import type { Summary } from "../src/fold.js";
import { fixture } from "./helpers.js";
import { obj, str } from "../src/types.js";

/** Folds one fixture log, keyed by the episode id its own first event names. */
function fold(file: string): { summary: Summary; rows: EpisodeFold["rows"] } {
  const events = fixture(file);
  const first = events[0];
  const id = str(obj(first?.data).id, file);
  const f = new EpisodeFold(id, { stream: false });
  for (const ev of events) f.push(ev);
  return { summary: f.summary, rows: f.rows };
}

/**
 * The episodes of a run, in the tree's own order with each one's depth,
 * which is the shape app.ts hands the figure.
 */
function run(...files: string[]): CausalityEpisode[] {
  const folded = files.map(fold);
  const roots = buildTree(folded.map((f) => f.summary));
  const rows = new Map(folded.map((f) => [f.summary.id, f.rows]));
  return flatten(roots).map(({ node, depth }) => readCausality(node.summary, rows.get(node.id) ?? [], depth));
}

function layout(...files: string[]): CausalityLayout {
  return layoutCausality(run(...files));
}

function lane(figure: CausalityLayout, id: string) {
  const found = figure.lanes.find((l) => l.id === id);
  assert.ok(found, `the figure has a lane for ${id}`);
  return found;
}

function row(figure: CausalityLayout, id: string) {
  const found = figure.rows.find((r) => r.id === id);
  assert.ok(found, `the figure has a row for ${id}`);
  return found;
}

// Lane allocation.

test("an episode opens a lane and a step does not", () => {
  const figure = layout("rich.jsonl");
  assert.deepEqual(
    figure.lanes.map((l) => l.id),
    ["ep_rich"],
  );
  assert.equal(figure.lanes[0]!.kind, "episode");
  // Two steps, each a mark on the one lane rather than a lane of its own.
  assert.deepEqual(
    figure.rows.map((r) => r.id),
    ["ep_rich/step/1", "ep_rich/step/2"],
  );
  for (const r of figure.rows) assert.equal(r.laneId, "ep_rich");
});

test("a spawned child opens a lane beside the caller's, and a call does not", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  assert.deepEqual(
    figure.lanes.map((l) => l.id),
    ["ep_root", "ep_child"],
  );
  assert.equal(lane(figure, "ep_root").column, 0);
  assert.equal(lane(figure, "ep_child").column, 1);
  assert.equal(lane(figure, "ep_child").parentId, "ep_root");
  // The root made six calls across its four steps and none of them is a lane.
  const calls = figure.rows.reduce((n, r) => n + r.calls.length, 0);
  assert.ok(calls >= 3, "the root's steps carry their calls as marks");
});

test("a workflow opens a lane of its own and its nodes are marks on it", () => {
  const figure = layout("workflow.jsonl");
  assert.deepEqual(
    figure.lanes.map((l) => l.id),
    ["ep_c5785a1e", "ep_c5785a1e/workflow"],
  );
  const nodes = figure.rows.filter((r) => r.kind === "node");
  assert.deepEqual(
    nodes.map((r) => r.label),
    ["manifest", "survey", "propose", "apply", "verify_change"],
  );
  for (const node of nodes) assert.equal(node.laneId, "ep_c5785a1e/workflow");
});

test("a node entered twice is one row and a loop edge, not two rows", () => {
  const figure = layout("workflow.jsonl");
  const survey = row(figure, "ep_c5785a1e/node/survey");
  assert.equal(survey.firings.length, 2, "survey fired twice");
  assert.equal(figure.rows.filter((r) => r.label === "survey").length, 1);
  const loops = figure.edges.filter((e) => e.kind === "loop");
  assert.equal(loops.length, 1);
  assert.equal(loops[0]!.to.y, survey.y, "the loop lands on the row survey already had");
  assert.ok(loops[0]!.from.y > loops[0]!.to.y, "the loop is a step back up");
  assert.notEqual(loops[0]!.bow, 0, "a loop returning to its own column bows out of it");
});

// Column reuse.

test("a lane column is released when the lane closes and taken by the next", () => {
  const figure = layout("workflow.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl", "workflow-apply-1.jsonl");
  const children = ["ep_8936e375", "ep_b0f26af9", "ep_d82415c7"].map((id) => lane(figure, id));
  // The three children run one after another under one workflow lane, so
  // they share one column rather than stepping one further right each.
  assert.deepEqual(
    children.map((l) => l.column),
    [2, 2, 2],
  );
  const spans = children.map((l) => [l.y1, l.y2] as const);
  for (let i = 1; i < spans.length; i += 1) {
    assert.ok(spans[i]![0] > spans[i - 1]![1], "a column is reused only after the lane on it closed");
  }
});

test("a lane's column follows occupancy, not tree depth", () => {
  const figure = layout("workflow.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl", "workflow-apply-1.jsonl");
  const columns = new Set(figure.lanes.map((l) => l.column));
  assert.ok(figure.lanes.length > columns.size, "more lanes than columns, so a column was reused");
  const child = lane(figure, "ep_8936e375");
  const depth = figure.episodes.find((e) => e.id === "ep_8936e375")!.depth;
  assert.equal(depth, 1, "the first proposal is one level under the run");
  assert.equal(child.column, 2, "its caller's workflow holds column 1, so the child takes 2");
  for (const l of figure.lanes) assert.equal(l.x - l.column * LANE_PITCH, figure.lanes[0]!.x);
});

// Every curve endpoint lands on a drawn line.

/** True when `(x, y)` sits on the line some lane draws. */
function onALine(figure: CausalityLayout, point: { x: number; y: number }): boolean {
  return figure.lanes.some((l) => Math.abs(l.x - point.x) < 0.001 && point.y >= l.y1 - 0.001 && point.y <= l.y2 + 0.001);
}

for (const files of [
  ["root.jsonl", "child.jsonl"],
  ["workflow.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl", "workflow-apply-1.jsonl"],
  ["overlap-parent.jsonl", "overlap-child.jsonl", "rich.jsonl"],
  ["root.jsonl", "child.jsonl", "fork.jsonl", "compact.jsonl", "retries-exhausted.jsonl"],
]) {
  test(`every curve endpoint lands on a drawn line: ${files.join(" ")}`, () => {
    const figure = layoutCausality(run(...files));
    assert.ok(figure.edges.length > 0, "the run has at least one curve");
    for (const edge of figure.edges) {
      assert.ok(onALine(figure, edge.from), `the ${edge.kind} of ${edge.laneId} leaves a line at ${edge.from.y}`);
      assert.ok(onALine(figure, edge.to), `the ${edge.kind} of ${edge.laneId} lands on a line at ${edge.to.y}`);
    }
  });
}

// The geometry over a chosen subset of rows at the heights they measured,
// which is what a view that collapses part of a run needs and what a view
// that shows all of it at one height is a special case of.

const WORKFLOW = ["workflow.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl", "workflow-apply-1.jsonl"];

test("rows stack by the heights they were given, not by a pitch", () => {
  const outline = causalityOutline(run(...WORKFLOW));
  // Alternating heights stand in for a run where a line of prose and a
  // result body are taller than a node.
  const heights = outline.rows.map((_, i) => (i % 2 === 0 ? 18 : 44));
  const figure = layoutLanes(outline, outline.rows, heights);
  figure.rows.forEach((placed, i) => {
    assert.equal(placed.height, heights[i]);
    if (i > 0) {
      const before = figure.rows[i - 1]!;
      assert.equal(placed.top, before.top + before.height, "each row starts where the one above it ended");
    }
    assert.equal(placed.y, placed.top + placed.height / 2);
  });
  for (const edge of figure.edges) {
    assert.ok(onALine(figure, edge.from), `the ${edge.kind} of ${edge.laneId} leaves a line`);
    assert.ok(onALine(figure, edge.to), `the ${edge.kind} of ${edge.laneId} lands on a line`);
  }
});

test("a lane with nothing visible on it is not drawn, and its column is freed", () => {
  const outline = causalityOutline(run(...WORKFLOW));
  const whole = layoutLanes(outline, outline.rows, outline.rows.map(() => 22));
  const hidden = "ep_8936e375";
  const visible = outline.rows.filter((r) => r.episodeId !== hidden);
  const part = layoutLanes(outline, visible, visible.map(() => 22));
  assert.ok(whole.lanes.some((l) => l.id === hidden), "the first proposal has a lane when its rows are shown");
  assert.ok(!part.lanes.some((l) => l.id === hidden), "and none when they are not");
  assert.ok(part.height < whole.height, "the figure is shorter for the rows it dropped");
  for (const edge of part.edges) {
    assert.ok(onALine(part, edge.from), `the ${edge.kind} of ${edge.laneId} leaves a line`);
    assert.ok(onALine(part, edge.to), `the ${edge.kind} of ${edge.laneId} lands on a line`);
  }
});

test("a loop is drawn only when a reader can see both of its ends", () => {
  const outline = causalityOutline(run("workflow.jsonl"));
  const whole = layoutLanes(outline, outline.rows, outline.rows.map(() => 22));
  assert.equal(whole.edges.filter((e) => e.kind === "loop").length, 1);
  const visible = outline.rows.filter((r) => r.id !== "ep_c5785a1e/node/survey");
  const part = layoutLanes(outline, visible, visible.map(() => 22));
  assert.equal(part.edges.filter((e) => e.kind === "loop").length, 0, "the row the loop returned to is hidden");
});

test("a parent's line is stretched to meet the curves that join it", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const parent = lane(figure, "ep_root");
  const child = lane(figure, "ep_child");
  const branch = figure.edges.find((e) => e.kind === "branch" && e.laneId === "ep_child")!;
  const merge = figure.edges.find((e) => e.kind === "merge" && e.laneId === "ep_child")!;
  assert.equal(branch.from.x, parent.x);
  assert.equal(merge.to.x, parent.x);
  assert.ok(parent.y1 <= branch.from.y, "the parent reaches the elbow above the child's first row");
  assert.ok(parent.y2 >= merge.to.y, "the parent reaches the merge below the child's last row");
  assert.equal(branch.to.y - branch.from.y, ELBOW);
  assert.ok(child.y1 <= branch.to.y && child.y2 >= merge.from.y);
});

test("a lane of one row is still a line", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  for (const l of figure.lanes) {
    assert.ok(l.y2 > l.y1, `${l.id} is drawn as a line rather than a point`);
  }
  // `child` holds two steps in this fixture; a one-row lane is the case the
  // stub exists for, so it is checked on a lane built for the purpose.
  const one = layoutCausality([
    {
      id: "a",
      name: "a",
      depth: 0,
      parentId: null,
      outcome: null,
      lastSeq: 1,
      steps: [{ step: 1, seq: 0, endSeq: 1, answered: true, calls: [] }],
      firings: [],
    },
  ]);
  assert.equal(one.lanes[0]!.y2 - one.lanes[0]!.y1, STUB);
});

test("a lane's outcome mark stands clear of the last row's own vertex", () => {
  const figure = layout("root.jsonl", "child.jsonl", "fork.jsonl");
  let checked = 0;
  for (const l of figure.lanes) {
    if (l.outcome === null) continue;
    checked += 1;
    const lowest = figure.rows.filter((r) => r.laneId === l.id).reduce((low, r) => Math.max(low, r.y), -Infinity);
    assert.ok(l.y2 >= lowest + OUTCOME_TAIL, `${l.id} ends its line ${OUTCOME_TAIL} below its last row`);
  }
  assert.ok(checked > 0, "the run has at least one episode that ended");
});

test("one row per step and one row height between two of them", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const ys = figure.rows.map((r) => r.y);
  for (let i = 1; i < ys.length; i += 1) assert.equal(ys[i]! - ys[i - 1]!, ROW_PITCH);
  assert.ok(figure.height > ys[ys.length - 1]!, "the figure leaves ground under the last row for its merge");
});

// Label composition.

test("the layout claims no room past its own marks", () => {
  const figure = layout("workflow.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl", "workflow-apply-1.jsonl");
  const drawn = Math.max(
    ...figure.lanes.map((l) => l.x),
    ...figure.rows.flatMap((r) => r.calls.map((c) => c.x)),
  );
  assert.equal(figure.marksWidth, drawn, "the width it reports is where its drawing ends");
  // A row carries its tree depth and no text geometry at all, so what
  // stands beside the drawing is entirely the view's to decide.
  const node = row(figure, "ep_c5785a1e/node/propose");
  const step = row(figure, "ep_c5785a1e/step/8");
  assert.ok(node.depth > step.depth, "the graph a step ran is one level inside the episode");
  assert.ok(!("indent" in node), "the layout sets no text column");
});

test("a step is named by what it did, with its step number alongside", () => {
  assert.deepEqual(composeLabel({ kind: "step", step: 4, calls: [] }), { label: "answered", aside: "step 4" });
  // The tool name stands beside the target even though the tick already
  // draws a mark: a word is faster to scan than a glyph, and the target is
  // what the reader came for.
  assert.deepEqual(
    composeLabel({ kind: "step", step: 1, calls: [{ id: "a", name: "read", target: "src/parser.rs", failed: false, childId: null, childName: "" }] }),
    { label: "read src/parser.rs", aside: "step 1" },
  );
  assert.deepEqual(
    composeLabel({
      kind: "step",
      step: 2,
      calls: [
        { id: "a", name: "read", target: "src/parser.rs", failed: false, childId: null, childName: "" },
        { id: "b", name: "read", target: "src/lexer.rs", failed: false, childId: null, childName: "" },
        { id: "c", name: "grep", target: "", failed: false, childId: null, childName: "" },
      ],
    }),
    { label: "read src/parser.rs +2", aside: "step 2" },
  );
  assert.deepEqual(
    composeLabel({ kind: "step", step: 3, calls: [{ id: "a", name: "spawn", target: "surveyor", failed: false, childId: "ep_child", childName: "surveyor" }] }),
    { label: "spawn surveyor", aside: "step 3" },
  );
  assert.deepEqual(composeLabel({ kind: "node", node: "propose" }), { label: "propose", aside: "" });
  // A request the provider never answered is still a step: it is where the
  // episode spent what it spent, and a figure that dropped it would show
  // an exhausted episode as one that did nothing.
  assert.deepEqual(composeLabel({ kind: "step", step: 5, answered: false, calls: [] }), {
    label: "no answer",
    aside: "step 5",
  });
});

test("a target is a short field or nothing, never a slice of free text", () => {
  // The whole path where it fits: which directory a file is in is part of
  // what the reader is looking for.
  assert.equal(callTarget({ path: "tests/parser_test.py" }), "tests/parser_test.py");
  assert.equal(callTarget({ path: "crates/runtime/src/episode/parser.rs" }), "crates/…/parser.rs");
  assert.equal(callTarget({ cmd: "pytest tests/parser_test.py" }), "", "a shell command is free text and is not shown");
  assert.equal(callTarget({ program: "survey", task: "List the parser tests." }), "survey");
  assert.equal(callTarget({}), "");
  assert.equal(callTarget("a bare string"), "");
});

test("a path that must shorten elides its middle and keeps the basename", () => {
  assert.equal(shortenPath("src/parser.rs", 22), "src/parser.rs");
  assert.equal(shortenPath("crates/runtime/src/episode/parser.rs", 22), "crates/…/parser.rs");
  assert.equal(shortenPath("crates/runtime/src/episode/a_very_long_file_name.rs", 22), "a_very_long_file_name.rs");
});

test("the fixture's own steps read as their role", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const labels = figure.rows.filter((r) => r.episodeId === "ep_root").map((r) => `${r.label} · ${r.aside}`);
  assert.deepEqual(labels, [
    "read tests/parser_test.py · step 1",
    "spawn survey · step 2",
    "bash · step 3",
    "answered · step 4",
  ]);
});

// The conversation one row scopes to.

test("selecting a step scopes to its own messages and to what it opened", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const scope = scopeFor(figure, "ep_root/step/2");
  assert.ok(scope);
  assert.equal(scope.title, "spawn survey · step 2");
  assert.deepEqual(
    scope.segments.map((s) => s.episodeId),
    ["ep_root", "ep_child"],
  );
  const own = scope.segments[0]!;
  assert.ok(own.from <= 19 && own.to >= 29, "the step covers its request, its spawn and its result");
  const child = scope.segments[1]!;
  assert.equal(child.from, 0, "a node below is included whole");
  assert.ok(child.to >= 10);
});

test("selecting a step that opened nothing scopes to that step alone", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const scope = scopeFor(figure, "ep_root/step/1");
  assert.ok(scope);
  assert.deepEqual(
    scope.segments.map((s) => s.episodeId),
    ["ep_root"],
  );
});

test("a workflow node entered twice shows both passes, labelled", () => {
  const figure = layout("workflow.jsonl", "workflow-propose-1.jsonl", "workflow-propose-2.jsonl", "workflow-apply-1.jsonl");
  const scope = scopeFor(figure, "ep_c5785a1e/node/propose");
  assert.ok(scope);
  assert.deepEqual(
    scope.segments.map((s) => `${s.episodeId} ${s.pass}`),
    ["ep_c5785a1e pass 1", "ep_8936e375 ", "ep_c5785a1e pass 2", "ep_b0f26af9 "],
  );
  // Each pass covers the firing that opened it and nothing of the other.
  assert.deepEqual(
    scope.segments.filter((s) => s.episodeId === "ep_c5785a1e").map((s) => [s.from, s.to]),
    [
      [8, 13],
      [19, 24],
    ],
  );
});

test("a node entered once carries no pass label", () => {
  const figure = layout("workflow.jsonl", "workflow-apply-1.jsonl");
  const scope = scopeFor(figure, "ep_c5785a1e/node/apply");
  assert.ok(scope);
  assert.deepEqual(
    scope.segments.map((s) => s.pass),
    ["", ""],
  );
  assert.equal(scope.segments[1]!.episodeId, "ep_d82415c7");
});

test("an episode whose requests were never answered still has rows", () => {
  const figure = layout("retries-exhausted.jsonl");
  assert.ok(figure.rows.length > 0, "the exhausted episode is drawn");
  assert.ok(
    figure.rows.some((r) => r.label === "no answer"),
    "the request that no message answered is a row of its own",
  );
  assert.equal(figure.lanes.length, 1, "and it holds a lane");
});

test("a row that is not in the figure scopes to nothing", () => {
  assert.equal(scopeFor(layout("rich.jsonl"), "ep_rich/step/9"), null);
});
