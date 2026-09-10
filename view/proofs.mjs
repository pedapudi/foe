#!/usr/bin/env node
// Regenerates the proof screenshots beside this file.
//
// Each proof is one static export of a fixture tree, opened in a headless
// browser at the size the README states, with the settings the viewer keeps
// in local storage written before the page boots and any further state
// reached by clicking what a reader would click. Nothing here reaches into
// the page's own modules: what the script drives is what a reader drives.
//
//     node view/proofs.mjs [--binary PATH] [--only NAME]
//
// The binary defaults to target/release/foe and must be current, because a
// proof is a picture of the bundle that binary carries.

import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..");
const fixtures = join(here, "fixtures");

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf(name);
  return at === -1 ? fallback : args[at + 1];
};
const binary = resolve(repo, option("--binary", "target/release/foe"));
const only = option("--only", null);

/** The browser to drive. The first of these that exists is used. */
const BROWSERS = ["google-chrome", "chromium", "chromium-browser"];
const WIDTH = 1512;
const HEIGHT = 792;

/**
 * One proof: the episode tree it opens, the settings it starts under, and
 * the selectors a reader would click, in order, to reach what it shows.
 */
// `retries-exhausted.jsonl` is a recorded log and the rest are written to a
// fixed clock, so a tree holding both spans the years between them. The
// outline measures its gutter from the earliest start in the run, which is
// the right reading of a run and the wrong reading of an assembly, so no
// proof mixes a recorded log with a written one.
const PROOFS = [
  { name: "proof-light", root: "overlap-parent", children: ["overlap-child", "child"], theme: "google-light" },
  { name: "proof-dark", root: "overlap-parent", children: ["overlap-child", "child"], theme: "google-dark" },
  { name: "proof-one-episode", root: "rich", children: [], theme: "google-light" },
  {
    name: "proof-causality-light",
    root: "workflow",
    children: ["workflow-propose-1", "workflow-propose-2", "workflow-apply-1"],
    theme: "google-light",
    click: ["[data-axis='causality']"],
  },
  {
    name: "proof-causality-dark",
    root: "workflow",
    children: ["workflow-propose-1", "workflow-propose-2", "workflow-apply-1"],
    theme: "google-dark",
    click: ["[data-axis='causality']"],
  },
  {
    name: "proof-outline-light",
    root: "root",
    children: ["child", "fork"],
    theme: "google-light",
    layout: "outline",
  },
  {
    name: "proof-outline-dark",
    root: "root",
    children: ["child", "fork"],
    theme: "google-dark",
    layout: "outline",
    depth: "calls",
  },
];

/** The episode directory a proof opens, laid out as a run's log tree. */
function tree(work, proof) {
  const dir = join(work, proof.name);
  mkdirSync(join(dir, "children"), { recursive: true });
  cpSync(join(fixtures, `${proof.root}.jsonl`), join(dir, "episode.jsonl"));
  for (const child of proof.children) {
    const id = childId(join(fixtures, `${child}.jsonl`)) ?? child;
    mkdirSync(join(dir, "children", id), { recursive: true });
    cpSync(join(fixtures, `${child}.jsonl`), join(dir, "children", id, "episode.jsonl"));
  }
  return dir;
}

/** The episode id a log opens with, which is the directory name it takes. */
function childId(path) {
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    const event = JSON.parse(line);
    if (event.type === "episode/start") return event.data?.id ?? null;
  }
  return null;
}

/**
 * The exported page with a boot script ahead of the bundle. The settings a
 * reader keeps are written to local storage before the app reads them, and
 * the clicks a reader would make are dispatched once the app has drawn.
 */
function staged(html, proof) {
  const settings = {
    "foe.theme": proof.theme,
    "foe.layout": proof.layout ?? "details",
    ...(proof.depth ? { "foe.depth": proof.depth } : {}),
  };
  const boot = `<script>
try { for (const [k, v] of Object.entries(${JSON.stringify(settings)})) localStorage.setItem(k, v); } catch {}
addEventListener("load", () => setTimeout(() => {
  for (const selector of ${JSON.stringify(proof.click ?? [])}) document.querySelector(selector)?.click();
}, 400));
</script>`;
  return html.replace("</head>", `${boot}</head>`);
}

function browser() {
  for (const candidate of BROWSERS) {
    try {
      execFileSync("command", ["-v", candidate], { shell: true, stdio: "pipe" });
      return candidate;
    } catch {}
  }
  throw new Error(`no browser found; one of ${BROWSERS.join(", ")} is needed`);
}

const chrome = browser();
const work = mkdtempSync(join(tmpdir(), "foe-proofs-"));
try {
  for (const proof of PROOFS) {
    if (only && proof.name !== only) continue;
    const dir = tree(work, proof);
    const html = execFileSync(binary, ["view", dir], { encoding: "utf8", maxBuffer: 64 << 20 });
    const page = join(work, `${proof.name}.html`);
    writeFileSync(page, staged(html, proof));
    execFileSync(chrome, [
      "--headless",
      "--disable-gpu",
      "--no-sandbox",
      "--hide-scrollbars",
      `--window-size=${WIDTH},${HEIGHT}`,
      "--virtual-time-budget=9000",
      `--screenshot=${join(here, `${proof.name}.png`)}`,
      `file://${page}`,
    ], { stdio: "pipe" });
    console.log(`${proof.name}.png`);
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}
