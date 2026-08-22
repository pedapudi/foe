// Building SVG, and the two shapes every figure in the viewer takes.
//
// Both shapes are fit-to-width: the element is `width="100%"` with an
// explicit `viewBox`, an explicit `preserveAspectRatio`, and `role="img"`,
// so a figure never exceeds its pane and the pane never scrolls sideways to
// reach it. No figure pans or zooms; it reflows and is redrawn.
//
// The two differ in what happens between a resize and the redraw that
// follows it. A drawing whose marks must keep their shape scales uniformly
// and is anchored at its top left, so a dot stays round. A bar whose only
// mark is a rectangle stretches instead, because a bar carries its meaning
// in its length alone.

const SVG = "http://www.w3.org/2000/svg";

export function svg<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number | undefined> = {},
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined) el.setAttribute(key, String(value));
  }
  return el;
}

/**
 * A figure whose marks keep their shape: uniform scale, top left anchored.
 *
 * `scale` draws the figure larger than the units it was laid out in, which
 * is how a figure follows the reader's chosen text size. Laying out in one
 * set of units and drawing at another keeps every part of the figure in
 * proportion: a lane that must clear its own label still clears it when the
 * label grows, and a mark keeps its share of the row. `non-scaling-stroke`
 * holds every hairline at its own width through the same scale, so a larger
 * figure is a larger drawing rather than a heavier one.
 */
export function figureSvg(
  className: string,
  width: number,
  height: number,
  label: string,
  scale = 1,
): SVGSVGElement {
  return svg("svg", {
    class: className,
    width: "100%",
    height: Math.max(1, height) * scale,
    viewBox: `0 0 ${Math.max(1, width)} ${Math.max(1, height)}`,
    preserveAspectRatio: "xMinYMin meet",
    role: "img",
    "aria-label": label,
  });
}

/** A bar figure, which stretches to its host's width and holds no glyph. */
export function barSvg(
  className: string,
  width: number,
  height: number,
  label: string,
): SVGSVGElement {
  return svg("svg", {
    class: className,
    width: "100%",
    height,
    viewBox: `0 0 ${Math.max(1, width)} ${Math.max(1, height)}`,
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": label,
  });
}
