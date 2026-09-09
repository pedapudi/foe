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
  visibleRows,
  ROW_PITCH,
  STUB,
  composeLabel,
  elapsedLabel,
  layoutCausality,
  readCausality,
  scopeFor,
  shortenPath,
} from "../src/causality.js";
import type { CausalityCall, CausalityEpisode, CausalityLayout } from "../src/causality.js";
import { EpisodeFold } from "../src/fold.js";
import { buildTree, flatten } from "../src/episode-tree.js";
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

/** The same episodes with every `episode/end` dropped, so the run is live. */
function running(...files: string[]): CausalityEpisode[] {
  const folded = files.map((file) => {
    const events = fixture(file).filter((ev) => obj(ev).type !== "episode/end");
    const id = str(obj(obj(events[0]).data).id, file);
    const f = new EpisodeFold(id, { stream: false });
    for (const ev of events) f.push(ev);
    return { summary: f.summary, rows: f.rows };
  });
  const roots = buildTree(folded.map((f) => f.summary));
  const rows = new Map(folded.map((f) => [f.summary.id, f.rows]));
  return flatten(roots).map(({ node, depth }) => readCausality(node.summary, rows.get(node.id) ?? [], depth));
}

// docs/log-format.md "Seeding": events copied from the fork origin record
// what that episode did. A fork that reads them as its own draws itself
// repeating its origin's steps and opening its origin's children.
test("a forked episode owns only the rows written after seed/end", () => {
  const forked = run("root.jsonl", "fork.jsonl").find((episode) => episode.id === "ep_fork");
  assert.ok(forked, "the run holds the forked episode");
  assert.deepEqual(forked.steps.map((step) => step.step), [2]);
  assert.ok(forked.steps.every((step) => step.seq > 12), "every row is past the seed-end at seq 12");
});

