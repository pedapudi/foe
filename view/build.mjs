// Builds dist/viewer.js and dist/viewer.css, or with --tests bundles the
// unit tests into .test-build/. The build fails when the two dist files
// together exceed the gzipped size budget from docs/design.md, "Size".

import { build } from "esbuild";
import { deflateRawSync, gzipSync } from "node:zlib";
import { mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const BUDGET_BYTES = 150 * 1024;

async function buildBundle() {
  const dist = join(root, "dist");
  rmSync(dist, { recursive: true, force: true });
  mkdirSync(dist, { recursive: true });
  await build({
    entryPoints: [join(root, "src/main.ts")],
    outfile: join(dist, "viewer.js"),
    bundle: true,
    minify: true,
    format: "iife",
    target: "es2022",
    platform: "browser",
    legalComments: "none",
  });
  await build({
    entryPoints: [join(root, "src/viewer.css")],
    outfile: join(dist, "viewer.css"),
    bundle: true,
    minify: true,
    legalComments: "none",
    // Font files are served by the host at /fonts/<name>.woff2 or inlined by
    // it as data URIs; the stylesheet keeps the path as written.
    external: ["/fonts/*"],
  });
  // The host page inlines both files into <script> and <style> elements, so
  // neither may contain the sequence that would close its element early.
  for (const [name, forbidden] of [
    ["viewer.js", "</script"],
    ["viewer.css", "</style"],
  ]) {
    if (readFileSync(join(dist, name), "utf8").toLowerCase().includes(forbidden)) {
      console.error(`${name} contains the literal ${forbidden}, which would end the inlining element`);
      process.exit(1);
    }
  }
  // The binary embeds the deflated copies rather than these, because a
  // quarter of the bytes is a quarter of the binary; crates/view/src/lib.rs
  // inflates each once, the first time a page is built. Raw deflate, with no
  // container, is what `miniz_oxide::inflate::decompress_to_vec` reads.
  let total = 0;
  for (const name of ["viewer.js", "viewer.css"]) {
    const path = join(dist, name);
    const raw = statSync(path).size;
    const body = readFileSync(path);
    const gz = gzipSync(body, { level: 9 }).length;
    writeFileSync(`${path}.deflate`, deflateRawSync(body, { level: 9 }));
    total += gz;
    console.log(`${name}: ${raw} bytes, ${gz} bytes gzipped`);
  }
  console.log(`total: ${total} bytes gzipped (budget ${BUDGET_BYTES})`);
  if (total > BUDGET_BYTES) {
    console.error(`bundle exceeds the ${BUDGET_BYTES}-byte gzipped budget by ${total - BUDGET_BYTES} bytes`);
    process.exit(1);
  }
}

async function buildTests() {
  const out = join(root, ".test-build");
  rmSync(out, { recursive: true, force: true });
  const tests = readdirSync(join(root, "test"))
    .filter((f) => f.endsWith(".test.ts"))
    .map((f) => join(root, "test", f));
  await build({
    entryPoints: tests,
    outdir: out,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node22",
    outExtension: { ".js": ".mjs" },
  });
}

if (process.argv.includes("--tests")) await buildTests();
else await buildBundle();
