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

import { outcomeLabel } from "./fold.js";
import type { Row, StreamedCall, Summary } from "./fold.js";
import { IDENTITY_COLORS, identitySlot } from "./identity.js";
import { num, obj, str } from "./types.js";
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
   * The one line the tool wrote naming what it acted on and what came of
   * it. The tool is the only thing that knows: the arguments say what was
   * attempted, and a reader wants the outcome. Empty when the log carries
   * none, and a label then names the tool alone rather than guessing.
   */
  subject: string;
  /** True when the result reported a failure, which earns the cross. */
  failed: boolean;
  /** The episode this call opened, absent for a call that opened none. */
  childId: string | null;
  /** The opened episode's configured contract name, which names a delegation. */
  childName: string;
  /** The result as the conversation renders it, empty when none was read. */
  result: string;
  /** Log position of the result, which the deepest reading shows. */
  resultSeq: number;
  /** When the result was written, on the wall clock, which times the call's row. */
  resultTime: number;
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
  /** When that first event was written, on the wall clock. */
  time: number;
  /** Last log position the step covers, which bounds the scoped conversation. */
  endSeq: number;
  /**
   * True when a message answered the step's request. A step whose request
   * was retried until the budget ran out has none, and is still a step: it
   * is where the episode spent what it spent.
   */
  answered: boolean;
  /** What the model said in its own words, empty when it said nothing. */
  text: string;
  /** How many attempts the request took, which is one unless it retried. */
  attempts: number;
  calls: CausalityCall[];
}

