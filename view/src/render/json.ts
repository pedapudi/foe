// A JSON value as a structure a reader walks rather than a block of text to
// scan. An object or an array opens and closes and states how much it holds
// while closed; a scalar reads as its own kind; a string too long for its
// line sets behind an expander that holds the whole of it.
//
// Nothing is dropped. Every key is present, in the order the log wrote it,
// and every collapsed node opens to what it holds, down to the literal
// scalar. `src/json.ts` holds the rules; this module builds the elements.

import { h, lazyDetails } from "../dom.js";
import {
  children,
  countLabel,
  isInlineString,
  jsonKind,
  opensByDefault,
  previewLabel,
  stringHead,
} from "../json.js";

/** One value, and every value under it, as elements. */
export function renderJson(value: unknown, depth = 0): HTMLElement {
  switch (jsonKind(value)) {
    case "null":
      return h("span", { class: "j-null" }, "null");
    case "boolean":
      return h("span", { class: "j-bool" }, String(value));
    case "number":
      return h("span", { class: "j-num" }, String(value));
    case "string":
      return renderString(value as string);
    case "array":
    case "object":
      return renderNode(value, depth);
  }
}

/**
 * A string. One short enough to read on its line sets there; a longer one,
 * or one holding a line break, sets its first line and its length as a
 * summary over the whole text. Quotation marks are left off, because the
 * kind is already carried by the colour and the payload's literal text is
 * one control away.
 */
function renderString(text: string): HTMLElement {
  if (text === "") return h("span", { class: "j-empty" }, "(empty)");
  if (isInlineString(text)) return h("span", { class: "j-str" }, text);
  const lines = text.split("\n").length;
  const size = `${text.length} characters${lines > 1 ? ` · ${lines} lines` : ""}`;
  return lazyDetails(
    [h("span", { class: "j-str" }, stringHead(text)), h("span", { class: "meta" }, size)],
    () => h("pre", { class: "text" }, text),
    { class: "j-string" },
  );
}

/** An object or an array: its count and its first key names, over its members. */
function renderNode(value: unknown, depth: number): HTMLElement {
  const kind = jsonKind(value);
  const members = children(value);
  const brackets = kind === "array" ? "[ ]" : "{ }";
  if (members.length === 0) return h("span", { class: "j-empty" }, brackets.replace(" ", ""));
  const preview = previewLabel(value);
  return lazyDetails(
    [
      h("span", { class: "j-bracket" }, brackets),
      h("span", { class: "meta" }, countLabel(value)),
      preview ? h("span", { class: "j-preview" }, preview) : null,
    ],
    () => members.map((child) => renderEntry(child.label, child.value, depth + 1)),
    { class: `j-node ${kind}`, open: opensByDefault(value, depth) },
  );
}

/** One member under the label its parent gives it. */
function renderEntry(label: string, value: unknown, depth: number): HTMLElement {
  return h(
    "div",
    { class: "field" },
    h("div", { class: "field-key" }, label),
    h("div", { class: "field-value" }, renderJson(value, depth)),
  );
}
