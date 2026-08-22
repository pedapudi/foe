// Which payload fields the raw events tab gives a rendering of their own
// (src/payload.ts). Two properties matter beyond the individual rules: a
// field list holds every key the payload holds, in the log's order, and a
// value whose shape does not match its form falls back to the general
// renderer rather than being drawn wrong.

import assert from "node:assert/strict";
import { test } from "node:test";
import { messageFields, payloadFields, renderedPath } from "../src/payload.js";
import { fixture } from "./helpers.js";

const forms = (type: string, data: Record<string, unknown>): Record<string, string> =>
  Object.fromEntries(payloadFields(type, data).map((f) => [f.key, f.form]));

test("a field list holds every key of the payload, in the order the log wrote them", () => {
  const data = JSON.parse('{"step":1,"attempt":1,"request_id":"rq_0001","header_seq":2,"consumed":[1],"messages":[]}');
  assert.deepEqual(
    payloadFields("model/request", data).map((f) => f.key),
    ["step", "attempt", "request_id", "header_seq", "consumed", "messages"],
  );
});

test("a request's messages are drawn as messages and its scalars generally", () => {
  const data = {
    step: 1,
    messages: [{ role: "user", content: [{ type: "text", text: "do the thing" }] }],
  };
  assert.deepEqual(forms("model/request", data), { step: "json", messages: "messages" });
});

test("a messages value that is not a list of roles falls back to the general renderer", () => {
  assert.equal(forms("model/request", { messages: [] }).messages, "json");
  assert.equal(forms("model/request", { messages: [{ text: "x" }] }).messages, "json");
  assert.equal(forms("model/request", { messages: "none" }).messages, "json");
});

test("a response's text, reasoning, calls, and token count each have a form", () => {
  const data = {
    text: "I will read the test first.",
    thinking: [{ text: "consider", signature: "sig" }],
    tool_calls: [{ id: "tc_01", name: "read", args: { path: "a" } }],
    usage: { input: 4120, output: 88 },
    stop: "tool",
  };
  assert.deepEqual(forms("assistant/message", data), {
    text: "markdown",
    thinking: "thinking",
    tool_calls: "tool-calls",
    usage: "usage",
    stop: "json",
  });
});

test("a token count holding anything other than numbers falls back", () => {
  assert.equal(forms("assistant/message", { usage: { input: "many" } }).usage, "json");
  assert.equal(forms("assistant/message", { usage: [1, 2] }).usage, "json");
});

test("a streamed fragment is drawn as a fragment when it names its kind", () => {
  assert.equal(forms("assistant/chunk", { chunk: { kind: "text", delta: "I will" } }).chunk, "chunk");
  assert.equal(forms("assistant/chunk", { chunk: { delta: "I will" } }).chunk, "json");
});

test("a tool result's rendered text is drawn by its shape and its value generally", () => {
  const data = { name: "read", rendered: "1\timport pytest", value: { path: "a.py" }, is_error: false };
  assert.deepEqual(forms("tool/result", data), {
    name: "json",
    rendered: "rendered",
    value: "json",
    is_error: "json",
  });
});

test("an empty rendered text falls back, because there is no shape to read", () => {
  assert.equal(forms("tool/result", { rendered: "" }).rendered, "json");
});

test("both events that report an outcome draw it as an outcome", () => {
  assert.equal(forms("episode/end", { outcome: { kind: "completed", value: {} } }).outcome, "outcome");
  assert.equal(forms("spawn/end", { child_id: "ep_1", outcome: { kind: "failed", error: "x" } }).outcome, "outcome");
  assert.equal(forms("episode/end", { outcome: { code: "x" } }).outcome, "json");
});

test("a header's system prompt sets as Markdown and its schemas as tables", () => {
  const data = { reason: "initial", system: "# Role\n\nDo the work.", tools: [{ name: "read" }], model: {} };
  assert.deepEqual(forms("request/header", data), {
    reason: "json",
    system: "markdown",
    tools: "tool-schemas",
    model: "json",
  });
});

test("an event type this bundle knows nothing about draws every field generally", () => {
  assert.deepEqual(forms("future/event", { messages: [{ role: "user" }], usage: { input: 1 } }), {
    messages: "json",
    usage: "json",
  });
});

test("a key a known type does not name draws generally, so a newer field still reads", () => {
  assert.equal(forms("model/request", { cache_policy: { ttl: 300 } }).cache_policy, "json");
});

test("every field of every event in every fixture is present and none is dropped", () => {
  for (const name of ["root.jsonl", "child.jsonl", "fork.jsonl", "compact.jsonl", "rich.jsonl", "workflow.jsonl"]) {
    for (const ev of fixture(name)) {
      const keys = Object.keys(ev.data);
      assert.deepEqual(
        payloadFields(ev.type, ev.data).map((f) => f.key),
        keys,
        `${name} seq ${ev.seq} (${ev.type})`,
      );
    }
  }
});

test("a message drops its role from the field list and leads with its own words", () => {
  const user = messageFields({ role: "user", content: [{ type: "text", text: "go" }] });
  assert.deepEqual(user.map((f) => [f.key, f.form, f.lead]), [["content", "content", true]]);
  const assistant = messageFields({ role: "assistant", text: "done", tool_calls: [{ name: "read" }] });
  assert.deepEqual(assistant.map((f) => [f.key, f.form, f.lead]), [
    ["text", "markdown", true],
    ["tool_calls", "tool-calls", false],
  ]);
  const tool = messageFields({ role: "tool", call_id: "tc_01", rendered: "ok", is_error: false });
  assert.deepEqual(tool.map((f) => [f.key, f.form, f.lead]), [
    ["call_id", "json", false],
    ["rendered", "rendered", true],
    ["is_error", "json", false],
  ]);
});

test("a message whose body field is empty leads with nothing and still shows the field", () => {
  const assistant = messageFields({ role: "assistant", text: "", tool_calls: [{ name: "read" }] });
  assert.deepEqual(assistant.map((f) => [f.key, f.form, f.lead]), [
    ["text", "json", false],
    ["tool_calls", "tool-calls", false],
  ]);
});

test("a message of a role this bundle does not know draws generally", () => {
  const other = messageFields({ role: "auditor", note: "checked" });
  assert.deepEqual(other.map((f) => [f.key, f.form, f.lead]), [["note", "json", false]]);
});

test("the language of a rendered text comes from the path of the value beside it", () => {
  assert.equal(renderedPath({ value: { path: "src/main.rs" } }), "src/main.rs");
  assert.equal(renderedPath({ value: "text" }), "");
  assert.equal(renderedPath({}), "");
});
