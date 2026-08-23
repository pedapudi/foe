// Where every mark of the causality figure goes. The module holds no
// element and reads no document, so the placement rules are tested
// directly; render/causality.ts draws what this returns.
//
// The trajectory's three axes run left to right and measure *when*. This
// figure runs top to bottom and shows *what caused what*. Left to right
// runs out of width at about eleven columns and a real run has more;
// downward scrolls the way a long run wants to scroll. Time runs down,
// structure runs across.
//
// The model is built from the log's obligation pairs and never from an
// inferred parent, so no edge is drawn that the log does not carry.
//
// A lane is earned. Two things open one: an episode, which has its own
// agent, budget and typed outcome and can outlive the call that made it;
// and a workflow, which branches and loops. Everything else is a mark on
// the lane it belongs to. A step is a mark on its episode's lane. A tool
// call is a short tick off the lane with its mark at the end, and no
// return edge is drawn: the lane continuing past the tick is the return. A
// call cannot diverge and cannot outlive its caller, so a merge for it
// would draw a fact the structure already guarantees, and calls are the
// largest count in the model.
//
// `spawn` is not special-cased: it is the call whose tick opens a lane,
// because what it created can outlive it.

import type { Row, StreamedCall, Summary } from "./fold.js";
import { obj, str } from "./types.js";
import type { Outcome } from "./types.js";

// What the figure is handed, folded out of one episode's log.

/**
 * One tool call of one step. `childId` is set for the call that opened a
 * lane; the call is drawn no differently for it.
 */
export interface CausalityCall {
  id: string;
  name: string;
  /**
   * The call's target reduced to one short field: a path's basename, a
   * program name, an identifier. Empty when the arguments carry no field
   * that is short by construction, because a free-text substring truncates
   * unpredictably and would say a different thing at every width.
   */
  target: string;
  /** True when the result reported a failure, which earns the cross. */
  failed: boolean;
  /** The episode this call opened, absent for a call that opened none. */
  childId: string | null;
  /** The opened episode's configured program name, which names a delegation. */
  childName: string;
}

/**
 * One step: one model request and the tool calls it produced.
 * `docs/design.md` defines the word, and `step` is a `u32` on the log's
 * own events. The model's response text is a turn, which is a different
 * thing and is never a node here.
 */
export interface CausalityStep {
  step: number;
  /** Log position the step's first event took, which orders the rows. */
  seq: number;
  /** Last log position the step covers, which bounds the scoped conversation. */
  endSeq: number;
  /**
   * True when a message answered the step's request. A step whose request
   * was retried until the budget ran out has none, and is still a step: it
   * is where the episode spent what it spent.
   */
  answered: boolean;
  calls: CausalityCall[];
}

/** One firing of one declared workflow node, from its start to its end. */
export interface CausalityFiring {
  node: string;
  fire: number;
  startSeq: number;
  /** Absent while the firing runs. */
  endSeq: number | null;
  /** The child episode a model node's firing ran. */
  childId: string | null;
  /** The error the firing ended with, empty when it ended cleanly. */
  error: string;
}

export interface CausalityEpisode {
  id: string;
  name: string;
  /** Depth in the episode tree, which indents the label. */
  depth: number;
  parentId: string | null;
  outcome: Outcome | null;
  /** The last log position read, which bounds a whole-episode scope. */
  lastSeq: number;
  steps: CausalityStep[];
  /** Empty for an episode that runs the free loop rather than a graph. */
  firings: CausalityFiring[];
}

// What the figure draws.

/** One tool call placed on its step's row: a tick ending in a mark. */
export interface PlacedCall extends CausalityCall {
  /** Where the tick ends and the mark sits. */
  x: number;
  y: number;
}

export type RowKind = "step" | "node";

