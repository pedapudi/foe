// The trajectory pane: one row per episode, drawn as line art in the
// register docs/design-language.md sets out. Structure is faint, the
// selected row carries the one accent, a spawn edge is solid and a fork
// edge dashed, and outcome colour is earned by direction.
//
// A row stacks the channels an episode's work nests into. A workflow
// episode's node band sits above, one lane per node that fired; the
// lifetime line carries the model requests; the tool lane below it fans a
// batch of calls issued together so that the batch is countable.
//
// The pane holds no state of its own beyond the axis choice and the
// hovercard: it is handed the episodes and redraws when a digest of what
// it would draw changes.

import { clear, fmtDuration, fmtTime, h } from "../dom.js";
import { outcomeLabel } from "../fold.js";
import type {
  Axis,
  PlacedDecision,
  PlacedFiring,
  PlacedMark,
  TrajectoryEpisode,
  TrajectoryLayout,
  TrajectoryRow,
} from "../trajectory.js";
import { DECISION_GLYPH, MARK_MIN_WIDTH, layoutTrajectory } from "../trajectory.js";
import type { Outcome } from "../types.js";
import { str } from "../types.js";
import { Hovercard } from "./hovercard.js";
import { outcomeRole } from "./tree.js";
import { figureSvg, svg } from "./svg.js";

export interface TrajectoryState {
  selected: string | null;
  cursor: string | null;
}

export interface TrajectoryHandlers {
  select(id: string): void;
  /** Selects an episode and brings the conversation to one log position. */
  reveal(id: string, seq: number): void;
}

/** The word a mark reads as in the hovercard. */
function markLabel(mark: PlacedMark): string {
  switch (mark.kind) {
    case "request":
      return "model request";
    case "tool":
      return `tool ${mark.label}`;
    case "retry":
      return `retry ${mark.label}`;
    case "compaction":
      return "compaction";
    case "spawn":
      return `spawn ${mark.label}`;
  }
}

/**
 * How a firing ended, which is also its colour direction: an error is the
 * worse outcome, a clean end the better one, and a firing still running is
 * neutral.
 */
function firingRole(firing: PlacedFiring): string {
  if (firing.endSeq === null) return "running";
  return firing.error === "" ? "good" : "bad";
}

export class TrajectoryView {
  readonly el: HTMLElement;
  private readonly figure = h("div", { class: "traj-figure" });
  private readonly body: HTMLElement;
  private readonly card: Hovercard;
  private readonly axisButtons: HTMLElement;
  private axis: Axis = "time";
  private digest = "";
  private episodes: TrajectoryEpisode[] = [];
  private rows = 0;
  private state: TrajectoryState = { selected: null, cursor: null };

  constructor(private readonly handlers: TrajectoryHandlers) {
    this.axisButtons = h(
      "span",
      { class: "traj-axis", role: "radiogroup", "aria-label": "trajectory x axis" },
      this.axisButton("time", "wall clock"),
      this.axisButton("sequence", "sequence"),
    );
    this.body = h("div", { class: "traj-body" }, this.figure);
    this.card = new Hovercard(this.body);
    this.body.appendChild(this.card.el);
    this.el = h(
      "section",
      { class: "pane-trajectory", "aria-label": "trajectory" },
      h("div", { class: "pane-head" }, h("h2", null, "trajectory"), h("span", { class: "spacer" }), this.axisButtons),
      this.body,
    );
    this.figure.addEventListener("scroll", () => this.card.hide());
    this.syncAxis();
  }

  private axisButton(axis: Axis, label: string): HTMLElement {
    return h(
      "button",
      {
        class: "traj-axis-btn",
        type: "button",
        role: "radio",
        "data-axis": axis,
        title: axis === "time" ? "place marks by wall-clock time" : "place marks by log position",
        onclick: () => this.setAxis(axis),
      },
      label,
    );
  }

  setAxis(axis: Axis): void {
    if (this.axis === axis) return;
    this.axis = axis;
    this.syncAxis();
    this.draw(true);
  }

  private syncAxis(): void {
    for (const b of this.axisButtons.querySelectorAll<HTMLElement>("[role=radio]")) {
      const on = b.dataset.axis === this.axis;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.classList.toggle("active", on);
    }
  }

  update(episodes: TrajectoryEpisode[], state: TrajectoryState): void {
    this.episodes = episodes;
    this.state = state;
    this.draw(false);
  }

  /** Redraws when the pane is resized, without changing what it shows. */
  resized(): void {
    this.draw(false);
  }

