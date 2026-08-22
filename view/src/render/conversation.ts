// The conversation pane: one element per fold row, patched in place.

import { clear, compact, fmtDuration, fmtInt, fmtTime, h, lazyDetails, lineCount, pretty } from "../dom.js";
import type { Child } from "../dom.js";
import type { AssistantRow, CompactionRow, HeaderRow, NoteRow, Patch, Row, ToolRow, UserRow } from "../fold.js";
import { MARKS } from "../marks.js";
import type { MarkKind } from "../marks.js";
import { promptParts, toolParameters } from "../prompt.js";
import { Hovercard } from "./hovercard.js";
import { markSvg } from "./mark.js";
import { renderMarkdown, renderToolText } from "./markup.js";
import { languageForPath } from "./shape.js";
import { obj, str } from "../types.js";
import type { ContentBlock, ToolSchema } from "../types.js";

export interface RenderContext {
  /** Selects another episode, for rows that name one. */
  select(id: string): void;
}

const INLINE_ARGS_CHARS = 160;

export function renderRow(row: Row, ctx: RenderContext): HTMLElement {
  switch (row.kind) {
    case "header":
      return renderHeader(row);
    case "user":
      return renderUser(row);
    case "assistant":
      return renderAssistant(row);
    case "tool":
      return renderTool(row);
    case "note":
      return renderNote(row, ctx);
    case "compaction":
      return renderCompaction(row);
  }
}

/**
 * The row the conversation pane places at the cut: what the model sees from
 * here on in place of everything above, behind an expander. Rows above it
 * stay visible and carry the `compacted` marker from the stylesheet.
 */
function renderCompaction(row: CompactionRow): HTMLElement {
  const label = `context compacted: ${row.summarized} message${row.summarized === 1 ? "" : "s"} summarized`;
  const body = lazyDetails(
    [h("span", null, label), h("span", { class: "meta" }, `step ${row.step} · kept from seq ${row.firstKeptSeq}`)],
    () => h("pre", { class: "text" }, row.continuation),
    { key: "continuation" },
  );
  return h(
    "div",
    { class: "row compaction", "data-key": row.key },
    gutter(row),
    h("div", { class: "role" }, "system"),
    h("div", { class: "body" }, body),
  );
}

function gutter(row: Row): HTMLElement {
  return h("div", { class: "gutter", title: new Date(row.time).toISOString() }, `${row.seq}`, h("br"), fmtTime(row.time));
}

/**
 * The system prompt and the tool schemas in effect for the requests that
 * follow. The prompt is the best available account of why an agent behaved
 * as it did, so it is rendered the way an assistant message is: as
 * Markdown, under the names its author gave its sections.
 */
function renderHeader(row: HeaderRow): HTMLElement {
  const meta = [row.reason, row.model, `${row.tools.length} tool${row.tools.length === 1 ? "" : "s"}`, `${fmtInt(
    row.system.length,
  )} chars`]
    .filter(Boolean)
    .join(" · ");
  const parts = promptParts(row.system, row.instructions);
  const body = lazyDetails(
    [h("span", null, "system prompt"), h("span", { class: "meta" }, meta)],
    () => [
      // A header other than the first replaced the one before it, so what
      // it replaced comes before the prompt itself.
      row.changed.length
        ? h(
            "div",
            { class: "header-changed" },
            h("div", { class: "header-changed-lab" }, "changed from the header in effect"),
            h("ul", null, row.changed.map((line) => h("li", null, line))),
          )
        : null,
      row.system
        ? h(
            "div",
            { class: "prompt" },
            parts.sections.map((section) => [
              h("div", { class: "prompt-name" }, section.name),
              renderMarkdown(section.text),
            ]),
            parts.appended ? renderMarkdown(parts.appended) : null,
          )
        : h("pre", { class: "text" }, "(empty)"),
      row.tools.length ? h("div", { class: "tools-list" }, row.tools.map(renderToolSchema)) : null,
    ],
    { key: "header" },
  );
  return h("div", { class: "row header", "data-key": row.key }, gutter(row), h("div", { class: "role" }, "system"), h("div", { class: "body" }, body));
}

