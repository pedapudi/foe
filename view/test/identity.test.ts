// Which colour names an agent (src/identity.ts). A colour belongs to an
// episode: two episodes of one contract run at the same time in an ordinary
// team, and the outline reads them interleaved, so one colour across both
// would leave column position as the only thing telling their rows apart.

import assert from "node:assert/strict";
import { test } from "node:test";
import { IDENTITY_COLORS, claim, identityIndex, identitySlot } from "../src/identity.js";

test("two episodes of one contract take colours of their own", () => {
  claim([
    { id: "ep_1", name: "worker" },
    { id: "ep_2", name: "worker" },
    { id: "ep_3", name: "worker" },
  ]);
  const slots = ["ep_1", "ep_2", "ep_3"].map(identitySlot);
  assert.equal(new Set(slots).size, 3, `three episodes, three colours: ${slots}`);
  assert.equal(slots[0], identityIndex("worker"), "the first takes the colour the name selects");
});

test("a caller holding only a name reads the first episode of that name", () => {
  assert.equal(identitySlot("worker"), identitySlot("ep_1"));
});

test("an episode keeps the colour it was given when the fold is read again", () => {
  const before = identitySlot("ep_2");
  claim([{ id: "ep_2", name: "worker" }, { id: "ep_4", name: "surveyor" }]);
  assert.equal(identitySlot("ep_2"), before, "a colour never changes under a reader mid-run");
});

test("past the eighth episode the colours repeat", () => {
  const many = Array.from({ length: IDENTITY_COLORS + 4 }, (_, i) => ({ id: `ep_x${i}`, name: `role${i}` }));
  claim(many);
  const slots = many.map((episode) => identitySlot(episode.id));
  assert.equal(new Set(slots).size <= IDENTITY_COLORS, true, "there are only eight colours");
});

test("an episode the run never claimed falls back to its own hash", () => {
  assert.equal(identitySlot("ep_never"), identityIndex("ep_never"));
});
