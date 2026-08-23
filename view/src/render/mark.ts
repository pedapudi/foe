// Building one conversation mark: a small drawing that stands where a word
// in a box used to stand. It carries its own accessible name, because it
// replaces a word a screen reader read, and `data-mark`, so that the pane
// opens the shared hovercard over it without a listener per mark.

import { MARKS, MARK_HEIGHT, MARK_WIDTH } from "../marks.js";
import type { MarkKind } from "../marks.js";
import { svg } from "./svg.js";

/**
 * One mark's parts as a group centred on `(x, y)` in a figure's own units,
 * for a drawing that places its marks rather than setting them in a line
 * of text. `scale` fits the mark to the figure: the geometry is written
 * once, in `marks.ts`, and both callers draw the same shape.
 */
export function markGroup(kind: MarkKind, x: number, y: number, scale = 1): SVGGElement {
  const group = svg("g", {
    class: `mark ${kind}`,
    transform: `translate(${x - (MARK_WIDTH / 2) * scale} ${y - (MARK_HEIGHT / 2) * scale}) scale(${scale})`,
    "data-mark": kind,
  });
  for (const part of MARKS[kind].parts) group.appendChild(svg(part.shape, part.attrs));
  return group;
}

export function markSvg(kind: MarkKind): SVGSVGElement {
  const mark = MARKS[kind];
  const el = svg("svg", {
    class: `mark ${kind}`,
    viewBox: `0 0 ${MARK_WIDTH} ${MARK_HEIGHT}`,
    preserveAspectRatio: "xMidYMid meet",
    role: "img",
    "aria-label": mark.label,
    "data-mark": kind,
  });
  for (const part of mark.parts) el.appendChild(svg(part.shape, part.attrs));
  return el;
}