/** One firing of one declared workflow node, from its start to its end. */
export interface CausalityFiring {
  node: string;
  fire: number;
  startSeq: number;
  /** When the firing began, on the wall clock. */
  startTime: number;
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
  /** What the episode was told to do: the person's words for a run, and
   * the words of the episode that opened it for a spawned one. */
  task: string;
  /** When the episode started and settled, on the wall clock. */
  startTime: number;
  endTime: number | null;
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

export type RowKind = "episode" | "node" | "step" | "call" | "prose" | "result" | "outcome" | "concurrent" | "task";

/**
 * How deep a reading goes. The rail, the tree, the causal figure and the
 * transcript are one hierarchy read at different depths, so they are
 * settings of one control rather than separate views. Each rung is named
 * for the class of row it adds to the one before it.
 *
 * What the model said and what its tools returned sit on separate rungs,
 * because they differ in size by more than a factor of twenty: over one
 * recorded run of 1,255 events the model's own words came to 5,719
 * characters and the tool results to 139,281. A reader who wants the
 * conversation would otherwise have to take the tool output with it, and
 * the tool output carries all the weight.
 */
export type Depth = "episodes" | "steps" | "calls" | "conversation" | "outputs";

/** The depths in order, coarsest first. */
export const DEPTHS: readonly Depth[] = ["episodes", "steps", "calls", "conversation", "outputs"];

/** The coarsest reading each kind of row appears in. */
const APPEARS_AT: Readonly<Record<RowKind, Depth>> = {
  episode: "episodes",
  // The caption over a group of episodes that ran at once stands wherever
  // those episodes stand, which is every reading, including the rail.
  concurrent: "episodes",
  node: "steps",
  step: "steps",
  call: "calls",
  prose: "conversation",
  task: "conversation",
  outcome: "conversation",
  result: "outputs",
};

/**
 * One row of the model, before anything has decided whether it is visible
 * or how tall it is. `causalityOutline` builds these once from the log;
 * `layoutLanes` places whichever of them a reader can currently see.
 */
export interface CausalityRow {
  /** Stable across redraws: the episode and what the row stands for. */
  id: string;
  kind: RowKind;
  episodeId: string;
  /** The lane the row is a mark on. */
  laneId: string;
  /** The row this one is part of, absent for a root episode. */
  parent: string | null;
  /** The coarsest reading this row appears in. */
  appearsAt: Depth;
  /**
   * Depth in the episode tree. What a view does with it is the view's: one
   * that is read beside a conversation nests its text by it, and one that
   * is read as a conversation keeps every label in one column and lets the
   * gutter carry the nesting instead.
   */
  depth: number;
  /** The row's semantic role, in the fewest words that stay true. */
  label: string;
  /** `step 4`, set alongside the label in faint; empty for a workflow node. */
  aside: string;
  calls: CausalityCall[];
  /** Every firing of this node, in order; empty for a step row. */
  firings: CausalityFiring[];
  /** Log positions a step row covers; a node row scopes by its firings. */
  fromSeq: number;
  toSeq: number;
  /** Episodes this row opened, which the scoped conversation includes. */
  opens: string[];
  /**
   * Prose the row sets rather than names: what the model said, or what a
   * tool returned. Empty for every row whose whole content is its label.
   * A body runs the full width rather than taking the indent, because a
   * diff nested five levels in has lost the room it needs.
   */
  body: string;
  /** True for a result the tool reported as a failure, which earns the cross. */
  failed: boolean;
  /**
   * Where the row sits in the log, which is how a reader finds the event
   * itself. It orders rows within one episode and nothing across
   * episodes: every episode numbers its own log from zero.
   */
  seq: number;
  /**
   * When the row's event happened, on the wall clock. Rows are read in the
   * order of this field, so it runs down the page, and the gutter prints
   * it as the distance from `CausalityOutline.start`. A row whose event
   * the log did not place carries zero and prints nothing.
   */
  time: number;
  /**
   * Whether this row is the one to print the gutter on. A row that
   * continues the row above it — the prose of a step, the body of a
   * result — stands for the same event at the same instant, and printing
   * the time twice turns the column into a ladder of repeats. Set by
   * `visibleRows`, because which row continues which depends on what the
   * reading shows.
   */
  showTime?: boolean;
  /** How many visible rows this one sits inside; set by `visibleRows`. */
  level?: number;
  /** A step row's own number and attempt count, for the label it earns. */
  stepNumber?: number;
  attempts?: number;
  /** What an outcome row returned, which its body renders. */
  outcome?: Outcome;
  answered?: boolean;
  /**
   * True when nothing has answered the step yet and the episode is still
   * running: a step in flight, rather than one that never got an answer.
   */
  waiting?: boolean;
}

/** One row of the model, placed. */
export interface PlacedRow extends CausalityRow {
  /** Centre of the row, on its lane's column. */
  x: number;
  y: number;
  /** The lane colour this row's own marks take, which is its lane's. */
  tone: number;
  /** The band the row highlight fills, which is the height it was given. */
  top: number;
  height: number;
  calls: PlacedCall[];
  /**
   * The row draws the pulsing brand mark in place of its vertex: it is the
   * last row of an episode that has not settled, so it is where the run is
   * now.
   */
  pulse: boolean;
}

export type LaneKind = "episode" | "workflow";

/** What earns a lane and what it is, before it has a column or a length. */
export interface LaneSpec {
  id: string;
  kind: LaneKind;
  episodeId: string;
  /** The lane this one branched from, absent for a root episode's lane. */
  parentId: string | null;
  /** Which cycled lane colour represents this branch. */
  tone: number;
  /** The typed outcome drawn at the foot; only an episode lane has one. */
  outcome: Outcome | null;
  /**
   * When the episode this lane draws ran, on the wall clock. Two lanes
   * whose intervals overlap were open at the same time and cannot share a
   * column. `end` is null while the episode has not settled.
   */
  start: number;
  end: number | null;
  /** What the lane is, for the hovercard. */
  label: string;
}

export interface CausalityLane extends LaneSpec {
  /** Column the lane holds while it is open, allocated lowest-free. */
  column: number;
  x: number;
  /**
   * The one continuous line, from the lane's first row to its last,
   * stretched to reach every curve that joins it.
   */
  y1: number;
  y2: number;
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

/**
 * The rows and lanes a run has, in the order their events happened, with
 * nothing yet decided about which of them a reader can see or how tall
 * they are. Every view of a run reads this same model; a view then chooses
 * a visible subset and hands it to `layoutLanes`, which is the only place
 * the geometry lives.
 */
export interface CausalityOutline {
  rows: CausalityRow[];
  lanes: LaneSpec[];
  /** A node re-entered from further down, by the two rows it joins. */
  loops: { laneId: string; from: string; to: string }[];
  episodes: CausalityEpisode[];
  /**
   * When the run began, on the wall clock: the earliest start any of its
   * episodes recorded. Every row's time is read against it, so one origin
   * serves the run and a child's times are comparable with its caller's.
   */
  start: number;
}

export interface CausalityLayout {
  rows: PlacedRow[];
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
export const TONES = IDENTITY_COLORS;

/**
 * One episode folded into what the causality figure draws. The fold in
 * fold.ts has already read the log; this reads its rows and summary, so no
 * event is parsed twice and no obligation pair is re-derived.
 */
export function readCausality(summary: Summary, allRows: Row[], depth: number): CausalityEpisode {
  // Rows at or below `seed/end` were copied from the fork origin's log.
  // They record what that episode did, so reading them here would draw
  // this episode opening the children its origin opened and taking their
  // results. The conversation still shows them, under the seed-end note.
  const seeded = summary.seedEnd;
  const rows = seeded === null ? allRows : allRows.filter((row) => row.seq > seeded);
  const spawns = spawnsByCall(rows);
  const results = new Map<string, { text: string; body: string; seq: number; time: number; failed: boolean }>();
  for (const row of rows) {
    if (row.kind !== "tool") continue;
    results.set(row.callId, { text: row.subject, body: row.rendered, seq: row.seq, time: row.time, failed: row.isError });
  }
  // When each log position was written. A row is placed by its position
  // and read by its time, and the two come from the same event.
  const timeOf = new Map(rows.map((row) => [row.seq, row.time]));
  const retries = new Map<number, number>();
  for (const row of rows) {
    if (row.kind !== "note" || row.type !== "request/retry") continue;
    const step = num(obj(row.data).step);
    retries.set(step, (retries.get(step) ?? 0) + 1);
  }
  // Every step the log names is a row, whether or not a message answered
  // it: a request that was retried until the budget ran out is where the
  // episode spent itself, and a figure that dropped it would show an
  // episode that did nothing.
  const answers = new Map<number, { calls: StreamedCall[]; text: string }>();
  for (const row of rows) {
    if (row.kind === "assistant") answers.set(row.step, { calls: row.toolCalls, text: row.text });
  }
  const ranges = stepRanges(rows);
  const steps: CausalityStep[] = [...ranges.entries()]
    .sort((a, b) => a[1].from - b[1].from)
    .map(([step, range]) => ({
      step,
      seq: range.from,
      time: timeOf.get(range.from) ?? summary.startTime,
      endSeq: range.to,
      answered: answers.has(step),
      text: answers.get(step)?.text ?? "",
      attempts: (retries.get(step) ?? 0) + 1,
      calls: (answers.get(step)?.calls ?? []).map((call) => {
        const spawn = spawns.get(call.id);
        const result = results.get(call.id);
        return {
          id: call.id,
          name: call.name,
          subject: result?.text ?? "",
          failed: result?.failed === true,
          childId: spawn ? spawn.childId : null,
          childName: spawn ? spawn.contract : "",
          result: result?.body ?? "",
          resultSeq: result?.seq ?? range.to,
          // A call whose result never arrived is timed by the step that
          // issued it, which is when the episode was last known to be
          // working on it.
          resultTime: result?.time ?? timeOf.get(range.from) ?? summary.startTime,
        };
      }),
    }));
  return {
    id: summary.id,
    name: summary.name,
    depth,
    parentId: summary.parentId,
    outcome: summary.outcome,
    task: summary.task,
    startTime: summary.startTime,
    endTime: summary.endTime,
    lastSeq: summary.lastSeq,
    steps,
    firings: summary.firings.map((f) => ({
      node: f.node,
      fire: f.fire,
      startSeq: f.startSeq,
      startTime: f.startTime,
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
function spawnsByCall(rows: Row[]): Map<string, { childId: string; contract: string }> {
  const out = new Map<string, { childId: string; contract: string }>();
  for (const row of rows) {
    if (row.kind !== "note" || row.type !== "spawn/start") continue;
    const data = obj(row.data);
    const call = str(data.call_id);
    const child = str(data.child_id);
    if (call !== "" && child !== "") out.set(call, { childId: child, contract: str(data.contract, child) });
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
 *
 * A label stands in for children that are not on the page, so a step's
 * label defers to its calls when those calls are rows of their own: with
 * them hidden it reads `read src/parser.rs +1`, and with them shown it
 * falls back to `step 1` rather than echoing the line directly beneath it.
 */
export function composeLabel(row: {
  kind: RowKind;
  node?: string;
  step?: number;
  attempts?: number;
  answered?: boolean;
  waiting?: boolean;
  calls?: CausalityCall[];
  /** True when the step's calls are rows of their own on the same page. */
  callsVisible?: boolean;
}): { label: string; aside: string } {
  if (row.kind === "node") return { label: row.node ?? "", aside: "" };
  const calls = row.calls ?? [];
  const first = calls[0];
  const one = (call: CausalityCall): string => {
    if (call.childId !== null) return `spawn ${call.childName}`;
    // A failed call's subject is the error the tool wrote, which already
    // opens with the tool's own name; prefixing it again would stutter.
    if (call.subject.startsWith(`${call.name}:`)) return call.subject;
    return `${call.name} ${call.subject}`.trim();
  };
  if (row.kind === "call") return { label: first ? one(first) : "", aside: "" };
  const step = row.step === undefined ? "" : `step ${row.step}`;
  const attempts = row.attempts !== undefined && row.attempts > 1 ? ` – attempt ${row.attempts} of ${row.attempts}` : "";
  if (row.callsVisible) return { label: `${step}${attempts}`, aside: "" };
  const aside = step;
  // A step of a running episode that nothing has answered yet is in flight.
  // Only an episode that has ended can hold a step that never got an answer.
  if (row.waiting === true) return { label: "waiting", aside };
  if (row.answered === false) return { label: "no answer", aside };
  if (!first) return { label: "answered", aside };
  return { label: calls.length > 1 ? `${one(first)} +${calls.length - 1}` : one(first), aside };
}

interface LaneBuild {
  lane: CausalityLane;
  /** First and last row this lane and everything under it occupy. */
  first: number;
  last: number;
  /** The last row the lane holds itself, before children widened `last`. */
  ownLast: number;
  children: LaneBuild[];
}

/**
 * When a run began: the earliest start its episodes recorded. An episode
 * whose `episode/start` was never read carries a start of zero and is
 * passed over, because a run that began at the epoch would put every other
 * row decades after it.
 */
export function runStart(episodes: CausalityEpisode[]): number {
  const starts = episodes.map((episode) => episode.startTime).filter((start) => start > 0);
  return starts.length === 0 ? 0 : Math.min(...starts);
}

/**
 * How long after the run began a row's event happened, written the way the
 * gutter prints it. Minutes and seconds, to a tenth: a tenth is fine
 * enough that the ends of two episodes opened by one turn read apart, and
 * coarse enough to stay in the narrow column.
 *
 * An hour or more takes hours, minutes and whole seconds instead: at that
 * length a tenth of a second says nothing and the column has no room for
 * it. Empty for a time the log does not place, which a row of an episode
 * whose start was never read has.
 */
export function elapsedLabel(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const whole = Math.floor(ms / 1000);
  const seconds = whole % 60;
  const pad = (n: number): string => String(n).padStart(2, "0");
  if (ms < 3_600_000) {
    const tenth = Math.floor((ms % 1000) / 100);
    return `${Math.floor(whole / 60)}:${pad(seconds)}.${tenth}`;
  }
  return `${Math.floor(whole / 3600)}:${pad(Math.floor(whole / 60) % 60)}:${pad(seconds)}`;
}

/** A set of episodes that were open at the same time, and the span they cover. */
export interface ConcurrentGroup {
  /** The episodes of the group, in the order the caller gave them. */
  ids: string[];
  /** The earliest start of the group and the latest end, on the wall clock. */
  start: number;
  /** Null while one episode of the group has not settled. */
  end: number | null;
}

/**
 * Which of the episodes opened by one turn were open at the same time.
 * Two episodes belong to one group when their runs overlap, and overlap is
 * carried along a chain: an episode that overlaps only the second of three
 * still joins the one group, because the three were never one at a time.
 * A group of one episode is dropped, since nothing about it needs saying.
 *
 * An episode that has not settled is open until the run ends, so it
 * overlaps everything opened after it.
 */
export function concurrentGroups(episodes: { id: string; startTime: number; endTime: number | null }[]): ConcurrentGroup[] {
  const order = new Map(episodes.map((episode, index) => [episode.id, index]));
  const byStart = [...episodes].sort((a, b) => a.startTime - b.startTime);
  const groups: ConcurrentGroup[] = [];
  let open: typeof byStart = [];
  const close = (): void => {
    if (open.length > 1) {
      const ends = open.map((episode) => episode.endTime);
      groups.push({
        ids: open.map((episode) => episode.id).sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0)),
        start: Math.min(...open.map((episode) => episode.startTime)),
        end: ends.includes(null) ? null : Math.max(...(ends as number[])),
      });
    }
    open = [];
  };
  // The furthest any episode of the open group has reached. An episode
  // that starts after it belongs to a new group, because by then every
  // episode of the last one had finished.
  let reached = -Infinity;
  for (const episode of byStart) {
    if (episode.startTime >= reached) close();
    open.push(episode);
    reached = Math.max(reached, episode.endTime ?? Infinity);
  }
  close();
  return groups;
}

/**
 * The span a caption states, written the way the gutter writes a time:
 * from when the first episode of the group began to when the last one
 * ended. A group holding an episode that has not settled has no end yet.
 */
export function spanLabel(group: ConcurrentGroup, start: number): string {
  const from = elapsedLabel(group.start - start);
  return group.end === null ? `from ${from}` : `${from} – ${elapsedLabel(group.end - start)}`;
}

/**
 * Every row and lane a run has, in the order their events happened. This
 * reads the log's obligation pairs and decides nothing about geometry, so
 * a view that shows every row and a view that shows a collapsed part of a
 * run are two readers of one model rather than two copies of it.
 *
 * The rows are built structurally, each under the row it is part of, and
 * then ordered by time. Two episodes that ran at once therefore interleave,
 * which is what shows that they ran at once. What a row is part of is
 * carried by `parent`, by the lane it is a mark on, and by `depth`, rather
 * than by the rows next to it.
 */
export function causalityOutline(episodes: CausalityEpisode[]): CausalityOutline {
  const byId = new Map(episodes.map((e) => [e.id, e]));
  const start = runStart(episodes);
  const rows: CausalityRow[] = [];
  const lanes: LaneSpec[] = [];
  const loops: { laneId: string; from: string; to: string }[] = [];

  // A lane carries one episode, so it takes that episode's identity colour
  // rather than a colour cycled from its position, and the stroke agrees
  // with the name written beside it. A graph lane is not an agent; the
  // stylesheet draws it in neutral ink whatever slot it is given here.
  const openLane = (id: string, kind: LaneKind, of: CausalityEpisode, parentId: string | null, label: string, outcome: Outcome | null): void => {
    const tone = kind === "workflow" ? 0 : identitySlot(label);
    lanes.push({ id, kind, episodeId: of.id, parentId, tone, outcome, label, start: of.startTime, end: of.endTime });
  };

  const push = (row: Omit<CausalityRow, "appearsAt" | "body" | "failed" | "calls" | "firings" | "opens"> & Partial<CausalityRow>): CausalityRow => {
    const full: CausalityRow = {
      appearsAt: APPEARS_AT[row.kind],
      body: "",
      failed: false,
      calls: [],
      firings: [],
      opens: [],
      ...row,
    };
    rows.push(full);
    return full;
  };

  const emit = (episode: CausalityEpisode, parentLaneId: string | null, parentRow: string | null): void => {
    const laneId = episode.id;
    openLane(laneId, "episode", episode, parentLaneId, episode.name, episode.outcome);
    const workflowLaneId = episode.firings.length > 0 ? `${episode.id}/workflow` : null;
    if (workflowLaneId !== null) openLane(workflowLaneId, "workflow", episode, laneId, `${episode.name} graph`, null);

    // The episode itself is a row, not only a lane: read at its coarsest,
    // the outline is the episode rail, and a rail needs a row per episode.
    const head = push({
      id: episode.id,
      kind: "episode",
      episodeId: episode.id,
      laneId,
      parent: parentRow,
      depth: episode.depth,
      label: episode.name,
      aside: episode.id,
      fromSeq: 0,
      toSeq: Math.max(0, episode.lastSeq),
      seq: 0,
      time: episode.startTime,
    });

    // The task hangs under the episode it was given to. It is the one thing
    // that tells two children of one contract apart, and for a run it is
    // what the person asked for.
    if (episode.task !== "") {
      push({
        id: `${episode.id}/task`,
        kind: "task",
        episodeId: episode.id,
        laneId,
        parent: head.id,
        depth: episode.depth + 1,
        // The row above names the episode; this one names what it was told
        // to do. Without the label the prose reads as something the episode
        // said rather than something it was given.
        label: "task",
        aside: "",
        body: episode.task,
        fromSeq: 0,
        toSeq: 0,
        seq: 0,
        time: episode.startTime,
      });
    }

    // Steps and firings are one sequence down the row: both are marks on
    // this episode's work, and the log's order is the order they happened.
    const items = [
      ...episode.steps.map((step) => ({ seq: step.seq, step, firing: null as CausalityFiring | null })),
      ...episode.firings.map((firing) => ({ seq: firing.startSeq, step: null as CausalityStep | null, firing })),
    ].sort((a, b) => a.seq - b.seq);

    // A node entered twice is one row and a loop edge, not two rows. That
    // is what lets the deepest reading show both passes.
    const nodeRow = new Map<string, CausalityRow>();
    let previous: CausalityRow | null = null;

    for (const item of items) {
      if (item.step) {
        const step = item.step;
        const row = push({
          id: `${episode.id}/step/${step.step}`,
          kind: "step",
          episodeId: episode.id,
          laneId,
          parent: head.id,
          depth: episode.depth + 1,
          label: "",
          aside: "",
          calls: step.calls,
          fromSeq: step.seq,
          toSeq: step.endSeq,
          seq: step.seq,
          time: step.time,
        });
        // What a step is called depends on whether its calls are shown, so
        // the label is set when the visible set is known, not here.
        row.attempts = step.attempts;
        row.answered = step.answered;
        row.waiting = !step.answered && episode.outcome === null;
        row.stepNumber = step.step;
        if (step.text !== "") {
          push({
            id: `${row.id}/prose`,
            kind: "prose",
            episodeId: episode.id,
            laneId,
            parent: row.id,
            depth: episode.depth + 1,
            label: "",
            aside: "",
            body: step.text,
            fromSeq: step.seq,
            toSeq: step.endSeq,
            seq: step.seq,
            time: step.time,
          });
        }
        // The children a turn opened, in the order its calls opened them,
        // which breaks ties between rows that share an instant.
        const opened: { child: CausalityEpisode; callRow: CausalityRow }[] = [];
        for (const call of step.calls) {
          const callRow = push({
            id: `${row.id}/call/${call.id}`,
            kind: "call",
            episodeId: episode.id,
            laneId,
            parent: row.id,
            depth: episode.depth + 1,
            label: composeLabel({ kind: "call", calls: [call] }).label,
            aside: "",
            calls: [call],
            failed: call.failed,
            fromSeq: step.seq,
            toSeq: call.resultSeq,
            seq: call.resultSeq,
            time: call.resultTime,
          });
          if (call.result !== "") {
            push({
              id: `${callRow.id}/result`,
              kind: "result",
              episodeId: episode.id,
              laneId,
              parent: callRow.id,
              depth: episode.depth + 1,
              label: "",
              aside: "",
              body: call.result,
              failed: call.failed,
              fromSeq: call.resultSeq,
              toSeq: call.resultSeq,
              seq: call.resultSeq,
              time: call.resultTime,
            });
          }
          const child = call.childId === null ? undefined : byId.get(call.childId);
          if (!child) continue;
          row.opens.push(child.id);
          opened.push({ child, callRow });
        }
        // A child is built under the call that opened it, which is where
        // `parent` and the lane's branch come from. The final order is by
        // time, so a child's rows run beside its caller's rather than after
        // them.
        //
        // Children of one turn that were open at the same time interleave
        // there, which is what shows that they overlapped. A caption over
        // the first child of each such group states the group's span in
        // words. Children a declared graph's firings opened carry no
        // caption: each firing opens one child at its own point in the graph,
        // so there is no one row where a group of them is opened.
        const captions = new Map<string, ConcurrentGroup>();
        for (const group of concurrentGroups(opened.map(({ child }) => child))) captions.set(group.ids[0]!, group);
        for (const { child, callRow } of opened) {
          const group = captions.get(child.id);
          if (group !== undefined) {
            push({
              id: `${row.id}/concurrent/${child.id}`,
              kind: "concurrent",
              episodeId: episode.id,
              laneId,
              parent: row.id,
              depth: episode.depth + 1,
              label: `${group.ids.length} episodes ran at the same time`,
              aside: spanLabel(group, start),
              opens: [...group.ids],
              fromSeq: step.seq,
              toSeq: step.endSeq,
              seq: step.seq,
              // A caption covers a span rather than standing at an instant,
              // and its own text carries that span, so it prints no time of
              // its own.
              time: group.start,
              showTime: false,
            });
          }
          emit(child, laneId, callRow.id);
        }
        continue;
      }
      const firing = item.firing!;
      let row = nodeRow.get(firing.node);
      if (row === undefined) {
        row = push({
          id: `${episode.id}/node/${firing.node}`,
          kind: "node",
          episodeId: episode.id,
          laneId: workflowLaneId ?? laneId,
          parent: head.id,
          depth: episode.depth + 1,
          label: composeLabel({ kind: "node", node: firing.node }).label,
          aside: "",
          fromSeq: firing.startSeq,
          toSeq: firing.endSeq ?? firing.startSeq,
          seq: firing.startSeq,
          time: firing.startTime,
        });
        nodeRow.set(firing.node, row);
      }
      row.firings.push(firing);
      row.fromSeq = Math.min(row.fromSeq, firing.startSeq);
      row.toSeq = Math.max(row.toSeq, firing.endSeq ?? firing.startSeq);
      // A step back up the graph is the loop. Drawn as an edge to the row
      // the node already has rather than as a second row of the same name.
      if (previous !== null && previous !== row && rows.indexOf(row) < rows.indexOf(previous) && workflowLaneId !== null) {
        loops.push({ laneId: workflowLaneId, from: previous.id, to: row.id });
      }
      previous = row;
      const child = firing.childId === null ? undefined : byId.get(firing.childId);
      if (child) {
        row.opens.push(child.id);
        emit(child, workflowLaneId ?? laneId, row.id);
      }
    }

    // What the episode returned, which is its answer. An episode can reach it
    // without ever writing a word of prose: every step spends itself on tool
    // calls and the last one returns a typed value. A reading that stopped at
    // the model's own words would then show a run that said nothing.
    if (episode.outcome !== null) {
      push({
        id: `${episode.id}/outcome`,
        kind: "outcome",
        episodeId: episode.id,
        laneId,
        parent: head.id,
        depth: episode.depth + 1,
        label: outcomeLabel(episode.outcome),
        aside: "",
        outcome: episode.outcome,
        fromSeq: episode.lastSeq,
        toSeq: episode.lastSeq,
        seq: episode.lastSeq,
        // What an episode returned is the last thing it did, so the row
        // takes the moment it settled; an episode still running has none.
        time: episode.endTime ?? episode.startTime,
      });
    }
  };

  for (const episode of episodes) {
    if (episode.parentId !== null && byId.has(episode.parentId)) continue;
    emit(episode, null, null);
  }
  return { rows: chronological(rows, start), lanes, loops, episodes, start };
}

/**
 * The rows in the order their events happened. Two rows at one instant keep
 * the order the structure gave them, so a step still stands above its own
 * prose and an episode above the task it was given.
 *
 * A row the log did not place stands where the row it is part of stands,
 * and a row with neither stands at the start of the run. An episode whose
 * `episode/start` was never read is the case: its rows would otherwise sort
 * to the epoch, decades above the run that opened them, and the position
 * would be an accident of the missing event rather than a statement about
 * when the work happened. Such a row prints no time either, since there is
 * none to print.
 *
 * The rows arrive with every row after the row it is part of, so one
 * forward pass settles every inherited time before it is needed.
 */
function chronological(rows: CausalityRow[], start: number): CausalityRow[] {
  const when = new Map<string, number>();
  for (const row of rows) {
    const placed = Number.isFinite(row.time) && row.time > 0;
    const inherited = row.parent === null ? undefined : when.get(row.parent);
    when.set(row.id, placed ? row.time : inherited ?? start);
  }
  // Sorting is stable, so rows sharing an instant keep the structural order.
  return [...rows].sort((a, b) => (when.get(a.id) ?? start) - (when.get(b.id) ?? start));
}

/**
 * The rows a reading shows, in the order they happened, each with how many
 * visible rows it sits inside. A row appears when the reading is at least
 * as deep as its kind, or when the row it is part of is itself visible and
 * opened, which is what a caret does: it opens one branch one level past
 * the reading without expanding the run.
 *
 * A caret therefore hides a subtree by membership rather than a stretch of
 * the page. Rows are read in the order their events happened, so the rows
 * of one episode are spread through the rows of every episode that ran
 * beside it, and a shut caret takes rows out of the middle of the page.
 *
 * Depth is counted against the visible set and never against the raw
 * hierarchy. Read at its coarsest a child episode is still an episode one
 * level in, even though the call that spawned it is hidden; counting
 * against the hierarchy instead leaves gaps in the nesting.
 *
 * A step whose calls are shown gives up its ticks and its summary, because
 * both exist to stand in for children that are not on the page.
 */
export function visibleRows(outline: CausalityOutline, depth: Depth, opened: ReadonlySet<string> = new Set()): CausalityRow[] {
  const wanted = DEPTHS.indexOf(depth);
  const byId = new Map(outline.rows.map((row) => [row.id, row]));
  const above = (row: CausalityRow): CausalityRow | undefined => (row.parent === null ? undefined : byId.get(row.parent));

  /**
   * Whether the reading shows this row, answered from the row it is part
   * of rather than from anything already decided further up the page: a
   * row can stand above the row that opened it once the order is time.
   */
  const shown = new Map<string, boolean>();
  const isShown = (row: CausalityRow): boolean => {
    const settled = shown.get(row.id);
    if (settled !== undefined) return settled;
    // A caret opens the row it sits on, so what it reveals is that row's
    // own children and not a descendant further down.
    const parent = above(row);
    const answer = DEPTHS.indexOf(row.appearsAt) <= wanted
      || (parent !== undefined && opened.has(parent.id) && isShown(parent));
    shown.set(row.id, answer);
    return answer;
  };

  // Nesting is counted from the nearest ancestor the reading shows, not
  // from the immediate one: read at its coarsest a child episode is one
  // level inside its caller even though the call that opened it is not
  // on the page, and counting the hidden rows would leave gaps.
  const level = new Map<string, number>();
  const levelOf = (row: CausalityRow): number => {
    const settled = level.get(row.id);
    if (settled !== undefined) return settled;
    let at = above(row);
    while (at !== undefined && !isShown(at)) at = above(at);
    const nested = at === undefined ? 0 : levelOf(at) + 1;
    level.set(row.id, nested);
    return nested;
  };

  const out = outline.rows.filter((row) => isShown(row));

  return out.map((row, index) => {
    const nested = levelOf(row);
    // A row continues the one above it when that row is the one it is part
    // of and the two stand for the same event: one event, one line in the
    // gutter. Two rows of different episodes never continue one another,
    // even at the same log position, because each episode numbers its own
    // log and the two positions name different events.
    const before = out[index - 1];
    const continues = before !== undefined && before.id === row.parent && before.seq === row.seq;
    // A row the fold has already settled keeps what it was given: a caption
    // over a group of episodes states its own span and stands at no instant.
    // A row the log did not place has no time to print either.
    const showTime = row.showTime !== false && !continues && row.time > 0;
    if (row.kind !== "step") return { ...row, level: nested, showTime };
    const callsShown = row.calls.some((call) => shown.get(`${row.id}/call/${call.id}`) === true);
    const composed = composeLabel({
      kind: "step",
      step: row.stepNumber,
      attempts: row.attempts,
      answered: row.answered,
      waiting: row.waiting,
      calls: row.calls,
      callsVisible: callsShown,
    });
    return { ...row, level: nested, showTime, label: composed.label, aside: composed.aside, calls: callsShown ? [] : row.calls };
  });
}

/**
 * Where the visible rows and the lanes they are marks on go.
 *
 * `visible` is the rows a reader can currently see, in the order they
 * happened, and `heights` is what each of them measured. Heights are given
 * rather than assumed because rows are not one size: a line of prose or a
 * tool result's body is taller than a node, and a lane whose ends were
 * computed from a fixed pitch would then miss the rows it must reach. A
 * view that shows every row at one height passes that height for each of
 * them.
 *
 * Lanes are computed over the visible rows alone, so a view that collapses
 * part of a run gets the lanes that part earns, and every view recomputes
 * on the change that moved a row.
 */
export function layoutLanes(outline: CausalityOutline, visible: CausalityRow[], heights: number[]): CausalityLayout {
  // Nothing is in flight once the run's own episode has settled. An
  // episode under it that recorded no outcome did not finish; it stopped,
  // and drawing it as still running would report a run that has ended as
  // one still going.
  const running = !outline.episodes.some((episode) => episode.depth === 0 && episode.outcome !== null);
  const at = new Map(visible.map((row, index) => [row.id, index]));
  const builds = new Map<string, LaneBuild>();
  for (const spec of outline.lanes) {
    builds.set(spec.id, {
      lane: { ...spec, column: 0, x: 0, y1: 0, y2: 0 },
      first: Infinity,
      last: -Infinity,
      ownLast: -Infinity,
      children: [],
    });
  }
  for (const build of builds.values()) {
    const parent = build.lane.parentId === null ? undefined : builds.get(build.lane.parentId);
    if (parent) parent.children.push(build);
  }

  visible.forEach((row, index) => {
    const build = builds.get(row.laneId);
    if (!build) return;
    build.first = Math.min(build.first, index);
    build.last = Math.max(build.last, index);
    build.ownLast = index;
  });
  for (const build of builds.values()) {
    if (build.lane.parentId === null || !builds.has(build.lane.parentId)) extend(build);
  }

  // A lane with no visible row of its own and nothing visible under it is
  // not drawn: there is nothing for its line to run between.
  const ordered = [...builds.values()].filter((b) => Number.isFinite(b.first));
  allocateColumns(ordered);

  const columns = ordered.reduce((most, b) => Math.max(most, b.lane.column), 0) + 1;
  const lanesRight = LANE_LEFT + (columns - 1) * LANE_PITCH;
  const fan = visible.reduce((most, row) => Math.max(most, row.calls.length), 0);
  const marksWidth = lanesRight + (fan === 0 ? 0 : CALL_TICK + (fan - 1) * CALL_PITCH);

  // The top of each visible row, from the heights they measured.
  const tops: number[] = [];
  let cursor = TOP;
  visible.forEach((_, index) => {
    tops.push(cursor);
    cursor += heights[index] ?? ROW_PITCH;
  });
  // A row's mark sits on its first line rather than at its vertical
  // middle: a row holding a diff is many lines tall, and a vertex halfway
  // down it would sit beside the diff rather than beside the name it
  // belongs to.
  const yOf = (index: number): number =>
    (tops[index] ?? TOP) + Math.min(heights[index] ?? ROW_PITCH, ROW_PITCH) / 2;

  const rows: PlacedRow[] = visible.map((row, index) => {
    const build = builds.get(row.laneId);
    const lane = build?.lane;
    const x = lane ? lane.x : LANE_LEFT;
    const y = yOf(index);
    return {
      ...row,
      x,
      y,
      tone: lane ? lane.tone : 0,
      top: tops[index] ?? TOP,
      height: heights[index] ?? ROW_PITCH,
      calls: row.calls.map((call, i) => ({ ...call, x: x + CALL_TICK + i * CALL_PITCH, y })),
      pulse: running
        && build !== undefined
        && build.ownLast === index
        && build.lane.kind === "episode"
        && build.lane.outcome === null,
    };
  });

  for (const build of ordered) {
    const lane = build.lane;
    lane.y1 = yOf(build.first);
    lane.y2 = yOf(build.last);
    // A lane of one row still needs a line, or its own elbow and its merge
    // meet at a point with nothing between them.
    if (lane.y1 === lane.y2) {
      lane.y1 -= STUB / 2;
      lane.y2 += STUB / 2;
    }
    // The foot carries the outcome mark, which is the last thing on the
    // lane and so sits below the last row rather than on it. A lane still
    // running has no foot: its last row draws the pulsing brand mark in
    // place of its own vertex, which is where the run is.
    if (lane.outcome !== null) lane.y2 += OUTCOME_TAIL;
  }

  // A lane reaches past its children, because a child branches from it above
  // the child's own first row and merges back into it below the child's own
  // foot. The children are settled before the parent reads them: `ordered`
  // holds each lane before the lanes it opened, so reading it backwards puts
  // every child's foot in its final place first.
  for (const build of [...ordered].reverse()) {
    for (const child of build.children) {
      if (!Number.isFinite(child.first)) continue;
      build.lane.y1 = Math.min(build.lane.y1, child.lane.y1 - ELBOW);
      // Only a child that folds back needs ground under it to fold into.
      if (child.lane.end === null) continue;
      build.lane.y2 = Math.max(build.lane.y2, child.lane.y2 + ELBOW);
    }
  }

  const edges: CausalityEdge[] = [];
  for (const build of ordered) {
    const parent = build.lane.parentId === null ? undefined : builds.get(build.lane.parentId);
    if (!parent || !Number.isFinite(parent.first)) continue;
    // The curves leave and rejoin at the lane's own ends, not at its first
    // and last rows: a lane runs past its last row by the height of its
    // outcome mark, and a merge drawn from the row would leave the foot
    // hanging below the point it had already folded away at.
    const { y1: top, y2: bottom } = build.lane;
    edges.push({
      kind: "branch",
      from: { x: parent.lane.x, y: top - ELBOW },
      to: { x: build.lane.x, y: top },
      bow: 0,
      tone: build.lane.tone,
      laneId: build.lane.id,
    });
    // A merge is the parent taking what the child returned. A lane whose
    // episode has not settled has returned nothing, so it has no merge: its
    // line ends at the row the episode reached, under the pulsing mark.
    if (build.lane.end === null) continue;
    edges.push({
      kind: "merge",
      from: { x: build.lane.x, y: bottom },
      to: { x: parent.lane.x, y: bottom + ELBOW },
      bow: 0,
      tone: build.lane.tone,
      laneId: build.lane.id,
    });
  }
  // A loop whose other end a reader cannot see is not drawn: a curve to a
  // row that is not there would end in empty space.
  for (const loop of outline.loops) {
    const lane = builds.get(loop.laneId)?.lane;
    const from = at.get(loop.from);
    const to = at.get(loop.to);
    if (!lane || from === undefined || to === undefined || !Number.isFinite(builds.get(loop.laneId)!.first)) continue;
    edges.push({
      kind: "loop",
      from: { x: lane.x, y: yOf(from) },
      to: { x: lane.x, y: yOf(to) },
      bow: -LOOP_BOW,
      tone: lane.tone,
      laneId: lane.id,
    });
  }

  return {
    rows,
    lanes: ordered.map((b) => b.lane),
    edges,
    episodes: outline.episodes,
    marksWidth,
    height: cursor + BOTTOM,
  };
}

/**
 * Every row of a run at one height, which is the two-pane figure's whole
 * reading: it shows the shape of the run and leaves the messages to the
 * conversation beside it.
 */
export function layoutCausality(episodes: CausalityEpisode[]): CausalityLayout {
  const outline = causalityOutline(episodes);
  const visible = visibleRows(outline, "steps");
  return layoutLanes(outline, visible, visible.map(() => ROW_PITCH));
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
  /** For each column, the row and the moment at which it becomes free. */
  const free: { row: number; time: number | null }[] = [];
  // A column may be reused only by a lane that opened after the lane
  // holding it had closed. Rows alone do not decide that: read as the
  // episode rail, three episodes that ran at once are three consecutive
  // rows, and a column freed by row would draw them as one lane reused
  // three times — the drawing that says each waited for the one before.
  for (const build of order) {
    const closed = (until: { row: number; time: number | null }): boolean =>
      until.row < build.first && until.time !== null && until.time <= build.lane.start;
    let column = free.findIndex(closed);
    if (column < 0) {
      column = free.length;
      free.push({ row: 0, time: 0 });
    }
    free[column] = { row: build.last, time: build.lane.end };
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
  return { rowId, title: row.aside === "" ? row.label : `${row.label} – ${row.aside}`, segments };
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
