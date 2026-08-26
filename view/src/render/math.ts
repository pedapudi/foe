// The mathematics renderer, and the seam that decides which one runs.
//
// A renderer converts one TeX expression to an element. None is installed
// by default: `main.ts` installs the Temml one, and a build that omits
// that call omits Temml from the bundle, because nothing else imports it.
// An embedder that has its own converter installs that instead.
//
// Without a renderer, and for an expression the installed renderer
// rejects, the expression is shown as its own source in mono with the
// delimiters kept, so the text stays readable as the mathematics it is.

import { h } from "../dom.js";

/**
 * Converts one TeX expression to an element, or returns null when it
 * cannot. `display` distinguishes a block expression from an inline one.
 */
export type MathRenderer = (tex: string, display: boolean) => HTMLElement | null;

let renderer: MathRenderer | null = null;

/** Installs the renderer, or removes the installed one with null. */
export function setMathRenderer(next: MathRenderer | null): void {
  renderer = next;
  converted.clear();
}

/** True when a renderer is installed, so mathematics is typeset. */
export function hasMathRenderer(): boolean {
  return renderer !== null;
}

/**
 * Converted expressions, keyed by their source and mode. A conversation
 * redraws whenever its episode gains an event, and every redraw rebuilds
 * the rows it changed, so an expression is converted once and cloned
 * afterwards.
 */
const converted = new Map<string, HTMLElement>();

/**
 * Renders one TeX expression. The installed renderer runs only for an
 * expression not already converted; the result is cached and cloned.
 */
export function renderMath(tex: string, display: boolean): HTMLElement {
  const key = `${display ? "d" : "i"}:${tex}`;
  const cached = converted.get(key);
  if (cached) return cached.cloneNode(true) as HTMLElement;
  const host = renderer?.(tex, display) ?? source(tex, display);
  converted.set(key, host);
  return host.cloneNode(true) as HTMLElement;
}

/** The expression as its own source, in mono. */
function source(tex: string, display: boolean): HTMLElement {
  const host = h("span", { class: display ? "math untypeset display" : "math untypeset inline" });
  host.title = renderer ? "this expression is not valid TeX" : "this build does not typeset mathematics";
  host.textContent = display ? `$$${tex}$$` : `$${tex}$`;
  return host;
}
