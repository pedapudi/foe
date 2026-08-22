// The states the conversation draws rather than spells.
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

/** A state a conversation row carries, drawn beside the row's first line. */
export type MarkKind = "interrupted" | "live" | "error" | "synthetic";

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
  // A hairline that runs and stops at an open terminal. The line is the
  // response arriving and the ring is open because the turn never closed,
  // which is what an interruption is. Nothing follows the ring: the mark
  // has a past and no future, and `live` below is its mirror.
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
  // A result the length of a tool's own, in the dashed stroke the language
  // uses for something supplied rather than observed. It is a plain span
  // because a result has extent, and it is dashed because no tool ran.
  synthetic: {
    label: "synthetic",
    meaning: "the runtime wrote this result; the tool did not run",
    parts: [{ shape: "path", attrs: { d: `M 1 ${MID} L 13 ${MID}` } }],
  },
};
