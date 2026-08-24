// The declared graph read from a log, and its layout. The fixture is
// written by the runtime, so the assertions name what the graph declares
// and what the run did rather than any episode id or timestamp.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  STUB_LENGTH,
  TASK_SOURCE,
  declaredWorkflow,
  layoutWorkflow,
  rankNodes,
  readWorkflow,
} from "../src/workflow.js";
import type { Edge, Workflow } from "../src/workflow.js";
import type { LogEvent } from "../src/types.js";
import { fixture } from "./helpers.js";

const WIDTH = 1000;

function run(): Workflow {
  const fold = new EpisodeFold("workflow", { stream: false });
  const events = fixture("workflow.jsonl");
  for (const ev of events) fold.push(ev);
  const workflow = readWorkflow(fold.summary.program, events);
  assert.ok(workflow, "the fixture episode declares a workflow");
  return workflow;
}

function node(workflow: Workflow, name: string) {
  const found = workflow.nodes.find((n) => n.name === name);
  assert.ok(found, `the graph declares ${name}`);
  return found;
}

function edge(workflow: Workflow, from: string, to: string, label: string | null = null): Edge {
  const found = workflow.edges.find((e) => e.from === from && e.to === to && e.label === label);
  assert.ok(found, `the graph declares ${from} -> ${to}${label === null ? "" : ` under ${label}`}`);
  return found;
}

test("an episode with no workflow key declares no graph", () => {
  assert.equal(declaredWorkflow({}), null);
  assert.equal(declaredWorkflow({ workflow: { nodes: {} } }), null);
  assert.equal(readWorkflow({ name: "coding" }, []), null);
});

test("every declared node appears, whether or not it fired", () => {
  const workflow = run();
  assert.deepEqual(
    workflow.nodes.map((n) => n.name),
    ["apply", "manifest", "propose", "record_abandonment", "survey", "verify_change"],
  );
  assert.equal(node(workflow, "record_abandonment").firings.length, 0, "the abandon branch was never chosen");
  assert.equal(node(workflow, "record_abandonment").direction, "", "a node that never fired takes no direction");
});

test("a node's kind and what it runs come from the declaration", () => {
  const workflow = run();
  assert.equal(node(workflow, "survey").kind, "tool");
  assert.equal(node(workflow, "survey").detail, "grep");
  assert.equal(node(workflow, "propose").kind, "model");
  assert.equal(node(workflow, "propose").detail, "propose");
  assert.equal(node(workflow, "verify_change").terminal, true);
  assert.equal(node(workflow, "survey").maxFires, 2);
  assert.equal(node(workflow, "manifest").maxFires, null, "an acyclic node declares no bound");
});

test("a node takes its direction from its last firing's end", () => {
  const workflow = run();
  assert.equal(node(workflow, "apply").direction, "good");
  assert.equal(node(workflow, "verify_change").direction, "good", "the retry ended cleanly");
  assert.equal(node(workflow, "verify_change").firings[0]!.error !== "", true, "the first firing errored");
  assert.equal(node(workflow, "verify_change").firings[1]!.error, "");
  for (const n of workflow.nodes) assert.equal(n.running, false, "the run ended");
});

test("a node on a cycle records every firing", () => {
  const workflow = run();
  assert.deepEqual(node(workflow, "survey").firings.map((f) => f.fire), [1, 2]);
  assert.deepEqual(node(workflow, "propose").firings.map((f) => f.fire), [1, 2]);
  assert.deepEqual(node(workflow, "manifest").firings.map((f) => f.fire), [1]);
});

test("a model node's firing names the child episode it ran", () => {
  const workflow = run();
  const firings = node(workflow, "propose").firings;
  assert.equal(firings.length, 2);
  for (const firing of firings) assert.match(String(firing.childId), /^ep_/);
  assert.notEqual(firings[0]!.childId, firings[1]!.childId, "each firing is its own episode");
  assert.equal(node(workflow, "survey").firings[0]!.childId, null, "a tool node runs no episode");
});

test("an edge is traversed when a firing received a value across it", () => {
  const workflow = run();
  assert.equal(edge(workflow, TASK_SOURCE, "propose").traversed, true);
  assert.equal(edge(workflow, "manifest", "survey").traversed, true);
  assert.equal(edge(workflow, "survey", "propose").traversed, true);
  assert.equal(edge(workflow, "apply", "verify_change").traversed, true);
  assert.equal(edge(workflow, "propose", "apply", "apply").traversed, true);
  assert.equal(edge(workflow, "propose", "survey", "widen").traversed, true);
  assert.equal(edge(workflow, "propose", "record_abandonment", "abandon").traversed, false);
});

