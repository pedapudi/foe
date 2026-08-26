// Elements for the four kinds of rich text the conversation shows:
// Markdown, coloured code, unified diffs, and mathematics.
//
// Every element is built with document.createElement and text is set as
// text nodes, so no string of model output is ever parsed as markup by the
// browser. Markdown is parsed by markdown.ts, code coloured by
// highlight.ts, and diffs read by unified-diff.ts.

import { h } from "../dom.js";
import type { Child } from "../dom.js";
import { isKnownLanguage, tokenize } from "./highlight.js";
import { hasMath, parseMarkdown } from "./markdown.js";
import { renderMath } from "./math.js";
import type { Align, Block, Inline } from "./markdown.js";
import { resultShape } from "./shape.js";
import { parseUnifiedDiff } from "./unified-diff.js";

/** Renders assistant text and any other Markdown the log holds. */
export function renderMarkdown(text: string): HTMLElement {
  const host = h("div", { class: "md" });
  for (const block of parseMarkdown(text)) host.appendChild(renderBlock(block));
  return host;
}

function renderBlock(block: Block): HTMLElement {
  switch (block.kind) {
    case "heading": {
      const level = Math.min(6, Math.max(1, block.level));
      const el = document.createElement(`h${level}`);
      el.className = "md-h";
      appendInline(el, block.children);
      return el;
    }
    case "paragraph": {
      const el = h("p", { class: "md-p" });
      appendInline(el, block.children);
      return el;
    }
    case "code":
      return renderCode(block.text, block.lang);
    case "quote":
      return h("blockquote", { class: "md-quote" }, block.blocks.map(renderBlock));
    case "list": {
      const el = block.ordered
        ? h("ol", { class: "md-list", start: block.start === 1 ? undefined : block.start })
        : h("ul", { class: "md-list" });
      for (const item of block.items) {
        // An item of one paragraph stays inline so that a tight list has no
        // paragraph spacing inside it.
        const only = item.length === 1 && item[0]!.kind === "paragraph" ? item[0] : null;
        const li = h("li");
        if (only && only.kind === "paragraph") appendInline(li, only.children);
        else for (const b of item) li.appendChild(renderBlock(b));
        el.appendChild(li);
      }
      return el;
    }
    case "table":
      return renderTable(block.header, block.align, block.rows);
    case "rule":
      return h("hr", { class: "md-rule" });
    case "math":
      return h("div", { class: "md-math-block" }, renderMath(block.tex, true));
  }
}

function renderTable(header: Inline[][], align: Align[], rows: Inline[][][]): HTMLElement {
  const cell = (tag: "th" | "td", content: Inline[], at: number): HTMLElement => {
    const el = h(tag, { class: align[at] ? `align-${align[at]}` : undefined });
    appendInline(el, content);
    return el;
  };
  const table = h(
    "table",
    { class: "md-table" },
    h("thead", null, h("tr", null, header.map((c, i) => cell("th", c, i)))),
    h("tbody", null, rows.map((row) => h("tr", null, row.map((c, i) => cell("td", c, i))))),
  );
  return h("div", { class: "md-table-scroll" }, table);
}

function appendInline(host: HTMLElement, nodes: Inline[]): void {
  for (const node of nodes) host.appendChild(renderInline(node));
}

function renderInline(node: Inline): Node {
  switch (node.kind) {
    case "text":
      return document.createTextNode(node.text);
    case "emphasis":
      return withChildren(h("em"), node.children);
    case "strong":
      return withChildren(h("strong"), node.children);
    case "strike":
      return withChildren(h("s"), node.children);
    case "code":
      return h("code", { class: "md-code" }, node.text);
    case "link":
      // The page runs on loopback or from a file, and following a link out
      // of it would leave the log behind, so the target is shown rather
      // than followed.
      return withChildren(h("span", { class: "md-link", title: node.href }), node.children);
    case "math":
      return renderMath(node.tex, node.display);
    case "break":
      return h("br");
  }
}

function withChildren(el: HTMLElement, children: Inline[]): HTMLElement {
  appendInline(el, children);
  return el;
}

// ---- mathematics ----

// The renderer and its fallback live in math.ts, which decides what
// converts an expression; this module only places the result.
export { renderMath };

/** True when a text carries a mathematics delimiter, so math is needed. */
export { hasMath };

