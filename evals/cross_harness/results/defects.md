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

Bearing on the result: hypothesis H2 predicted foe would spend more input
than the other harness because its graph gives each node a fresh episode that
re-reads the tree. The prediction holds, but this defect and not the graph is
the larger part of the gap, and the reported figure is a measurement of the
defect as much as of the architecture. Re-measure after the repair.

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

Reproduction: grant write on a directory, populate it with a cargo target
directory, and run a build through the `bash` tool.
