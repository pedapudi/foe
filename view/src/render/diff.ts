// The diff tab: two episodes that share a fork prefix, prefix shown once
// and the two suffixes side by side.

import { h } from "../dom.js";
import type { Patch, Row } from "../fold.js";
import { ConversationView } from "./conversation.js";
import type { RenderContext } from "./conversation.js";

export interface DiffSide {
  id: string;
  name: string;
  rows: Row[];
}

export class DiffView {
  readonly el: HTMLElement;
  private readonly shared: ConversationView;
  private readonly left: ConversationView;
  private readonly right: ConversationView;

  constructor(
    readonly a: DiffSide,
    readonly b: DiffSide,
    /** Number of leading events the two logs share. */
    readonly sharedLen: number,
    ctx: RenderContext,
  ) {
    const inPrefix = (row: Row) => row.seq < sharedLen;
    const inSuffix = (row: Row) => row.seq >= sharedLen;
    this.shared = new ConversationView(ctx, inPrefix);
    this.left = new ConversationView(ctx, inSuffix);
    this.right = new ConversationView(ctx, inSuffix);
    this.shared.load(a.rows);
    this.left.load(a.rows);
    this.right.load(b.rows);
    this.el = h(
      "div",
      { class: "diff" },
      h("div", { class: "diff-head" }, `${a.name} (${a.id}) and ${b.name} (${b.id}) share ${sharedLen} leading events`),
      h("details", { class: "shared" }, h("summary", null, `shared, seq 0–${sharedLen - 1}`), this.shared.el),
      h(
        "div",
        { class: "columns" },
        h("div", { class: "column" }, h("h2", null, `${a.name} · ${a.id} · seq ${sharedLen} onward`), this.left.el),
        h("div", { class: "column" }, h("h2", null, `${b.name} · ${b.id} · seq ${sharedLen} onward`), this.right.el),
      ),
    );
  }

  /** Routes new rows of either episode to the panes that show them. */
  apply(id: string, patches: Patch[]): void {
    if (id === this.a.id) {
      this.shared.apply(patches);
      this.left.apply(patches);
    }
    if (id === this.b.id) this.right.apply(patches);
  }
}

export function renderNoDiff(reason: string): HTMLElement {
  return h("div", { class: "diff" }, h("div", { class: "empty" }, reason));
}
