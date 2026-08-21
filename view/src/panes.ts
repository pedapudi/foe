// The three regions of the page and the grips that size them, persisted
// under `foe.panes` beside the appearance settings in chrome.ts. One
// function applies a size, and every control that changes one calls it, so
// a grip, the keyboard, and stored state never disagree.
//
// The sidebar is stored in pixels because its useful width does not follow
// the window. The two row splits are stored as a fraction of the column
// they divide, so a taller window gives the timeline and the details pane
// proportionally more room.

import { h } from "./dom.js";

export interface PaneSizes {
  /** Width of the episodes and details column, in pixels. */
  sidebar: number;
  /** Share of that column's height the details pane takes, from 0 to 1. */
  details: number;
  /** Share of the right column's height the trajectory pane takes. */
  trajectory: number;
}

/** The height and width available to the splits, measured from the layout. */
export interface PaneExtent {
  width: number;
  leftHeight: number;
  rightHeight: number;
}

export const PANE_DEFAULTS: PaneSizes = { sidebar: 300, details: 0.3, trajectory: 0.35 };

export const PANE_LIMITS = {
  /** Narrowest and widest the sidebar goes, before the window limit. */
  sidebarMin: 210,
  sidebarMax: 640,
  /** The window may not give the sidebar more than this share of its width. */
  sidebarShare: 0.6,
  /** Shortest either part of a row split goes. */
  rowMin: 96,
};

const KEY_PANES = "foe.panes";

/** How far one arrow key moves a grip. */
export const PANE_STEP = 16;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/**
 * The sizes as the layout can actually use them. A split whose column is
 * too short to honour both minimums is placed at the middle, because
 * neither part can be given its minimum.
 */
export function clampPanes(sizes: PaneSizes, extent: PaneExtent): PaneSizes {
  const { sidebarMin, sidebarMax, sidebarShare, rowMin } = PANE_LIMITS;
  const widest = extent.width > 0 ? Math.max(sidebarMin, Math.min(sidebarMax, extent.width * sidebarShare)) : sidebarMax;
  const split = (fraction: number, height: number): number => {
    if (!Number.isFinite(fraction)) return 0.5;
    if (height <= 0) return clamp(fraction, 0, 1);
    if (height < rowMin * 2) return 0.5;
    return clamp(fraction, rowMin / height, 1 - rowMin / height);
  };
  return {
    sidebar: clamp(Number.isFinite(sizes.sidebar) ? sizes.sidebar : PANE_DEFAULTS.sidebar, sidebarMin, widest),
    details: split(sizes.details, extent.leftHeight),
    trajectory: split(sizes.trajectory, extent.rightHeight),
  };
}

/** Reads the stored sizes through `get`, falling back to the defaults. */
export function parsePanes(raw: string | null): PaneSizes {
  if (raw === null) return { ...PANE_DEFAULTS };
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { ...PANE_DEFAULTS };
  }
  if (value === null || typeof value !== "object") return { ...PANE_DEFAULTS };
  const stored = value as Record<string, unknown>;
  const pick = (key: keyof PaneSizes): number =>
    typeof stored[key] === "number" && Number.isFinite(stored[key]) ? (stored[key] as number) : PANE_DEFAULTS[key];
  return { sidebar: pick("sidebar"), details: pick("details"), trajectory: pick("trajectory") };
}

export function serialisePanes(sizes: PaneSizes): string {
  return JSON.stringify({
    sidebar: Math.round(sizes.sidebar),
    details: Number(sizes.details.toFixed(4)),
    trajectory: Number(sizes.trajectory.toFixed(4)),
  });
}

// ---- the live layout ----

interface Host {
  root: HTMLElement;
  left: HTMLElement;
  right: HTMLElement;
}

let host: Host | null = null;
let current: PaneSizes = { ...PANE_DEFAULTS };
const gripElements = new Map<keyof PaneSizes, HTMLElement>();

function readStored(): string | null {
  try {
    return window.localStorage.getItem(KEY_PANES);
  } catch {
    return null;
  }
}

function writeStored(value: string): void {
  try {
    window.localStorage.setItem(KEY_PANES, value);
  } catch {
    // Storage can be unavailable; the sizes then last for the page only.
  }
}

export function currentPanes(): PaneSizes {
  return { ...current };
}

function extent(): PaneExtent {
  if (!host) return { width: 0, leftHeight: 0, rightHeight: 0 };
  return {
    width: host.root.clientWidth,
    leftHeight: host.left.clientHeight,
    rightHeight: host.right.clientHeight,
  };
}

/**
 * Applies the sizes to the layout and stores them. Every grip, key, and
 * reset goes through this one call.
 */
export function applyPanes(sizes: Partial<PaneSizes>): void {
  current = clampPanes({ ...current, ...sizes }, extent());
  if (!host) return;
  const style = host.root.style;
  const box = extent();
  style.setProperty("--pane-sidebar", `${Math.round(current.sidebar)}px`);
  style.setProperty("--pane-details", `${Math.round(current.details * box.leftHeight)}px`);
  style.setProperty("--pane-trajectory", `${Math.round(current.trajectory * box.rightHeight)}px`);
  writeStored(serialisePanes(current));
  for (const [name, el] of gripElements) {
    el.setAttribute("aria-valuenow", String(Math.round(name === "sidebar" ? current[name] : current[name] * 100)));
  }
  for (const fn of listeners) fn();
}

