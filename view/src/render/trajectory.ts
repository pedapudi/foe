// The trajectory pane: one row per episode, drawn as line art in the
// register docs/design-language.md sets out. Structure is faint, the
// selected row carries the one accent, a spawn edge is solid and a fork
// edge dashed, and outcome colour is earned by direction.
//
// A row stacks the channels an episode's work nests into, in the order of
// containment: model requests above the lifetime line, the line itself,
// then the node band of a declared graph with one lane per node that fired,
// then the tool lane, which fans a batch of calls issued together so that
// the batch is countable.
//
// Beside the three horizontal axes the pane offers a fourth reading, which
// is a different orientation rather than a fourth axis: the causality
// figure runs top to bottom and shows what caused what. It is laid out and
// drawn by its own two modules, and shares this pane's heading, hovercard
// and redraw gate.
//
// The pane holds no state of its own beyond the reading it shows and the
// hovercard: it is handed the episodes and redraws when a digest of what
// it would draw changes.

import { layoutCausality, scopeFor } from "../causality.js";
import type { CausalityEpisode, CausalityLayout, ConversationScope } from "../causality.js";
import { currentFontScale } from "../chrome.js";
import { clear, fmtDuration, fmtTime, h } from "../dom.js";
import { outcomeLabel } from "../fold.js";
import { shortIdentity } from "../lineage.js";
import type {
  Axis,
  PlacedDecision,
  PlacedFiring,
  PlacedMark,
  TrajectoryEpisode,
  TrajectoryLayout,
  TrajectoryRow,
} from "../trajectory.js";
import { DECISION_GLYPH, MARK_MIN_WIDTH, MARK_THICKNESS, layoutTrajectory } from "../trajectory.js";
import type { Outcome } from "../types.js";
import { str } from "../types.js";
import { renderCausality } from "./causality.js";
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
  /**
   * Scopes the conversation to one node of the causality figure, or back
   * to the whole run for null.
   */
  scope(scope: ConversationScope | null): void;
}

/**
 * What the pane reads a run as. The three axes measure *when* and run left
 * to right; `causality` shows *what caused what* and runs top to bottom,
 * so it is an orientation rather than a fourth value of `Axis`.
 */
