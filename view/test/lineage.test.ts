// The forest built from a set of summaries: what hangs under what, the
// order roots stand in, and the spans of rows that are runs of one program.

import { strict as assert } from "node:assert";
import test from "node:test";

import { EpisodeFold } from "../src/fold.js";
import { buildTree, flatten, programRuns, shortIdentity } from "../src/lineage.js";
import type { Summary } from "../src/fold.js";
import { fixture } from "./helpers.js";

function summary(name: string): Summary {
  const fold = new EpisodeFold(name.replace(".jsonl", ""), { stream: false });
  for (const event of fixture(name)) fold.push(event);
  return fold.summary;
}

/** The flattened rows as `programRuns` reads them. */
function rows(...names: string[]) {
  return flatten(buildTree(names.map(summary))).map(({ node, depth }) => ({
    id: node.id,
    identity: node.summary.identity,
    name: node.summary.name,
    depth,
  }));
}

test("a set of unrelated logs yields one root each", () => {
  const list = rows("root.jsonl", "compact.jsonl", "overlap-parent.jsonl");
  assert.deepEqual(
    list.map((r) => r.depth),
    [0, 0, 0],
  );
  assert.deepEqual(new Set(list.map((r) => r.id)), new Set(["ep_root", "ep_compact", "ep_over_parent"]));
});

test("a spawned child and a forked child hang under the episode they came from", () => {
  const list = rows("root.jsonl", "child.jsonl", "fork.jsonl");
  assert.deepEqual(
    list.map((r) => [r.id, r.depth]),
    [
      ["ep_root", 0],
      ["ep_child", 1],
      ["ep_fork", 1],
    ],
  );
});

test("roots of one program stand together, whatever came between them", () => {
  // `ep_compact` and `ep_over_parent` carry one identity and started 67
  // days apart; `ep_root` started between them and ran another program.
  const list = rows("compact.jsonl", "root.jsonl", "overlap-parent.jsonl", "overlap-child.jsonl");
  const roots = list.filter((r) => r.depth === 0).map((r) => r.id);
  assert.deepEqual(roots, ["ep_root", "ep_compact", "ep_over_parent"]);
  // A root's descendants still follow it.
  assert.deepEqual(
    list.map((r) => r.id),
    ["ep_root", "ep_compact", "ep_over_parent", "ep_over_child"],
  );
});

test("a program with more than one root spans its rows and its descendants", () => {
  const list = rows("compact.jsonl", "root.jsonl", "overlap-parent.jsonl", "overlap-child.jsonl");
  const runs = programRuns(list);
  assert.equal(runs.length, 1, "only the identity two roots share is a group");
  const run = runs[0]!;
  assert.equal(run.identity, "sha256:cccc");
  assert.equal(run.runs, 2);
  assert.equal(list[run.first]!.id, "ep_compact");
  assert.equal(list[run.last]!.id, "ep_over_child", "the last row of the group is the last root's descendant");
});

test("one root of a program is no group, and a root without a start is no group", () => {
  assert.deepEqual(programRuns(rows("root.jsonl", "compact.jsonl")), []);
  const unread = [
    { identity: "", name: "a", depth: 0 },
    { identity: "", name: "b", depth: 0 },
  ];
  assert.deepEqual(programRuns(unread), [], "an episode whose start has not been read groups with nothing");
});

test("an identity reads as the first eight characters of its digest", () => {
  assert.equal(shortIdentity("sha256:14db506b616181ce"), "14db506b…");
  assert.equal(shortIdentity("sha256:aaaa"), "aaaa");
  assert.equal(shortIdentity(""), "");
});
