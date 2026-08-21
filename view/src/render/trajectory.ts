// The trajectory pane: one row per episode, drawn as line art in the
// register docs/design-language.md sets out. Structure is faint, the
// selected row carries the one accent, a spawn edge is solid and a fork
// edge dashed, and outcome colour is earned by direction.
//
// The pane holds no state of its own beyond the axis choice and the
// hovercard: it is handed the episodes and redraws when a digest of what
// it would draw changes.

import { clear, fmtDuration, fmtTime, h } from "../dom.js";
import { outcomeLabel } from "../fold.js";
import type { Axis, PlacedMark, TrajectoryEpisode, TrajectoryLayout } from "../trajectory.js";
import { MARK_MIN_WIDTH, layoutTrajectory } from "../trajectory.js";
import type { Outcome } from "../types.js";
import { str } from "../types.js";
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

export class TrajectoryView {
  readonly el: HTMLElement;
  private readonly figure = h("div", { class: "traj-figure" });
  private readonly card = h("div", { class: "traj-card", hidden: true });
  private readonly axisButtons: HTMLElement;
  private axis: Axis = "time";
  private digest = "";
  private episodes: TrajectoryEpisode[] = [];
  private state: TrajectoryState = { selected: null, cursor: null };

  constructor(private readonly handlers: TrajectoryHandlers) {
    this.axisButtons = h(
      "span",
      { class: "traj-axis", role: "radiogroup", "aria-label": "trajectory x axis" },
      this.axisButton("time", "wall clock"),
      this.axisButton("sequence", "sequence"),
    );
    this.el = h(
      "section",
      { class: "pane-trajectory", "aria-label": "trajectory" },
      h("div", { class: "pane-head" }, h("h2", null, "trajectory"), h("span", { class: "spacer" }), this.axisButtons),
      h("div", { class: "traj-body" }, this.figure, this.card),
    );
    this.figure.addEventListener("scroll", () => this.hideCard());
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
        .map((e) => `${e.id}:${e.depth}:${e.lastSeq}:${e.startTime}:${e.endTime ?? "-"}:${e.marks.length}:${e.outcome ? e.outcome.kind : ""}`)
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
    clear(this.figure);
    this.hideCard();
    if (this.episodes.length === 0) {
      this.figure.appendChild(h("div", { class: "empty sub" }, "no episodes"));
      return;
    }
    this.figure.appendChild(this.build(layout, width));
  }

  private build(layout: TrajectoryLayout, width: number): SVGSVGElement {
    const figure = figureSvg("traj", width, layout.height, "episode trajectory");

    // The axis: leader ticks with a small mono label, and no gridlines.
    const axis = svg("g", { class: "traj-axisline" });
    for (const tick of layout.ticks) {
      axis.appendChild(svg("line", { class: "tick", x1: tick.x, y1: layout.plot.top - 6, x2: tick.x, y2: layout.plot.top - 1 }));
      const label = svg("text", { class: "tick-label", x: tick.x, y: layout.plot.top - 9, "text-anchor": "middle" });
      label.textContent = tick.label;
      axis.appendChild(label);
    }
    axis.appendChild(svg("line", { class: "baseline", x1: layout.plot.left, y1: layout.plot.top - 1, x2: layout.plot.right, y2: layout.plot.top - 1 }));
    figure.appendChild(axis);

    // Connectors first, so that rows paint over them.
    const edges = svg("g", { class: "traj-edges" });
    for (const edge of layout.connectors) {
      const mid = (edge.from.y + edge.to.y) / 2;
      edges.appendChild(
        svg("path", {
          class: `traj-edge${edge.fork ? " fork" : ""}`,
          d: `M ${edge.from.x} ${edge.from.y} C ${edge.from.x} ${mid}, ${edge.to.x} ${mid}, ${edge.to.x} ${edge.to.y}`,
        }),
      );
    }
    figure.appendChild(edges);

    for (const row of layout.rows) {
      const selected = row.id === this.state.selected;
      const group = svg("g", {
        class: `traj-row${selected ? " selected" : ""}${row.id === this.state.cursor ? " cursor" : ""}`,
        "data-id": row.id,
      });
      const hit = svg("rect", {
        class: "traj-hit",
        x: 0,
        y: row.y - layout.rowHeight / 2,
        width,
        height: layout.rowHeight,
      });
      hit.addEventListener("click", () => this.handlers.select(row.id));
      group.appendChild(hit);
      // The figure's one emphasis: a spine down the row's leading edge. A
      // filled row would compete with the bars drawn inside it.
      group.appendChild(
        svg("line", {
          class: "traj-spine",
          x1: 1,
          y1: row.y - layout.rowHeight / 2 + 2,
          x2: 1,
          y2: row.y + layout.rowHeight / 2 - 2,
        }),
      );

      // The label: the program name, then the episode id in mono.
      const label = svg("text", { class: "traj-label", x: 6 + row.depth * 10, y: row.y + 3.5 });
      const name = svg("tspan");
      name.textContent = row.name === row.id ? row.id : row.name;
      label.appendChild(name);
      if (row.name !== row.id) {
        const id = svg("tspan", { class: "traj-id", dx: 6 });
        id.textContent = row.id;
        label.appendChild(id);
      }
      label.addEventListener("click", () => this.handlers.select(row.id));
      group.appendChild(label);

      // The lifetime bar, dashed past the last event while still running.
      group.appendChild(svg("line", { class: "traj-life", x1: row.x1, y1: row.y, x2: row.x2, y2: row.y }));
      if (row.running) {
        group.appendChild(svg("line", { class: "traj-life running", x1: row.x1, y1: row.y, x2: row.x2, y2: row.y }));
      }

      for (const mark of row.marks) group.appendChild(this.markElement(mark, layout));
      group.appendChild(this.outcomeGlyph(row));
      figure.appendChild(group);
    }
    return figure;
  }

