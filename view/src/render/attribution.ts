// The three figures that say where a scope's input tokens went: the parts
// of every request, the parts ranked by what resending them cost, and the
// split of the input into text sent once and text resent.
//
// Two kinds of number appear together throughout, and each figure says
// which is which. Characters and the provider's input count are
// measurements the log carries. The tokens attributed to one part of a
// request are derived, by dividing that request's measured input in
// proportion to characters, and a request that reported no input count
// yields characters and no tokens at all.

import { fmtInt, h } from "../dom.js";
import type { Child } from "../dom.js";
import { KIND_NAMES, KIND_ORDER, byKind, layoutOrigin, layoutReplayCost, layoutRequestInput } from "../attribution.js";
import type { Attribution, PartKind, PartTotal, RequestInput } from "../attribution.js";
import type { Hovercard } from "./hovercard.js";
import { barSvg, svg } from "./svg.js";

/** What the statistics view lends each figure. */
export interface FigureTools {
  card: Hovercard;
  /** A heading, the drawing, and a caption stating what to see. */
  figure(name: string, body: Child, caption: string): HTMLElement;
  /** Brings the conversation to a log position. */
  reveal(episodeId: string, seq: number): void;
  /** A percentage in the form the statistics view uses everywhere. */
  percent(fraction: number): string;
  /** The word for a quantity no event measured. */
  absent(): HTMLElement;
}

/** Parts beyond this many are summarized in the caption rather than drawn. */
const RANKED_ROWS = 12;

/** A token total, marked as a floor when a request that carried it reported no usage. */
function cost(total: number | null, bounded: boolean): string {
  if (total === null) return "";
  return `${bounded ? "≥ " : ""}${fmtInt(Math.round(total))}`;
}

function stepLabel(request: RequestInput): string {
  const step = request.compaction ? "compaction" : `step ${request.step}`;
  return request.attempt > 1 ? `${step} · attempt ${request.attempt}` : step;
}

/** The kind that accounts for the most of the scope's input, which the figures accent. */
function leadKind(attribution: Attribution): PartKind | null {
  const kinds = byKind(attribution);
  if (kinds.length === 0) return null;
  return kinds.reduce((a, b) => ((a.tokens ?? a.chars) >= (b.tokens ?? b.chars) ? a : b)).kind;
}

function legend(kinds: PartKind[], lead: PartKind | null): HTMLElement {
  return h(
    "span",
    { class: "fig-legend" },
    kinds.map((kind) =>
      h(
        "span",
        { class: "legend-item" },
        h("span", { class: `legend-mark kind-${kind}${kind === lead ? " chosen" : ""}` }),
        KIND_NAMES[kind],
      ),
    ),
  );
}

/**
 * One bar per request, divided by where its input came from. The bar's
 * length is the request's characters against the largest request, because
 * every request has characters and a request whose answer reported no usage
 * would otherwise draw nothing.
 */
export function inputSourceFigure(tools: FigureTools, attribution: Attribution, width: number): HTMLElement {
  if (attribution.requests.length === 0) {
    return tools.figure("where the input came from", h("div", { class: "sub" }, "no model request in this scope"), "");
  }
  const barWidth = Math.max(60, Math.min(420, width - 300));
  const bars = layoutRequestInput(attribution, barWidth);
  const lead = leadKind(attribution);
  const present = KIND_ORDER.filter((kind) => attribution.requests.some((r) => r.shares.some((s) => s.part.kind === kind)));
  const rows = bars.map(({ request, segments }) => {
    const bar = barSvg("input-bar", barWidth, 12, "the parts of this request's input");
    for (const { share, x, w } of segments) {
      const group = svg("g", {
        class: `input-seg kind-${share.part.kind}${share.part.kind === lead ? " lead" : ""}${share.replayed ? " replayed" : ""}`,
      });
      group.appendChild(svg("rect", { class: "seg", x, y: 2, width: Math.max(0.4, w), height: 8 }));
      group.appendChild(svg("line", { class: "edge", x1: x, y1: 1, x2: x, y2: 11 }));
      tools.card.attach(
        group,
        () => share.part.label,
        () =>
          share.tokens === null
            ? "characters measured; no answer reported an input count for this request"
            : `${fmtInt(share.part.chars)} characters ÷ ${fmtInt(request.chars)} × ${fmtInt(request.input ?? 0)} input tokens`,
        () =>
          `${KIND_NAMES[share.part.kind]} · ${share.replayed ? "resent" : "first sent here"}` +
          (share.tokens === null ? "" : ` · ${fmtInt(Math.round(share.tokens))} tokens`),
      );
      bar.appendChild(group);
    }
    const row = h(
      "tr",
      { class: "input-row" },
      h("td", { class: "step-name" }, stepLabel(request)),
      h("td", { class: "num" }, request.input === null ? tools.absent() : fmtInt(request.input)),
      h("td", { class: "num" }, fmtInt(request.chars)),
      h("td", { class: "input-cell" }, bar),
    );
    row.addEventListener("click", () => tools.reveal(request.episodeId, request.requestSeq));
    return row;
  });
  const rates = attribution.requests.map((r) => r.charsPerToken).filter((r): r is number => r !== null);
  const spread =
    rates.length === 0
      ? "No answer in this scope reported an input count, so no part of any request carries a token figure."
      : `Across these requests the measured rate runs from ${Math.min(...rates).toFixed(2)} to ` +
        `${Math.max(...rates).toFixed(2)} characters per input token. A part's share is exact only where the ` +
        "whole request encodes at one rate, which no tokenizer guarantees, so the division between the parts " +
        "of a request is derived while the request's own total is measured.";
  return tools.figure(
    "where the input came from",
    [
      legend(present, lead),
      h(
        "table",
        { class: "stats-table input-table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", null, "request"),
            h("th", null, "input tokens"),
            h("th", null, "characters"),
            h("th", null, ""),
          ),
        ),
        h("tbody", null, rows),
      ),
    ],
    `Each bar is one request, as long as that request is large in characters and divided by where its text ` +
      `came from. The token column is the input the answer reported. ${spread}`,
  );
}

