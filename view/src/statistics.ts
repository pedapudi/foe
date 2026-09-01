// Every quantity the statistics view shows, derived from events the log
// already carries. The module holds no element and reads no document, so
// the arithmetic is tested directly and render/statistics.ts draws what
// this returns.
//
// Two rules govern the result. A quantity the log never measured is null
// rather than zero, because zero asserts a measurement: a run with no cache
// read at all has no hit rate. And every figure a reader could not derive
// by eye carries the values it came from, so each result keeps its parts
// beside its total.

import { num, obj, str } from "./types.js";
import type { LogEvent, Outcome } from "./types.js";

/** One episode as the statistics read it. */
export interface StatisticsEpisode {
  id: string;
  name: string;
  events: LogEvent[];
  startTime: number;
  /** Absent while the episode runs. */
  endTime: number | null;
  /** `episode/start.contract`, whose `budget` declares the limits. */
  contract: Record<string, unknown>;
  /** Depth below the scope's own root, which is 0 for that root. */
  depth: number;
  /** The outcome once `episode/end` is read, which the run table names. */
  outcome: Outcome | null;
}

/** One model request and the answer it received. */
export interface Step {
  episodeId: string;
  step: number;
  attempt: number;
  requestId: string;
  requestSeq: number;
  requestTime: number;
  /**
   * True for a compaction's own summarization call, whose `request_id`
   * starts with `cmp_`. It spends from the same budget as a step of the
   * dialogue and its input is the summarization prompt rather than the
   * context, so the totals count it and the context curve leaves it out.
   */
  compaction: boolean;
  /**
   * Milliseconds from `model/request` to the first `assistant/chunk` of
   * that request that carries output. A request answered only by an error
   * produced no token, so this is absent for it.
   */
  timeToFirstToken: number | null;
  /** Milliseconds from `model/request` to its `assistant/message`. */
  latencyMs: number | null;
  /** Milliseconds from `model/request` to the last event it produced. */
  spanMs: number | null;
  input: number | null;
  output: number | null;
  cacheRead: number | null;
  /** Output tokens per second over the total latency. */
  outputRate: number | null;
}

export interface ToolGroup {
  name: string;
  calls: number;
  /** Total of the `duration_ms` the results report. */
  durationMs: number;
  errors: number;
}

/** Where the wall clock went, in milliseconds. */
export interface WallClock {
  /** From the scope root's `episode/start` to its end, or to the clock now. */
  totalMs: number;
  /** Time inside model requests, from each request to the answer it got. */
  modelMs: number;
  /** Total of the `duration_ms` every tool result reports. */
  toolMs: number;
  /** Total of the `delay_ms` every `request/retry` reports. */
  backoffMs: number;
  /** Wall clock none of the three measured intervals accounts for. */
  otherMs: number;
  /**
   * True when the measured intervals sum past the wall clock, which happens
   * when episodes of the scope ran at the same time.
   */
  concurrent: boolean;
}

/** One declared budget limit and what the scope spent against it. */
export interface Limit {
  /** The limit's key in `contract.budget`. */
  key: string;
  /** What the limit bounds, in words. */
  name: string;
  used: number;
  limit: number;
  unit: string;
  /** How the used figure was counted, for the hovercard. */
  counted: string;
}

export interface Statistics {
  /** Episode ids in the scope, the scope's own root first. */
  episodes: string[];
  steps: Step[];
  tools: ToolGroup[];
  wallClock: WallClock;
  /** Totals over every `assistant/message` in the scope. */
  tokens: { input: number | null; output: number | null; cacheRead: number | null };
  /**
   * Cache reads against total input tokens. Absent when no answer reported
   * a cache-read figure, which is a measurement that was not made rather
   * than a hit rate of zero. `rate` weights each request by its input;
   * `perRequest` is the unweighted mean over the `measuredRequests`
   * requests that reported both figures, so one large request cannot hide
   * many cold ones.
   */
  cache: { read: number; input: number; rate: number; perRequest: number | null; measuredRequests: number } | null;
  limits: Limit[];
  /** `request/retry` events in the scope. */
  retries: number;
  /** Model requests in the scope, retried attempts included. */
  requests: number;
}

interface Pending {
  step: Step;
  /** Last event the request produced, which ends its span. */
  lastTime: number;
}