// The mark that says a run is in flight belongs on the row the run reached,
// and belongs nowhere once the run has settled.
test("the row a live episode reached carries the pulse, and a settled run carries none", () => {
  const live = layoutLanes(
    causalityOutline(running("root.jsonl", "child.jsonl")),
    visibleRows(causalityOutline(running("root.jsonl", "child.jsonl")), "steps"),
    visibleRows(causalityOutline(running("root.jsonl", "child.jsonl")), "steps").map(() => ROW_PITCH),
  );
  const pulsing = live.rows.filter((row) => row.pulse);
  assert.ok(pulsing.length > 0, "a run with nothing settled pulses");
  for (const row of pulsing) {
    const own = live.rows.filter((r) => r.laneId === row.laneId);
    assert.equal(row, own[own.length - 1], "the pulse is on the last row the lane holds");
  }
  assert.equal(layout("root.jsonl", "child.jsonl").rows.filter((row) => row.pulse).length, 0);
});

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
  // The episode's own row and its two steps, all marks on the one lane
  // rather than lanes of their own.
  assert.deepEqual(
    figure.rows.map((r) => r.id),
    ["ep_rich", "ep_rich/step/1", "ep_rich/step/2"],
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

// A merge is the parent taking what the child returned. Drawing one for a
// child still running reports a return that has not happened, which is the
// same claim as an outcome mark on a lane with no outcome.
test("a lane that has not settled folds back into nothing", () => {
  const files = ["root.jsonl", "child.jsonl"];
  const settled = layout(...files);
  const child = "ep_child";
  assert.ok(
    settled.edges.some((edge) => edge.kind === "merge" && edge.laneId === child),
    "a child that returned folds back into its caller",
  );
  const live = layoutCausality(running(...files));
  assert.equal(lane(live, child).end, null, "the fixture without its ends holds an unsettled child");
  assert.equal(live.edges.filter((edge) => edge.kind === "merge").length, 0);
  assert.ok(
    live.edges.some((edge) => edge.kind === "branch" && edge.laneId === child),
    "it still branches from the call that opened it",
  );
  // Its caller keeps no ground under it for a fold that will not come.
  assert.notEqual(lane(live, "ep_root").y2, lane(live, child).y2 + ELBOW);
  for (const edge of live.edges) {
    assert.ok(onALine(live, edge.from), `the ${edge.kind} of ${edge.laneId} leaves a line`);
    assert.ok(onALine(live, edge.to), `the ${edge.kind} of ${edge.laneId} lands on a line`);
  }
});

// docs/viewer.md: every episode row carries the task the episode was given,
// which is what tells two children of one contract apart.
test("an episode's task hangs under its own row", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  for (const episode of outline.episodes) {
    const row = outline.rows.find((r) => r.id === `${episode.id}/task`);
    assert.ok(row, `${episode.id} carries its task`);
    assert.equal(row.body, episode.task);
    assert.notEqual(episode.task, "", "the fixture episodes were each given one");
    assert.equal(row.parent, episode.id, "it hangs under the episode row");
    assert.equal(outline.rows.indexOf(row), outline.rows.findIndex((r) => r.id === episode.id) + 1);
  }
  // Two children of one contract are told apart by it and by nothing else.
  const tasks = outline.episodes.map((e) => e.task);
  assert.equal(new Set(tasks).size, tasks.length);
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

// Two children a run held open at the same time are two lanes at the same
// time, whatever order their rows are read in. The rows of a child hang
// under the call that opened it, so concurrent children occupy sequential
// row ranges; a column released by row alone would draw them as one lane
// reused, which is the drawing that says each waited for the one before.
test("two episodes that overlap in time never share a column", () => {
  const figure = layout("overlap-parent.jsonl", "overlap-child.jsonl", "rich.jsonl");
  const first = lane(figure, "ep_over_child");
  const second = lane(figure, "ep_rich");
  assert.ok(second.start < (first.end ?? Infinity), "the fixture holds both episodes open at once");
  assert.notEqual(first.column, second.column);
});

/**
 * A run written out rather than folded from a log: a lead whose one turn
 * spawns a child per call, and children whose start and end the caller
 * states. Times are milliseconds on the wall clock, and the lead starts at
 * 1 so that it is the run's own start. The call that opened each child
 * takes the child's end, which is when its result came back.
 */
function fanOut(children: { id: string; start: number; end: number | null }[]): CausalityEpisode[] {
  const calls: CausalityCall[] = children.map((child, index) => ({
    id: `tc_${index + 1}`,
    name: "spawn",
    subject: "",
    failed: false,
    childId: child.id,
    childName: "worker",
    result: "",
    resultSeq: 2,
    resultTime: child.end ?? 0,
  }));
  const lead: CausalityEpisode = {
    id: "ep_lead",
    name: "lead",
    task: "the task the person gave",
    depth: 0,
    parentId: null,
    outcome: null,
    startTime: 1,
    endTime: null,
    lastSeq: 9,
    steps: [{ step: 1, seq: 1, time: 2, endSeq: 3, answered: true, text: "", attempts: 1, calls }],
    firings: [],
  };
  return [
    lead,
    ...children.map((child) => ({
      id: child.id,
      name: "worker",
      task: `the unit for ${child.id}`,
      depth: 1,
      parentId: "ep_lead",
      outcome: child.end === null ? null : { kind: "completed", value: null },
      startTime: child.start,
      endTime: child.end,
      lastSeq: 4,
      steps: [{ step: 1, seq: 1, time: child.start, endSeq: 4, answered: true, text: "", attempts: 1, calls: [] }],
      firings: [],
    })),
  ];
}

// One turn that opens several children opens them together. Its own calls
// are read before any child's transcript, so the fan-out is one row after
// another rather than a child wedged between two of its siblings' calls,
// and the columns those children take are as many as ran at once.
test("the calls of one step are read before the children they opened", () => {
  const episodes = fanOut([{ id: "ep_a", start: 10, end: 90 }, { id: "ep_b", start: 20, end: 80 }]);
  const outline = causalityOutline(episodes);
  const at = (id: string) => outline.rows.findIndex((row) => row.id === id);
  assert.ok(at("ep_lead/step/1/call/tc_1") < at("ep_lead/step/1/call/tc_2"), "the two calls keep their order");
  assert.ok(at("ep_lead/step/1/call/tc_2") < at("ep_a"), "both calls are read before the first child's rows");
  assert.ok(at("ep_a") < at("ep_b"), "the children follow in the order their calls opened them");
  const figure = layoutCausality(episodes);
  assert.notEqual(lane(figure, "ep_a").column, lane(figure, "ep_b").column, "both were open at once");
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
    // A tall row's mark sits on its first line rather than halfway down it.
    assert.equal(placed.y, placed.top + Math.min(placed.height, 22) / 2);
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
  // Read at its coarsest an episode with no children is one row, which is
  // the case the stub exists for.
  const outline = causalityOutline(run("rich.jsonl"));
  const rail = visibleRows(outline, "episodes");
  assert.equal(rail.length, 1);
  const one = layoutLanes(outline, rail, [22]);
  assert.equal(one.lanes[0]!.y2 - one.lanes[0]!.y1 - OUTCOME_TAIL, STUB);
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
  const head = row(figure, "ep_c5785a1e");
  const node = row(figure, "ep_c5785a1e/node/propose");
  assert.ok(node.depth > head.depth, "a node of the graph sits inside the episode that ran it");
  assert.ok(!("indent" in node), "the layout sets no text column");
});

test("a step is named by what it did, with its step number alongside", () => {
  assert.deepEqual(composeLabel({ kind: "step", step: 4, calls: [] }), { label: "answered", aside: "step 4" });
  // The tool name stands beside the target even though the tick already
  // draws a mark: a word is faster to scan than a glyph, and the target is
  // what the reader came for.
  assert.deepEqual(
    composeLabel({ kind: "step", step: 1, calls: [{ id: "a", name: "read", subject: "src/parser.rs", failed: false, childId: null, childName: "", result: "", resultSeq: 0, resultTime: 0 }] }),
    { label: "read src/parser.rs", aside: "step 1" },
  );
  assert.deepEqual(
    composeLabel({
      kind: "step",
      step: 2,
      calls: [
        { id: "a", name: "read", subject: "src/parser.rs", failed: false, childId: null, childName: "", result: "", resultSeq: 0, resultTime: 0 },
        { id: "b", name: "read", subject: "src/lexer.rs", failed: false, childId: null, childName: "", result: "", resultSeq: 0, resultTime: 0 },
        { id: "c", name: "grep", subject: "", failed: false, childId: null, childName: "", result: "", resultSeq: 0, resultTime: 0 },
      ],
    }),
    { label: "read src/parser.rs +2", aside: "step 2" },
  );
  assert.deepEqual(
    composeLabel({ kind: "step", step: 3, calls: [{ id: "a", name: "spawn", subject: "surveyor", failed: false, childId: "ep_child", childName: "surveyor", result: "", resultSeq: 0, resultTime: 0 }] }),
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

test("a call with no subject names its tool alone rather than guessing", () => {
  // A log written before tools stated what they acted on, or a tool that
  // states nothing, leaves the field empty. A missing target is honest
  // where a target guessed from the arguments is not.
  const bare = { id: "a", name: "grep", subject: "", failed: false, childId: null, childName: "", result: "", resultSeq: 0, resultTime: 0 };
  assert.deepEqual(composeLabel({ kind: "call", calls: [bare] }), { label: "grep", aside: "" });
  assert.deepEqual(composeLabel({ kind: "step", step: 1, calls: [bare] }), { label: "grep", aside: "step 1" });
});

test("a failed call reads as the line its tool wrote, without stuttering", () => {
  const failed = {
    id: "a",
    name: "read",
    subject: "read: src/parser.rs: No such file or directory (os error 2)",
    failed: true,
    childId: null,
    childName: "",
    result: "",
    resultSeq: 0,
    resultTime: 0,
  };
  assert.deepEqual(composeLabel({ kind: "call", calls: [failed] }), {
    label: "read: src/parser.rs: No such file or directory (os error 2)",
    aside: "",
  });
});

test("a path that must shorten elides its middle and keeps the basename", () => {
  assert.equal(shortenPath("src/parser.rs", 22), "src/parser.rs");
  assert.equal(shortenPath("crates/runtime/src/episode/parser.rs", 22), "crates/…/parser.rs");
  assert.equal(shortenPath("crates/runtime/src/episode/a_very_long_file_name.rs", 22), "a_very_long_file_name.rs");
});

test("the fixture's own steps read as their role", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const labels = figure.rows.filter((r) => r.episodeId === "ep_root").map((r) => `${r.label} – ${r.aside}`);
  // Each call reads as the line its own tool wrote. The interrupted `bash`
  // names what failed, and because that line already opens with the tool's
  // name the label does not repeat it.
  assert.deepEqual(labels, [
    "fix-test – ep_root",
    "read tests/parser_test.py lines 1\u20135 of 5 – step 1",
    "spawn survey – step 2",
    "bash: the request was interrupted before the tool ran – step 3",
    "answered – step 4",
  ]);
});

// The four readings of one hierarchy.

test("read at its coarsest the outline is the episode rail", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const rail = visibleRows(outline, "episodes");
  assert.deepEqual(
    rail.map((r) => `${r.kind} ${r.id}`),
    ["episode ep_root", "episode ep_child"],
  );
  // The spawned child is one level in even though the call that opened it
  // is not on the page. Counted against the hierarchy it would be three,
  // and the rail would lose its nesting.
  assert.equal(rail[0]!.level, 0);
  assert.equal(rail[1]!.level, 1);
});

test("each reading adds the kinds of row it names", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const kinds = (depth: Parameters<typeof visibleRows>[1]) =>
    [...new Set(visibleRows(outline, depth).map((r) => r.kind))].sort();
  assert.deepEqual(kinds("episodes"), ["episode"]);
  assert.deepEqual(kinds("steps"), ["episode", "step"]);
  assert.deepEqual(kinds("calls"), ["call", "episode", "step"]);
  // What the model said and what its tools returned are separate rungs,
  // because a reader who wants the conversation must not have to take the
  // tool output with it.
  // The conversation carries what the episode returned as well as what the
  // model said: an episode can spend every step on tool calls and answer only
  // in its typed return, and a reading that left it out would show a run that
  // said nothing.
  assert.deepEqual(kinds("conversation"), ["call", "episode", "outcome", "prose", "step", "task"]);
  assert.deepEqual(kinds("outputs"), ["call", "episode", "outcome", "prose", "result", "step", "task"]);
  const graph = causalityOutline(run("workflow.jsonl"));
  assert.ok(visibleRows(graph, "steps").some((r) => r.kind === "node"));
});

test("the conversation costs almost nothing and the outputs cost everything", () => {
  // The finding the depth ladder exists for: over a recorded run of 1,255
  // events the model's own words came to 5,719 characters and its tools
  // returned 139,281. The two therefore sit on separate rungs, and the
  // fixtures show the same shape at their own scale.
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const mass = (depth: Parameters<typeof visibleRows>[1]) =>
    visibleRows(outline, depth).reduce((n, r) => n + r.body.length, 0);
  const prose = mass("conversation");
  const all = mass("outputs");
  assert.ok(prose > 0, "the conversation rung sets what the model said");
  assert.ok(all > prose * 2, `result bodies dominate: ${all - prose} against ${prose}`);
  for (const row of visibleRows(outline, "conversation")) {
    assert.notEqual(row.kind, "result", "no tool output on the conversation rung");
  }
});

test("a step's label defers to its calls once they are on the page", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const summarised = visibleRows(outline, "steps").find((r) => r.id === "ep_root/step/1")!;
  assert.equal(summarised.label, "read tests/parser_test.py lines 1\u20135 of 5");
  assert.equal(summarised.calls.length, 1, "it draws the call it stands in for");
  const deferred = visibleRows(outline, "calls").find((r) => r.id === "ep_root/step/1")!;
  assert.equal(deferred.label, "step 1", "it does not echo the line beneath it");
  assert.equal(deferred.aside, "");
  assert.equal(deferred.calls.length, 0, "and gives up the tick the call now draws itself");
});

test("a step that retried says so once its calls are shown", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const retried = visibleRows(outline, "calls").find((r) => r.id === "ep_root/step/2")!;
  assert.equal(retried.label, "step 2 – attempt 2 of 2");
});

