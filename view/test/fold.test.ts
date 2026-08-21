// The episode fold: rows for the conversation pane, the summary for the tree
// pane, and the lineage helpers, against the fixture logs.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import type { AssistantRow, CompactionRow, NoteRow, Row, ToolRow, UserRow } from "../src/fold.js";
import { buildTree, flatten, sharedPrefix } from "../src/lineage.js";
import { fixture } from "./helpers.js";

function fold(name: string, stream = false): EpisodeFold {
  const f = new EpisodeFold(name.replace(".jsonl", ""), { stream });
  for (const ev of fixture(name)) f.push(ev);
  return f;
}

function rows<T extends Row>(f: EpisodeFold, kind: T["kind"]): T[] {
  return f.rows.filter((r) => r.kind === kind) as T[];
}

test("a stream still open when the episode ends is interrupted rather than live", () => {
  // Recorded from a run whose five attempts each failed in transport, so
  // every request wrote chunks and none reached an assistant/message.
  const f = fold("retries-exhausted.jsonl", true);
  const turns = rows<AssistantRow>(f, "assistant");
  assert.equal(turns.length, 5, "one row per request that streamed");
  assert.equal(f.summary.outcome?.kind, "blocked");
  for (const turn of turns) {
    assert.equal(turn.streaming, false, "an ended episode has no stream still arriving");
    assert.equal(turn.interrupted, true, "a stream cut off by the end of the episode is interrupted");
  }
});

test("closing a cut-off stream emits an update for each row it changes", () => {
  const f = new EpisodeFold("ep_retries", { stream: true });
  const events = fixture("retries-exhausted.jsonl");
  const end = events[events.length - 1]!;
  assert.equal(end.type, "episode/end");
  for (const ev of events.slice(0, -1)) f.push(ev);
  assert.equal(rows<AssistantRow>(f, "assistant").every((r) => r.streaming), true, "still arriving before the end");
  const patches = f.push(end);
  const updated = patches.filter((p) => p.op === "update").map((p) => p.row.key);
  assert.equal(updated.length, 5);
  assert.equal(new Set(updated).size, 5, "one update per streaming row");
  assert.equal(patches.some((p) => p.op === "append" && p.row.kind === "note"), true, "the outcome row still appends");
});

test("a stream that is answered is not marked interrupted when the episode ends", () => {
  const f = fold("root.jsonl", true);
  const turns = rows<AssistantRow>(f, "assistant");
  assert.equal(f.summary.outcome?.kind, "completed");
  assert.equal(turns.some((t) => t.streaming), false);
  // Only the turn the log itself records as interrupted carries the mark.
  assert.deepEqual(turns.map((t) => t.interrupted), [false, false, true, false]);
});

test("summary carries lineage, budget, usage, and sandbox from the log", () => {
  const s = fold("root.jsonl").summary;
  assert.equal(s.id, "ep_root");
  assert.equal(s.name, "fix-test");
  assert.equal(s.parentId, null);
  assert.equal(s.forkOrigin, null);
  assert.equal(s.modelCalls, 5, "every model/request counts, including the retried attempt");
  assert.equal(s.retries, 1);
  assert.deepEqual(s.budget, { modelCalls: 10, tokens: 100000 });
  assert.equal(s.usage.input, 410 + 520 + 610 + 680);
  assert.equal(s.usage.output, 28 + 31 + 14 + 9);
  assert.equal(s.usage.cacheRead, 400 + 520 + 600);
  assert.deepEqual(s.sandbox, { mode: "best-effort", landlockAbi: 7 });
  assert.equal(s.outcome?.kind, "completed");
  assert.deepEqual([...s.children.keys()], ["ep_child"]);
  assert.equal(s.roster.get("ep_child")?.phase, "active");
});

test("static fold ignores chunks and renders the assembled message", () => {
  const f = fold("root.jsonl");
  const assistants = rows<AssistantRow>(f, "assistant");
  assert.equal(assistants.length, 4);
  assert.ok(assistants.every((a) => !a.streaming));
  assert.equal(assistants[0]!.text, "I will read the test first.");
  assert.equal(assistants[0]!.toolCalls[0]!.name, "read");
});

