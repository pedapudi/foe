// Reading a rendered system prompt back into its declared sections, the
// parameters of a tool schema, and what one request header changed.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EpisodeFold } from "../src/fold.js";
import type { HeaderRow } from "../src/fold.js";
import { headerDifference, promptParts, toolParameters } from "../src/prompt.js";
import { fixture } from "./helpers.js";

/** The composition rule from crates/core: sections joined by a blank line. */
function compose(...parts: string[]): string {
  return parts.join("\n\n");
}

const INSTRUCTIONS = {
  "10-role": "You are a coding agent.",
  "20-style": "Prefer the smallest change that passes the test.",
};

const APPENDED = "# Tool instructions\n\n## read\n\nRead a file before editing it.";

test("a prompt splits into the sections its keys name, in key order", () => {
  const system = compose(INSTRUCTIONS["10-role"], INSTRUCTIONS["20-style"], APPENDED);
  const parts = promptParts(system, INSTRUCTIONS);
  assert.ok(parts.named);
  assert.deepEqual(parts.sections.map((s) => s.name), ["10-role", "20-style"]);
  assert.equal(parts.sections[0]!.text, INSTRUCTIONS["10-role"]);
  assert.equal(parts.appended, APPENDED);
});

test("keys split in lexicographic order rather than the order they were written", () => {
  const instructions = { "20-style": "Be brief.", "10-role": "You are an agent." };
  const parts = promptParts(compose(instructions["10-role"], instructions["20-style"]), instructions);
  assert.deepEqual(parts.sections.map((s) => s.name), ["10-role", "20-style"]);
});

test("a prompt of sections alone leaves nothing appended", () => {
  const parts = promptParts(compose(INSTRUCTIONS["10-role"], INSTRUCTIONS["20-style"]), INSTRUCTIONS);
  assert.ok(parts.named);
  assert.equal(parts.appended, "");
});

test("a section whose text carries blank lines still splits", () => {
  const instructions = { role: "One line.\n\nAnother line." };
  const parts = promptParts(compose(instructions.role, APPENDED), instructions);
  assert.ok(parts.named);
  assert.equal(parts.sections[0]!.text, instructions.role);
  assert.equal(parts.appended, APPENDED);
});

test("a prompt the sections do not compose is left whole", () => {
  const parts = promptParts("Something else entirely.", INSTRUCTIONS);
  assert.equal(parts.named, false);
  assert.deepEqual(parts.sections, []);
  assert.equal(parts.appended, "Something else entirely.");
});

test("a contract that declares no instruction leaves the prompt whole", () => {
  const parts = promptParts("A prompt.", {});
  assert.equal(parts.named, false);
  assert.equal(parts.appended, "A prompt.");
});

test("a tool's parameters read as rows rather than as a schema", () => {
  const rows = toolParameters({
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", description: "File path." },
      limit: { type: "integer", description: "Maximum lines." },
      globs: { type: "array", items: { type: "string" } },
    },
  });
  assert.deepEqual(rows.map((r) => r.name), ["path", "limit", "globs"]);
  assert.deepEqual(rows.map((r) => r.type), ["string", "integer", "array of string"]);
  assert.deepEqual(rows.map((r) => r.required), [true, false, false]);
  assert.equal(rows[0]!.description, "File path.");
});

test("an enumerated parameter names its options in its description", () => {
  const rows = toolParameters({ properties: { mode: { enum: ["read", "write"], description: "Access." } } });
  assert.equal(rows[0]!.type, "one of");
  assert.equal(rows[0]!.description, "Access. (read, write)");
});

test("a schema with no properties has no parameters", () => {
  assert.deepEqual(toolParameters({ type: "object" }), []);
  assert.deepEqual(toolParameters(null), []);
  assert.deepEqual(toolParameters("not a schema"), []);
});

const READ = { name: "read", description: "Read a file.", parameters: { properties: {} } };
const BASH = { name: "bash", description: "Run a command.", parameters: { properties: {} } };

test("two identical headers differ in nothing", () => {
  const one = { system: "A prompt.", tools: [READ], model: "anthropic/claude" };
  assert.deepEqual(headerDifference(one, { ...one }, {}), []);
});

test("a changed route is named first", () => {
  const lines = headerDifference(
    { system: "A prompt.", tools: [READ], model: "anthropic/claude" },
    { system: "A prompt.", tools: [READ], model: "openai-codex/gpt" },
    {},
  );
  assert.deepEqual(lines, ["route anthropic/claude became openai-codex/gpt"]);
});

test("a prompt whose declared sections held names what followed them", () => {
  // The runtime composes both prompts from the instructions the contract
  // declares, so the sections are the same and the appended tool
  // instructions are what a changed tool set moved.
  const sections = compose(INSTRUCTIONS["10-role"], INSTRUCTIONS["20-style"]);
  const lines = headerDifference(
    { system: compose(sections, APPENDED), tools: [READ], model: "m" },
    { system: compose(sections, `${APPENDED}\n\n## bash\n\nRun a command.`), tools: [READ, BASH], model: "m" },
    INSTRUCTIONS,
  );
  assert.deepEqual(lines, ["the tool instructions changed", "tool bash added"]);
});

test("a section the declared map does not carry is named as added", () => {
  const grown = { ...INSTRUCTIONS, "30-care": "Read before you edit." };
  const lines = headerDifference(
    { system: compose(INSTRUCTIONS["10-role"], INSTRUCTIONS["20-style"]), tools: [], model: "m" },
    { system: compose(grown["10-role"], grown["20-style"], grown["30-care"]), tools: [], model: "m" },
    grown,
  );
  assert.deepEqual(lines, ["instruction 30-care added"]);
});

test("an added and a removed tool are each named", () => {
  const lines = headerDifference(
    { system: "p", tools: [READ], model: "m" },
    { system: "p", tools: [BASH], model: "m" },
    {},
  );
  assert.deepEqual(lines, ["tool bash added", "tool read removed"]);
});

test("a tool whose schema changed is named as redeclared", () => {
  const lines = headerDifference(
    { system: "p", tools: [READ], model: "m" },
    { system: "p", tools: [{ ...READ, description: "Read a text file." }], model: "m" },
    {},
  );
  assert.deepEqual(lines, ["tool read redeclared"]);
});

test("a prompt change the sections cannot explain is reported as a whole", () => {
  const lines = headerDifference(
    { system: "one", tools: [], model: "m" },
    { system: "two", tools: [], model: "m" },
    {},
  );
  assert.deepEqual(lines, ["the system prompt changed"]);
});

test("the first header of an episode changed nothing", () => {
  const f = new EpisodeFold("compact", { stream: false });
  for (const ev of fixture("compact.jsonl")) f.push(ev);
  const headers = f.rows.filter((r) => r.kind === "header") as HeaderRow[];
  assert.ok(headers.length >= 1);
  assert.equal(headers[0]!.reason, "initial");
  assert.deepEqual(headers[0]!.changed, []);
});

test("a later header names what it changed", () => {
  const f = new EpisodeFold("compact", { stream: false });
  for (const ev of fixture("compact.jsonl")) f.push(ev);
  const headers = f.rows.filter((r) => r.kind === "header") as HeaderRow[];
  const later = headers.slice(1);
  assert.ok(later.length > 0, "the fixture carries a header that changed");
  for (const header of later) {
    assert.notEqual(header.reason, "initial");
    assert.ok(header.changed.length > 0, `seq ${header.seq} says what it changed`);
  }
});
