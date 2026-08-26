// The mathematics seam: which renderer runs, and what happens with none.
//
// These assertions stay clear of the DOM, which the bundle's tests have no
// implementation of. What renderMath builds is covered by the browser; what
// is covered here is the decision of whether a renderer runs at all, which
// is what a build chooses by installing one or not.

import assert from "node:assert/strict";
import { test } from "node:test";
import { hasMathRenderer, setMathRenderer } from "../src/render/math.js";

test("no renderer is installed until one is", () => {
  setMathRenderer(null);
  assert.equal(hasMathRenderer(), false);
});

test("installing a renderer is observable, and removing it restores the fallback", () => {
  setMathRenderer(() => null);
  assert.equal(hasMathRenderer(), true);
  setMathRenderer(null);
  assert.equal(hasMathRenderer(), false);
});

test("a renderer replaces the one installed before it", () => {
  const first = (): null => null;
  const second = (): null => null;
  setMathRenderer(first);
  setMathRenderer(second);
  assert.equal(hasMathRenderer(), true);
  setMathRenderer(null);
});