export interface CausalityRow {
  /** Stable across redraws: the episode and what the row stands for. */
  id: string;
  kind: RowKind;
  episodeId: string;
  /** The lane the row is a mark on. */
  laneId: string;
  /** Depth in the episode tree, which indents the label and nothing else. */
  depth: number;
  /** Centre of the row, on its lane's column. */
  x: number;
  y: number;
  /** The lane colour this row's own marks take, which is its lane's. */
  tone: number;
  /** The band the row highlight fills. */
  top: number;
  height: number;
  /**
   * How far the row's name is indented from wherever its reader sets the
   * text column, which carries tree depth. Lanes are allocated by
   * occupancy rather than by depth, so the indent is the only place
   * nesting reads in the text.
   */
  indent: number;
  /** The row's semantic role, in the fewest words that stay true. */
  label: string;
  /** `step 4`, set alongside the label in faint; empty for a workflow node. */
  aside: string;
  calls: PlacedCall[];
  /** Every firing of this node, in order; empty for a step row. */
  firings: CausalityFiring[];
  /** Log positions a step row covers; a node row scopes by its firings. */
  fromSeq: number;
  toSeq: number;
  /** Episodes this row opened, which the scoped conversation includes. */
  opens: string[];
}

export type LaneKind = "episode" | "workflow";

export interface CausalityLane {
  id: string;
  kind: LaneKind;
  episodeId: string;
  /** The lane this one branched from, absent for a root episode's lane. */
  parentId: string | null;
  /** Column the lane holds while it is open, allocated lowest-free. */
  column: number;
  x: number;
  /**
   * The one continuous line, from the lane's first row to its last,
   * stretched to reach every curve that joins it.
   */
  y1: number;
  y2: number;
  /** Which of the cycled lane colours carries this branch's identity. */
  tone: number;
  /** The typed outcome drawn at the foot; only an episode lane has one. */
  outcome: Outcome | null;
  /** What the lane is, for the hovercard. */
  label: string;
}

export type EdgeKind = "branch" | "merge" | "loop";

export interface CausalityEdge {
  kind: EdgeKind;
  from: { x: number; y: number };
  to: { x: number; y: number };
  /**
   * How far the curve bows sideways. Zero puts the control points on the
   * midline between the two ends, which is right for a curve that changes
   * column. A loop returns to the column it left, where a midline control
   * point would draw a straight line, so it bows instead.
   */
  bow: number;
  tone: number;
  /** The lane the curve joins, for the hovercard and for the endpoint test. */
  laneId: string;
}

export interface CausalityLayout {
  rows: CausalityRow[];
  lanes: CausalityLane[];
  edges: CausalityEdge[];
  episodes: CausalityEpisode[];
  /**
   * Width the strokes and marks occupy, measured from the left edge. The
   * figure claims nothing beyond it and makes no assumption about what its
   * reader sets there: a caller that writes the row names beside the
   * drawing puts its text column at this plus whatever gap it wants, and a
   * caller that puts something else there is free to.
   */
  marksWidth: number;
  height: number;
}

/** Vertical distance between two rows. */
export const ROW_PITCH = 22;

/** Top of the first row. It clears one elbow, so a branch at the first row fits. */
export const TOP = 22;

/** Ground kept under the last row, which one elbow and a merge need. */
const BOTTOM = 22;

/** Column of the first lane. */
const LANE_LEFT = 14;

/** Distance between two lane columns. */
export const LANE_PITCH = 17;

/**
 * How far above its first row a branch leaves the parent, and how far
 * below its last row it rejoins. The parent's line is stretched to reach
 * both points, so no curve ends in empty space.
 *
 * It must stay under half a row, or one lane's merge and the next lane's
 * branch overlap on the parent's column and draw an X between two rows
 * that never met.
 */
export const ELBOW = ROW_PITCH / 2 - 1;

/** Length of the line a lane of one row gets, so its curves have ground to meet. */
export const STUB = 12;

/**
 * How far past its last row a lane runs before its outcome mark. Without
 * it the mark would sit on that row's own vertex and the two would read as
 * one glyph.
 */
