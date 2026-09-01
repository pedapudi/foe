# Telemetry

Telemetry reads a finished episode log and writes one OTLP trace per
episode: what kind of work the episode did, what it cost, how it ended, and
which tools it called. It is native to `foe` and off by default: one
machine-level file turns it on, and every run then emits after its episode
ends. The kernel carries no telemetry code, `crates/telemetry` depends on
`crates/log` alone, and an installation that never enables telemetry
produces none and behaves no differently.

## What it is a function of

The output is a function of three inputs: the episode log, the rules
compiled into the crate, and a local key stored beside the capture file.
Nothing else reaches it. The crate opens no network connection, calls no
model, reads no environment variable, and starts no process.

Four properties follow.

- **The loop pays nothing.** Telemetry runs after the episode ended, over
  `episode.jsonl` as it was written. There is no hook in the kernel and no
  cost inside a running episode.
- **The same log produces the same bytes.** Nothing draws on a random
  source or on the wall clock. Span and trace identifiers are derived by
  hashing (below), and every timestamp is a time the log itself recorded.
- **Preview is emission.** `foe telemetry` runs the emission and prints
  the result, so what a person reviews is what a run wrote, down to the
  pseudonyms.
- **Enablement is outside the contract.** The switch is a machine-level
  file, never a key in a contract configuration, so turning telemetry on or
  off changes no contract fingerprint: the same contract observed and unobserved
  is one contract.

## Enabling, emitting, inspecting

`~/.config/foe/telemetry.json` turns telemetry on:

```json
{ "capture": "~/.local/state/foe/telemetry/otel.jsonl" }
```

`capture` names the file every emission appends to; a leading `~/` resolves
against the home directory of the passwd database, like every other path
under `~/.config/foe/`. The capture's directory also holds the local key.
With the file present, `foe` emits one JSON object per episode — the root
and every descendant under `children/` — after each run ends, and prints
one line saying how many episodes went where. A file that exists but cannot
be read is an error on runs and warns rather than silently disabling.
Telemetry failures never change a run's outcome or exit code.

```
foe telemetry LOG... [--json]
```

prints the emission for a person: the top category with the evidence behind
it, the totals, the scrub counts, and one line per span. `--json` prints
the exact payload bytes. With telemetry enabled the pseudonyms are the
emitted ones, because the key is the capture's own; disabled, a stand-in
key is used and the output says so.

The run emits from the log it just wrote, so the writer and the reader are
one binary and no line can be unreadable through version skew. The rule
below therefore matters to `foe telemetry` over foreign or older logs, and
to any future reader of archived captures.

A log that stops without `episode/end` was cut short, and everything before
the cut is still emitted. Structural validation is not applied.

A line whose event shape this build cannot read is a different matter, and
emission refuses the whole log over even one. The scrubber learns the values
it must remove from the log itself, so the unreadable line may be the one
carrying the granted roots, and the known-value layer then quietly removes
nothing. Version skew between this binary and the runtime that wrote the
log is the realistic cause.

`foe telemetry` still renders it, because seeing what the log holds is how a person
finds out why it cannot be read. It says up front that emission refuses the
log, so nothing in the output is mistaken for what would be written.

## Categories

The classifier reads typed log fields and nothing else: file extensions
from `read` and `edit` path arguments and `grep` globs, the head of each
segment of every `bash` command line, and the tool names called. Episode
structure — spawned children, workflow nodes — casts no vote: it says how
the work was arranged rather than what it was about, so an episode whose
only evidence is structure is unclassified. It never reads the task
text, the model's output, or a tool result body. It therefore cannot be
steered by anything the model wrote, and it cannot leak what the episode
read. The cost is that it sees only the shape of the work.

A shell command line is split on `&&`, `||`, `;`, `|`, and newlines before
the head is taken. A segment beginning with `cd` yields to the segment after
it, and leading `VAR=value` assignments are skipped. For a small set of
dispatchers whose subcommand carries the meaning, the head and its
subcommand vote together, so `cargo test` and `cargo build` are separate
evidence.

Two levels. The top level is seeded from the task categories OpenRouter
ranks model usage by: programming, data analysis, technology, science,
translation, legal, finance, health, academia, marketing, trivia, roleplay.
The structural rules reach only the first three; the rest of the list is
present so that a later topical layer has somewhere to land. Seven
subcategories sit under the top level: debugging, testing, build,
refactoring, documentation, data-analysis, and infrastructure. Work over
tabular data rolls up into data analysis whatever language it is written
in, and provisioning or operating machines rolls up into technology.