  /**
   * Pixels the rows of the last drawing took. The pane's derived height is
   * this plus the axis and the heading, so a row with a node band opens a
   * region tall enough to hold it.
   */
  rowsHeight(): number {
    return this.rows;
  }

  /**
   * Height of the heading above the figure. The pane's derived height is
   * the figure's content plus this.
   */
  chromeHeight(): number {
    const head = this.el.querySelector<HTMLElement>(".pane-head");
    return head ? head.offsetHeight : 0;
  }

  private draw(force: boolean): void {
    const width = this.figure.clientWidth;
    const height = this.figure.clientHeight;
    const digest = [
      this.axis,
      width,
      height,
      this.state.selected,
      this.state.cursor,
      this.episodes
        .map(
          (e) =>
            `${e.id}:${e.depth}:${e.lastSeq}:${e.startTime}:${e.endTime ?? "-"}:${e.marks.length}:${e.firings.length}:${e.decisions.length}:${e.outcome ? e.outcome.kind : ""}`,
        )
        .join("|"),
    ].join("~");
    if (!force && digest === this.digest) return;
    this.digest = digest;
    if (width === 0) return;
    const layout = layoutTrajectory({
      episodes: this.episodes,
      axis: this.axis,
      width,
      height,
      now: Date.now(),
    });
    this.rows = layout.rowsHeight;
    clear(this.figure);
    this.card.hide();
    if (this.episodes.length === 0) {
      this.figure.appendChild(h("div", { class: "empty sub" }, "no episodes"));
      return;
    }
    this.figure.appendChild(this.build(layout, width));
  }

  private build(layout: TrajectoryLayout, width: number): SVGSVGElement {
    const figure = figureSvg("traj", width, layout.height, "episode trajectory");

    // The axis: leader ticks with a small mono label, each carrying a
    // gridline down the plot. A span's length is read against the axis, so
    // the gridline is what lets a reader take that length off the figure.
    const axis = svg("g", { class: "traj-axisline" });
    for (const tick of layout.ticks) {
      axis.appendChild(svg("line", { class: "grid", x1: tick.x, y1: layout.plot.top - 1, x2: tick.x, y2: layout.plot.bottom + 2 }));
      axis.appendChild(svg("line", { class: "tick", x1: tick.x, y1: layout.plot.top - 6, x2: tick.x, y2: layout.plot.top - 1 }));
      const label = svg("text", { class: "tick-label", x: tick.x, y: layout.plot.top - 9, "text-anchor": "middle" });
      label.textContent = tick.label;
      axis.appendChild(label);
    }
    axis.appendChild(svg("line", { class: "baseline", x1: layout.plot.left, y1: layout.plot.top - 1, x2: layout.plot.right, y2: layout.plot.top - 1 }));
    figure.appendChild(axis);

    // Connectors first, so that rows paint over them. Each drops at the x
    // where the parent started the child and turns once into the child's
    // row, so it crosses an intervening row as a hairline rather than
    // sweeping along it.
    const edges = svg("g", { class: "traj-edges" });
    for (const edge of layout.connectors) {
      const turn = edge.to.y - 6;
      edges.appendChild(
        svg("path", {
          class: `traj-edge${edge.fork ? " fork" : ""}`,
          d: `M ${edge.from.x} ${edge.from.y} L ${edge.from.x} ${turn} L ${edge.to.x} ${turn} L ${edge.to.x} ${edge.to.y}`,
        }),
      );
    }
    figure.appendChild(edges);

    for (const row of layout.rows) figure.appendChild(this.rowElement(row, width));
    return figure;
  }