export const OUTCOME_TAIL = 6;

/** How far the first call's mark sits from its lane. */
const CALL_TICK = 11;

/** Distance between two marks of one step, so a batch of six is countable. */
const CALL_PITCH = 9;

/** How far a loop bows out of its own column. Under half a lane pitch. */
export const LOOP_BOW = 7;

/** Indent per level of the episode tree, in the label column. */
export const DEPTH_INDENT = 12;

/** How many lane colours the figure cycles through. */
export const TONES = 5;

/**
 * How many characters a call's target may set before its middle is
 * elided. Wide enough for the ordinary path of a source tree, so that the
 * directory a file sits in is usually kept whole.
 */
export const TARGET_ROOM = 24;

/**
 * One episode folded into what the causality figure draws. The fold in
 * fold.ts has already read the log; this reads its rows and summary, so no
 * event is parsed twice and no obligation pair is re-derived.
 */
export function readCausality(summary: Summary, rows: Row[], depth: number): CausalityEpisode {
  const spawns = spawnsByCall(rows);
  const failed = new Set<string>();
  for (const row of rows) {
    if (row.kind === "tool" && row.isError) failed.add(row.callId);
  }
  // Every step the log names is a row, whether or not a message answered
  // it: a request that was retried until the budget ran out is where the
  // episode spent itself, and a figure that dropped it would show an
  // episode that did nothing.
  const answers = new Map<number, StreamedCall[]>();
  for (const row of rows) {
    if (row.kind === "assistant") answers.set(row.step, row.toolCalls);
  }
  const ranges = stepRanges(rows);
  const steps: CausalityStep[] = [...ranges.entries()]
    .sort((a, b) => a[1].from - b[1].from)
    .map(([step, range]) => ({
      step,
      seq: range.from,
      endSeq: range.to,
      answered: answers.has(step),
      calls: (answers.get(step) ?? []).map((call) => {
        const spawn = spawns.get(call.id);
        return {
          id: call.id,
          name: call.name,
          target: callTarget(call.args),
          failed: failed.has(call.id),
          childId: spawn ? spawn.childId : null,
          childName: spawn ? spawn.program : "",
        };
      }),
    }));
  return {
    id: summary.id,
    name: summary.name,
    depth,
    parentId: summary.parentId,
    outcome: summary.outcome,
    lastSeq: summary.lastSeq,
    steps,
    firings: summary.firings.map((f) => ({
      node: f.node,
      fire: f.fire,
      startSeq: f.startSeq,
      endSeq: f.endSeq,
      childId: f.childId,
      error: f.error,
    })),
  };
}

/**
 * The episode each tool call opened, by call id. `spawn/start` names the
 * call that spawned the child, which is the obligation pair the lane is
 * built from; nothing here infers a parent.
 */
function spawnsByCall(rows: Row[]): Map<string, { childId: string; program: string }> {
  const out = new Map<string, { childId: string; program: string }>();
  for (const row of rows) {
    if (row.kind !== "note" || row.type !== "spawn/start") continue;
    const data = obj(row.data);
    const call = str(data.call_id);
    const child = str(data.child_id);
    if (call !== "" && child !== "") out.set(call, { childId: child, program: str(data.program, child) });
  }
  return out;
}

/**
 * The first and last log position each step covers. A row that names a
 * step opens it and every row after it belongs to it until another does,
 * so a step's range holds its retries, its results and the spawn notes it
 * produced. Rows before the first step — the header and the invocation —
 * belong to no step and are in no range.
 */
export function stepRanges(rows: Row[]): Map<number, { from: number; to: number }> {
  const out = new Map<number, { from: number; to: number }>();
  let current: number | null = null;
  for (const row of rows) {
    const named = stepOf(row);
    if (named !== null) current = named;
    if (current === null) continue;
    const range = out.get(current);
    if (range) range.to = Math.max(range.to, row.seq);
    else out.set(current, { from: row.seq, to: row.seq });
  }
  return out;
}

