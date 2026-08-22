// The raw events tab: every event of one episode as a filterable table.

import { fmtTime, h, pretty } from "../dom.js";
import { renderPayload } from "./payload.js";
import type { LogEvent } from "../types.js";

interface RawRow {
  ev: LogEvent;
  tr: HTMLTableRowElement;
  payload: HTMLTableRowElement | null;
  /**
   * The payload's own JSON text. It is what the filter searches, so a
   * query reaches every value the payload holds, including the ones inside
   * a node the reader has not opened. Built on the first filter or expand.
   */
  json: string | null;
}

export class RawView {
  readonly el: HTMLElement;
  readonly input: HTMLInputElement;
  private readonly tbody: HTMLTableSectionElement;
  private readonly count: HTMLElement;
  private readonly rows: RawRow[] = [];
  private query = "";

  constructor() {
    this.input = h("input", {
      type: "search",
      placeholder: "filter by seq, type, or payload text",
      "aria-label": "event filter",
      oninput: () => this.setFilter(this.input.value),
    });
    this.count = h("span", { class: "count" });
    this.tbody = h("tbody");
    this.el = h(
      "div",
      { class: "raw" },
      h("div", { class: "bar" }, this.input, this.count),
      h(
        "table",
        { class: "events" },
        h("thead", null, h("tr", null, h("th", null, "seq"), h("th", null, "time"), h("th", null, "type"))),
        this.tbody,
      ),
    );
    this.updateCount();
  }

  add(events: LogEvent[]): void {
    for (const ev of events) {
      const tr = h(
        "tr",
        { class: "ev" },
        h("td", { class: "seq" }, `${ev.seq}`),
        h("td", { class: "time", title: new Date(ev.time).toISOString() }, fmtTime(ev.time)),
        h("td", { class: `type${ev.type === "sandbox/denied" ? " error" : ""}` }, ev.type),
      );
      const row: RawRow = { ev, tr, payload: null, json: null };
      tr.addEventListener("click", () => this.toggle(row));
      this.tbody.appendChild(tr);
      this.rows.push(row);
      if (this.query) this.applyOne(row);
    }
    this.updateCount();
  }

  focus(): void {
    this.input.focus();
    this.input.select();
  }

  setFilter(query: string): void {
    this.query = query.trim().toLowerCase();
    for (const row of this.rows) this.applyOne(row);
    this.updateCount();
  }

  private json(row: RawRow): string {
    if (row.json === null) row.json = pretty(row.ev.data);
    return row.json;
  }

  private applyOne(row: RawRow): void {
    const q = this.query;
    const show =
      q === "" ||
      String(row.ev.seq) === q ||
      row.ev.type.toLowerCase().includes(q) ||
      this.json(row).toLowerCase().includes(q);
    row.tr.hidden = !show;
    if (row.payload) row.payload.hidden = !show;
  }

  private toggle(row: RawRow): void {
    if (row.payload) {
      row.payload.remove();
      row.payload = null;
      row.tr.classList.remove("open");
      return;
    }
    row.payload = h("tr", { class: "payload" }, h("td", { colspan: 3 }, renderPayload(row.ev)));
    row.tr.after(row.payload);
    row.tr.classList.add("open");
  }

  private updateCount(): void {
    const total = this.rows.length;
    const shown = this.query ? this.rows.filter((r) => !r.tr.hidden).length : total;
    this.count.textContent = this.query ? `${shown} of ${total}` : `${total} events`;
  }
}
