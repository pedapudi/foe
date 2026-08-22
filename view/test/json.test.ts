// The rules the raw events tab draws a JSON value by (src/json.ts): what
// kind a value is, what a collapsed node states, and which nodes open
// before a reader asks.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  INLINE_STRING_CHARS,
  OPEN_LINES,
  PREVIEW_KEYS,
  STRING_HEAD_CHARS,
  children,
  countLabel,
  isInlineString,
  jsonKind,
  openLines,
  opensByDefault,
  previewLabel,
  stringHead,
} from "../src/json.js";

test("every JSON value reports one of the six kinds", () => {
  assert.equal(jsonKind(null), "null");
  assert.equal(jsonKind(undefined), "null");
  assert.equal(jsonKind(true), "boolean");
  assert.equal(jsonKind(3), "number");
  assert.equal(jsonKind("x"), "string");
  assert.equal(jsonKind([1]), "array");
  assert.equal(jsonKind({ a: 1 }), "object");
});

test("keys keep the order the log wrote them in rather than being sorted", () => {
  const value = JSON.parse('{"step":1,"attempt":1,"request_id":"rq_0001","consumed":[1]}') as unknown;
  assert.deepEqual(
    children(value).map((c) => c.label),
    ["step", "attempt", "request_id", "consumed"],
  );
});

test("an array's members are labelled by position", () => {
  assert.deepEqual(children(["a", "b"]), [
    { label: "0", value: "a" },
    { label: "1", value: "b" },
  ]);
});

test("a scalar has no members", () => {
  assert.deepEqual(children("text"), []);
  assert.deepEqual(children(null), []);
});

test("a collapsed node states how much it holds, with the noun agreeing", () => {
  assert.equal(countLabel({ a: 1 }), "1 key");
  assert.equal(countLabel({ a: 1, b: 2 }), "2 keys");
  assert.equal(countLabel([1]), "1 item");
  assert.equal(countLabel([]), "0 items");
  assert.equal(countLabel("text"), "");
});

test("a collapsed object names its first keys and stops with an ellipsis", () => {
  const many = { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6 };
  assert.equal(children(many).length > PREVIEW_KEYS, true);
  assert.equal(previewLabel(many), "a, b, c, d, …");
  assert.equal(previewLabel({ a: 1, b: 2 }), "a, b");
});

test("a collapsed array names nothing, because its members have no names", () => {
  assert.equal(previewLabel([1, 2, 3]), "");
});

test("a string sets inline up to the inline length and no further", () => {
  assert.equal(isInlineString("x".repeat(INLINE_STRING_CHARS)), true);
  assert.equal(isInlineString("x".repeat(INLINE_STRING_CHARS + 1)), false);
});

test("a string holding a line break never sets inline", () => {
  assert.equal(isInlineString("one\ntwo"), false);
});

test("the summary of a block string is its first line, cut with an ellipsis", () => {
  assert.equal(stringHead("first\nsecond"), "first…");
  const long = "y".repeat(STRING_HEAD_CHARS + 20);
  assert.equal(stringHead(long), `${"y".repeat(STRING_HEAD_CHARS)}…`);
  assert.equal(stringHead("short"), "short");
});

test("a scalar takes one line and a block string takes two", () => {
  assert.equal(openLines(3), 1);
  assert.equal(openLines("short"), 1);
  assert.equal(openLines("one\ntwo"), 2);
});

test("counting lines stops at the cap, so a large payload costs no more than a small one", () => {
  const deep: unknown[] = [];
  let node: unknown[] = deep;
  for (let i = 0; i < 500; i++) {
    const next: unknown[] = [];
    node.push(next);
    node = next;
  }
  assert.equal(openLines(deep), OPEN_LINES + 1);
});

test("a short node near the surface opens without being asked", () => {
  assert.equal(opensByDefault({ step: 1, attempt: 1 }, 0), true);
  assert.equal(opensByDefault([1, 2, 3], 1), true);
});

test("a node deeper than the opening depth stays closed however little it holds", () => {
  assert.equal(opensByDefault({ a: 1 }, 3), false);
});

test("a node longer than the opening length stays closed", () => {
  const wide: Record<string, number> = {};
  for (let i = 0; i < OPEN_LINES + 2; i++) wide[`k${i}`] = i;
  assert.equal(opensByDefault(wide, 0), false);
});

test("an empty node and a scalar carry no expander", () => {
  assert.equal(opensByDefault({}, 0), false);
  assert.equal(opensByDefault([], 0), false);
  assert.equal(opensByDefault("text", 0), false);
});
