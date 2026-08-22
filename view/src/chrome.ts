// The top bar and the four persisted appearance settings: colour theme,
// typeface, text size, and page scale (docs/design-language.md, "Chrome").
// One function applies each setting, and every control that changes a
// value goes through it, so the controls never disagree with the page.

import {
  DEFAULT_FONTSIZE,
  DEFAULT_THEME_DARK,
  DEFAULT_THEME_LIGHT,
  DEFAULT_TYPEFACE,
  FONT_SIZES,
  SCALE_DEFAULT,
  SCALE_MAX,
  SCALE_MIN,
  SCALE_STEP,
  THEMES,
  TYPEFACES,
  leadFamily,
  normaliseScale,
} from "./appearance.js";
import type { TypefaceMode } from "./appearance.js";
import { brandLockup, researchPreview } from "./brand.js";
import { clear, h } from "./dom.js";

export { SCALE_DEFAULT, SCALE_MAX, SCALE_MIN, SCALE_STEP, THEMES, TYPEFACES, normaliseScale };

const KEY_THEME = "foe.theme";
const KEY_TYPEFACE = "foe.typeface";
const KEY_FONTSIZE = "foe.fontsize";
const KEY_SCALE = "foe.scale";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable; the setting then lasts for the page only.
  }
}

type Listener = () => void;
const listeners = new Set<Listener>();

/** Registers a callback run after any setting changes, so pickers can resync. */
export function onSettingsChange(fn: Listener): void {
  listeners.add(fn);
}

function notify(): void {
  for (const fn of listeners) fn();
}

export function currentTheme(): string {
  return document.documentElement.dataset.theme ?? DEFAULT_THEME_DARK;
}

export function currentTypeface(): string {
  return document.documentElement.dataset.typeface ?? DEFAULT_TYPEFACE;
}

export function currentFontSize(): string {
  return document.documentElement.dataset.fontsize ?? DEFAULT_FONTSIZE;
}

/**
 * The multiplier the chosen text size applies to the base size. A figure
 * laid out in pixels reads it, because a figure's rows and lanes hold text
 * and must grow with it; the stylesheet reads the same number from
 * `--dt-font-scale`.
 */
export function currentFontScale(): number {
  return (FONT_SIZES.find((o) => o.id === currentFontSize()) ?? FONT_SIZES[0]!).scale;
}

let scaleRoot: HTMLElement | null = null;
let currentScaleValue = SCALE_DEFAULT;

export function currentScale(): number {
  return currentScaleValue;
}

export function applyTheme(id: string): void {
  const known = THEMES.some((t) => t.id === id) ? id : DEFAULT_THEME_DARK;
  document.documentElement.dataset.theme = known;
  write(KEY_THEME, known);
  notify();
}

export function applyTypeface(id: string): void {
  const known = TYPEFACES.some((t) => t.id === id) ? id : DEFAULT_TYPEFACE;
  document.documentElement.dataset.typeface = known;
  write(KEY_TYPEFACE, known);
  notify();
}

export function applyFontSize(id: string): void {
  const option = FONT_SIZES.find((o) => o.id === id) ?? FONT_SIZES[0]!;
  document.documentElement.dataset.fontsize = option.id;
  document.documentElement.style.setProperty("--dt-font-scale", String(option.scale));
  write(KEY_FONTSIZE, option.id);
  notify();
}

/** Page scale in percent, applied as `zoom` on the app root so the page reflows. */
export function applyScale(value: unknown): void {
  const n = normaliseScale(value);
  currentScaleValue = n;
  if (scaleRoot) scaleRoot.style.zoom = n === 100 ? "" : String(n / 100);
  write(KEY_SCALE, String(n));
  notify();
}

/**
 * Applies the stored settings, or the defaults, at boot. A theme the host
 * page already stamped on the root element wins over the stored one. Without
 * either, `prefers-color-scheme` chooses google-dark on a machine asking for
 * a dark ground and google-light on one asking for a light ground.
 */
export function loadSettings(root: HTMLElement): void {
  scaleRoot = root;
  const stamped = document.documentElement.dataset.theme;
  const stored = read(KEY_THEME);
  if (stamped && THEMES.some((t) => t.id === stamped)) {
    applyTheme(stamped);
  } else if (stored) {
    applyTheme(stored);
  } else {
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    applyTheme(dark.matches ? DEFAULT_THEME_DARK : DEFAULT_THEME_LIGHT);
  }
  applyTypeface(read(KEY_TYPEFACE) ?? DEFAULT_TYPEFACE);
  applyFontSize(read(KEY_FONTSIZE) ?? DEFAULT_FONTSIZE);
  applyScale(read(KEY_SCALE) ?? SCALE_DEFAULT);
}

// ---- pickers ----

let openPopover: HTMLElement | null = null;

function closePopovers(): void {
  if (openPopover) openPopover.classList.remove("open");
  openPopover = null;
}

document.addEventListener("click", (e) => {
  if (openPopover && !openPopover.contains(e.target as Node)) closePopovers();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openPopover) {
    const trigger = openPopover.querySelector<HTMLElement>(".trigger");
    closePopovers();
    trigger?.focus();
  }
});