test("a caret opens one branch one level past the reading", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const shut = visibleRows(outline, "steps").map((r) => r.id);
  assert.ok(!shut.includes("ep_root/step/1/call/tc_01"));
  const open = visibleRows(outline, "steps", new Set(["ep_root/step/1"])).map((r) => r.id);
  assert.ok(open.includes("ep_root/step/1/call/tc_01"), "the branch that was opened");
  assert.ok(!open.includes("ep_root/step/3/call/tc_03"), "and no other");
  assert.ok(
    !open.includes("ep_root/step/1/call/tc_01/result"),
    "one level, so the call's own result waits for its own caret",
  );
  const deeper = visibleRows(outline, "steps", new Set(["ep_root/step/1", "ep_root/step/1/call/tc_01"]));
  assert.ok(deeper.some((r) => r.id === "ep_root/step/1/call/tc_01/result"));
});

test("every row carries its log position, because the outline reorders time", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  for (const r of visibleRows(outline, "outputs")) {
    assert.equal(typeof r.seq, "number", `${r.id} knows where it sits in the log`);
  }
  // A child's rows sit under the call that spawned it rather than in log
  // order, so reading order jumps. The elapsed time in the gutter is the
  // sign, since it is measured from one origin for the run.
  const ids = visibleRows(outline, "outputs").map((r) => r.id);
  assert.ok(ids.indexOf("ep_child") < ids.indexOf("ep_root/step/3"), "the child is read before later steps");
});

