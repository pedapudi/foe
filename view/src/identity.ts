// The color that names an agent. An episode's name is written in one of eight
// hues so that two agents on screen together are told apart without a legend.
// `tokens.css` holds the eight values and docs/design-language.md states where
// the channel may be used: on a name set as text, never on a mark in a figure.
//
// The name's hash picks the color, so one role keeps one color across the
// tree, the trajectory, the outline, the boards, and the terminal transcript.
// A hash alone is not enough, because eight colors over four names collide
// more often than not, and two agents in one figure wearing one color is the
// failure this channel exists to prevent. `claim` therefore moves a name on to
// the next free color when the color it hashes to is already held, and keeps
// what it assigned, so a color never changes under a reader mid-run.

/** How many identity colors `tokens.css` defines. */
export const IDENTITY_COLORS = 8;

/**
 * FNV-1a over the name's UTF-16 code units, reduced to a color index. The same
 * function is written in `crates/view/src/terminal.rs` and in the landing
 * page's scripts. An empty name takes the first color.
 */
export function identityIndex(name: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < name.length; i += 1) {
    hash ^= name.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash % IDENTITY_COLORS;
}

const held = new Map<string, number>();

/**
 * Gives every name a color of its own, keeping the ones already given out.
 * Call it with the run's episode names, in the order the run opened them,
 * whenever the fold changes: a name new to the run takes the color it hashes
 * to, or the next free one when another name holds that color. Order matters
 * only where two names want one color, and it is the order the run opened
 * them because that is the order a terminal transcript, which cannot revise
 * what it has already written, has to assign in. Past the eighth name the
 * colors repeat.
 */
export function claim(names: string[]): void {
  const used = new Set(held.values());
  for (const name of new Set(names)) {
    if (held.has(name)) continue;
    let slot = identityIndex(name);
    for (let step = 0; step < IDENTITY_COLORS && used.has(slot); step += 1) {
      slot = (slot + 1) % IDENTITY_COLORS;
    }
    used.add(slot);
    held.set(name, slot);
  }
}

/** The custom property holding the color for `name`, ready for a style value. */
export function identityColor(name: string): string {
  return `var(--foe-id-${(held.get(name) ?? identityIndex(name)) + 1})`;
}

/**
 * The inline declaration an element carries so the stylesheet can put the
 * identity color where it wants it. Setting `--id` rather than `color`
 * leaves the selected and hovered states, which are author rules, in charge.
 */
export function identityStyle(name: string): string {
  return `--id: ${identityColor(name)}`;
}
