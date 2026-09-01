// The tree pane: lineage drawn as SVG line art, and details of the selected
// episode below it. Spawn edges are solid and fork edges dashed; the
// selected node carries the one accent in the figure.

import { fmtDate, fmtDuration, fmtInt, h } from "../dom.js";
import type { Child } from "../dom.js";
import { completionProvenance, outcomeLabel, provenanceText } from "../fold.js";
import type { Summary } from "../fold.js";
import { flatten, contractRuns, shortFingerprint, siblingShares, spentTokens } from "../lineage.js";
import type { TreeNode } from "../lineage.js";
import { str } from "../types.js";
import { barSvg, figureSvg, svg } from "./svg.js";

export interface TreeState {
  selected: string | null;
  cursor: string | null;
  compare: string[];
}

export interface TreeHandlers {
  select(id: string): void;
  toggleCompare(id: string): void;
}

const ROW = 40;
const INDENT = 18;
const LEFT = 12;
const DOT_R = 4;
const COMPARE_W = 40;
/** Height of the per-row measure bar. */
const MEASURE_H = 2.5;
/** Advance of one character at the base and the secondary size, in pixels. */
const NAME_CHAR = 7.6;
const SUB_CHAR = 6.2;

/** Colour role for an outcome, earned by direction (docs/design-language.md). */
export function outcomeRole(outcome: Summary["outcome"]): "good" | "bad" | "caution" | "flat" | "" {
  if (!outcome) return "";
  switch (outcome.kind) {
    case "completed":
      return "good";
    case "failed":
      return "bad";
    case "exhausted":
      return "caution";
    case "blocked":
      return "flat";
    default:
      return "";
  }
}

/**
 * The two parts of an outcome: the word itself, and the code, limit, or
 * error that qualifies it. A running episode has the word and no detail.
 */
export function outcomeParts(outcome: Summary["outcome"]): { word: string; detail: string } {
  if (!outcome) return { word: "running", detail: "" };
  const o = outcome as Record<string, unknown>;
  switch (outcome.kind) {
    case "completed":
      return { word: "completed", detail: "" };
    case "blocked":
      return { word: "blocked", detail: str(o.code, "?") };
    case "exhausted":
      return { word: "exhausted", detail: `${str(o.limit, "?")} spent` };
    case "failed":
      return { word: "failed", detail: str(o.error, "?") };
    default:
      return { word: str(outcome.kind, "unknown"), detail: "" };
  }
}

function fit(text: string, px: number, charW: number): string {
  const max = Math.max(3, Math.floor(px / charW));
  return text.length <= max ? text : `${text.slice(0, max - 1)}\u2026`;
}

