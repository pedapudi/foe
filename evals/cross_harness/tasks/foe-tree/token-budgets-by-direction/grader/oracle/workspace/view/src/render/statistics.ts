// The statistics tab: nine figures over one episode, over that episode and
// its descendants, or one row per root of a collection. Line art in the
// register docs/design-language.md sets out, with two rules of its own.
//
// Every number a reader could not derive by eye carries a hovercard giving
// the quantity's definition and the values it was computed from, so a cache
// hit rate of 4 percent shows the two counts behind it. And a quantity the
// log never measured is shown as absent rather than as zero, because zero
// asserts a measurement that was not made.

import { clear, fmtDuration, fmtInt, h } from "../dom.js";
import type { Child } from "../dom.js";
import { computeAttribution } from "../attribution.js";
import { inputOriginFigure, inputSourceFigure, replayCostFigure } from "./attribution.js";
import type { FigureTools } from "./attribution.js";
import { outcomeLabel } from "../fold.js";
import { computeRuns, computeStatistics, layoutContextCurve, layoutTools, layoutWallClock } from "../statistics.js";
import type { CurveLayout, Run, Statistics, StatisticsEpisode, Step } from "../statistics.js";
import { Hovercard } from "./hovercard.js";
import { outcomeRole } from "./tree.js";
import { barSvg, figureSvg, svg } from "./svg.js";

/**
 * What the figures cover: the selected episode alone, that episode with
 * every episode under it, which is the scope a declared budget bounds, or
 * every root of the collection reported one row each.
 */
export type Scope = "episode" | "tree" | "runs";

/** The word for a quantity no event in the scope measured. */
const ABSENT = "not measured";

function absent(): HTMLElement {
  return h("span", { class: "absent", title: "no event in this scope measured this quantity" }, ABSENT);
}

/** A number with its unit, or the absent word when the number is null. */
function measure(value: number | null, render: (v: number) => string): Child {
  return value === null ? absent() : render(value);
}

/**
 * A percentage, with one decimal below ten and above ninety-nine and a
 * half. Rounding 99.76 to 100 would assert that nothing else was measured,
 * so a share short of the whole never reads as the whole.
 */