/**
 * The parts by what resending them cost. A tool result is carried by every
 * request after the one that produced it, so its cost over an episode is
 * its size times the requests that carried it, and a middling result on one
 * turn can outweigh a large one that arrived late.
 */
export function replayCostFigure(tools: FigureTools, attribution: Attribution, width: number): HTMLElement {
  if (attribution.parts.length === 0) {
    return tools.figure("replay cost", h("div", { class: "sub" }, "no model request in this scope"), "");
  }
  const measured = attribution.input !== null;
  const barWidth = Math.max(60, Math.min(240, width - 420));
  const ranked = layoutReplayCost(attribution, barWidth, RANKED_ROWS);
  const rows = ranked.map(({ total, w }) => replayCostRow(tools, attribution, total, w, barWidth, measured));
  const head = h(
    "thead",
    null,
    h(
      "tr",
      null,
      h("th", null, "part"),
      h("th", null, "characters"),
      h("th", null, "sends"),
      h("th", null, measured ? "replay cost, tokens" : "characters sent"),
      h("th", null, ""),
    ),
  );
  const hidden = attribution.parts.length - ranked.length;
  const top = ranked[0]!;
  const lead = measured
    ? `${top.total.part.label} cost ${cost(top.total.tokens, top.total.bounded)} input tokens over ${top.total.sends} ` +
      `send${top.total.sends === 1 ? "" : "s"} of ${fmtInt(top.total.part.chars)} characters.`
    : `${top.total.part.label} was sent ${top.total.sends} time${top.total.sends === 1 ? "" : "s"}, ` +
      `${fmtInt(top.total.charCost)} characters in all.`;
  const rest = hidden > 0 ? ` ${hidden} smaller part${hidden === 1 ? "" : "s"} are not drawn.` : "";
  return tools.figure(
    "replay cost",
    h("table", { class: "stats-table" }, head, h("tbody", null, rows)),
    `A part is resent by every request after the one that introduced it, so what it costs an episode is its ` +
      `size times the number of requests that carried it. ${lead}${rest}`,
  );
}

