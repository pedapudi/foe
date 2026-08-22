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
import { trajectoryContentHeight } from "./trajectory.js";

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

/** The sidebar opens at the rail width `--dt-rail` in tokens.css names. */
export const PANE_DEFAULTS: PaneSizes = { sidebar: 288, details: 0.3, trajectory: 0.35 };

export const PANE_KEYS: (keyof PaneSizes)[] = ["sidebar", "details", "trajectory"];

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

/**
 * The share of the right column the trajectory takes when the reader has
 * not sized it: enough for every row, held at or above the shortest pane
 * and at or below half the column, so that a run of one episode opens a
 * pane the height of one episode and a run of forty still leaves the
 * conversation half the column.
 *
 * `chromeHeight` is the height of the pane's heading, which sits above the
 * figure inside the same region.
 */
export function fitTrajectory(rows: number, columnHeight: number, chromeHeight: number): number {
  if (!(columnHeight > 0)) return PANE_DEFAULTS.trajectory;
  const wanted = chromeHeight + trajectoryContentHeight(rows);
  const height = clamp(wanted, PANE_LIMITS.rowMin, columnHeight / 2);
  return height / columnHeight;
}

/** Reads the stored sizes, falling back to the defaults for what is absent. */
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

/**
 * The sizes the stored value actually names. A size the reader has never
 * moved is absent, which is how a derived size stays derived across a
 * reload.
 */
export function storedPaneKeys(raw: string | null): (keyof PaneSizes)[] {
  if (raw === null) return [];
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return [];
  }
  if (value === null || typeof value !== "object") return [];
  const stored = value as Record<string, unknown>;
  return PANE_KEYS.filter((key) => typeof stored[key] === "number" && Number.isFinite(stored[key]));
}

/** Writes `keys`, which default to all three sizes. */
export function serialisePanes(sizes: PaneSizes, keys: (keyof PaneSizes)[] = PANE_KEYS): string {
  const out: Record<string, number> = {};
  for (const key of keys) {
    out[key] = key === "sidebar" ? Math.round(sizes.sidebar) : Number(sizes[key].toFixed(4));
  }
  return JSON.stringify(out);
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

/**
 * Sizes the reader has set with a grip. A pinned size is stored and is
 * never recomputed; an unpinned one follows `derived`, or the default when
 * nothing derives it.
 */
const pinned = new Set<keyof PaneSizes>();

/** Sizes computed from what a region holds, for the sizes not pinned. */
const derived: Partial<PaneSizes> = {};

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
 * reset goes through this one call. A size named in `sizes` becomes
 * pinned, because naming one is what a reader moving a grip does; a size
 * left out follows what derives it until a grip pins it.
 */
export function applyPanes(sizes: Partial<PaneSizes>): void {
  const next: PaneSizes = { ...current };
  for (const key of PANE_KEYS) {
    const given = sizes[key];
    if (given !== undefined) {
      pinned.add(key);
      next[key] = given;
    } else if (!pinned.has(key)) {
      next[key] = derived[key] ?? PANE_DEFAULTS[key];
    }
  }
  current = clampPanes(next, extent());
  if (!host) return;
  const style = host.root.style;
  const box = extent();
  style.setProperty("--pane-sidebar", `${Math.round(current.sidebar)}px`);
  style.setProperty("--pane-details", `${Math.round(current.details * box.leftHeight)}px`);
  style.setProperty("--pane-trajectory", `${Math.round(current.trajectory * box.rightHeight)}px`);
  writeStored(serialisePanes(current, [...pinned]));
  for (const [name, el] of gripElements) {
    el.setAttribute("aria-valuenow", String(Math.round(name === "sidebar" ? current[name] : current[name] * 100)));
  }
  for (const fn of listeners) fn();
}

/**
 * Returns one size to whatever derives it, which a double click on its
 * grip does. A size with nothing deriving it returns to its default.
 */
export function resetPane(name: keyof PaneSizes): void {
  pinned.delete(name);
  applyPanes({});
}

/**
 * States how many rows the trajectory holds, so that its height follows
 * its content while the reader has not sized it. A spawn during a live run
 * adds a row and grows the region.
 */
export function setTrajectoryRows(rows: number, chromeHeight: number): void {
  const fraction = fitTrajectory(rows, extent().rightHeight, chromeHeight);
  if (derived.trajectory === fraction) return;
  derived.trajectory = fraction;
  if (!pinned.has("trajectory")) applyPanes({});
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
  const raw = readStored();
  current = parsePanes(raw);
  for (const key of storedPaneKeys(raw)) pinned.add(key);
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
  el.addEventListener("dblclick", () => resetPane(options.name));
  el.addEventListener("keydown", (event) => {
    const back = options.orientation === "col" ? "ArrowLeft" : "ArrowUp";
    const forward = options.orientation === "col" ? "ArrowRight" : "ArrowDown";
    const [low, high] = options.ends();
    if (event.key === "Enter" || event.key === " ") {
      resetPane(options.name);
      event.preventDefault();
      return;
    }
    let next: number | null = null;
    if (event.key === back) next = current[options.name] - options.stepValue(PANE_STEP);
    else if (event.key === forward) next = current[options.name] + options.stepValue(PANE_STEP);
    else if (event.key === "Home") next = low;
    else if (event.key === "End") next = high;
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
