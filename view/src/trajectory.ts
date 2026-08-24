// Where every mark of the trajectory pane goes. The module holds no
// element and reads no document, so the placement rules are tested
// directly; render/trajectory.ts draws what this returns.
//
// One row per episode, in the order the episode tree lists them. The x
// axis is wall-clock time or log position, and both map linearly onto the
// same plot area, so switching axes moves marks and changes nothing else.
//
// A row holds the channels an episode's work nests into, stacked by
// position because duration does not separate them. Model requests sit
// above the lifetime line; under it come the node band of a workflow
// episode, one lane per node that fired, and then the tool lane. The order
// down the row is the order of containment: the episode, the nodes of its
// graph, and the calls it made.

import { programRuns } from "./lineage.js";
import type { Outcome } from "./types.js";

/**
 * What x measures. `time` is the wall clock, which is right for one tree,
 * where two rows at one x ran at one moment. `elapsed` is the time since
 * the start of the row's own root, which lets runs started days apart be
 * read against each other: every root begins at zero and the axis spans
 * the longest run rather than the interval between the runs. `sequence` is
 * the log position.
 */
export type Axis = "time" | "elapsed" | "sequence";

export type MarkKind = "request" | "tool" | "compaction" | "retry" | "spawn";

/**
 * Where a mark that occupies an interval ends, and where the first token of
 * a model request arrived inside it. Both axes are carried, because a
 * request spans events as well as time: the chunks it produced sit between
 * its `model/request` and its `assistant/message`, so it has a length in
 * log positions too.
 */
export interface Span {
  endTime: number;
  endSeq: number;
  /** Absent for a request that no token answered. */
  firstTokenTime: number | null;
  firstTokenSeq: number | null;
}

export interface Mark {
  kind: MarkKind;
  seq: number;
  /** Milliseconds since the epoch at which the event was written. */
  time: number;
  /** Milliseconds the mark spans; zero for an instant. */
  durationMs: number;
  /** The interval a request occupies, absent for a mark drawn at a point. */
  span: Span | null;
  /** Tool name, retry cause, compaction trigger, or spawned child id. */
  label: string;
  /** One line of detail for the hovercard. */
  detail: string;
}

/**
 * One firing of one declared workflow node, from its `workflow/node-start`
 * to the `workflow/node-end` that closes it. `durationMs` is the length the
 * node itself reported and is absent while the firing runs; the span the
 * figure draws runs between the two events, which is the interval the log
 * observed.
 */
export interface NodeFiring {
  node: string;
  fire: number;
  startSeq: number;
  startTime: number;
  /** Absent while the firing runs. */
  endSeq: number | null;
  endTime: number | null;
  durationMs: number | null;
  /** The error the firing ended with, empty when it ended cleanly. */
  error: string;
  /** The child episode a model node's firing ran, which has a row of its own. */
  childId: string | null;
  /** Seq of each event that produced a value this firing received. */
  inputs: number[];
}

/**
 * A point at which the run departed from the straight path through the
 * graph: a `workflow/branch` chose one label of a choice point, or a
 * `workflow/recovery` acted on a firing that failed.
 */
export interface NodeDecision {
  kind: "branch" | "recovery";
  node: string;
  fire: number;
  seq: number;
  time: number;
  /** The label a branch chose, or the action a recovery applied. */
  label: string;
  /** One line of detail for the hovercard. */
  detail: string;
}

export interface TrajectoryEpisode {
  id: string;
  name: string;
  /** `episode/start.identity`, which is equal for two runs of one program. */
  identity: string;
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
  /** Empty for an episode that runs the free loop rather than a graph. */
  firings: NodeFiring[];
  decisions: NodeDecision[];
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
  /**
   * Width of the leading part of a request's span: the wait before its
   * first token. Zero when the mark has no such part.
   */
  head: number;
  y: number;
}

export interface PlacedFiring extends NodeFiring {
  episodeId: string;
  x: number;
  w: number;
  y: number;
}

/** One lane of the node band: every firing of one node, at one height. */
export interface NodeLane {
  node: string;
  y: number;
  /** Left edge of the lane's name in the label column. */
  labelX: number;
  /** The node's name shortened to the room the column leaves it. */
  label: string;
}

export interface PlacedDecision extends NodeDecision {
  episodeId: string;
  x: number;
  y: number;
  /**
   * True when the label is written beside the mark. A label is dropped when
   * the one before it on the same lane would run into it, and the hovercard
   * is then the only place it reads.
   */
  showLabel: boolean;
}

