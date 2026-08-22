// The drawn marks: the invariants that keep four small drawings legible,
// alignable, and rethemable.

import assert from "node:assert/strict";
import { test } from "node:test";
import { MARKS, MARK_HEIGHT, MARK_WIDTH } from "../src/marks.js";
import type { Mark, MarkKind } from "../src/marks.js";

const KINDS: MarkKind[] = ["interrupted", "live", "error", "synthetic"];

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

test("the catalogue holds one mark for each state the conversation draws", () => {
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

test("every part stays inside the one box all four marks share", () => {
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
  assert.ok(Number(stopped[1]!.attrs.cx) > Number(running[0]!.attrs.cx));
});
