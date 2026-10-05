// Two rules of the figures that hold for every rendering, checked where
// they can fail: the source that builds a figure, and the one function that
// prints a share.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readdirSync, readFileSync } from "node:fs";
import { percent } from "../src/render/statistics.js";

const renderDir = new URL("../src/render/", import.meta.url);

/**
 * docs/design-language.md "The hovercard": no figure appends a tooltip of the
 * browser's own. An SVG `title` element is one, so no render module builds one.
 */
test("no figure builds a browser tooltip", () => {
  const offenders = readdirSync(renderDir)
    .filter((name) => name.endsWith(".ts"))
    .filter((name) => readFileSync(new URL(name, renderDir), "utf8").includes('svg("title")'));
  assert.deepEqual(offenders, []);
});

/**
 * docs/viewer.md "The statistics view": a share short of the whole never reads as the
 * whole, and the whole reads as the whole.
 */
test("a share short of the whole never prints as one hundred percent", () => {
  assert.equal(percent(0.9996), "99.9%");
  assert.equal(percent(0.99949), "99.9%");
  assert.equal(percent(0.9976), "99.8%");
  assert.equal(percent(1), "100%");
  assert.equal(percent(0.5), "50%");
  assert.equal(percent(0.042), "4.2%");
});