export type Reading = Axis | "causality";

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
  private reading: Reading = "time";
  /**
   * True once the reader has picked an axis. Until then the axis follows
   * what the figure holds: wall clock inside one tree, where simultaneity
   * is real, and elapsed time across independent roots, where it is not.
   */
  private chosen = false;
  private digest = "";
  private episodes: TrajectoryEpisode[] = [];
  private causality: CausalityEpisode[] = [];
  private scoped: string | null = null;
  private causalityLayout: CausalityLayout | null = null;
  private rows = 0;
  private state: TrajectoryState = { selected: null, cursor: null };

  constructor(private readonly handlers: TrajectoryHandlers) {
    this.axisButtons = h(
      "span",
      { class: "traj-axis", role: "radiogroup", "aria-label": "how the trajectory is read" },
      this.axisButton("time", "wall clock"),
      this.axisButton("elapsed", "elapsed"),
      this.axisButton("sequence", "sequence"),
      this.axisButton("causality", "causality"),
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

  private axisButton(axis: Reading, label: string): HTMLElement {
    const title = {
      time: "place marks by wall-clock time, so rows that ran at one moment line up",
      elapsed: "place marks by the time since each root episode's own start, so runs begin together",
      sequence: "place marks by log position",
      causality: "read the run downward, as what caused what rather than as when",
    };
    return h(
      "button",
      {
        class: "traj-axis-btn",
        type: "button",
        role: "radio",
        "data-axis": axis,
        title: title[axis],
        onclick: () => this.setReading(axis),
      },
      label,
    );
  }

  setReading(reading: Reading): void {
    this.chosen = true;
    if (this.reading === reading) return;
    // Leaving the causality figure gives the conversation the whole run
    // back, because the scope it set has no meaning in the other readings.
    if (this.reading === "causality" && this.scoped !== null) {
      this.scoped = null;
      this.handlers.scope(null);
    }
    this.reading = reading;
    this.syncAxis();
    this.draw(true);
  }

  /** The node the conversation is scoped to, which the figure marks selected. */
  setScope(rowId: string | null): void {
    if (this.scoped === rowId) return;
    this.scoped = rowId;
    const layout = this.causalityLayout;
    this.handlers.scope(rowId === null || layout === null ? null : scopeFor(layout, rowId));
    this.draw(true);
  }

  private syncAxis(): void {
    for (const b of this.axisButtons.querySelectorAll<HTMLElement>("[role=radio]")) {
      const on = b.dataset.axis === this.reading;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.classList.toggle("active", on);
    }
  }

  update(episodes: TrajectoryEpisode[], causality: CausalityEpisode[], state: TrajectoryState): void {
    this.episodes = episodes;
    this.causality = causality;
    this.state = state;
    // A figure of independent roots opens on the elapsed axis: their wall
    // clocks may be days apart, which would draw each run as a sliver of
    // an axis that spans the gap between them. One tree opens on the wall
    // clock, where two rows at one x did run at one moment.
    if (!this.chosen) {
      const roots = episodes.filter((e) => e.depth === 0).length;
      const axis: Axis = roots > 1 ? "elapsed" : "time";
      if (axis !== this.reading) {
        this.reading = axis;
        this.syncAxis();
      }
    }
    this.draw(false);
  }

  /** Redraws when the pane is resized, without changing what it shows. */
  resized(): void {
    this.draw(false);
  }

  /**
   * Layout units the rows of the last drawing took. The pane's derived
   * height is this plus the axis and the heading, taken at the reader's
   * text size, so a row with a node band opens a region tall enough to
   * hold it.
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
    // The figure is laid out in units of the default text size and drawn at
    // the reader's, so the pane's own pixels are divided by that multiple
    // before the layout sees them. A lane whose pitch is fixed in layout
    // units then still clears the lane label, which grows with the type.
    const scale = currentFontScale();
    const width = this.figure.clientWidth / scale;
    const height = this.figure.clientHeight / scale;
    const digest = [
      this.reading,
      width,
      height,
      scale,
      this.state.selected,
      this.state.cursor,
      this.scoped,
      this.episodes
        .map(
          (e) =>
            `${e.id}:${e.depth}:${e.identity}:${e.lastSeq}:${e.startTime}:${e.endTime ?? "-"}:${e.marks.length}:${e.firings.length}:${e.decisions.length}:${e.outcome ? e.outcome.kind : ""}`,
        )
        .join("|"),
    ].join("~");
    // A pane the layout has not measured yet draws nothing, and the digest
    // is left alone so that the draw happens once a width arrives. Storing
    // it here would make every later draw of the same content a repeat.
    if (width === 0) return;
    if (!force && digest === this.digest) return;
    this.digest = digest;
    if (this.reading === "causality") {
      this.drawCausality(width, scale);
      return;
    }
    const layout = layoutTrajectory({
      episodes: this.episodes,
      axis: this.reading,
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
    this.figure.appendChild(this.build(layout, width, scale));
  }

  /**
   * The causality figure in place of the timeline. It has no axis and no
   * horizontal domain, so the pane's width is only the room its labels
   * have; its height is what its rows need, and the pane scrolls to them.
   */
  private drawCausality(width: number, scale: number): void {
    const layout = layoutCausality(this.causality, width);
    this.causalityLayout = layout;
    this.rows = layout.height;
    clear(this.figure);
    this.card.hide();
    if (this.causality.length === 0) {
      this.figure.appendChild(h("div", { class: "empty sub" }, "no episodes"));
      return;
    }
    this.figure.appendChild(
      renderCausality(layout, this.scoped, this.card, {
        scope: (rowId) => this.setScope(rowId),
        select: (id) => this.handlers.select(id),
        reveal: (id, seq) => this.handlers.reveal(id, seq),
      }, scale),
    );
  }

  private build(layout: TrajectoryLayout, width: number, scale: number): SVGSVGElement {
    const figure = figureSvg("traj", width, layout.height, "episode trajectory", scale);

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

    // Every row's ground first, then the connectors over it, then what the
    // rows hold. A row that carries a ground would otherwise cover the
    // connector that leaves it, and the connector has to leave the firing
    // it belongs to rather than the row's lower edge.
    const grounds = svg("g", { class: "traj-grounds" });
    for (const row of layout.rows) {
      const hit = svg("rect", {
        class: `traj-hit${row.id === this.state.selected ? " selected" : ""}${row.id === this.state.cursor ? " cursor" : ""}`,
        x: 0,
        y: row.top,
        width,
        height: row.height,
      });
      hit.addEventListener("click", () => this.handlers.select(row.id));
      grounds.appendChild(hit);
    }
    figure.appendChild(grounds);

    // A connector drops at the x where the parent started the child and
    // turns once into the child's row, so it crosses an intervening row as
    // a hairline rather than sweeping along it.
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

    // A bracket in the gutter between the labels and the plot spans the
    // rows of one program, so that runs which are comparable read as a
    // set. Nothing else in the figure claims that gutter.
    const groups = svg("g", { class: "traj-groups" });
    for (const group of layout.groups) {
      const bracket = svg("path", {
        class: "traj-group",
        d: `M ${group.x + 3} ${group.y1} H ${group.x} V ${group.y2} H ${group.x + 3}`,
      });
      this.card.attach(
        bracket,
        () => `${group.runs} runs of ${group.name}`,
        () => "one program: equal `episode/start.identity`",
        () => shortIdentity(group.identity),
      );
      groups.appendChild(bracket);
    }
    figure.appendChild(groups);

    for (const row of layout.rows) figure.appendChild(this.rowElement(row));
    return figure;
  }

  private rowElement(row: TrajectoryRow): SVGGElement {
    const selected = row.id === this.state.selected;
    const group = svg("g", {
      class: `traj-row${selected ? " selected" : ""}${row.id === this.state.cursor ? " cursor" : ""}`,
      "data-id": row.id,
    });
    // The figure's one accent: a spine down the row's leading edge. The
    // ground under a selected row is neutral, because an accented fill
    // would be louder than every bar drawn over it.
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
    label.textContent = row.label;
    label.addEventListener("click", () => this.handlers.select(row.id));
    // The card carries the id the label no longer prints, and the whole
    // program name when the column was too narrow to set it.
    this.card.attach(
      label,
      () => row.name,
      () => row.id,
      () => (row.outcome ? outcomeLabel(row.outcome) : "running"),
    );
    group.appendChild(label);

    for (const lane of row.lanes) {
      const name = svg("text", { class: "traj-lane-label", x: lane.labelX, y: lane.y + 3 });
      name.textContent = lane.label;
      const fires = row.firings.filter((f) => f.node === lane.node).length;
      this.card.attach(name, () => lane.node, () => (fires === 1 ? "1 firing" : `${fires} firings`), () => "");
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
    const thick = MARK_THICKNESS.firing;
    group.appendChild(
      svg("rect", {
        class: "bar",
        x: firing.x,
        y: firing.y - thick / 2,
        width: Math.max(MARK_MIN_WIDTH, firing.w),
        height: thick,
        rx: 3,
      }),
    );
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
          svg("rect", {
            class: "seg",
            x: mark.x,
            y: mark.y - MARK_THICKNESS.tool / 2,
            width: Math.max(MARK_MIN_WIDTH, mark.w),
            height: MARK_THICKNESS.tool,
            rx: 3,
          }),
        );
        break;
      case "request": {
        // A request is a bar above the lifetime line, running from the call
        // to the answer, so its length is the time the answer took. The two
        // parts of that time carry the encoding the statistics view's
        // per-step bars use: the whole answer is the lower and fainter bar,
        // and the wait before the first token is the taller one over it.
        const width = Math.max(MARK_MIN_WIDTH, mark.w);
        const span = MARK_THICKNESS.requestSpan;
        const wait = MARK_THICKNESS.requestWait;
        group.appendChild(svg("rect", { class: "span", x: mark.x, y: mark.y - 2 - span, width, height: span, rx: 3 }));
        if (mark.head > 0) {
          group.appendChild(
            svg("rect", {
              class: "wait",
              x: mark.x,
              y: mark.y - 1 - wait,
              width: Math.max(MARK_MIN_WIDTH, mark.head),
              height: wait,
              rx: 3,
            }),
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
