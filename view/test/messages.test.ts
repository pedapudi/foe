// The derived-messages rule (docs/log-format.md, "Derived messages") against
// the fixture logs. Every `model/request` in a fixture records the list the
// runtime would send; the recomputed list must match it exactly.

import assert from "node:assert/strict";
import { test } from "node:test";
import { deriveAllRequests, deriveMessages } from "../src/messages.js";
import { fixture } from "./helpers.js";

for (const name of ["root.jsonl", "child.jsonl", "fork.jsonl"]) {
  test(`recomputed messages equal model/request.messages in ${name}`, () => {
    const events = fixture(name);
    const requests = deriveAllRequests(events);
    assert.ok(requests.length > 0, "fixture has at least one request");
    for (const r of requests) {
      assert.deepEqual(r.derived, r.recorded, `request at seq ${r.seq}`);
    }
  });
}

test("rule 3: consecutive consumed inbox items merge into one user message", () => {
  const events = fixture("child.jsonl");
  const first = events.find((e) => e.type === "model/request")!;
  const messages = deriveMessages(events, first.seq);
  assert.equal(messages.length, 1);
  const user = messages[0]!;
  assert.equal(user.role, "user");
  if (user.role === "user") {
    assert.equal(user.content.length, 2, "task and peer item concatenated");
    assert.equal(user.content[1]!.text, "Report the count when done.");
  }
});

test("rule 5: an interrupted assistant message keeps its text prefix and recorded tool calls", () => {
  const events = fixture("root.jsonl");
  const interrupted = events.find((e) => e.type === "assistant/message" && e.data.interrupted === true)!;
  const next = events.find((e) => e.type === "model/request" && e.seq > interrupted.seq)!;
  const messages = deriveMessages(events, next.seq);
  const assistant = messages.filter((m) => m.role === "assistant").at(-1)!;
  assert.equal(assistant.role, "assistant");
  if (assistant.role === "assistant") {
    assert.equal(assistant.text, "Running the test now.");
    assert.equal(assistant.tool_calls.length, 1);
    assert.equal(assistant.tool_calls[0]!.id, "tc_03");
  }
});

test("rule 6: a synthetic error result becomes a tool message carrying rendered", () => {
  const events = fixture("root.jsonl");
  const last = events.filter((e) => e.type === "model/request").at(-1)!;
  const messages = deriveMessages(events, last.seq);
  const tool = messages.filter((m) => m.role === "tool").at(-1)!;
  assert.equal(tool.role, "tool");
  if (tool.role === "tool") {
    assert.equal(tool.call_id, "tc_03");
    assert.equal(tool.is_error, true);
    assert.match(tool.rendered, /interrupted/);
  }
});

test("rule 7: retries, budget, spawn, team, and sandbox events contribute nothing", () => {
  const events = fixture("root.jsonl");
  const last = events.filter((e) => e.type === "model/request").at(-1)!;
  const messages = deriveMessages(events, last.seq);
  const roles = new Set(messages.map((m) => m.role));
  assert.deepEqual([...roles].sort(), ["assistant", "tool", "user"]);
  const assistantCount = events.filter((e) => e.type === "assistant/message" && e.seq < last.seq).length;
  assert.equal(messages.filter((m) => m.role === "assistant").length, assistantCount);
});

test("an inbox item enters at the position of the request that consumed it", () => {
  const events = fixture("root.jsonl");
  // The system warning (seq 11) arrives after the first tool result and is
  // consumed by the step 2 request, so it follows that result.
  const step2 = events.find((e) => e.type === "model/request" && e.data.step === 2)!;
  const messages = deriveMessages(events, step2.seq);
  assert.deepEqual(
    messages.map((m) => m.role),
    ["user", "assistant", "tool", "user"],
  );
});

test("a fork's seeded prefix derives the same messages as the origin", () => {
  const root = fixture("root.jsonl");
  const fork = fixture("fork.jsonl");
  const origin = fork[0]!.data.fork_origin as { episode_id: string; seq: number };
  assert.equal(origin.episode_id, "ep_root");
  const rootFirst = root.find((e) => e.type === "model/request")!;
  const forkFirst = fork.find((e) => e.type === "model/request")!;
  assert.deepEqual(deriveMessages(fork, forkFirst.seq), deriveMessages(root, rootFirst.seq));
  assert.equal(fork.find((e) => e.type === "seed/end")!.seq, origin.seq);
});