/** The step a row names, or null for a row that names none. */
function stepOf(row: Row): number | null {
  if (row.kind === "assistant" || row.kind === "compaction") return row.step;
  if (row.kind !== "note") return null;
  const step = obj(row.data).step;
  return typeof step === "number" ? step : null;
}

/**
 * The one short field a call's arguments carry, or empty when they carry
 * none. A value with whitespace in it is free text — a shell command, a
 * task, a prompt — and is never shown: it has no meaningful end to keep
 * and would read as a different thing at every width. What is left is
 * path-shaped or identifier-shaped.
 *
 * `room` is how many characters the caller can set. The whole path is
 * shown where it fits, because which directory a file is in is part of
 * what a reader is looking for; a longer one is elided in its middle.
 */
export function callTarget(args: unknown, room = TARGET_ROOM): string {
  const fields = obj(args);
  for (const value of Object.values(fields)) {
    if (typeof value !== "string" || value === "") continue;
    if (/\s/.test(value)) continue;
    return shortenPath(value, room);
  }
  return "";
}

/**
 * A path shortened to `room` characters with its middle elided, so the
 * basename — the part that says which file — always survives. A basename
 * longer than the room is left whole rather than cut, because a cut
 * basename names a file that does not exist. `room` of zero asks for the
 * basename alone.
 */
export function shortenPath(path: string, room: number): string {
  if (path.length <= room) return path;
  const parts = path.split("/");
  const base = parts[parts.length - 1] ?? path;
  if (parts.length > 2) {
    const elided = `${parts[0]}/…/${base}`;
    if (elided.length <= room) return elided;
  }
  return base;
}

/**
 * What a row reads as. Semantic role first, durable identifier second, and
 * never a free-text substring. Kept in one function because how a step
 * with several calls names itself is still being settled.
 */
export function composeLabel(row: {
  kind: RowKind;
  node?: string;
  step?: number;
  answered?: boolean;
  calls?: CausalityCall[];
}): { label: string; aside: string } {
  if (row.kind === "node") return { label: row.node ?? "", aside: "" };
  const aside = row.step === undefined ? "" : `step ${row.step}`;
  const calls = row.calls ?? [];
  const first = calls[0];
  if (row.answered === false) return { label: "no answer", aside };
  if (!first) return { label: "answered", aside };
  const one = first.childId !== null ? `spawn ${first.childName}` : `${first.name} ${first.target}`.trim();
  return { label: calls.length > 1 ? `${one} +${calls.length - 1}` : one, aside };
}

interface LaneBuild {
  lane: CausalityLane;
  /** First and last row this lane and everything under it occupy. */
  first: number;
  last: number;
  children: LaneBuild[];
}

