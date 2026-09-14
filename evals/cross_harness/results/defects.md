# Defects the cross-harness evaluation found

Each entry names the behaviour, the evidence, the reproduction, and what the
evaluation must say about it. These are recorded during a run and repaired
after it, because a repair changes what a harness receives and the run is
pre-registered.

## One prompt cache key serves every episode that runs a contract

`crates/transport/src/format/responses.rs` derives `prompt_cache_key` from
the system prompt and the tool names alone. Its intent is stated in the
comment: the head an episode repeats unchanged names the cache the episode's
growing prefix belongs to. Two episodes running the same contract therefore
receive the same key, and the key is what routes a request to the cache that
holds its prefix. Unrelated conversations are sent to one slot, where each
evicts the prefix the last one wrote.

The evaluation's foe episodes used seven keys for thirty-three conversations
across six tasks. One key served seven conversations.

Measured against the other harness on the same provider, model, and route,
by turn position within a conversation:

| turn | cache hit, foe | cache hit, the other harness |
|---|---|---|
| 1 | 4% | 17% |
| 2 | 1% | 73% |
| 3 | 1% | 85% |
| 5 | 4% | 87% |
| 7 | 24% | 92% |
| 10 and later | 43% | 98% |

foe's requests are not the cause of the miss: every request in an episode is
a pure extension of the last, verified by comparing the logged message arrays
of consecutive `model/request` records. The prefix is stable and the head is
identical; only the routing key is wrong. An episode climbs toward a hit rate
as it runs because a long episode eventually holds the slot it shares.

The cost, over the development and holdout attempts scored so far: foe read
27% of its input from cache against the other harness's 93%, on comparable
work. Over the two solvable tasks and four attempts each, foe's uncached
input totals 2,732,102 tokens against the other harness's 246,818, which is
683,025 against 61,704 per attempt.

Reproduction: run any two foe episodes of one contract back to back and read
`cache_read` in the `assistant/message` records of the second.

Repair: name the cache after the conversation rather than the contract. The
episode identifier is stable for the life of an episode and distinct between
episodes, which is what the key routes on. Carried in
https://github.com/pedapudi/foe/pull/246.

Repairing the key alone changed nothing measurable: the episode below, run
with a per-episode key, still read 15.9 percent of its input from cache. The
larger part of the gap is the missing request header recorded under "The
Codex route omitted the header the backend routes cache affinity on".

## A granted executable is unreachable by name from bash

`crates/code/src/lib.rs` gives the `bash` and `session` tools a fixed
`PATH` of the six system directories. A document that grants execute on a
toolchain outside them makes the toolchain runnable by absolute path and not
by name, so an ordinary build script fails.

On the `correlation-as-inbox-source` holdout attempt under `foe-configured`,
the workspace's own check suite exited 127 with `cargo: not found` at three
separate points in the episode. The agent read that as the toolchain being
absent, ended the episode `blocked` with `missing-capability`, and named the
evidence honestly. The hidden tests then passed on the workspace it had
already written, so the attempt scores `wrong-stop`: a correct diagnosis of
what the agent could observe, from an environment that misled it.

The other harness inherits the invoking `PATH` and finds the toolchain, so
the two arms did not run the same environment. The check tool is unaffected,
because the evaluation's check wrapper sets its own search path from the
document's tool roots; only a command the model runs itself is affected.

Reproduction: grant execute on a directory outside the six system
directories and run a bash command naming an executable in it.

Repair: extend the `bash` and `session` search path with the execute roots
the contract grants. The grant already names them, so this adds no
configuration and no environment variable, and it makes the search path agree
with the permission. Carried in https://github.com/pedapudi/foe/pull/246.

Bearing on the result: twelve of the fourteen foe attempts of that run
contain a command reporting the toolchain missing, and two of them cite it in
their own reported evidence. The reach is the whole of foe's side of that
run, not one attempt.