test("every declared label is kept, including one with no successor", () => {
  const workflow = run();
  assert.deepEqual(
    node(workflow, "propose").branches,
    [
      { label: "abandon", successors: ["record_abandonment"] },
      { label: "apply", successors: ["apply"] },
      { label: "nothing", successors: [] },
      { label: "widen", successors: ["survey"] },
    ],
  );
  assert.deepEqual(workflow.chosen, ["propose/apply", "propose/widen"]);
  assert.deepEqual(node(workflow, "propose").firings.map((f) => f.label), ["widen", "apply"]);
});

test("a recovery names the node that failed, its action, and its cause", () => {
  const workflow = run();
  assert.equal(workflow.recoveries.length, 1);
  const recovery = workflow.recoveries[0]!;
  assert.equal(recovery.node, "verify_change");
  assert.equal(recovery.action, "retry");
  assert.equal(recovery.cause, "tool-error");
  assert.equal(recovery.target, "verify_change");
  assert.equal(recovery.intervention, 1);
});

test("a guard-skipped node carries its skip and its value crosses to its successors", () => {
  const program = {
    workflow: {
      nodes: {
        work: { tool: "t", verify: "check" },
        audit: { model: { name: "audit" }, follows: ["work"], skip_when_verified: "work" },
        report: { tool: "t", follows: ["audit"], terminal: true },
      },
    },
  };
  const events = [
    { seq: 0, time: 1, type: "episode/start", data: { id: "e", program } },
    { seq: 1, time: 2, type: "workflow/node-start", data: { node: "work", fire: 1, inputs: [] } },
    { seq: 2, time: 3, type: "verification/result", data: { tool: "check", status: "accepted", findings: [], duration_ms: 1 } },
    { seq: 3, time: 4, type: "workflow/node-end", data: { node: "work", fire: 1, value: {}, rendered: "", duration_ms: 1 } },
    { seq: 4, time: 5, type: "workflow/node-skipped", data: { node: "audit", verified_by: "work", verification_seq: 2 } },
    { seq: 5, time: 6, type: "workflow/node-start", data: { node: "report", fire: 1, inputs: [4] } },
    { seq: 6, time: 7, type: "workflow/node-end", data: { node: "report", fire: 1, value: {}, rendered: "", duration_ms: 1 } },
  ] as LogEvent[];
  const workflow = readWorkflow(program, events)!;
  const audit = workflow.nodes.find((n) => n.name === "audit")!;
  assert.deepEqual(audit.skipped, { seq: 4, verifiedBy: "work", verificationSeq: 2 });
  assert.equal(audit.firings.length, 0);
  assert.equal(audit.direction, "", "a skipped node earns no direction");
  assert.equal(workflow.nodes.find((n) => n.name === "work")!.verify, "check");
  const onward = workflow.edges.find((e) => e.from === "audit" && e.to === "report")!;
  assert.equal(onward.traversed, true, "the skip's contributed value crossed the edge");
});

test("rank is the longest path of forward edges, and a cycle does not push it", () => {
  const workflow = run();
  const rank = rankNodes(workflow.nodes.map((n) => n.name), workflow.edges);
  assert.equal(rank.get("manifest"), 0);
  assert.equal(rank.get("survey"), 1);
  assert.equal(rank.get("propose"), 2);
  assert.equal(rank.get("apply"), 3);
  assert.equal(rank.get("record_abandonment"), 3);
  assert.equal(rank.get("verify_change"), 4);
});

test("the layout is a pure function of the graph and the width", () => {
  const workflow = run();
  const once = layoutWorkflow(workflow, WIDTH);
  const twice = layoutWorkflow(workflow, WIDTH);
  assert.deepEqual(once, twice);
  assert.deepEqual(once.ranks, [
    [TASK_SOURCE],
    ["manifest"],
    ["survey"],
    ["propose"],
    ["apply", "record_abandonment"],
    ["verify_change"],
  ]);
});

test("columns follow rank and rows follow name order within a column", () => {
  const out = layoutWorkflow(run(), WIDTH);
  const at = (name: string) => out.nodes.find((n) => n.name === name)!;
  assert.equal(at(TASK_SOURCE).rank, 0, "the task source stands ahead of every node");
  assert.equal(at("manifest").rank, 1);
  assert.ok(at("record_abandonment").y > at("apply").y, "a column is ordered by name");
  assert.equal(at("apply").y, at("verify_change").y, "each column starts at the same top");
  assert.ok(at("survey").x > at("manifest").x);
  assert.ok(at("verify_change").x > at("apply").x);
  assert.equal(at("apply").height, NODE_HEIGHT);
});