// ---- code ----

/** A fenced block: coloured text, the language named top right, and a copy control. */
export function renderCode(text: string, lang: string): HTMLElement {
  const code = h("code", { class: "code-text" });
  const language = lang.trim().toLowerCase();
  for (const token of tokenize(text, language)) {
    if (token.role === "plain") code.appendChild(document.createTextNode(token.text));
    else code.appendChild(h("span", { class: `t-${token.role}` }, token.text));
  }
  const tag = language ? h("span", { class: "code-lang" }, language) : null;
  return h(
    "div",
    { class: "code-block", "data-lang": language || undefined },
    h("div", { class: "code-bar" }, tag, copyButton(text)),
    h("pre", { class: "code-pre" }, code),
  );
}

function copyButton(text: string): HTMLElement {
  const button = h("button", { class: "copy", type: "button", title: "copy this block" }, "copy");
  button.addEventListener("click", () => {
    const done = () => {
      button.textContent = "copied";
      window.setTimeout(() => {
        button.textContent = "copy";
      }, 1200);
    };
    const clipboard = navigator.clipboard;
    if (clipboard) void clipboard.writeText(text).then(done, () => {
      button.textContent = "copy failed";
    });
    else button.textContent = "copy unavailable";
  });
  return button;
}

// ---- diffs ----

/** A unified diff with line numbers and tinted added and removed lines. */
export function renderDiff(text: string): HTMLElement | null {
  const diff = parseUnifiedDiff(text);
  if (!diff) return null;
  const body = h("div", { class: "diff-lines" });
  for (const line of diff.lines) {
    const marker = line.kind === "add" ? "+" : line.kind === "remove" ? "-" : " ";
    body.append(
      h("div", { class: `diff-line ${line.kind}` },
        h("span", { class: "diff-num old" }, line.oldNumber === null ? "" : String(line.oldNumber)),
        h("span", { class: "diff-num new" }, line.newNumber === null ? "" : String(line.newNumber)),
        h("span", { class: "diff-mark" }, line.kind === "hunk" || line.kind === "file" || line.kind === "meta" ? "" : marker),
        h("span", { class: "diff-text" }, line.text),
      ),
    );
  }
  const counts = h(
    "div",
    { class: "diff-counts" },
    h("span", { class: "added" }, `+${diff.added}`),
    h("span", { class: "removed" }, `-${diff.removed}`),
  );
  return h(
    "div",
    { class: "diff-block" },
    diff.preamble.filter((l) => l.trim() !== "").length || diff.added || diff.removed
      ? h("div", { class: "diff-bar" }, h("span", { class: "diff-title" }, diff.preamble.join(" ").trim()), counts)
      : null,
    body,
  );
}

// ---- tool results ----

/** Renders a tool result by its shape, with `lang` colouring numbered source. */
export function renderToolText(rendered: string, lang: string): HTMLElement {
  switch (resultShape(rendered)) {
    case "diff": {
      const diff = renderDiff(rendered);
      if (diff) return diff;
      break;
    }
    case "json":
      return renderCode(prettyJson(rendered), "json");
    case "source":
      return renderNumberedSource(rendered, lang);
    case "text":
      break;
  }
  return h("pre", { class: "text" }, rendered);
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2) ?? text;
  } catch {
    return text;
  }
}

/** Source whose lines the tool numbered, as `<number>\t<text>`. */
function renderNumberedSource(rendered: string, lang: string): HTMLElement {
  const body = h("div", { class: "src-lines" });
  const colour = isKnownLanguage(lang);
  const lines = rendered.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  for (const line of lines) {
    const tab = line.indexOf("\t");
    const number = tab > 0 ? line.slice(0, tab).trim() : "";
    const text = tab > 0 ? line.slice(tab + 1) : line;
    const cell: Child[] = colour
      ? tokenize(text, lang).map((t) =>
          t.role === "plain" ? document.createTextNode(t.text) : h("span", { class: `t-${t.role}` }, t.text),
        )
      : [text];
    body.append(
      h("div", { class: "src-line" }, h("span", { class: "src-num" }, number), h("span", { class: "src-text" }, cell)),
    );
  }
  return h("div", { class: "src-block", "data-lang": colour ? lang : undefined }, body);
}
