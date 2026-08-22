// Which fields of an event payload the raw events tab draws with a
// rendering of its own, and which fall to the general JSON renderer.
//
// A payload written by this runtime is not anonymous JSON: a `model/request`
// carries a conversation, an `assistant/message` carries a token count, a
// `tool/result` carries a diff. Drawing those as nested braces makes a
// reader parse a shape the viewer already knows how to set. This module
// names the shapes and holds the rule that decides one; it builds nothing,
// so the rule is checked by a unit test.
//
// Two properties hold whatever the log contains. Every key of a payload
// yields a field, in the order the log wrote it, so nothing is dropped. A
// field whose value does not have the shape its form expects falls back to
// the general renderer, so a payload from a runtime this bundle does not
// know still reads.

import { arr, obj, str } from "./types.js";

/** How one field of a payload is drawn. */
export type FieldForm =
  /** A derived message list, as the conversation sets messages. */
  | "messages"
  /** A list of content blocks, as the conversation sets a user message. */
  | "content"
  /** A token count, as one line of labelled numbers. */
  | "usage"
  /** Tool calls, each as its name over its arguments. */
  | "tool-calls"
  /** Reasoning blocks, each as its text with its signature behind an expander. */
  | "thinking"
  /** One streamed fragment, as its kind over the text it carried. */
  | "chunk"
  /** How an episode or a child ended, in the colour of its direction. */
  | "outcome"
  /** The text a tool returned, drawn by its shape as a diff, JSON, or source. */
  | "rendered"
  /** Tool schemas, each as its name over a table of its parameters. */
  | "tool-schemas"
  /** Markdown, as the conversation sets assistant text. */
  | "markdown"
  /** Prose that is not Markdown, set preformatted. */
  | "text"
  /** Anything else: the general structured JSON renderer. */
  | "json";

export interface PayloadField {
  readonly key: string;
  readonly value: unknown;
  readonly form: FieldForm;
  /**
   * True for the one field of a message that carries the message's own
   * words. Such a field is set without its key, because the role beside it
   * already names what it is.
   */
  readonly lead: boolean;
}

/**
 * The form each named field of each event type takes. An event type absent
 * from this table, and a key absent from its row, takes the general
 * renderer. The types listed are the ones whose payloads recur: the request
 * that carries a conversation, the response and the fragments that stream
 * it, the tool result, the inbox and team messages, the header that carries
 * the system prompt and the tool schemas, the two events that report an
 * outcome, the task, the compaction summary, and the workflow node result.
 */
const FORMS: Readonly<Record<string, Readonly<Record<string, FieldForm>>>> = {
  "episode/start": { task: "text" },
  "episode/end": { outcome: "outcome" },
  "request/header": { system: "markdown", tools: "tool-schemas" },
  "model/request": { messages: "messages" },
  "assistant/message": {
    text: "markdown",
    thinking: "thinking",
    tool_calls: "tool-calls",
    usage: "usage",
  },
  "assistant/chunk": { chunk: "chunk" },
  "tool/result": { rendered: "rendered" },
  "host/tool-call": { args: "json" },
  "inbox/item": { content: "content" },
  "team/message": { content: "content" },
  "spawn/end": { outcome: "outcome" },
  "compaction/summary": { summary: "markdown" },
  "compaction/end": { usage: "usage" },
  "workflow/node-end": { rendered: "rendered" },
};

/** The form the fields of one derived message take, by the message's role. */
const MESSAGE_FORMS: Readonly<Record<string, Readonly<Record<string, FieldForm>>>> = {
  user: { content: "content" },
  assistant: { text: "markdown", thinking: "thinking", tool_calls: "tool-calls" },
  tool: { rendered: "rendered" },
};

/** The field of a message that carries its own words, by role. */
const MESSAGE_BODY: Readonly<Record<string, string>> = {
  user: "content",
  assistant: "text",
  tool: "rendered",
};

/** True when a value has the shape a form draws. */
function fits(form: FieldForm, value: unknown): boolean {
  switch (form) {
    case "messages":
      return isListOf(value, (m) => str(obj(m).role) !== "");
    case "content":
      return isListOf(value, (b) => isRecord(b));
    case "tool-calls":
    case "tool-schemas":
      return isListOf(value, (c) => str(obj(c).name) !== "");
    case "thinking":
      return isListOf(value, (b) => typeof obj(b).text === "string");
    case "usage":
      return isRecord(value) && Object.values(value as Record<string, unknown>).every((v) => typeof v === "number");
    case "chunk":
    case "outcome":
      return str(obj(value).kind) !== "";
    case "rendered":
    case "markdown":
    case "text":
      return typeof value === "string" && value !== "";
    case "json":
      return true;
  }
}

function isRecord(value: unknown): boolean {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isListOf(value: unknown, ok: (item: unknown) => boolean): boolean {
  return Array.isArray(value) && value.length > 0 && value.every(ok);
}

/** The form one field takes, falling back where the value has another shape. */
export function fieldForm(
  table: Readonly<Record<string, Readonly<Record<string, FieldForm>>>>,
  group: string,
  key: string,
  value: unknown,
): FieldForm {
  const wanted = table[group]?.[key];
  if (wanted === undefined) return "json";
  return fits(wanted, value) ? wanted : "json";
}

/** Every field of one event payload, in the order the log wrote the keys. */
export function payloadFields(type: string, data: Record<string, unknown>): PayloadField[] {
  return Object.keys(data).map((key) => ({
    key,
    value: data[key],
    form: fieldForm(FORMS, type, key, data[key]),
    lead: false,
  }));
}

/**
 * Every field of one derived message, in the order it was written, with the
 * field that carries the message's own words marked. The role is left out,
 * because the rendering sets it as the message's label.
 */
export function messageFields(message: Record<string, unknown>): PayloadField[] {
  const role = str(message.role);
  const body = MESSAGE_BODY[role];
  return Object.keys(message)
    .filter((key) => key !== "role")
    .map((key) => {
      const form = fieldForm(MESSAGE_FORMS, role, key, message[key]);
      return { key, value: message[key], form, lead: key === body && form !== "json" };
    });
}

/**
 * The tool result whose language colours a `rendered` text, taken from the
 * `path` of the canonical value beside it. A payload carrying no such path
 * yields the empty string, and the text is left uncoloured.
 */
export function renderedPath(data: Record<string, unknown>): string {
  return str(obj(data.value).path);
}

/** The messages of a payload field, as records the renderer can read. */
export function messageList(value: unknown): Record<string, unknown>[] {
  return arr(value).map((m) => obj(m));
}
