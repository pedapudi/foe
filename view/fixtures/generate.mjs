// Writes the fixture logs under this directory. Each fixture is one episode
// log in the format of docs/log-format.md. The `messages` lists recorded in
// `model/request` events are written out by hand here so that the unit
// tests can compare them with the derived-messages rule.
//
// Run with `pnpm fixtures` after changing this file.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = dirname(fileURLToPath(import.meta.url));

class Log {
  constructor(startTime) {
    this.events = [];
    this.time = startTime;
  }
  /** Appends one event; seq is the position and time advances by `dt` ms. */
  ev(type, data, dt = 10) {
    this.time += dt;
    this.events.push({ seq: this.events.length, time: this.time, type, data });
    return this.events.length - 1;
  }
  write(name) {
    const text = this.events.map((e) => JSON.stringify(e)).join("\n") + "\n";
    writeFileSync(join(dir, name), text);
  }
}

const text = (t) => ({ type: "text", text: t });
const user = (...blocks) => ({ role: "user", content: blocks });
const assistant = (t, calls = []) => ({ role: "assistant", text: t, tool_calls: calls });
const tool = (call_id, name, rendered, is_error = false) => ({ role: "tool", call_id, name, rendered, is_error });

const tools = [
  { name: "read", description: "Read a file.\nReturns numbered lines.", parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
  { name: "bash", description: "Run a command.", parameters: { type: "object", properties: { cmd: { type: "string" } }, required: ["cmd"] } },
  { name: "spawn", description: "Spawn a child program.", parameters: { type: "object", properties: { program: { type: "string" }, task: { type: "string" } } } },
  { name: "block", description: "Report that the task cannot proceed.", parameters: { type: "object", properties: { code: { type: "string" }, message: { type: "string" } } } },
];

const program = (name, budget) => ({
  name,
  instructions: { charter: "You fix failing tests with the smallest change." },
  tools: tools.map((t) => t.name),
  budget,
});

const runtime = { version: "0.1.0", build: "sha256:0f0f" };
const model = { provider: "replay", model: "recorded-1" };

// Root episode: a team lead that spawns one child, survives one interrupted
// request, and completes.

function root() {
  const log = new Log(1724200000000);
  const taskText = "Fix the failing parser test.";
  const readCall = { id: "tc_01", name: "read", args: { path: "tests/parser_test.py" } };
  const readRendered = "1\timport pytest\n2\tfrom parser import parse\n3\t\n4\tdef test_parse():\n5\t    assert parse('a') == ['a']";
  const spawnCall = { id: "tc_02", name: "spawn", args: { program: "survey", task: "List the parser tests." } };
  const bashCall = { id: "tc_03", name: "bash", args: { cmd: "pytest tests/parser_test.py" } };

  log.ev("episode/start", {
    id: "ep_root",
    parent_id: null,
    fork_origin: null,
    team_id: null,
    program: program("fix-test", { model_calls: 10, tokens: 100000 }),
    identity: "sha256:aaaa",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You fix failing tests with the smallest change.", tools, model });

  const messages1 = [user(text(taskText))];
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_01", header_seq: header, consumed: [task], messages: messages1 });
  log.ev("assistant/chunk", { step: 1, request_id: "rq_01", chunk: { kind: "text", delta: "I will " } });
  log.ev("assistant/chunk", { step: 1, request_id: "rq_01", chunk: { kind: "text", delta: "read the test first." } });
  log.ev("assistant/chunk", { step: 1, request_id: "rq_01", chunk: { kind: "tool_call_start", id: "tc_01", name: "read" } });
  log.ev("assistant/chunk", { step: 1, request_id: "rq_01", chunk: { kind: "tool_call_delta", id: "tc_01", delta: JSON.stringify(readCall.args) } });
  log.ev("assistant/chunk", { step: 1, request_id: "rq_01", chunk: { kind: "tool_call_end", id: "tc_01" } });
  log.ev("assistant/message", {
    step: 1,
    request_id: "rq_01",
    text: "I will read the test first.",
    tool_calls: [readCall],
    stop: "tool",
    usage: { input: 410, output: 28, cache_read: 0 },
    interrupted: false,
  });
  log.ev("tool/result", { step: 1, call_id: "tc_01", name: "read", value: { content: readRendered.replace(/^\d+\t/gm, "") }, rendered: readRendered, is_error: false, spill: null, duration_ms: 4, synthetic: false });
  const warning = log.ev("inbox/item", { source: "system", content: [text("Budget: 8 model calls remain.")], from: null, message_id: null });

  // Step 2 is requested twice: the first attempt fails in transport before
  // any byte arrives and is retried. The warning was consumed by the first
  // attempt, so the retry's consumed list is empty.
  const messages2 = [user(text(taskText)), assistant("I will read the test first.", [readCall]), tool("tc_01", "read", readRendered), user(text("Budget: 8 model calls remain."))];
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_02", header_seq: header, consumed: [warning], messages: messages2 });
  log.ev("request/retry", { step: 2, attempt: 1, cause: "transport", delay_ms: 500 }, 200);
  log.ev("model/request", { step: 2, attempt: 2, request_id: "rq_03", header_seq: header, consumed: [], messages: messages2 }, 500);
  log.ev("assistant/chunk", { step: 2, request_id: "rq_03", chunk: { kind: "text", delta: "A survey of the tests will help." } });
  log.ev("assistant/chunk", { step: 2, request_id: "rq_03", chunk: { kind: "tool_call_start", id: "tc_02", name: "spawn" } });
  log.ev("assistant/chunk", { step: 2, request_id: "rq_03", chunk: { kind: "tool_call_delta", id: "tc_02", delta: JSON.stringify(spawnCall.args) } });
  log.ev("assistant/chunk", { step: 2, request_id: "rq_03", chunk: { kind: "tool_call_end", id: "tc_02" } });
  log.ev("assistant/message", {
    step: 2,
    request_id: "rq_03",
    text: "A survey of the tests will help.",
    tool_calls: [spawnCall],
    stop: "tool",
    usage: { input: 520, output: 31, cache_read: 400 },
    interrupted: false,
  });
  log.ev("budget/reserve", { child_id: "ep_child", reserved: { model_calls: 3, tokens: 20000 } });
  log.ev("spawn/start", { child_id: "ep_child", program: "survey", context: "fresh", call_id: "tc_02" });
  log.ev("team/roster", { member_id: "ep_child", name: "surveyor", description: "Lists parser tests.", phase: "provisioning" });
  log.ev("team/roster", { member_id: "ep_child", name: "surveyor", description: "Lists parser tests.", phase: "active" }, 300);
  log.ev("team/message", { message_id: "tm_01", from: "ep_root", to: "ep_child", content: [text("Report the count when done.")] });
  log.ev("team/delivered", { message_id: "tm_01", to: "ep_child" }, 40);
  log.ev("sandbox/denied", { pid: 4120, comm: "ruff", path: "/etc/shadow", access: "read" }, 800);
  log.ev("spawn/end", { child_id: "ep_child", outcome: { kind: "completed", value: { tests: ["test_parse"] } } }, 1500);
  log.ev("budget/release", { child_id: "ep_child", spent: { model_calls: 2, tokens: 1340 } });
  log.ev("tool/result", { step: 2, call_id: "tc_02", name: "spawn", value: { tests: ["test_parse"] }, rendered: "tests: test_parse", is_error: false, spill: null, duration_ms: 2700, synthetic: false });
  const note = log.ev("inbox/item", { source: "child", content: [text("Survey complete: 1 test.")], from: "ep_child", message_id: null });

  // Step 3 is interrupted after the tool call started; the call receives a
  // synthetic error result and the next step continues.
  const messages3 = [...messages2, assistant("A survey of the tests will help.", [spawnCall]), tool("tc_02", "spawn", "tests: test_parse"), user(text("Survey complete: 1 test."))];
  log.ev("model/request", { step: 3, attempt: 1, request_id: "rq_04", header_seq: header, consumed: [note], messages: messages3 });
  log.ev("assistant/chunk", { step: 3, request_id: "rq_04", chunk: { kind: "text", delta: "Running the test now." } });
  log.ev("assistant/chunk", { step: 3, request_id: "rq_04", chunk: { kind: "tool_call_start", id: "tc_03", name: "bash" } });
  log.ev("assistant/chunk", { step: 3, request_id: "rq_04", chunk: { kind: "tool_call_delta", id: "tc_03", delta: JSON.stringify(bashCall.args) } });
  log.ev("assistant/message", {
    step: 3,
    request_id: "rq_04",
    text: "Running the test now.",
    tool_calls: [bashCall],
    stop: "interrupted",
    usage: { input: 610, output: 14, cache_read: 520 },
    interrupted: true,
  });
  log.ev("tool/result", { step: 3, call_id: "tc_03", name: "bash", value: null, rendered: "The request was interrupted before the tool ran.", is_error: true, spill: null, duration_ms: 0, synthetic: true });

  const messages4 = [...messages3, assistant("Running the test now.", [bashCall]), tool("tc_03", "bash", "The request was interrupted before the tool ran.", true)];
  log.ev("model/request", { step: 4, attempt: 1, request_id: "rq_05", header_seq: header, consumed: [], messages: messages4 });
  log.ev("assistant/chunk", { step: 4, request_id: "rq_05", chunk: { kind: "text", delta: "Done. The test passes." } });
  log.ev("assistant/message", {
    step: 4,
    request_id: "rq_05",
    text: "Done. The test passes.",
    tool_calls: [],
    stop: "end",
    usage: { input: 680, output: 9, cache_read: 600 },
    interrupted: false,
  });
  log.ev("episode/end", { outcome: { kind: "completed", value: "Done. The test passes." } });
  return log;
}

// Child episode: spawned by the root, a team member, receives a peer message.

function child() {
  const log = new Log(1724200001000);
  const taskText = "List the parser tests.";
  log.ev("episode/start", {
    id: "ep_child",
    parent_id: "ep_root",
    fork_origin: null,
    team_id: "ep_root",
    program: program("survey", { model_calls: 3, tokens: 20000 }),
    identity: "sha256:bbbb",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You survey a repository and report.", tools: tools.slice(0, 2), model });
  const peer = log.ev("inbox/item", { source: "peer", content: [text("Report the count when done.")], from: "ep_root", message_id: "tm_01" });
  const grep = { id: "tc_10", name: "bash", args: { cmd: "grep -rn 'def test_' tests/" } };
  const grepOut = "tests/parser_test.py:4:def test_parse():";
  const messages1 = [user(text(taskText), text("Report the count when done."))];
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_10", header_seq: header, consumed: [task, peer], messages: messages1 });
  log.ev("assistant/message", { step: 1, request_id: "rq_10", text: "", tool_calls: [grep], stop: "tool", usage: { input: 300, output: 20, cache_read: 0 }, interrupted: false });
  log.ev("tool/result", { step: 1, call_id: "tc_10", name: "bash", value: { exit: 0, stdout: grepOut }, rendered: grepOut, is_error: false, spill: null, duration_ms: 12, synthetic: false });
  log.ev("workflow/node-end", { node: "survey", status: "ok" });
  const messages2 = [...messages1, assistant("", [grep]), tool("tc_10", "bash", grepOut)];
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_11", header_seq: header, consumed: [], messages: messages2 });
  log.ev("assistant/message", { step: 2, request_id: "rq_11", text: "One test: test_parse.", tool_calls: [], stop: "end", usage: { input: 350, output: 8, cache_read: 300 }, interrupted: false });
  log.ev("episode/end", { outcome: { kind: "completed", value: { tests: ["test_parse"] } } });
  return log;
}

// Fork episode: seeded from the root at seq 12, which copies root events
// 1 to 11 (task, header, first request, chunks, message, result, warning).

function fork(rootLog) {
  const boundary = 12;
  const log = new Log(1724200005000);
  const start = rootLog.events[0].data;
  log.ev("episode/start", { ...start, id: "ep_fork", fork_origin: { episode_id: "ep_root", seq: boundary } });
  for (const e of rootLog.events.slice(1, boundary)) log.ev(e.type, e.data, 0);
  log.ev("seed/end", {});
  const taskText = start.task;
  const readCall = rootLog.events.find((e) => e.type === "assistant/message").data.tool_calls[0];
  const readRendered = rootLog.events.find((e) => e.type === "tool/result").data.rendered;
  const header = 2;
  const messages2 = [user(text(taskText)), assistant("I will read the test first.", [readCall]), tool("tc_01", "read", readRendered), user(text("Budget: 8 model calls remain."))];
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_20", header_seq: header, consumed: [11], messages: messages2 });
  const block = { id: "tc_20", name: "block", args: { code: "missing-capability", message: "The edit tool is not available." } };
  log.ev("assistant/message", { step: 2, request_id: "rq_20", text: "The program lacks an edit tool.", tool_calls: [block], stop: "tool", usage: { input: 500, output: 25, cache_read: 400 }, interrupted: false });
  log.ev("tool/result", { step: 2, call_id: "tc_20", name: "block", value: { code: "missing-capability" }, rendered: "blocked: missing-capability", is_error: false, spill: null, duration_ms: 0, synthetic: false });
  log.ev("episode/end", { outcome: { kind: "blocked", code: "missing-capability", message: "The edit tool is not available." } });
  return log;
}

// Compacted episode: three steps, then a compaction before the fourth
// request that keeps only the third step. The continuation message is
// written out by hand here, in the form docs/compaction.md specifies, so
// that the derived-messages test checks the bundle's rendering of it.

function compact() {
  const log = new Log(1724200009000);
  const taskText = "Rename the helper and update its callers.";
  const grepCall = { id: "tc_01", name: "grep", args: { pattern: "helper(" } };
  const grepOut = "src/a.py:3:helper()\nsrc/b.py:9:helper()";
  const readCall = { id: "tc_02", name: "read", args: { path: "src/a.py" } };
  const readOut = "1\tdef helper():\n2\t    return 1";
  const editCall = { id: "tc_03", name: "edit", args: { path: "src/a.py", edits: [{ find: "helper", replace: "load_one" }] } };
  const editOut = "+1 -1";
  const narrative = "## Goal\nRename `helper` and update its callers.\n\n## Progress\nFound two callers with grep; read src/a.py.\n\n## Decisions\nRename to `load_one`.\n\n## Open items\nsrc/b.py is not yet edited.\n\n## Next step\nEdit src/b.py.";
  const state = {
    task: taskText,
    done_when: "a turn with no tool calls",
    outstanding_findings: [],
    files: { read: ["src/a.py"], written: [], edited: [] },
    children: [],
    covered: { first_seq: 1, last_seq: 8 },
    budget_remaining: { model_calls: 7, tokens: 95000 },
  };
  const continuation = [
    "## Continuation state",
    "",
    "covered: seq 1 to 8",
    "done_when: a turn with no tool calls",
    "outstanding_findings: (none)",
    "files_read:\n- src/a.py",
    "files_written: (none)",
    "files_edited: (none)",
    "children: (none)",
    "budget_remaining: model_calls 7, tokens 95000, seconds unlimited",
    "",
    "## Summary",
    "",
    narrative,
  ].join("\n");
  const summaryTools = tools.slice(0, 2).concat([{ name: "edit", description: "Edit a file.", parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } }]);

  log.ev("episode/start", {
    id: "ep_compact",
    parent_id: null,
    fork_origin: null,
    team_id: null,
    program: { ...program("rename-helper", { model_calls: 10, tokens: 100000 }), context: { compact: true, window_tokens: 4000, reserve_tokens: 500, keep_recent_tokens: 40 } },
    identity: "sha256:cccc",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You fix failing tests with the smallest change.", tools: summaryTools, model });
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_0001", header_seq: header, consumed: [task], messages: [user(text(taskText))] });
  log.ev("assistant/message", { step: 1, request_id: "rq_0001", text: "I will grep for callers.", tool_calls: [grepCall], stop: "tool", usage: { input: 900, output: 12, cache_read: 0 }, interrupted: false });
  log.ev("tool/result", { step: 1, call_id: "tc_01", name: "grep", value: { matches: 2 }, rendered: grepOut, is_error: false, spill: null, duration_ms: 9, synthetic: false });
  const messages2 = [user(text(taskText)), assistant("I will grep for callers.", [grepCall]), tool("tc_01", "grep", grepOut)];
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_0002", header_seq: header, consumed: [], messages: messages2 });
  log.ev("assistant/message", { step: 2, request_id: "rq_0002", text: "Reading a.", tool_calls: [readCall], stop: "tool", usage: { input: 1400, output: 10, cache_read: 900 }, interrupted: false });
  log.ev("tool/result", { step: 2, call_id: "tc_02", name: "read", value: { content: "def helper():\n    return 1" }, rendered: readOut, is_error: false, spill: null, duration_ms: 3, synthetic: false });
  const messages3 = [...messages2, assistant("Reading a.", [readCall]), tool("tc_02", "read", readOut)];
  const kept = log.ev("model/request", { step: 3, attempt: 1, request_id: "rq_0003", header_seq: header, consumed: [], messages: messages3 });
  log.ev("assistant/message", { step: 3, request_id: "rq_0003", text: "Editing a.", tool_calls: [editCall], stop: "tool", usage: { input: 1900, output: 30, cache_read: 1400 }, interrupted: false });
  log.ev("tool/result", { step: 3, call_id: "tc_03", name: "edit", value: { added: 1, removed: 1 }, rendered: editOut, is_error: false, spill: null, duration_ms: 5, synthetic: false });

  // Step 4 begins with a compaction: the projection of the next request
  // crosses the threshold, the oldest two steps are summarized through a
  // request with a cmp_ id under its own header, and the header returns.
  log.ev("compaction/start", { step: 4, covered: { first_seq: 1, last_seq: 8 }, trigger: "threshold", projected_tokens: 3982, reserved: { model_calls: 7, tokens: 95000 } });
  const summaryHeader = log.ev("request/header", { reason: "change", system: "A coding agent's conversation is being condensed.", tools: [], model });
  const transcript = `# Transcript\n\n[user]\n${taskText}\n\n[assistant]\nI will grep for callers.\n[call grep {"pattern":"helper("}]\n\n[result grep]\n${grepOut}\n\n[assistant]\nReading a.\n[call read {"path":"src/a.py"}]\n\n[result read]\n${readOut}`;
  const summaryRequest = log.ev("model/request", { step: 4, attempt: 1, request_id: "cmp_0004", header_seq: summaryHeader, consumed: [], messages: [user(text(transcript))] });
  log.ev("assistant/chunk", { step: 4, request_id: "cmp_0004", chunk: { kind: "text", delta: narrative } });
  log.ev("assistant/message", { step: 4, request_id: "cmp_0004", text: narrative, tool_calls: [], stop: "end", usage: { input: 700, output: 80, cache_read: 0 }, interrupted: false });
  log.ev("compaction/summary", { step: 4, summary: narrative, state, first_kept_seq: kept, summary_request_seq: summaryRequest });
  log.ev("compaction/end", { step: 4, ok: true, usage: { input: 700, output: 80, cache_read: 0 }, active_estimate: 260 });
  const restored = log.ev("request/header", { reason: "change", system: "You fix failing tests with the smallest change.", tools: summaryTools, model });
  const messages4 = [user(text(taskText)), user(text(continuation)), assistant("Editing a.", [editCall]), tool("tc_03", "edit", editOut)];
  log.ev("model/request", { step: 4, attempt: 1, request_id: "rq_0005", header_seq: restored, consumed: [], messages: messages4 });
  log.ev("assistant/message", { step: 4, request_id: "rq_0005", text: "Done.", tool_calls: [], stop: "end", usage: { input: 640, output: 4, cache_read: 0 }, interrupted: false });
  log.ev("episode/end", { outcome: { kind: "completed", value: "Done." } });
  return log;
}

const r = root();
r.write("root.jsonl");
child().write("child.jsonl");
fork(r).write("fork.jsonl");
compact().write("compact.jsonl");
