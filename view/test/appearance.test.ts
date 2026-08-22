// The appearance catalogue: the defaults, the naming rules the pickers
// draw, and the page-scale clamp.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DEFAULT_FONTSIZE,
  DEFAULT_THEME_DARK,
  DEFAULT_THEME_LIGHT,
  DEFAULT_TYPEFACE,
  FONT_SIZES,
  SCALE_DEFAULT,
  SCALE_MAX,
  SCALE_MIN,
  SCALE_STEP,
  THEMES,
  TYPEFACES,
  leadFamily,
  normaliseScale,
  specimenLine,
} from "../src/appearance.js";

test("every default names an option the catalogue holds", () => {
  assert.ok(THEMES.some((t) => t.id === DEFAULT_THEME_LIGHT));
  assert.ok(THEMES.some((t) => t.id === DEFAULT_THEME_DARK));
  assert.ok(TYPEFACES.some((f) => f.id === DEFAULT_TYPEFACE));
  assert.ok(FONT_SIZES.some((o) => o.id === DEFAULT_FONTSIZE));
});

test("the two default themes are the light and the dark ground of one palette", () => {
  assert.equal(DEFAULT_THEME_LIGHT, "google-light");
  assert.equal(DEFAULT_THEME_DARK, "google-dark");
});

test("the default typeface is the first face of the first mode", () => {
  assert.equal(TYPEFACES[0]!.id, DEFAULT_TYPEFACE);
  assert.equal(TYPEFACES[0]!.mode, "technical");
});

test("the catalogue holds sixteen themes and twelve faces, four per mode", () => {
  assert.equal(THEMES.length, 16);
  assert.equal(TYPEFACES.length, 12);
  for (const mode of ["technical", "editorial", "display"]) {
    assert.equal(TYPEFACES.filter((f) => f.mode === mode).length, 4, mode);
  }
});

test("every identifier and every name is used once", () => {
  assert.equal(new Set(THEMES.map((t) => t.id)).size, THEMES.length);
  assert.equal(new Set(TYPEFACES.map((f) => f.id)).size, TYPEFACES.length);
  assert.equal(new Set(TYPEFACES.map((f) => f.label)).size, TYPEFACES.length);
});

test("an option that sets one family everywhere is named by that family", () => {
  for (const face of TYPEFACES) {
    const oneFamily = face.sans === face.mono && face.sans === face.head;
    if (oneFamily) assert.ok(!face.label.includes(" + "), face.id);
  }
  assert.equal(TYPEFACES.find((f) => f.id === "technical-inconsolata")!.label, "inconsolata");
});

test("a name is at most one family longer than its lead", () => {
  for (const face of TYPEFACES) {
    assert.ok(face.label.split(" + ").length <= 2, face.id);
  }
});

test("the trigger's name is the option's first family", () => {
  assert.equal(leadFamily("inconsolata"), "inconsolata");
  assert.equal(leadFamily("ia writer + jetbrains"), "ia writer");
  assert.equal(leadFamily("barlow + space grotesk"), "barlow");
  for (const face of TYPEFACES) assert.ok(leadFamily(face.label).length > 0, face.id);
});

test("a technical face specimens code and the other two a sentence", () => {
  assert.equal(specimenLine("technical"), "let outcome = episode.run();");
  assert.equal(specimenLine("editorial"), specimenLine("display"));
  assert.ok(!specimenLine("editorial").includes("="));
});

test("the page scale clamps to its range and snaps to its step", () => {
  assert.equal(normaliseScale(100), SCALE_DEFAULT);
  assert.equal(normaliseScale(103), 105);
  assert.equal(normaliseScale(1), SCALE_MIN);
  assert.equal(normaliseScale(1000), SCALE_MAX);
  assert.equal(normaliseScale("not a number"), SCALE_DEFAULT);
  assert.equal(normaliseScale(undefined), SCALE_DEFAULT);
  assert.equal(normaliseScale("115"), 115);
  assert.equal((SCALE_MAX - SCALE_MIN) % SCALE_STEP, 0);
});

test("a text size scales up and never down", () => {
  assert.equal(FONT_SIZES[0]!.scale, 1);
  for (let i = 1; i < FONT_SIZES.length; i += 1) {
    assert.ok(FONT_SIZES[i]!.scale > FONT_SIZES[i - 1]!.scale, FONT_SIZES[i]!.id);
  }
});

test("every theme previews six chips as six hex colours", () => {
  for (const theme of THEMES) {
    assert.equal(theme.preview.length, 6, theme.id);
    for (const chip of theme.preview) assert.match(chip, /^#[0-9a-fA-F]{6}$/, `${theme.id} ${chip}`);
  }
});