/**
 * One segment of the rail that carries depth in the label column. `elbow`
 * marks the segment that turns into this row's own label; the others pass
 * through the row because a deeper row follows.
 */
export interface RowGuide {
  x: number;
  y1: number;
  y2: number;
  elbow: boolean;
}

export interface TrajectoryRow {
  id: string;
  name: string;
  depth: number;
  /** The lifetime line, which the request lane sits above and tools below. */
  y: number;
  /** Top edge of the row and the height it occupies. */
  top: number;
  height: number;
  /** Left and right edge of the lifetime bar. */
  x1: number;
  x2: number;
  /** True while the episode has no `episode/end`, which dashes the bar's tail. */
  running: boolean;
  outcome: Outcome | null;
  marks: PlacedMark[];
  lanes: NodeLane[];
  firings: PlacedFiring[];
  decisions: PlacedDecision[];
  guides: RowGuide[];
  /** Left edge of the row's own label. */
  labelX: number;
  /** The program name shortened to the room the column leaves it. */
  label: string;
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

/**
 * The rows of one program, bracketed beside the plot. A bracket is drawn
 * only where two or more roots carry one identity, because a bracket
 * around a single row groups nothing.
 */
export interface ProgramGroup {
  identity: string;
  /** The program name, which every root of the group shares. */
  name: string;
  /** Roots in the group, which is how many runs of the program there are. */
  runs: number;
  x: number;
  y1: number;
  y2: number;
}

export interface TrajectoryLayout {
  rows: TrajectoryRow[];
  groups: ProgramGroup[];
  connectors: Connector[];
  ticks: AxisTick[];
  plot: Plot;
  /** Axis values at the left and right edge of the plot. */
  domain: [number, number];
  /** Pixels every row takes together, which derives the pane's height. */
  rowsHeight: number;
  labelWidth: number;
  /** Height the figure needs, which exceeds the pane when rows overflow it. */
  height: number;
  axis: Axis;
}

/** The height of a row that holds no node band and no stacked tool calls. */
export const ROW_HEIGHT = 24;

/**
 * How far below the lifetime line the tool lane sits. Two channels share a
 * row: model requests and the outcome on the lifetime line, tool calls
 * below it. Position separates them when duration does not.
 */
export const TOOL_LANE = 5;

/** Shortest a tool segment is drawn, so a call of no measured length shows. */
export const MARK_MIN_WIDTH = 1.5;

/**
 * How far apart two calls of one co-timed cluster sit. A batch of parallel
 * calls lands on one x, so without a second dimension six calls draw as one
 * mark. Each keeps its own x and takes the next free height, so the count
 * is readable and no call is moved in time. The pitch exceeds the height
 * render/trajectory.ts gives a segment, so two stacked calls have clear
 * ground between them and a batch of six is six marks rather than a bar.
 */
export const TOOL_PITCH = 6;

/** Height of one lane of the node band, which holds one node's firings. */
export const NODE_LANE = 9;

/**
 * How thick each channel's mark is drawn, in layout units.
 *
 * Thickness is one of the three channels that separate a mark's kind, so no
 * two of these are equal, and each is under the lane that holds it, so a
 * mark never crosses into the channel below it. The request's two parts
 * differ from each other for the same reason: the wait before the first
 * token is drawn over the whole answer, and the taller of the two is the
 * one a reader is meant to read first.
 */
export const MARK_THICKNESS = {
  /** The whole answer, from the call to the message that answers it. */
  requestSpan: 4,
  /** The wait before the first token, drawn over the span. */
  requestWait: 6,
  /** One firing of one node of a declared graph. */
  firing: 5,
  /** One tool call. */
  tool: 3.5,
} as const;

/** Indent per level of the episode tree, in the label column. */
export const DEPTH_INDENT = 16;

/** Left edge of a row's label before its depth is added. */
const LABEL_PAD = 6;

const AXIS_HEIGHT = 20;
const PAD_RIGHT = 14;
const PAD_BOTTOM = 8;
/** Space between the axis and the first row. */
const AXIS_GAP = 6;
const LABEL_MIN = 116;
const LABEL_MAX = 230;
const TICK_TARGET = 5;
/** Clear space kept between two marks before they count as co-timed. */
const FAN_GAP = 2;
/** Pixels one character of a decision label takes, for collision alone. */
const DECISION_CHAR = 5.4;
/** Widest a character of a row label sets, which fits it to its column. */
const NAME_CHAR = 6.6;
/** The same for a lane's name, which sets smaller and in the data face. */
const LANE_CHAR = 5.4;
/** Room a decision's own glyph and the gap after it take before its label. */
export const DECISION_GLYPH = 8;

/**
 * The height the figure needs to show rows totalling `rowsHeight` layout
 * units: the axis, the gap below it, the rows themselves, and the padding
 * under the last row. The pane's default height is derived from this, so a
 * run with one episode opens a pane the size of one episode.
 *
 * `fontScale` is the multiplier the reader's text size applies. The figure
 * is laid out in units of the default text size and drawn at that
 * multiple, so the pane it needs grows with the type it holds.
 */
export function trajectoryContentHeight(rowsHeight: number, fontScale = 1): number {
  return (AXIS_HEIGHT + AXIS_GAP + Math.max(0, rowsHeight) + PAD_BOTTOM) * fontScale;
}

/**
 * Room the row labels take, which follows the pane width within limits and
 * widens by one indent per level of the deepest row, so that a deep tree
 * does not spend the whole column on its indents. The column never takes
 * more than a share of the pane, because the plot is what the figure is.
 */
export function labelWidthFor(width: number, depth = 0): number {
  const wanted = Math.max(LABEL_MIN + Math.max(0, depth) * DEPTH_INDENT, width * 0.26);
  return Math.round(Math.min(LABEL_MAX, width * 0.45, wanted));
}

/**
 * `text` shortened to what fits in `room` pixels, with an ellipsis where
 * anything was dropped. `charWidth` is the widest a character of the face
 * is expected to set, so the result never crosses into the plot.
 */
export function fitLabel(text: string, room: number, charWidth: number): string {
  const max = Math.floor(room / charWidth);
  if (text.length <= max) return text;
  if (max < 2) return "";
  return `${text.slice(0, max - 1)}…`;
}

/**
 * The axis value of one point of one episode. On the wall-clock axis the
 * value is the clock reading; on the elapsed axis it is the time since
 * `origin`, the start of the row's own root; on the sequence axis it is
 * the log position.
 */
function value(axis: Axis, time: number, seq: number, origin: number): number {
  if (axis === "sequence") return seq;
  return axis === "elapsed" ? time - origin : time;
}

/**
 * The clock reading each row measures from: the start of the root of its
 * own tree. Episodes arrive in tree order, so every row after a root and
 * before the next one belongs to that root. A child keeps its offset from
 * its root, so the elapsed axis aligns independent runs at zero and leaves
 * the shape of a tree intact.
 */
export function originsOf(episodes: TrajectoryEpisode[]): Map<string, number> {
  const origins = new Map<string, number>();
  let origin = 0;
  for (const episode of episodes) {
    if (episode.depth === 0) origin = episode.startTime;
    origins.set(episode.id, origin);
  }
  return origins;
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

/**
 * The lanes of one episode's node band, one per node that fired, ordered by
 * the first firing of each. That order is the order the run entered the
 * nodes, so a workflow that runs straight through reads as a staircase and
 * a cycle reads as a step back up.
 */
export function nodeLaneOrder(firings: NodeFiring[]): string[] {
  const order: string[] = [];
  for (const firing of firings) {
    if (!order.includes(firing.node)) order.push(firing.node);
  }
  return order;
}

/**
 * The height each mark of the tool lane takes, counted from the lane's own
 * baseline, so that marks landing on one x stack instead of overprinting. A
 * mark takes the lowest height whose last mark ended at least `FAN_GAP`
 * pixels before it starts, so a run of calls one after another stays on one
 * height and a batch issued together fans. Marks arrive in seq order and
 * keep it, so the earliest call of a batch is the one nearest the line.
 */
function stack(placed: { x: number; w: number }[]): number[] {
  const ends: number[] = [];
  return placed.map((mark) => {
    const right = mark.x + Math.max(MARK_MIN_WIDTH, mark.w);
    for (let height = 0; height < ends.length; height += 1) {
      if (ends[height]! + FAN_GAP <= mark.x) {
        ends[height] = right;
        return height;
      }
    }
    ends.push(right);
    return ends.length - 1;
  });
}

export function layoutTrajectory(input: TrajectoryInput): TrajectoryLayout {
  const { episodes, axis, width } = input;
  const deepest = episodes.reduce((most, e) => Math.max(most, e.depth), 0);
  const labelWidth = labelWidthFor(width, deepest);
  const plotLeft = labelWidth + 10;
  const plotRight = Math.max(plotLeft + 20, width - PAD_RIGHT);
  const top = AXIS_HEIGHT + AXIS_GAP;
  const origins = originsOf(episodes);
  const domain = domainOf(episodes, axis, input.now, origins);
  const span = domain[1] - domain[0];
  const scale = (v: number): number =>
    span <= 0 ? plotLeft : plotLeft + ((v - domain[0]) / span) * (plotRight - plotLeft);

  const byId = new Map(episodes.map((e) => [e.id, e]));
  const rowY = new Map<string, number>();
  const rows: TrajectoryRow[] = [];
  let cursor = top;
  episodes.forEach((episode, index) => {
    const lanes = nodeLaneOrder(episode.firings);
    const bandHeight = lanes.length * NODE_LANE;
    const y = cursor + ROW_HEIGHT / 2;
    const laneY = new Map(lanes.map((node, i) => [node, y + TOOL_LANE + i * NODE_LANE + NODE_LANE / 2]));
    rowY.set(episode.id, y);
    const origin = origins.get(episode.id) ?? episode.startTime;
    const running = episode.endTime === null;
    const endValue = running
      ? value(axis, input.now, episode.lastSeq, origin)
      : value(axis, episode.endTime!, episode.lastSeq, origin);

    const marks: PlacedMark[] = episode.marks.map((mark) => placeMark(mark, episode.id, axis, scale, y, origin));
    const tools = marks.filter((m) => m.kind === "tool");
    const heights = stack(tools);
    tools.forEach((mark, i) => {
      mark.y = y + TOOL_LANE + bandHeight + heights[i]! * TOOL_PITCH;
    });
    // The deepest stacked call decides how far the row extends past the
    // plain height; a row whose calls never collide extends by nothing.
    const fan = heights.reduce((deepest, height) => Math.max(deepest, height), 0) * TOOL_PITCH;

    const firings: PlacedFiring[] = episode.firings.map((firing) => {
      const start = scale(value(axis, firing.startTime, firing.startSeq, origin));
      const end =
        firing.endTime === null || firing.endSeq === null
          ? scale(value(axis, input.now, episode.lastSeq, origin))
          : scale(value(axis, firing.endTime, firing.endSeq, origin));
      return {
        ...firing,
        episodeId: episode.id,
        x: start,
        w: Math.max(MARK_MIN_WIDTH, end - start),
        y: laneY.get(firing.node) ?? y,
      };
    });
    const decisions = placeDecisions(episode, firings, axis, scale, laneY, y, origin);

    const height = bandHeight + ROW_HEIGHT + fan;
    const next = episodes[index + 1];
    const labelX = LABEL_PAD + episode.depth * DEPTH_INDENT;
    rows.push({
      id: episode.id,
      name: episode.name,
      depth: episode.depth,
      y,
      top: cursor,
      height,
      x1: scale(value(axis, episode.startTime, 0, origin)),
      x2: scale(endValue),
      running,
      outcome: episode.outcome,
      marks,
      lanes: lanes.map((node) => {
        const labelX = LABEL_PAD + (episode.depth + 1) * DEPTH_INDENT;
        return { node, y: laneY.get(node)!, labelX, label: fitLabel(node, labelWidth - labelX, LANE_CHAR) };
      }),
      firings,
      decisions,
      guides: guidesFor(episode.depth, cursor, height, y, next ? next.depth : -1),
      labelX,
      label: fitLabel(episode.name, labelWidth - labelX, NAME_CHAR),
    });
    cursor += height;
  });

  const plot: Plot = { left: plotLeft, right: plotRight, top, bottom: cursor };
  return {
    rows,
    groups: groupsFor(episodes, rows, plotLeft - 5),
    connectors: connectorsFor(episodes, byId, rowY, rows, axis, scale, origins),
    ticks: ticksFor(domain, axis, scale),
    plot,
    domain,
    rowsHeight: cursor - top,
    labelWidth,
    height: Math.max(input.height, cursor + PAD_BOTTOM),
    axis,
  };
}

/**
 * One mark on its episode's row. A tool call's height is set afterwards,
 * because it depends on every other call of the same row.
 */
function placeMark(
  mark: Mark,
  episodeId: string,
  axis: Axis,
  scale: (v: number) => number,
  y: number,
  origin: number,
): PlacedMark {
  const at = scale(value(axis, mark.time, mark.seq, origin));
  if (mark.kind === "tool" && mark.durationMs > 0 && axis !== "sequence") {
    // A tool result is written when the call returns, so the segment runs
    // back from the event by the duration the result reports.
    const x1 = scale(value(axis, mark.time - mark.durationMs, mark.seq, origin));
    return { ...mark, episodeId, x: x1, w: Math.max(MARK_MIN_WIDTH, at - x1), head: 0, y };
  }
  if (mark.kind === "retry" && mark.durationMs > 0 && axis !== "sequence") {
    // The backoff runs forward from the decision to retry, so the segment
    // ends where the next attempt is allowed to start. A delay has no
    // length in log positions, so on the sequence axis the mark is a tick.
    const end = scale(value(axis, mark.time + mark.durationMs, mark.seq, origin));
    return { ...mark, episodeId, x: at, w: end - at, head: 0, y };
  }
  // A request occupies the interval from the call to the answer, so it is a
  // span rather than a point. Its leading part is the wait before the first
  // token; the rest of it is the answer streaming in.
  if (mark.span) {
    const end = scale(value(axis, mark.span.endTime, mark.span.endSeq, origin));
    const first =
      mark.span.firstTokenTime === null || mark.span.firstTokenSeq === null
        ? null
        : scale(value(axis, mark.span.firstTokenTime, mark.span.firstTokenSeq, origin));
    return {
      ...mark,
      episodeId,
      x: at,
      w: Math.max(MARK_MIN_WIDTH, end - at),
      head: first === null ? 0 : Math.max(0, first - at),
      y,
    };
  }
  return { ...mark, episodeId, x: at, w: 0, head: 0, y };
}

/**
 * Every branch and recovery on the lane of the node it names. The label
 * stands beside the mark and is written only where the lane is clear for
 * its whole width: another label already claimed the room, or a firing of
 * the same node starts inside it, and either would set one thing over
 * another. The hovercard names a decision whose label is dropped.
 */
function placeDecisions(
  episode: TrajectoryEpisode,
  firings: PlacedFiring[],
  axis: Axis,
  scale: (v: number) => number,
  laneY: Map<string, number>,
  fallbackY: number,
  origin: number,
): PlacedDecision[] {
  const taken = new Map<string, number>();
  return episode.decisions.map((decision) => {
    const x = scale(value(axis, decision.time, decision.seq, origin));
    const from = x + DECISION_GLYPH;
    const right = from + decision.label.length * DECISION_CHAR;
    const clear =
      x >= (taken.get(decision.node) ?? -Infinity) &&
      !firings.some((f) => f.node === decision.node && f.x + f.w > from && f.x < right);
    if (clear) taken.set(decision.node, right + FAN_GAP);
    return {
      ...decision,
      episodeId: episode.id,
      x,
      y: laneY.get(decision.node) ?? fallbackY,
      showLabel: clear,
    };
  });
}

/**
 * The rail that carries depth in the label column. One segment stands at
 * each ancestor's indent: the nearest ancestor's turns into this row's
 * label, and a further one passes through the row when a deeper row
 * follows it. `nextDepth` is `-1` when this row is the last.
 */
export function guidesFor(
  depth: number,
  top: number,
  height: number,
  labelY: number,
  nextDepth: number,
): RowGuide[] {
  const out: RowGuide[] = [];
  for (let level = 0; level < depth; level += 1) {
    const x = LABEL_PAD + level * DEPTH_INDENT + 3;
    const elbow = level === depth - 1;
    const y2 = nextDepth > level ? top + height : elbow ? labelY : top;
    if (y2 > top || elbow) out.push({ x, y1: top, y2, elbow });
  }
  return out;
}

/**
 * A connector per child, from where the parent started it to where the
 * child's own bar starts. A model node's firing names the child it ran, so
 * the connector leaves that firing's span rather than the spawn ring, and
 * the graph and the child's row read as the same interval.
 */
function connectorsFor(
  episodes: TrajectoryEpisode[],
  byId: Map<string, TrajectoryEpisode>,
  rowY: Map<string, number>,
  rows: TrajectoryRow[],
  axis: Axis,
  scale: (v: number) => number,
  origins: Map<string, number>,
): Connector[] {
  const rowById = new Map(rows.map((r) => [r.id, r]));
  const connectors: Connector[] = [];
  for (const episode of episodes) {
    const childY = rowY.get(episode.id);
    if (childY === undefined) continue;
    // A child and the episode that started it share a root, so one origin
    // places both ends of the connector.
    const origin = origins.get(episode.id) ?? episode.startTime;
    const childX = scale(value(axis, episode.startTime, 0, origin));
    if (episode.parentId && byId.has(episode.parentId)) {
      const parent = byId.get(episode.parentId)!;
      const parentRow = rowById.get(parent.id)!;
      const firing = parentRow.firings.find((f) => f.childId === episode.id);
      if (firing) {
        connectors.push({ from: { x: firing.x, y: firing.y }, to: { x: childX, y: childY }, fork: false, childId: episode.id });
        continue;
      }
      const spawn = parent.marks.find((m) => m.kind === "spawn" && m.label === episode.id);
      const originValue = spawn
        ? value(axis, spawn.time, spawn.seq, origin)
        : value(axis, parent.startTime, 0, origin);
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
      const source = byId.get(fork.episodeId)!;
      const originValue = value(axis, timeAtSeq(source, fork.seq), fork.seq, origin);
      connectors.push({
        from: { x: scale(originValue), y: rowY.get(source.id)! },
        to: { x: childX, y: childY },
        fork: true,
        childId: episode.id,
      });
    }
  }
  return connectors;
}

function domainOf(
  episodes: TrajectoryEpisode[],
  axis: Axis,
  now: number,
  origins: Map<string, number>,
): [number, number] {
  if (episodes.length === 0) return [0, 1];
  if (axis === "sequence") {
    let high = 0;
    for (const e of episodes) high = Math.max(high, e.lastSeq);
    return [0, Math.max(1, high)];
  }
  let low = Infinity;
  let high = -Infinity;
  for (const e of episodes) {
    // On the elapsed axis every reading is taken from the start of the
    // row's own root, so the extent is the longest run rather than the
    // interval that separates the runs.
    const at = (time: number) => value(axis, time, 0, origins.get(e.id) ?? e.startTime);
    low = Math.min(low, at(e.startTime));
    high = Math.max(high, at(e.endTime ?? now));
    for (const mark of e.marks) {
      // A retry's duration is the backoff that follows it, so it extends the
      // domain; a tool call's duration precedes its event and does not.
      const end = mark.span ? mark.span.endTime : mark.kind === "retry" ? mark.time + mark.durationMs : mark.time;
      high = Math.max(high, at(end));
    }
    for (const firing of e.firings) high = Math.max(high, at(firing.endTime ?? firing.startTime));
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1];
  return [low, Math.max(high, low + 1)];
}

/**
 * One bracket per program with more than one root in the figure, spanning
 * every row of those roots, placed in the gutter between the label column
 * and the plot.
 */
function groupsFor(episodes: TrajectoryEpisode[], rows: TrajectoryRow[], x: number): ProgramGroup[] {
  return programRuns(episodes).map((span) => ({
    identity: span.identity,
    name: span.name,
    runs: span.runs,
    x,
    y1: rows[span.first]!.top + 2,
    y2: rows[span.last]!.top + rows[span.last]!.height - 2,
  }));
}

/**
 * A short label for one axis value: elapsed time from the left edge, or a
 * log position. The step decides the unit and the precision, so that no
 * two adjacent labels read the same however short the run was.
 */
export function tickLabel(axis: Axis, value: number, origin: number, step: number): string {
  if (axis === "sequence") return String(Math.round(value));
  const elapsed = value - origin;
  if (step < 1000) return `${Math.round(elapsed)} ms`;
  const seconds = elapsed / 1000;
  if (seconds < 90) return `${seconds.toFixed(step < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/**
 * Ticks at round offsets from the left edge of the domain. The offsets are
 * measured from `low` rather than from zero, because on the time axis zero
 * is the Unix epoch and a tick there would carry no meaning.
 */
function ticksFor(domain: [number, number], axis: Axis, scale: (v: number) => number): AxisTick[] {
  const [low, high] = domain;
  const step = niceStep((high - low) / TICK_TARGET, axis);
  const out: AxisTick[] = [];
  for (let offset = 0; offset <= high - low + step / 1000 && out.length < 24; offset += step) {
    out.push({ x: scale(low + offset), label: tickLabel(axis, low + offset, low, step) });
  }
  if (out.length === 0) out.push({ x: scale(low), label: tickLabel(axis, low, low, step) });
  return out;
}

/** Rounds a step up to 1, 2, or 5 times a power of ten, in the axis's unit. */
export function niceStep(raw: number, axis: Axis): number {
  const floor = 1;
  if (!Number.isFinite(raw) || raw <= 0) return floor;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 5, 10]) {
    const step = factor * magnitude;
    if (step >= raw) return Math.max(floor, axis === "sequence" ? Math.round(step) : step);
  }
  return Math.max(floor, magnitude * 10);
}
