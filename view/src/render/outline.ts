// The unified outline: the episode rail, the causality figure and the
// conversation as one scrollable hierarchy read at four depths. Collapsed
// to episodes it is the rail; opened to calls it is the figure; opened
// fully it is the transcript. A caret on any row opens one branch one
// level past the reading, so a reader can sit at `steps` and open a single
// call's result without expanding the run.
//
// The gutter nests and the text does not. Every label starts in one
// column, because structure is already drawn by the lanes and a ragged
// left edge makes a run tedious to skim: the eye reads the indent instead
// of the sequence. The one exception is a tool call and its result body,
// which step in one level under the step that issued them, because a call
// is part of the step above it rather than the next thing that happened.
// Exactly two label columns, therefore. Prose and result bodies break even
// that and run full width, because a diff would otherwise lose the room it
// needs.
//
// Rows are read in the order their events happened, so two episodes that
// ran at once interleave and the gutter's clock runs one way down the page.
// The gutter carries how long after the run began each row's event
// happened. It compares across episodes, which a log position cannot, since
// every episode numbers its own log from zero. What belongs under what is
// carried by the lane the row is a mark on and by the colour of the episode
// it belongs to.
//
// Rows are not one height, so the figure is drawn in two passes: the rows
// are laid out and measured, then the lanes are computed from the heights
// they actually took. Both passes run again on every change of depth or
// caret, and on every event a live run writes. A row keeps the element it
// has across those redraws unless what it draws changed, because the
// element under the reader's pointer must survive a click and the pane
// must not empty while a run works.

import { clear, fmtInt, h } from "../dom.js";
import { elapsedLabel, layoutLanes, rowSignature, visibleRows } from "../causality.js";
import { DEPTHS } from "../causality.js";
import type { CausalityLayout, CausalityOutline, CausalityRow, Depth } from "../causality.js";
import { identityStyle } from "../identity.js";
import { renderJson } from "./json.js";
import { outcomeRole } from "./tree.js";
import { renderMarkdown, renderToolText } from "./markup.js";
import { obj, str } from "../types.js";
import { taskSections } from "../task.js";
import { languageForPath } from "./shape.js";
import { Hovercard } from "./hovercard.js";
import { laneStrokes } from "./causality.js";
import type { CausalityHandlers } from "./causality.js";

/** How far a tool call and its result step in under the step that issued them. */
const CALL_INDENT = 14;

/** Clear ground between the drawing and the one column the labels start in. */
const LABEL_GAP = 12;

export interface OutlineHandlers extends CausalityHandlers {
  /** Opens or shuts one branch one level past the current reading. */
  toggle(rowId: string): void;
}

export interface OutlineState {
  depth: Depth;
  opened: ReadonlySet<string>;
  selected: string | null;
}

/**
 * The outline on the page. It holds its elements between draws and gives a
 * row a new one only when that row draws something else, because a run
 * writes events while a reader is reading and every event redraws the
 * page: emptying the pane sixty times a second makes it flash, drops the
 * reader's place in it, and destroys the button under the pointer between
 * the press and the release, so no caret can be clicked while a run works.
 *
 * The view must be in the document before it is drawn: the rows are laid
 * out and measured, and the lanes are computed from the heights they took.
 */
export class OutlineView {
  readonly el = h("div", { class: "outline" });
  private readonly strokes = h("div", { class: "outline-strokes" });
  private readonly list = h("div", { class: "outline-rows" });
  private readonly nothing = h("div", { class: "empty sub", hidden: true }, "no episodes");
  /** The element each row holds, with what it was drawn from. */
  private readonly built = new Map<string, { el: HTMLElement; signature: string }>();
  /** Read when a row is clicked, so a kept element answers for the reading it is in. */
  private selected: string | null = null;
  /** What the drawing was last built from, so a run that moved nothing keeps it. */
  private geometry = "";