/**
 * One tool: its name, what the model is told it does, and its parameters as
 * a table. The schema's JSON says the same thing, and says it in a shape
 * that has to be parsed by eye before it can be read.
 */
function renderToolSchema(tool: ToolSchema): HTMLElement {
  const parameters = toolParameters(tool.parameters);
  return h(
    "div",
    { class: "tool-schema" },
    h(
      "div",
      { class: "tool-schema-head" },
      h("code", null, tool.name ?? "?"),
      h("span", { class: "tool-desc" }, firstLine(tool.description ?? "")),
    ),
    parameters.length === 0
      ? h("div", { class: "sub" }, "no parameters")
      : h(
          "table",
          { class: "schema-table" },
          h(
            "thead",
            null,
            h(
              "tr",
              null,
              h("th", null, "parameter"),
              h("th", null, "type"),
              h("th", null, "required"),
              h("th", null, "description"),
            ),
          ),
          h(
            "tbody",
            null,
            parameters.map((p) =>
              h(
                "tr",
                null,
                h("td", { class: "schema-name" }, p.name),
                h("td", { class: "schema-type" }, p.type),
                h("td", { class: "schema-required" }, p.required ? "yes" : ""),
                h("td", null, p.description),
              ),
            ),
          ),
        ),
  );
}

function renderUser(row: UserRow): HTMLElement {
  // Where the message came from leads the row's metadata. Provenance takes
  // one of six words, and a six-glyph alphabet would be learned rather than
  // read, so it stays text beside the sender and the message id.
  const meta: string[] = [row.source];
  if (row.from) meta.push(`from ${row.from}`);
  if (row.messageId) meta.push(row.messageId);
  return h(
    "div",
    { class: "row user", "data-key": row.key },
    gutter(row),
    h("div", { class: "role" }, "user"),
    h(
      "div",
      { class: "body" },
      h("div", { class: "meta" }, meta.join(" · ")),
      row.content.length ? row.content.map((b, i) => renderBlock(b, `block:${i}`)) : h("pre", { class: "text" }, "(no content)"),
    ),
  );
}

function renderBlock(block: ContentBlock, key: string): HTMLElement {
  if (block.type === "text" && typeof block.text === "string") {
    return h("div", { class: "block" }, h("pre", { class: "text" }, block.text));
  }
  return h(
    "div",
    { class: "block" },
    lazyDetails([h("code", null, block.type ?? "block")], () => h("pre", { class: "text" }, pretty(block)), { key }),
  );
}

function renderAssistant(row: AssistantRow): HTMLElement {
  const meta: string[] = [];
  if (row.step) meta.push(`step ${row.step}`);
  if (row.stop) meta.push(`stop ${row.stop}`);
  if (row.usage) {
    const u = row.usage;
    const parts = [`in ${fmtInt(u.input ?? 0)}`, `out ${fmtInt(u.output ?? 0)}`];
    if (u.cache_read) parts.push(`cache ${fmtInt(u.cache_read)}`);
    meta.push(parts.join(" / "));
  }
  const body = h(
    "div",
    { class: `body${row.streaming ? " streaming" : ""}` },
    row.thinking
      ? lazyDetails([h("span", { class: "meta" }, `thinking · ${fmtInt(row.thinking.length)} chars`)], () => h("pre", { class: "text" }, row.thinking), {
          key: "thinking",
        })
      : null,
    // An assistant turn is Markdown once it is complete. While it streams
    // the text is shown as it arrives, because a half-written fence or
    // table would parse as something the model did not mean.
    row.text
      ? row.streaming
        ? h("pre", { class: "text" }, row.text)
        : renderMarkdown(row.text)
      : row.toolCalls.length === 0
        ? h("pre", { class: "text" }, row.streaming ? "" : "(no text)")
        : null,
    row.toolCalls.map((call, i) => {
      const args = typeof call.args === "string" ? call.args : compact(call.args);
      const short = args.length <= INLINE_ARGS_CHARS;
      return h(
        "div",
        { class: "call" },
        h("span", { class: "call-name" }, call.name),
        short
          ? h("code", { class: "args" }, args)
          : lazyDetails([h("span", { class: "meta" }, `${fmtInt(args.length)} chars of arguments`)], () => h("pre", { class: "text" }, typeof call.args === "string" ? call.args : pretty(call.args)), {
              key: `call:${i}`,
            }),
        call.id ? h("span", { class: "meta" }, call.id) : null,
        !call.done ? h("span", { class: "meta" }, "streaming") : null,
      );
    }),
  );
  return h(
    "div",
    { class: `row assistant${row.interrupted ? " interrupted" : ""}`, "data-key": row.key },
    gutter(row),
    h("div", { class: "role" }, "assistant"),
    h(
      "div",
      null,
      h(
        "div",
        { class: "meta-line" },
        // A turn is interrupted whether the response reported truncation
        // or the stream was cut off before a response was assembled; both
        // read the same. `live` says a response is arriving now, so it
        // appears only while the episode is running. The two marks are
        // mirrors: a line that ran and stopped at an open terminal, and an
        // open terminal with the line still to come.
        row.interrupted ? markSvg("interrupted") : null,
        row.streaming ? markSvg("live") : null,
        meta.length ? h("span", { class: "meta" }, meta.join(" · ")) : null,
      ),
      body,
    ),
  );
}

