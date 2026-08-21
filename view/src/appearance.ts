// The catalogue of appearance choices and the rules for naming them: the
// sixteen colour themes, the twelve typefaces, the three text sizes, the
// limits of the page scale, and the defaults. The module reads no document,
// so its rules are tested directly; src/chrome.ts builds the controls that
// apply them.

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

/**
 * Twelve faces, four per mode. The first face of each mode is that mode's
 * default, and the first technical face is the viewer's default.
 *
 * `label` names an option by the families it sets. One family in every role
 * gives a name of one family. Two families give both names joined by a plus
 * sign. The one option that sets three names the two that carry the most
 * text, which are its body face and its data face. Each family is named as
 * short as it stays unambiguous among the twelve.
 */
export const TYPEFACES: { id: string; mode: TypefaceMode; label: string; sans: string; mono: string; head: string }[] = [
  { id: "technical-inconsolata", mode: "technical", label: "inconsolata", sans: "'Inconsolata', ui-monospace, monospace", mono: "'Inconsolata', ui-monospace, monospace", head: "'Inconsolata', ui-monospace, monospace" },
  { id: "technical-ia-writer", mode: "technical", label: "ia writer + jetbrains", sans: "'iA Writer Mono', ui-monospace, monospace", mono: "'JetBrains Mono', ui-monospace, monospace", head: "'iA Writer Mono', ui-monospace, monospace" },
  { id: "technical-source", mode: "technical", label: "source sans + source code", sans: "'Source Sans 3', system-ui, sans-serif", mono: "'Source Code Pro', ui-monospace, monospace", head: "'Source Sans 3', system-ui, sans-serif" },
  { id: "technical-ubuntu", mode: "technical", label: "ubuntu + ubuntu mono", sans: "'Ubuntu', system-ui, sans-serif", mono: "'Ubuntu Mono', ui-monospace, monospace", head: "'Ubuntu', system-ui, sans-serif" },
  { id: "editorial-source-serif", mode: "editorial", label: "source serif", sans: "'Source Serif 4', Georgia, serif", mono: "'Source Serif 4', Georgia, serif", head: "'Source Serif 4', Georgia, serif" },
  { id: "editorial-fraunces", mode: "editorial", label: "fraunces", sans: "'Fraunces', Georgia, serif", mono: "'Fraunces', Georgia, serif", head: "'Fraunces', Georgia, serif" },
  { id: "editorial-bitter", mode: "editorial", label: "bitter", sans: "'Bitter', Georgia, serif", mono: "'Bitter', Georgia, serif", head: "'Bitter', Georgia, serif" },
  { id: "editorial-literata", mode: "editorial", label: "literata", sans: "'Literata', Georgia, serif", mono: "'Literata', Georgia, serif", head: "'Literata', Georgia, serif" },
  { id: "display-space-grotesk", mode: "display", label: "space grotesk + jetbrains", sans: "'Space Grotesk', system-ui, sans-serif", mono: "'JetBrains Mono', ui-monospace, monospace", head: "'Archivo Narrow', 'Space Grotesk', system-ui, sans-serif" },
  { id: "display-hanken", mode: "display", label: "hanken grotesk", sans: "'Hanken Grotesk', system-ui, sans-serif", mono: "'Hanken Grotesk', system-ui, sans-serif", head: "'Hanken Grotesk', system-ui, sans-serif" },
  { id: "display-barlow", mode: "display", label: "barlow + space grotesk", sans: "'Space Grotesk', system-ui, sans-serif", mono: "'Space Grotesk', system-ui, sans-serif", head: "'Barlow Condensed', 'Archivo Narrow', system-ui, sans-serif" },
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

/**
 * The defaults before a reader has chosen anything. The two themes are the
 * light and the dark ground of one palette, so a machine set to either
 * ground gets the same design; `prefers-color-scheme` picks between them
 * and a stored choice wins over both.
 */
export const DEFAULT_THEME_LIGHT = "google-light";
export const DEFAULT_THEME_DARK = "google-dark";
export const DEFAULT_TYPEFACE = "technical-inconsolata";
export const DEFAULT_FONTSIZE = "small";

/**
 * The one line each option sets in its own face. A technical face is a face
 * for reading code, so its specimen is a line of code; an editorial or
 * display face sets a sentence.
 */
export function specimenLine(mode: TypefaceMode): string {
  return mode === "technical" ? "let outcome = episode.run();" : "One bounded release of work.";
}

/**
 * The first family of an option's name. The picker's trigger has room for
 * one family beside its specimen, and an option that pairs two families
 * leads with the one that sets its body text.
 */
export function leadFamily(label: string): string {
  const plus = label.indexOf(" + ");
  return plus === -1 ? label : label.slice(0, plus);
}

/** The page scale in percent, clamped to the range and snapped to the step. */
export function normaliseScale(value: unknown): number {
  let n = Number(value);
  if (!Number.isFinite(n)) n = SCALE_DEFAULT;
  n = Math.round(n / SCALE_STEP) * SCALE_STEP;
  return Math.min(SCALE_MAX, Math.max(SCALE_MIN, n));
}