function percent(fraction: number): string {
  const pct = fraction * 100;
  const fine = pct < 10 || (pct > 99.5 && fraction < 1);
  return `${fine ? pct.toFixed(1) : Math.round(pct)}%`;
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

/** What the largest share of the wall clock means, in one sentence. */
function leadCaption(name: string, fraction: number): string {
  const share = percent(fraction);
  if (name === "model") return `The run is ${share} model-bound.`;
  if (name === "tool") return `The run is ${share} tool-bound.`;
  if (name === "retry backoff") return `${share} of the wall clock went to waiting between retried requests.`;
  return `${share} of the wall clock falls outside every measured interval.`;
}

export interface StatisticsHandlers {
  /** Brings the conversation to the log position of one step. */
  reveal(episodeId: string, seq: number): void;
}

export class StatisticsView {
  readonly el: HTMLElement;
  private readonly body = h("div", { class: "stats-body" });
  private readonly scopeButtons: HTMLElement;
  private readonly card: Hovercard;
  private scope: Scope = "episode";
  private episodes: StatisticsEpisode[] = [];
  /** One scope per root of the collection, for the run comparison. */
  private roots: StatisticsEpisode[][] = [];
  private names = new Map<string, string>();
  private digest = "";

  constructor(private readonly handlers: StatisticsHandlers) {
    this.el = h("div", { class: "stats" });
    this.card = new Hovercard(this.el);
    this.scopeButtons = h(
      "span",
      { class: "stats-scope", role: "radiogroup", "aria-label": "statistics scope" },
      this.scopeButton("episode", "this episode"),
      this.scopeButton("tree", "episode tree"),
      this.scopeButton("runs", "every run"),
    );
    this.el.append(
      h("div", { class: "fig-head" }, h("h3", null, "statistics"), h("span", { class: "spacer" }), this.scopeButtons),
      this.body,
      this.card.el,
    );
    this.el.addEventListener("scroll", () => this.card.hide());
    this.syncScope();
  }

  private scopeButton(scope: Scope, label: string): HTMLElement {
    return h(
      "button",
      {
        class: "stats-scope-btn",
        type: "button",
        role: "radio",
        "data-scope": scope,
        title: {
          episode: "quantities of the selected episode alone",
          tree: "quantities of the selected episode and every episode under it, which draw on one budget pool",
          runs: "one row per root episode, each counted on its own, because roots hold separate budget pools",
        }[scope],
        onclick: () => this.setScope(scope),
      },
      label,
    );
  }

  setScope(scope: Scope): void {
    if (this.scope === scope) return;
    this.scope = scope;
    this.syncScope();
    this.draw(true);
  }

  private syncScope(): void {
    for (const b of this.scopeButtons.querySelectorAll<HTMLElement>("[role=radio]")) {
      const on = b.dataset.scope === this.scope;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.classList.toggle("active", on);
    }
  }

  /**
   * `episodes` is the selected episode followed by its descendants, in tree
   * order, each with its depth below the selected one. `roots` is one such
   * scope per root of the collection, which the run comparison reads; the
   * comparison is offered only where there is more than one root, since a
   * collection of one has nothing to compare.
   */
  update(episodes: StatisticsEpisode[], names: Map<string, string>, roots: StatisticsEpisode[][] = []): void {
    this.episodes = episodes;
    this.names = names;
    this.roots = roots;
    const button = this.scopeButtons.querySelector<HTMLElement>('[data-scope="runs"]');
    if (button) button.hidden = roots.length < 2;
    if (this.scope === "runs" && roots.length < 2) {
      this.scope = "episode";
      this.syncScope();
    }
    this.draw(false);
  }

  resized(): void {
    this.draw(false);
  }

  private draw(force: boolean): void {
    const width = this.body.clientWidth;
    const scope = this.scope === "episode" ? this.episodes.slice(0, 1) : this.episodes;
    const shown = this.scope === "runs" ? this.roots.flat() : scope;
    const digest = [
      width,
      this.scope,
      shown.map((e) => `${e.id}:${e.events.length}:${e.endTime ?? "-"}`).join("|"),
    ].join("~");
    if (!force && digest === this.digest) return;
    // A tab that is not mounted has no width; the digest is kept unset so
    // that mounting it draws.
    if (width === 0) return;
    this.digest = digest;
    clear(this.body);
    this.card.hide();
    if (shown.length === 0) {
      this.body.appendChild(h("div", { class: "empty sub" }, "no episode selected"));
      return;
    }
    if (this.scope === "runs") {
      this.body.appendChild(this.runFigure(width));
      return;
    }
    const stats = computeStatistics(scope, Date.now());
    const attribution = computeAttribution(scope);
    this.body.append(
      this.wallClockFigure(stats, width),
      this.contextFigure(stats, width),
      inputSourceFigure(this.tools, attribution, width),
      replayCostFigure(this.tools, attribution, width),
      inputOriginFigure(this.tools, attribution, width),
      this.stepFigure(stats, width),
      this.cacheFigure(stats, width),
      this.budgetFigure(stats),
      this.toolFigure(stats, width),
    );
  }

  /** What the attribution figures borrow from this view. */
  private get tools(): FigureTools {
    return {
      card: this.card,
      figure: (name, body, caption) => this.figure(name, body, caption),
      reveal: (episodeId, seq) => this.handlers.reveal(episodeId, seq),
      percent,
      absent,
    };
  }

  /** A figure: a heading, the drawing, and a caption stating what to see. */
  private figure(name: string, body: Child, caption: string): HTMLElement {
    return h(
      "section",
      { class: "fig" },
      h("h4", null, name),
      h("div", { class: "fig-body" }, body),
      h("div", { class: "fig-caption" }, caption),
    );
  }

  // ---- one row per run -----------------------------------------------------

  /**
   * Every root of the collection, one row each. No column is a total over
   * the roots: each root reserves its own budget pool and settles its own
   * children, so a sum across them would assert a pool that does not
   * exist. The bar reads each run's tokens against the largest run, which
   * is a comparison rather than a total.
   */
  private runFigure(width: number): HTMLElement {
    const barWidth = Math.max(60, Math.min(200, width - 460));
    const runs = computeRuns(this.roots, Date.now(), barWidth);
    const head = h(
      "thead",
      null,
      h(
        "tr",
        null,
        h("th", null, "run"),
        h("th", null, "outcome"),
        h("th", null, "requests"),
        h("th", null, "tokens"),
        h("th", null, "wall clock"),
        h("th", null, "retries"),
        h("th", null, ""),
      ),
    );
    const table = h(
      "table",
      { class: "stats-table" },
      head,
      h("tbody", null, runs.map((run) => this.runRow(run, barWidth))),
    );
    return this.figure(
      "every run",
      table,
      `${runs.length} root episodes, each counted over itself and its descendants. ` +
        "Nothing here is added across roots: every root holds a budget pool of its own, " +
        "so a total over them would state a pool that does not exist. The bar is each run's " +
        "tokens against the largest run.",
    );
  }

  private runRow(run: Run, barWidth: number): HTMLElement {
    const stats = run.statistics;
    const clock = stats.wallClock;
    const bar = barSvg("tool-bar", barWidth, 10, "tokens against the largest run");
    // A run whose answers reported no usage has nothing to draw; a bar of
    // zero width would read as a run that spent nothing.
    if (run.tokens !== null) {
      bar.appendChild(svg("rect", { class: "seg", x: 0, y: 3, width: Math.max(0.5, run.w), height: 4 }));
    }
    const role = outcomeRole(run.outcome);
    const row = h(
      "tr",
      { class: "run-row" },
      h("td", { class: "step-name" }, run.name, h("span", { class: "sub" }, run.id)),
      h("td", null, h("span", { class: `outcome ${role}` }, outcomeLabel(run.outcome))),
      h("td", { class: "num" }, fmtInt(stats.requests)),
      h("td", { class: "num" }, measure(run.tokens, fmtInt)),
      h("td", { class: "num" }, fmtDuration(clock.totalMs)),
      h("td", { class: "num" }, fmtInt(stats.retries)),
      h("td", null, bar),
    );
    row.addEventListener("click", () => this.handlers.reveal(run.id, 0));
    this.card.attach(
      row,
      () => `${run.name} · ${run.id}`,
      () => "this root and every episode under it, counted on their own",
      () =>
        `${run.episodes} episode${run.episodes === 1 ? "" : "s"} · ` +
        `${run.tokens === null ? ABSENT : `${fmtInt(stats.tokens.input ?? 0)} input plus ${fmtInt(stats.tokens.output ?? 0)} output`} tokens`,
    );
    return row;
  }

  // ---- where the wall clock went -------------------------------------------

  private wallClockFigure(stats: Statistics, width: number): HTMLElement {
    const clock = stats.wallClock;
    const barWidth = Math.max(80, width - 24);
    const { shares, divisor } = layoutWallClock(clock, barWidth);
    const height = 30;
    const figure = barSvg("fig-svg", barWidth, height, "where the wall clock went");
    const largest = shares.reduce<(typeof shares)[number] | null>((a, b) => (a && a.ms >= b.ms ? a : b), null);
    for (const share of shares) {
      const group = svg("g", {
        class: `clock-share ${share.name.replace(/ /g, "-")}${share === largest ? " lead" : ""}`,
      });
      group.appendChild(svg("rect", { class: "seg", x: share.x, y: 6, width: Math.max(0.5, share.w), height: 12 }));
      group.appendChild(svg("line", { class: "edge", x1: share.x, y1: 4, x2: share.x, y2: 20 }));
      if (share.w > 46) {
        const label = svg("text", { class: "share-label", x: share.x + 4, y: 28 });
        label.textContent = `${share.name} ${percent(share.fraction)}`;
        group.appendChild(label);
      }
      this.card.attach(
        group,
        () => `${share.name} time`,
        () => `${share.name} milliseconds ÷ ${clock.concurrent ? "the measured total" : "wall clock"}`,
        () => `${fmtInt(Math.round(share.ms))} ÷ ${fmtInt(Math.round(divisor))} ms = ${percent(share.fraction)}`,
      );
      figure.appendChild(group);
    }
    const total = h(
      "span",
      { class: "fig-total" },
      `${seconds(clock.totalMs)} of wall clock`,
    );
    this.card.attach(
      total,
      () => "wall clock",
      () => "the scope root's `episode/start` to its `episode/end`",
      () => `${fmtInt(clock.totalMs)} ms`,
    );
    const caption = clock.concurrent
      ? "The shares sum past the wall clock, because episodes of this scope ran at the same time; each share is drawn against that sum."
      : largest === null
        ? "No interval in this scope was measured."
        : leadCaption(largest.name, largest.fraction);
    return this.figure("where the wall clock went", [figure, total], caption);
  }

  // ---- context growth ------------------------------------------------------

  private contextFigure(stats: Statistics, width: number): HTMLElement {
    const height = 150;
    const curve = layoutContextCurve(stats, this.names, Math.max(120, width - 24), height);
    if (curve.series.length === 0) {
      return this.figure(
        "context growth",
        h("div", { class: "sub" }, "no answer in this scope reported an input token count"),
        "Input tokens per step are not measured here.",
      );
    }
    const figure = figureSvg("fig-svg", curve.width, height, "input tokens per step");
    figure.appendChild(this.curveAxes(curve));
    // The declared limit is the envelope, dashed as the register asks.
    if (curve.budget) {
      figure.appendChild(
        svg("line", {
          class: "curve-budget",
          x1: curve.plot.left,
          y1: curve.budget.y,
          x2: curve.plot.right,
          y2: curve.budget.y,
        }),
      );
      const label = svg("text", { class: "curve-budget-label", x: curve.plot.right, y: curve.budget.y - 4, "text-anchor": "end" });
      label.textContent = `${fmtInt(curve.budget.inputTokens)} input-token budget`;
      figure.appendChild(label);
    }
    const leading = curve.series.reduce((a, b) => (a.points.length >= b.points.length ? a : b));
    for (const series of curve.series) {
      const group = svg("g", { class: `curve${series === leading ? " lead" : ""}` });
      const d = series.points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
      group.appendChild(svg("path", { class: "line", d }));
      for (const point of series.points) {
        const dot = svg("circle", { class: "dot", cx: point.x, cy: point.y, r: 2.6 });
        this.card.attach(
          dot,
          () => `step ${point.step.step} input`,
          () => `${series.name}: the context the request carried`,
          () => `${fmtInt(point.input)} input tokens`,
        );
        dot.addEventListener("click", () => this.handlers.reveal(series.episodeId, point.step.requestSeq));
        group.appendChild(dot);
      }
      figure.appendChild(group);
    }
    const first = leading.points[0]!;
    const last = leading.points[leading.points.length - 1]!;
    const growth = first.input > 0 ? `${(last.input / first.input).toFixed(1)} times` : "an unmeasured factor";
    const share = curve.budget
      ? ` and reaches ${percent(last.input / curve.budget.inputTokens)} of the declared input-token budget`
      : "";
    return this.figure(
      "context growth",
      figure,
      `Input tokens per step. The longest series grows ${growth} from its first step to its last${share}. ` +
        "A compaction's own summarization call is left out, because its input is the summarization prompt rather than the context.",
    );
  }

  private curveAxes(curve: CurveLayout): SVGGElement {
    const axes = svg("g", { class: "curve-axes" });
    axes.appendChild(
      svg("line", { class: "baseline", x1: curve.plot.left, y1: curve.plot.bottom, x2: curve.plot.right, y2: curve.plot.bottom }),
    );
    // Two leader labels, at the first step and the last, and no gridlines.
    const ends = curve.steps <= 1 ? [1] : [1, curve.steps];
    for (const step of ends) {
      const x = step === 1 ? curve.plot.left : curve.plot.right;
      const label = svg("text", {
        class: "tick-label",
        x,
        y: curve.plot.bottom + 13,
        "text-anchor": step === 1 ? "start" : "end",
      });
      label.textContent = `step ${step}`;
      axes.appendChild(label);
    }
    // The peak is labelled where it sits, so the reader can scale the
    // curve; a declared limit close to it carries its own label already.
    const budgetY = curve.budget === null ? Number.NEGATIVE_INFINITY : curve.budget.y;
    if (Math.abs(curve.peakY - budgetY) > 11) {
      const peak = svg("text", {
        class: "tick-label",
        x: curve.plot.left - 6,
        y: curve.peakY + 3.5,
        "text-anchor": "end",
      });
      peak.textContent = fmtInt(curve.peak);
      axes.appendChild(peak);
    }
    return axes;
  }

  // ---- one row per step ----------------------------------------------------

  private stepFigure(stats: Statistics, width: number): HTMLElement {
    if (stats.steps.length === 0) {
      return this.figure("per step", h("div", { class: "sub" }, "no model request in this scope"), "");
    }
    const longest = Math.max(1, ...stats.steps.map((s) => s.latencyMs ?? s.spanMs ?? 0));
    const barWidth = Math.max(60, Math.min(320, width - 380));
    const slowest = stats.steps.reduce((a, b) => ((a.latencyMs ?? 0) >= (b.latencyMs ?? 0) ? a : b));
    const rows = stats.steps.map((step) => this.stepRow(step, longest, barWidth, step === slowest));
    const table = h(
      "table",
      { class: "stats-table" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          h("th", null, "step"),
          h("th", null, "first token"),
          h("th", null, "latency"),
          h("th", null, "out/s"),
          h("th", null, ""),
        ),
      ),
      h("tbody", null, rows),
    );
    const measured = stats.steps.filter((s) => s.latencyMs !== null);
    const caption =
      measured.length === 0
        ? "No request in this scope was answered by a message, so no latency was measured."
        : `The accented row is the slowest of ${measured.length} answered request${measured.length === 1 ? "" : "s"}. ` +
          "The filled part of each bar is the wait for the first token and the rest is the wait for the whole answer.";
    return this.figure("per step", table, caption);
  }

  private stepRow(step: Step, longest: number, barWidth: number, slowest: boolean): HTMLElement {
    const name = this.names.get(step.episodeId) ?? step.episodeId;
    const label = `${step.compaction ? "compaction" : `step ${step.step}`}${step.attempt > 1 ? ` · attempt ${step.attempt}` : ""}`;
    const bar = barSvg("step-bar", barWidth, 12, "wait for the first token, then for the whole answer");
    const latency = step.latencyMs;
    if (latency !== null) {
      const full = (latency / longest) * barWidth;
      const head = step.timeToFirstToken === null ? 0 : (step.timeToFirstToken / longest) * barWidth;
      bar.appendChild(svg("rect", { class: "rest", x: 0, y: 4, width: Math.max(0.5, full), height: 4 }));
      if (head > 0) bar.appendChild(svg("rect", { class: "head", x: 0, y: 3, width: Math.max(0.5, head), height: 6 }));
    }
    const row = h(
      "tr",
      { class: `step-row${slowest ? " lead" : ""}${step.compaction ? " compaction" : ""}` },
      h("td", { class: "step-name" }, label, h("span", { class: "sub" }, name)),
      h("td", { class: "num" }, measure(step.timeToFirstToken, (v) => `${fmtInt(v)} ms`)),
      h("td", { class: "num" }, measure(latency, (v) => `${fmtInt(v)} ms`)),
      h("td", { class: "num" }, measure(step.outputRate, (v) => v.toFixed(1))),
      h("td", { class: "step-cell" }, bar),
    );
    row.addEventListener("click", () => this.handlers.reveal(step.episodeId, step.requestSeq));
    this.card.attach(
      row,
      () => `${name} · ${label}`,
      () =>
        "first token: `model/request` to its first `assistant/chunk`; latency: `model/request` to `assistant/message`; out/s: output tokens ÷ latency",
      () =>
        latency === null
          ? "no message answered this request"
          : `${step.output === null ? ABSENT : fmtInt(step.output)} output tokens ÷ ${(latency / 1000).toFixed(3)} s` +
            `${step.input === null ? "" : ` · ${fmtInt(step.input)} input tokens`}`,
    );
    return row;
  }

  // ---- cache reads ---------------------------------------------------------

  private cacheFigure(stats: Statistics, width: number): HTMLElement {
    if (stats.cache === null) {
      return this.figure(
        "cache reads",
        h("div", { class: "sub" }, [absent(), ": no answer in this scope reported a cache-read count"]),
        "A run with no cache-read figure has no hit rate; drawing it as zero percent would assert a measurement that was not made.",
      );
    }
    const { read, input, rate } = stats.cache;
    const barWidth = Math.max(80, width - 24);
    const height = 26;
    const figure = barSvg("fig-svg", barWidth, height, "cache-read tokens against total input tokens");
    figure.appendChild(svg("rect", { class: "cache-track", x: 0, y: 6, width: barWidth, height: 12 }));
    const hit = svg("rect", { class: "cache-hit", x: 0, y: 6, width: Math.max(1, rate * barWidth), height: 12 });
    figure.appendChild(hit);
    this.card.attach(
      figure,
      () => "cache hit rate",
      () => "cache-read tokens ÷ total input tokens",
      () => `${fmtInt(read)} ÷ ${fmtInt(input)} = ${percent(rate)}`,
    );
    return this.figure(
      "cache reads",
      [figure, h("span", { class: "fig-total" }, `${fmtInt(read)} of ${fmtInt(input)} input tokens · ${percent(rate)}`)],
      `${percent(rate)} of the input tokens this scope sent came from the provider's cache.`,
    );
  }

  // ---- budget --------------------------------------------------------------

  private budgetFigure(stats: Statistics): HTMLElement {
    if (stats.limits.length === 0) {
      return this.figure(
        "budget",
        h("div", { class: "sub" }, "this episode's program declares no limit"),
        "Nothing bounds this scope but the runtime's own caps.",
      );
    }
    const rows = stats.limits.map((limit) => {
      const fraction = limit.limit <= 0 ? 0 : Math.min(1, limit.used / limit.limit);
      const w = 96;
      const mark = barSvg("mark", w, 8, "share of the limit spent");
      mark.appendChild(svg("line", { class: "track", x1: 0, y1: 4, x2: w, y2: 4 }));
      mark.appendChild(
        svg("line", { class: `fill${fraction >= 1 ? " caution" : ""}`, x1: 0, y1: 4, x2: Math.max(0.5, fraction * w), y2: 4 }),
      );
      const used = limit.key === "seconds" ? limit.used.toFixed(1) : fmtInt(Math.round(limit.used));
      const row = h(
        "tr",
        { class: "limit-row" },
        h("td", null, limit.name),
        h("td", { class: "num" }, `${used} / ${fmtInt(limit.limit)} ${limit.unit}`),
        h("td", { class: "num" }, percent(fraction)),
        h("td", null, mark),
      );
      this.card.attach(
        row,
        () => `${limit.name} against budget.${limit.key}`,
        () => limit.counted,
        () => `${used} ÷ ${fmtInt(limit.limit)} = ${percent(fraction)}`,
      );
      return row;
    });
    return this.figure(
      "budget",
      h("table", { class: "stats-table" }, h("tbody", null, rows)),
      "Every limit the program declares, against what this scope spent. A child draws on the pool its root holds, so the tree scope is what the limit actually bounds.",
    );
  }

  // ---- tools ---------------------------------------------------------------

  private toolFigure(stats: Statistics, width: number): HTMLElement {
    if (stats.tools.length === 0) {
      return this.figure("tool calls", h("div", { class: "sub" }, "no tool was called in this scope"), "");
    }
    const barWidth = Math.max(60, Math.min(220, width - 400));
    const bars = layoutTools(stats.tools, barWidth);
    const rows = bars.map(({ group, w }) => {
      const mark = barSvg("tool-bar", barWidth, 10, "duration against the longest");
      mark.appendChild(svg("rect", { class: "seg", x: 0, y: 3, width: Math.max(0.5, w), height: 4 }));
      const row = h(
        "tr",
        { class: "tool-row" },
        h("td", { class: "tool-name" }, group.name),
        h("td", { class: "num" }, `${fmtInt(group.calls)} call${group.calls === 1 ? "" : "s"}`),
        h("td", { class: "num" }, fmtDuration(group.durationMs)),
        h("td", { class: "num" }, group.errors > 0 ? h("span", { class: "bad" }, `${group.errors} error`) : ""),
        h("td", null, mark),
      );
      this.card.attach(
        row,
        () => `tool ${group.name}`,
        () => "calls and the total of the `duration_ms` their results report",
        () => `${fmtInt(group.calls)} calls · ${fmtInt(group.durationMs)} ms total`,
      );
      return row;
    });
    const calls = stats.tools.reduce((sum, t) => sum + t.calls, 0);
    return this.figure(
      "tool calls",
      h("table", { class: "stats-table" }, h("tbody", null, rows)),
      `${fmtInt(calls)} call${calls === 1 ? "" : "s"} across ${stats.tools.length} tool${stats.tools.length === 1 ? "" : "s"}, ordered by total duration.`,
    );
  }
}