test("streaming fold builds a row from chunks and replaces it with the message", () => {
  const f = new EpisodeFold("ep_root", { stream: true });
  const events = fixture("root.jsonl");
  const firstMessage = events.findIndex((e) => e.type === "assistant/message");
  let appended = 0;
  let updated = 0;
  for (const ev of events.slice(0, firstMessage)) {
    for (const p of f.push(ev)) {
      if (p.row.kind !== "assistant") continue;
      if (p.op === "append") appended++;
      else updated++;
    }
  }
  assert.equal(appended, 1, "the first chunk appends the row");
  assert.equal(updated, 4, "later chunks update it in place");
  const live = rows<AssistantRow>(f, "assistant")[0]!;
  assert.equal(live.streaming, true);
  assert.equal(live.text, "I will read the test first.");
  assert.deepEqual(live.toolCalls[0]!.args, { path: "tests/parser_test.py" });
  const patches = f.push(events[firstMessage]!);
  assert.equal(patches.length, 1);
  assert.equal(patches[0]!.op, "update");
  const done = rows<AssistantRow>(f, "assistant");
  assert.equal(done.length, 1, "the message replaces the streamed row rather than adding one");
  assert.equal(done[0]!.streaming, false);
  assert.equal(done[0]!.usage?.input, 410);
});

test("interrupted messages and synthetic results are marked", () => {
  const f = fold("root.jsonl");
  const interrupted = rows<AssistantRow>(f, "assistant").filter((a) => a.interrupted);
  assert.equal(interrupted.length, 1);
  assert.equal(interrupted[0]!.stop, "interrupted");
  const synthetic = rows<ToolRow>(f, "tool").filter((t) => t.synthetic);
  assert.equal(synthetic.length, 1);
  assert.equal(synthetic[0]!.isError, true);
  assert.equal(synthetic[0]!.callId, "tc_03");
});

test("inbox items carry their source, and peer items their sender", () => {
  const root = rows<UserRow>(fold("root.jsonl"), "user").map((u) => u.source);
  assert.deepEqual(root, ["task", "system", "child"]);
  const peer = rows<UserRow>(fold("child.jsonl"), "user").find((u) => u.source === "peer")!;
  assert.equal(peer.from, "ep_root");
  assert.equal(peer.messageId, "tm_01");
});

test("spawn, team, budget, retry, and sandbox events become compact rows", () => {
  const f = fold("root.jsonl");
  const notes = rows<NoteRow>(f, "note");
  const byType = (t: string) => notes.filter((n) => n.type === t);
  assert.equal(byType("spawn/start").length, 1);
  assert.equal(byType("spawn/start")[0]!.link, "ep_child");
  assert.equal(byType("spawn/end")[0]!.level, "completed");
  assert.equal(byType("team/roster").length, 2);
  assert.equal(byType("team/message").length, 1);
  assert.equal(byType("team/delivered").length, 1);
  assert.equal(byType("budget/reserve").length, 1);
  assert.equal(byType("budget/release").length, 1);
  assert.equal(byType("request/retry").length, 1);
  assert.equal(byType("sandbox/denied")[0]!.level, "error");
  assert.equal(byType("episode/end")[0]!.level, "completed");
});

test("reserved and unknown event types render as generic rows and never throw", () => {
  const f = fold("child.jsonl");
  const reserved = rows<NoteRow>(f, "note").find((n) => n.type === "workflow/node-end")!;
  assert.equal(reserved.label, "workflow/node-end");
  assert.match(reserved.detail, /node=survey/);
  const g = new EpisodeFold("x", { stream: false });
  const patches = g.push({ seq: 0, time: 1, type: "future/unknown", data: { deep: { nested: true }, n: 3 } });
  assert.equal(patches.length, 1);
  assert.equal(patches[0]!.row.kind, "note");
  assert.doesNotThrow(() => g.push({ seq: 1, time: 1, type: "assistant/message", data: { tool_calls: "not an array" } }));
  assert.doesNotThrow(() => g.push({ seq: 2, time: 1, type: "episode/end", data: { outcome: 42 } }));
  assert.doesNotThrow(() => g.push({ seq: 3, time: 1, type: "tool/result", data: null as unknown as Record<string, unknown> }));
});