export function layoutCausality(episodes: CausalityEpisode[]): CausalityLayout {
  const byId = new Map(episodes.map((e) => [e.id, e]));
  const rows: CausalityRow[] = [];
  const builds = new Map<string, LaneBuild>();
  const loops: { laneId: string; from: number; to: number }[] = [];
  let tone = 0;

  const openLane = (id: string, kind: LaneKind, episodeId: string, parentId: string | null, label: string, outcome: Outcome | null): LaneBuild => {
    const lane: CausalityLane = {
      id,
      kind,
      episodeId,
      parentId,
      column: 0,
      x: 0,
      y1: 0,
      y2: 0,
      tone: tone % TONES,
      outcome,
      label,
    };
    tone += 1;
    const build: LaneBuild = { lane, first: Infinity, last: -Infinity, children: [] };
    builds.set(id, build);
    const parent = parentId === null ? undefined : builds.get(parentId);
    if (parent) parent.children.push(build);
    return build;
  };

  const emit = (episode: CausalityEpisode, parentLaneId: string | null): void => {
    const laneId = episode.id;
    openLane(laneId, "episode", episode.id, parentLaneId, episode.name, episode.outcome);
    const workflowLaneId = episode.firings.length > 0 ? `${episode.id}/workflow` : null;
    if (workflowLaneId !== null) openLane(workflowLaneId, "workflow", episode.id, laneId, `${episode.name} graph`, null);

    // Steps and firings are one sequence down the row: both are marks on
    // this episode's work, and the log's order is the order they happened.
    const items = [
      ...episode.steps.map((step) => ({ seq: step.seq, step, firing: null as CausalityFiring | null })),
      ...episode.firings.map((firing) => ({ seq: firing.startSeq, step: null as CausalityStep | null, firing })),
    ].sort((a, b) => a.seq - b.seq);

    // A node entered twice is one row and a loop edge, not two rows. That
    // is what lets the scoped conversation show both passes.
    const nodeRow = new Map<string, number>();
    let previous: number | null = null;

    for (const item of items) {
      if (item.step) {
        const step = item.step;
        const composed = composeLabel({ kind: "step", step: step.step, answered: step.answered, calls: step.calls });
        const index = rows.length;
        rows.push({
          id: `${episode.id}/step/${step.step}`,
          kind: "step",
          episodeId: episode.id,
          laneId,
          depth: episode.depth,
          x: 0,
          y: 0,
          tone: 0,
          top: 0,
          height: ROW_PITCH,
          indent: episode.depth * DEPTH_INDENT,
          label: composed.label,
          aside: composed.aside,
          calls: step.calls.map((call) => ({ ...call, x: 0, y: 0 })),
          firings: [],
          fromSeq: step.seq,
          toSeq: step.endSeq,
          opens: [],
        });
        for (const call of step.calls) {
          const child = call.childId === null ? undefined : byId.get(call.childId);
          if (!child) continue;
          rows[index]!.opens.push(child.id);
          emit(child, laneId);
        }
        continue;
      }
      const firing = item.firing!;
      let index = nodeRow.get(firing.node);
      if (index === undefined) {
        index = rows.length;
        nodeRow.set(firing.node, index);
        rows.push({
          id: `${episode.id}/node/${firing.node}`,
          kind: "node",
          episodeId: episode.id,
          laneId: workflowLaneId ?? laneId,
          depth: episode.depth + 1,
          x: 0,
          y: 0,
          tone: 0,
          top: 0,
          height: ROW_PITCH,
          indent: (episode.depth + 1) * DEPTH_INDENT,
          label: composeLabel({ kind: "node", node: firing.node }).label,
          aside: "",
          calls: [],
          firings: [],
          fromSeq: firing.startSeq,
          toSeq: firing.endSeq ?? firing.startSeq,
          opens: [],
        });
      }
      const row = rows[index]!;
      row.firings.push(firing);
      row.fromSeq = Math.min(row.fromSeq, firing.startSeq);
      row.toSeq = Math.max(row.toSeq, firing.endSeq ?? firing.startSeq);
      // A step back up the graph is the loop. Drawn as an edge to the row
      // the node already has rather than as a second row of the same name.
      if (previous !== null && index < previous && workflowLaneId !== null) {
        loops.push({ laneId: workflowLaneId, from: previous, to: index });
      }
      previous = index;
      const child = firing.childId === null ? undefined : byId.get(firing.childId);
      if (child) {
        row.opens.push(child.id);
        emit(child, workflowLaneId ?? laneId);
      }
    }
  };

  for (const episode of episodes) {
    if (episode.parentId !== null && byId.has(episode.parentId)) continue;
    emit(episode, null);
  }

  rows.forEach((row, index) => {
    const build = builds.get(row.laneId);
    if (!build) return;
    build.first = Math.min(build.first, index);
    build.last = Math.max(build.last, index);
  });
  const roots = [...builds.values()].filter((b) => b.lane.parentId === null || !builds.has(b.lane.parentId));
  for (const root of roots) extend(root);

  const ordered = [...builds.values()].filter((b) => Number.isFinite(b.first));
  allocateColumns(ordered);

  const columns = ordered.reduce((most, b) => Math.max(most, b.lane.column), 0) + 1;
  const lanesRight = LANE_LEFT + (columns - 1) * LANE_PITCH;
  const fan = rows.reduce((most, row) => Math.max(most, row.calls.length), 0);
  const marksWidth = lanesRight + (fan === 0 ? 0 : CALL_TICK + (fan - 1) * CALL_PITCH);

  rows.forEach((row, index) => {
    const lane = builds.get(row.laneId)?.lane;
    row.x = lane ? lane.x : LANE_LEFT;
    row.tone = lane ? lane.tone : 0;
    row.y = TOP + index * ROW_PITCH;
    row.top = row.y - ROW_PITCH / 2;
    row.calls.forEach((call, i) => {
      call.x = row.x + CALL_TICK + i * CALL_PITCH;
      call.y = row.y;
    });
  });

  const yOf = (index: number): number => TOP + index * ROW_PITCH;
  for (const build of ordered) {
    const lane = build.lane;
    lane.y1 = yOf(build.first);
    lane.y2 = yOf(build.last);
    for (const child of build.children) {
      if (!Number.isFinite(child.first)) continue;
      lane.y1 = Math.min(lane.y1, yOf(child.first) - ELBOW);
      lane.y2 = Math.max(lane.y2, yOf(child.last) + ELBOW);
    }
    // A lane of one row still needs a line, or its own elbow and its merge
    // meet at a point with nothing between them.
    if (lane.y1 === lane.y2) {
      lane.y1 -= STUB / 2;
      lane.y2 += STUB / 2;
    }
    if (lane.outcome !== null) lane.y2 += OUTCOME_TAIL;
  }

  const edges: CausalityEdge[] = [];
  for (const build of ordered) {
    const parent = build.lane.parentId === null ? undefined : builds.get(build.lane.parentId);
    if (!parent || !Number.isFinite(parent.first)) continue;
    const top = yOf(build.first);
    const bottom = yOf(build.last);
    edges.push({
      kind: "branch",
      from: { x: parent.lane.x, y: top - ELBOW },
      to: { x: build.lane.x, y: top },
      bow: 0,
      tone: build.lane.tone,
      laneId: build.lane.id,
    });
    edges.push({
      kind: "merge",
      from: { x: build.lane.x, y: bottom },
      to: { x: parent.lane.x, y: bottom + ELBOW },
      bow: 0,
      tone: build.lane.tone,
      laneId: build.lane.id,
    });
  }
  for (const loop of loops) {
    const lane = builds.get(loop.laneId)?.lane;
    if (!lane) continue;
    edges.push({
      kind: "loop",
      from: { x: lane.x, y: yOf(loop.from) },
      to: { x: lane.x, y: yOf(loop.to) },
      bow: -LOOP_BOW,
      tone: lane.tone,
      laneId: lane.id,
    });
  }

  return {
    rows,
    lanes: ordered.map((b) => b.lane),
    edges,
    episodes,
    marksWidth,
    height: TOP + Math.max(1, rows.length) * ROW_PITCH + BOTTOM,
  };
}