  private rowElement(row: TrajectoryRow, width: number): SVGGElement {
    const selected = row.id === this.state.selected;
    const group = svg("g", {
      class: `traj-row${selected ? " selected" : ""}${row.id === this.state.cursor ? " cursor" : ""}`,
      "data-id": row.id,
    });
    const hit = svg("rect", { class: "traj-hit", x: 0, y: row.top, width, height: row.height });
    hit.addEventListener("click", () => this.handlers.select(row.id));
    group.appendChild(hit);
    // The figure's one emphasis: a spine down the row's leading edge. A
    // filled row would compete with the bars drawn inside it.
    group.appendChild(svg("line", { class: "traj-spine", x1: 1, y1: row.top + 2, x2: 1, y2: row.top + row.height - 2 }));

    // The rail that carries depth: one segment per ancestor, the nearest of
    // which turns into this row's own label.
    for (const guide of row.guides) {
      group.appendChild(svg("line", { class: "traj-guide", x1: guide.x, y1: guide.y1, x2: guide.x, y2: guide.y2 }));
      if (guide.elbow) {
        group.appendChild(svg("line", { class: "traj-guide", x1: guide.x, y1: row.y, x2: row.labelX - 3, y2: row.y }));
      }
    }

    // The label names the program. The episode id stands beside it in the
    // sidebar and in the breadcrumbs, so the row does not repeat it.
    const label = svg("text", { class: "traj-label", x: row.labelX, y: row.y + 3.5 });
    label.textContent = row.name === row.id ? row.id : row.name;
    label.addEventListener("click", () => this.handlers.select(row.id));
    group.appendChild(label);

    for (const lane of row.lanes) {
      const name = svg("text", { class: "traj-lane-label", x: lane.labelX, y: lane.y + 3 });
      name.textContent = lane.node;
      group.appendChild(name);
    }

    // The lifetime bar, dashed past the last event while still running.
    group.appendChild(svg("line", { class: "traj-life", x1: row.x1, y1: row.y, x2: row.x2, y2: row.y }));
    if (row.running) {
      group.appendChild(svg("line", { class: "traj-life running", x1: row.x1, y1: row.y, x2: row.x2, y2: row.y }));
    }

    for (const firing of row.firings) group.appendChild(this.firingElement(firing));
    for (const decision of row.decisions) group.appendChild(this.decisionElement(decision));
    for (const mark of row.marks) group.appendChild(this.markElement(mark));
    group.appendChild(this.outcomeGlyph(row));
    return group;
  }