test("the gutter is printed once per event, not once per row", () => {
  const outline = causalityOutline(run("root.jsonl", "child.jsonl"));
  const deep = visibleRows(outline, "outputs");
  // A step's prose and a call's result body stand for the event their own
  // row already named, so they leave the column blank; a repeat would say
  // that a second thing happened at that instant.
  const prose = deep.find((r) => r.kind === "prose")!;
  const result = deep.find((r) => r.kind === "result")!;
  assert.equal(prose.showTime, false);
  assert.equal(result.showTime, false);
  for (const row of deep) {
    if (row.showTime !== false) continue;
    const above = deep[deep.indexOf(row) - 1]!;
    assert.equal(above.id, row.parent, `${row.id} continues the row above it`);
    assert.equal(above.seq, row.seq, `${row.id} stands for the event above it`);
    assert.equal(above.time, row.time, `${row.id} stands for the same instant`);
  }
});

test("every row is timed from one origin, so two episodes are comparable", () => {
  const episodes = run("root.jsonl", "child.jsonl");
  const outline = causalityOutline(episodes);
  const root = episodes.find((e) => e.id === "ep_root")!;
  const child = episodes.find((e) => e.id === "ep_child")!;
  assert.equal(outline.start, root.startTime, "the run began when its root episode began");
  // Collapsed to the rail the child follows the root directly, and both
  // logs number their own events from zero. The gutter says what the two
  // zeroes cannot: the child began after the root did.
  const rail = visibleRows(outline, "episodes");
  assert.deepEqual(rail.map((r) => r.seq), [0, 0]);
  assert.deepEqual(rail.map((r) => r.time), [root.startTime, child.startTime]);
  assert.ok(child.startTime > root.startTime, "the fixture opens the child after the run begins");
});

