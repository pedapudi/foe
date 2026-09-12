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
work. On the solvable tasks that is 651,000 uncached input tokens against
58,000.

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

Bearing on the result: one holdout attempt is affected, recorded rather than
re-run. The affected cell is named in the results document.
