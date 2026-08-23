// The drawn marks: the invariants that keep six small drawings legible,
// alignable, and rethemable.

import assert from "node:assert/strict";
import { test } from "node:test";
import { MARKS, MARK_HEIGHT, MARK_WIDTH } from "../src/marks.js";
import type { Mark, MarkKind } from "../src/marks.js";

const KINDS: MarkKind[] = ["interrupted", "live", "error", "synthetic", "call", "settled"];

/** Every coordinate a part places, as absolute positions in the mark's box. */
function points(mark: Mark): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  for (const part of mark.parts) {
    if (part.shape === "circle") {
      const cx = Number(part.attrs.cx);
      const cy = Number(part.attrs.cy);
      const r = Number(part.attrs.r);
      out.push({ x: cx - r, y: cy - r }, { x: cx + r, y: cy + r });
      continue;
    }
    // A path here is a run of `M x y` and `L x y` moves and `l dx dy`
    // offsets from the point before, which is all four marks use.
    let x = 0;
    let y = 0;
    const tokens = String(part.attrs.d).trim().split(/\s+/);
    for (let i = 0; i < tokens.length; i += 3) {
      const op = tokens[i]!;
      const a = Number(tokens[i + 1]);
      const b = Number(tokens[i + 2]);
      assert.ok(["M", "L", "l"].includes(op), `unexpected path command ${op}`);
      if (op === "l") {
        x += a;
        y += b;
      } else {
        x = a;
        y = b;
      }
      out.push({ x, y });
    }
  }
  return out;
}

/** One part reduced to its geometry, so two marks can be compared as drawings. */
type Figure = { circle: [number, number, number] } | { line: { x: number; y: number }[] };

/** Coordinates are compared after rounding, because a mirrored coordinate is
 * a subtraction and a subtraction of decimals does not land exactly. */
const round = (n: number) => Number(n.toFixed(4));

function figures(mark: Mark): Figure[] {
  return mark.parts.map((part) =>
    part.shape === "circle"
      ? {
          circle: [round(Number(part.attrs.cx)), round(Number(part.attrs.cy)), round(Number(part.attrs.r))] as [
            number,
            number,
            number,
          ],
        }
      : { line: points({ ...mark, parts: [part] }).map((p) => ({ x: round(p.x), y: round(p.y) })) },
  );
}

/** The same drawing reflected about the vertical centre of the mark's box. */
function reflected(mark: Mark): Figure[] {
  const flip = (x: number) => round(MARK_WIDTH - x);
  return figures(mark)
    .map((f) =>
      "circle" in f
        ? { circle: [flip(f.circle[0]), f.circle[1], f.circle[2]] as [number, number, number] }
        : { line: f.line.map((p) => ({ x: flip(p.x), y: p.y })).reverse() },
    )
    .reverse();
}

test("the catalogue holds one mark for each state a row or a figure draws", () => {
  assert.deepEqual(Object.keys(MARKS).sort(), [...KINDS].sort());
});

test("every mark names the word it replaces and what that word means", () => {
  for (const kind of KINDS) {
    const mark = MARKS[kind];
    assert.equal(mark.label, kind, `${kind} is read out as the word it replaced`);
    assert.ok(mark.meaning.length > 0, `${kind} has no meaning to show on hover`);
    assert.ok(!mark.meaning.endsWith("."), `${kind} meaning is a phrase, not a sentence with a stop`);
  }
});

test("every part stays inside the one box every mark shares", () => {
  for (const kind of KINDS) {
    for (const p of points(MARKS[kind])) {
      assert.ok(p.x >= 0 && p.x <= MARK_WIDTH, `${kind} places x ${p.x} outside 0..${MARK_WIDTH}`);
      assert.ok(p.y >= 0 && p.y <= MARK_HEIGHT, `${kind} places y ${p.y} outside 0..${MARK_HEIGHT}`);
    }
  }
});

test("every mark is drawn, so none is an empty box", () => {
  for (const kind of KINDS) {
    assert.ok(MARKS[kind].parts.length > 0, `${kind} draws nothing`);
  }
});

test("no part names a colour, so every mark rethemes", () => {
  for (const kind of KINDS) {
    for (const part of MARKS[kind].parts) {
      for (const [name, value] of Object.entries(part.attrs)) {
        assert.ok(
          !/^(fill|stroke|color)$/.test(name),
          `${kind} sets ${name} on a part; colour belongs to the stylesheet`,
        );
        assert.ok(!/#|rgb|hsl|var\(/.test(String(value)), `${kind} names a colour in ${name}`);
      }
    }
  }
});

test("the interrupted stream runs and stops, and the live stream is its mirror", () => {
  // The reading rests on the two being opposites: interrupted carries its
  // line behind the ring and nothing ahead, live carries it ahead and
  // nothing behind. A change that broke that would make them one mark.
  const stopped = MARKS.interrupted.parts;
  const running = MARKS.live.parts;
  assert.equal(stopped[0]!.shape, "path");
  assert.equal(stopped[1]!.shape, "circle");
  assert.equal(running[0]!.shape, "circle");
  assert.equal(running[1]!.shape, "path");
  // Reflection about the box's vertical centre, part for part. Direction is
  // the whole of the difference between the two, so nothing else may
  // differ: a ring one mark drew larger than the other would read as a
  // second distinction that means nothing.
  assert.deepEqual(reflected(MARKS.live), figures(MARKS.interrupted));
});

test("the ring stays open at the smallest size a reader can set", () => {
  // A ring closes into a dot once its hole falls under about three device
  // pixels, and the two mirrored marks then read as one. The worst case a
  // reader can produce is the smallest text size, which leaves the base 13
  // pixels unscaled, at the lowest page scale of 70 percent; the stylesheet
  // gives a mark a height of 1.2em and a 1.2-pixel stroke that the box does
  // not scale.
  const stroke = 1.2;
  const perUnit = (13 * 1.2 * 0.7) / MARK_HEIGHT;
  for (const kind of ["interrupted", "live"] as const) {
    const ring = MARKS[kind].parts.find((p) => p.shape === "circle");
    assert.ok(ring, `${kind} has no ring`);
    const hole = 2 * Number(ring.attrs.r) * perUnit - stroke;
    assert.ok(hole >= 3, `${kind} ring hole falls to ${hole.toFixed(2)}px, under the 3px that still reads as open`);
  }
});