test("elapsed time is written to a tenth of a second, and past an hour to a second", () => {
  assert.equal(elapsedLabel(0), "0:00.0");
  assert.equal(elapsedLabel(56_237), "0:56.2");
  assert.equal(elapsedLabel(93_255), "1:33.2");
  assert.equal(elapsedLabel(3_599_900), "59:59.9");
  assert.equal(elapsedLabel(3_600_000), "1:00:00");
  assert.equal(elapsedLabel(45_296_000), "12:34:56");
  // A row of an episode whose start the log never recorded would otherwise
  // read as a time before the run began.
  assert.equal(elapsedLabel(-1), "");
  assert.equal(elapsedLabel(Number.NaN), "");
});

test("lanes hold at every reading, and every curve still lands on a line", () => {
  for (const files of [["root.jsonl", "child.jsonl"], WORKFLOW]) {
    const outline = causalityOutline(run(...files));
    for (const depth of ["episodes", "steps", "calls", "conversation", "outputs"] as const) {
      const visible = visibleRows(outline, depth);
      // Heights that vary the way prose and a result body vary.
      const heights = visible.map((r) => (r.body === "" ? 22 : 60));
      const figure = layoutLanes(outline, visible, heights);
      for (const edge of figure.edges) {
        assert.ok(onALine(figure, edge.from), `${depth}: the ${edge.kind} of ${edge.laneId} leaves a line`);
        assert.ok(onALine(figure, edge.to), `${depth}: the ${edge.kind} of ${edge.laneId} lands on a line`);
      }
      for (const lane of figure.lanes) assert.ok(lane.y2 > lane.y1, `${depth}: ${lane.id} is a line`);
    }
  }
});

// The conversation one row scopes to.

test("selecting a step scopes to its own messages and to what it opened", () => {
  const figure = layout("root.jsonl", "child.jsonl");
  const scope = scopeFor(figure, "ep_root/step/2");
  assert.ok(scope);
  assert.equal(scope.title, "spawn survey – step 2");
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
      [6, 13],
      [17, 24],
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

test("a step of a running episode that nothing has answered is waiting, not unanswered", () => {
  assert.equal(composeLabel({ kind: "step", step: 4, answered: false, waiting: true }).label, "waiting");
  assert.equal(composeLabel({ kind: "step", step: 4, answered: false }).label, "no answer");
  assert.equal(composeLabel({ kind: "step", step: 4, answered: false, waiting: true }).aside, "step 4");
});
