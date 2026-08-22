# Landscape

This document compares foe with coding-agent systems documented in August
2026. It identifies shared behavior, foe's distinct combination of properties,
and the work required for unattended execution by a person or another harness.
Each claim about an external system links to its source. Claims found only in
secondary sources are marked `unverified`.

The document uses three terms:

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

[Sweep](https://sweep.dev/) is a JetBrains editor plugin rather than an
autonomous agent product.

### Interactive harnesses and their delegation surfaces

| Harness | Headless invocation | Tool and agent extension points | Bounding controls |
|---|---|---|---|
| [Claude Code](https://code.claude.com/docs/en/headless) | `claude -p "task" --output-format json`; `--json-schema` for validated structured output; `--bare` skips local hooks, plugins, and MCP discovery | MCP client over stdio, HTTP, SSE, and WebSocket; subagents defined as Markdown files with `tools`, `model`, `maxTurns`, and `permissionMode`, which cannot be external processes ([subagents](https://code.claude.com/docs/en/sub-agents)); Python and TypeScript [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) | `--max-turns`, `--max-budget-usd`, `--allowedTools`, `--permission-mode dontAsk` ([CLI reference](https://code.claude.com/docs/en/cli-reference)); MCP tool output capped at 25,000 tokens by default ([MCP](https://code.claude.com/docs/en/mcp)) |
| [Codex CLI](https://learn.chatgpt.com/docs/non-interactive-mode) | `codex exec "task"`; `--json` streams events as JSON Lines; `--output-schema` for structured output; `-o` writes the last message to a file; `codex exec resume <id>` | MCP client configured in `config.toml` ([MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)); registered ACP agent | `--sandbox read-only` by default, `workspace-write`, or `danger-full-access`; Seatbelt on macOS, Landlock with seccomp on Linux ([security](https://learn.chatgpt.com/docs/security)) |
| [Cursor CLI](https://cursor.com/docs/cli/headless) | `agent -p "task" --output-format json`; `--force` permits direct file changes | MCP client; registered ACP agent; Cloud Agents API above | Exit status; model selection |
| [Antigravity](https://antigravity.google/docs/getting-started) | Agent Manager in the desktop app; `/goal` runs to completion without intermediate input; CLI with headless mode and a Python SDK | MCP across web, CLI, and SDK; projects bound agents to named folders and repositories | Project-scoped access policies |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) | `copilot -p "task"`; `-s` prints only the response | `--allow-tool`, `--deny-tool`, `--allow-all-tools`; registered ACP agent | Per-tool allow and deny patterns |
| [Gemini CLI](https://geminicli.com/docs/cli/headless/) | `gemini -p "task" --output-format json`; exit code 53 on turn limit | MCP client; registered ACP agent | Turn limit, approval mode |
| [Exo](https://github.com/exoharness/exo/tree/960656626097b3a4ef56f3e4aff3c25573c1623d) | `exo conversation send AGENT CONVERSATION "task"` runs one turn; `exo repl` resumes an interactive conversation | Built-in Rust and recursive-language-model executors, TypeScript harness modules, Codex, Claude Code, and Cursor presets, installable TypeScript tools, and skills ([executors](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/website/docs-src/concepts/executors.md)) | Per-request output and tool-round limits; sandbox provider, image, mounts, storage, and network policy; no documented whole-run spend pool |
| [Aider](https://aider.chat/docs/scripting.html) | `aider --message "task"` or `--message-file`; Python API through `Coder` | No tool protocol | None beyond the model call |
| [SWE-agent](https://swe-agent.com/latest/usage/batch_mode/) | `sweagent run-batch --config x.yaml` over instance files; per-instance Docker image | YAML tool bundles | `per_instance_cost_limit`; project is in maintenance mode with mini-SWE-agent as successor |
| [Goose](https://block.github.io/goose/) | `goose run --recipe file.yaml` (flag details unverified; vendor page not reachable) | MCP client; registered ACP agent | Recipe parameters |
| [Amp](https://ampcode.com/manual) | `amp -x "task"` waits for the turn to end and prints the final message; `--stream-json` and `--stream-json-input` | MCP client; built-in subagents and an oracle model | None documented beyond execute mode |
| [Cline](https://docs.cline.bot/usage/cli-overview) | Headless when `--json` is passed or stdin is piped | SDK; MCP client; registered ACP agent | Per-run flags |
| Roo Code | Reported archived in May 2026 (unverified) | | |

### Exo's long-lived agent architecture

Exo commit
[`9606566`](https://github.com/exoharness/exo/commit/960656626097b3a4ef56f3e4aff3c25573c1623d),
dated August 19, 2026, in Pacific time, is the source for this comparison.
The workspace reports version 0.1.0, publishes no tags or releases, and
describes its public API as unstable
([version](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/Cargo.toml#L14-L18),
[releases](https://github.com/exoharness/exo/releases),
[status](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/website/docs-src/index.md#L38-L42)).

Exo separates durable agent state from the code that decides how an agent
acts. Exo calls its state-management layer the trusted substrate. This layer
stores append-only conversation events, immutable versioned artifacts,
secrets, bindings, and sandbox lifecycle state. A replaceable executor
controls prompt assembly, model calls, tools, and context policy
([architecture](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/website/docs-src/concepts/exoharness-and-executor.md#L13-L57)).

The default Exo agent can modify its prompts, tools, and harness source. The
agent then asks a host-side process called the guardian to build and restart
the services. Users can resume or fork Exo conversations. Exo can snapshot
and rewind its sandbox independently of the append-only event log
([self-control](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/docs/SELF-CONTROL.md#L31-L50),
[time travel](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/website/docs-src/concepts/time-travel.md#L8-L42)).
Local and hosted sandbox providers include Docker, Apple Container, Daytona,
E2B, Sprites, Vercel, and AWS AgentCore. The comparison commit also includes
feature-gated Firecracker support
([sandboxes](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/website/docs-src/concepts/sandboxes.md#L8-L64),
[Firecracker](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/support/firecracker/README.md#L213-L323)).

Both Exo and foe preserve durable evidence outside the model's finite context
window. Exo writes full tool results as versioned artifacts and puts compact
previews in conversation history
([tools](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/website/docs-src/concepts/tools.md#L8-L95)).
foe records one canonical JSON value and the exact rendered projection for
every tool result. foe uses the recorded projection when it reconstructs the
next model request.

Exo delegates request construction and compaction to its replaceable executor.
Its event schema does not document a complete snapshot of each model request
([event model](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/crates/exoharness/src/types.rs#L254-L520)).
At this commit, the built-in Rust and TypeScript execution loops rebuild model
context from conversation history without summarizing it. Executors that wrap
Codex, Claude Code, or Cursor may still compact context internally
([Rust loop](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/crates/executor/src/basic.rs#L128-L180),
[TypeScript loop](https://github.com/exoharness/exo/blob/960656626097b3a4ef56f3e4aff3c25573c1623d/exoharness/typescript/model-runtime/turn-loop.ts#L101-L186)).

Exo specializes in long-lived agents whose executors, tools, prompts, and
sandboxes can change while durable state survives. foe specializes in bounded
runs called episodes. Before each episode begins, a content-addressed program
fixes its behavior, authority, budget, termination rule, and runtime text.

### Protocols

- [Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
  connects a harness to tools. The 2026-07-28 revision removed the session
  handshake, made every request self-describing, and moved long-running
  requests into a tasks extension polled with `tasks/get`. Sampling, the
  feature that let a server ask the client's model for a completion, is
  deprecated. MCP is common among the general-purpose interactive harnesses
  in the table. Aider and SWE-agent do not expose it. Exo stores an MCP
  binding in its data model, but the comparison commit has no general MCP tool
  invocation path.
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
- [Terminal-Bench 2.0](https://arxiv.org/abs/2601.11868) contains 89 tasks.
  Each task supplies a container environment, an instruction, a human-written
  solution, and tests.
  Version 2.1 fixed 28 tasks and added continuous validation
  ([news](https://www.tbench.ai/news)). The 2.1 leaderboard lists Claude
  Code at 83.8 percent and Codex at 83.1 percent, with a rule that
  submissions may not modify timeouts or resources
  ([leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.1)).
  Tasks run through the Harbor harness.

Terminal-Bench treats a task as successful when an agent, working without
human input, produces a state in which a hidden test command exits zero within
fixed wall-clock and resource limits. The leaderboard reports cost per task
beside success rate.

## Common patterns

The survey found six common patterns.

1. **The task is a prompt string plus a repository reference.** Jules takes
   `prompt` and `sourceContext`; Devin takes `prompt`; Cursor takes a prompt
   and a repository URL; Copilot takes an issue body. No surveyed product
   accepts a structured task with an explicit acceptance condition. The
   nearest forms are Devin's `structured_output_schema` and Claude Code's
   `--json-schema`, which constrain the answer rather than the task.
2. **The report is usually a pull request or diff.** Jules, Codex cloud,
   Devin, Copilot, Claude Code on the web, Cursor, and Kiro produce one of
   these review artifacts when a run finishes. The artifact signals
   completion, preserves the proposed changes, and transfers the work to a
   person.
3. **The sandbox is a disposable VM or container with network policy
   attached.** Codex cloud turns internet off during the agent phase and
   offers domain allowlists. Claude Code on the web defaults to a limited
   allowlist with credentials held behind a proxy. Copilot runs behind a
   firewall in Actions. Local harnesses use Seatbelt, Landlock, or
   bubblewrap.
4. **Permission settings control unattended execution.** OpenHands headless always approves.
   Claude Code `-p` takes `--permission-mode` and `--allowedTools`. Codex
   exec defaults to read-only and escalates through `--sandbox`. Factory
   Droid uses `--auto` tiers. Antigravity's `/goal` runs without intermediate
   input. These products support execution without intermediate approval.
   Each invocation selects its permission setting.
5. **Headless interfaces commonly emit JSON.** Claude Code, Codex CLI,
   Cursor CLI, Gemini CLI, Amp, OpenHands, and Factory Droid emit a final JSON
   object, a JSON Lines event stream, or both. Their documented failure modes
   return a nonzero exit status.
6. **Persistent sessions accept follow-up instructions.** `codex exec
   resume`, `claude --resume`, Cursor
   follow-up runs, Devin `sendMessage`, and Jules `sendMessage` all continue
   a prior conversation. Each listed product uses follow-up messages to steer
   an existing session.

The Model Context Protocol (MCP) is the common tool-server protocol. An MCP
client over standard input and output reaches most general-purpose harnesses
in the survey. Many interactive harnesses expose the Agent Client Protocol
(ACP). Aider, SWE-agent, and Exo require separate integration paths.

## Where foe's combination is distinct

The comparison below is against documented behavior of the surveyed systems.

- **Program identity as a hash computed from configuration alone.** foe
  defines `identity(program)` as a SHA-256 digest over instructions, tool
  specifications, grant policy, budget, termination condition, child program
  identities, runtime-contributed strings, and runtime version. Computing the
  identity does not start an executable, access a network, or read a
  credential. No surveyed system documents a
  content-addressed identity for an agent configuration. Managed Agents
  references an agent by a server-assigned id; Claude Code subagents are
  named files. Exo issue
  [154](https://github.com/exoharness/exo/issues/154) proposes a harness hash,
  but the comparison commit does not implement one. foe is the only implemented
  system in the survey with this content-addressed program identity.
- **Every model request reconstructable from an append-only log, with
  byte-stable prefixes.** Transcript recording is common: Codex `--json`
  streams events, SWE-agent writes trajectory files, Cursor's API exposes a
  per-run stream, and Managed Agents persists event history.
  Durable-execution engines journal step results. Exo also stores append-only
  conversation events. None of these systems documents that the exact
  request sent to the model is derivable from the record. None documents
  byte-stable request prefixes across steps and sibling episodes for cache
  hits. Cursor's documentation states that its stream does not replay prior
  runs. foe's replay guarantee and cache-stability commitment have no
  documented counterparts in the survey.
- **Canonical and rendered tool results.** foe stores each tool's full JSON
  value in the log and shows the model a rendered projection. Claude Code
  limits MCP tool output to 25,000 tokens by default, and its documentation
  does not say where the remainder goes. Exo stores full tool results as
  versioned artifacts and places compact previews in conversation history.
  foe additionally records the exact model-visible rendering for every tool
  result and uses that rendering to reconstruct subsequent requests.
- **One allow list that derives both the tool's capability handle and the
  kernel ruleset.** Codex CLI also uses Landlock, and sandbox-runtime also
  restricts filesystem and network. Both express policy as a mode or a path
  list applied to the whole process. foe's grants name individual
  executables and type each tool by effect (`pure`, `reads`, `writes`,
  `execs`, or `spawns`). Program construction rejects a tool whose required
  effect its grants do not cover. The runtime gives each tool only the
  capability handle authorized by its effect. The
  Landlock mechanism is shared with Codex CLI; the effect typing and
  per-executable execute rules are not documented elsewhere.
- **Budget as a pool reserved down an episode tree.** Claude Code's
  `--max-budget-usd` is a cap that, when reached, stops running subagents
  and refuses new ones. Managed Agents has session budgets. foe additionally
  reserves the budget the child program declares from the parent's remainder
  at spawn and returns the unspent part when the child settles, alongside
  caps on depth,
  lifetime episode count, and concurrency. The reservation-and-return
  accounting has no documented counterpart.
- **Termination that a parent can route on.** A2A's task lifecycle includes
  `INPUT_REQUIRED`, and Claude Code on the web lets a session ask and wait.
  No foe episode waits for a person. Its `wait` tool holds an episode until
  the children it started have ended, and nothing holds one for an answer
  from outside the tree. An episode ends in one of `Completed`, `Blocked`
  with a code from a fixed vocabulary, `Exhausted`, or `Failed`, and the
  runtime itself ends an episode that repeats a tool call or a reasoning
  turn three times. OpenHands headless mode and Antigravity's `/goal` also
  omit a state that waits for a person. Among the surveyed systems, only foe
  documents a fixed blocking vocabulary for parent routing.

Every hosted product in the first table runs without a person present.
Every local harness also offers a permission mode for unattended execution.
The surveyed systems do not combine foe's declared completion condition,
whole-tree budget pool, stuck-agent detection, and closed blocking vocabulary.
OpenHands exposes a `stuck` status. Copilot imposes a hard session cap. Each
mechanism covers one part of foe's runtime contract. Local harnesses with
approvals disabled provide
unattended execution without foe's runtime contract. Claude Code on the web
and sandbox-runtime isolate credentials through network proxies. foe isolates
credentials by placing the model transport in a separate process.

## Where foe is behind

- **No hosted execution.** Every autonomous product provisions a machine,
  clones a repository, and runs without the caller keeping a process alive.
  foe runs where it is invoked. The invoking host must remain active until
  the episode ends.
- **No pull request path.** Surveyed hosted coding products report changes as
  a branch, pull request, or diff. foe reports an outcome value and a log.
  Producing a branch and a pull request requires the
  caller to grant `git` and a forge CLI as configured executables and to
  write instructions for them. No example configuration does this yet.
- **No entry points from issue trackers or chat.** Copilot, Devin, Jules,
  Codex cloud, and Kiro all accept a task from GitHub, Slack, Linear, or
  Jira. foe accepts a task from a command line or a host process.
- **Linux only for enforcement.** Landlock exists only on Linux. Codex CLI
  and sandbox-runtime cover macOS and Windows as well. On other platforms
  foe's `best-effort` mode cannot enforce the declared Landlock rules.
- **No domain-level egress control.** Landlock restricts TCP by port. Codex
  cloud, Claude Code on the web, and sandbox-runtime restrict by domain
  through a proxy with allowlist presets. foe's design removes network from
  executables where the kernel supports it and otherwise leaves egress to
  the host.
- **No MCP client.** foe's tools come from built-ins, configured
  executables, and host tools over foe's own line protocol. MCP is the common
  extension protocol among general-purpose interactive harnesses. A team
  with an existing MCP server for its issue tracker or database cannot
  attach it to foe directly.
- **No ACP implementation.** The ACP registry lists agents available to Zed
  and JetBrains. foe is absent from the registry.
- **No published external benchmark results.** Claude Code, Codex, Terminus,
  Cursor CLI, and mini-SWE-agent publish Terminal-Bench 2.1 scores and costs.
  foe has no published SWE-bench Verified or Terminal-Bench result, so its
  success rate on broad autonomous tasks is unknown.
- **No resume.** Episodes never resume; a later episode may be seeded from
  a log prefix. The persistent products and harnesses in the survey offer a
  follow-up message on a finished session. Exo also forks conversations from
  an event. foe requires a new episode for every follow-up.
- **No workspace checkpoint tied to a fork.** Log-prefix seeding preserves
  model-visible history, but foe does not capture or attest the corresponding
  filesystem state. Exo snapshots and rewinds sandbox state independently of
  conversation history. A reproducible foe fork requires the caller to
  restore the workspace that existed at the selected log event.
- **No browser or computer use.** Cursor Cloud Agents and Devin give the
  agent a browser. foe's built-in coding tools are `read`, `grep`, `edit`,
  and `bash`.
- **No stable interface.** The design document states that no interface is
  stable. Every integration path below depends on CLI and protocol contracts
  that may change.

## Default coding tool surface

The built-in coding program exposes `read`, `grep`, `edit`, and `bash`.
These four tools cover inspection, content search, exact text replacement,
and arbitrary local commands. The `bash` tool also provides file discovery,
file creation, version control, builds, and tests. This dependence gives
read-only programs less file-discovery capability and makes common file
operations harder to constrain and inspect.

### Search uses ripgrep's engine already

foe's `grep` implementation uses the `grep-searcher`, `grep-regex`,
`grep-matcher`, and `ignore` Rust crates from ripgrep. It searches in process,
honors `.gitignore` and `.ignore`, skips binary files, sorts results, and reads
each candidate through foe's bounded reader capability. Calling the external
`rg` executable would duplicate foe's core search behavior. It
would also move containment, output limits, and canonical result construction
across a process boundary.

foe should keep the in-process implementation and adopt ripgrep features
whose value is demonstrated by task evaluations. File-type aliases,
`.rgignore`, multiple include and exclude globs, and replacement previews are
possible additions. They should enter through the existing `grep` result
contract.

### Oh My Pi's Hashline is an evaluation candidate

The Hashline edit design originated in
[Oh My Pi](https://github.com/can1357/oh-my-pi/blob/2b5eed286de2d030e6e562705a120cd101061232/packages/hashline/README.md).
Its `read` and `grep` results carry a four-hex content tag for the normalized
file plus numbered lines. Its `edit` tool applies a compact line-oriented
patch to the tagged snapshot. Operations replace, insert, cut, paste, remove,
rename, or resolve a syntax block
([edit contract](https://github.com/can1357/oh-my-pi/blob/2b5eed286de2d030e6e562705a120cd101061232/docs/tools/edit.md)).
The patcher rejects unrecognized snapshot tags. When a tag identifies a stale
retained snapshot, the patcher applies a three-way merge only if it produces
one safe result. It also rejects edits to lines that the model did not
observe.

Hashline can reduce the old text repeated in an edit call. It also addresses
duplicate text that foe's exact-match editor requires the model to
disambiguate with surrounding context. Using Hashline would add four
model-facing concepts to foe's exact replacement list: a patch language,
snapshot state, registers, and optional syntax-block resolution.

A standalone
[Hashline implementation](https://github.com/quangdang46/hashline) derived
from Oh My Pi uses `N:hh` per-line hashes and supplies six MCP tools. Its
published measurements cover local latency, hash collisions, and deterministic
behaviors. Neither repository publishes a paired comparison of completed
coding tasks, model calls, retries, and tokens against exact-text editing.

foe can borrow Oh My Pi's file-version guard without adding its patch
language. `read` can emit one content hash for the observed file. `edit` can
accept the hash as an expected version and reject the entire batch when the
file has changed. The guard preserves the existing two-tool interaction.

Line-anchored ranges should replace exact-text editing only if a paired
evaluation shows a higher hidden-test pass rate or fewer calls at the same
token budget. The comparison should include duplicate blocks, edits after
line insertions, concurrent file changes, and ordinary multi-line
replacements. It should report completed tasks, incorrect edits, rejected
edits, model calls, and total tokens.

### Missing operations

The default surface has two material gaps. Both can be filled while keeping
the model-facing vocabulary small.

1. **Path discovery.** Add one read-effect tool named `files`. It lists paths
   under a granted root, filters by glob and entry kind, honors ignore files,
   and returns a bounded deterministic result. A read-only program can then
   discover an empty file, a binary asset, or a filename whose contents
   contain no searchable term.
2. **Create, remove, and rename text files.** Extend `edit` with these three
   operations. Creation must refuse an existing path. Removal must
   require the expected file version. Rename must refuse an existing
   destination. Keeping these operations under `edit` preserves one mutation
   concept and one write-effect boundary.

Symbol navigation can improve large-repository work, but a default
implementation would add language parsers, generated indexes, and new result
semantics. It belongs behind a configured or host tool until an evaluation
shows a consistent advantage over `grep`. Browser use, web search, issue
trackers, and credential-bearing version-control operations also belong in
host tools because their authority is installation-specific.

## Features to borrow from Exo

Exo's useful mechanisms fit foe when each mechanism preserves a fresh,
bounded episode and a fixed program identity.

1. **Forks tied to workspace state.** Expose log-prefix seeding through a
   `foe fork` command. Record a caller-supplied workspace snapshot or baseline
   identifier in the new episode. Refuse the fork when the restored workspace
   does not match that identifier.
2. **Content-addressed spill values.** Add a content hash to every spill
   locator. Large canonical values then retain immutable identity without a
   general artifact database.
3. **Verification-gated self-extension.** Let a child episode modify foe in a
   separate worktree. Build and test the candidate before starting it as a
   fresh program identity. The active episode continues under its original
   binary and policy.
4. **Enforced process resources.** Derive CPU, memory, and process-count
   limits through Linux control groups (cgroups). Record whether the host
   enforced each requested limit or observed it without enforcement.
5. **History retrieval after compaction.** Add model-visible history lookup
   through the context-policy seam only when compaction is active. A
   compaction-loss evaluation must show that retrieval improves obligation or
   evidence recovery before it becomes a built-in tool.

Outer virtual machines, hosted sandboxes, trigger schedulers, and
credential stores should remain host integrations. foe can record their
attestations while its inner grant policy remains authoritative. Persistent
agent identities, resumed completed episodes, hot-loaded tools, mutable
executor policy, and agent replacement of the active kernel would weaken the
bounded program contract and should stay outside foe.

## How a conventional harness would delegate to foe

Each path below names the mechanism, the task payload, what returns, and
what foe must implement. Shell invocation and the Python package are
implemented. The `foe mcp` and `foe acp` subcommands remain proposals. The
paths are ranked by leverage at the end.

### Claude Code

**Shell invocation.** Claude Code's Bash tool runs `foe "..." --config
CONFIG --headless`. The harness operator pre-approves the binary with
`--allowedTools "Bash(foe *)"` or a `permissions.allow` rule. The payload is
the task string and a configuration path. Standard output receives one final
JSON outcome. Progress goes to standard error, and events go to the log. Exit
status maps to the outcome kind, so a `claude -p` script can branch on it.
This path works from every harness with a shell tool.

**MCP server.** A proposed `foe mcp` subcommand speaks MCP over standard input
and output. It exposes `run_episode`, which accepts `config`, `task`, and
optional `budget` overrides. It also exposes `identity`, which returns the
program hash. Claude Code registers the server with
`claude mcp add foe -- foe mcp`. The tool result is the same final JSON object.
Two constraints shape this path.

Claude Code caps MCP tool output at 25,000 tokens, so the result must be
compact and point to the log rather than include it. Claude Code aborts a
stdio tool call that sends nothing for 30 minutes. `run_episode` must
therefore emit progress notifications while the episode runs. It can instead
return a task handle under the 2026-07-28 tasks extension. The client then
polls the handle with `tasks/get`.

Claude Code subagents cannot be external processes. A subagent definition can
present foe as a named target by permitting only `mcp__foe__run_episode`.

**Agent SDK.** A Python program using the Agent SDK defines an in-process
tool that calls foe's standard-library Python package and returns the typed
outcome. This path suits pipelines already written against the Agent SDK.

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
app, CLI, and SDK. The proposed `foe mcp` server serves both.

**ACP agent.** Cursor and Antigravity are agents rather than ACP clients,
so ACP does not connect them to foe. ACP matters for editors: Zed,
JetBrains, Neovim, and Emacs clients can launch any registered agent. A
`foe acp` subcommand would answer `session/new` and `session/prompt`, stream
`session/update` notifications from the log, and never send
`session/request_permission`. It would end the turn with `end_turn` on
`Completed` and `refusal` on `Blocked`. That subcommand makes foe launchable
from those editors and discoverable through the registry. The payload contains
the prompt text and the working directory supplied by the client. foe maps the
directory to read and write roots from a default program.

### Ranking by leverage

1. **CLI contract with JSON outcome and exit codes.** Prerequisite for every
   other path. This contract is implemented and is sufficient on its own for
   Claude Code, Copilot CLI, Gemini CLI, Amp, Cline, and any CI system.
2. **MCP stdio server with progress or task handles.** One implementation
   reaches most general-purpose harnesses surveyed, and it gives the host a
   typed tool rather than a shell string.
3. **ACP agent.** Reaches editors and the registry; modest implementation
   cost because foe's log already produces the events ACP streams.
4. **Python SDK as an in-process tool.** Serves Agent SDK and OpenHands SDK
   users; narrow audience.
5. **A2A server.** Valuable only once foe has a hosted form, because A2A
   assumes an HTTP endpoint that stays up.

## How a human delegates to foe

A command shape consistent with the design is:

```
foe "Make cargo test pass in crates/log after adding a Seeded event. Keep crates/view unchanged." \
  --config PROGRAM.json --headless --log-dir runs/2026-08-21-seeded
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

- An exit status and one JSON line holding the outcome kind and, according
  to the kind, the returned value, the blocked code and message, or the
  exhausted limit. The budget consumed and the log directory are read from
  the log rather than from that line.
- The log directory, which a later `foe view DIR` writes to standard output
  as a single HTML file showing the episode tree, every tool call with
  rendered and canonical forms, budget consumption, sandbox status, and the
  outcome.
- The working tree as the episode left it. foe does not commit. If the
  program grants `git`, the instructions can require a commit on a named
  branch, which is how the PR report-back of hosted products is
  reproduced.

A person who delegates the same program repeatedly keeps the program file
under version control. Each run records the identity reported by `foe plan
--config PROGRAM.json --json`. Two runs with the same hash differ only in
their task and model responses.

## Recommendations

The five items below are in priority order. Each names the gap it closes.

1. **Complete the common file operations.** Add deterministic path discovery
   and explicit file lifecycle operations. Add a file-version guard to
   `read` and `edit`. Compare exact-text and hash-anchored editing on a paired
   model evaluation before changing the default edit representation.
2. **Make forks reproduce workspace state.** Expose the implemented seeding
   rules through `foe fork`. Bind each fork to a workspace snapshot or
   baseline identifier, and hash every spilled value. This closes the largest
   evidence gap revealed by Exo.
3. **Enforce structural resource limits.** Use root-held leases for
   whole-tree episode and concurrency caps. Apply cgroup limits for CPU,
   memory, and process count. Record observed limits separately when the host
   cannot enforce them.
4. **Publish assessed quality and cost.** Run foe's deterministic micro
   evaluation on every release. Publish a fixed Harness-Bench or
   Terminal-Bench slice with repeated attempts, model, program identity,
   tokens, calls, wall time, and typed outcome distribution.
5. **Add interoperability as adapters.** A `foe mcp` server would give
   existing harnesses typed access to bounded episodes. ACP would make foe
   available in editors. Pull-request creation, domain-filtered egress, hosted
   sandboxes, and issue-tracker triggers should use configured executables or
   host tools so their credentials and authority stay outside the episode.