This entry also understated the defect itself. Putting the granted
directories on the search path makes the toolchain resolvable and not
runnable: the shell also names the workspace as the home directory, so the
toolchain manager looks for its installation there, cannot create one because
the write grants name directories under the workspace rather than the
workspace itself, and falls back to a download the sandbox refuses. Both
halves are repaired, and the second is what the measurement turned on.

## A subprocess cannot write an existing file under a granted write root

With the workspace named in `grants.write`, a build tool run through `bash`
is refused when it opens `<workspace>/target/debug/.cargo-lock`:

```
error: failed to open: .../root/workspace/target/debug/.cargo-lock
Caused by:
  Permission denied (os error 13)
```

The file exists, is owned by the user, and is mode 664; the directory holding
it is mode 775. The contract grants write on the workspace, which contains
it. The refusal happened 55 times across the two foe arms of one task in the
run, and the other harness, whose sandbox grants write on the same workspace,
was refused zero times.

The sharper form of it, from the check tool rather than from cargo: a
subprocess creates a directory under the granted write root successfully and
is then refused creating a file inside the directory it just created.

```
mkdir -p <workspace>/.check-tmp            succeeds
printf ... > <workspace>/.check-tmp/CACHEDIR.TAG   Permission denied
```

The refusal is not permanent within an episode: a later run of the same
check suite in the same workspace exits zero. What triggers it is not
established.

What this is not, each checked rather than assumed:

- Not the file mode. The same file in the other harness's workspace has the
  same mode and is written.
- Not a missing write rule. The compiled policy names the workspace as a
  write root, and a write root that the in-process writer binds is added to
  the kernel ruleset separately, so a subprocess is covered either way.
- Not a missing truncate right. The write access set the policy compiles
  includes truncation from the kernel interface version this host runs.

The effect is a cost rather than an outcome: the arms work around it by
building into a directory they create themselves, which takes model calls the
other harness does not spend. On the one task where both foe arms and both
of the other harness's arms have completed, foe spent 59 and 38 model calls
against 36 and 19. Any comparison of cost carries this until it is closed.

A minimal case does not reproduce it. One scripted episode under an enforced
sandbox, granting read, write and execute on an empty workspace, running a
configured executable that creates a directory beneath the grant, a file
inside that new directory, and a file at the root of the grant: all three
succeed. Adding an execute grant on the same directory as the write grant,
which the evaluation's documents carry and the minimal case first lacked,
changes nothing. So the refusal needs something the minimal case does not
have, and the five candidates ruled out above are joined by a sixth: it is
not the bare combination of a write grant, a subprocess, and a newly created
directory.

What the minimal case lacks that the run has: a workspace of thousands of
files rather than an empty one, a build tool spawning many processes at once,
and an episode that has already run other tools. The refusal being transient
within a single episode points at the second of those.

Reproduction, in the run rather than in isolation: run any task whose check
suite builds, and read the tool results for a refused write under the
workspace.

Bearing on the result: while the check script treated a refused scratch
directory as fatal, this defect stopped the check suite from running at all
under every foe arm, so the verifier the configured arm is built around
produced nothing. The script no longer treats it as fatal. What remains is
a check that sometimes reports this refusal as a finding, which costs the
arm a repair cycle it did not earn.

## The Codex route omitted the header the backend routes cache affinity on

The Codex backend keys prompt-cache affinity on the `session-id` request
header. Its own client sends `session-id` and `thread-id` on every request,
carrying the same value it sends as `prompt_cache_key`, and its source
states the reason beside the code that builds the header. foe's
`openai-codex` route sent `prompt_cache_key` in the body and no such
header, so each request was routed to whichever replica the balancer chose
and reached the replica holding its prefix by chance.

Evidence, from one four-node episode of `duplicate-grant-roots` under
`foe-configured`, captured at the wire from a build that prints each
request body:

