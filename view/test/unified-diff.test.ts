// The unified diff reader and the tool-result shape rule.

import assert from "node:assert/strict";
import { test } from "node:test";
import { looksLikeUnifiedDiff, parseUnifiedDiff } from "../src/render/unified-diff.js";
import { languageForPath, resultShape } from "../src/render/shape.js";

const EDIT_RESULT = [
  "edited src/parser.rs: 2 edit(s), +2 -2 lines",
  "--- a/src/parser.rs",
  "+++ b/src/parser.rs",
  "@@ -10,6 +10,6 @@",
  " fn parse(input: &str) -> Result<Ast> {",
  "     let mut lexer = Lexer::new(input);",
  "-    let tree = lexer.run();",
  "-    Ok(tree)",
  "+    let tree = lexer.run()?;",
  "+    Ok(tree.finish())",
  " }",
  "",
].join("\n");

test("a diff is recognized by its hunk and file markers", () => {
  assert.equal(looksLikeUnifiedDiff(EDIT_RESULT), true);
  assert.equal(looksLikeUnifiedDiff("just some prose\nwith lines"), false);
  assert.equal(parseUnifiedDiff("just some prose"), null);
});

test("the preamble above the first marker is kept apart from the diff", () => {
  const diff = parseUnifiedDiff(EDIT_RESULT)!;
  assert.deepEqual(diff.preamble, ["edited src/parser.rs: 2 edit(s), +2 -2 lines"]);
  assert.equal(diff.added, 2);
  assert.equal(diff.removed, 2);
});

test("line numbers advance from the hunk header on the side each line belongs to", () => {
  const diff = parseUnifiedDiff(EDIT_RESULT)!;
  const body = diff.lines.filter((l) => l.kind !== "file" && l.kind !== "hunk");
  assert.deepEqual(
    body.map((l) => [l.kind, l.oldNumber, l.newNumber]),
    [
      ["context", 10, 10],
      ["context", 11, 11],
      ["remove", 12, null],
      ["remove", 13, null],
      ["add", null, 12],
      ["add", null, 13],
      ["context", 14, 14],
    ],
  );
});

test("the marker is stripped from every line and the text kept", () => {
  const diff = parseUnifiedDiff(EDIT_RESULT)!;
  const added = diff.lines.filter((l) => l.kind === "add").map((l) => l.text);
  assert.deepEqual(added, ["    let tree = lexer.run()?;", "    Ok(tree.finish())"]);
  const context = diff.lines.filter((l) => l.kind === "context").map((l) => l.text);
  assert.equal(context[0], "fn parse(input: &str) -> Result<Ast> {");
});

test("a second hunk restarts numbering from its own header", () => {
  const text = "--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-a\n+A\n@@ -40,2 +40,2 @@\n ctx\n-b\n+B\n";
  const diff = parseUnifiedDiff(text)!;
  const hunks = diff.lines.filter((l) => l.kind === "hunk");
  assert.equal(hunks.length, 2);
  const second = diff.lines.filter((l) => l.kind === "context");
  assert.deepEqual(second.map((l) => l.oldNumber), [40]);
  assert.deepEqual(diff.lines.filter((l) => l.kind === "remove").map((l) => l.oldNumber), [1, 41]);
});

test("a line without a marker is kept as metadata", () => {
  const diff = parseUnifiedDiff("--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-a\n+A\n\\ No newline at end of file\n")!;
  const meta = diff.lines.filter((l) => l.kind === "meta");
  assert.deepEqual(meta.map((l) => l.text), ["\\ No newline at end of file"]);
});

test("a deletion of the whole file has an empty new side", () => {
  const diff = parseUnifiedDiff("--- a/f\n+++ b/f\n@@ -1,1 +0,0 @@\n-only\n")!;
  assert.equal(diff.removed, 1);
  assert.equal(diff.added, 0);
});

test("a result's shape is decided from its text", () => {
  assert.equal(resultShape(EDIT_RESULT), "diff");
  assert.equal(resultShape('{"path": "a", "shown": 4}'), "json");
  assert.equal(resultShape("[1, 2, 3]"), "json");
  assert.equal(resultShape("1\tfirst line\n2\tsecond line\n3\tthird\n"), "source");
  assert.equal(resultShape("ran the command\nexit status 0\n"), "text");
  assert.equal(resultShape(""), "text");
  assert.equal(resultShape("{not json after all"), "text");
});

test("a colouring language is inferred from a path extension", () => {
  assert.equal(languageForPath("crates/log/src/lib.rs"), "rust");
  assert.equal(languageForPath("view/src/app.ts"), "typescript");
  assert.equal(languageForPath("Cargo.toml"), "toml");
  assert.equal(languageForPath("README"), "");
  assert.equal(languageForPath("notes.unknown"), "");
});