function togglePopover(wrap: HTMLElement): void {
  const willOpen = !wrap.classList.contains("open");
  closePopovers();
  if (willOpen) {
    wrap.classList.add("open");
    openPopover = wrap;
    wrap.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
  }
}

/** Arrow keys move focus among the options of an open listbox. */
function listKeys(list: HTMLElement): void {
  list.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const options = [...list.querySelectorAll<HTMLElement>('[role="option"]')];
    const at = options.indexOf(document.activeElement as HTMLElement);
    const next = options[Math.min(options.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)))];
    next?.focus();
    e.preventDefault();
  });
}

function swatchStrip(preview: string[], small = false): HTMLElement {
  return h(
    "span",
    { class: `swatch-strip${small ? " sm" : ""}`, "aria-hidden": "true" },
    preview.map((c) => h("span", { class: "swatch", style: `background:${c}` })),
  );
}

export function buildSwatchDropdown(): HTMLElement {
  const name = h("span", { class: "cd-name" });
  const stripHost = h("span");
  const trigger = h("button", { class: "trigger", type: "button", "aria-haspopup": "listbox", "aria-expanded": "false", title: "colour theme" }, stripHost, name, h("span", { class: "caret" }, "▾"));
  const list = h("div", { class: "cd-list", role: "listbox", "aria-label": "colour theme" });
  const wrap = h("div", { class: "cd" }, trigger, list);
  const sync = () => {
    const current = currentTheme();
    const theme = THEMES.find((t) => t.id === current) ?? THEMES[0]!;
    name.textContent = theme.label;
    clear(stripHost);
    stripHost.appendChild(swatchStrip(theme.preview, true));
    trigger.setAttribute("aria-expanded", wrap.classList.contains("open") ? "true" : "false");
    for (const opt of list.querySelectorAll<HTMLElement>('[role="option"]')) {
      opt.setAttribute("aria-selected", opt.dataset.id === current ? "true" : "false");
    }
  };
  for (const theme of THEMES) {
    list.appendChild(
      h(
        "button",
        {
          class: "cd-option",
          type: "button",
          role: "option",
          "data-id": theme.id,
          tabindex: -1,
          onclick: () => {
            applyTheme(theme.id);
            closePopovers();
            trigger.focus();
          },
        },
        swatchStrip(theme.preview),
        h("span", { class: "cd-name" }, theme.label),
      ),
    );
  }
  listKeys(list);
  trigger.addEventListener("click", () => {
    togglePopover(wrap);
    sync();
  });
  onSettingsChange(sync);
  sync();
  return wrap;
}

/** The face's own name, set in that face. */
function faceName(face: (typeof TYPEFACES)[number], className: string): HTMLElement {
  return h("span", { class: className, style: `font-family:${face.head}` }, face.label);
}

/**
 * Two letters in the option's body face beside two digits in its data face.
 * The option's name is set in its heading face, so the name and this
 * specimen together show all three faces an option resolves, in four
 * glyphs. The families are set inline, so the specimen shows the option it
 * names whatever the page is currently set in, and the trigger and every
 * row of the popover carry the same one.
 */
function microSpecimen(face: (typeof TYPEFACES)[number]): HTMLElement {
  return h(
    "span",
    { class: "tf-micro", "aria-hidden": "true" },
    h("span", { class: "tf-micro-body", style: `font-family:${face.sans}` }, "Aa"),
    h("span", { class: "tf-micro-data", style: `font-family:${face.mono}` }, "01"),
  );
}

export function buildTypefacePopover(): HTMLElement {
  const nameHost = h("span", { class: "tf-current" });
  const trigger = h("button", { class: "trigger tf-trigger", type: "button", "aria-haspopup": "listbox", "aria-expanded": "false", title: "typeface" }, nameHost, h("span", { class: "caret" }, "▾"));
  const list = h("div", { class: "cd-list tf-list", role: "listbox", "aria-label": "typeface" });
  const modes: TypefaceMode[] = ["technical", "editorial", "display"];
  for (const mode of modes) {
    list.appendChild(h("div", { class: "cd-group", role: "presentation" }, mode));
    for (const face of TYPEFACES.filter((f) => f.mode === mode)) {
      list.appendChild(
        h(
          "button",
          {
            class: "tf-option",
            type: "button",
            role: "option",
            "data-id": face.id,
            tabindex: -1,
            onclick: () => {
              applyTypeface(face.id);
              closePopovers();
              trigger.focus();
            },
          },
          microSpecimen(face),
          faceName(face, "tf-name"),
        ),
      );
    }
  }
  const sizes = h(
    "span",
    { class: "sizeseg", role: "radiogroup", "aria-label": "text size" },
    FONT_SIZES.map((o) =>
      h(
        "button",
        {
          class: "sizeseg-btn",
          type: "button",
          role: "radio",
          "data-id": o.id,
          title: `${o.id} text`,
          // Each control is set at the size it selects.
          style: `font-size:${(11 * o.scale).toFixed(1)}px`,
          onclick: () => applyFontSize(o.id),
        },
        o.label,
      ),
    ),
  );
  const foot = h("div", { class: "tf-foot" }, h("span", { class: "tf-foot-lab" }, "text size"), sizes);
  const pop = h("div", { class: "tf-pop" }, list, foot);
  const wrap = h("div", { class: "cd tf" }, trigger, pop);
  const sync = () => {
    const current = currentTypeface();
    const face = TYPEFACES.find((f) => f.id === current) ?? TYPEFACES[0]!;
    clear(nameHost);
    nameHost.append(microSpecimen(face), h("span", { class: "cd-name tf-lead" }, leadFamily(face.label)));
    trigger.title = `typeface: ${face.label}`;
    trigger.setAttribute("aria-label", `typeface: ${face.label}`);
    trigger.setAttribute("aria-expanded", wrap.classList.contains("open") ? "true" : "false");
    for (const opt of list.querySelectorAll<HTMLElement>('[role="option"]')) {
      opt.setAttribute("aria-selected", opt.dataset.id === current ? "true" : "false");
    }
    const size = currentFontSize();
    for (const b of sizes.querySelectorAll<HTMLElement>("[role=radio]")) {
      b.setAttribute("aria-checked", b.dataset.id === size ? "true" : "false");
    }
  };
  listKeys(list);
  trigger.addEventListener("click", () => {
    togglePopover(wrap);
    sync();
  });
  onSettingsChange(sync);
  sync();
  return wrap;
}

