// The states the conversation draws rather than writes out as a word.
//
// The trajectory already draws a grammar: a hairline that runs is work in
// progress, an open ring is something not yet closed, a dashed stroke is
// something supplied or still to come, and a cross is an attempt that
// failed. A reader who learns that grammar in the timeline meets the same
// shapes in the conversation, so each of these four marks reuses one.
//
// The geometry is data rather than markup so that it can be checked. Every
// part stays inside one box, and it is the same box for all four marks, so
// two marks set side by side sit on one baseline. No part names a colour:
// a mark strokes `currentColor` and the stylesheet sets the role token.

/**
 * A state a row or a figure's node carries, drawn where a word would
 * otherwise stand. The first four are the conversation's; the last two are
 * the causality figure's leaves, which reuse this geometry so that a
 * reader meets one set of shapes in both places.
 */
export type MarkKind = "interrupted" | "live" | "error" | "synthetic" | "call" | "settled";

/** One stroked shape. Its attributes carry geometry and nothing else. */
export interface MarkPart {
  readonly shape: "path" | "circle";
  readonly attrs: Readonly<Record<string, string | number>>;
}

export interface Mark {
  /** The word the mark replaces: its accessible name and hovercard heading. */
  readonly label: string;
  /** What the state means, in one sentence, shown under the label on hover. */
  readonly meaning: string;
  readonly parts: readonly MarkPart[];
}

/** Width and height of every mark's box, in the units its parts are drawn in. */
export const MARK_WIDTH = 14;
export const MARK_HEIGHT = 12;

const MID = MARK_HEIGHT / 2;

/**
 * Where the ring of `interrupted` sits, and how wide it is. Its mirror in
 * `live` is placed by reflection, so the pair cannot drift apart.
 *
 * The radius is what the ring needs to stay open. A ring drawn much smaller
 * than this closes into a dot once the page scale is turned down, which
 * would make the two mirrored marks one mark; `MARK_HEIGHT` and the `em`
 * height the stylesheet gives a mark put this radius at the same number of
 * device pixels as the trajectory's running-episode ring.
 */
const RING_X = 11;
const RING_R = 2.4;
/** Where the hairline meets the ring, and where it ends at the far edge. */
const LINE_INNER = RING_X - RING_R;
const LINE_OUTER = MARK_WIDTH - RING_X - RING_R;

/** The x a part at `x` takes in the mirrored mark. */
const flip = (x: number): number => MARK_WIDTH - x;

export const MARKS: Readonly<Record<MarkKind, Mark>> = {
  // A hairline that runs and stops at an open ring. The line is the response
  // that arrived and the ring is open because the turn never closed, which
  // is what an interruption is. The ring is the last thing in the box, so
  // the mark carries nothing after the stop; `live` below reverses that.
  interrupted: {
    label: "interrupted",
    meaning: "the response stopped before the turn closed",
    parts: [
      { shape: "path", attrs: { d: `M ${LINE_OUTER} ${MID} L ${LINE_INNER} ${MID}` } },
      { shape: "circle", attrs: { cx: RING_X, cy: MID, r: RING_R } },
    ],
  },
  // The open ring the trajectory gives a running episode, with the dashed
  // continuation it gives a lifetime that has not ended yet. The ring is
  // the turn still open and the dashes are the tokens still to arrive.
  // Reflecting `interrupted` is what makes the two read as opposites, so
  // the reflection is computed rather than written out.
  live: {
    label: "live",
    meaning: "the response is still arriving",
    parts: [
      { shape: "circle", attrs: { cx: flip(RING_X), cy: MID, r: RING_R } },
      { shape: "path", attrs: { d: `M ${flip(LINE_INNER)} ${MID} L ${flip(LINE_OUTER)} ${MID}` } },
    ],
  },
  // The cross the trajectory gives a failed outcome and a retried attempt.
  // The meaning is the same one: this attempt did not succeed.
  error: {
    label: "error",
    meaning: "the tool reported a failure",
    parts: [{ shape: "path", attrs: { d: "M 4 3 l 6 6 M 10 3 l -6 6" } }],
  },
  // A hairline the full width of the box, dashed. The width says that a
  // result of the ordinary extent is present, and the dashes say that the
  // runtime supplied it rather than a tool producing it. The stylesheet
  // sets a finer dash rhythm than a text dash, so the mark does not read as
  // a run of em dashes in the metadata beside it.
  synthetic: {
    label: "synthetic",
    meaning: "the runtime wrote this result; the tool did not run",
    parts: [{ shape: "path", attrs: { d: `M 1 ${MID} L 13 ${MID}` } }],
  },
  // The end of a tool call's tick in the causality figure: a dot, which is
  // the least a leaf can be drawn as. It carries no direction, because the
  // call returned; a call that failed takes `error` instead, which is the
  // same cross the conversation gives a failed result.
  call: {
    label: "call",
    meaning: "one tool call, which returned",
    parts: [{ shape: "circle", attrs: { cx: MARK_WIDTH / 2, cy: MID, r: 2 } }],
  },
  // The foot of a lane that closed: the same open ring `interrupted` and
  // the trajectory's running episode use, standing alone. Its colour is
  // the outcome's direction, which is the one place hue is a verdict.
  settled: {
    label: "settled",
    meaning: "the episode reached a typed outcome",
    parts: [{ shape: "circle", attrs: { cx: MARK_WIDTH / 2, cy: MID, r: 3.2 } }],
  },
};
