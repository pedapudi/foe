// Building one conversation mark: a small drawing that stands where a word
// in a box used to stand. It carries its own accessible name, because it
// replaces a word a screen reader read, and `data-mark`, so that the pane
// opens the shared hovercard over it without a listener per mark.

import { MARKS, MARK_HEIGHT, MARK_WIDTH } from "../marks.js";
import type { MarkKind } from "../marks.js";
import { svg } from "./svg.js";

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
