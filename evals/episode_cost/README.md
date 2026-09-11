# Episode cost measurements

These reports measure search time, runtime resources, and workflow token
usage. The log reports read stored evidence. The search harnesses drive the
built-in `grep` tool through scripted responses. They require no endpoint
or credential. [Measured observations](measurements.md) include the corpus
identity, commands, results, and limits of the comparisons.

Two decisions rest on these numbers. Whether to maintain a search index
depends on total search time, shared traversal and content work, and index
construction and update costs. Distinct queries can reuse the same inventory
and content index. Repeated arguments measure a separate opportunity for
result caching, subject to content changes. Whether to change the
built-in coding workflow's assessment stages depends on what fraction of a
run's tokens those stages spend. Input usage includes cache-read tokens.
Cache hits can affect latency and cost while reported input stays the same.
A report of cache markers alone establishes no measured saving.

## What each report measures

`search_in_logs.py` reads a directory of episode logs. For each episode and
each run it reports the number of `grep` and `read` calls, the wall clock
they took, the share of the episode's tool time that is, and the number of
those calls that repeat an earlier one. It counts `bash` calls whose
command line names a search program separately, because a shell search
costs the same wall clock and a content index would not serve it.

It also reports two behavioural proxies for result quality. The first
counts a search followed within three model steps by a pattern that
contains it or is contained by it. The second counts a search none of whose
matched paths any later call names. Both are weak, and the output labels
them so.

`workflow_node_tokens.py` reads the same logs and attributes input, output,
and cache-read tokens to the workflow node that spent them. Each firing of
a model node is a child episode, and `workflow/node-start` names it, so the
split between implementing, assessing, and repairing is a fold over stored
events. Missing model-child logs are reported as unavailable. Their metrics
are null, observed totals are labeled, and usage ratios are withheld.
Workflow tool nodes without a child episode do not count as model firings. The report gives the ratio of assessment and repair input tokens to
implementation input tokens. It gives the rendered characters of tool
output each role put into its own context. It gives the files an assessing
or repairing node read that the implementing node had already read.

Both log reports give the size of one tool result, by tool, as the number
of calls, the median, the 95th percentile, the maximum, and the total. The
total says how much context a role assembled; the distribution says what one
call contributes, and only the second is what a change to a tool's render
bound moves. Every character a tool renders reaches the model on the step
that produced it and on every later step of the same episode, so a tool that
returns more candidates costs the episode here whatever it saved in
milliseconds.

`grep_cost_curve.py` runs the real `grep` tool inside real episodes over
two corpora and two page-cache conditions, and reads the durations back out
of the `tool/result` events those episodes wrote. The corpora are the
working tree the script is run against and a tree the script generates from
a seed. The report gives, per query class, the median and 95th-percentile
duration, the files the tool streamed, and the matches it found. It gives
throughput in megabytes per second for a call that streamed every file in
the corpus and stopped at no collection bound, which is the only case where
the bytes read are known exactly. It also gives the cumulative
time a fixed twenty-query sequence spends in search after 1, 2, 5, and 20
queries. Warm sequences begin after a full traversal. Content-evicted
sequences evict before the first query and retain subsequent reuse. Isolated
query rows evict before each query and label that condition separately.
Directory metadata remains cached in both evicted conditions.

## Running the reports

The log reports need only Python and a directory of episode logs. They open
every log for reading and write nothing under it.

```
/usr/bin/python3 evals/episode_cost/search_in_logs.py --logs ~/.foe
/usr/bin/python3 evals/episode_cost/workflow_node_tokens.py --logs ~/.foe
```

Both accept `--json PATH`, which writes the complete report, including
every per-episode and per-node row the printed summary folds together.

The cost curve needs a built foe binary.

```
cargo build --release -p foe
/usr/bin/python3 evals/episode_cost/grep_cost_curve.py --foe target/release/foe
```

`--generated-files` sets the size of the generated corpus and defaults to
10,000. `--seed` selects which corpus that count generates; the report
prints the seed and a SHA-256 over the manifest of relative paths, sizes,
and content digests, so two machines can confirm they measured the same
tree. `--repeats` sets how many times each query class runs and defaults to
five. `--repository` names the working tree to search and defaults to the
one this file belongs to. `--keep DIR` retains the configurations, the
generated corpus, and the episode logs.

`--work-dir` says where the generated corpus and the episode logs go, and
defaults to `target/`. It must name a disk-backed filesystem. The cold
condition releases each corpus file from the page cache with
`posix_fadvise`, which needs no privilege. It cannot release the pages of a
memory-backed filesystem, so a corpus on a `tmpfs` mount such as many
systems give `/tmp` would leave the cold rows repeating the warm ones. The
report prints the filesystem type of each corpus and states when a cold row
measured the warm condition.

`search_reuse.py` measures one process and two concurrent processes over the
generated corpus. It runs mixed and distinct queries, and checks that edits
and ignore-file changes affect subsequent searches. The supplied corpus must
contain neither `mutation.txt` nor `.gitignore`; the harness retains its edits.
Each output directory must be unused.

```sh
python3 evals/episode_cost/search_reuse.py --foe /absolute/path/to/foe --corpus /absolute/path/to/generated --keep /absolute/path/to/observations
```

Wall time includes startup and logging. CPU time covers child processes.
Memory, thread, and descriptor counts are sampled every five milliseconds,
so short spikes can be missed. A null count means the process refused
inspection. The host process is excluded from these resource counts.

Unit tests of the folds run without a binary and without a corpus:

```
/usr/bin/python3 evals/episode_cost/episode_cost_test.py
```

## What the cold condition does and does not establish

`POSIX_FADV_DONTNEED` releases a file's cached contents. It does not
release the directory entries and inodes the traversal walks, and it acts
only on clean pages, so the script synchronizes the filesystem before the
first eviction of a cold run. A cold measurement taken this way therefore
finds the tree's metadata cached and understates what a search costs on a
machine that has just started.

## Reading the numbers honestly

A per-call duration is not the metric that decides whether an index is
worth building. The metric is the seconds a whole episode spends in search
and the number of searches a completed task takes. Latency savings must be compared with extra tool output, follow-up searches,
and index maintenance. A faster isolated query does not establish a faster
completed task. The
cumulative sequence figures and the per-episode figures in the log report
are stated for that reason.

The log reports say what a particular set of recorded episodes did. A
corpus of a few dozen episodes establishes the method and bounds the
repeat rate for those episodes. It supports no claim about episodes that
were not recorded.
