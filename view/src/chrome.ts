// The top bar and the four persisted appearance settings: colour theme,
// typeface, text size, and page scale (docs/design-language.md, "Chrome").
// One function applies each setting, and every control that changes a
// value goes through it, so the controls never disagree with the page.

import { brandLockup, researchPreview } from "./brand.js";
import { clear, h } from "./dom.js";

// Preview tuples ported from zicato's ui.js COLOR_THEMES, in the order
// ground, surface, ink, improve, regress, accent. These hex values draw the
// swatch strips only; the page itself reads the --v2-* tokens in tokens.css.
// lunaria-eclipse previews a substituted magenta accent because its live
// accent is too close to its ink to read in a strip.
export const THEMES: { id: string; label: string; preview: [string, string, string, string, string, string] }[] = [
  { id: "monokai", label: "monokai", preview: ["#1e1f1c", "#272822", "#f8f8f2", "#a6e22e", "#f92672", "#66d9ef"] },
  { id: "solarized-dark", label: "solarized dark", preview: ["#04222B", "#0A2D38", "#93A1A1", "#8BB80E", "#E0483C", "#2AA198"] },
  { id: "solarized-light", label: "solarized light", preview: ["#FDF6E3", "#FBF1D6", "#586E75", "#6B9B0B", "#DC322F", "#268BD2"] },
  { id: "google-light", label: "google light", preview: ["#FFFFFF", "#F4F4F4", "#474A4E", "#34A853", "#EA4335", "#1B9CB8"] },
  { id: "google-dark", label: "google dark", preview: ["#202124", "#2C2D30", "#FFFFFF", "#34A853", "#EA4335", "#24C1E0"] },
  { id: "lunaria-light", label: "lunaria light", preview: ["#EBE4E1", "#E2DCD9", "#363434", "#497D46", "#783C1F", "#3778A9"] },
  { id: "lunaria-eclipse", label: "lunaria eclipse", preview: ["#323F46", "#3B484F", "#DFE2ED", "#BEDBC1", "#BA9088", "#C8429F"] },
  { id: "belafonte-day", label: "belafonte day", preview: ["#D5CCBA", "#CCC3B2", "#34292D", "#6e6a4e", "#BE100E", "#426A79"] },
  { id: "belafonte-night", label: "belafonte night", preview: ["#20111B", "#271821", "#D5CCBA", "#a6a07a", "#d6403e", "#6F8E97"] },
  { id: "paper", label: "paper", preview: ["#F2EEDE", "#E6E2D3", "#1A1A1A", "#216609", "#CC3E28", "#1E6FCC"] },
  { id: "zenburn", label: "zenburn", preview: ["#3A3A3A", "#424241", "#DCDCCC", "#8FB28F", "#CC9393", "#8CD0D3"] },
  { id: "selenized-black", label: "selenized black", preview: ["#181818", "#202020", "#DEDEDE", "#83C746", "#FF5E56", "#56D8C9"] },
  { id: "relaxed", label: "relaxed", preview: ["#353A44", "#3D424B", "#F7F7F7", "#A0AC77", "#BC5653", "#7EAAC7"] },
  { id: "espresso", label: "espresso", preview: ["#323232", "#3A3A3A", "#FFFFFF", "#A5C261", "#D25252", "#6C99BB"] },
  { id: "dracula", label: "dracula", preview: ["#282A36", "#343746", "#F8F8F2", "#50FA7B", "#FF5555", "#BD93F9"] },
  { id: "ubuntu", label: "ubuntu", preview: ["#300A24", "#3D1530", "#EEEEEC", "#8AE234", "#CC0000", "#34E2E2"] },
];

export type TypefaceMode = "technical" | "editorial" | "display";

