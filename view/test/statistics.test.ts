// The statistics: every quantity's arithmetic, the rule that an
// unobserved quantity is absent rather than zero, and the placement of the
// three figures the dashboard draws.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import {
  computeStatistics,
  layoutContextCurve,
  layoutTools,
  layoutWallClock,
} from "../src/statistics.js";
import type { Statistics, StatisticsEpisode } from "../src/statistics.js";
import { fixture } from "./helpers.js";

/** One fixture log as the statistics read it. */
function episode(name: string, depth = 0): StatisticsEpisode {
  const events = fixture(name);
  const fold = new EpisodeFold(name.replace(".jsonl", ""), { stream: false });
  for (const ev of events) fold.push(ev);
  const s = fold.summary;
  return { id: s.id, name: s.name, events, startTime: s.startTime, endTime: s.endTime, program: s.program, depth };
}

function stats(names: [string, number][]): Statistics {
  return computeStatistics(names.map(([name, depth]) => episode(name, depth)), 0);
}

test("each step carries the interval from its request to its first token", () => {
  const out = stats([["compact.jsonl", 0]]);
  const dialogue = out.steps.filter((s) => !s.compaction);
  assert.deepEqual(dialogue.map((s) => s.requestId), ["rq_0001", "rq_0002", "rq_0003", "rq_0005"]);
  // The compaction fixture records chunks for the summarization call
  // alone, so every dialogue step has no measured first token and the
  // interval is absent rather than zero.
  for (const step of dialogue) assert.equal(step.timeToFirstToken, null);
  assert.equal(out.steps.find((s) => s.compaction)!.timeToFirstToken, 10);
  const root = stats([["root.jsonl", 0]]);
  const first = root.steps[0]!;
  assert.equal(first.timeToFirstToken, 10, "the first chunk arrived one event after the request");
  assert.ok(first.latencyMs !== null && first.latencyMs > first.timeToFirstToken!);
});

test("total latency runs from the request to its assembled message", () => {
  const out = stats([["compact.jsonl", 0]]);
  const step = out.steps.find((s) => s.requestId === "rq_0001")!;
  assert.equal(step.latencyMs, 10);
  assert.equal(step.input, 900);
  assert.equal(step.output, 12);
  assert.equal(step.outputRate, 12 / 0.01, "output tokens over the total latency in seconds");
});

test("a request that no message answered has no latency and no rate", () => {
  const out = stats([["root.jsonl", 0]]);
  // The root fixture retries step 2: the first attempt is answered by no
  // message at all, so every quantity that needs one is absent.
  const retried = out.steps.find((s) => s.step === 2 && s.attempt === 1)!;
  assert.equal(retried.latencyMs, null);
  assert.equal(retried.outputRate, null);
  assert.equal(retried.input, null);
  assert.ok(retried.spanMs !== null, "its span still ends at the last event it produced");
});

test("a compaction's own call is marked and still counts against the budget", () => {
  const out = stats([["compact.jsonl", 0]]);
  const compaction = out.steps.filter((s) => s.compaction);
  assert.equal(compaction.length, 1);
  assert.equal(compaction[0]!.requestId, "cmp_0004");
  assert.equal(out.requests, 5, "every model/request is a model call");
  // 900 + 1400 + 1900 + 700 + 640 in, 12 + 10 + 30 + 80 + 4 out.
  assert.equal(out.tokens.input, 5540);
  assert.equal(out.tokens.output, 136);
  const calls = out.limits.find((l) => l.key === "model_calls")!;
  assert.equal(calls.used, 5);
  assert.equal(calls.limit, 10);
});

test("the cache hit rate keeps the two counts it came from", () => {
  const out = stats([["compact.jsonl", 0]]);
  assert.deepEqual(out.cache, { read: 2300, input: 5540, rate: 2300 / 5540 });
});

test("a run that measured no cache read has no hit rate", () => {
  const events = fixture("compact.jsonl").map((ev) =>
    ev.type === "assistant/message"
      ? { ...ev, data: { ...ev.data, usage: { input: 100, output: 10 } } }
      : ev,
  );
  const one = episode("compact.jsonl");
  const out = computeStatistics([{ ...one, events }], 0);
  assert.equal(out.cache, null, "absent rather than a hit rate of zero");
  assert.equal(out.tokens.cacheRead, null);
  assert.equal(out.tokens.input, 500);
});

test("the wall clock divides into model time, tool time, and retry backoff", () => {
  const out = stats([["root.jsonl", 0]]);
  const clock = out.wallClock;
  assert.equal(clock.backoffMs, 500, "the one retry asked for 500 ms");
  assert.equal(clock.toolMs, out.tools.reduce((sum, t) => sum + t.durationMs, 0));
  assert.equal(clock.modelMs, out.steps.reduce((sum, s) => sum + (s.spanMs ?? 0), 0));
  assert.equal(clock.otherMs, clock.totalMs - clock.modelMs - clock.toolMs - clock.backoffMs);
  assert.equal(clock.concurrent, false);
});

