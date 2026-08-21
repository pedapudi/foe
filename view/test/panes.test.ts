// Pane sizes: what is stored, what is read back, and how the limits keep
// a region from collapsing.

import assert from "node:assert/strict";
import { test } from "node:test";
import { PANE_DEFAULTS, PANE_LIMITS, clampPanes, parsePanes, serialisePanes } from "../src/panes.js";
import type { PaneExtent, PaneSizes } from "../src/panes.js";

const ROOM: PaneExtent = { width: 1512, leftHeight: 700, rightHeight: 700 };

test("no stored value gives the defaults", () => {
  assert.deepEqual(parsePanes(null), PANE_DEFAULTS);
  assert.deepEqual(parsePanes("not json"), PANE_DEFAULTS);
  assert.deepEqual(parsePanes("null"), PANE_DEFAULTS);
  assert.deepEqual(parsePanes("[1,2,3]"), PANE_DEFAULTS);
});

test("a stored size survives a round trip", () => {
  const sizes: PaneSizes = { sidebar: 412, details: 0.42, trajectory: 0.28 };
  assert.deepEqual(parsePanes(serialisePanes(sizes)), sizes);
});

test("a partial or misspelled stored value keeps the defaults for what is missing", () => {
  assert.deepEqual(parsePanes('{"sidebar": 260}'), { ...PANE_DEFAULTS, sidebar: 260 });
  assert.deepEqual(parsePanes('{"sidebar": "wide"}'), PANE_DEFAULTS);
  assert.deepEqual(parsePanes('{"details": null, "trajectory": 0.5}'), { ...PANE_DEFAULTS, trajectory: 0.5 });
});

test("the sidebar stays between its narrowest and widest", () => {
  assert.equal(clampPanes({ ...PANE_DEFAULTS, sidebar: 10 }, ROOM).sidebar, PANE_LIMITS.sidebarMin);
  assert.equal(clampPanes({ ...PANE_DEFAULTS, sidebar: 5000 }, ROOM).sidebar, PANE_LIMITS.sidebarMax);
  assert.equal(clampPanes({ ...PANE_DEFAULTS, sidebar: 380 }, ROOM).sidebar, 380);
});

test("a narrow window caps the sidebar at a share of its width", () => {
  const narrow: PaneExtent = { width: 600, leftHeight: 700, rightHeight: 700 };
  assert.equal(clampPanes({ ...PANE_DEFAULTS, sidebar: 5000 }, narrow).sidebar, 600 * PANE_LIMITS.sidebarShare);
});

test("a window narrower than the smallest sidebar still gives that smallest width", () => {
  const tiny: PaneExtent = { width: 200, leftHeight: 700, rightHeight: 700 };
  assert.equal(clampPanes({ ...PANE_DEFAULTS, sidebar: 10 }, tiny).sidebar, PANE_LIMITS.sidebarMin);
});

test("neither part of a row split falls below the shortest pane", () => {
  const { rowMin } = PANE_LIMITS;
  const low = clampPanes({ ...PANE_DEFAULTS, details: 0.01 }, ROOM);
  assert.equal(low.details, rowMin / ROOM.leftHeight);
  const high = clampPanes({ ...PANE_DEFAULTS, trajectory: 0.99 }, ROOM);
  assert.equal(high.trajectory, 1 - rowMin / ROOM.rightHeight);
  assert.equal(low.details * ROOM.leftHeight, rowMin);
});

test("a column too short for both minimums splits in the middle", () => {
  const short: PaneExtent = { width: 1512, leftHeight: 120, rightHeight: 120 };
  const sizes = clampPanes({ sidebar: 300, details: 0.05, trajectory: 0.95 }, short);
  assert.equal(sizes.details, 0.5);
  assert.equal(sizes.trajectory, 0.5);
});

test("a size that is not a number falls back rather than propagating", () => {
  const sizes = clampPanes({ sidebar: Number.NaN, details: Number.NaN, trajectory: Number.NaN }, ROOM);
  assert.equal(sizes.sidebar, PANE_DEFAULTS.sidebar);
  assert.equal(sizes.details, 0.5);
  assert.equal(sizes.trajectory, 0.5);
});

test("the defaults themselves survive clamping at a common window size", () => {
  assert.deepEqual(clampPanes(PANE_DEFAULTS, ROOM), PANE_DEFAULTS);
});

test("an unmeasured layout leaves the fractions alone", () => {
  const unmeasured: PaneExtent = { width: 0, leftHeight: 0, rightHeight: 0 };
  const sizes = clampPanes({ sidebar: 300, details: 0.3, trajectory: 0.35 }, unmeasured);
  assert.deepEqual(sizes, { sidebar: 300, details: 0.3, trajectory: 0.35 });
});