function renderTool(row: ToolRow): HTMLElement {
  const lines = lineCount(row.rendered);
  const meta: string[] = [];
  if (row.callId) meta.push(row.callId);
  meta.push(`${lines} line${lines === 1 ? "" : "s"}`);
  if (row.durationMs) meta.push(fmtDuration(row.durationMs));
  return h(
    "div",
    { class: `row tool${row.isError ? " error" : ""}`, "data-key": row.key },
    gutter(row),
    h("div", { class: "role" }, "tool"),
    h(
      "div",
      { class: "body" },
      h(
        "div",
        null,
        h("span", { class: "tool-name" }, row.name),
        row.isError ? markSvg("error") : null,
        row.synthetic ? markSvg("synthetic") : null,
        // Where the canonical value is stored is a locator a reader reads
        // and copies, so it stays text, and it joins the rest of the row's
        // metadata under the same separator rather than standing apart.
        h(
          "span",
          { class: "meta" },
          row.spill ? [h("span", { title: "canonical value stored under spill/" }, `spill ${row.spill}`), " · "] : null,
          meta.join(" · "),
        ),
      ),
      row.rendered
        ? renderToolText(row.rendered, languageForPath(str(obj(row.value).path)))
        : h("pre", { class: "text" }, "(no rendered text)"),
      lazyDetails([h("span", null, "value")], () => h("pre", { class: "text" }, pretty(row.value)), { key: "value", class: "value" }),
    ),
  );
}

function renderNote(row: NoteRow, ctx: RenderContext): HTMLElement {
  const line: Child[] = [h("span", { class: "detail" }, row.detail)];
  if (row.link) {
    const id = row.link;
    line.push(
      h(
        "button",
        {
          class: "link",
          onclick: (e: Event) => {
            e.preventDefault();
            e.stopPropagation();
            ctx.select(id);
          },
        },
        "open",
      ),
    );
  }
  const body =
    row.data === null || row.data === undefined
      ? h("div", { class: "line" }, line)
      : lazyDetails(line, () => h("pre", { class: "text" }, pretty(row.data)), { key: "data" });
  return h(
    "div",
    { class: `row note level-${row.level}`, "data-key": row.key, "data-type": row.type },
    gutter(row),
    h("div", { class: "role", title: row.type }, row.label),
    h("div", { class: "body" }, body),
  );
}

function firstLine(text: string): string {
  const nl = text.indexOf("\n");
  return nl >= 0 ? text.slice(0, nl) : text;
}

/**
 * Keeps one scrolling list of rows in sync with a fold. Updated rows keep
 * the open state of their expanders; the scroll position follows the end
 * while the reader is at the end and stays put otherwise.
 */
export class ConversationView {
  readonly el: HTMLElement;
  private readonly list: HTMLElement;
  private readonly card: Hovercard;
  private readonly els = new Map<string, HTMLElement>();
  private stick = true;

