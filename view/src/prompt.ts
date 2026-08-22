// Reading a system prompt back into the parts it was composed from, and
// saying what one `request/header` changed against the one before it.
//
// The runtime composes a system prompt from the `instructions` object a
// program declares: the sections in lexicographic order of their keys,
// joined by a blank line, followed by a heading and the instruction of
// every tool that has one (docs/config.md, "instructions"). The keys are
// the names the author gave the sections, so a prompt read back through
// them has headings instead of being one wall of text.
//
// The module reads no document and is tested directly.

import type { ToolSchema } from "./types.js";
import { arr, obj, str } from "./types.js";

/** What the runtime puts between two parts of a system prompt. */
const SEPARATOR = "\n\n";

export interface PromptSection {
  /** The key the program's `instructions` object gave this section. */
  name: string;
  text: string;
}

export interface PromptParts {
  /** The declared sections, in the order the prompt renders them. */
  sections: PromptSection[];
  /**
   * What follows the last declared section: the tool instructions the
   * runtime appends, which are already written as Markdown headings.
   */
  appended: string;
  /**
   * True when at least one declared section was found where the
   * composition rule puts it. False when nothing matched, in which case
   * `appended` holds the whole prompt and the reader sees it unsplit
   * rather than mis-split.
   */
  named: boolean;
}

/**
 * Splits a rendered system prompt into its declared sections and whatever
 * follows them.
 *
 * The walk stops at the first key whose text is not where the composition
 * rule puts it, and everything from there on is `appended`. A prompt the
 * program did not compose therefore yields no section rather than a wrong
 * one, and a prompt composed from a set of instructions that has since
 * changed yields the sections it still agrees on.
 *
 * A section's text is never empty: `docs/config.md` requires each entry to
 * carry text, and an empty one would match everywhere and consume nothing.
 */
export function promptParts(system: string, instructions: Record<string, string>): PromptParts {
  const sections: PromptSection[] = [];
  let rest = system;
  for (const key of Object.keys(instructions).sort()) {
    const text = instructions[key] ?? "";
    if (text === "" || !rest.startsWith(text)) break;
    sections.push({ name: key, text });
    rest = rest.slice(text.length);
    if (rest.startsWith(SEPARATOR)) rest = rest.slice(SEPARATOR.length);
    else break;
  }
  if (sections.length === 0) return { sections: [], appended: system, named: false };
  return { sections, appended: rest, named: true };
}

export interface Parameter {
  name: string;
  /** The JSON Schema type, with an array's item type folded into it. */
  type: string;
  required: boolean;
  description: string;
}

/** The type of one property, as a phrase rather than as a schema. */
function typeOf(schema: Record<string, unknown>): string {
  const type = str(schema.type);
  if (type === "array") {
    const items = obj(schema.items);
    const inner = str(items.type);
    return inner ? `array of ${inner}` : "array";
  }
  if (!type) {
    const options = arr(schema.enum);
    if (options.length > 0) return "one of";
  }
  return type || "any";
}

/**
 * The parameters of one tool, in the order the schema lists them, so that a
 * reader sees the shape of a call rather than the JSON that declares it. A
 * schema with no `properties` object has no parameters to list.
 */
export function toolParameters(parameters: unknown): Parameter[] {
  const schema = obj(parameters);
  const properties = obj(schema.properties);
  const required = new Set(arr(schema.required).map((r) => str(r)));
  return Object.entries(properties).map(([name, value]) => {
    const property = obj(value);
    const options = arr(property.enum).map((o) => str(o)).filter(Boolean);
    const description = str(property.description);
    return {
      name,
      type: typeOf(property),
      required: required.has(name),
      description: options.length > 0 ? `${description}${description ? " " : ""}(${options.join(", ")})` : description,
    };
  });
}

export interface HeaderParts {
  system: string;
  tools: ToolSchema[];
  /** The route, as `provider/model`. */
  model: string;
}

/**
 * What one header changed against the header in effect before it. The
 * runtime writes a header only when the system prompt, the tool schemas, or
 * the route differ, so a header with `reason` other than `initial` changed
 * at least one of the three and this names which.
 *
 * The lines are ordered route, then prompt, then tools, and an empty list
 * means the two headers carry the same three parts.
 */
export function headerDifference(
  before: HeaderParts,
  after: HeaderParts,
  instructions: Record<string, string>,
): string[] {
  const lines: string[] = [];
  if (before.model !== after.model) {
    lines.push(`route ${before.model} became ${after.model}`);
  }
  lines.push(...promptDifference(before.system, after.system, instructions));
  lines.push(...toolDifference(before.tools, after.tools));
  return lines;
}

function promptDifference(before: string, after: string, instructions: Record<string, string>): string[] {
  if (before === after) return [];
  const a = promptParts(before, instructions);
  const b = promptParts(after, instructions);
  if (!a.named || !b.named) return ["the system prompt changed"];
  const lines: string[] = [];
  const byName = new Map(a.sections.map((s) => [s.name, s.text]));
  for (const section of b.sections) {
    const was = byName.get(section.name);
    if (was === undefined) lines.push(`instruction ${section.name} added`);
    else if (was !== section.text) lines.push(`instruction ${section.name} rewritten`);
    byName.delete(section.name);
  }
  for (const name of byName.keys()) lines.push(`instruction ${name} removed`);
  if (a.appended !== b.appended) lines.push("the tool instructions changed");
  return lines;
}

function toolDifference(before: ToolSchema[], after: ToolSchema[]): string[] {
  const lines: string[] = [];
  const was = new Map(before.map((t) => [str(t.name, "?"), t]));
  for (const tool of after) {
    const name = str(tool.name, "?");
    const old = was.get(name);
    if (old === undefined) lines.push(`tool ${name} added`);
    else if (JSON.stringify(old) !== JSON.stringify(tool)) lines.push(`tool ${name} redeclared`);
    was.delete(name);
  }
  for (const name of was.keys()) lines.push(`tool ${name} removed`);
  return lines;
}