- The body is correct. Every request's input array is a byte-identical
  extension of the one before it, with the same instructions, tool
  definitions, model, reasoning settings, and one cache key for the episode.
  Storage is off because the backend refuses anything else, with
  `HTTP 400: Store must be set to false`, so the harness compared sends the
  same.
- The misses are not a prefix problem. 7 of 37 calls read from cache, and
  each of those read 51 to 98 percent of its input; the other 30 read
  nothing. A prefix that is cached and served only on some calls is a
  routing pattern.
- Adding the two headers, and nothing else, on the same task, model, route,
  and account: 43 of 45 calls read from cache, 89.9 percent of input against
  15.9 percent before, with every call from the fourth step of each node
  above 83 percent. The harness compared reads 93 percent on comparable
  work.

Reproduction: run one foe episode on the `openai-codex` route from a build
before `dba1a859` and read `cache_read` in its `assistant/message` records;
repeat from that commit.

Repair: the route sends `session-id` and `thread-id` equal to the cache key,
which is now the episode digest in UUID form. Carried in the same branch as
the check-write and timeout repairs.

Bearing on the result: the cost comparison recorded so far is a measurement
of this defect rather than of the harnesses. foe's uncached input on the two
solvable tasks, 683,025 tokens per attempt against 61,704, was produced
with a route that missed the cache on four calls in five. Hypothesis H2
still predicts foe spends more input than the other harness, because its
graph gives each node a fresh episode that re-reads the tree; whether that
holds, and by how much, is measurable only from attempts run with the
header. Every foe cost figure in the records that predate it is withdrawn
from the comparison.

## A verifier that hangs ended the episode before the model could report

An executable verifier killed at its timeout was a protocol failure by
documented design: the episode ended `failed`, carrying the kill and
nothing the model had learned.

Evidence: `unwritten-pipe-context` under `foe-configured` in the autonomy
run of 2026-09-13. The implementing node completed its change in 11 calls
and its node verifier, the task's check suite, waited on a pipe the
workspace never writes. The runtime killed the verifier at 120 seconds and
ended the episode: status `failed`, code none, evidence "verifier `check`
failed: [killed after 120 seconds]". The grader scored it `wrong-stop`. On
the three sibling non-terminating tasks the same arm ran the suite itself,
saw the hang, and stopped with `goal-unreachable`; the difference is only
whether the model or the verifier met the hang first.

Reproduction: any contract whose `verify` names an executable that does not
return within `timeout_seconds`, from a build before `db8b6a01`.

Repair: a timed-out verifier returns one finding, that the candidate could
not be verified within the bound; the node re-fires on it up to `retries`
times and the episode then ends `blocked` with `verification-unsatisfiable`.
A nonzero exit or an end by signal is still a failed verifier. Carried in
`db8b6a01`; `docs/config.md` states the rule.

Bearing on the result: one foe-configured attempt of sixty is scored
`wrong-stop` for this reason, and the record stands as scored. The run
document `runs/verifier-timeout.json` re-runs the four non-terminating
tasks under `foe-configured` with the repair, and campaign two reads those
records beside the first campaign's.

## The equivalent prompt does not carry foe's definition of `missing-capability`

foe's `block` tool tells the model to use `missing-capability` "when the
task needs a tool or permission this contract lacks". The codex-equivalent
prompt names the three codes only as a schema enumeration, and its prose
names the conditions "cannot be completed as stated", "ambiguous", and
"the goal is unreachable", which are the first two codes' definitions. The
arm called equivalent therefore steers its model toward `goal-unreachable`
on every obstacle, while foe's model has a definition under which a lock
another process holds, or a socket the sandbox denies, is a permission the
contract lacks.

Evidence: every foe-configured stop on a non-terminating task that the
grader scored `wrong-stop` carries `missing-capability` with a reason that
names the held lock or the denied socket: `waiting-check-suite` in the
first campaign and in the verifier-timeout re-run, and
`unreleased-lock-context` in the re-run. On the same tasks both Codex arms
coded `goal-unreachable`. The non-terminating tasks accept
`goal-unreachable`, `looping-tool-call`, and `verification-unsatisfiable`;
the last two are runtime-detected, so `goal-unreachable` is the only code a
model can report and have accepted.