  constructor(private readonly card: Hovercard, private readonly handlers: OutlineHandlers) {
    this.el.append(this.strokes, this.list, this.nothing);
  }

  update(outline: CausalityOutline | null, state: OutlineState, scale: number): void {
    this.selected = state.selected;
    const visible = outline === null ? [] : visibleRows(outline, state.depth, state.opened);
    this.nothing.hidden = visible.length > 0;
    if (outline === null || visible.length === 0) {
      for (const [id, entry] of this.built) {
        entry.el.remove();
        this.built.delete(id);
      }
      return;
    }

    // Pass one: the rows the reading shows, in the order they happened,
    // each keeping its element when it draws what it drew before. The
    // cursor walks the elements already on the page; a row whose element
    // is already there is stepped over, and every other one is moved or
    // inserted in front of the cursor.
    const elements: HTMLElement[] = [];
    const shown = new Set(visible.map((row) => row.id));
    let at: ChildNode | null = this.list.firstChild;
    for (const row of visible) {
      // The cursor steps over the element this row holds before the row is
      // drawn, because a row that is drawn again takes a new element and
      // its old one leaves the page: a cursor left on it would name a node
      // that is no longer a child of the list.
      if (at !== null && this.built.get(row.id)?.el === at) at = at.nextSibling;
      const el = this.element(row, outline.start, state.opened.has(row.id));
      if (el.parentNode !== this.list || el.nextSibling !== at) this.list.insertBefore(el, at);
      el.classList.toggle("selected", row.id === state.selected);
      elements.push(el);
    }
    for (const [id, entry] of this.built) {
      if (shown.has(id)) continue;
      entry.el.remove();
      this.built.delete(id);
    }

    // Pass two: the lanes, from the heights the rows measured. A lane whose
    // ends were computed from a fixed pitch would miss the rows it must
    // reach the moment one row held a diff.
    const heights = elements.map((el) => el.offsetHeight / scale);
    const layout = layoutLanes(outline, visible, heights);
    const textLeft = layout.marksWidth + LABEL_GAP;
    this.el.style.setProperty("--outline-text", `${textLeft * scale}px`);
    elements.forEach((el, i) => {
      const row = layout.rows[i];
      if (!row) return;
      const step = `${(row.kind === "call" || row.kind === "result" ? CALL_INDENT : 0) * scale}px`;
      if (el.style.getPropertyValue("--outline-step") !== step) el.style.setProperty("--outline-step", step);
    });

    // The drawing is one element, so it is replaced whole; a run that added
    // no row and moved none keeps it, and with it any hovercard open over
    // one of its marks.
    const geometry = geometryDigest(layout, state.selected, scale);
    if (geometry === this.geometry) return;
    this.geometry = geometry;
    clear(this.strokes);
    this.strokes.appendChild(laneStrokes(layout, state.selected, this.card, this.handlers, scale));
    // The card was open over a mark this replaced, which can no longer
    // report that the pointer left it.
    this.card.hide();
  }

  /** The element for one row, built only when it draws something new. */
  private element(row: CausalityRow, start: number, open: boolean): HTMLElement {
    const signature = rowSignature(row, start, open);
    const held = this.built.get(row.id);
    if (held !== undefined && held.signature === signature) return held.el;
    if (held !== undefined) held.el.remove();
    const el = rowElement(row, start, open, this.handlers, () => this.selected);
    this.built.set(row.id, { el, signature });
    return el;
  }
}

/**
 * What the drawing is built from: where every lane, curve, vertex and call
 * mark sits, and which row is selected. A redraw that computes the same
 * one draws the same picture.
 */
