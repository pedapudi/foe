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
// caret.

import { clear, fmtInt, h } from "../dom.js";
import { DEPTHS, elapsedLabel, layoutLanes, visibleRows } from "../causality.js";
import type { CausalityOutline, CausalityRow, Depth } from "../causality.js";
import { identityStyle } from "../identity.js";
import { renderJson } from "./json.js";
import { outcomeRole } from "./tree.js";
import { renderMarkdown, renderToolText } from "./markup.js";
import { obj } from "../types.js";
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
 * Draws the outline into `host`, which must already be in the document:
 * the rows are measured after they are laid out and before the lanes are
 * computed from what they measured.
 */
export function drawOutline(
  host: HTMLElement,
  outline: CausalityOutline,
  state: OutlineState,
  card: Hovercard,
  handlers: OutlineHandlers,
  scale: number,
): void {
  const visible = visibleRows(outline, state.depth, state.opened);
  clear(host);
  if (visible.length === 0) {
    host.appendChild(h("div", { class: "empty sub" }, "no episodes"));
    return;
  }

  const board = h("div", { class: "outline" });
  const strokes = h("div", { class: "outline-strokes" });
  const list = h("div", { class: "outline-rows" });
  board.append(strokes, list);
  host.appendChild(board);

  // Pass one: the rows, at whatever height their content takes.
  const elements = visible.map((row) => {
    const el = rowElement(row, outline, state, handlers);
    list.appendChild(el);
    return el;
  });

  // Pass two: the lanes, from the heights the rows measured. A lane whose
  // ends were computed from a fixed pitch would miss the rows it must
  // reach the moment one row held a diff.
  const heights = elements.map((el) => el.offsetHeight / scale);
  const layout = layoutLanes(outline, visible, heights);
  const textLeft = layout.marksWidth + LABEL_GAP;
  board.style.setProperty("--outline-text", `${textLeft * scale}px`);
  strokes.appendChild(laneStrokes(layout, state.selected, card, handlers, scale));
  elements.forEach((el, i) => {
    const row = layout.rows[i];
    if (row) el.style.setProperty("--outline-step", `${(row.kind === "call" || row.kind === "result" ? CALL_INDENT : 0) * scale}px`);
  });
}

/**
 * One row: how long after the run began its event happened in the gutter,
 * its name in the one text column, and, for prose and a result, a body
 * under both that runs the full width.
 */
function rowElement(row: CausalityRow, outline: CausalityOutline, state: OutlineState, handlers: OutlineHandlers): HTMLElement {
  // A caret stands only where opening the row would reveal something. A row
  // whose children the reading already shows has nothing folded under it, and
  // a caret there opens onto what is already on the page.
  const wanted = DEPTHS.indexOf(state.depth);
  const openable = outline.rows.some((r) => r.parent === row.id && DEPTHS.indexOf(r.appearsAt) > wanted);
  const open = state.opened.has(row.id);
  const el = h("div", {
    class: [
      "outline-row",
      // The kind is prefixed: `call` and `result` are already class names
      // the conversation uses for its own blocks, and a bare kind here
      // would take their styling.
      `kind-${row.kind}`,
      row.kind === "outcome" ? outcomeRole(row.outcome ?? null) : "",
      row.id === state.selected ? "selected" : "",
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
  const when = elapsedLabel(row.time - outline.start);
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
    row.kind === "node" && row.firings.length > 1 ? h("span", { class: "aside" }, `${row.firings.length} passes`) : null,
  );
  el.appendChild(name);
  if (row.body !== "" || row.kind === "outcome") el.appendChild(bodyElement(row));
  el.addEventListener("click", () => handlers.scope(row.id === state.selected ? null : row.id));
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
