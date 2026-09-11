import assert from "node:assert/strict";
import { test } from "node:test";
import { communications, readCommunication } from "../src/communication.js";
import type { CommunicationEvidence } from "../src/communication.js";
import { EpisodeFold } from "../src/fold.js";
import { causalityOutline, layoutLanes, readCausality, visibleRows, ROW_PITCH } from "../src/causality.js";
import { fixture } from "./helpers.js";
import { obj, str } from "../src/types.js";

const logs = ["coordinator", "editor", "tester"].map((name) => fixture(`communication/${name}.jsonl`));
function run() {
  return logs.map((events, depth) => {
    const fold = new EpisodeFold(str(obj(events[0]!.data).id), { stream: false });
    for (const event of events) fold.push(event);
    return { fold, episode: readCausality(fold.summary, fold.rows, depth === 0 ? 0 : 1) };
  });
}

// docs/viewer.md: the conversation joins recorded communication and hides tool payloads.
test("a recorded execution shows each communication body once with its kind", () => {
  const episodes = run();
  const messages = communications(episodes.flatMap(({ fold }) => readCommunication(fold.summary, fold.rows)));
  assert.equal(messages.length, 7);
  assert.deepEqual(messages.map((message) => message.kind).sort(), ["ask", "ask", "default", "notify", "reply", "send", "send"]);
  assert.ok(messages.every((message) => message.delivered));
  assert.ok(messages.every((message) => !message.body.includes("Answer this with send")));
  assert.equal(messages.filter((message) => message.body === "Checking the section.").length, 1);
  assert.ok(messages.every((message) => !message.body.includes("ended: completed")));
  const outline = causalityOutline(episodes.map(({ episode }) => episode));
  const rows = visibleRows(outline, "conversation");
  assert.equal(rows.filter((row) => row.kind === "message").length, 7);
  assert.ok(!rows.some((row) => row.kind === "result"));
  const layout = layoutLanes(outline, rows, rows.map(() => ROW_PITCH));
  const arrows = layout.edges.filter((edge) => edge.kind === "message");
  assert.ok(arrows.length >= 3, `${arrows.length} recorded cross-lane messages`);
  for (const arrow of arrows) {
    assert.equal(arrow.from.y, arrow.to.y);
    assert.notEqual(arrow.from.x, arrow.to.x);
  }
  const question = rows.findIndex((row) => row.body === "Can this wording ship?");
  assert.ok(rows.findIndex((row) => row.body === "The wording is ready.") > question);
});

function evidence(overrides: Partial<CommunicationEvidence>): CommunicationEvidence {
  return { stage: "queued", episodeId: "lead", seq: 1, time: 1, from: "a", to: "b", messageId: "q", kind: "message", body: "Words", ...overrides };
}

// docs/viewer.md: a delivery record confirms only a matching queued route.
test("missing receipts retain sent state and orphan confirmations invent no messages", () => {
  const queued = evidence({});
  assert.equal(communications([queued])[0]!.delivered, false);
  const orphan = evidence({ stage: "confirmed", seq: 2, from: "", body: "", to: "elsewhere" });
  assert.equal(communications([queued, orphan])[0]!.delivered, false);
  assert.deepEqual(communications([orphan]), []);
  const receipt = evidence({ stage: "received", episodeId: "b", kind: "send" });
  assert.deepEqual(communications([receipt, orphan])[0]!.stages, new Set(["received"]));
});

// docs/viewer.md: self-addressed questions, answers, and defaults remain distinct.
test("a shared identifier does not combine a self question with its answer", () => {
  const records = [
    evidence({ to: "a" }), evidence({ to: "a", seq: 2 }),
    evidence({ to: "a", stage: "received", kind: "ask" }),
    evidence({ to: "a", stage: "received", kind: "reply", seq: 2 }),
    evidence({ to: "a", stage: "received", kind: "default", from: "", seq: 3 }),
  ];
  const result = communications(records);
  assert.deepEqual(result.map((message) => message.kind), ["ask", "reply", "default"]);
  assert.equal(new Set(result.map((message) => message.id)).size, 3);
});

// docs/viewer.md: repeated notifications retain their separate recorded occurrences.
test("identical notifications pair receipts and acknowledgements in order", () => {
  const receipt = evidence({ stage: "received", kind: "notify", messageId: "" });
  const ack = evidence({ stage: "acknowledged", kind: "notify", messageId: "" });
  assert.equal(communications([receipt, receipt, ack, ack]).length, 2);
});