Each piece of evidence votes for one bucket. Two views of the votes come
out, and both are emitted. The counts are multi-label and include roll-ups, so `programming`
carries its own votes plus every subcategory's. The single top bucket is
chosen on direct votes alone, because once roll-ups are added a top-level
category holds the sum of its children and no subcategory could ever win.
On direct votes the two levels are peers: `programming` means source work
with no more specific signal, and it wins only when the generic evidence
outweighs every specific kind. Equal counts go to the more specific label.
An episode no rule matched is `unclassified`.

Every matched rule is emitted with its bucket and count. That list is the
whole explanation of the choice, and it is safe to emit because the tokens
come from the shipped rules and the tool vocabulary rather than from the
episode's content.

There are no confidence scores. A rule vote is a count of matches, and a
number formatted like a probability would be read as one.

**Accuracy is unmeasured.** No labelled sample exists, so the crate makes
no accuracy claim. The measurement path is part of the design: once real
capture accumulates, hand-label a sample of episodes, publish the confusion
matrix against these rules, and let that decide whether a trained layer is
worth adding.

## What is scrubbed, and what is never emitted

Most protection comes from what the schema omits. It carries counts,
durations, token usage, outcome terms, tool names, hashes, and category
labels. Free text is limited to two fields, both short and written by a
tool rather than by a model:

- `tool/result.subject`, the one line a tool writes naming what it acted on
  and what came of it, which on a failure is the error line.
- The outcome's detail, which exists only for a blocked episode's message
  and a failed episode's error.

A completed episode's value is the report the model wrote, and it is a
result body, so it is never emitted. Neither is the task text, the system
prompt, any model output, any tool result body, or any file content.

Six layers run over those two fields, in order.

1. **Invisible-character removal.** Zero-width spaces, joiners and
   non-joiners, the word joiner, the byte-order mark, the soft hyphen, and
   the directional marks are removed before anything else runs. They render
   as nothing, so removing them changes no reader's view of the field, and
   a value interrupted by them is one value again for every later layer.
2. **Encoded-run scanning.** Percent-encoded runs and base64-shaped runs
   of 16 characters or more are decoded, and the decoded text is checked
   against the known values and the format detectors. A run whose decoding
   hides either is replaced whole: the encoded form is the leak, so the
   encoded form is what the pseudonym stands in for. This layer runs before
   substitution so that a known value matched literally inside an encoded
   run cannot split the run before it is judged.
3. **Known-value substitution.** The known set is built from the log
   itself: every absolute path in the resolved configuration, which covers
   the granted roots and the workspace, the directory the log was read
   from, and the user name component of any `/home/<user>` or
   `/Users/<user>` path among them. Each value is matched in its variant
   forms — as recorded, with a trailing slash, JSON-escaped, and
   tilde-abbreviated against the home directory. All four forms of one
   value carry the same pseudonym, so a join over the workspace does not
   split four ways.
4. **Format detectors,** all in one pass. Key material: PEM headers, `ssh-`
   keys, JWT shape, and the token prefixes issued by cloud, source-forge,
   model, chat, package-registry, and payment providers. Identifiers and
   addresses: email addresses, URLs carrying a host or user information,
   UUIDs, MAC addresses, and IPv4 and IPv6 addresses. Payment cards: digit
   runs of 13 to 19 digits, with spaces or hyphens allowed, that pass the
   Luhn checksum — a run that fails the checksum is left alone, because a
   version string or an issue number shaped like a card is not one. Runs
   that carry no shape but too much information: bare hexadecimal of 32
   characters or more, and base64-shaped runs of 20 characters or more that
   mix digits with upper case and whose Shannon entropy reaches 3.5 bits
   per character.
5. **Path componentization** for whatever slash-separated path remains.
   Components in a dictionary of universal names are kept, as is the
   alphabetic extension of the last component. The dictionary holds three
   groups. The standard filesystem hierarchy: `usr`, `bin`, `sbin`, `etc`,
   `var`, `tmp`, `opt`, `home`, `root`, `dev`, `proc`, `sys`, `run`, `mnt`,
   `srv`, `boot`, `log`, `share`, `local`, `include`. The directory names
   common to source trees: `src`, `lib`, `test`, `tests`, `docs`, `target`,
   `build`, `dist`, `node_modules`, `git`. The system files and commands
   that are the same on every machine, among them `null`, `bash`,
   `useradd`, `systemctl`, `nginx`, `sshd_config`, `passwd`, `hosts`, and
   `resolv.conf`. Everything else is replaced. Relative paths are included,
   because tool subjects report paths relative to the workspace.

   Keeping these names costs no privacy and buys the evidence back.
   Masking `null` protects nobody, and `/dev/null` reduced to two
   pseudonyms hides the fact that the episode discarded the output. A
   component that could name a person, a project, or a task is not in the
   dictionary; when in doubt, it is masked, so `/etc/nginx/staging.conf`
   keeps `etc` and `nginx` and replaces the rest.