test("events at or below the last seq are ignored, so a replay after reconnect is safe", () => {
  const f = new EpisodeFold("ep_root", { stream: false });
  const events = fixture("root.jsonl");
  for (const ev of events) f.push(ev);
  const before = f.rows.length;
  for (const ev of events.slice(0, 5)) assert.deepEqual(f.push(ev), []);
  assert.equal(f.rows.length, before);
  assert.equal(f.summary.modelCalls, 5);
});

test("the fork fixture records its origin and seed boundary", () => {
  const f = fold("fork.jsonl");
  assert.deepEqual(f.summary.forkOrigin, { episodeId: "ep_root", seq: 12 });
  assert.equal(f.summary.seedEnd, 12);
  assert.equal(f.summary.outcome?.kind, "blocked");
  assert.equal((f.summary.outcome as { code: string }).code, "missing-capability");
});

test("a compaction becomes a row carrying the summary and the count of messages it replaced", () => {
  const f = fold("compact.jsonl");
  const rows_ = rows<CompactionRow>(f, "compaction");
  assert.equal(rows_.length, 1);
  const c = rows_[0]!;
  assert.equal(c.step, 4);
  assert.equal(c.firstKeptSeq, 9);
  assert.equal(c.summarized, 5, "the task, two assistant turns, and two results lie in the covered span");
  assert.match(c.summary, /^## Goal\n/);
  assert.match(c.continuation, /^## Continuation state\n\ncovered: seq 1 to 8\n/);
  const notes = rows<NoteRow>(f, "note");
  assert.equal(notes.filter((n) => n.type === "compaction/start").length, 1);
  assert.match(notes.find((n) => n.type === "compaction/start")!.detail, /covering seq 1–8/);
  assert.equal(notes.find((n) => n.type === "compaction/end")!.level, "info");
  assert.equal(f.summary.modelCalls, 5, "the summarization request counts as a model call");
});

test("the tree hangs spawned children under parent_id and forks under fork_origin", () => {
  const summaries = ["root.jsonl", "child.jsonl", "fork.jsonl"].map((n) => fold(n).summary);
  const roots = buildTree(summaries);
  assert.equal(roots.length, 1);
  const flat = flatten(roots);
  assert.deepEqual(
    flat.map((f) => [f.node.id, f.depth, f.node.fork]),
    [
      ["ep_root", 0, false],
      ["ep_child", 1, false],
      ["ep_fork", 1, true],
    ],
  );
});

test("sharedPrefix is the seed boundary for a fork and its origin, and the smaller boundary for two forks", () => {
  const map = new Map(["root.jsonl", "child.jsonl", "fork.jsonl"].map((n) => fold(n).summary).map((s) => [s.id, s]));
  assert.equal(sharedPrefix("ep_root", "ep_fork", map), 12);
  assert.equal(sharedPrefix("ep_fork", "ep_root", map), 12);
  assert.equal(sharedPrefix("ep_root", "ep_child", map), 0, "a spawned child shares no log prefix");
  const second = { ...map.get("ep_fork")!, id: "ep_fork2", forkOrigin: { episodeId: "ep_root", seq: 27 } };
  map.set("ep_fork2", second);
  assert.equal(sharedPrefix("ep_fork", "ep_fork2", map), 12);
  const nested = { ...map.get("ep_fork")!, id: "ep_fork3", forkOrigin: { episodeId: "ep_fork", seq: 15 } };
  map.set("ep_fork3", nested);
  assert.equal(sharedPrefix("ep_fork3", "ep_root", map), 12, "a fork of a fork shares the shorter boundary with the root");
  assert.equal(sharedPrefix("ep_fork3", "ep_fork", map), 15);
});
