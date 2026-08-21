// Where every mark of the trajectory pane goes. The module holds no
// element and reads no document, so the placement rules are tested
// directly; render/trajectory.ts draws what this returns.
//
// One row per episode, in the order the episode tree lists them. The x
// axis is wall-clock time or log position, and both map linearly onto the
// same plot area, so switching axes moves marks and changes nothing else.

import type { Outcome } from "./types.js";

export type Axis = "time" | "sequence";

export type MarkKind = "request" | "tool" | "compaction" | "retry" | "spawn";

export interface Mark {
  kind: MarkKind;
  seq: number;
  /** Milliseconds since the epoch at which the event was written. */
  time: number;
  /** Milliseconds the mark spans; zero for an instant. */
  durationMs: number;
  /** Tool name, retry cause, compaction trigger, or spawned child id. */
  label: string;
  /** One line of detail for the hovercard. */
  detail: string;
}

export interface TrajectoryEpisode {
  id: string;
  name: string;
  /** Depth in the episode tree, which indents the row label. */
  depth: number;
  startTime: number;
  /** The time of `episode/end`, absent while the episode runs. */
  endTime: number | null;
  /** The seq of the last event read. */
  lastSeq: number;
  outcome: Outcome | null;
  parentId: string | null;
  forkOrigin: { episodeId: string; seq: number } | null;
  marks: Mark[];
}

export interface TrajectoryInput {
  episodes: TrajectoryEpisode[];
  axis: Axis;
  width: number;
  height: number;
  /** Clock reading that bounds an episode still running, on the time axis. */
  now: number;
}

export interface PlacedMark extends Mark {
  episodeId: string;
  /** Left edge of the mark in pane coordinates. */
  x: number;
  /** Width of the mark; zero for a tick. */
  w: number;
  y: number;
}

export interface TrajectoryRow {
  id: string;
  name: string;
  depth: number;
  y: number;
  /** Left and right edge of the lifetime bar. */
  x1: number;
  x2: number;
  /** True while the episode has no `episode/end`, which dashes the bar's tail. */
  running: boolean;
  outcome: Outcome | null;
  marks: PlacedMark[];
}

export interface Connector {
  from: { x: number; y: number };
  to: { x: number; y: number };
  /** A fork edge is dashed and a spawn edge solid (docs/design-language.md). */
  fork: boolean;
  childId: string;
}

export interface AxisTick {
  x: number;
  label: string;
}

export interface Plot {
  left: number;
  right: number;
  top: number;
  /** Bottom of the last row, which is the drawn height. */
  bottom: number;
}

export interface TrajectoryLayout {
  rows: TrajectoryRow[];
  connectors: Connector[];
  ticks: AxisTick[];
  plot: Plot;
  /** Axis values at the left and right edge of the plot. */
  domain: [number, number];
  rowHeight: number;
  labelWidth: number;
  /** Height the figure needs, which exceeds the pane when rows overflow it. */
  height: number;
  axis: Axis;
}

export const ROW_HEIGHT = 24;
const AXIS_HEIGHT = 20;
const PAD_RIGHT = 14;
const PAD_BOTTOM = 8;
const LABEL_MIN = 116;
const LABEL_MAX = 230;
const TICK_TARGET = 5;

/** Room the row labels take, which follows the pane width within limits. */
export function labelWidthFor(width: number): number {
  return Math.round(Math.min(LABEL_MAX, Math.max(LABEL_MIN, width * 0.26)));
}

/**
 * The axis value of one point of one episode. On the time axis the value
 * is the wall clock; on the sequence axis it is the log position.
 */
function value(axis: Axis, time: number, seq: number): number {
  return axis === "time" ? time : seq;
}

/**
 * The time at which an episode reached a given seq, taken from the nearest
 * mark at or below it, or the episode's start when it has none. Used to
 * place a fork edge's origin on the time axis.
 */
export function timeAtSeq(episode: TrajectoryEpisode, seq: number): number {
  let best = episode.startTime;
  for (const mark of episode.marks) {
    if (mark.seq <= seq && mark.time >= best) best = mark.time;
  }
  return best;
}