/**
 * Reads one episode's steps. A request is matched to its chunks and its
 * answer by `request_id`.
 */
function stepsOf(episode: StatisticsEpisode): Step[] {
  const pending = new Map<string, Pending>();
  const out: Step[] = [];
  for (const event of episode.events) {
    const data = obj(event.data);
    const requestId = str(data.request_id);
    if (event.type === "model/request") {
      const step: Step = {
        episodeId: episode.id,
        step: num(data.step),
        attempt: num(data.attempt, 1),
        requestId,
        requestSeq: event.seq,
        requestTime: event.time,
        compaction: requestId.startsWith("cmp_"),
        timeToFirstToken: null,
        latencyMs: null,
        spanMs: null,
        input: null,
        output: null,
        cacheRead: null,
        outputRate: null,
      };
      const entry: Pending = { step, lastTime: event.time };
      pending.set(requestId, entry);
      out.push(step);
      continue;
    }
    const entry = pending.get(requestId);
    if (!entry) continue;
    if (event.type === "assistant/chunk") {
      entry.lastTime = event.time;
      const kind = str(obj(data.chunk).kind);
      if (kind !== "error" && entry.step.timeToFirstToken === null) {
        entry.step.timeToFirstToken = event.time - entry.step.requestTime;
      }
      continue;
    }
    if (event.type !== "assistant/message") continue;
    entry.lastTime = event.time;
    const step = entry.step;
    step.latencyMs = event.time - step.requestTime;
    const usage = obj(data.usage);
    step.input = typeof usage.input === "number" ? usage.input : null;
    step.output = typeof usage.output === "number" ? usage.output : null;
    step.cacheRead = typeof usage.cache_read === "number" ? usage.cache_read : null;
    if (step.output !== null && step.latencyMs > 0) {
      step.outputRate = step.output / (step.latencyMs / 1000);
    }
  }
  for (const entry of pending.values()) {
    entry.step.spanMs = entry.lastTime - entry.step.requestTime;
  }
  return out;
}

/** The declared budget of the scope's root, which is the pool its children draw on. */
function limitsOf(root: StatisticsEpisode, counts: Record<string, number>): Limit[] {
  const budget = obj(obj(root.contract).budget);
  const declared: [string, string, string, number, string][] = [
    ["model_calls", "model calls", "calls", counts.requests!, "one per `model/request`, retried attempts included"],
    ["input_tokens", "input tokens", "tokens", counts.input!, "input over every `assistant/message`"],
    ["output_tokens", "output tokens", "tokens", counts.output!, "output over every `assistant/message`"],
    ["seconds", "wall clock", "seconds", counts.seconds!, "from the root's `episode/start` to its end"],
    ["max_episodes", "episodes", "episodes", counts.episodes!, "one per log under this episode, itself included"],
    ["max_depth", "depth", "levels", counts.depth!, "the deepest episode nesting below this episode"],
  ];
  const out: Limit[] = [];
  for (const [key, name, unit, used, counted] of declared) {
    const limit = budget[key];
    if (typeof limit !== "number") continue;
    out.push({ key, name, used, limit, unit, counted });
  }
  return out;
}

/**
 * Every quantity for one scope: one episode alone, or an episode with its
 * descendants. `scope[0]` is the scope's own root, whose declared budget
 * gives the limits and whose lifetime gives the wall clock. `now` bounds an
 * episode that has not ended.
 */
