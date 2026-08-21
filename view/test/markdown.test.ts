// The Markdown parser: the block and inline grammar assistant messages use.

import assert from "node:assert/strict";
import { test } from "node:test";
import { hasMath, parseInline, parseMarkdown } from "../src/render/markdown.js";
import type { Block, Inline } from "../src/render/markdown.js";

function text(nodes: Inline[]): string {
  return nodes
    .map((n) => {
      switch (n.kind) {
        case "text":
          return n.text;
        case "code":
          return `\`${n.text}\``;
        case "math":
          return n.display ? `$$${n.tex}$$` : `$${n.tex}$`;
        case "break":
          return "\n";
        case "link":
          return `[${text(n.children)}](${n.href})`;
        default:
          return text(n.children);
      }
    })
    .join("");
}

function kinds(blocks: Block[]): string[] {
  return blocks.map((b) => b.kind);
}

test("headings, paragraphs, and thematic breaks separate", () => {
  const blocks = parseMarkdown("# Title\n\nA paragraph.\n\n---\n\n## Second\n");
  assert.deepEqual(kinds(blocks), ["heading", "paragraph", "rule", "heading"]);
  const first = blocks[0]!;
  assert.equal(first.kind === "heading" && first.level, 1);
  const second = blocks[3]!;
  assert.equal(second.kind === "heading" && second.level, 2);
  assert.equal(second.kind === "heading" ? text(second.children) : "", "Second");
});

test("a heading that opens a block ends the paragraph above it", () => {
  const blocks = parseMarkdown("one line\n# Heading\nmore");
  assert.deepEqual(kinds(blocks), ["paragraph", "heading", "paragraph"]);
});

test("a fenced block keeps its language and its text verbatim", () => {
  const blocks = parseMarkdown("before\n\n```rust\nlet x = 1;\n  // kept\n```\n\nafter\n");
  assert.deepEqual(kinds(blocks), ["paragraph", "code", "paragraph"]);
  const code = blocks[1]!;
  assert.equal(code.kind === "code" && code.lang, "rust");
  assert.equal(code.kind === "code" && code.text, "let x = 1;\n  // kept");
});

test("an unterminated fence runs to the end of the text", () => {
  const blocks = parseMarkdown("```\nno close\n");
  assert.deepEqual(kinds(blocks), ["code"]);
  assert.equal(blocks[0]!.kind === "code" && blocks[0]!.text, "no close");
});

test("markers inside a fenced block stay literal", () => {
  const blocks = parseMarkdown("```\n# not a heading\n- not a list\n```\n");
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0]!.kind === "code" && blocks[0]!.text, "# not a heading\n- not a list");
});

test("a pipe table carries its alignment, header, and rows", () => {
  const source = "| tool | calls |\n|:---|---:|\n| read | 12 |\n| edit | 3 |\n";
  const blocks = parseMarkdown(source);
  assert.deepEqual(kinds(blocks), ["table"]);
  const table = blocks[0]!;
  assert.equal(table.kind, "table");
  if (table.kind !== "table") return;
  assert.deepEqual(table.align, ["left", "right"]);
  assert.deepEqual(table.header.map(text), ["tool", "calls"]);
  assert.deepEqual(table.rows.map((r) => r.map(text)), [["read", "12"], ["edit", "3"]]);
});

test("a line of pipes without an alignment rule stays a paragraph", () => {
  assert.deepEqual(kinds(parseMarkdown("a | b\nc | d")), ["paragraph"]);
});

test("lists nest by indentation and keep their ordering", () => {
  const blocks = parseMarkdown("- one\n- two\n  - inner\n- three\n");
  assert.deepEqual(kinds(blocks), ["list"]);
  const list = blocks[0]!;
  assert.equal(list.kind === "list" && list.ordered, false);
  if (list.kind !== "list") return;
  assert.equal(list.items.length, 3);
  assert.deepEqual(kinds(list.items[1]!), ["paragraph", "list"]);
  const inner = list.items[1]![1]!;
  assert.equal(inner.kind === "list" && inner.items.length, 1);
});

