// Token attribution: the parts one request's input is made of, the replay
// cost of each part over an episode, the split of input into text sent once
// and text resent, and the rule that a request nothing measured contributes
// characters without contributing tokens.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import { byKind, computeAttribution, layoutOrigin, layoutReplayCost, layoutRequestInput } from "../src/attribution.js";
import type { Attribution } from "../src/attribution.js";
import type { StatisticsEpisode } from "../src/statistics.js";
import { fixture } from "./helpers.js";

function episode(name: string, depth = 0): StatisticsEpisode {
  const events = fixture(name);
  const fold = new EpisodeFold(name.replace(".jsonl", ""), { stream: false });
  for (const ev of events) fold.push(ev);
  const s = fold.summary;
  return {
    id: s.id,
    name: s.name,
    events,
    startTime: s.startTime,
    endTime: s.endTime,
    program: s.program,
    depth,
    outcome: s.outcome,
  };
}

function attribution(names: string[]): Attribution {
  return computeAttribution(names.map((name) => episode(name)));
}

test("the parts of a request divide the input its answer reported", () => {
  const out = attribution(["root.jsonl"]);
  for (const request of out.requests) {
    if (request.input === null) continue;
    const sum = request.shares.reduce((total, share) => total + (share.tokens ?? 0), 0);
    assert.ok(Math.abs(sum - request.input) < 1e-9, `${request.requestId} sums to its reported input`);
  }
  const first = out.requests[0]!;
  assert.equal(first.requestId, "rq_01");
  assert.deepEqual(first.shares.map((s) => s.part.kind), ["system", "schemas", "inbox"]);
  assert.equal(first.chars, 523, "the characters of the header and the task, measured");
  assert.equal(first.input, 410);
  assert.ok(Math.abs(first.charsPerToken! - 523 / 410) < 1e-9);
});

test("the system prompt and the tool schemas come from the header the request names", () => {
  const out = attribution(["compact.jsonl"]);
  // The summarization header declares no tool, so the call that uses it
  // carries a system prompt and no schemas at all.
  const summary = out.requests.find((r) => r.requestId === "cmp_0004")!;
  assert.deepEqual(summary.shares.map((s) => s.part.kind), ["system", "summary"]);
  assert.equal(summary.shares[0]!.part.chars, 49, "the summarization prompt's own system text");
  // Its system prompt differs from the dialogue's, so the two are two parts
  // and the second is named by the step that introduced it.
  const dialogue = out.requests[0]!.shares[0]!.part;
  assert.equal(dialogue.chars, 47);
  assert.equal(dialogue.label, "system prompt");
  assert.equal(summary.shares[0]!.part.label, "system prompt · step 4");
});

test("a summarization prompt is its own kind and is never a replay", () => {
  const out = attribution(["compact.jsonl"]);
  const summary = out.parts.find((p) => p.part.kind === "summary")!;
  assert.equal(summary.part.label, "summarization prompt");
  assert.equal(summary.sends, 1);
  const share = out.requests.find((r) => r.compaction)!.shares.find((s) => s.part.kind === "summary")!;
  assert.equal(share.replayed, false);
});

test("a tool result is named by its call and costs its size once per request that carried it", () => {
  const out = attribution(["root.jsonl"]);
  const result = out.parts.find((p) => p.part.label === "read · tc_01")!;
  assert.equal(result.part.kind, "tool");
  assert.equal(result.part.chars, 98);
  assert.equal(result.sends, 4, "the four requests after the call that produced it");
  assert.equal(result.charCost, 98 * 4);
  // One of those four requests went unanswered, so three of the four sends
  // carry a token figure and the total is a floor.
  assert.equal(result.measuredSends, 3);
  assert.equal(result.bounded, true);
});

test("each part names the request that introduced it", () => {
  const out = attribution(["root.jsonl"]);
  const result = out.parts.find((p) => p.part.label === "read · tc_01")!;
  const first = out.requests.find((r) => r.shares.some((s) => s.part === result.part))!;
  assert.equal(result.part.seq, first.requestSeq);
  assert.equal(result.part.episodeId, first.episodeId);
});