Reproduction: compare `BLOCK_DESCRIPTION` in `crates/contract/src/harness_text.rs`
with `PHASES` in `run.py` and `DEFAULT_SCHEMA` in `arms/codex_arm.py`.

Repair, for a later campaign and not for any run that has ended: the
equivalent prompt carries the three definitions in foe's words, and a task
whose obstacle is a permission the sandbox denies accepts
`missing-capability`. Neither is applied to the first campaign or to
campaign two, whose predictions were committed against the code sets as
they stand.

Bearing on the result: the two foe-configured attempts scored `wrong-stop`
in the first campaign are this disagreement and the hanging-verifier
defect. Under a reading that accepts a `blocked` status with a true reason
on the non-terminating tasks, a reading chosen after the run and reported
as such, foe-configured is actionable on fourteen of fifteen in the first
campaign, and the enforcement-against-instruction comparison stays a null
with one discordant pair.

## An episode ending on its token ceiling reported nothing

The loop warned the model before its last request only when one model call
remained. An episode ending on its input-token ceiling received no warning
and ended `exhausted` with the runtime's one line, "the input_tokens budget
was exhausted", and nothing the model had learned.

Evidence: `duplicate-grant-roots` under `foe-configured` in the
budget-bounded case of campaign two, a 400,000-token ceiling below what
every arm spent on the task in the first campaign. The episode ended after
19 requests and 418,009 input tokens, with the work under way and no report
of what was done or what remained. The pre-registered prediction for the
case named a report; the runtime could not produce one.

Reproduction: any episode whose `budget.input_tokens` is below what its
work needs, from a build before `63cdb65f`.

Repair: the final-request warning also precedes a request when the input
allowance left is under twice the last request's input, so the model is
told to report before the ceiling ends the episode. Carried in `63cdb65f`;
`docs/config.md` and `docs/log-format.md` state the rule.

Bearing on the result: the budget-bounded case measures what each harness
leaves behind when the budget ends the work. The foe attempts recorded
under the earlier build measure this defect; the case is re-run for
foe-configured under `runs/budget-bounded-warning.json` from the repaired
build, and both sets of records are reported.

## A workflow that ends on its ceiling carries none of what its nodes produced

With the token-ceiling warning in place, the implementing node of
`duplicate-grant-roots` under a 400,000-token ceiling received the warning,
finished, and returned its change report: the paths it changed and what it
established. The assessing node then started on the remainder, spent it
in its first requests before its own warning could fire, and the workflow
ended `exhausted`. The episode's outcome is the limit's name alone, and the
record's evidence is the runtime's one line; the change report sits in the
child's `workflow/node-end` event, where an operator reading the log finds
it and the caller reading the outcome does not.

Evidence: `budget-bounded-warning/records/duplicate-grant-roots/foe-configured/01.json`
against `attempts/.../log/ep_18ed3ce8/children/ep_2b6eaa2a/episode.jsonl`.

Two causes. A fresh child judges the warning against its own last request,
and has none before its first, so a child spawned onto a small remainder
gets no warning. And an `exhausted` outcome carries only the limit, so a
workflow that produced values before the ceiling reports none of them.

Repair, proposed and not carried: an `exhausted` or `failed` workflow
outcome carries the rendered value of every node that completed, under a
`produced` member, and a child spawned onto a remainder smaller than twice
its parent's last request receives the warning on its first request. The
first is a change to the outcome's shape in `docs/log-format.md` and
`docs/protocol.md`, which a version step should carry, so it is left to a
change that can take that step.

Bearing on the result: the budget-bounded prediction for foe stays
falsified at the level of the outcome. The repair in `63cdb65f` moved the
report into the log, not into what the caller receives.
