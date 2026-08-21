# Landscape

This document places foe among the coding-agent systems that exist in August
2026. It records what those systems do, where foe's properties are shared,
where they are distinct, and what foe must implement so that an interactive
harness or a person can hand it a task and walk away. Every claim about an
external system carries a link to the page it was read from. A claim marked
unverified was found only in secondary sources.

The terms used throughout:

- A **harness** is the program that runs a model in a loop with tools. Claude
  Code, Codex CLI, and Cursor are harnesses.
- An **autonomous agent product** is a hosted harness that accepts a task,
  runs it in a cloud sandbox without a person present, and returns a result.
  Google Jules and the Codex cloud agent are examples.
- **Delegation** is the act of handing a bounded task to a separate executor
  and receiving a result later. The executor may be a product, a subprocess,
  or a protocol peer.

## The field in 2026

### Autonomous agent products

| System | Delegation mechanism | Sandbox | Termination model | Programmatic access | Report-back shape |
|---|---|---|---|---|---|
| [Google Jules](https://jules.google/docs) | Web app, CLI, or `POST /v1alpha/sessions` with `prompt` and a GitHub `sourceContext` ([API reference](https://jules.google/docs/api/reference/)) | Cloud VM that clones the repository; network stays on | Agent plans, optionally waits for plan approval (`requirePlanApproval`), executes, and opens a pull request (`automationMode: AUTO_CREATE_PR`) | Alpha REST API with `X-Goog-Api-Key`; `sendMessage` and `approvePlan` endpoints | Activity list per session plus a pull request URL; daily task caps of 15, 100, and 300 by plan ([limits](https://jules.google/docs/usage-limits/)) |
| [OpenAI Codex cloud](https://learn.chatgpt.com/docs/cloud) | Web app, `@codex` in GitHub, Linear, or `codex cloud` from the CLI | Isolated cloud environment; internet is off during the agent phase by default, with per-environment allowlist presets and an option to permit only `GET`, `HEAD`, and `OPTIONS` ([internet access](https://learn.chatgpt.com/docs/cloud/internet-access)) | Agent finishes and presents a diff; the user creates the PR or requests follow-ups | No task-creation API found on the documentation consulted; unverified | Diff, summary, and optional pull request |
| [Devin](https://docs.devin.ai/get-started/devin-intro) | Web app, Slack or Teams thread, Linear or Jira ticket, or `POST /v3/organizations/{org}/sessions` ([API](https://docs.devin.ai/api-reference/sessions/create-a-new-devin-session)) | Devin's own VM with shell, editor, and browser | Session ends when the agent finishes or when `max_acu_limit` is reached | REST API; request accepts `structured_output_schema`, `secret_ids`, `idempotent`, and `tags` | Draft pull request, session URL, and optional structured output; billed in compute units called ACUs (price points unverified) |
| [GitHub Copilot coding agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent) | Assign an issue to Copilot, mention `@copilot` on a PR, the agents panel, or Teams, Slack, Azure Boards, Jira, and Linear | Ephemeral GitHub Actions environment behind a firewall | Hard maximum of 59 minutes per session; shorter timeouts configurable | Issue assignment through the GitHub API; no dedicated task API found on the pages consulted | Draft pull request; one premium request per session plus Actions minutes ([changelog](https://github.blog/changelog/2025-07-10-github-copilot-coding-agent-now-uses-one-premium-request-per-session/)) |
| [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web) | Web app, mobile app, or `claude --cloud "task"` from a checkout | Isolated VM; network limited by default and configurable per cloud environment; git credentials held outside the sandbox behind a proxy | Agent finishes or asks a question and waits; `claude -p "msg" --cloud <id>` queues a follow-up and exits | CLI flags; routines can be triggered by API call | Pull request from the web UI, or `--teleport` pulls the branch and transcript into a terminal; no separate VM charge |
| [Cursor Cloud Agents](https://cursor.com/docs/cloud-agent/api/endpoints) | IDE, web, or `POST /v1/agents` with prompt, repository, ref, and auto-PR flag | Cloud VM per agent; self-hosted option | A run ends when the agent finishes; follow-up runs extend the same agent | Public-beta REST API with per-run SSE stream and cancel; webhooks listed as coming soon | Branch, PR URL, and assistant output |
| [Kiro autonomous agent](https://kiro.dev/docs/autonomous-agent/) | Web, CLI, or IDE | Isolated sandbox with configurable access controls | Agent plans, codes, and opens a PR | MCP for tool extension; no task API found on the page consulted | Pull request; requires a Pro or higher subscription, billed in credits |
| [OpenHands Cloud](https://docs.openhands.dev/openhands/usage/cloud/cloud-api) | `POST /api/v1/app-conversations` with an initial message and repository; also `openhands --headless -t "task"` locally ([headless](https://docs.openhands.dev/openhands/usage/run-openhands/headless-mode)) | Docker container per conversation | `execution_status` reaches `finished`, `error`, or `stuck`; headless mode always approves every action | REST API with status polling; headless `--json` emits one event per line | Conversation trajectory and repository changes |
| [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) | `POST` a session that references an agent and an environment, then send user events | Managed cloud sandbox or self-hosted sandbox | Session runs until the agent finishes; session budgets cap spend | REST API with server-sent events; full event history persisted server-side | Streamed events and sandbox outputs |
| [Factory Droid](https://docs.factory.ai/droid-exec/overview) | `droid exec "prompt"` | Local process; autonomy tiers `--auto low`, `medium`, `high` gate what the agent may do | Single non-interactive pass; non-zero exit on permission violation, tool error, or unmet objective | `--output-format json` returns result and session id; `--session-id` and `--fork` continue runs | stdout result and exit code |

Sweep, which began as an issue-to-pull-request bot, has retired that bot in
favor of a JetBrains editor plugin ([sweep.dev](https://sweep.dev/)). It is
no longer an autonomous agent product.

### Interactive harnesses and their delegation surfaces

| Harness | Headless invocation | Tool and agent extension points | Bounding controls |
|---|---|---|---|
| [Claude Code](https://code.claude.com/docs/en/headless) | `claude -p "task" --output-format json`; `--json-schema` for validated structured output; `--bare` skips local hooks, plugins, and MCP discovery | MCP client over stdio, HTTP, SSE, and WebSocket; subagents defined as Markdown files with `tools`, `model`, `maxTurns`, and `permissionMode`, which cannot be external processes ([subagents](https://code.claude.com/docs/en/sub-agents)); Python and TypeScript [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) | `--max-turns`, `--max-budget-usd`, `--allowedTools`, `--permission-mode dontAsk` ([CLI reference](https://code.claude.com/docs/en/cli-reference)); MCP tool output capped at 25,000 tokens by default ([MCP](https://code.claude.com/docs/en/mcp)) |
| [Codex CLI](https://learn.chatgpt.com/docs/non-interactive-mode) | `codex exec "task"`; `--json` streams events as JSON Lines; `--output-schema` for structured output; `-o` writes the last message to a file; `codex exec resume <id>` | MCP client configured in `config.toml` ([MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)); registered ACP agent | `--sandbox read-only` by default, `workspace-write`, or `danger-full-access`; Seatbelt on macOS, Landlock with seccomp on Linux ([security](https://learn.chatgpt.com/docs/security)) |
| [Cursor CLI](https://cursor.com/docs/cli/headless) | `agent -p "task" --output-format json`; `--force` permits direct file changes | MCP client; registered ACP agent; Cloud Agents API above | Exit status; model selection |
| [Antigravity](https://antigravity.google/docs/getting-started) | Agent Manager in the desktop app; `/goal` runs to completion without intermediate input; CLI with headless mode and a Python SDK | MCP across web, CLI, and SDK; projects bound agents to named folders and repositories | Project-scoped access policies |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) | `copilot -p "task"`; `-s` prints only the response | `--allow-tool`, `--deny-tool`, `--allow-all-tools`; registered ACP agent | Per-tool allow and deny patterns |
| [Gemini CLI](https://geminicli.com/docs/cli/headless/) | `gemini -p "task" --output-format json`; exit code 53 on turn limit | MCP client; registered ACP agent | Turn limit, approval mode |
| [Aider](https://aider.chat/docs/scripting.html) | `aider --message "task"` or `--message-file`; Python API through `Coder` | No tool protocol | None beyond the model call |
| [SWE-agent](https://swe-agent.com/latest/usage/batch_mode/) | `sweagent run-batch --config x.yaml` over instance files; per-instance Docker image | YAML tool bundles | `per_instance_cost_limit`; project is in maintenance mode with mini-SWE-agent as successor |
| [Goose](https://block.github.io/goose/) | `goose run --recipe file.yaml` (flag details unverified; vendor page not reachable) | MCP client; registered ACP agent | Recipe parameters |
| [Amp](https://ampcode.com/manual) | `amp -x "task"` waits for the turn to end and prints the final message; `--stream-json` and `--stream-json-input` | MCP client; built-in subagents and an oracle model | None documented beyond execute mode |
| [Cline](https://docs.cline.bot/usage/cli-overview) | Headless when `--json` is passed or stdin is piped | SDK; MCP client; registered ACP agent | Per-run flags |
| Roo Code | Reported archived in May 2026 (unverified) | | |

### Protocols

- [Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
  connects a harness to tools. The 2026-07-28 revision removed the session
  handshake, made every request self-describing, and moved long-running
  requests into a tasks extension polled with `tasks/get`. Sampling, the
  feature that let a server ask the client's model for a completion, is
  deprecated. Every harness in the table above is an MCP client.
- [Agent Client Protocol](https://agentclientprotocol.com/protocol/prompt-turn)
  connects an editor to an agent over JSON-RPC on stdio. A client sends
  `session/prompt`; the agent streams `session/update` notifications, may ask
  `session/request_permission`, and ends the turn with a stop reason of
  `end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, or `cancelled`.
  Forty agents are listed as implementing it, including Claude Code, Codex
  CLI, Cursor, Gemini CLI, GitHub Copilot, Goose, Cline, OpenHands, and
  Factory Droid ([agents](https://agentclientprotocol.com/overview/agents)).
  Zed and JetBrains host a registry ([registry](https://zed.dev/blog/acp-registry)).
- [Agent2Agent](https://a2a-protocol.org/latest/specification/) connects
  agents across organizations over HTTP. An agent publishes an Agent Card; a
  client calls `SendMessage` and receives a task that moves through
  `SUBMITTED`, `WORKING`, `INPUT_REQUIRED`, `AUTH_REQUIRED`, and terminal
  states `COMPLETED`, `FAILED`, `CANCELED`, or `REJECTED`. Outputs are
  artifacts. Version 1.0.0 is governed by the Linux Foundation.

### Runtimes with overlapping ideas

- Durable-execution engines record each completed step so that a crashed
  workflow resumes by replaying the record. Temporal stores an event history
  and replays deterministic workflow code against it
  ([event history](https://docs.temporal.io/encyclopedia/event-history)).
  Inngest memoizes each `step.run` result and re-executes the function from
  the top ([steps](https://www.inngest.com/docs/features/inngest-functions/steps-workflows)).
  Restate journals every side effect and skips completed steps on retry
  ([durable execution](https://docs.restate.dev/concepts/durable_execution)).
  LangGraph saves a state checkpoint per super-step and forks by editing a
  checkpoint ([persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).
- Process sandboxes restrict a local agent without a container. Codex CLI
  uses Seatbelt on macOS and Landlock with seccomp on Linux. The
  `sandbox-runtime` package wraps a process with bubblewrap on Linux and
  Seatbelt on macOS, removes the network namespace, and routes permitted
  domains through a proxy ([sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)).
  Landlock itself gained TCP bind and connect rules in ABI 4, IPC scoping in
  ABI 6, and audit logging in ABI 7 ([kernel docs](https://docs.kernel.org/userspace-api/landlock.html)).
- Hosted sandboxes give an agent a disposable machine. E2B runs Firecracker
  microVMs ([docs](https://docs.e2b.dev/)). Modal runs gVisor containers with
  a default five-minute lifetime extendable to 24 hours
  ([sandbox guide](https://modal.com/docs/guide/sandbox)). Daytona runs
  containers with sub-90-millisecond cold starts and optional VM isolation
  ([docs](https://www.daytona.io/docs/en/getting-started/)).
- Budget bounds appear as `--max-turns` and `--max-budget-usd` in Claude
  Code, `max_acu_limit` in Devin, `per_instance_cost_limit` in SWE-agent, a
  turn-limit exit code in Gemini CLI, and session budgets in Managed Agents.

### Benchmarks

- [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)
  holds 500 human-validated GitHub issues from Python repositories. An agent
  succeeds on an instance when the tests listed in `FAIL_TO_PASS` pass after
  its patch and the tests listed in `PASS_TO_PASS` still pass.
- [Terminal-Bench 2.0](https://arxiv.org/abs/2601.11868) holds 89 tasks,
  each a container, an instruction, a human-written solution, and tests.
  Version 2.1 fixed 28 tasks and added continuous validation
  ([news](https://www.tbench.ai/news)). The 2.1 leaderboard lists Claude
  Code at 83.8 percent and Codex at 83.1 percent, with a rule that
  submissions may not modify timeouts or resources
  ([leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.1)).
  Tasks run through the Harbor harness.

In measurable terms, "succeeds at autonomous coding" means: given a
container, an instruction, and no person, the agent produces a state in
which a hidden test command exits zero, within a fixed wall-clock and
resource limit. Cost per task is reported alongside success rate on the
Terminal-Bench leaderboard.

## What everyone does

Six patterns recur across the surveyed systems.

1. **The task is a prompt string plus a repository reference.** Jules takes
   `prompt` and `sourceContext`; Devin takes `prompt`; Cursor takes a prompt
   and a repository URL; Copilot takes an issue body. No surveyed product
   accepts a structured task with an explicit acceptance condition. The
   nearest forms are Devin's `structured_output_schema` and Claude Code's
   `--json-schema`, which constrain the answer rather than the task.
2. **The report is a pull request.** Jules, Codex cloud, Devin, Copilot,
   Claude Code on the web, Cursor, and Kiro all terminate by producing a
   branch and a PR or a diff for a person to review. The PR is the
   completion signal, the audit artifact, and the hand-off.
3. **The sandbox is a disposable VM or container with network policy
   attached.** Codex cloud turns internet off during the agent phase and
   offers domain allowlists. Claude Code on the web defaults to a limited
   allowlist with credentials held behind a proxy. Copilot runs behind a
   firewall in Actions. Local harnesses use Seatbelt, Landlock, or
   bubblewrap.
4. **Autonomy is a permission mode.** OpenHands headless always approves.
   Claude Code `-p` takes `--permission-mode` and `--allowedTools`. Codex
   exec defaults to read-only and escalates through `--sandbox`. Factory
   Droid uses `--auto` tiers. Antigravity's `/goal` runs without intermediate
   input. Hands-free execution is available everywhere; it is configured per
   invocation rather than guaranteed by the runtime.
5. **Headless output is one JSON object or a JSON Lines stream.** Claude
   Code, Codex CLI, Cursor CLI, Gemini CLI, Amp, OpenHands, and Factory Droid
   all offer a final JSON result and a per-event stream, and all exit
   non-zero on failure.
6. **Sessions resume.** `codex exec resume`, `claude --resume`, Cursor
   follow-up runs, Devin `sendMessage`, and Jules `sendMessage` all continue
   a prior conversation. Follow-up is the universal steering mechanism.

Two further patterns apply to tool integration. Every harness is an MCP
client, and most are ACP agents. A tool that speaks MCP over stdio is
reachable from every harness surveyed.

## What foe does that nothing else does

The comparison below is against documented behavior of the surveyed systems.

- **Program identity as a hash computed from configuration alone.** foe
  defines `identity(program)` as a SHA-256 over instructions, tool
  specifications, grant policy, budget, termination condition, child program
  identities, runtime-contributed strings, and runtime version, computed
  with no process, network, or credential. No surveyed system documents a
  content-addressed identity for an agent configuration. Managed Agents
  references an agent by a server-assigned id; Claude Code subagents are
  named files. This property is unique among the systems surveyed.
- **Every model request reconstructable from an append-only log, with
  byte-stable prefixes.** Transcript recording is universal: Codex `--json`
  streams events, SWE-agent writes trajectory files, Cursor's API exposes a
  per-run stream, Managed Agents persists event history. Durable-execution
  engines journal step results. None of these documents that the exact
  request sent to the model is derivable from the record, or that request
  prefixes are held stable across steps and sibling episodes for cache
  hits. Cursor's documentation states that its stream does not replay prior
  runs. foe's replay guarantee is stronger than any documented equivalent,
  and the cache-stability commitment has no documented counterpart.
- **Canonical and rendered tool results.** foe stores each tool's full JSON
  value in the log and shows the model a rendered projection. Claude Code
  limits MCP tool output to 25,000 tokens by default, and its documentation
  does not say where the remainder goes. No surveyed system documents a
  dual representation in which the log keeps what the model did not see.
- **One allow list that derives both the tool's capability handle and the
  kernel ruleset.** Codex CLI also uses Landlock, and sandbox-runtime also
  restricts filesystem and network. Both express policy as a mode or a path
  list applied to the whole process. foe's grants name individual
  executables, type each tool by effect (`pure`, `reads`, `writes`, `execs`,
  `spawns`), refuse at construction a tool whose effect the grants do not
  cover, and hand each tool only the handle its effect entitles it to. The
  Landlock mechanism is shared with Codex CLI; the effect typing and
  per-executable execute rules are not documented elsewhere.
- **Budget as a pool reserved down an episode tree.** Claude Code's
  `--max-budget-usd` is a cap that, when reached, stops running subagents
  and refuses new ones. Managed Agents has session budgets. foe additionally
  reserves a child's budget from the parent's remainder at spawn and
  returns the unspent part when the child settles, alongside caps on depth,
  lifetime episode count, and concurrency. The reservation-and-return
  accounting has no documented counterpart.
- **Termination that a parent can route on.** A2A's task lifecycle includes
  `INPUT_REQUIRED`, and Claude Code on the web lets a session ask and wait.
  foe has no waiting state. An episode ends in one of `Completed`,
  `Blocked` with a code from a fixed vocabulary, `Exhausted`, or `Failed`,
  and the runtime itself ends an episode that repeats a tool call or a
  reasoning turn three times. The absence of a waiting state is a design
  choice shared in spirit by OpenHands headless mode and Antigravity's
  `/goal`; the fixed blocking vocabulary for parent routing is foe's own.

Two properties that might look distinctive are partly shared, and the
partition matters. Running without a person present during execution is
the premise of every hosted product in the first table, and every local
harness offers it as a permission mode. What no surveyed system offers is a
runtime designed around that absence: a declared completion condition, a
budget held as a pool across the episode tree, runtime detection of a stuck
agent, and a closed vocabulary of blocking conditions, each replacing a job
a person would otherwise do. OpenHands's `stuck` status and Copilot's hard
session cap are the nearest single pieces. Local harnesses with approvals
disabled are unsupervised rather than autonomous. Credential isolation by
placing the model transport in a separate process is matched by Claude Code
on the web's credential proxy and by sandbox-runtime's network proxy.

## Where foe is behind

- **No hosted execution.** Every autonomous product provisions a machine,
  clones a repository, and runs without the caller keeping a process alive.
  foe runs where it is invoked. A person who wants to close a laptop needs
  something else to keep foe running.
- **No pull request path.** The universal report-back is a PR. foe reports
  an outcome value and a log. Producing a branch and a PR requires the
  caller to grant `git` and a forge CLI as configured executables and to
  write instructions for them. No example configuration does this yet.
- **No entry points from issue trackers or chat.** Copilot, Devin, Jules,
  Codex cloud, and Kiro all accept a task from GitHub, Slack, Linear, or
  Jira. foe accepts a task from a command line or a host process.
- **Linux only for enforcement.** Landlock exists only on Linux. Codex CLI
  and sandbox-runtime cover macOS and Windows as well. On other platforms
  foe's `best-effort` mode applies nothing.
- **No domain-level egress control.** Landlock restricts TCP by port. Codex
  cloud, Claude Code on the web, and sandbox-runtime restrict by domain
  through a proxy with allowlist presets. foe's design removes network from
  executables where the kernel supports it and otherwise leaves egress to
  the host.
- **No MCP client.** foe's tools come from built-ins, configured
  executables, and host tools over foe's own line protocol. Every surveyed
  harness consumes MCP servers. A team with an existing MCP server for its
  issue tracker or database cannot attach it to foe.
- **No ACP implementation.** Forty agents are discoverable from Zed and
  JetBrains through the ACP registry. foe is not among them.
- **No benchmark results.** Claude Code, Codex, Terminus, Cursor CLI, and
  mini-SWE-agent have Terminal-Bench 2.1 scores with cost. foe has no
  published SWE-bench Verified or Terminal-Bench result, so its success rate
  on autonomous tasks is unknown.
- **No resume.** Episodes never resume; a later episode may be seeded from
  a log prefix. Every surveyed product offers a follow-up message on a
  finished session. For a person, "add one more instruction" is the most
  common second action after reviewing a result, and foe requires a new
  episode for it.
- **No context compaction.** Claude Code auto-compacts when the window
  approaches capacity, and Managed Agents advertises compaction. foe's
  design specifies rendering and truncation of tool results and says
  nothing about what happens when a long episode fills the model's context.
- **No browser or computer use.** Cursor Cloud Agents and Devin give the
  agent a browser. foe's built-in tools are `read`, `grep`, `edit`, `bash`,
  and spawn.
- **No stable interface.** The design document states that no interface is
  stable. Every integration path below depends on a CLI contract and a
  protocol that do not yet exist in final form.

## How a conventional harness would delegate to foe

Each path below names the mechanism, the task payload, what returns, and
what foe must implement. The subcommands named here (`foe run`, `foe mcp`,
`foe acp`) are proposals; none is implemented. The paths are ranked by
leverage at the end.

### Claude Code

**Shell invocation.** Claude Code's Bash tool runs `foe run CONFIG --task
"..."`. The harness operator pre-approves the binary with
`--allowedTools "Bash(foe *)"` or a `permissions.allow` rule. The payload is
the task string and a configuration path. What returns is foe's stdout. The
design currently echoes every event to stdout, so foe must add a mode in
which the event stream goes to a file or stderr and stdout carries one final
JSON object. That object holds the outcome kind, the value or blocked code,
the budget spent, the log directory, and the HTML export path. Exit status
must map to outcome kind so that `claude -p` scripts can branch on it. This path works from every harness
with a shell tool and requires nothing beyond the CLI contract.

**MCP server.** foe ships a `foe mcp` subcommand that speaks MCP over stdio
and exposes a `run_episode` tool taking `config`, `task`, and optional
`budget` overrides, and an `identity` tool returning the program hash.
Claude Code registers it with `claude mcp add foe -- foe mcp`. The tool
result is the same final JSON object. Two constraints shape this path.
Claude Code caps MCP tool output at 25,000 tokens, so the result must be
compact and point to the log rather than include it. Claude Code aborts a
stdio tool call that sends nothing for 30 minutes, so `run_episode` must
emit MCP progress notifications while the episode runs, or return a task
handle under the 2026-07-28 tasks extension and let the client poll
`tasks/get`. Because Claude Code subagents cannot be external processes,
the way to make foe a "subagent type" is a subagent definition whose only
permitted tool is `mcp__foe__run_episode`, which gives Claude Code's
delegation heuristics a named target.

**Agent SDK.** A Python program using the Agent SDK defines an in-process
tool that calls foe's Python package and returns the outcome. This suits
pipelines already written against the SDK and needs only the Python SDK
foe already plans.

### Codex CLI

**Shell invocation under the Codex sandbox.** `codex exec --sandbox
workspace-write` runs commands with network blocked by default and Landlock
restrictions that a child process inherits. A foe episode started from
inside that sandbox cannot reach a model provider unless the Codex sandbox
is widened or foe's host-supplied transport is used. This path is therefore
weak under Codex's default posture.

**MCP server.** Codex reads `[mcp_servers.foe]` from `config.toml` with
`command = "foe"` and `args = ["mcp"]`. Whether an MCP server process
inherits the command sandbox is not stated on the pages consulted and
should be checked before relying on it. The payload, result, and progress
constraints are the same as for Claude Code. `codex exec --output-schema`
can then demand that Codex's final message include foe's outcome fields.

**Codex as an ACP agent.** Codex CLI is listed as an ACP agent, which means
an editor can drive it; it does not by itself let Codex call foe.

### Cursor and Antigravity

**MCP server.** Both are MCP clients, and Cursor's Cloud Agents API accepts
MCP server configuration, so a cloud agent with the foe binary in its
environment can call `run_episode`. Antigravity supports MCP from its web
app, CLI, and SDK. The same `foe mcp` server serves both.

**ACP agent.** Cursor and Antigravity are agents rather than ACP clients,
so ACP does not connect them to foe. ACP matters for editors: Zed,
JetBrains, Neovim, and Emacs clients can launch any registered agent. A
`foe acp` subcommand would answer `session/new` and `session/prompt`, stream
`session/update` notifications from the log, and never send
`session/request_permission`. It would end the turn with `end_turn` on
`Completed` and `refusal` on `Blocked`. That subcommand makes foe launchable
from those editors and discoverable through the registry. The payload is the prompt text plus
the working directory the client supplies; foe maps the directory to its
read and write roots from a default program.

### Ranking by leverage

1. **CLI contract with JSON outcome and exit codes.** Prerequisite for every
   other path, and sufficient on its own for Claude Code, Copilot CLI, Gemini
   CLI, Amp, Cline, and any CI system.
2. **MCP stdio server with progress or task handles.** One implementation
   reaches every harness surveyed, and it gives the host a typed tool rather
   than a shell string.
3. **ACP agent.** Reaches editors and the registry; modest implementation
   cost because foe's log already produces the events ACP streams.
4. **Python SDK as an in-process tool.** Serves Agent SDK and OpenHands SDK
   users; narrow audience.
5. **A2A server.** Valuable only once foe has a hosted form, because A2A
   assumes an HTTP endpoint that stays up.

## How a human delegates to foe

A command shape consistent with the design is:

```
foe run PROGRAM.json --task "Make `cargo test` pass in crates/log after
  adding a `Seeded` event; do not touch crates/view." --out runs/2026-08-21-seeded
```

A good delegation has four parts, each mapping to a field foe already
defines.

- **An acceptance condition that a tool can check.** The task names a
  command whose exit status decides success, and the program's `done_when`
  names the same command as its `verify` tool with a retry count. A task
  without a checkable condition ends when the model stops calling tools,
  which is the weakest termination.
- **Named roots.** The program's grants list the directories the episode
  may read and write. A person delegating a change to one crate grants
  write access to that crate and read access to the workspace.
- **A budget.** Spend caps in tokens or requests, plus the structural caps
  on depth and episode count if the program spawns children.
- **A return shape, when the result is data.** `done_when.returns` with a
  JSON Schema makes the episode end by calling `return` with a conforming
  value. This form suits a task such as "find the three slowest tests and
  report them". A task such as "fix the tests" suits the `verify` form.

What the person gets back:

- An exit status and one JSON line: outcome kind, the returned value or
  the blocked code and message, budget consumed, and the log directory.
- The log directory, which a later `foe view` renders as a single HTML file
  showing the episode tree, every tool call with rendered and canonical
  forms, budget consumption, sandbox status, and the outcome.
- The working tree as the episode left it. foe does not commit. If the
  program grants `git`, the instructions can require a commit on a named
  branch, which is how the PR report-back of hosted products is
  reproduced.

A person who delegates the same program repeatedly keeps the program file
under version control and records `foe identity PROGRAM.json` with each run,
so that two runs with the same hash differ only in task and model
responses.

## Recommendations

The five items below are in priority order. Each names the gap it closes.

1. **Fix the headless CLI contract before any other interface.** Define the
   final JSON object on stdout, the exit-status mapping from outcome kind,
   the `--out` log directory flag, and a single-file HTML export from a
   finished log. Every harness in the survey delegates through a shell tool
   today, and none can consume an event stream on stdout as a result. This
   is the only item that no other item can substitute for.
2. **Ship `foe mcp`.** A stdio MCP server exposing `run_episode` and
   `identity`, with progress notifications during the run and a result under
   Claude Code's 25,000-token cap. Implement the 2026-07-28 tasks extension
   so that clients that support it poll rather than hold a connection. This
   single binary mode makes foe callable from every harness surveyed.
3. **Ship `foe acp`.** Map the log's events to `session/update`, never
   request permission, and map outcomes to stop reasons. Register it so that
   Zed and JetBrains users can launch foe by name. The cost is low because
   the log already carries every event ACP streams.
4. **Publish a Terminal-Bench 2.1 and SWE-bench Verified result with cost.**
   Run through Harbor with a public program file and record its identity
   hash in the submission. Without a score, the claim that foe succeeds at
   autonomous coding has no evidence, and delegating harnesses have no basis
   for choosing it.
5. **Provide the PR report-back and domain egress that hosted products
   take for granted.** Ship an example program that grants `git` and a forge
   CLI as configured executables and ends with a pushed branch. Document the
   host-transport pattern, in which a host process holds credentials and
   enforces a domain allowlist, so that the episode has no network of its
   own. These two items close the largest gaps between what a person
   receives from foe and what they receive from Jules, Codex cloud, or
   Copilot.
