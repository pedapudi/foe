// The brand lockup, copied from docs/brand/foe-lockup.svg with whitespace
// removed and every coordinate rounded to two decimals. The mark strokes
// in `currentColor` and fills its core with `--foe-accent`, which
// tokens.css sets per theme, so the lockup follows the text colour around
// it on every ground.
//
// docs/brand/README.md states the geometry and the rules: the mark is
// never stretched, recoloured, or animated, and the dashed limit stays.

import { h } from "./dom.js";

const LOCKUP =
  '<svg viewBox="0 0 299 120" role="img" aria-label="foe"><g fill="none" stroke="currentColor" stroke-linecap="' +
  'round"><path d="M60,49 L60,14" stroke-width="1.8"/><path d="M50.47,54.5 L20.16,37" stroke-width="1.8"/><path' +
  ' d="M50.47,65.5 L20.16,83" stroke-width="1.8"/><path d="M60,71 L60,106" stroke-width="1.8"/><path d="M69.53,' +
  '65.5 L99.84,83" stroke-width="1.8"/><path d="M69.53,54.5 L99.84,37" stroke-width="1.8"/><path d="M54.5,50.47' +
  ' L45,34.02" stroke-width="1.2" opacity="0.6"/><path d="M49,60 L30,60" stroke-width="1.2" opacity="0.6"/><pat' +
  'h d="M54.5,69.53 L45,85.98" stroke-width="1.2" opacity="0.6"/><path d="M65.5,69.53 L75,85.98" stroke-width="' +
  '1.2" opacity="0.6"/><path d="M71,60 L90,60" stroke-width="1.2" opacity="0.6"/><path d="M65.5,50.47 L75,34.02' +
  '" stroke-width="1.2" opacity="0.6"/><path d="M57.15,49.37 L54.31,38.75" stroke-width="0.9" opacity="0.35"/><' +
  'path d="M52.22,52.22 L44.44,44.44" stroke-width="0.9" opacity="0.35"/><path d="M49.37,57.15 L38.75,54.31" st' +
  'roke-width="0.9" opacity="0.35"/><path d="M49.37,62.85 L38.75,65.69" stroke-width="0.9" opacity="0.35"/><pat' +
  'h d="M52.22,67.78 L44.44,75.56" stroke-width="0.9" opacity="0.35"/><path d="M57.15,70.63 L54.31,81.25" strok' +
  'e-width="0.9" opacity="0.35"/><path d="M62.85,70.63 L65.69,81.25" stroke-width="0.9" opacity="0.35"/><path d' +
  '="M67.78,67.78 L75.56,75.56" stroke-width="0.9" opacity="0.35"/><path d="M70.63,62.85 L81.25,65.69" stroke-w' +
  'idth="0.9" opacity="0.35"/><path d="M70.63,57.15 L81.25,54.31" stroke-width="0.9" opacity="0.35"/><path d="M' +
  '67.78,52.22 L75.56,44.44" stroke-width="0.9" opacity="0.35"/><path d="M62.85,49.37 L65.69,38.75" stroke-widt' +
  'h="0.9" opacity="0.35"/><circle cx="60" cy="60" r="26" stroke-width="1.2" opacity="0.42"/><circle cx="60" cy' +
  '="60" r="38" stroke-width="1" opacity="0.28"/><circle cx="60" cy="60" r="52" stroke-width="1.4" opacity="0.5' +
  '5" stroke-dasharray="4 5"/><circle cx="60" cy="60" r="5" fill="var(--foe-accent)" stroke="none"/></g><g fill' +
  '="currentColor"><path d="M181.76 22.87V28.88H173.55Q169.66 28.88 168.15 30.47Q166.64 32.06 166.64 36.11V40H1' +
  '81.76V45.62H166.64V84H159.41V45.62H147.66V40H159.41V36.94Q159.41 29.71 162.73 26.29Q166.05 22.87 173.08 22.8' +
  '7Z"/><path d="M212.64 45.07Q207.14 45.07 204.31 49.35Q201.48 53.63 201.48 62.04Q201.48 70.41 204.31 74.71Q20' +
  '7.14 79.01 212.64 79.01Q218.18 79.01 221.01 74.71Q223.84 70.41 223.84 62.04Q223.84 53.63 221.01 49.35Q218.18' +
  ' 45.07 212.64 45.07ZM212.64 38.94Q221.79 38.94 226.64 44.87Q231.5 50.8 231.5 62.04Q231.5 73.31 226.66 79.23Q' +
  '221.83 85.14 212.64 85.14Q203.49 85.14 198.65 79.23Q193.82 73.31 193.82 62.04Q193.82 50.8 198.65 44.87Q203.4' +
  '9 38.94 212.64 38.94Z"/><path d="M280.56 60.19V63.73H249.25V63.96Q249.25 71.15 253.01 75.08Q256.76 79.01 263' +
  '.59 79.01Q267.05 79.01 270.82 77.91Q274.59 76.81 278.88 74.57V81.76Q274.75 83.45 270.92 84.29Q267.09 85.14 2' +
  '63.51 85.14Q253.26 85.14 247.49 78.99Q241.71 72.84 241.71 62.04Q241.71 51.51 247.37 45.23Q253.03 38.94 262.4' +
  '5 38.94Q270.86 38.94 275.71 44.64Q280.56 50.33 280.56 60.19ZM273.34 58.07Q273.18 51.71 270.33 48.39Q267.48 4' +
  '5.07 262.14 45.07Q256.91 45.07 253.54 48.53Q250.16 51.98 249.53 58.11Z"/></g></svg>';

/**
 * The lockup as an element. The stylesheet sizes it so that the
 * wordmark's ascenders match the cap height of the text wordmark it
 * replaced, and the `viewer` tag beside it keeps its own size.
 */
export function brandLockup(): HTMLElement {
  const host = h("span", { class: "lockup", "aria-hidden": "true" });
  host.insertAdjacentHTML("afterbegin", LOCKUP);
  return host;
}

/**
 * The product-status tag: two stacked words in the wordmark's register.
 * It is informational and never interactive, so it takes no pointer
 * events and is announced as a note.
 */
export function researchPreview(): HTMLElement {
  return h(
    "span",
    { class: "respreview", role: "note", "aria-label": "research preview" },
    h("span", null, "research"),
    h("span", null, "preview"),
  );
}