test("the parts are ranked by replay cost rather than by size", () => {
  const out = attribution(["root.jsonl"]);
  const labels = out.parts.map((p) => p.part.label);
  assert.equal(labels[0], "tool schemas", "448 characters sent five times outrank every larger single send");
  const costs = out.parts.map((p) => p.tokens ?? -1);
  for (let i = 1; i < costs.length; i += 1) assert.ok(costs[i - 1]! >= costs[i]!, "descending replay cost");
  const schemas = out.parts[0]!;
  const turn = out.parts.find((p) => p.part.label === "assistant turn 2")!;
  assert.ok(schemas.part.chars < turn.part.chars * 4, "the schemas are not the largest part");
  assert.ok(schemas.tokens! > turn.tokens!, "and are still the costliest, because they are resent most");
});

test("unique and replayed input add to the input the answers reported", () => {
  const out = attribution(["root.jsonl"]);
  assert.equal(out.input, 410 + 520 + 610 + 680);
  assert.ok(out.unique !== null && out.replayed !== null);
  assert.ok(Math.abs(out.unique! + out.replayed! - out.input!) < 1e-9);
  assert.ok(out.unique! > 0 && out.replayed! > 0);
});

test("the split of the input is the difference between consecutive reported counts", () => {
  // Every request of this log carries everything the request before it
  // carried, so the text new to each one cost exactly the difference
  // between the two counts the provider reported.
  const out = attribution(["root.jsonl"]);
  assert.equal(out.originDerived, 0, "nothing had to be apportioned");
  assert.equal(out.unique, 410 + (520 - 410) + (610 - 520) + (680 - 610));
  assert.equal(out.replayed, 0 + 410 + 520 + 610);
});

test("a compaction drops text, so the requests it breaks are apportioned instead", () => {
  const out = attribution(["compact.jsonl"]);
  // The summarization call carries neither the schemas nor the transcript
  // as messages, and the request after it carries neither the summarization
  // prompt nor the tool results the summary replaced.
  assert.equal(out.originDerived, 2);
  assert.ok(Math.abs(out.unique! + out.replayed! - out.input!) < 1e-9);
});

test("cache reads are totalled beside the input and never inside it", () => {
  const out = attribution(["root.jsonl"]);
  assert.equal(out.cacheRead, 0 + 400 + 520 + 600);
  assert.ok(out.cacheRead! < out.input!, "a cached token is still an input token");
});

test("a request no answer measured contributes characters and no tokens", () => {
  const out = attribution(["root.jsonl"]);
  const retried = out.requests.find((r) => r.requestId === "rq_02")!;
  assert.equal(retried.input, null);
  assert.equal(retried.cacheRead, null);
  assert.equal(retried.charsPerToken, null);
  assert.ok(retried.chars > 0);
  for (const share of retried.shares) assert.equal(share.tokens, null);
  assert.equal(out.unmeasured, 1);
  assert.equal(out.bounded, true, "every token total in this scope is a floor");
});

test("an unmeasured attempt does not turn its retry's text into a replay", () => {
  const out = attribution(["root.jsonl"]);
  // rq_02 and rq_03 are two attempts at one step and carry the same list.
  // Nothing measured rq_02, so the text is first billed at rq_03 and the
  // tokens of that request belong to the unique share.
  const retry = out.requests.find((r) => r.requestId === "rq_03")!;
  const result = retry.shares.find((s) => s.part.label === "read · tc_01")!;
  assert.equal(result.replayed, false);
  const later = out.requests.find((r) => r.requestId === "rq_04")!;
  assert.equal(later.shares.find((s) => s.part.label === "read · tc_01")!.replayed, true);
});

