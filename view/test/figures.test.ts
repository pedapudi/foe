// Rules of the figures that hold for every rendering, checked where they
// can fail.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readdirSync, readFileSync } from "node:fs";

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
