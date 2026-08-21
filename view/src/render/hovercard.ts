// A hovercard over a figure: a heading, one line of context, and one line
// of detail. Both figures that state their own arithmetic use it, so that
// a number a reader could not derive by eye names the quantity it is and
// the values it came from.

import { clear, h } from "../dom.js";

export class Hovercard {
  readonly el: HTMLElement = h("div", { class: "hovercard", hidden: true });

  /** `host` positions the card; it must be a containing block. */
  constructor(private readonly host: HTMLElement) {}

  /**
   * Opens the card at the pointer. `head` names the quantity, `meta` gives
   * its definition, and `detail` gives the values it was computed from.
   */
  show(event: PointerEvent, head: string, meta: string, detail: string): void {
    clear(this.el);
    this.el.append(
      h("div", { class: "hovercard-head" }, head),
      meta ? h("div", { class: "hovercard-meta" }, meta) : "",
      detail ? h("div", { class: "hovercard-detail" }, detail) : "",
    );
    this.el.hidden = false;
    const box = this.host.getBoundingClientRect();
    const width = this.el.offsetWidth;
    const x = event.clientX - box.left + this.host.scrollLeft;
    const y = event.clientY - box.top + this.host.scrollTop;
    const room = Math.max(4, this.host.clientWidth - width - 6 + this.host.scrollLeft);
    this.el.style.left = `${Math.min(Math.max(4, x + 12), room)}px`;
    this.el.style.top = `${y + 16}px`;
  }

  hide(): void {
    this.el.hidden = true;
  }

  /** Opens the card on hover over `target` and closes it on leaving. */
  attach(target: Element, head: () => string, meta: () => string, detail: () => string): void {
    target.addEventListener("pointerenter", (e) => this.show(e as PointerEvent, head(), meta(), detail()));
    target.addEventListener("pointerleave", () => this.hide());
  }
}