  private markElement(mark: PlacedMark, layout: TrajectoryLayout): SVGGElement {
    const group = svg("g", { class: `traj-mark ${mark.kind}` });
    switch (mark.kind) {
      case "tool":
        // The lane's own band: the segment hangs below its baseline so that
        // the tool channel never touches the lifetime line above it.
        group.appendChild(
          svg("rect", { class: "seg", x: mark.x, y: mark.y - 2, width: Math.max(MARK_MIN_WIDTH, mark.w), height: 4.5, rx: 1.2 }),
        );
        break;
      case "request":
        // A request tick rises from the lifetime line rather than crossing
        // it, so that requests read above the line and tools below it.
        group.appendChild(
          svg("line", { class: "tick", x1: mark.x, y1: mark.y - (layout.rowHeight / 2 - 3), x2: mark.x, y2: mark.y + 1 }),
        );
        break;
      case "retry":
        group.appendChild(svg("path", { class: "glyph", d: `M ${mark.x - 3} ${mark.y - 3} l 6 6 M ${mark.x + 3} ${mark.y - 3} l -6 6` }));
        break;
      case "compaction":
        group.appendChild(svg("path", { class: "glyph", d: `M ${mark.x} ${mark.y - 4} l 4 4 l -4 4 l -4 -4 z` }));
        break;
      case "spawn":
        group.appendChild(svg("circle", { class: "glyph", cx: mark.x, cy: mark.y, r: 2.6 }));
        break;
    }
    group.addEventListener("pointerenter", (event) => this.showCard(event as PointerEvent, mark));
    group.addEventListener("pointerleave", () => this.hideCard());
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
  private outcomeGlyph(row: TrajectoryLayout["rows"][number]): SVGGElement {
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
    const title = svg("title");
    title.textContent = label;
    group.appendChild(title);
    const detail = outcome ? str((outcome as Record<string, unknown>).message) : "";
    const meta = row.name === row.id ? row.id : `${row.name} · ${row.id}`;
    group.addEventListener("pointerenter", (event) => this.showText(event as PointerEvent, label, meta, detail));
    group.addEventListener("pointerleave", () => this.hideCard());
    return group;
  }

  private showCard(event: PointerEvent, mark: PlacedMark): void {
    const lines: string[] = [`seq ${mark.seq}`, fmtTime(mark.time)];
    if (mark.durationMs > 0) lines.push(fmtDuration(mark.durationMs));
    this.showText(event, markLabel(mark), lines.join(" · "), mark.detail);
  }

  private showText(event: PointerEvent, head: string, meta: string, detail: string): void {
    clear(this.card);
    this.card.append(
      h("div", { class: "traj-card-head" }, head),
      meta ? h("div", { class: "traj-card-meta" }, meta) : "",
      detail ? h("div", { class: "traj-card-detail" }, detail) : "",
    );
    this.card.hidden = false;
    const box = this.figure.getBoundingClientRect();
    const width = this.card.offsetWidth;
    const left = Math.min(Math.max(4, event.clientX - box.left + 12), Math.max(4, box.width - width - 4));
    this.card.style.left = `${left}px`;
    this.card.style.top = `${event.clientY - box.top + 16}px`;
  }

  private hideCard(): void {
    this.card.hidden = true;
  }
}
