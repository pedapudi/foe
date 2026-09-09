// The color that names an agent. An episode's name is written in one of eight
// hues so that two agents on screen together are told apart without a legend.
// `tokens.css` holds the eight values and docs/design-language.md states where
// the channel may be used: on a name set as text, never on a mark in a figure.
//
// A color belongs to an episode, not to a contract. Two episodes of one
// contract run at the same time in an ordinary team, and the outline reads
// them interleaved, so a color shared between them leaves the reader with
// nothing but column position to tell one worker's row from the other's.
// The terminal has always assigned per lane for this reason.
//
// The episode's name still picks the first color it is offered, so a run
// with one episode per contract writes each role in the color that role
// keeps across the tree, the trajectory, the outline, and the boards. Eight
// colors over four names collide more often than not, so `claim` moves an
// episode on to the next free color when the one its name selects is held,
// and keeps what it assigned, so a color never changes under a reader
// mid-run. A caller that has only a name, such as a board naming a roster
// member, reads the color the first episode of that name took.

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
 * Gives every episode a color of its own, keeping the ones already given out.
 * Call it with the run's episodes, in the order the run opened them, whenever
 * the fold changes: an episode new to the run takes the color its name
 * selects, or the next free one when another episode holds that color. Order
 * matters only where two episodes want one color, and it is the order the run
 * opened them because that is the order a terminal transcript, which cannot
 * revise what it has already written, has to assign in. Past the eighth
 * episode the colors repeat.
 *
 * The first episode of a name also registers under that name, so a caller
 * holding a name and no episode reads the same color.
 */
export function claim(episodes: { id: string; name: string }[]): void {
  const used = new Set(held.values());
  for (const episode of episodes) {
    if (held.has(episode.id)) continue;
    let slot = identityIndex(episode.name);
    for (let step = 0; step < IDENTITY_COLORS && used.has(slot); step += 1) {
      slot = (slot + 1) % IDENTITY_COLORS;
    }
    used.add(slot);
    held.set(episode.id, slot);
    if (!held.has(episode.name)) {
      held.set(episode.name, slot);
    }
  }
}

/**
 * The color `key` holds: the one `claim` gave it, or the one it hashes to
 * when it was never claimed. `key` is an episode id wherever the caller has
 * one, because a color belongs to an episode; a caller holding only a name
 * passes that. A figure that draws a name rather than writing it, such as
 * the lane a causality drawing gives an episode, reads the slot and not the
 * custom property.
 */
export function identitySlot(key: string): number {
  return held.get(key) ?? identityIndex(key);
}

/** The custom property holding the color for `key`, ready for a style value. */
export function identityColor(key: string): string {
  return `var(--foe-id-${identitySlot(key) + 1})`;
}

/**
 * The inline declaration an element carries so the stylesheet can put the
 * identity color where it wants it. Setting `--id` rather than `color`
 * leaves the selected and hovered states, which are author rules, in charge.
 */
export function identityStyle(key: string): string {
  return `--id: ${identityColor(key)}`;
}