export function buildScalePill(): HTMLElement {
  const range = h("input", {
    class: "scale-range",
    type: "range",
    min: SCALE_MIN,
    max: SCALE_MAX,
    step: SCALE_STEP,
    "aria-label": "page scale",
    oninput: () => applyScale(range.value),
  });
  const readout = h("span", { class: "scale-val" });
  const reset = h("button", { class: "scale-reset", type: "button", title: "reset page scale to 100%", onclick: () => applyScale(SCALE_DEFAULT) }, "⟲");
  const sync = () => {
    range.value = String(currentScale());
    readout.textContent = `${currentScale()}%`;
  };
  onSettingsChange(sync);
  sync();
  return h("span", { class: "scale-pill" }, range, readout, reset);
}

// ---- the status pill ----

export type ConnectionState = "file" | "connected" | "reconnecting" | "ended" | "unavailable";

export class StatusPill {
  readonly el: HTMLElement;
  private readonly dot = h("span", { class: "status-dot" });
  private readonly word = h("span", { class: "status-text" });
  private readonly runLabel = h("span", { class: "run-label" });
  private readonly runCount = h("span", { class: "run-count" });
  private readonly run: HTMLElement;

  constructor() {
    this.run = h("span", { class: "run-badge", "aria-live": "polite", hidden: true }, h("span", { class: "run-pulse", "aria-hidden": "true" }), this.runLabel, this.runCount);
    this.el = h("span", { class: "status" }, this.dot, this.word, this.run);
  }

  set(state: ConnectionState, detail: string, running: number): void {
    this.el.className = `status ${state}`;
    this.word.textContent = detail || state;
    this.word.title = detail;
    if (running > 0) {
      this.run.hidden = false;
      this.runLabel.textContent = "running";
      this.runCount.textContent = `${running} in flight`;
    } else {
      this.run.hidden = true;
    }
  }
}

// ---- the top bar ----

export interface Crumb {
  id: string;
  label: string;
}

export class Topbar {
  readonly el: HTMLElement;
  readonly status = new StatusPill();
  private readonly up: HTMLButtonElement;
  private readonly crumbs = h("nav", { class: "crumbs", "aria-label": "lineage" });
  private crumbDigest = "";

  constructor(handlers: { up(): void; select(id: string): void }) {
    this.up = h("button", { class: "up off", type: "button", title: "one level up the episode tree", onclick: () => handlers.up() }, h("span", { class: "up-glyph" }, "↑"), "up");
    this.select = handlers.select;
    this.el = h(
      "header",
      { class: "topbar" },
      this.up,
      h("span", { class: "brand" }, brandLockup(), h("span", { class: "variant" }, "viewer"), researchPreview()),
      this.crumbs,
      h("span", { class: "spacer" }),
      buildSwatchDropdown(),
      buildTypefacePopover(),
      buildScalePill(),
      this.status.el,
    );
  }

  private readonly select: (id: string) => void;

  setCrumbs(crumbs: Crumb[]): void {
    const digest = crumbs.map((c) => `${c.id}\u0000${c.label}`).join("\u0001");
    if (digest === this.crumbDigest) return;
    this.crumbDigest = digest;
    clear(this.crumbs);
    crumbs.forEach((c, i) => {
      if (i > 0) this.crumbs.appendChild(h("span", { class: "crumb-sep" }, "›"));
      const last = i === crumbs.length - 1;
      this.crumbs.appendChild(
        last
          ? h("span", { class: "crumb current", "aria-current": "page" }, c.label)
          : h("button", { class: "crumb", type: "button", onclick: () => this.select(c.id) }, c.label),
      );
    });
    this.up.classList.toggle("off", crumbs.length < 2);
    this.up.disabled = crumbs.length < 2;
  }
}
