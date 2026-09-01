// The causality figure: what caused what, running down the pane. Line art
// in the register docs/design-language.md sets out, drawn from what
// src/causality.ts placed.
//
// Three layers, in this order: the row highlight, the trajectory strokes,
// the labels. A selected row must never hide a line and a line must never
// cross out a name, so the strokes sit at `z-index: 2`, the labels at `3`,
// and the rows carry no `z-index` at all — a `z-index` on a row would form
// a stacking context and trap its label under the strokes. Nothing in the
// stack is transparent.
//
// Lane colour distinguishes branches and never carries a verdict; hue carries the
// outcome and carries it only on the marks, so a lane is never read as a
// judgement of the work on it.

import { h } from "../dom.js";
import { outcomeLabel } from "../fold.js";
import { edgePath } from "../causality.js";
import { DEPTH_INDENT } from "../causality.js";
import type {
  CausalityEdge,
  CausalityLane,
  CausalityLayout,
  PlacedCall,
  PlacedRow,
} from "../causality.js";
import { str } from "../types.js";
import { Hovercard } from "./hovercard.js";
import { markGroup } from "./mark.js";
import { svg } from "./svg.js";
import { outcomeRole } from "./tree.js";

export interface CausalityHandlers {
  /** Scopes the conversation to one row, or back to the whole run for null. */
  scope(rowId: string | null): void;
  /** Selects an episode, which the lane's foot and its label do. */
  select(id: string): void;
  /** Selects an episode and brings its conversation to one log position. */
  reveal(id: string, seq: number): void;
}

/** How much of the shared mark box a leaf of the figure takes. */
const CALL_MARK = 0.7;
const OUTCOME_MARK = 0.9;

/**
 * Clear ground this pane leaves between the drawing and the row names.
 * The layout claims no room past its marks, so where the text column
 * stands is this renderer's choice and not the layout's.
 */
const LABEL_GAP = 12;

/**
 * The whole figure: the three layers in one positioned box. `scale` is the
 * reader's text size, which the layout was computed without, so every
 * coordinate is multiplied by it here and the drawing grows with the type.
 */
export function renderCausality(
  layout: CausalityLayout,
  selected: string | null,
  card: Hovercard,
  handlers: CausalityHandlers,
  scale: number,
): HTMLElement {
  const box = h("div", { class: "caus" });
  box.style.height = `${layout.height * scale}px`;
  const textLeft = layout.marksWidth + LABEL_GAP;

  // 1. the row highlights, which are also the click targets.
  for (const row of layout.rows) box.appendChild(rowGround(row, selected, handlers, scale));

  // 2. the strokes.
  box.appendChild(laneStrokes(layout, selected, card, handlers, scale));

  // 3. the labels.
  for (const row of layout.rows) box.appendChild(rowLabel(row, textLeft, selected, scale));

  return box;
}

/**
 * A row's ground: the band the highlight fills and the surface a click on
 * nothing in particular lands on. It carries no `z-index`, so the strokes
 * above it are not trapped under it.
 */
function rowGround(row: PlacedRow, selected: string | null, handlers: CausalityHandlers, scale: number): HTMLElement {
  const ground = h("div", {
    class: `caus-row${row.id === selected ? " selected" : ""}`,
    "data-row": row.id,
    role: "button",
    tabindex: 0,
    "aria-pressed": row.id === selected ? "true" : "false",
  });
  ground.style.top = `${row.top * scale}px`;
  ground.style.height = `${row.height * scale}px`;
  const toggle = () => handlers.scope(row.id === selected ? null : row.id);
  ground.addEventListener("click", toggle);
  ground.addEventListener("keydown", (event) => {
    const key = (event as KeyboardEvent).key;
    if (key !== "Enter" && key !== " ") return;
    event.preventDefault();
    toggle();
  });
  return ground;
}

/**
 * A row's name. Semantic role first and the durable identifier second: the
 * step number rides alongside in faint, and the indent carries tree depth
 * so nesting reads in the text column even though lanes are allocated by
 * column rather than by depth.
 */
function rowLabel(row: PlacedRow, textLeft: number, selected: string | null, scale: number): HTMLElement {
  const label = h(
    "div",
    { class: `caus-label${row.id === selected ? " selected" : ""} ${row.kind}` },
    h("span", { class: "name" }, row.label),
    row.aside ? h("span", { class: "aside" }, row.aside) : null,
  );
  // This view nests the text as well as the gutter, because it is read
  // beside a conversation rather than as one, and the tree it shows is
  // shallow enough that a ragged edge costs nothing.
  label.style.left = `${(textLeft + row.depth * DEPTH_INDENT) * scale}px`;
  label.style.top = `${row.top * scale}px`;
  label.style.height = `${row.height * scale}px`;
  return label;
}

