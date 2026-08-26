// Writes the fixture logs under this directory. Each fixture is one episode
// log in the format of docs/log-format.md. The `messages` lists recorded in
// `model/request` events are written out by hand here so that the unit
// tests can compare them with the derived-messages rule.
//
// The four workflow fixtures are an exception: the foe runtime writes them.
// `workflowRun` below assembles a configuration, a scripted model program,
// and a scripted verification program, runs the `foe` binary over them, and
// copies the logs here with the machine's own paths replaced. A declared
// graph has rules a hand-written log would satisfy only by accident, so the
// fixtures come from the runtime that enforces them.
//
// Run with `pnpm fixtures` after changing this file.
//
// `retries-exhausted.jsonl` is not written here. It is a log recorded from a
// run against a live model whose every attempt failed in transport, kept
// verbatim so that the tests read the shapes a real provider failure
// produces rather than shapes chosen to make them pass.

import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
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
    program: program("fix-test", { model_calls: 10, input_tokens: 80000, output_tokens: 20000 }),
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
  log.ev("tool/result", { step: 1, call_id: "tc_01", name: "read", value: { content: readRendered.replace(/^\d+\t/gm, "") }, rendered: readRendered, is_error: false, spill: null, subject: "tests/parser_test.py lines 1–5 of 5", duration_ms: 4, synthetic: false });
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
  log.ev("budget/reserve", { child_id: "ep_child", reserved: { model_calls: 3, input_tokens: 16000, output_tokens: 4000 } });
  log.ev("spawn/start", { child_id: "ep_child", program: "survey", context: "fresh", call_id: "tc_02" });
  log.ev("team/roster", { member_id: "ep_child", name: "surveyor", description: "Lists parser tests.", phase: "provisioning" });
  log.ev("team/roster", { member_id: "ep_child", name: "surveyor", description: "Lists parser tests.", phase: "active" }, 300);
  log.ev("team/message", { message_id: "tm_01", from: "ep_root", to: "ep_child", content: [text("Report the count when done.")] });
  log.ev("team/delivered", { message_id: "tm_01", to: "ep_child" }, 40);
  log.ev("sandbox/denied", { pid: 4120, comm: "ruff", path: "/etc/shadow", access: "read" }, 800);
  log.ev("spawn/end", { child_id: "ep_child", outcome: { kind: "completed", value: { tests: ["test_parse"] } } }, 1500);
  log.ev("budget/release", { child_id: "ep_child", spent: { model_calls: 2, input_tokens: 1300, output_tokens: 40 } });
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
  log.ev("tool/result", { step: 3, call_id: "tc_03", name: "bash", value: null, rendered: "The request was interrupted before the tool ran.", is_error: true, spill: null, subject: "bash: the request was interrupted before the tool ran", duration_ms: 0, synthetic: true });

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
    program: program("survey", { model_calls: 3, input_tokens: 16000, output_tokens: 4000 }),
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
  log.ev("tool/result", { step: 1, call_id: "tc_10", name: "bash", value: { exit: 0, stdout: grepOut }, rendered: grepOut, is_error: false, spill: null, subject: "grep -rn TODO src · exit 0 in 0.01s", duration_ms: 12, synthetic: false });
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
    budget_remaining: { model_calls: 7, input_tokens: 76000, output_tokens: 19000 },
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
    "budget_remaining: model_calls 7, input_tokens 76000, output_tokens 19000, seconds unlimited",
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
    program: { ...program("rename-helper", { model_calls: 10, input_tokens: 80000, output_tokens: 20000 }), context: { compact: true, window_tokens: 4000, reserve_tokens: 500, keep_recent_tokens: 40 } },
    identity: "sha256:cccc",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You fix failing tests with the smallest change.", tools: summaryTools, model });
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_0001", header_seq: header, consumed: [task], messages: [user(text(taskText))] });
  log.ev("assistant/message", { step: 1, request_id: "rq_0001", text: "I will grep for callers.", tool_calls: [grepCall], stop: "tool", usage: { input: 900, output: 12, cache_read: 0 }, interrupted: false });
  log.ev("tool/result", { step: 1, call_id: "tc_01", name: "grep", value: { matches: 2 }, rendered: grepOut, is_error: false, spill: null, subject: "2 matches in 1 files under .", duration_ms: 9, synthetic: false });
  const messages2 = [user(text(taskText)), assistant("I will grep for callers.", [grepCall]), tool("tc_01", "grep", grepOut)];
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_0002", header_seq: header, consumed: [], messages: messages2 });
  log.ev("assistant/message", { step: 2, request_id: "rq_0002", text: "Reading a.", tool_calls: [readCall], stop: "tool", usage: { input: 1400, output: 10, cache_read: 900 }, interrupted: false });
  log.ev("tool/result", { step: 2, call_id: "tc_02", name: "read", value: { content: "def helper():\n    return 1" }, rendered: readOut, is_error: false, spill: null, subject: "src/helper.py lines 1–2 of 2", duration_ms: 3, synthetic: false });
  const messages3 = [...messages2, assistant("Reading a.", [readCall]), tool("tc_02", "read", readOut)];
  const kept = log.ev("model/request", { step: 3, attempt: 1, request_id: "rq_0003", header_seq: header, consumed: [], messages: messages3 });
  log.ev("assistant/message", { step: 3, request_id: "rq_0003", text: "Editing a.", tool_calls: [editCall], stop: "tool", usage: { input: 1900, output: 30, cache_read: 1400 }, interrupted: false });
  log.ev("tool/result", { step: 3, call_id: "tc_03", name: "edit", value: { added: 1, removed: 1 }, rendered: editOut, is_error: false, spill: null, subject: "src/helper.py: 1 edit(s), +1 -1 lines", duration_ms: 5, synthetic: false });

  // Step 4 begins with a compaction: the projection of the next request
  // crosses the threshold, the oldest two steps are summarized through a
  // request with a cmp_ id under its own header, and the header returns.
  log.ev("compaction/start", { step: 4, covered: { first_seq: 1, last_seq: 8 }, trigger: "threshold", projected_tokens: 3982, reserved: { model_calls: 7, input_tokens: 76000, output_tokens: 19000 } });
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