test("a scope whose answers reported no usage has characters and no token figure", () => {
  const out = attribution(["retries-exhausted.jsonl"]);
  assert.equal(out.requests.length, 5);
  assert.equal(out.input, null, "absent rather than zero");
  assert.equal(out.cacheRead, null);
  assert.equal(out.unique, null);
  assert.equal(out.replayed, null);
  assert.equal(out.originDerived, 0, "no split was apportioned, because none was computed at all");
  assert.equal(out.unmeasured, 5);
  assert.equal(out.chars, 3928 * 5);
  for (const part of out.parts) {
    assert.equal(part.tokens, null);
    assert.equal(part.measuredSends, 0);
    assert.equal(part.bounded, true);
  }
  assert.deepEqual(out.parts.map((p) => p.part.label), ["tool schemas", "system prompt", "task"]);
});

test("two episodes hold two conversations, so neither replays the other's text", () => {
  const out = computeAttribution([episode("root.jsonl"), episode("child.jsonl", 1)]);
  const prompts = out.parts.filter((p) => p.part.kind === "system");
  assert.ok(prompts.length >= 2, "one system prompt per episode, however alike the text");
  for (const request of out.requests.filter((r) => r.episodeId !== out.requests[0]!.episodeId)) {
    const system = request.shares.find((s) => s.part.kind === "system")!;
    assert.equal(system.part.episodeId, request.episodeId);
  }
});

test("input grouped by where it came from covers every part exactly once", () => {
  const out = attribution(["root.jsonl"]);
  const kinds = byKind(out);
  assert.deepEqual(kinds.map((k) => k.kind), ["system", "schemas", "inbox", "assistant", "tool"]);
  assert.equal(kinds.reduce((sum, k) => sum + k.chars, 0), out.chars);
  const tokens = kinds.reduce((sum, k) => sum + (k.tokens ?? 0), 0);
  assert.ok(Math.abs(tokens - out.input!) < 1e-9);
});

test("the request bars are as long as their requests are large", () => {
  const out = attribution(["root.jsonl"]);
  const bars = layoutRequestInput(out, 300);
  assert.equal(bars.length, out.requests.length);
  const largest = Math.max(...out.requests.map((r) => r.chars));
  const longest = bars.find((b) => b.request.chars === largest)!;
  assert.ok(Math.abs(longest.w - 300) < 1e-9, "the largest request fills the width");
  for (const bar of bars) {
    const last = bar.segments[bar.segments.length - 1]!;
    assert.ok(Math.abs(last.x + last.w - bar.w) < 1e-9, "the segments cover the bar");
    for (let i = 1; i < bar.segments.length; i += 1) {
      assert.ok(Math.abs(bar.segments[i]!.x - (bar.segments[i - 1]!.x + bar.segments[i - 1]!.w)) < 1e-9);
    }
  }
});

test("the replay-cost bars fall back to characters where no token figure exists", () => {
  const measured = layoutReplayCost(attribution(["root.jsonl"]), 200, 3);
  assert.equal(measured.length, 3);
  assert.equal(measured[0]!.measure, measured[0]!.total.tokens);
  assert.ok(Math.abs(measured[0]!.w - 200) < 1e-9);
  const absent = layoutReplayCost(attribution(["retries-exhausted.jsonl"]), 200, 10);
  assert.equal(absent[0]!.measure, absent[0]!.total.charCost, "characters, which every request has");
  assert.ok(Math.abs(absent[0]!.w - 200) < 1e-9);
});

test("the origin bar is absent where no answer reported an input count", () => {
  assert.equal(layoutOrigin(attribution(["retries-exhausted.jsonl"]), 200), null);
  const shares = layoutOrigin(attribution(["root.jsonl"]), 200)!;
  assert.deepEqual(shares.map((s) => s.name), ["unique", "replayed"]);
  assert.ok(Math.abs(shares[0]!.w + shares[1]!.w - 200) < 1e-9);
  assert.ok(Math.abs(shares[0]!.fraction + shares[1]!.fraction - 1) < 1e-9);
  assert.equal(shares[0]!.x, 0);
  assert.ok(Math.abs(shares[1]!.x - shares[0]!.w) < 1e-9);
});