test("an ordered list keeps its first number", () => {
  const blocks = parseMarkdown("3. third\n4. fourth\n");
  const list = blocks[0]!;
  assert.equal(list.kind === "list" && list.ordered, true);
  assert.equal(list.kind === "list" && list.start, 3);
});

test("a block quote parses its contents as blocks", () => {
  const blocks = parseMarkdown("> quoted\n>\n> - item\n");
  assert.deepEqual(kinds(blocks), ["quote"]);
  const quote = blocks[0]!;
  assert.deepEqual(quote.kind === "quote" ? kinds(quote.blocks) : [], ["paragraph", "list"]);
});

test("emphasis, strong emphasis, and code spans nest", () => {
  const nodes = parseInline("plain *em* **strong** `code` ~~gone~~");
  assert.deepEqual(nodes.map((n) => n.kind), ["text", "emphasis", "text", "strong", "text", "code", "text", "strike"]);
  assert.equal(text(nodes), "plain em strong `code` gone");
});

test("a code span keeps its punctuation literal", () => {
  const nodes = parseInline("call `a *b* c` now");
  const code = nodes[1]!;
  assert.equal(code.kind === "code" && code.text, "a *b* c");
});

test("a link keeps its target and never carries a navigable address", () => {
  const nodes = parseInline("see [the log format](docs/log-format.md) first");
  const link = nodes[1]!;
  assert.equal(link.kind, "link");
  if (link.kind !== "link") return;
  assert.equal(link.href, "docs/log-format.md");
  assert.equal(text(link.children), "the log format");
});

test("a backslash escapes a marker", () => {
  const nodes = parseInline("2 \\* 3 \\* 4");
  assert.deepEqual(nodes.map((n) => n.kind), ["text"]);
  assert.equal(text(nodes), "2 * 3 * 4");
});

test("two trailing spaces make a hard break and a bare newline a space", () => {
  assert.deepEqual(parseInline("a  \nb").map((n) => n.kind), ["text", "break", "text"]);
  assert.deepEqual(parseInline("a\nb").map((n) => n.kind), ["text"]);
  assert.equal(text(parseInline("a\nb")), "a b");
});

test("inline mathematics parses in both delimiter forms", () => {
  const dollars = parseInline("cost $x^2$ here");
  assert.equal(dollars[1]!.kind, "math");
  assert.equal(dollars[1]!.kind === "math" && dollars[1]!.tex, "x^2");
  assert.equal(dollars[1]!.kind === "math" && dollars[1]!.display, false);
  const parens = parseInline("cost \\(y_1\\) here");
  assert.equal(parens[1]!.kind === "math" && parens[1]!.tex, "y_1");
});

test("a lone dollar sign beside a space stays literal text", () => {
  assert.deepEqual(parseInline("costs $5 and $6 more").map((n) => n.kind), ["text"]);
  assert.deepEqual(parseInline("PATH=$ HOME").map((n) => n.kind), ["text"]);
});

test("display mathematics alone in a paragraph becomes its own block", () => {
  assert.deepEqual(kinds(parseMarkdown("text\n\n$$\\sum_{i=1}^n i$$\n\nmore")), ["paragraph", "math", "paragraph"]);
  assert.deepEqual(kinds(parseMarkdown("\\[a=b\\]")), ["math"]);
  const block = parseMarkdown("$$a+b$$")[0]!;
  assert.equal(block.kind === "math" && block.tex, "a+b");
});

test("display mathematics inside a sentence stays inline and keeps display mode", () => {
  const nodes = parseInline("so $$a+b$$ follows");
  assert.equal(nodes[1]!.kind === "math" && nodes[1]!.display, true);
});

test("hasMath answers whether Temml is needed at all", () => {
  assert.equal(hasMath("plain prose with no delimiter"), false);
  assert.equal(hasMath("an inline $x+1$ expression"), true);
  assert.equal(hasMath("a display \\[x\\] expression"), true);
  assert.equal(hasMath("a \\(y\\) expression"), true);
});