type Listener = () => void;
const listeners = new Set<Listener>();

/** Registers a callback run after any pane size changes, for panes that redraw. */
export function onPanesChange(fn: Listener): void {
  listeners.add(fn);
}

/** Reads the stored sizes and applies them to `root`. */
export function loadPanes(parts: Host): void {
  host = parts;
  current = parsePanes(readStored());
  applyPanes({});
  if (typeof ResizeObserver !== "undefined") {
    // A window resize changes what the fractions mean in pixels and can
    // push a size past a minimum, so the sizes are re-applied and clamped.
    new ResizeObserver(() => applyPanes({})).observe(parts.root);
  }
}

export type GripName = keyof PaneSizes;

interface GripOptions {
  name: GripName;
  label: string;
  /** "col" moves the sidebar edge sideways; "row" moves a split up and down. */
  orientation: "col" | "row";
  /** Turns a pointer position into a new value for this size. */
  valueAt(position: { x: number; y: number }): number;
  /** Turns an arrow-key step in pixels into a change in this size's units. */
  stepValue(pixels: number): number;
  /** The value at each end of the range, for Home and End. */
  ends(): [number, number];
}

/**
 * A divider: a hairline with a centred pill grip. It drags with pointer
 * capture, redraws once per frame, answers the arrow keys, Home, and End,
 * and returns to its default on a double click.
 */
export function buildGrip(options: GripOptions): HTMLElement {
  const el = h("div", {
    class: `grip ${options.orientation}`,
    role: "separator",
    tabindex: 0,
    "aria-label": options.label,
    "aria-orientation": options.orientation === "col" ? "vertical" : "horizontal",
    "aria-valuemin": "0",
    "aria-valuemax": options.name === "sidebar" ? String(PANE_LIMITS.sidebarMax) : "100",
    title: `${options.label}; double click to reset`,
  }, h("span", { class: "grip-pill", "aria-hidden": "true" }));
  gripElements.set(options.name, el);

  let frame = 0;
  let pending: number | null = null;
  const commit = () => {
    frame = 0;
    if (pending === null) return;
    applyPanes({ [options.name]: pending } as Partial<PaneSizes>);
    pending = null;
  };
  const move = (event: PointerEvent) => {
    pending = options.valueAt({ x: event.clientX, y: event.clientY });
    if (frame === 0) frame = requestAnimationFrame(commit);
  };
  el.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    el.setPointerCapture(event.pointerId);
    el.classList.add("dragging");
    document.body.classList.add(options.orientation === "col" ? "resizing-col" : "resizing-row");
    el.addEventListener("pointermove", move);
    event.preventDefault();
    el.focus();
  });
  const release = (event: PointerEvent) => {
    el.removeEventListener("pointermove", move);
    el.classList.remove("dragging");
    document.body.classList.remove("resizing-col", "resizing-row");
    if (el.hasPointerCapture(event.pointerId)) el.releasePointerCapture(event.pointerId);
    if (frame !== 0) {
      cancelAnimationFrame(frame);
      commit();
    }
  };
  el.addEventListener("pointerup", release);
  el.addEventListener("pointercancel", release);
  el.addEventListener("dblclick", () => applyPanes({ [options.name]: PANE_DEFAULTS[options.name] } as Partial<PaneSizes>));
  el.addEventListener("keydown", (event) => {
    const back = options.orientation === "col" ? "ArrowLeft" : "ArrowUp";
    const forward = options.orientation === "col" ? "ArrowRight" : "ArrowDown";
    const [low, high] = options.ends();
    let next: number | null = null;
    if (event.key === back) next = current[options.name] - options.stepValue(PANE_STEP);
    else if (event.key === forward) next = current[options.name] + options.stepValue(PANE_STEP);
    else if (event.key === "Home") next = low;
    else if (event.key === "End") next = high;
    else if (event.key === "Enter" || event.key === " ") next = PANE_DEFAULTS[options.name];
    if (next === null) return;
    applyPanes({ [options.name]: next } as Partial<PaneSizes>);
    event.preventDefault();
  });
  return el;
}

/** The grip between the episodes column and the rest of the page. */
export function sidebarGrip(): HTMLElement {
  return buildGrip({
    name: "sidebar",
    label: "width of the episodes column",
    orientation: "col",
    valueAt: (at) => (host ? at.x - host.root.getBoundingClientRect().left : current.sidebar),
    stepValue: (pixels) => pixels,
    ends: () => [0, PANE_LIMITS.sidebarMax],
  });
}

/** A grip that splits one column into an upper and a lower pane. */
export function rowGrip(name: "details" | "trajectory", label: string): HTMLElement {
  const column = () => (name === "details" ? host?.left : host?.right);
  return buildGrip({
    name,
    label,
    orientation: "row",
    valueAt: (at) => {
      const el = column();
      if (!el) return current[name];
      const box = el.getBoundingClientRect();
      if (box.height <= 0) return current[name];
      // The details pane is below its grip and the trajectory pane above
      // its own, so the two fractions run in opposite directions.
      const above = (at.y - box.top) / box.height;
      return name === "details" ? 1 - above : above;
    },
    stepValue: (pixels) => {
      const el = column();
      const height = el ? el.clientHeight : 0;
      const step = height > 0 ? pixels / height : 0.02;
      return name === "details" ? -step : step;
    },
    ends: () => (name === "details" ? [1, 0] : [0, 1]),
  });
}
