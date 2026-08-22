// Pane sizes: what is stored, what is read back, and how the limits keep
// a region from collapsing.

import assert from "node:assert/strict";
import { test } from "node:test";
import { PANE_DEFAULTS, PANE_LIMITS, clampPanes, fitTrajectory, parsePanes, serialisePanes, storedPaneKeys } from "../src/panes.js";
import type { PaneExtent, PaneSizes } from "../src/panes.js";
import { ROW_HEIGHT, trajectoryContentHeight } from "../src/trajectory.js";

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

// ---- the trajectory's derived height ----

const CHROME = 28;

test("the figure's content height grows by the pixels its rows take", () => {
  const one = trajectoryContentHeight(ROW_HEIGHT);
  assert.equal(trajectoryContentHeight(2 * ROW_HEIGHT) - one, ROW_HEIGHT);
  assert.equal(trajectoryContentHeight(0), one - ROW_HEIGHT);
  assert.equal(trajectoryContentHeight(-3), trajectoryContentHeight(0), "a negative extent is no rows");
});

test("one episode takes the height of one episode rather than a fixed share", () => {
  const column = 700;
  const height = fitTrajectory(ROW_HEIGHT, column, CHROME) * column;
  // One row wants less than the shortest pane, so the floor decides.
  assert.equal(CHROME + trajectoryContentHeight(ROW_HEIGHT), 86);
  assert.equal(height, PANE_LIMITS.rowMin);
  assert.ok(height < PANE_DEFAULTS.trajectory * column, "the derived height is under the fixed default");
});

test("each further row raises the derived height by one row", () => {
  const column = 700;
  // Counted from a row count whose content already clears the floor.
  const three = fitTrajectory(3 * ROW_HEIGHT, column, CHROME) * column;
  const five = fitTrajectory(5 * ROW_HEIGHT, column, CHROME) * column;
  assert.equal(three, CHROME + trajectoryContentHeight(3 * ROW_HEIGHT));
  assert.equal(five - three, 2 * ROW_HEIGHT);
});

test("many rows stop at half the column, leaving the conversation the rest", () => {
  const column = 700;
  assert.equal(fitTrajectory(40 * ROW_HEIGHT, column, CHROME), 0.5);
  assert.equal(fitTrajectory(400 * ROW_HEIGHT, column, CHROME), 0.5);
});

test("a row count too small to reach the shortest pane still reaches it", () => {
  const column = 700;
  assert.equal(fitTrajectory(0, column, 0) * column, PANE_LIMITS.rowMin);
});

test("an unmeasured column falls back to the fixed default", () => {
  assert.equal(fitTrajectory(1, 0, CHROME), PANE_DEFAULTS.trajectory);
  assert.equal(fitTrajectory(1, -10, CHROME), PANE_DEFAULTS.trajectory);
});

test("the derived height survives clamping, so it is applied as computed", () => {
  const room: PaneExtent = { width: 1512, leftHeight: 700, rightHeight: 700 };
  const fraction = fitTrajectory(3, 700, CHROME);
  assert.equal(clampPanes({ ...PANE_DEFAULTS, trajectory: fraction }, room).trajectory, fraction);
});

// ---- which sizes the reader has set ----

test("a stored value names only the sizes the reader has moved", () => {
  assert.deepEqual(storedPaneKeys(null), []);
  assert.deepEqual(storedPaneKeys("{}"), []);
  assert.deepEqual(storedPaneKeys("not json"), []);
  assert.deepEqual(storedPaneKeys('{"sidebar":420}'), ["sidebar"]);
  assert.deepEqual(storedPaneKeys('{"trajectory":0.6,"sidebar":420}'), ["sidebar", "trajectory"]);
  assert.deepEqual(storedPaneKeys('{"trajectory":"tall"}'), [], "a value that is not a number names nothing");
});

test("serialising writes only the sizes it is given", () => {
  const sizes: PaneSizes = { sidebar: 412, details: 0.42, trajectory: 0.28 };
  assert.equal(serialisePanes(sizes, ["trajectory"]), '{"trajectory":0.28}');
  assert.deepEqual(storedPaneKeys(serialisePanes(sizes, [])), []);
  assert.deepEqual(storedPaneKeys(serialisePanes(sizes)), ["sidebar", "details", "trajectory"]);
});

test("a stored trajectory overrides the derived height, and its absence does not", () => {
  const raw = serialisePanes({ sidebar: 300, details: 0.3, trajectory: 0.62 }, ["trajectory"]);
  assert.deepEqual(storedPaneKeys(raw), ["trajectory"]);
  assert.equal(parsePanes(raw).trajectory, 0.62);
  // With nothing stored the parsed value is the fixed default, which the
  // derived height then replaces because no key is pinned.
  assert.deepEqual(storedPaneKeys("{}"), []);
  assert.equal(parsePanes("{}").trajectory, PANE_DEFAULTS.trajectory);
});

test("an unmeasured layout leaves the fractions alone", () => {
  const unmeasured: PaneExtent = { width: 0, leftHeight: 0, rightHeight: 0 };
  const sizes = clampPanes({ sidebar: 300, details: 0.3, trajectory: 0.35 }, unmeasured);
  assert.deepEqual(sizes, { sidebar: 300, details: 0.3, trajectory: 0.35 });
});