6. **Pseudonyms.** Every replacement is `⟨t:xxxxxxxx⟩`. The digest is
   HMAC-SHA256 of the value under the local key, truncated to eight
   hexadecimal characters. The tag `t` is one letter naming what was
   replaced: `p` path component, `u` user, `e` email, `h` host, `s`
   secret-shaped.

Eight hexadecimal characters sit below every detector's length threshold,
and the `⟨` and `⟩` delimiters fall outside every detector's character
class. A pseudonym therefore trips no detector, which is what makes
scrubbing idempotent: scrubbing an already scrubbed string returns it
unchanged. A test asserts this for every type tag in the shapes a second
pass would see.

### The local key

The key is 32 bytes read from the system random source on first use and
stored at `<out-dir>/key` with mode 0600. It is never emitted, never
logged, and not part of the contract fingerprint.

Keyed hashing is what makes a pseudonym irrecoverable: an unkeyed hash of a
user name falls to a word list in milliseconds. The pseudonym for one value
is stable across every episode written under one output directory, so
cross-episode joins hold. It is meaningless outside that directory, so
cross-installation joins are impossible. That is a property of the design.

### Digest collisions

The runtime's own identifiers — contract fingerprint, build hash, episode
identifiers — travel in dedicated schema fields that the scrubber never
scans. Inside the two free-text fields, a long hexadecimal run is masked
even when it is probably a commit hash. Losing a commit hash from an error
line is an acceptable loss. Shipping a key that looked like one is not.

### The self-check

After scrubbing, the detectors and a known-value scan run over the output.
Any hit fails the whole emission with a named finding, and no capture file
is written or appended to.

The known-value scan is looser than the substitution above.
Substitution must match exactly, because folding case would corrupt paths
that differ only by case; detection may match however loosely it likes. A
known value that reaches the output in a form the substitution did not
cover is therefore caught here. A fixture whose error line names a user in
upper case exercises that path and fails emission:

```
scrub self-check failed, nothing emitted: known user survived scrubbing in
tool/result.subject seq 5
```

## The schema

Traces only. There is no metrics signal; numbers ride as span attributes,
and a collector derives metrics from them. The encoding is OTLP's JSON
form, written directly rather than through an OpenTelemetry SDK, which
would bring a transport stack this add-on must not have. A golden file
pins the encoding byte for byte, and it has been accepted by a stock
collector over the OTLP/HTTP receiver.

Each episode becomes one trace: a root span for the episode, a child span
per model call, and a child span per tool call. Identifiers are derived
rather than drawn from a random source, because the output must be a
function of the log:

- trace identifier: the first 16 bytes of SHA-256 over the episode
  identifier.
- span identifier: the first 8 bytes of SHA-256 over the episode
  identifier, the span kind, and the sequence number of the event the span
  was built from.

Timestamps are the log's own event times in milliseconds, converted to
nanoseconds.

### Every field answers a question

A field that answers no question is not emitted. This table is the gate.

| field | the question it answers |
|---|---|
| `service.name` | which system produced this trace |
| `foe.contract.fingerprint` | which contract configuration ran |
| `foe.runtime.version`, `foe.runtime.build` | which build produced the log |
| `foe.schema.version`, `foe.taxonomy.version`, `foe.ruleset.version` | may these two payloads be compared, or did the fields, the buckets, or the rules change between them |
| `foe.scrub.*` | how much of the free text was replaced, by type |
| `foe.episode.id` | which episode, and which children belong to which parent |
| `foe.outcome.kind` | what fraction of episodes complete, block, exhaust, or fail |
| `foe.outcome.exit_class` | which limit was reached, or which blocking condition was hit |
| `foe.outcome.detail` | what a blocked or failed episode said about why |
| `foe.completion.provenance` | how a completed episode's completion was established: `verifier`, `reviewed`, or `model-report`; absent for any other outcome |
| `foe.verification.runs`, `foe.verification.findings` | how often authoritative verification ran in the episode, and how many findings it returned in all |
| `foe.workflow.recovery.interventions` | how many model-selected recovery actions the workflow applied |
| `foe.workflow.recovery.actions` | counts by the applied `retry`, `amend`, `skip`, or `abort` action; an unrecognized value is `unknown` |
| `foe.workflow.empty_substitutions` | optional model children whose blocked or exhausted outcome contributed the declared `empty` value |
| `foe.model.provider`, `foe.model.model` | does outcome or cost differ by route |
| `foe.category`, `foe.category.top_level` | failure rate and cost distribution by kind of work |
| `foe.category.counts` | which categories an episode belongs to at once, and how strongly, since one episode can be both testing and infrastructure |
| `foe.evidence` | why the classifier chose that category |
| `foe.tokens.input`, `foe.tokens.output`, `foe.tokens.cache_read` | what an episode costs, and how much of the input the cache served |
| `foe.tokens.cache_read_fraction` | what fraction of the input the cache served, as `cache_read` divided by `input`, on each model-call span and on the episode span; absent when no input token was recorded |
| `foe.model_calls` | how many turns a kind of work takes |
| `foe.tool_calls`, `foe.tool_errors` | how often tool calls fail, per episode |
| `foe.duration_ms` | how long a kind of work takes end to end |
| `foe.step` | which turn a span belongs to |
| `foe.stop_reason` | how often responses end on length rather than on completion or a tool call |
| `foe.tool.name` | the tool-mix profile of a kind of work |
| `foe.tool.seq` | where in the log a span's tool call is |
| `foe.tool.duration_ms` | which tools dominate wall-clock time |
| `foe.tool.is_error` | which tools fail most |
| `foe.tool.subject` | what a failing tool acted on |