/** One row of the replay-cost table. */
function replayCostRow(
  tools: FigureTools,
  attribution: Attribution,
  total: PartTotal,
  w: number,
  barWidth: number,
  measured: boolean,
): HTMLElement {
  const bar = barSvg("tool-bar", barWidth, 10, "replay cost against the largest");
  bar.appendChild(svg("rect", { class: "seg", x: 0, y: 3, width: Math.max(0.5, w), height: 4 }));
  // A total that spans a request nothing measured is a floor, and a dashed
  // end says that the bar stops where the measurements stop.
  if (total.bounded && measured) {
    bar.appendChild(svg("line", { class: "open", x1: Math.max(0.5, w), y1: 1, x2: Math.max(0.5, w), y2: 9 }));
  }
  const row = h(
    "tr",
    { class: "tool-row input-row" },
    h("td", { class: "tool-name" }, total.part.label),
    h("td", { class: "num" }, fmtInt(total.part.chars)),
    h("td", { class: "num" }, fmtInt(total.sends)),
    h(
      "td",
      { class: "num" },
      measured ? (total.tokens === null ? tools.absent() : cost(total.tokens, total.bounded)) : fmtInt(total.charCost),
    ),
    h("td", null, bar),
  );
  row.addEventListener("click", () => tools.reveal(total.part.episodeId, total.part.seq));
  const share =
    measured && total.tokens !== null && attribution.input
      ? `, which is ${tools.percent(total.tokens / attribution.input)} of the input this scope sent`
      : "";
  tools.card.attach(
    row,
    () => total.part.label,
    () =>
      measured
        ? "the input tokens apportioned to this part, added over every request that carried it"
        : "characters of this part times the requests that carried it",
    () =>
      `${fmtInt(total.part.chars)} characters × ${total.sends} send${total.sends === 1 ? "" : "s"}` +
      (total.bounded ? `, ${total.sends - total.measuredSends} of them unmeasured` : "") +
      (measured && total.tokens !== null ? ` = ${cost(total.tokens, total.bounded)} input tokens${share}` : ""),
  );
  return row;
}

/**
 * The input divided into text this scope sent for the first time and text
 * an earlier request had already sent, with the cache read reported beside
 * the two rather than taken out of either. A cached token is an input token
 * in the provider's accounting, so subtracting it here would present it as
 * a saving in the token metric.
 */
export function inputOriginFigure(tools: FigureTools, attribution: Attribution, width: number): HTMLElement {
  const barWidth = Math.max(80, width - 24);
  const shares = layoutOrigin(attribution, barWidth);
  if (shares === null) {
    return tools.figure(
      "unique against replayed input",
      h("div", { class: "sub" }, [tools.absent(), ": no answer in this scope reported an input count"]),
      "The requests carry text this scope can measure in characters. Dividing an input count that was never " +
        "reported would assert a measurement that was not made.",
    );
  }
  const figure = barSvg("fig-svg", barWidth, 30, "unique against replayed input tokens");
  const largest = shares.reduce((a, b) => (a.tokens >= b.tokens ? a : b));
  for (const share of shares) {
    const group = svg("g", { class: `origin-share ${share.name}${share === largest ? " lead" : ""}` });
    group.appendChild(svg("rect", { class: "seg", x: share.x, y: 6, width: Math.max(0.5, share.w), height: 12 }));
    group.appendChild(svg("line", { class: "edge", x1: share.x, y1: 4, x2: share.x, y2: 20 }));
    if (share.w > 60) {
      const label = svg("text", { class: "share-label", x: share.x + 4, y: 28 });
      label.textContent = `${share.name} ${tools.percent(share.fraction)}`;
      group.appendChild(label);
    }
    tools.card.attach(
      group,
      () => `${share.name} input`,
      () =>
        share.name === "unique"
          ? "input tokens carrying text no earlier request of the episode had sent"
          : "input tokens carrying text an earlier request of the episode had already sent",
      () =>
        `${fmtInt(Math.round(share.tokens))} ÷ ${fmtInt(attribution.input ?? 0)} = ${tools.percent(share.fraction)}`,
    );
    figure.appendChild(group);
  }
  const cache = attribution.cacheRead;
  const beside = h(
    "span",
    { class: "fig-total" },
    cache === null
      ? [fmtInt(attribution.input ?? 0), " input tokens · cache read ", tools.absent()]
      : `${fmtInt(attribution.input ?? 0)} input tokens · ${fmtInt(cache)} of them read from the provider's cache`,
  );
  tools.card.attach(
    beside,
    () => "cache-read tokens",
    () => "reported beside the input rather than inside either share",
    () =>
      cache === null
        ? "no answer in this scope reported a cache-read count"
        : `${fmtInt(cache)} of ${fmtInt(attribution.input ?? 0)} input tokens, which is ${tools.percent(cache / Math.max(1, attribution.input ?? 1))} of the input and still billed as input`,
  );
  const floor = attribution.bounded
    ? ` ${attribution.unmeasured} request${attribution.unmeasured === 1 ? "" : "s"} in this scope reported no input count, so both shares are floors.`
    : "";
  return tools.figure(
    "unique against replayed input",
    [figure, beside],
    `${tools.percent(shares[1]!.fraction)} of the input was the transcript being resent. The size of tool ` +
      `results moves the unique share and the number of turns moves the replayed one, so which of the two ` +
      `dominates decides which is worth attacking.${floor}`,
  );
}