export function computeStatistics(scope: StatisticsEpisode[], now: number): Statistics {
  const root = scope[0];
  const steps = scope.flatMap(stepsOf);
  const groups = new Map<string, ToolGroup>();
  let toolMs = 0;
  let backoffMs = 0;
  let retries = 0;
  for (const episode of scope) {
    for (const event of episode.events) {
      const data = obj(event.data);
      if (event.type === "tool/result") {
        const name = str(data.name, "?");
        const group = groups.get(name) ?? { name, calls: 0, durationMs: 0, errors: 0 };
        group.calls += 1;
        group.durationMs += num(data.duration_ms);
        if (data.is_error === true) group.errors += 1;
        groups.set(name, group);
        toolMs += num(data.duration_ms);
      } else if (event.type === "verification/result") {
        // A verification run is a tool execution the registry ran, so the
        // per-tool table counts it. Wall-clock tool time keeps its
        // documented definition over `tool/result` alone.
        const name = str(data.tool, "?");
        const group = groups.get(name) ?? { name, calls: 0, durationMs: 0, errors: 0 };
        group.calls += 1;
        group.durationMs += num(data.duration_ms);
        if (str(data.status) === "failed") group.errors += 1;
        groups.set(name, group);
      } else if (event.type === "request/retry") {
        retries += 1;
        backoffMs += num(data.delay_ms);
      }
    }
  }

  let input: number | null = null;
  let output: number | null = null;
  let cacheRead: number | null = null;
  for (const step of steps) {
    if (step.input !== null) input = (input ?? 0) + step.input;
    if (step.output !== null) output = (output ?? 0) + step.output;
    if (step.cacheRead !== null) cacheRead = (cacheRead ?? 0) + step.cacheRead;
  }

  const modelMs = steps.reduce((sum, step) => sum + (step.spanMs ?? 0), 0);
  const totalMs = root === undefined ? 0 : (root.endTime ?? now) - root.startTime;
  const measured = modelMs + toolMs + backoffMs;
  const wallClock: WallClock = {
    totalMs,
    modelMs,
    toolMs,
    backoffMs,
    otherMs: Math.max(0, totalMs - measured),
    concurrent: measured > totalMs,
  };

  const tokens = { input, output, cacheRead };
  let fractions = 0;
  let measuredRequests = 0;
  for (const step of steps) {
    if (step.cacheRead === null || step.input === null || step.input === 0) continue;
    fractions += step.cacheRead / step.input;
    measuredRequests += 1;
  }
  const cache =
    cacheRead === null || input === null || input === 0
      ? null
      : {
          read: cacheRead,
          input,
          rate: cacheRead / input,
          perRequest: measuredRequests === 0 ? null : fractions / measuredRequests,
          measuredRequests,
        };

  const depth = Math.max(0, ...scope.map((e) => e.depth));
  const limits =
    root === undefined
      ? []
      : limitsOf(root, {
          requests: steps.length,
          input: input ?? 0,
          output: output ?? 0,
          seconds: totalMs / 1000,
          episodes: scope.length,
          depth,
        });

  return {
    episodes: scope.map((e) => e.id),
    steps,
    tools: [...groups.values()].sort((a, b) => b.durationMs - a.durationMs || a.name.localeCompare(b.name)),
    wallClock,
    tokens,
    cache,
    limits,
    retries,
    requests: steps.length,
  };
}

// ---- one column per root ---------------------------------------------------

/** One root episode with its descendants, as the run comparison reads it. */
export interface Run {
  id: string;
  name: string;
  /** Episodes in this root's own scope, itself included. */
  episodes: number;
  outcome: Outcome | null;
  statistics: Statistics;
  /**
   * Input plus output tokens over the root's scope, absent when no answer
   * in that scope reported either figure. A run whose provider reported no
   * usage spent an unknown number of tokens rather than none.
   */
  tokens: number | null;
  /**
   * Width of the token bar, against the largest total among the runs. Zero
   * for a run whose tokens were never measured, which draws no bar.
   */
  w: number;
}

/**
 * One entry per root, each computed over that root and its descendants
 * alone, with a token bar drawn against the largest total among them.
 *
 * Nothing here is summed across roots. A budget is a pool a root reserves
 * down to its descendants, so a total over two roots would assert one pool
 * where there are two, which is the same kind of claim as reporting an
 * unmeasured quantity as zero. Each root's own figures stand beside each
 * other instead, which is what makes two runs comparable.
 */
export function computeRuns(roots: StatisticsEpisode[][], now: number, width: number): Run[] {
  const runs = roots
    .filter((scope) => scope.length > 0)
    .map((scope) => {
      const root = scope[0]!;
      const statistics = computeStatistics(scope, now);
      const { input, output } = statistics.tokens;
      const tokens = input === null && output === null ? null : (input ?? 0) + (output ?? 0);
      const run = { id: root.id, name: root.name, episodes: scope.length, outcome: root.outcome };
      return { ...run, statistics, tokens, w: 0 };
    });
  const largest = Math.max(1, ...runs.map((run) => run.tokens ?? 0));
  return runs.map((run) => ({ ...run, w: ((run.tokens ?? 0) / largest) * width }));
}

// ---- the context curve -----------------------------------------------------

export interface CurvePoint {
  /** Position of the step within its episode's own steps, counted from 1. */
  index: number;
  input: number;
  step: Step;
  x: number;
  y: number;
}