  /**
   * One firing of one node, as a span between its two events on the node's
   * own lane. A firing that ran a child episode is outlined rather than
   * filled, because its work is drawn on that child's row below.
   */
  private firingElement(firing: PlacedFiring): SVGGElement {
    const child = firing.childId !== null;
    const group = svg("g", { class: `traj-firing ${firingRole(firing)}${child ? " child" : ""}` });
    group.appendChild(svg("rect", { class: "bar", x: firing.x, y: firing.y - 2.5, width: Math.max(MARK_MIN_WIDTH, firing.w), height: 5, rx: 3 }));
    const observed = firing.endTime === null ? null : firing.endTime - firing.startTime;
    const meta = [
      `seq ${firing.startSeq}`,
      fmtTime(firing.startTime),
      observed === null ? "running" : `${fmtDuration(observed)} between its two events`,
    ].join(" · ");
    const detail = [
      firing.durationMs === null ? "no duration reported" : `the node reported ${fmtDuration(firing.durationMs)}`,
      child ? `ran ${firing.childId}` : "",
      firing.error === "" ? "" : firing.error,
    ]
      .filter((part) => part !== "")
      .join(" · ");
    this.card.attach(
      group,
      () => `${firing.node} · firing ${firing.fire}`,
      () => meta,
      () => detail,
    );
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      if (firing.childId !== null) this.handlers.select(firing.childId);
      else this.handlers.reveal(firing.episodeId, firing.startSeq);
    });
    return group;
  }

  /**
   * A branch or a recovery, on the lane of the node it names. A branch is a
   * tick over the lane with the label it chose; a recovery is an open
   * square in the colour of a limit reached, because it is an intervention
   * in the graph rather than a step through it.
   */
  private decisionElement(decision: PlacedDecision): SVGGElement {
    const group = svg("g", { class: `traj-decision ${decision.kind}` });
    if (decision.kind === "branch") {
      group.appendChild(svg("line", { class: "glyph", x1: decision.x, y1: decision.y - 3.5, x2: decision.x, y2: decision.y + 3.5 }));
    } else {
      group.appendChild(svg("rect", { class: "glyph", x: decision.x - 2.5, y: decision.y - 2.5, width: 5, height: 5 }));
    }
    if (decision.showLabel) {
      const text = svg("text", { class: "traj-decision-label", x: decision.x + DECISION_GLYPH, y: decision.y + 3 });
      text.textContent = decision.label;
      group.appendChild(text);
    }
    this.card.attach(
      group,
      () => `${decision.kind} · ${decision.node}`,
      () => `seq ${decision.seq} · ${fmtTime(decision.time)}`,
      () => decision.detail,
    );
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      this.handlers.reveal(decision.episodeId, decision.seq);
    });
    return group;
  }

  private markElement(mark: PlacedMark): SVGGElement {
    const group = svg("g", { class: `traj-mark ${mark.kind}` });
    switch (mark.kind) {
      case "tool":
        // The lane's own band: the segment hangs below its baseline so that
        // the tool channel never touches the lifetime line above it. Calls
        // issued together share one x and take successive heights, so a
        // batch of six reads is six marks rather than one.
        group.appendChild(
          svg("rect", { class: "seg", x: mark.x, y: mark.y - 2, width: Math.max(MARK_MIN_WIDTH, mark.w), height: 4.5, rx: 3 }),
        );
        break;
      case "request": {
        // A request is a bar above the lifetime line, running from the call
        // to the answer, so its length is the time the answer took. The two
        // parts of that time carry the encoding the statistics view's
        // per-step bars use: the whole answer is the lower and fainter bar,
        // and the wait before the first token is the taller one over it.
        const width = Math.max(MARK_MIN_WIDTH, mark.w);
        group.appendChild(svg("rect", { class: "span", x: mark.x, y: mark.y - 6, width, height: 4, rx: 3 }));
        if (mark.head > 0) {
          group.appendChild(
            svg("rect", { class: "wait", x: mark.x, y: mark.y - 7, width: Math.max(MARK_MIN_WIDTH, mark.head), height: 6, rx: 3 }),
          );
        }
        // A hairline back to the line ties the bar to the row it belongs
        // to, so a bar never floats free of its episode.
        group.appendChild(svg("line", { class: "stem", x1: mark.x, y1: mark.y - 2, x2: mark.x, y2: mark.y + 1 }));
        break;
      }
      case "retry":
        // The backoff the retry imposes runs forward from it as a dashed
        // segment, so a doubling sequence reads as doubling lengths. On the
        // sequence axis a delay has no length and the cross stands alone.
        if (mark.w > 0) {
          group.appendChild(svg("line", { class: "backoff", x1: mark.x, y1: mark.y, x2: mark.x + mark.w, y2: mark.y }));
        }
        group.appendChild(svg("path", { class: "glyph", d: `M ${mark.x - 3} ${mark.y - 3} l 6 6 M ${mark.x + 3} ${mark.y - 3} l -6 6` }));
        break;
      case "compaction":
        group.appendChild(svg("path", { class: "glyph", d: `M ${mark.x} ${mark.y - 4} l 4 4 l -4 4 l -4 -4 z` }));
        break;
      case "spawn":
        group.appendChild(svg("circle", { class: "glyph", cx: mark.x, cy: mark.y, r: 2.6 }));
        break;
    }
    this.card.attach(group, () => markLabel(mark), () => markMeta(mark), () => mark.detail);
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      this.handlers.reveal(mark.episodeId, mark.seq);
    });
    return group;
  }

  /**
   * The end of a row: a glyph whose colour is the outcome's direction, and
   * a hollow ring while the episode is still running. Hovering it opens the
   * same hovercard the marks use, carrying the outcome's code or limit.
   */
  private outcomeGlyph(row: TrajectoryRow): SVGGElement {
    const { x2: x, y } = row;
    const outcome: Outcome | null = row.outcome;
    const role = outcomeRole(outcome);
    const group = svg("g", { class: `traj-outcome ${role || (row.running ? "running" : "")}` });
    const kind = outcome ? str(outcome.kind) : "";
    if (kind === "completed") group.appendChild(svg("circle", { cx: x, cy: y, r: 3.6 }));
    else if (kind === "failed") group.appendChild(svg("path", { d: `M ${x - 3.4} ${y - 3.4} l 6.8 6.8 M ${x + 3.4} ${y - 3.4} l -6.8 6.8` }));
    else if (kind === "exhausted") group.appendChild(svg("path", { d: `M ${x} ${y - 4} l 4 7 l -8 0 z` }));
    else if (kind === "blocked") group.appendChild(svg("rect", { x: x - 1.6, y: y - 4.5, width: 3.2, height: 9 }));
    else group.appendChild(svg("circle", { class: "open", cx: x, cy: y, r: 3.2 }));
    const label = outcome ? outcomeLabel(outcome) : "running";
    const detail = outcome ? str((outcome as Record<string, unknown>).message) : "";
    const meta = row.name === row.id ? row.id : `${row.name} · ${row.id}`;
    this.card.attach(group, () => label, () => meta, () => detail);
    return group;
  }
}

/**
 * The one line of context under a mark's name: where it sits in the log,
 * when it happened, how long it took, and, for a request that received a
 * token, how long the first one took to arrive.
 */
function markMeta(mark: PlacedMark): string {
  const lines: string[] = [`seq ${mark.seq}`, fmtTime(mark.time)];
  if (mark.durationMs > 0) lines.push(fmtDuration(mark.durationMs));
  // A request's card names the two parts of its span separately, because
  // the bar draws them as two and a reader asks which one was long.
  const first = mark.span?.firstTokenTime ?? null;
  if (first !== null) lines.push(`${fmtDuration(first - mark.time)} to first token`);
  return lines.join(" · ");
}
