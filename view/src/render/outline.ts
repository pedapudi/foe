// The unified outline: the episode rail, the causality figure and the
// conversation as one scrollable hierarchy read at four depths. Collapsed
// to episodes it is the rail; opened to calls it is the figure; opened
// fully it is the transcript. A caret on any row opens one branch one
// level past the reading, so a reader can sit at `steps` and open a single
// call's result without expanding the run.
//
// The gutter nests and the text does not. Every label starts in one
// column, because structure is already drawn by the lanes and a ragged
// left edge makes a run tedious to skim: the eye reads the indent instead
// of the sequence. The one exception is a tool call and its result body,
// which step in one level under the step that issued them, because a call
// is part of the step above it rather than the next thing that happened.
// Exactly two label columns, therefore. Prose and result bodies break even
// that and run full width, because a diff would otherwise lose the room it
// needs.
//
// A child episode's rows sit under the call that spawned it rather than in
// log order, so reading order is not global order. Every row keeps its
// sequence number in the gutter; it is the only way to see where order
// jumped.
//
// Rows are not one height, so the figure is drawn in two passes: the rows
// are laid out and measured, then the lanes are computed from the heights
// they actually took. Both passes run again on every change of depth or
// caret.

import { clear, fmtInt, h } from "../dom.js";
import { DEPTHS, layoutLanes, visibleRows } from "../causality.js";
import type { CausalityOutline, CausalityRow, Depth } from "../causality.js";
import { renderMarkdown, renderToolText } from "./markup.js";
import { languageForPath } from "./shape.js";
import { Hovercard } from "./hovercard.js";
import { laneStrokes } from "./causality.js";
import type { CausalityHandlers } from "./causality.js";

/** How far a tool call and its result step in under the step that issued them. */
const CALL_INDENT = 14;

/** Clear ground between the drawing and the one column the labels start in. */
const LABEL_GAP = 12;

export interface OutlineHandlers extends CausalityHandlers {
  /** Opens or shuts one branch one level past the current reading. */
  toggle(rowId: string): void;
}

export interface OutlineState {
  depth: Depth;
  opened: ReadonlySet<string>;
  selected: string | null;
}

/**
 * Draws the outline into `host`, which must already be in the document:
 * the rows are measured after they are laid out and before the lanes are
 * computed from what they measured.
 */
export function drawOutline(
  host: HTMLElement,
  outline: CausalityOutline,
  state: OutlineState,
  card: Hovercard,
  handlers: OutlineHandlers,
  scale: number,
): void {
  const visible = visibleRows(outline, state.depth, state.opened);
  clear(host);
  if (visible.length === 0) {
    host.appendChild(h("div", { class: "empty sub" }, "no episodes"));
    return;
  }

  const board = h("div", { class: "outline" });
  const strokes = h("div", { class: "outline-strokes" });
  const list = h("div", { class: "outline-rows" });
  board.append(strokes, list);
  host.appendChild(board);

  // Pass one: the rows, at whatever height their content takes.
  const elements = visible.map((row) => {
    const el = rowElement(row, outline, state, handlers);
    list.appendChild(el);
    return el;
  });

  // Pass two: the lanes, from the heights the rows measured. A lane whose
  // ends were computed from a fixed pitch would miss the rows it must
  // reach the moment one row held a diff.
  const heights = elements.map((el) => el.offsetHeight / scale);
  const layout = layoutLanes(outline, visible, heights);
  const textLeft = layout.marksWidth + LABEL_GAP;
  board.style.setProperty("--outline-text", `${textLeft * scale}px`);
  strokes.appendChild(laneStrokes(layout, state.selected, card, handlers, scale));
  elements.forEach((el, i) => {
    const row = layout.rows[i];
    if (row) el.style.setProperty("--outline-step", `${(row.kind === "call" || row.kind === "result" ? CALL_INDENT : 0) * scale}px`);
  });
}

/**
 * One row: its log position in the gutter, its name in the one text
 * column, and, for prose and a result, a body under both that runs the
 * full width.
 */
function rowElement(row: CausalityRow, outline: CausalityOutline, state: OutlineState, handlers: OutlineHandlers): HTMLElement {
  const openable = outline.rows.some((r) => r.parent === row.id);
  const open = state.opened.has(row.id);
  const el = h("div", {
    class: [
      "outline-row",
      // The kind is prefixed: `call` and `result` are already class names
      // the conversation uses for its own blocks, and a bare kind here
      // would take their styling.
      `kind-${row.kind}`,
      row.id === state.selected ? "selected" : "",
      row.failed ? "failed" : "",
    ]
      .filter(Boolean)
      .join(" "),
    "data-row": row.id,
  });
  // The position is printed once per event: a row that continues the one
  // above it leaves the column blank rather than repeating the number.
  el.appendChild(
    row.showSeq === false
      ? h("div", { class: "outline-seq" })
      : h("div", { class: "outline-seq", title: "log position" }, fmtInt(row.seq)),
  );
  const caret = openable
    ? h("button", {
        class: `outline-caret${open ? " open" : ""}`,
        type: "button",
        "aria-expanded": open ? "true" : "false",
        "aria-label": open ? `close ${row.label || row.kind}` : `open ${row.label || row.kind}`,
        onclick: (event: Event) => {
          event.stopPropagation();
          handlers.toggle(row.id);
        },
      })
    : h("span", { class: "outline-caret empty" });
  const name = h(
    "div",
    { class: "outline-name" },
    caret,
    row.label ? h("span", { class: "label" }, row.label) : null,
    row.aside ? h("span", { class: "aside" }, row.aside) : null,
    row.kind === "node" && row.firings.length > 1 ? h("span", { class: "aside" }, `${row.firings.length} passes`) : null,
  );
  el.appendChild(name);
  if (row.body !== "") el.appendChild(bodyElement(row));
  el.addEventListener("click", () => handlers.scope(row.id === state.selected ? null : row.id));
  return el;
}

/**
 * What a row sets rather than names. The model's own words are prose and
 * are read as Markdown; a tool's result is output and is set as it came.
 */
function bodyElement(row: CausalityRow): HTMLElement {
  const body = h("div", { class: "outline-body" });
  if (row.kind === "prose") body.appendChild(renderMarkdown(row.body));
  else body.appendChild(renderToolText(row.body, languageForPath(row.label)));
  return body;
}

/** The depth control: the four readings, coarsest first. */
export function depthControl(current: Depth, choose: (depth: Depth) => void): HTMLElement {
  const title: Record<Depth, string> = {
    episodes: "episodes alone, which is the rail",
    steps: "the graph nodes and steps each episode ran",
    calls: "the tool calls each step made, with their targets",
    everything: "what the model said and what each tool returned",
  };
  return h(
    "span",
    { class: "traj-axis", role: "radiogroup", "aria-label": "how deep the outline reads" },
    DEPTHS.map((depth) =>
      h(
        "button",
        {
          class: `traj-axis-btn${depth === current ? " active" : ""}`,
          type: "button",
          role: "radio",
          "aria-checked": depth === current ? "true" : "false",
          "data-depth": depth,
          title: title[depth],
          onclick: () => choose(depth),
        },
        depth,
      ),
    ),
  );
}