export interface CurveSeries {
  episodeId: string;
  name: string;
  points: CurvePoint[];
}

export interface CurveLayout {
  series: CurveSeries[];
  /** Highest input token count any point carries. */
  peak: number;
  /** Where that peak sits, which is where the axis labels it. */
  peakY: number;
  /** Longest series, which is the x extent. */
  steps: number;
  /** The declared input-token limit and its y, absent when none is declared. */
  budget: { inputTokens: number; y: number } | null;
  plot: { left: number; right: number; top: number; bottom: number };
  width: number;
  height: number;
}

/**
 * Places the input-token curve: one series per episode of the scope, x the
 * step's position in its episode and y its input tokens. Context growth
 * against a declared limit decides whether a run finishes, and a curve
 * shows what a total conceals, so the declared limit sets the y extent
 * whenever it exceeds the peak.
 */
export function layoutContextCurve(
  stats: Statistics,
  names: Map<string, string>,
  width: number,
  height: number,
): CurveLayout {
  const byEpisode = new Map<string, Step[]>();
  for (const step of stats.steps) {
    if (step.input === null || step.compaction) continue;
    const list = byEpisode.get(step.episodeId);
    if (list) list.push(step);
    else byEpisode.set(step.episodeId, [step]);
  }
  const declared = stats.limits.find((l) => l.key === "input_tokens");
  const left = 52;
  const right = Math.max(left + 20, width - 14);
  const top = 10;
  const bottom = Math.max(top + 20, height - 22);
  let peak = 0;
  let steps = 1;
  for (const list of byEpisode.values()) {
    steps = Math.max(steps, list.length);
    for (const step of list) peak = Math.max(peak, step.input ?? 0);
  }
  const ceiling = Math.max(peak, declared ? declared.limit : 0, 1);
  const x = (index: number) => (steps <= 1 ? left : left + ((index - 1) / (steps - 1)) * (right - left));
  const y = (value: number) => bottom - (value / ceiling) * (bottom - top);
  const series: CurveSeries[] = [...byEpisode.entries()]
    .sort((a, b) => stats.episodes.indexOf(a[0]) - stats.episodes.indexOf(b[0]))
    .map(([episodeId, list]) => ({
      episodeId,
      name: names.get(episodeId) ?? episodeId,
      points: list.map((step, i) => ({
        index: i + 1,
        input: step.input ?? 0,
        step,
        x: x(i + 1),
        y: y(step.input ?? 0),
      })),
    }));
  return {
    series,
    peak,
    peakY: y(peak),
    steps,
    budget: declared ? { inputTokens: declared.limit, y: y(declared.limit) } : null,
    plot: { left, right, top, bottom },
    width,
    height,
  };
}

// ---- the wall-clock breakdown ----------------------------------------------

export interface Share {
  /** One of model, tool, retry backoff, and other. */
  name: string;
  ms: number;
  /** Share of the divisor, from 0 to 1. */
  fraction: number;
  x: number;
  w: number;
}

/**
 * The four shares of the wall clock as one bar `width` pixels wide. The
 * divisor is the wall clock, or the sum of the measured intervals when
 * episodes that ran at the same time push that sum past it.
 */
export function layoutWallClock(clock: WallClock, width: number): { shares: Share[]; divisor: number } {
  const parts: [string, number][] = [
    ["model", clock.modelMs],
    ["tool", clock.toolMs],
    ["retry backoff", clock.backoffMs],
    ["other", clock.otherMs],
  ];
  const divisor = Math.max(1, clock.concurrent ? clock.modelMs + clock.toolMs + clock.backoffMs : clock.totalMs);
  const shares: Share[] = [];
  let x = 0;
  for (const [name, ms] of parts) {
    if (ms <= 0) continue;
    const fraction = ms / divisor;
    const w = fraction * width;
    shares.push({ name, ms, fraction, x, w });
    x += w;
  }
  return { shares, divisor };
}

/** The tool groups as bars, longest total duration first. */
export function layoutTools(tools: ToolGroup[], width: number): { name: string; w: number; group: ToolGroup }[] {
  const longest = Math.max(1, ...tools.map((t) => t.durationMs));
  return tools.map((group) => ({ name: group.name, w: (group.durationMs / longest) * width, group }));
}