/** A lane spans its own rows and everything opened under it. */
function extend(build: LaneBuild): { first: number; last: number } {
  for (const child of build.children) {
    const span = extend(child);
    build.first = Math.min(build.first, span.first);
    build.last = Math.max(build.last, span.last);
  }
  return { first: build.first, last: build.last };
}

/**
 * The lowest free column for each lane, released when the lane closes.
 * Column is not tree depth: a child that closed frees its column for the
 * next lane to open, so a run of short-lived children stays two columns
 * wide however deep the tree is.
 *
 * Two lanes that open at one row are ordered by which closes later, so the
 * lane that contains the other stands to its left and their curves cross
 * nothing.
 */
export function allocateColumns(builds: LaneBuild[]): void {
  const order = [...builds].sort((a, b) => a.first - b.first || b.last - a.last || a.lane.id.localeCompare(b.lane.id));
  /** The row at which each column becomes free again. */
  const free: number[] = [];
  for (const build of order) {
    let column = free.findIndex((until) => until < build.first);
    if (column < 0) {
      column = free.length;
      free.push(0);
    }
    free[column] = build.last;
    build.lane.column = column;
    build.lane.x = LANE_LEFT + column * LANE_PITCH;
  }
}

/** The `d` of one edge: cubic, with its control points on the midline. */
export function edgePath(edge: CausalityEdge): string {
  const { from, to, bow } = edge;
  if (bow !== 0) {
    // A loop returns to the column it left, where a control point on the
    // midline would draw a straight line. It bows out of its own column
    // instead, on the side the lane's marks do not use.
    const cx = from.x + bow;
    return `M ${from.x} ${from.y} C ${cx} ${from.y}, ${cx} ${to.y}, ${to.x} ${to.y}`;
  }
  const mid = (from.y + to.y) / 2;
  return `M ${from.x} ${from.y} C ${from.x} ${mid}, ${to.x} ${mid}, ${to.x} ${to.y}`;
}