function geometryDigest(layout: CausalityLayout, selected: string | null, scale: number): string {
  const parts: (string | number)[] = [layout.marksWidth, layout.height, scale, selected ?? ""];
  for (const lane of layout.lanes) {
    parts.push(lane.id, lane.label, lane.x, lane.y1, lane.y2, lane.tone);
    const settled = obj(lane.outcome);
    parts.push(lane.outcome === null ? "" : `${str(settled.kind)} ${str(settled.message)}`);
  }
  for (const edge of layout.edges) {
    parts.push(edge.kind, edge.tone, edge.bow, edge.from.x, edge.from.y, edge.to.x, edge.to.y);
  }
  for (const row of layout.rows) {
    parts.push(row.id, row.label, row.x, row.y, row.tone, row.pulse ? "pulse" : "", row.firings.length);
    for (const call of row.calls) parts.push(call.x, call.y, call.name, call.subject, call.childId ?? "", call.childName, call.failed ? "failed" : "");
  }
  return parts.join(" ");
}

/**
 * One row: how long after the run began its event happened in the gutter,
 * its name in the one text column, and, for prose and a result, a body
 * under both that runs the full width.
 *
 * `selected` is read when the row is clicked rather than when it is built,
 * because the element outlives the reading it was built in.
 */
function rowElement(row: CausalityRow, start: number, open: boolean, handlers: OutlineHandlers, selected: () => string | null): HTMLElement {
  // A caret stands only where opening the row would reveal something. A row
  // whose children the reading already shows has nothing folded under it, and
  // a caret there opens onto what is already on the page.
  const openable = row.openable === true;
  const el = h("div", {
    class: [
      "outline-row",
      // The kind is prefixed: `call` and `result` are already class names
      // the conversation uses for its own blocks, and a bare kind here
      // would take their styling.
      `kind-${row.kind}`,
      row.kind === "outcome" ? outcomeRole(row.outcome ?? null) : "",
      row.failed ? "failed" : "",
    ]
      .filter(Boolean)
      .join(" "),
    "data-row": row.id,
  });
  // Time since the run began, printed once per event: a row that continues
  // the one above it stands for the same instant and leaves the column
  // blank rather than repeating it. The log position the column used to
  // carry rides on the title, because a reader who wants to find the event
  // in the log needs it and a reader following the run does not; two
  // numbers side by side would only have to be told apart.
  const when = elapsedLabel(row.time - start);
  el.appendChild(
    row.showTime === false || when === ""
      ? h("div", { class: "outline-when" })
      : h("div", { class: "outline-when", title: `${when} into the run, at log position ${fmtInt(row.seq)}` }, when),
  );
  const caret = openable
    ? h("button", {
        class: `outline-caret${open ? " open" : ""}`,
        type: "button",
        "aria-expanded": open ? "true" : "false",
        "aria-label": open ? `close ${row.label || row.kind}` : `open ${row.label || row.kind}`,
        onclick: (event: Event) => {
          event.stopPropagation();
          handlers.toggle(row.id);
        },
      })
    : h("span", { class: "outline-caret empty" });
  const name = h(
    "div",
    { class: "outline-name" },
    caret,
    // An episode row names an agent, so its label carries that agent's
    // identity color; every other row names an event and takes plain ink.
    row.label
      ? h(
          "span",
          row.kind === "episode" ? { class: "label identity", style: identityStyle(row.episodeId) } : { class: "label" },
          ...labelParts(row),
        )
      : null,
    row.aside ? h("span", { class: "aside" }, row.aside) : null,
    // What the episode was handed, which the row under this one carries.
    row.handed ? h("span", { class: "handed" }, row.handed) : null,
    row.kind === "node" && row.firings.length > 1 ? h("span", { class: "aside" }, `${row.firings.length} passes`) : null,
  );
  // A row whose kind was named on the line above has no name of its own to
  // draw, and an empty name line would take the room the two lines saved.
  if (row.kind !== "task" || row.label !== "") el.appendChild(name);
  if (row.body !== "" || row.kind === "outcome") el.appendChild(bodyElement(row));
  el.addEventListener("click", () => handlers.scope(row.id === selected() ? null : row.id));
  return el;
}

