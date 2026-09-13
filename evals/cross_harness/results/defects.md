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