test("episodes that overlap push the measured shares past the wall clock", () => {
  const out = stats([["overlap-parent.jsonl", 0], ["overlap-child.jsonl", 1]]);
  assert.equal(out.episodes.length, 2);
  assert.equal(out.wallClock.concurrent, true);
  assert.equal(out.wallClock.otherMs, 0, "there is no unaccounted time to report");
  const bar = layoutWallClock(out.wallClock, 200);
  const measured = out.wallClock.modelMs + out.wallClock.toolMs + out.wallClock.backoffMs;
  assert.equal(bar.divisor, measured, "the bar divides by the sum rather than by the clock");
  assert.ok(Math.abs(bar.shares.reduce((sum, s) => sum + s.w, 0) - 200) < 1e-9);
});

test("tools are grouped by name with their call count and total duration", () => {
  const out = stats([["compact.jsonl", 0]]);
  assert.deepEqual(out.tools, [
    { name: "grep", calls: 1, durationMs: 9, errors: 0 },
    { name: "edit", calls: 1, durationMs: 5, errors: 0 },
    { name: "read", calls: 1, durationMs: 3, errors: 0 },
  ]);
  const bars = layoutTools(out.tools, 100);
  assert.equal(bars[0]!.w, 100, "the longest total sets the scale");
  assert.equal(bars[2]!.w, (3 / 9) * 100);
});

test("a limit the program does not declare is left out", () => {
  const out = stats([["compact.jsonl", 0]]);
  assert.deepEqual(out.limits.map((l) => l.key), ["model_calls", "tokens"]);
  const tokens = out.limits.find((l) => l.key === "tokens")!;
  assert.equal(tokens.limit, 100000);
  assert.equal(tokens.used, 5540 + 136);
});

test("the scope rolls every quantity up across the episode tree", () => {
  const alone = stats([["workflow.jsonl", 0]]);
  const tree = stats([
    ["workflow.jsonl", 0],
    ["workflow-propose-1.jsonl", 1],
    ["workflow-propose-2.jsonl", 1],
    ["workflow-apply-1.jsonl", 1],
  ]);
  assert.equal(alone.episodes.length, 1);
  assert.equal(tree.episodes.length, 4);
  assert.equal(alone.requests, 1, "the workflow episode's own request is its recovery decision");
  assert.equal(tree.requests, 5, "the three child episodes add four more");
  assert.ok(tree.tokens.input! > alone.tokens.input!);
  assert.ok(tree.tools.length > alone.tools.length);
  assert.equal(tree.wallClock.totalMs, alone.wallClock.totalMs, "the scope's root sets the wall clock");
  const depth = tree.limits.find((l) => l.key === "max_depth");
  if (depth) assert.equal(depth.used, 1, "the children are one level below the root");
});

test("the context curve places one series per episode against the declared limit", () => {
  const out = stats([["compact.jsonl", 0]]);
  const names = new Map(out.episodes.map((id) => [id, id]));
  const curve = layoutContextCurve(out, names, 400, 160);
  assert.equal(curve.series.length, 1);
  const points = curve.series[0]!.points;
  assert.deepEqual(points.map((p) => p.input), [900, 1400, 1900, 640], "the compaction call is left out");
  assert.equal(points[0]!.x, curve.plot.left);
  assert.equal(points[points.length - 1]!.x, curve.plot.right);
  assert.equal(curve.peak, 1900);
  assert.equal(curve.peakY, points[2]!.y, "the axis labels the peak where the peak sits");
  assert.ok(curve.budget, "the program declares a token limit");
  assert.equal(curve.budget!.tokens, 100000);
  assert.equal(curve.budget!.y, curve.plot.top, "the declared limit sets the top of the plot");
  // A larger input sits higher, because y grows downwards.
  assert.ok(points[2]!.y < points[0]!.y);
});

test("with no declared token limit the curve scales to its own peak", () => {
  const one = episode("root.jsonl");
  const out = computeStatistics([{ ...one, program: { ...one.program, budget: { model_calls: 10 } } }], 0);
  const names = new Map(out.episodes.map((id) => [id, id]));
  const curve = layoutContextCurve(out, names, 400, 160);
  assert.equal(curve.budget, null);
  const highest = curve.series[0]!.points.reduce((a, b) => (a.input > b.input ? a : b));
  assert.equal(highest.y, curve.plot.top);
});

test("the layouts are pure functions of what they are given", () => {
  const out = stats([["compact.jsonl", 0]]);
  const names = new Map(out.episodes.map((id) => [id, id]));
  assert.deepEqual(layoutContextCurve(out, names, 400, 160), layoutContextCurve(out, names, 400, 160));
  assert.deepEqual(layoutWallClock(out.wallClock, 300), layoutWallClock(out.wallClock, 300));
  assert.deepEqual(layoutTools(out.tools, 90), layoutTools(out.tools, 90));
});