/**
 * The drawing alone: the lanes, the curves that join them, and what each
 * row puts on its own lane. Both views of a run draw it — the figure beside
 * a conversation and the unified outline — so it is written once and takes
 * a layout rather than reading any state of its own.
 */
export function laneStrokes(
  layout: CausalityLayout,
  selected: string | null,
  card: Hovercard,
  handlers: CausalityHandlers,
  scale: number,
): SVGSVGElement {
  const figure = svg("svg", {
    class: "caus-strokes",
    width: layout.marksWidth * scale,
    height: layout.height * scale,
    viewBox: `0 0 ${layout.marksWidth} ${layout.height}`,
    preserveAspectRatio: "xMinYMin meet",
    role: "img",
    "aria-label": "what caused what",
  });

  // Every lane is one continuous line from its first row to its last,
  // stretched to reach every curve that joins it, so no curve ends in
  // empty space and a lane of one row is still a line.
  const lanes = svg("g", { class: "caus-lanes" });
  for (const lane of layout.lanes) lanes.appendChild(laneElement(lane, card, handlers));
  figure.appendChild(lanes);

  // Cubic curves with their control points on the midline. No arrowheads:
  // time runs down, so direction is already unambiguous and a head on
  // every edge would be noise.
  const edges = svg("g", { class: "caus-edges" });
  for (const edge of layout.edges) edges.appendChild(edgeElement(edge));
  figure.appendChild(edges);

  for (const row of layout.rows) figure.appendChild(rowStrokes(row, selected, card, handlers));
  return figure;
}

function laneElement(lane: CausalityLane, card: Hovercard, handlers: CausalityHandlers): SVGGElement {
  const group = svg("g", { class: `caus-lane tone-${lane.tone} ${lane.kind}` });
  group.appendChild(svg("line", { class: "line", x1: lane.x, y1: lane.y1, x2: lane.x, y2: lane.y2 }));
  if (lane.outcome !== null) {
    const role = outcomeRole(lane.outcome);
    const kind = str(lane.outcome.kind) === "failed" ? "error" : "settled";
    const foot = svg("g", { class: `caus-outcome ${role}` });
    foot.appendChild(markGroup(kind, lane.x, lane.y2, OUTCOME_MARK));
    card.attach(
      foot,
      () => outcomeLabel(lane.outcome),
      () => lane.label,
      () => str((lane.outcome as Record<string, unknown>).message),
    );
    foot.addEventListener("click", (event) => {
      event.stopPropagation();
      handlers.select(lane.episodeId);
    });
    group.appendChild(foot);
  }
  return group;
}

function edgeElement(edge: CausalityEdge): SVGPathElement {
  return svg("path", { class: `caus-edge ${edge.kind} tone-${edge.tone}`, d: edgePath(edge) });
}

/**
 * What one row draws on its lane: the vertex it occupies, and one tick per
 * tool call with its mark at the end. No return edge is drawn for a call —
 * the lane continuing past the tick is the return.
 */
function rowStrokes(row: PlacedRow, selected: string | null, card: Hovercard, handlers: CausalityHandlers): SVGGElement {
  const group = svg("g", {
    class: `caus-node ${row.kind} tone-${row.tone}${row.id === selected ? " selected" : ""}`,
    "data-row": row.id,
  });
  group.appendChild(svg("circle", { class: "vertex", cx: row.x, cy: row.y, r: 2.4 }));
  for (const call of row.calls) group.appendChild(callElement(row, call, card, handlers));
  if (row.kind === "node" && row.firings.length > 1) {
    // A node the run entered more than once is one row and a loop edge.
    // The count says how many passes the scoped conversation will hold.
    const passes = svg("text", { class: "caus-passes", x: row.x + 6, y: row.y + 2.5 });
    passes.textContent = `${row.firings.length}`;
    group.appendChild(passes);
  }
  return group;
}

function callElement(row: PlacedRow, call: PlacedCall, card: Hovercard, handlers: CausalityHandlers): SVGGElement {
  const group = svg("g", { class: `caus-call${call.failed ? " failed" : ""}` });
  group.appendChild(svg("line", { class: "tick", x1: row.x, y1: call.y, x2: call.x, y2: call.y }));
  group.appendChild(markGroup(call.failed ? "error" : "call", call.x, call.y, CALL_MARK));
  const target = call.childId === null ? call.subject : call.childName;
  card.attach(
    group,
    () => (call.childId === null ? call.name : `spawn ${call.childName}`),
    () => (target === "" ? row.label : target),
    () => (call.failed ? "the tool reported a failure" : call.childId === null ? "" : call.childId),
  );
  group.addEventListener("click", (event) => {
    event.stopPropagation();
    if (call.childId !== null) handlers.select(call.childId);
    else handlers.reveal(row.episodeId, row.fromSeq);
  });
  return group;
}