  constructor(
    private readonly ctx: RenderContext,
    private readonly filter: (row: Row) => boolean = () => true,
  ) {
    this.list = h("div", { class: "conv" });
    this.el = h("div", { class: "conv-scroll" }, this.list);
    this.card = new Hovercard(this.el);
    this.el.appendChild(this.card.el);
    // One listener for the pane rather than one per mark: a patch replaces
    // a row whole, and a listener per mark would be rebound with each of
    // them. `pointerover` bubbles, so leaving a mark for anything else in
    // the pane closes the card without a second listener per mark.
    this.list.addEventListener("pointerover", (event) => {
      const over = (event.target as Element).closest<SVGElement>("[data-mark]");
      const mark = over ? MARKS[over.dataset.mark as MarkKind] : undefined;
      if (mark) this.card.show(event as PointerEvent, mark.label, mark.meaning, "");
      else this.card.hide();
    });
    this.list.addEventListener("pointerleave", () => this.card.hide());
    this.el.addEventListener("scroll", () => {
      this.card.hide();
      this.stick = this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight < 16;
    });
  }

  load(rows: Row[]): void {
    clear(this.list);
    this.els.clear();
    for (const row of rows) this.apply([{ op: "append", row }]);
  }

  apply(patches: Patch[]): void {
    const before = this.el.scrollHeight;
    for (const patch of patches) {
      if (!this.filter(patch.row)) continue;
      const fresh = renderRow(patch.row, this.ctx);
      fresh.dataset.seq = String(patch.row.seq);
      const old = this.els.get(patch.row.key);
      if (old) {
        transferOpenState(old, fresh);
        old.replaceWith(fresh);
      } else {
        this.list.insertBefore(fresh, this.cutBefore(patch.row));
      }
      this.els.set(patch.row.key, fresh);
    }
    if (this.stick) {
      this.el.scrollTop = this.el.scrollHeight;
    } else {
      // An updated row above the viewport changes height; shift by the
      // same amount so the visible rows do not move.
      const delta = this.el.scrollHeight - before;
      if (delta !== 0 && patches.some((p) => p.op === "update")) {
        const first = patches.find((p) => p.op === "update");
        const el = first ? this.els.get(first.row.key) : undefined;
        if (el && el.offsetTop < this.el.scrollTop) this.el.scrollTop += delta;
      }
    }
  }

  /**
   * Brings the row at or after one log position into view and marks it, so
   * that a click in the trajectory pane lands on the events it stands for.
   */
  scrollToSeq(seq: number): void {
    let target: HTMLElement | null = null;
    for (const el of this.list.children) {
      const at = Number((el as HTMLElement).dataset.seq);
      if (Number.isFinite(at) && at >= seq) {
        target = el as HTMLElement;
        break;
      }
    }
    if (!target) target = this.list.lastElementChild as HTMLElement | null;
    if (!target) return;
    this.stick = false;
    this.el.scrollTop = target.offsetTop - this.el.clientHeight / 3;
    for (const el of this.list.querySelectorAll(".row.revealed")) el.classList.remove("revealed");
    target.classList.add("revealed");
  }

  /**
   * Where a new row goes: at the end, except that a compaction row goes
   * before the first row of the kept suffix, so that the rows it replaced
   * sit above it and the rows the model still sees sit below it.
   */
  private cutBefore(row: Row): HTMLElement | null {
    if (row.kind !== "compaction") return null;
    for (const el of this.list.children) {
      if (Number((el as HTMLElement).dataset.seq) >= row.firstKeptSeq) return el as HTMLElement;
    }
    return null;
  }
}

function transferOpenState(from: HTMLElement, to: HTMLElement): void {
  const open = new Set<string>();
  for (const d of from.querySelectorAll<HTMLDetailsElement>("details[data-key]")) {
    if (d.open) open.add(d.dataset.key ?? "");
  }
  if (open.size === 0) return;
  for (const d of to.querySelectorAll<HTMLDetailsElement>("details[data-key]")) {
    if (open.has(d.dataset.key ?? "")) d.open = true;
  }
}
