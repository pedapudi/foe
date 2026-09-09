// How a task divides into sections (src/task.ts). A task a person wrote is
// one section of prose; a task a workflow node was given carries an earlier
// node's returned value under that node's name, and the value must reach the
// reader as a value rather than as a line of braces.

import assert from "node:assert/strict";
import { test } from "node:test";
import { taskSections } from "../src/task.js";

test("a task a person wrote is one untitled section of prose", () => {
  assert.deepEqual(taskSections("say hello"), [{ title: null, text: "say hello", value: null }]);
});

test("the section holding the task itself takes no title", () => {
  const sections = taskSections("## task\n\nsay hello\n");
  assert.deepEqual(sections, [{ title: null, text: "say hello", value: null }]);
});

test("a section holding a returned value reaches the reader as that value", () => {
  const returned = '{"summary":"hello","unresolved_risks":[],"changed_paths":[]}';
  const sections = taskSections(`## task\n\nsay hello\n\n## implement-task\n\n${returned}\n`);
  assert.equal(sections.length, 2);
  assert.equal(sections[1]!.title, "implement-task");
  assert.deepEqual(sections[1]!.value, { summary: "hello", unresolved_risks: [], changed_paths: [] });
});

test("a section that only looks like a value stays prose", () => {
  const sections = taskSections("## note\n\n{ this is not JSON }\n");
  assert.equal(sections[0]!.value, null);
  assert.equal(sections[0]!.text, "{ this is not JSON }");
});

test("an empty section is not a section", () => {
  assert.deepEqual(taskSections("## task\n\n\n## other\n\nbody"), [
    { title: "other", text: "body", value: null },
  ]);
});