/** Draws the forest into a figure `width` pixels wide. */
export function renderTree(roots: TreeNode[], width: number, state: TreeState, handlers: TreeHandlers): HTMLElement {
  const host = h("div", { class: "tree", role: "tree" });
  const rows = flatten(roots);
  if (rows.length === 0) {
    host.appendChild(h("div", { class: "empty sub" }, "no episodes"));
    return host;
  }
  const height = rows.length * ROW;
  const shares = siblingShares(roots);
  const figure = figureSvg("tree-figure", width, height, "episodes by lineage");
  const position = new Map<string, { x: number; y: number }>();
  rows.forEach(({ node, depth }, i) => {
    position.set(node.id, { x: LEFT + depth * INDENT, y: i * ROW + ROW / 2 });
  });

  // A bracket down the left edge spans the rows of one contract. Two roots
  // of one contract carry one `episode/start.contract_fingerprint`; without the bracket
  // the list shows them only as two rows that happen to read alike.
  const runs = contractRuns(rows.map(({ node, depth }) => ({ ...node.summary, depth })));
  for (const run of runs) {
    const y1 = run.first * ROW + 4;
    const y2 = (run.last + 1) * ROW - 4;
    const bracket = svg("path", { class: "contract-run", d: `M 6 ${y1} H 3 V ${y2} H 6` });
    const runTitle = svg("title");
    runTitle.textContent = `${run.runs} runs of ${run.name} · one contract fingerprint ${shortFingerprint(run.contractFingerprint)}`;
    bracket.appendChild(runTitle);
    figure.appendChild(bracket);
  }

  // Edges first so that nodes paint over them.
  for (const { node } of rows) {
    const s = node.summary;
    const parentId = s.parentId ?? s.forkOrigin?.episodeId ?? null;
    const from = parentId ? position.get(parentId) : undefined;
    const to = position.get(node.id);
    if (!from || !to) continue;
    figure.appendChild(
      svg("path", {
        class: `edge${node.fork ? " fork" : ""}`,
        d: `M ${from.x} ${from.y + DOT_R + 3} V ${to.y} H ${to.x - DOT_R - 2}`,
      }),
    );
  }

  for (const { node } of rows) {
    const s = node.summary;
    const { x, y } = position.get(node.id)!;
    const role = outcomeRole(s.outcome);
    const classes = ["node"];
    if (state.selected === node.id) classes.push("selected");
    if (state.cursor === node.id) classes.push("cursor");
    const g = svg("g", {
      class: classes.join(" "),
      role: "treeitem",
      "data-id": node.id,
      "aria-selected": state.selected === node.id ? "true" : "false",
    });
    const hit = svg("rect", { class: "hit", x: 1, y: y - ROW / 2 + 2, width: width - 2, height: ROW - 4, rx: 3 });
    // The row's one emphasis: a spine down its leading edge, which the
    // stylesheet colours on the selected row and leaves clear on the rest.
    const spine = svg("line", { class: "spine", x1: 1, y1: y - ROW / 2 + 3, x2: 1, y2: y + ROW / 2 - 3 });
    const title = svg("title");
    title.textContent = node.fork
      ? `fork of ${s.forkOrigin?.episodeId} at seq ${s.forkOrigin?.seq}`
      : s.parentId
        ? `spawned by ${s.parentId}`
        : "root episode";
    g.append(title, hit, spine);
    g.appendChild(svg("circle", { class: `dot ${role}`, cx: x, cy: y, r: DOT_R }));

    const textX = x + DOT_R + 9;
    const textW = Math.max(24, width - textX - COMPARE_W - 6);
    // First line: the contract name, then the episode id when it fits.
    const name = svg("text", { class: "name", x: textX, y: y - 4 });
    const nameText = fit(s.name, textW * 0.66, NAME_CHAR);
    const nameSpan = svg("tspan");
    nameSpan.textContent = nameText;
    name.appendChild(nameSpan);
    const idRoom = textW - nameText.length * NAME_CHAR - 10;
    if (s.id !== s.name && idRoom >= 6 * SUB_CHAR) {
      const idSpan = svg("tspan", { class: "id", dx: 8 });
      idSpan.textContent = fit(s.id, idRoom, SUB_CHAR);
      name.appendChild(idSpan);
    }
    g.appendChild(name);

    // Second line: the outcome word, then what qualifies it. A completed
    // outcome is qualified by how completion was established, in the same
    // faint text the other outcomes set their detail in.
    const parts = outcomeParts(s.outcome);
    if (s.outcome?.kind === "completed") {
      const provenance = completionProvenance(s);
      if (provenance) parts.detail = provenanceText(provenance);
    }
    const sub = svg("text", { class: "sub", x: textX, y: y + 12 });
    const word = svg("tspan", { class: role || "running" });
    word.textContent = parts.word;
    sub.appendChild(word);
    if (parts.detail) {
      const room = textW - parts.word.length * SUB_CHAR - 10;
      const detail = svg("tspan", { class: "detail", dx: 7 });
      detail.textContent = fit(parts.detail, room, SUB_CHAR);
      sub.appendChild(detail);
    }
    g.appendChild(sub);

    // Third line: how much of the sibling group's spending this episode
    // did. The tree names episodes and outcomes; the question in front of
    // a tree is where the work went, and this answers it without a click.
    const share = shares.get(node.id);
    if (share !== undefined) {
      const barW = Math.max(12, textW);
      const measure = svg("g", { class: "measure" });
      const measureTitle = svg("title");
      const group = "among the episodes with the same parent";
      measureTitle.textContent =
        share >= 1
          ? `${fmtInt(spentTokens(s))} tokens, the largest total ${group}`
          : `${fmtInt(spentTokens(s))} tokens, ${Math.round(share * 100)} percent of the largest total ${group}`;
      measure.append(
        measureTitle,
        svg("rect", { class: "measure-track", x: textX, y: y + 16, width: barW, height: MEASURE_H, rx: 1 }),
        svg("rect", { class: "measure-fill", x: textX, y: y + 16, width: Math.max(1, share * barW), height: MEASURE_H, rx: 1 }),
      );
      g.appendChild(measure);
    }

    const comparing = state.compare.includes(node.id);
    const boxX = width - COMPARE_W;
    const box = svg("rect", { class: `compare-box${comparing ? " on" : ""}`, x: boxX, y: y - 5, width: 10, height: 10, rx: 2 });
    const boxTitle = svg("title");
    boxTitle.textContent = comparing ? "remove from comparison" : "mark for comparison";
    box.appendChild(boxTitle);
    const label = svg("text", { class: "compare-label", x: boxX + 14, y: y + 4 });
    label.textContent = comparing ? "cmp" : "";
    const toggle = (e: Event) => {
      e.stopPropagation();
      handlers.toggleCompare(node.id);
    };
    box.addEventListener("click", toggle);
    label.addEventListener("click", toggle);
    g.append(box, label);
    g.addEventListener("click", () => handlers.select(node.id));
    figure.appendChild(g);
  }
  host.appendChild(figure);
  return host;
}

