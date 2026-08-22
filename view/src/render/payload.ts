// One event payload, drawn field by field. A field whose shape this
// runtime writes often is set the way the rest of the viewer sets it: a
// message list reads as messages, a tool result reads as a diff or as
// source, a token count reads as a line of numbers. Every other field falls
// to the general JSON renderer.
//
// The tab exists so that a reader can see what the log says, so the literal
// payload stays reachable: every collapsed node opens to what it holds, and
// one control at the foot of the panel holds the payload's own JSON text.

import { compact, fmtInt, h, lazyDetails, pretty } from "../dom.js";
import type { Child } from "../dom.js";
import { messageFields, messageList, payloadFields, renderedPath } from "../payload.js";
import type { FieldForm, PayloadField } from "../payload.js";
import { obj, str } from "../types.js";
import type { LogEvent, ToolSchema } from "../types.js";
import { renderJson } from "./json.js";
import { renderMarkdown, renderToolText } from "./markup.js";
import { languageForPath } from "./shape.js";
import { renderToolSchema } from "./conversation.js";
import { outcomeRole } from "./tree.js";

/** Characters of arguments that still set on the line the call's name is on. */
const INLINE_ARGS_CHARS = 160;

/** One event's payload as a panel of fields, over its own literal JSON. */
export function renderPayload(ev: LogEvent): HTMLElement {
  const fields = payloadFields(ev.type, ev.data);
  return h(
    "div",
    { class: "payload-body" },
    fields.length === 0
      ? h("div", { class: "sub" }, "this event carries no payload")
      : fields.map((field) => renderField(field, ev.data)),
    lazyDetails([h("span", null, "literal JSON")], () => h("pre", { class: "text" }, pretty(ev.data)), {
      class: "j-literal",
    }),
  );
}

/** One field under its key, or without it where the field leads a message. */
function renderField(field: PayloadField, data: Record<string, unknown>): HTMLElement {
  const value = h("div", { class: "field-value" }, renderForm(field.form, field.value, data));
  if (field.lead) return h("div", { class: "field lead" }, value);
  return h("div", { class: "field" }, h("div", { class: "field-key" }, field.key), value);
}

/**
 * The keys of a value that a rendering drew part of, set as ordinary fields
 * under it. They go through the general renderer, so a key holding a long
 * value, such as a provider's replay token, still collapses.
 */
function remainingFields(value: Record<string, unknown>, drawn: string[]): HTMLElement[] {
  return Object.keys(value)
    .filter((key) => !drawn.includes(key))
    .map((key) =>
      h(
        "div",
        { class: "field" },
        h("div", { class: "field-key" }, key),
        h("div", { class: "field-value" }, renderJson(value[key], 1)),
      ),
    );
}

function renderForm(form: FieldForm, value: unknown, data: Record<string, unknown>): Child {
  switch (form) {
    case "messages":
      return h("div", { class: "msgs" }, messageList(value).map(renderMessage));
    case "content":
      return renderContent(value);
    case "usage":
      return renderUsage(value);
    case "tool-calls":
      return (value as unknown[]).map(renderCall);
    case "thinking":
      return (value as unknown[]).map(renderThinking);
    case "chunk":
      return renderChunk(value);
    case "outcome":
      return renderOutcome(value);
    case "rendered":
      return renderToolText(str(value), languageForPath(renderedPath(data)));
    case "tool-schemas":
      return h("div", { class: "tools-list" }, (value as ToolSchema[]).map(renderToolSchema));
    case "markdown":
      return renderMarkdown(str(value));
    case "text":
      return h("pre", { class: "text" }, str(value));
    case "json":
      return renderJson(value);
  }
}

/**
 * One derived message: its role, then its own fields. The field carrying
 * the message's words is set without a key, because the role names it.
 */
function renderMessage(message: Record<string, unknown>): HTMLElement {
  const role = str(message.role, "?");
  return h(
    "div",
    { class: `msg ${role}` },
    h("div", { class: "msg-role" }, role),
    h("div", { class: "msg-body" }, messageFields(message).map((field) => renderField(field, message))),
  );
}

/**
 * Content blocks. A text block is the text itself, as the conversation sets
 * a user message; any other block keeps its type as a label over its value.
 */
function renderContent(value: unknown): Child {
  return (value as unknown[]).map((raw) => {
    const block = obj(raw);
    if (block.type === "text" && typeof block.text === "string") {
      return h("div", { class: "block" }, h("pre", { class: "text" }, block.text));
    }
    return h(
      "div",
      { class: "block" },
      h("div", { class: "block-type" }, str(block.type, "block")),
      renderJson(block, 1),
    );
  });
}

/** A token count as one line: every key the payload carries, in its order. */
function renderUsage(value: unknown): HTMLElement {
  const usage = obj(value);
  const parts = Object.keys(usage).map((key) =>
    h("span", { class: "usage-part" }, h("span", { class: "usage-name" }, key.replace(/_/g, " ")), fmtInt(Number(usage[key]))),
  );
  return h("div", { class: "usage-line" }, parts);
}

/** One tool call: its name, then its arguments inline or behind an expander. */
function renderCall(raw: unknown): HTMLElement {
  const call = obj(raw);
  const args = typeof call.args === "string" ? call.args : compact(call.args);
  return h(
    "div",
    { class: "call" },
    h(
      "div",
      { class: "call-head" },
      h("span", { class: "call-name" }, str(call.name, "?")),
      args.length <= INLINE_ARGS_CHARS
        ? h("code", { class: "args" }, args)
        : lazyDetails([h("span", { class: "meta" }, `${fmtInt(args.length)} characters of arguments`)], () =>
            renderJson(call.args, 1),
          ),
    ),
    remainingFields(call, ["name", "args"]),
  );
}

/** One reasoning block: its text, with the provider's replay token beside it. */
function renderThinking(raw: unknown): HTMLElement {
  const block = obj(raw);
  return h("div", { class: "block" }, h("pre", { class: "text" }, str(block.text)), remainingFields(block, ["text"]));
}

/**
 * One streamed fragment: the kind it is, then the text it carried. A
 * fragment that carries an identifier or a name rather than text states
 * those beside the kind, so no part of the chunk goes unshown.
 */
function renderChunk(value: unknown): HTMLElement {
  const chunk = obj(value);
  const rest = remainingFields(chunk, ["kind", "delta"]);
  const delta = typeof chunk.delta === "string";
  return h(
    "div",
    { class: "chunk" },
    h(
      "div",
      { class: "chunk-head" },
      h("span", { class: "chunk-kind" }, str(chunk.kind, "?")),
      delta ? h("code", { class: "chunk-delta" }, chunk.delta as string) : null,
      delta || rest.length > 0 ? null : h("span", { class: "meta" }, "no text"),
    ),
    rest,
  );
}

/**
 * How an episode or a child ended. The word takes the colour of its
 * direction, which is the one thing about an outcome a reader reads first;
 * every other key of the outcome follows it as an ordinary field.
 */
function renderOutcome(value: unknown): HTMLElement {
  const outcome = obj(value);
  const kind = str(outcome.kind, "?");
  const role = outcomeRole({ kind } as { kind: string });
  return h(
    "div",
    { class: "outcome-value" },
    h("span", { class: `outcome-kind ${role}` }, kind),
    remainingFields(outcome, ["kind"]),
  );
}