test("a narrow pane shrinks the boxes and keeps room for the labels", () => {
  const workflow = run();
  const wide = layoutWorkflow(workflow, 1400);
  const narrow = layoutWorkflow(workflow, 900);
  const gapOf = (out: ReturnType<typeof layoutWorkflow>) => {
    const first = out.nodes.find((n) => n.rank === 0)!;
    const second = out.nodes.find((n) => n.rank === 1)!;
    return second.x - (first.x + first.width);
  };
  assert.equal(wide.nodes[0]!.width, NODE_WIDTH, "a pane with room keeps the box width");
  assert.ok(narrow.nodes[0]!.width < NODE_WIDTH, "a narrow pane shrinks the box");
  assert.ok(narrow.width < wide.width);
  // The gap between two columns carries the left one's branch labels, so it
  // never falls below what the longest label needs.
  const longest = Math.max(...workflow.nodes.flatMap((n) => n.branches.map((b) => b.label.length)));
  assert.ok(gapOf(narrow) > longest * 6, `the gap is ${gapOf(narrow)} for a ${longest}-character label`);
  assert.equal(gapOf(narrow), gapOf(wide), "the label room holds at both widths");
  const tiny = layoutWorkflow(workflow, 300);
  assert.ok(tiny.width > 300, "below every minimum the figure keeps its own width");
});

test("a choice point gives each label its own anchor on the node's edge", () => {
  const out = layoutWorkflow(run(), WIDTH);
  const propose = out.nodes.find((n) => n.name === "propose")!;
  assert.deepEqual(propose.anchors.map((a) => a.label), ["abandon", "apply", "nothing", "widen"]);
  const ys = propose.anchors.map((a) => a.point.y);
  assert.deepEqual([...ys].sort((a, b) => a - b), ys, "the anchors keep label order down the edge");
  for (const anchor of propose.anchors) {
    assert.equal(anchor.point.x, propose.x + propose.width);
    assert.ok(anchor.point.y > propose.y && anchor.point.y < propose.y + propose.height);
    assert.ok(anchor.labelPoint.x > anchor.point.x, "the label sits at the end of a leader");
  }
  // The labels fan further apart than the node's own edge allows, so that
  // four labels on a 48-pixel box stay legible.
  const gaps = propose.anchors.slice(1).map((a, i) => a.labelPoint.y - propose.anchors[i]!.labelPoint.y);
  for (const gap of gaps) assert.ok(gap >= 12, `label lines are ${gap} apart`);
  const widen = out.edges.find((e) => e.from === "propose" && e.to === "survey")!;
  const anchor = propose.anchors.find((a) => a.label === "widen")!;
  assert.equal(widen.from_.y, anchor.labelPoint.y, "the edge continues from its label");
  assert.ok(widen.from_.x > anchor.labelPoint.x);
});

test("an edge that runs back to an earlier column is routed under the rows", () => {
  const out = layoutWorkflow(run(), WIDTH);
  const widen = out.edges.find((e) => e.from === "propose" && e.to === "survey")!;
  assert.equal(widen.back, true);
  assert.ok(widen.dip > Math.max(...out.nodes.map((n) => n.y + n.height)));
  const forward = out.edges.find((e) => e.from === "apply" && e.to === "verify_change")!;
  assert.equal(forward.back, false);
});

test("a label with no successor becomes a stub of its own", () => {
  const out = layoutWorkflow(run(), WIDTH);
  assert.equal(out.stubs.length, 1);
  const stub = out.stubs[0]!;
  assert.equal(stub.node, "propose");
  assert.equal(stub.label, "nothing");
  assert.equal(stub.to_.x - stub.from_.x, STUB_LENGTH);
  assert.equal(stub.to_.y, stub.from_.y);
});

test("a recovery is placed where the failed node's inputs arrive", () => {
  const out = layoutWorkflow(run(), WIDTH);
  assert.equal(out.recoveries.length, 1);
  const recovery = out.recoveries[0]!;
  const verify = out.nodes.find((n) => n.name === "verify_change")!;
  assert.deepEqual(recovery.at, { x: verify.x, y: verify.y + verify.height / 2 });
  assert.equal(recovery.to_, null, "the retry named the failed node itself");
});