/**
 * A row's name, with the tool a call named set apart from what it acted on.
 * A call's label opens with the tool's own name, followed by a space or, for
 * a failure the tool wrote, a colon. Splitting there lets a reader see which
 * tool ran before reading the subject it ran on.
 */
function labelParts(row: CausalityRow): (string | HTMLElement)[] {
  const tool = row.calls?.[0]?.name ?? "";
  const rest = row.label.slice(tool.length);
  const divides = rest === "" || rest.startsWith(" ") || rest.startsWith(":");
  if (tool === "" || !row.label.startsWith(tool) || !divides) return [row.label];
  return [h("span", { class: "tool" }, tool), rest];
}

/**
 * What a row sets rather than names. The model's own words are prose and
 * are read as Markdown; a tool's result is output and is set as it came.
 */
function bodyElement(row: CausalityRow): HTMLElement {
  const body = h("div", { class: "outline-body" });
  // What the episode returned is a value, not text: an object of a summary
  // and whatever else the contract asked it to return. The summary sets as
  // prose, because it is the answer and a reader should not have to open
  // anything to read it; the whole value sets beneath it, read the way every
  // other value in the viewer is read.
  if (row.kind === "outcome") {
    const value = returned(row.outcome);
    const summary = obj(value).summary;
    if (typeof summary === "string" && summary !== "") body.appendChild(renderMarkdown(summary));
    body.appendChild(renderJson(value));
  }
  else if (row.kind === "task") taskBody(body, row.body);
  else if (row.kind === "prose") body.appendChild(renderMarkdown(row.body));
  else body.appendChild(renderToolText(row.body, languageForPath(row.label)));
  return body;
}

/**
 * A task, as the sections `src/task.ts` reads it. A section holding what an
 * earlier node returned sets that value's summary as prose and the whole
 * value beneath it, behind the caret an outcome already uses. Setting the
 * JSON as prose instead wraps a single line of braces and quotation marks
 * across the pane.
 */
function taskBody(body: HTMLElement, text: string): void {
  for (const section of taskSections(text)) {
    // The heading names the workflow node whose returned value the section
    // carries, which a reader matches against the graph, so the name is kept
    // as it is. It sets as a caption rather than as a name, because an
    // episode row a few lines up carries the same word as its own name.
    if (section.title !== null) body.appendChild(h("div", { class: "section-of" }, section.title));
    if (section.value === null) {
      body.appendChild(renderMarkdown(section.text));
      continue;
    }
    const summary = obj(section.value).summary;
    if (typeof summary === "string" && summary !== "") body.appendChild(renderMarkdown(summary));
    body.appendChild(renderJson(section.value));
  }
}

/** The part of an outcome a reader came for: what it returned, or why not. */
function returned(outcome: CausalityRow["outcome"]): unknown {
  if (outcome === undefined || outcome === null) return null;
  const fields = outcome as unknown as Record<string, unknown>;
  return fields.value ?? fields.message ?? fields.error ?? fields.limit ?? null;
}

/** The depth control: the four readings, coarsest first. */
export function depthControl(current: Depth, choose: (depth: Depth) => void): HTMLElement {
  const title: Record<Depth, string> = {
    episodes: "episodes alone, which is the rail",
    steps: "the graph nodes and steps each episode ran",
    calls: "the tool calls each step made, with their targets",
    conversation: "what the model said at each step, without the tool output",
    outputs: "what each tool returned, which is most of a run's text",
  };
  return h(
    "span",
    { class: "traj-axis", role: "radiogroup", "aria-label": "how deep the outline reads" },
    DEPTHS.map((depth) =>
      h(
        "button",
        {
          class: `traj-axis-btn${depth === current ? " active" : ""}`,
          type: "button",
          role: "radio",
          "aria-checked": depth === current ? "true" : "false",
          "data-depth": depth,
          title: title[depth],
          onclick: () => choose(depth),
        },
        depth,
      ),
    ),
  );
}