Input, output, and cache-read tokens are separate throughout, following the
repository's standing convention that a single token total hides the three
different costs it sums. The cached-input fraction is emitted beside the
two counts it is derived from, so a collector can chart cache efficiency
without recomputing it, and its absence distinguishes an unmeasured spend
from a cold cache.

Span status is `OK` for a completed episode and a tool call that did not
error, and `ERROR` otherwise.

### Completion provenance

`foe.completion.provenance` states how a completed episode's completion
was established, derived from the log alone — no episode event carries
it. The derivation lives in `crates/telemetry/src/extract.rs` and is the
one place it is implemented.

- `verifier` — the completing value was accepted by an authoritative
  `verification/result`. For an episode running the free loop that is the
  log's last such event, since acceptance is what completes it. For a
  workflow episode it is an accepted event after the completing terminal
  `workflow/node-end` (the episode's `done_when.verify`), one between
  that firing's start and end (the node's own `verify`).
- `reviewed` — no verifier accepted the completing value, but the
  completing terminal node is a model node that received another model
  node's completion value among its inputs: an independent review
  episode, as in the built-in coding workflow.
- `model-report` — neither: completion rests on the model's own account.

A workflow that completes through a branch label with no successors
flags no terminal node; the last errorless `workflow/node-end` then
stands in as the completing firing.

### Workflow correction evidence

Workflow correction fields are derived after the episode ends. The
runtime records the decisions and outcomes it already applies. Telemetry
counts those typed events without adding an event or a running-episode
instrumentation hook.

A `workflow/recovery` event contributes one intervention and action. A
collector combines these fields with `foe.outcome.kind` to compare
completion rates. Such a comparison records association rather than
causation. Recovery causes remain in the local log because their string
field is outside telemetry's closed emission vocabulary.

An optional model child can contribute its declared `empty` value after
ending blocked or exhausted. Its `workflow/node-start` names the child
episode. A blocked or exhausted `spawn/end` for that child, followed by a
non-null `workflow/node-end` for the same firing, is an empty substitution.
This derivation uses typed links and outcomes rather than error text.

## Reading the capture

`otel.jsonl` holds one OTLP JSON object per line. A collector reading that
file is the destination path; the crate sends nothing anywhere. Posting a
line to a collector's OTLP/HTTP receiver at `/v1/traces` with
`Content-Type: application/json` is enough to ingest it.

## Limits

- Classifier accuracy is unmeasured, as stated above.
- Scrubbing over-masks. A version number shaped like an address, a
  timestamp shaped like an IPv6 address, and a commit hash are all
  replaced. Over-masking costs readability, and under-masking costs a
  secret.
- Pseudonyms are stable within one output directory. Emitting the same
  episode to two directories yields two different sets of pseudonyms.
- Scrubbing catches values the log names and values with a recognizable
  format. A personal name typed into a command line is neither, so
  `git config user.name 'Ada Lovelace'` reaches the capture with the name
  intact while the email address beside it is replaced. Closing that gap
  needs a name lexicon, which this version does not carry.
- A tool subject is already truncated by the tool that wrote it, so a long
  error line reaches telemetry cut short.
- A single line this build's event types cannot read costs the whole log:
  `emit` refuses it rather than emit under scrubbing it cannot vouch for.
  Reading such a log needs a build whose event types match the runtime that
  wrote it.

## Not in this version

Metrics signal, an on-disk database, network destinations, scrubbed
trajectory export, a topical lexicon layer, task-text emission, and
confidence scores.
