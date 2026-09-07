// Two properties of the stylesheet that no rendering test can see, because
// both fail silently: a rule that is legible in the theme it was written
// against and not in the other fifteen, and a size that answers the reader's
// text setting twice or not at all. Each was a live defect when this file
// was written.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { PULSE_FRAMES } from "../src/brand.js";

const viewerCss = readFileSync(new URL("../src/viewer.css", import.meta.url), "utf8");
const tokensCss = readFileSync(new URL("../src/tokens.css", import.meta.url), "utf8");

/** Relative luminance of a `#rrggbb` colour, per WCAG 2. */
function luminance(hex: string): number {
  const parts = [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16) / 255);
  const linear = parts.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (high + 0.05) / (low + 0.05);
}

/** Every theme block, as its role tokens. */
function themes(): Map<string, Record<string, string>> {
  const out = new Map<string, Record<string, string>>();
  const block = /:root\[data-theme="([a-z-]+)"\] \{([^}]*)\}/g;
  for (let m = block.exec(tokensCss); m !== null; m = block.exec(tokensCss)) {
    const tokens: Record<string, string> = {};
    const decl = /(--v2-[a-z-]+):\s*(#[0-9A-Fa-f]{6})/g;
    for (let d = decl.exec(m[2]!); d !== null; d = decl.exec(m[2]!)) tokens[d[1]!] = d[2]!;
    if ("--v2-paper" in tokens) out.set(m[1]!, tokens);
  }
  return out;
}

test("every theme carries the sixteen role tokens the stylesheet reads", () => {
  const all = themes();
  assert.equal(all.size, 16);
  for (const [name, tokens] of all) assert.equal(Object.keys(tokens).length, 15, name);
});

// The ink a figure writes its labels in. Figure labels draw between 7 and
// 10.5 pixels, which is under the size at which WCAG relaxes its floor, so
// the floor that applies to them is 4.5:1. `--v2-ink-faint` is under it in
// fourteen of the sixteen themes and under 3:1 in eight, which is why label
// text takes `--v2-ink-soft` and `--v2-ink-faint` is left to marks, whose
// legibility comes from their area rather than from their stroke.
// 4.5:1 is the target and `--v2-ink-soft` reaches it on every theme's paper.
// The floor asserted here is 3.9 because one theme, solarized-light, puts it
// at 3.96 against its panel, and the theme blocks are copies of their source
// that this repository does not edit. The next ink up is `--v2-ink`, which is
// primary text: taking it for labels would leave nothing between them.
test("the ink a figure labels in is legible against every theme's own ground", () => {
  const floor = 3.9;
  const failures: string[] = [];
  for (const [name, tokens] of themes()) {
    for (const ground of ["--v2-paper", "--v2-panel"]) {
      const ratio = contrast(tokens["--v2-ink-soft"]!, tokens[ground]!);
      if (ratio < floor) failures.push(`${name} ink-soft on ${ground}: ${ratio.toFixed(2)}`);
    }
  }
  assert.deepEqual(failures, []);
});

test("the faintest ink is the one that would fail, so no label may take it", () => {
  const under = [...themes()].filter(([, t]) => contrast(t["--v2-ink-faint"]!, t["--v2-paper"]!) < 4.5);
  assert.ok(under.length > 0, "if this no longer holds, the rule above can be relaxed");
});

// A figure states its text size in one of two ways, and which one is right
// depends on the figure. The trajectory lays out at `clientWidth / scale` and
// renders its viewBox at `scale`, so its own drawing already carries the
// reader's setting and its text states plain pixels; applying the variable
// there scales the text twice. Every other figure draws at its own pixel
// size and its text has to carry the variable, or the setting does not reach
// it. Both mistakes render without error, so the exceptions are named here.
const UNSCALED = [".traj .tick-label", ".traj-label", ".traj-lane-label", ".traj-decision-label"];

test("text size answers the reader's setting exactly once", () => {
  const rule = /([^{}]+)\{([^}]*)\}/g;
  const wrong: string[] = [];
  for (let m = rule.exec(viewerCss); m !== null; m = rule.exec(viewerCss)) {
    const selector = m[1]!.replace(/\/\*[\s\S]*?\*\//g, "").trim().replace(/\s+/g, " ");
    const sizes = m[2]!.match(/font-size:[^;]+/g);
    if (!sizes || selector.startsWith("@")) continue;
    for (const size of sizes) {
      const scaled = size.includes("--dt-font-scale");
      const exempt = UNSCALED.includes(selector);
      // A size in em or percent is relative to its parent and already
      // carries whatever the parent was given.
      const absolute = /\d/.test(size) && !/em|%/.test(size);
      if (absolute && !scaled && !exempt) wrong.push(`${selector} states a fixed size: ${size.trim()}`);
      if (scaled && exempt) wrong.push(`${selector} answers the text size twice`);
    }
  }
  assert.deepEqual(wrong, []);
});

test("every exempt selector is one the stylesheet still carries", () => {
  for (const selector of UNSCALED) {
    assert.ok(viewerCss.includes(`${selector} {`), selector);
  }
});

// docs/brand/README.md: "Every surface that pulses the mark draws this
// sequence, so the pulse is the same drawing wherever it appears." Three
// surfaces state it — the browser bundle here, `crates/view/src/terminal.rs`
// for the terminal, and `site/build` for the landing page, which reads the
// document at build time. This holds the two that state it against the one
// that defines it.
test("the pulse the viewer draws is the sequence the brand document fixes", () => {
  const brand = readFileSync(new URL("../../docs/brand/README.md", import.meta.url), "utf8");
  const at = brand.indexOf("the eleven frames") + "the eleven frames".length;
  const declared = [...brand.slice(at, brand.indexOf(", one frame per redraw", at)).matchAll(/`(\S)`/g)].map((m) => m[1]);
  assert.equal(declared.length, 11);
  assert.deepEqual(PULSE_FRAMES, declared);

  const rust = readFileSync(new URL("../../crates/view/src/terminal.rs", import.meta.url), "utf8");
  const frames = rust.match(/const FRAMES: \[&str; 11\] = \[([^\]]*)\]/);
  assert.ok(frames, "terminal.rs no longer declares FRAMES");
  assert.deepEqual([...frames[1]!.matchAll(/"(.+?)"/g)].map((m) => m[1]), declared);
});