/**
 * A value made of several parts, separated by a middle dot. Each part is
 * one unbreakable run, so a narrow pane wraps between two parts and never
 * inside one: `cache read 2,560` stays on one line, and `best-effort` is
 * never split at its hyphen. An empty part is left out.
 */
function parts(list: string[]): Child {
  const kept = list.filter((p) => p !== "");
  // The separator leads its part rather than trailing the one before it, so
  // that a line never ends on a dangling dot.
  return kept.flatMap((part, i) => [i > 0 ? " " : null, h("span", { class: "part" }, i > 0 ? `· ${part}` : part)]);
}

export function renderInfo(s: Summary | null): HTMLElement {
  if (!s) return h("div", { class: "episode-info" }, h("span", { class: "sub" }, "select an episode"));
  const rows: [string, Child][] = [];
  const role = outcomeRole(s.outcome);
  const provenance = completionProvenance(s);
  rows.push([
    "outcome",
    [
      h("span", { class: `outcome ${role}` }, outcomeLabel(s.outcome)),
      provenance ? h("span", { class: "sub" }, ` · ${provenanceText(provenance)}`) : null,
    ],
  ]);
  // Consumption rows appear once something was consumed; before the first
  // request there is nothing observed to report.
  if (s.modelCalls > 0) {
    rows.push([
      "model calls",
      [ratio(s.modelCalls, s.budget.modelCalls, "calls"), s.retries ? h("div", { class: "sub" }, `${s.retries} retr${s.retries === 1 ? "y" : "ies"} included`) : null],
    ]);
  }
  if (s.usage.input > 0) {
    rows.push([
      "input tokens",
      [
        ratio(s.usage.input, s.budget.inputTokens, "tokens"),
        h("div", { class: "sub" }, `cache read ${fmtInt(s.usage.cacheRead)}`),
      ],
    ]);
  }
  if (s.usage.output > 0) {
    rows.push(["output tokens", ratio(s.usage.output, s.budget.outputTokens, "tokens")]);
  }
  const abi = s.sandbox.landlockAbi;
  if (abi !== null || s.sandbox.mode) {
    const landlock = abi === null ? "" : abi === 0 ? "landlock unavailable" : `landlock abi ${abi}`;
    rows.push(["sandbox", parts([landlock, s.sandbox.mode])]);
  }
  // The fingerprint says whether two episodes ran one contract, so the
  // details name it. The whole hash is long; the row sets its first eight
  // characters and carries the rest in its tooltip.
  if (s.contractFingerprint) {
    rows.push(["contract fingerprint", h("span", { title: s.contractFingerprint }, shortFingerprint(s.contractFingerprint))]);
  }
  if (s.forkOrigin) rows.push(["fork origin", `${s.forkOrigin.episodeId} at seq ${s.forkOrigin.seq}`]);
  if (s.parentId) rows.push(["parent", s.parentId]);
  if (s.teamId) rows.push(["team", s.teamId]);
  if (s.seedEnd !== null) rows.push(["seed end", `seq ${s.seedEnd}`]);
  if (s.children.size) rows.push(["children", `${s.children.size}`]);
  if (s.roster.size) {
    rows.push(["roster", [...s.roster.entries()].map(([id, m]) => `${m.name || id} (${m.phase})`).join(", ")]);
  }
  rows.push(["events", `${s.lastSeq + 1}`]);
  if (s.startTime) rows.push(["started", fmtDate(s.startTime)]);
  if (s.endTime !== null && s.startTime) rows.push(["duration", fmtDuration(s.endTime - s.startTime)]);
  return h(
    "div",
    { class: "episode-info" },
    h("dl", null, rows.map(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
    s.task ? h("div", { class: "task", title: s.task }, s.task) : null,
  );
}

/** A count with its limit, and a hairline mark of the fraction spent. */
function ratio(used: number, limit: number | null, unit: string): Child {
  if (limit === null || limit <= 0) return `${fmtInt(used)} ${unit}`;
  const fraction = Math.min(1, used / limit);
  const pct = Math.round(fraction * 100);
  const w = 72;
  const mark = barSvg("mark", w, 8, "share of the limit spent");
  mark.appendChild(svg("line", { class: "track", x1: 0, y1: 4, x2: w, y2: 4 }));
  mark.appendChild(svg("line", { class: `fill${fraction >= 1 ? " caution" : ""}`, x1: 0, y1: 4, x2: Math.max(0.5, fraction * w), y2: 4 }));
  mark.appendChild(svg("line", { class: "tick", x1: fraction * w, y1: 0, x2: fraction * w, y2: 8 }));
  const t = svg("title");
  t.textContent = `${pct}% of ${fmtInt(limit)} ${unit}`;
  mark.appendChild(t);
  return [`${fmtInt(used)} / ${fmtInt(limit)} ${unit} (${pct}%)`, mark];
}