// The conversation the figure scopes to.

export interface ScopeSegment {
  episodeId: string;
  /** Log positions the segment covers, inclusive. */
  from: number;
  to: number;
  /** `pass 2` when the node was entered more than once, empty otherwise. */
  pass: string;
  /** What the segment is, which the conversation writes in its role column. */
  title: string;
}

export interface ConversationScope {
  rowId: string;
  /** What the header names, with the escape back to the whole run beside it. */
  title: string;
  segments: ScopeSegment[];
}

/**
 * The conversation one row scopes to: its own messages and those of every
 * node below it. A workflow node entered twice by a loop yields one
 * segment per pass, which is the payoff of drawing the loop as an edge
 * rather than as a repeated node.
 */
export function scopeFor(layout: CausalityLayout, rowId: string): ConversationScope | null {
  const row = layout.rows.find((r) => r.id === rowId);
  if (!row) return null;
  const segments: ScopeSegment[] = [];
  if (row.kind === "node") {
    const passes = row.firings.length;
    row.firings.forEach((firing, i) => {
      segments.push({
        episodeId: row.episodeId,
        from: firing.startSeq,
        to: firing.endSeq ?? firing.startSeq,
        pass: passes > 1 ? `pass ${i + 1}` : "",
        title: row.label,
      });
      for (const id of descendants(layout, firing.childId)) segments.push(whole(layout, id, row.label));
    });
  } else {
    segments.push({ episodeId: row.episodeId, from: row.fromSeq, to: row.toSeq, pass: "", title: row.label });
    for (const opened of row.opens) {
      for (const id of descendants(layout, opened)) segments.push(whole(layout, id, row.label));
    }
  }
  return { rowId, title: row.aside === "" ? row.label : `${row.label} · ${row.aside}`, segments };
}

/** One whole episode as a segment, which is how a node below is included. */
function whole(layout: CausalityLayout, id: string, title: string): ScopeSegment {
  const episode = layout.episodes.find((e) => e.id === id);
  return { episodeId: id, from: 0, to: episode ? Math.max(0, episode.lastSeq) : 0, pass: "", title };
}

/** An episode and every episode under it, or nothing when it is absent. */
function descendants(layout: CausalityLayout, id: string | null): string[] {
  if (id === null || !layout.episodes.some((e) => e.id === id)) return [];
  const out = [id];
  for (let i = 0; i < out.length; i += 1) {
    for (const episode of layout.episodes) {
      if (episode.parentId === out[i] && !out.includes(episode.id)) out.push(episode.id);
    }
  }
  return out;
}