// A parent with two children, for the trajectory pane. The surveyor
// outlives most of the parent's own run: the parent spawns it at 2 s, keeps
// calling tools while it is alive, and it ends 1 s before the parent does.
// The writer, whose log is `rich.jsonl`, runs inside the same window.

function overlapParent() {
  const log = new Log(1730000000000);
  const taskText = "Survey the crates and summarize.";
  const spawnCall = { id: "tc_p1", name: "spawn", args: { program: "surveyor", task: "Read every crate manifest." } };
  const writeCall = { id: "tc_p3", name: "spawn", args: { program: "writer", task: "Explain the budget rule and tighten the parser." } };
  const readCall = { id: "tc_p2", name: "read", args: { path: "Cargo.toml" } };
  log.ev("episode/start", {
    id: "ep_over_parent",
    parent_id: null,
    fork_origin: null,
    team_id: null,
    program: program("lead", { model_calls: 20, input_tokens: 160000, output_tokens: 40000 }),
    identity: "sha256:cccc",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You lead a survey.", tools, model });
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_p1", header_seq: header, consumed: [task], messages: [user(text(taskText))] }, 1000);
  log.ev("assistant/message", { step: 1, request_id: "rq_p1", text: "Spawning a surveyor.", tool_calls: [spawnCall], stop: "tool", usage: { input: 400, output: 20, cache_read: 0 }, interrupted: false }, 900);
  log.ev("budget/reserve", { child_id: "ep_over_child", reserved: { model_calls: 8, input_tokens: 64000, output_tokens: 16000 } });
  log.ev("spawn/start", { child_id: "ep_over_child", program: "surveyor", context: "fresh", call_id: "tc_p1" }, 90);
  // The parent keeps working while the child runs.
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_p2", header_seq: header, consumed: [], messages: [user(text(taskText))] }, 500);
  log.ev("assistant/message", { step: 2, request_id: "rq_p2", text: "Reading the workspace manifest.", tool_calls: [readCall, writeCall], stop: "tool", usage: { input: 500, output: 18, cache_read: 400 }, interrupted: false }, 700);
  log.ev("tool/result", { step: 2, call_id: "tc_p2", name: "read", value: { path: "Cargo.toml" }, rendered: "1\t[workspace]\n2\tresolver = \"2\"", is_error: false, spill: null, subject: "Cargo.toml lines 1–2 of 2", duration_ms: 1400, synthetic: false }, 1400);
  log.ev("budget/reserve", { child_id: "ep_rich", reserved: { model_calls: 6, input_tokens: 48000, output_tokens: 12000 } });
  log.ev("spawn/start", { child_id: "ep_rich", program: "writer", context: "fresh", call_id: "tc_p3" }, 90);
  log.ev("spawn/end", { child_id: "ep_rich", outcome: { kind: "completed", value: "The parser now propagates the error." } }, 2200);
  log.ev("budget/release", { child_id: "ep_rich", spent: { model_calls: 2, input_tokens: 2200, output_tokens: 429 } });
  log.ev("tool/result", { step: 2, call_id: "tc_p3", name: "spawn", value: "The parser now propagates the error.", rendered: "The parser now propagates the error.", is_error: false, spill: null, duration_ms: 2290, synthetic: false });
  log.ev("spawn/end", { child_id: "ep_over_child", outcome: { kind: "completed", value: { crates: 8 } } }, 3800);
  log.ev("budget/release", { child_id: "ep_over_child", spent: { model_calls: 3, input_tokens: 9000, output_tokens: 100 } });
  log.ev("tool/result", { step: 1, call_id: "tc_p1", name: "spawn", value: { crates: 8 }, rendered: "crates: 8", is_error: false, spill: null, duration_ms: 8000, synthetic: false });
  log.ev("model/request", { step: 3, attempt: 1, request_id: "rq_p3", header_seq: header, consumed: [], messages: [user(text(taskText))] }, 200);
  log.ev("assistant/message", { step: 3, request_id: "rq_p3", text: "Eight crates.", tool_calls: [], stop: "end", usage: { input: 900, output: 6, cache_read: 800 }, interrupted: false }, 700);
  log.ev("episode/end", { outcome: { kind: "completed", value: "Eight crates." } }, 100);
  return log;
}

function overlapChild() {
  // Starts 2 s after its parent and ends 1 s before it.
  const log = new Log(1730000002000);
  const taskText = "Read every crate manifest.";
  const call = { id: "tc_c1", name: "bash", args: { cmd: "ls crates" } };
  log.ev("episode/start", {
    id: "ep_over_child",
    parent_id: "ep_over_parent",
    fork_origin: null,
    team_id: null,
    program: program("surveyor", { model_calls: 8, input_tokens: 64000, output_tokens: 16000 }),
    identity: "sha256:dddd",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You read manifests.", tools: tools.slice(0, 2), model });
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_c1", header_seq: header, consumed: [task], messages: [user(text(taskText))] }, 800);
  log.ev("assistant/message", { step: 1, request_id: "rq_c1", text: "", tool_calls: [call], stop: "tool", usage: { input: 200, output: 12, cache_read: 0 }, interrupted: false }, 600);
  log.ev("tool/result", { step: 1, call_id: "tc_c1", name: "bash", value: { exit: 0 }, rendered: "cli\ncode\ncore\nlog", is_error: false, spill: null, subject: "ls crates · exit 0 in 0.01s", duration_ms: 2600, synthetic: false }, 2600);
  log.ev("compaction/start", { step: 2, covered: { first_seq: 1, last_seq: 5 }, trigger: "threshold", projected_tokens: 74000, reserved: { model_calls: 6, input_tokens: 56000, output_tokens: 14000 } }, 400);
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_c2", header_seq: header, consumed: [], messages: [user(text(taskText))] }, 900);
  log.ev("request/retry", { step: 2, attempt: 1, cause: "rate-limit", delay_ms: 1000 }, 300);
  log.ev("model/request", { step: 2, attempt: 2, request_id: "rq_c3", header_seq: header, consumed: [], messages: [user(text(taskText))] }, 1000);
  log.ev("assistant/message", { step: 2, request_id: "rq_c3", text: "Eight crates.", tool_calls: [], stop: "end", usage: { input: 260, output: 6, cache_read: 200 }, interrupted: false }, 600);
  log.ev("episode/end", { outcome: { kind: "completed", value: { crates: 8 } } }, 100);
  return log;
}

// The writer the parent above spawns, whose one assistant turn exercises
// every rich rendering the conversation pane has: Markdown with a table and
// a fenced Rust block, inline and display mathematics, an `edit` result
// carrying a unified diff, and a `read` result carrying numbered source.

function rich() {
  const log = new Log(1730000004200);
  const taskText = "Explain the budget rule and tighten the parser.";
  const answer = [
    "# Budget",
    "",
    "An episode stops when any limit is reached. The share a child receives is",
    "$r = b_{\\text{parent}} / n$, and the parent keeps the rest.",
    "",
    "$$\\sum_{i=1}^{n} r_i \\le b_{\\text{parent}}$$",
    "",
    "| limit | unit | checked |",
    "|:---|:---:|---:|",
    "| `model_calls` | calls | before each request |",
    "| `input_tokens` | tokens | counted after each response |",
    "| `output_tokens` | tokens | capped before and counted after each request |",
    "",
    "The check itself is one comparison:",
    "",
    "```rust",
    "/// Returns the remainder, or None when the limit is reached.",
    "fn remaining(spent: u64, limit: Option<u64>) -> Option<u64> {",
    "    let limit = limit?;",
    "    limit.checked_sub(spent).filter(|left| *left > 0)",
    "}",
    "```",
    "",
    "See [the log format](docs/log-format.md) for the events, and note that",
    "**every** limit is *declared* rather than inferred.",
    "",
    "> A limit with no declared value is unlimited.",
    "",
    "- read the manifest",
    "- tighten the parser",
    "  - add the error path",
    "- run the tests",
  ].join("\n");
  const editCall = { id: "tc_r1", name: "edit", args: { path: "src/parser.rs", edits: [{ find: "lexer.run()", replace: "lexer.run()?" }] } };
  const readCall = { id: "tc_r2", name: "read", args: { path: "src/parser.rs", offset: 1, limit: 6 } };
  const diff = [
    "edited src/parser.rs: 2 edit(s), +2 -2 lines",
    "--- a/src/parser.rs",
    "+++ b/src/parser.rs",
    "@@ -10,6 +10,6 @@",
    " fn parse(input: &str) -> Result<Ast> {",
    "     let mut lexer = Lexer::new(input);",
    "-    let tree = lexer.run();",
    "-    Ok(tree)",
    "+    let tree = lexer.run()?;",
    "+    Ok(tree.finish())",
    " }",
    "",
  ].join("\n");
  const source = [
    "1\tuse crate::ast::Ast;",
    "2\t",
    "3\t/// Parses one document. // 42 bytes at most",
    "4\tpub fn parse(input: &str) -> Result<Ast> {",
    "5\t    let mut lexer = Lexer::new(input);",
    "6\t    let tree = lexer.run()?;",
    "",
  ].join("\n");

  log.ev("episode/start", {
    id: "ep_rich",
    parent_id: "ep_over_parent",
    fork_origin: null,
    team_id: null,
    program: program("writer", { model_calls: 6, input_tokens: 48000, output_tokens: 12000 }),
    identity: "sha256:eeee",
    task: taskText,
    runtime,
    sandbox: { mode: "best-effort", landlock_abi: 7 },
  });
  const task = log.ev("inbox/item", { source: "task", content: [text(taskText)], from: null, message_id: null });
  const header = log.ev("request/header", { reason: "initial", system: "You explain and edit.", tools, model });
  log.ev("model/request", { step: 1, attempt: 1, request_id: "rq_r1", header_seq: header, consumed: [task], messages: [user(text(taskText))] });
  log.ev("assistant/message", {
    step: 1,
    request_id: "rq_r1",
    text: answer,
    tool_calls: [editCall, readCall],
    stop: "tool",
    usage: { input: 800, output: 420, cache_read: 0 },
    interrupted: false,
  }, 1200);
  log.ev("tool/result", { step: 1, call_id: "tc_r1", name: "edit", value: { path: "src/parser.rs", added: 2, removed: 2 }, rendered: diff, is_error: false, spill: null, subject: "src/collate.py: 1 edit(s), +1 -1 lines", duration_ms: 21, synthetic: false });
  log.ev("tool/result", { step: 1, call_id: "tc_r2", name: "read", value: { path: "src/parser.rs", offset: 1, shown: 6, total_lines: 40, truncated: true }, rendered: source, is_error: false, spill: null, subject: "src/collate.py lines 1–18 of 18", duration_ms: 3, synthetic: false });
  const messages2 = [user(text(taskText)), assistant(answer, [editCall, readCall]), tool("tc_r1", "edit", diff), tool("tc_r2", "read", source)];
  log.ev("model/request", { step: 2, attempt: 1, request_id: "rq_r2", header_seq: header, consumed: [], messages: messages2 }, 300);
  log.ev("assistant/message", { step: 2, request_id: "rq_r2", text: "The parser now propagates the error.", tool_calls: [], stop: "end", usage: { input: 1400, output: 9, cache_read: 800 }, interrupted: false }, 500);
  log.ev("episode/end", { outcome: { kind: "completed", value: "The parser now propagates the error." } });
  return log;
}

// The workflow fixtures, written by the runtime rather than by hand.
//
// The graph surveys a Python package for TODO comments, asks one model node
// for a plan, applies it in a second model node, and checks the result. It
// exercises what the workflow view has to draw: a node that fires twice, a
// choice point whose labels include one the model never chose and one with
// no successors, a node no firing ever reached, and a tool failure that a
// recovery decision retried.

/** Absolute path of the release binary that writes the logs. */
const BINARY = join(dir, "..", "..", "target", "release", "foe");

/** Paths written into the logs in place of the machine's own. */
const PROJECT = "/home/user/project";
const TOOLS = "/home/user/tools";

/** A scripted model program: one model/request line in, model/chunk lines out. */
const MODEL_PROGRAM = `#!/usr/bin/env python3
"""Answers model requests for the workflow fixture.

The answer is chosen from the tools the request offers and from a counter
kept beside this file, so one run of the graph gives the same answers every
time. A request offering \`recover\` is a recovery decision; one offering
\`return\` is the propose node; anything else is the apply node.
"""

import json
import os
import sys

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


def bump(name):
    path = os.path.join(STATE, name)
    n = int(open(path).read().strip()) if os.path.exists(path) else 0
    open(path, "w").write(str(n + 1))
    return n + 1


def emit(rid, chunk):
    sys.stdout.write(json.dumps({"type": "model/chunk", "request_id": rid, "chunk": chunk}) + "\\n")


def call(rid, cid, name, args):
    emit(rid, {"kind": "tool_call_start", "id": cid, "name": name})
    emit(rid, {"kind": "tool_call_delta", "id": cid, "delta": json.dumps(args)})
    emit(rid, {"kind": "tool_call_end", "id": cid})


def say(rid, body):
    for word in body.split(" "):
        emit(rid, {"kind": "text", "delta": word + " "})


def done(rid, stop, given, produced):
    emit(rid, {"kind": "done", "stop": stop,
               "usage": {"input": given, "output": produced, "cache_read": 0}})


req = json.loads(sys.stdin.readline())
rid = req["request_id"]
offered = [t["name"] for t in req.get("tools") or []]
messages = req.get("messages") or []

if "recover" in offered:
    n = bump("recover")
    failed = json.dumps(messages).split("Node \`", 1)[1].split("\`", 1)[0]
    call(rid, "tc_recover_%d" % n, "recover", {"action": "retry", "node": failed})
    done(rid, "tool", 1180, 96)
elif "return" in offered:
    n = bump("propose")
    plan = {"file": "src/collate.py",
            "todo": "TODO: the pass over the manifest is quadratic",
            "change": "widen the survey before choosing" if n == 1
                      else "index the manifest by key and look each row up once",
            "branch": "widen" if n == 1 else "apply"}
    say(rid, "The survey names one TODO that a local change resolves.")
    call(rid, "tc_return_%d" % n, "return", {"value": plan})
    done(rid, "tool", 2400 + 900 * n, 180)
elif len(messages) <= 1:
    call(rid, "tc_read_%d" % bump("read"), "read", {"path": "src/collate.py"})
    done(rid, "tool", 3100, 74)
else:
    say(rid, "Indexed the manifest by key so each row is looked up once, and removed the TODO comment.")
    done(rid, "end", 4260, 210)
`;

/** A scripted verification program: one finding on its first run, none after. */
const CHECK_PROGRAM = `#!/usr/bin/env python3
"""Stands in for a project's checker in the workflow fixture.

Prints one finding and exits 1 the first time it runs and prints none and
exits 0 after that, so the graph meets one tool failure and the recovery
decision that retries it.
"""

import os
import sys

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "check")
n = int(open(path).read().strip()) if os.path.exists(path) else 0
open(path, "w").write(str(n + 1))

if n == 0:
    sys.stderr.write("src/collate.py:41: undefined name 'index'\\n")
    sys.exit(1)

sys.stdout.write("checked src: no findings\\n")
`;

/** The package the graph surveys. Both TODO comments are real matches. */
const SOURCE = `"""Collates manifest rows into one table."""


def collate(manifest, rows):
    # TODO: the pass over the manifest is quadratic
    out = []
    for row in rows:
        for entry in manifest:
            if entry["key"] == row["key"]:
                out.append({**entry, **row})
    return out


def summarize(table):
    # TODO: report the widest column so the caller can pad
    return {"rows": len(table)}
`;

/** The configuration the runtime resolves, with `root` as every path's base. */
function workflowConfig(root) {
  const child = (name, role, toolNames, grants, calls, extra = {}) => ({
    name,
    instructions: { role },
    tools: toolNames,
    grants,
    budget: { model_calls: calls },
    ...extra,
  });
  return {
    version: 3,
    name: "survey-propose-apply",
    instructions: { role: "The ceiling of a declared workflow. Each model node carries its own instructions." },
    tools: ["read", "grep", "edit", "bash", "check"],
    tool_defs: {
      check: {
        exec: `${root}/check`,
        cwd: `${root}/project`,
        description: "Runs the project's checker over src and prints one finding per line; prints nothing when clean.",
      },
    },
    grants: { read: [`${root}/project`], write: [`${root}/project/src`] },
    budget: { model_calls: 30, input_tokens: 240000, output_tokens: 60000, max_episodes: 6 },
    sandbox: { mode: "off" },
    model: { provider: "exec", model: "scripted-answers", exec: `${root}/model` },
    workflow: {
      nodes: {
        manifest: { tool: "grep", args: { pattern: "^def ", glob: "*.py" } },
        survey: { tool: "grep", args: { pattern: "TODO", glob: "*.py" }, follows: ["manifest"], max_fires: 2 },
        propose: {
          model: child(
            "propose",
            "You receive the task of this run and the TODO comments found in a Python package, one grep match per line. Choose the one TODO that fits the task and that a small, local change can resolve, read the surrounding code, and return a plan: the file, the TODO, and the change to make. Return the branch `nothing` when no TODO is safe to resolve without more context.",
            ["read", "grep"],
            { read: [`${root}/project`] },
            8,
            {
              done_when: {
                returns: {
                  type: "object",
                  properties: { file: { type: "string" }, todo: { type: "string" }, change: { type: "string" } },
                  required: ["file", "todo", "change"],
                },
              },
            },
          ),
          follows: ["task", "survey"],
          branches: { apply: ["apply"], widen: ["survey"], abandon: ["record_abandonment"], nothing: [] },
          max_fires: 2,
        },
        apply: {
          model: child(
            "apply",
            "You receive a plan naming a file, a TODO comment in it, and the change that resolves it. Make that change and remove the comment. Run the tests that cover the file when there are any. Finish with one sentence stating what changed.",
            ["read", "edit", "bash"],
            { read: [`${root}/project`], write: [`${root}/project/src`] },
            12,
          ),
          follows: ["propose"],
          max_fires: 2,
        },
        verify_change: { tool: "check", args: { args: [] }, follows: ["apply"], max_fires: 2, terminal: true },
        record_abandonment: {
          tool: "bash",
          args: { command: "echo 'the run abandoned every TODO it surveyed'" },
        },
      },
      recovery: { max_interventions: 2 },
    },
    task: "Resolve one TODO comment in src.",
  };
}

/**
 * Runs the graph and writes its four logs. Without the release binary the
 * logs already here are left alone, so the rest of the fixtures regenerate
 * on a machine with no Rust toolchain.
 */
function workflowRun() {
  if (!existsSync(BINARY)) {
    console.log(`${BINARY} is absent; the workflow fixtures are left as they are`);
    console.log("build it with `cargo build --release --bin foe` to regenerate them");
    return;
  }
  const root = mkdtempSync(join(tmpdir(), "foe-workflow-fixture-"));
  try {
    mkdirSync(join(root, "project", "src"), { recursive: true });
    mkdirSync(join(root, "state"), { recursive: true });
    writeFileSync(join(root, "project", "src", "collate.py"), SOURCE);
    for (const [name, body] of [["model", MODEL_PROGRAM], ["check", CHECK_PROGRAM]]) {
      writeFileSync(join(root, name), body);
      chmodSync(join(root, name), 0o755);
    }
    const config = join(root, "config.json");
    writeFileSync(config, JSON.stringify(workflowConfig(root), null, 2));
    const logs = join(root, "logs");
    const run = spawnSync(BINARY, ["--config", config, "--log-dir", logs, "--headless", "--no-open"], {
      encoding: "utf8",
    });
    if (run.status !== 0) {
      throw new Error(`${BINARY} exited with ${run.status}: ${run.stderr}`);
    }
    const outcome = JSON.parse(run.stdout.trim().split("\n").pop());
    if (outcome.kind !== "completed") {
      throw new Error(`the graph ended ${outcome.kind}: ${JSON.stringify(outcome)}`);
    }
    // The logs name the scratch directory in grants, tool paths, and every
    // tool result. Two replacements put stable paths in their place; the
    // events are otherwise the bytes the runtime wrote.
    const settle = (text) => text.split(`${root}/project`).join(PROJECT).split(root).join(TOOLS);
    const read = (path) => settle(readFileSync(path, "utf8"));
    const episode = read(join(logs, "episode.jsonl"));
    writeFileSync(join(dir, "workflow.jsonl"), episode);
    const names = [];
    for (const line of episode.split("\n").filter((l) => l.trim() !== "")) {
      const event = JSON.parse(line);
      if (event.type !== "workflow/node-start" || !event.data.child_id) continue;
      const name = `workflow-${event.data.node.replace(/_/g, "-")}-${event.data.fire}.jsonl`;
      writeFileSync(join(dir, name), read(join(logs, "children", event.data.child_id, "episode.jsonl")));
      names.push(name);
    }
    console.log(`workflow.jsonl and ${names.join(", ")} written by ${BINARY}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

const r = root();
r.write("root.jsonl");
child().write("child.jsonl");
fork(r).write("fork.jsonl");
compact().write("compact.jsonl");
overlapParent().write("overlap-parent.jsonl");
overlapChild().write("overlap-child.jsonl");
rich().write("rich.jsonl");
workflowRun();
