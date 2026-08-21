// Small DOM helpers. The bundle uses no framework; every view builds
// elements with `h` and patches them in place.

export type Child = Node | string | number | null | undefined | false | Child[];

export type Attrs = Record<string, string | number | boolean | EventListener | undefined | null>;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: Attrs | null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith("on") && typeof value === "function") {
        el.addEventListener(key.slice(2), value);
      } else if (value === true) {
        el.setAttribute(key, "");
      } else {
        el.setAttribute(key, String(value));
      }
    }
  }
  append(el, children);
  return el;
}

export function append(el: Node, children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  }
}

export function clear(el: Element): void {
  while (el.firstChild) el.removeChild(el.firstChild);
}

/** Pretty-printed JSON; a value that cannot be serialized prints as text. */
export function pretty(value: unknown): string {
  if (value === undefined) return "undefined";
  try {
    return JSON.stringify(value, null, 2) ?? "undefined";
  } catch {
    return String(value);
  }
}

/** Single-line JSON, for inline argument display. */
export function compact(value: unknown): string {
  try {
    return JSON.stringify(value) ?? "undefined";
  } catch {
    return String(value);
  }
}

/**
 * A `<details>` whose body is built on first open, so that large payloads
 * cost nothing until a reader asks for them. `key` survives row updates:
 * the conversation view copies the open state between elements that share
 * one key.
 */
export function lazyDetails(
  summary: Child,
  build: () => Child,
  opts: { key?: string; class?: string; open?: boolean } = {},
): HTMLDetailsElement {
  const body = h("div", { class: "details-body" });
  const el = h(
    "details",
    { class: opts.class, "data-key": opts.key, open: opts.open === true },
    h("summary", null, summary),
    body,
  );
  let built = false;
  const fill = () => {
    if (built) return;
    built = true;
    append(body, [build()]);
  };
  el.addEventListener("toggle", () => {
    if (el.open) fill();
  });
  if (opts.open) fill();
  return el;
}

export function fmtTime(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  const d = new Date(ms);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}.${p(d.getUTCMilliseconds(), 3)}`;
}

export function fmtDate(ms: number): string {
  if (!Number.isFinite(ms) || ms === 0) return "";
  return new Date(ms).toISOString().replace("T", " ").replace("Z", " UTC");
}

export function fmtInt(n: number): string {
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "";
}

export function fmtDuration(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m} min ${s} s`;
}

export function lineCount(text: string): number {
  if (text.length === 0) return 0;
  let n = 1;
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) n++;
  return n;
}