export function layoutTrajectory(input: TrajectoryInput): TrajectoryLayout {
  const { episodes, axis, width } = input;
  const labelWidth = labelWidthFor(width);
  const plotLeft = labelWidth + 10;
  const plotRight = Math.max(plotLeft + 20, width - PAD_RIGHT);
  const rowHeight = ROW_HEIGHT;
  const top = AXIS_HEIGHT + 6;
  const bottom = top + episodes.length * rowHeight;
  const plot: Plot = { left: plotLeft, right: plotRight, top, bottom };
  const domain = domainOf(episodes, axis, input.now);
  const span = domain[1] - domain[0];
  const scale = (v: number): number =>
    span <= 0 ? plotLeft : plotLeft + ((v - domain[0]) / span) * (plotRight - plotLeft);

  const byId = new Map(episodes.map((e) => [e.id, e]));
  const rowY = new Map<string, number>();
  const rows: TrajectoryRow[] = episodes.map((episode, index) => {
    const y = top + index * rowHeight + rowHeight / 2;
    rowY.set(episode.id, y);
    const running = episode.endTime === null;
    const endValue = running
      ? value(axis, input.now, episode.lastSeq)
      : value(axis, episode.endTime!, episode.lastSeq);
    const marks: PlacedMark[] = episode.marks.map((mark) => {
      if (mark.kind === "tool" && mark.durationMs > 0 && axis === "time") {
        // A tool result is written when the call returns, so the segment
        // runs back from the event by the duration the result reports.
        const x1 = scale(mark.time - mark.durationMs);
        const x2 = scale(mark.time);
        return { ...mark, episodeId: episode.id, x: x1, w: Math.max(1.5, x2 - x1), y };
      }
      return { ...mark, episodeId: episode.id, x: scale(value(axis, mark.time, mark.seq)), w: 0, y };
    });
    return {
      id: episode.id,
      name: episode.name,
      depth: episode.depth,
      y,
      x1: scale(value(axis, episode.startTime, 0)),
      x2: scale(endValue),
      running,
      outcome: episode.outcome,
      marks,
    };
  });

  const connectors: Connector[] = [];
  for (const episode of episodes) {
    const childY = rowY.get(episode.id);
    if (childY === undefined) continue;
    const childX = scale(value(axis, episode.startTime, 0));
    if (episode.parentId && byId.has(episode.parentId)) {
      const parent = byId.get(episode.parentId)!;
      const spawn = parent.marks.find((m) => m.kind === "spawn" && m.label === episode.id);
      const originValue = spawn
        ? value(axis, spawn.time, spawn.seq)
        : value(axis, parent.startTime, 0);
      connectors.push({
        from: { x: scale(originValue), y: rowY.get(parent.id)! },
        to: { x: childX, y: childY },
        fork: false,
        childId: episode.id,
      });
      continue;
    }
    const fork = episode.forkOrigin;
    if (fork && byId.has(fork.episodeId)) {
      const origin = byId.get(fork.episodeId)!;
      const originValue = value(axis, timeAtSeq(origin, fork.seq), fork.seq);
      connectors.push({
        from: { x: scale(originValue), y: rowY.get(origin.id)! },
        to: { x: childX, y: childY },
        fork: true,
        childId: episode.id,
      });
    }
  }

  return {
    rows,
    connectors,
    ticks: ticksFor(domain, axis, scale),
    plot,
    domain,
    rowHeight,
    labelWidth,
    height: Math.max(input.height, bottom + PAD_BOTTOM),
    axis,
  };
}

function domainOf(episodes: TrajectoryEpisode[], axis: Axis, now: number): [number, number] {
  if (episodes.length === 0) return [0, 1];
  if (axis === "sequence") {
    let high = 0;
    for (const e of episodes) high = Math.max(high, e.lastSeq);
    return [0, Math.max(1, high)];
  }
  let low = Infinity;
  let high = -Infinity;
  for (const e of episodes) {
    low = Math.min(low, e.startTime);
    high = Math.max(high, e.endTime ?? now);
    for (const mark of e.marks) high = Math.max(high, mark.time);
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1];
  return [low, Math.max(high, low + 1)];
}

/** A short label for one axis value: elapsed time, or a log position. */
export function tickLabel(axis: Axis, value: number, origin: number): string {
  if (axis === "sequence") return String(Math.round(value));
  const seconds = (value - origin) / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 90) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function ticksFor(domain: [number, number], axis: Axis, scale: (v: number) => number): AxisTick[] {
  const [low, high] = domain;
  const step = niceStep((high - low) / TICK_TARGET, axis);
  const first = Math.ceil(low / step) * step;
  const out: AxisTick[] = [];
  for (let v = first; v <= high + step / 1000 && out.length < 24; v += step) {
    out.push({ x: scale(v), label: tickLabel(axis, v, low) });
  }
  if (out.length === 0) out.push({ x: scale(low), label: tickLabel(axis, low, low) });
  return out;
}

/** Rounds a step up to 1, 2, or 5 times a power of ten, in the axis's unit. */
export function niceStep(raw: number, axis: Axis): number {
  const floor = axis === "sequence" ? 1 : 1;
  if (!Number.isFinite(raw) || raw <= 0) return floor;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 5, 10]) {
    const step = factor * magnitude;
    if (step >= raw) return Math.max(floor, axis === "sequence" ? Math.round(step) : step);
  }
  return Math.max(floor, magnitude * 10);
}
