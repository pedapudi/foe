// The syntax tokenizer: the five roles, the languages it knows, and the
// invariant that colouring never changes a character.

import assert from "node:assert/strict";
import { test } from "node:test";
import { isKnownLanguage, tokenize } from "../src/render/highlight.js";
import type { Role } from "../src/render/highlight.js";

/** The concatenated text of every token of one role. */
function ofRole(code: string, lang: string, role: Role): string[] {
  return tokenize(code, lang)
    .filter((t) => t.role === role)
    .map((t) => t.text);
}

function roundTrips(code: string, lang: string): void {
  assert.equal(tokenize(code, lang).map((t) => t.text).join(""), code, `${lang} lost or changed text`);
}

const SAMPLES: [string, string][] = [
  ["rust", 'fn main() { /* note */ let n = 0x1F; println!("hi\\"there"); } // end'],
  ["python", 'def f(x):\n    """doc"""\n    return x + 1  # note'],
  ["typescript", 'export const n: number = 12; // note\nconst s = `a ${n} b`;'],
  ["json", '{"a": [1, 2.5, true, null], "b": "text"}'],
  ["shell", "for f in *.rs; do echo \"$f\" # note\ndone"],
  ["go", 'package main\n\nfunc main() {\n\ts := `raw`\n\t_ = s // note\n}'],
  ["c", '#include <stdio.h>\nint main(void) { /* note */ return 0; }'],
  ["toml", '[package]\nname = "foe" # note\nversion = 1'],
  ["yaml", "name: foe # note\nitems:\n  - 1\n  - two"],
  ["markdown", "# Title\n\n- item with `code`\n\n```\nfenced\n```"],
];

test("every known language reproduces its input exactly", () => {
  for (const [lang, code] of SAMPLES) roundTrips(code, lang);
});

test("an unknown language yields one plain token", () => {
  const tokens = tokenize("SELECT 1 FROM t;", "sql");
  assert.deepEqual(tokens, [{ role: "plain", text: "SELECT 1 FROM t;" }]);
  assert.equal(isKnownLanguage("sql"), false);
  assert.equal(isKnownLanguage("Rust"), true);
});

test("empty code yields no tokens", () => {
  assert.deepEqual(tokenize("", "rust"), []);
  assert.deepEqual(tokenize("", "sql"), []);
});

test("rust reads line comments, nested block comments, and raw strings", () => {
  assert.deepEqual(ofRole("let a = 1; // tail", "rust", "comment"), ["// tail"]);
  assert.deepEqual(ofRole("/* outer /* inner */ still */ x", "rust", "comment"), ["/* outer /* inner */ still */"]);
  assert.deepEqual(ofRole('let s = r#"a "quoted" b"#;', "rust", "string"), ['r#"a "quoted" b"#']);
  assert.deepEqual(ofRole("fn f() { let x = 1; }", "rust", "keyword"), ["fn", "let"]);
});

test("a string escape does not end the string", () => {
  assert.deepEqual(ofRole('"a \\" b" c', "rust", "string"), ['"a \\" b"']);
  assert.deepEqual(ofRole('"a \\" b" c', "typescript", "string"), ['"a \\" b"']);
});

test("a shell single-quoted string keeps a backslash literal", () => {
  assert.deepEqual(ofRole("echo 'a\\' b", "shell", "string"), ["'a\\'"]);
});

test("python triple quotes span lines", () => {
  assert.deepEqual(ofRole('x = """one\ntwo"""\n', "python", "string"), ['"""one\ntwo"""']);
});

test("numbers cover integers, floats, and radix prefixes", () => {
  assert.deepEqual(ofRole("a = 10 + 2.5 + 0xFF + 0b1010", "rust", "number"), ["10", "2.5", "0xFF", "0b1010"]);
});

test("json colours its literals as keywords and its keys as strings", () => {
  assert.deepEqual(ofRole('{"on": true, "off": false, "gone": null}', "json", "keyword"), ["true", "false", "null"]);
  assert.deepEqual(ofRole('{"a": 1}', "json", "string"), ['"a"']);
});

test("toml and yaml colour a key that opens its line", () => {
  assert.deepEqual(ofRole('name = "foe"\n', "toml", "keyword"), ["name"]);
  assert.deepEqual(ofRole("name: foe\nnested:\n  inner: 1\n", "yaml", "keyword"), ["name", "nested", "inner"]);
  assert.deepEqual(ofRole("a value with name inside\n", "yaml", "keyword"), []);
});

test("markdown colours headings, fences, and code spans", () => {
  assert.deepEqual(ofRole("# Title\ntext\n", "markdown", "keyword"), ["# Title"]);
  assert.deepEqual(ofRole("a `span` b\n", "markdown", "string"), ["`span`"]);
  // A newline carries no colour, so each line of the fence is its own token.
  assert.deepEqual(ofRole("```\nbody\n```\n", "markdown", "string"), ["```", "body", "```"]);
  assert.deepEqual(ofRole("- item\n", "markdown", "punct"), ["- "]);
});

test("punctuation is a role of its own", () => {
  const punct = ofRole("f(x);", "typescript", "punct").join("");
  assert.equal(punct, "();");
});

test("an unterminated comment or string runs to the end", () => {
  assert.deepEqual(ofRole("x /* never closed", "rust", "comment"), ["/* never closed"]);
  assert.deepEqual(ofRole('x = "never closed', "rust", "string"), ['"never closed']);
  roundTrips("x /* never closed", "rust");
});
