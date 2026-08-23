// The conversation scoped to one node of the causality figure: that node's
// own messages and those of every node below it, in place of the whole
// run. Selecting a node in the figure is meant to answer "what did this
// do", which a highlight on a row cannot answer and a filtered dialogue
// can.
//
// A workflow node the run entered twice yields one section per pass, each
// labelled, which is the payoff of drawing a loop as an edge rather than
// as a repeated node. Every section is named by the node itself, so the
// role column reads `propose`, `check`, `revise` — never four sections all
// reading "workflow".

import { h } from "../dom.js";
import type { ConversationScope, ScopeSegment } from "../causality.js";
import type { Row } from "../fold.js";
import { renderRow } from "./conversation.js";
import type { RenderContext } from "./conversation.js";

export interface ScopeSource {
  /** Every folded row of one episode, in log order. */
  rows(episodeId: string): Row[];
  /** What an episode is called, for a section that stands for a whole one. */
  name(episodeId: string): string;
}

export function renderScope(
  scope: ConversationScope,
  source: ScopeSource,
  ctx: RenderContext,
  escape: () => void,
): HTMLElement {
  const list = h("div", { class: "conv" });
  let shown = 0;
  for (const segment of scope.segments) {
    const rows = source.rows(segment.episodeId).filter((row) => row.seq >= segment.from && row.seq <= segment.to);
    if (rows.length === 0) continue;
    list.appendChild(sectionHead(segment, source.name(segment.episodeId), rows));
    for (const row of rows) {
      const el = renderRow(row, ctx);
      el.dataset.seq = String(row.seq);
      list.appendChild(el);
      shown += 1;
    }
  }
  if (shown === 0) list.appendChild(h("div", { class: "empty sub" }, "this node produced no messages of its own"));
  return h(
    "div",
    { class: "conv-scroll scoped" },
    h(
      "div",
      { class: "scope-head" },
      h("span", { class: "scope-title" }, scope.title),
      h(
        "button",
        { class: "scope-escape", type: "button", onclick: escape, title: "show the whole run again" },
        "whole run",
      ),
    ),
    list,
  );
}

/**
 * What one section is: the node's own name in the role column, the pass it
 * is when the node was entered more than once, and the episode the rows
 * came from when they came from a node below.
 */
function sectionHead(segment: ScopeSegment, name: string, rows: Row[]): HTMLElement {
  const detail = [segment.pass, `${rows.length} row${rows.length === 1 ? "" : "s"}`, name]
    .filter((part) => part !== "")
    .join(" · ");
  return h(
    "div",
    { class: "row scope-section" },
    h("div", { class: "gutter" }, `${segment.from}–${segment.to}`),
    h("div", { class: "role" }, segment.title),
    h("div", { class: "body" }, h("span", { class: "meta" }, detail)),
  );
}