/** Twelve faces, four per mode. The first face of each mode is that mode's default. */
export const TYPEFACES: { id: string; mode: TypefaceMode; label: string; sans: string; mono: string; head: string }[] = [
  { id: "technical-ia-writer", mode: "technical", label: "ia writer mono + jetbrains mono", sans: "'iA Writer Mono', ui-monospace, monospace", mono: "'JetBrains Mono', ui-monospace, monospace", head: "'iA Writer Mono', ui-monospace, monospace" },
  { id: "technical-source", mode: "technical", label: "source sans 3 + source code pro", sans: "'Source Sans 3', system-ui, sans-serif", mono: "'Source Code Pro', ui-monospace, monospace", head: "'Source Sans 3', system-ui, sans-serif" },
  { id: "technical-inconsolata", mode: "technical", label: "inconsolata", sans: "'Inconsolata', ui-monospace, monospace", mono: "'Inconsolata', ui-monospace, monospace", head: "'Inconsolata', ui-monospace, monospace" },
  { id: "technical-ubuntu", mode: "technical", label: "ubuntu + ubuntu mono", sans: "'Ubuntu', system-ui, sans-serif", mono: "'Ubuntu Mono', ui-monospace, monospace", head: "'Ubuntu', system-ui, sans-serif" },
  { id: "editorial-source-serif", mode: "editorial", label: "source serif 4", sans: "'Source Serif 4', Georgia, serif", mono: "'Source Serif 4', Georgia, serif", head: "'Source Serif 4', Georgia, serif" },
  { id: "editorial-fraunces", mode: "editorial", label: "fraunces", sans: "'Fraunces', Georgia, serif", mono: "'Fraunces', Georgia, serif", head: "'Fraunces', Georgia, serif" },
  { id: "editorial-bitter", mode: "editorial", label: "bitter", sans: "'Bitter', Georgia, serif", mono: "'Bitter', Georgia, serif", head: "'Bitter', Georgia, serif" },
  { id: "editorial-literata", mode: "editorial", label: "literata", sans: "'Literata', Georgia, serif", mono: "'Literata', Georgia, serif", head: "'Literata', Georgia, serif" },
  { id: "display-space-grotesk", mode: "display", label: "space grotesk + archivo narrow", sans: "'Space Grotesk', system-ui, sans-serif", mono: "'JetBrains Mono', ui-monospace, monospace", head: "'Archivo Narrow', 'Space Grotesk', system-ui, sans-serif" },
  { id: "display-hanken", mode: "display", label: "hanken grotesk", sans: "'Hanken Grotesk', system-ui, sans-serif", mono: "'Hanken Grotesk', system-ui, sans-serif", head: "'Hanken Grotesk', system-ui, sans-serif" },
  { id: "display-barlow", mode: "display", label: "barlow condensed + space grotesk", sans: "'Space Grotesk', system-ui, sans-serif", mono: "'Space Grotesk', system-ui, sans-serif", head: "'Barlow Condensed', 'Archivo Narrow', system-ui, sans-serif" },
  { id: "display-bricolage", mode: "display", label: "bricolage grotesque", sans: "'Bricolage Grotesque', system-ui, sans-serif", mono: "'Bricolage Grotesque', system-ui, sans-serif", head: "'Bricolage Grotesque', system-ui, sans-serif" },
];

export const FONT_SIZES: { id: string; label: string; scale: number }[] = [
  { id: "small", label: "S", scale: 1 },
  { id: "medium", label: "M", scale: 1.15 },
  { id: "large", label: "L", scale: 1.3 },
];

export const SCALE_MIN = 70;
export const SCALE_MAX = 150;
export const SCALE_STEP = 5;
export const SCALE_DEFAULT = 100;

const KEY_THEME = "foe.theme";
const KEY_TYPEFACE = "foe.typeface";
const KEY_FONTSIZE = "foe.fontsize";
const KEY_SCALE = "foe.scale";

const DEFAULT_THEME_DARK = "monokai";
const DEFAULT_THEME_LIGHT = "paper";
const DEFAULT_TYPEFACE = "technical-ia-writer";
const DEFAULT_FONTSIZE = "small";

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

export function normaliseScale(value: unknown): number {
  let n = Number(value);
  if (!Number.isFinite(n)) n = SCALE_DEFAULT;
  n = Math.round(n / SCALE_STEP) * SCALE_STEP;
  return Math.min(SCALE_MAX, Math.max(SCALE_MIN, n));
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
 * either, `prefers-color-scheme` chooses monokai for dark and paper for light.
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

/**
 * The one line each option sets in its own face. A technical face is a
 * face for reading code, so its specimen is a line of code; an editorial
 * or display face sets a sentence.
 */
export function specimenLine(mode: TypefaceMode): string {
  return mode === "technical" ? "let outcome = episode.run();" : "One bounded release of work.";
}

/** The family the specimen line is set in: the mode's data face or its body face. */
function specimenFamily(face: (typeof TYPEFACES)[number]): string {
  return face.mode === "technical" ? face.mono : face.sans;
}

/** The face's own name, set in that face. */
function faceName(face: (typeof TYPEFACES)[number], className: string): HTMLElement {
  return h("span", { class: className, style: `font-family:${face.head}` }, face.label);
}

export function buildTypefacePopover(): HTMLElement {
  const nameHost = h("span", { class: "tf-current" });
  const trigger = h("button", { class: "trigger", type: "button", "aria-haspopup": "listbox", "aria-expanded": "false", title: "typeface" }, nameHost, h("span", { class: "caret" }, "▾"));
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
          faceName(face, "tf-name"),
          h("span", { class: "tf-spec", style: `font-family:${specimenFamily(face)}`, "aria-hidden": "true" }, specimenLine(face.mode)),
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
    nameHost.appendChild(faceName(face, "cd-name"));
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
    const digest = crumbs.map((c) => `${c.id} ${c.label}`).join("");
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
