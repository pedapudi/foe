# Episode cost measurements

Three reports that say where an episode's time and tokens go. Two read
stored logs and run nothing. One drives the built-in `grep` tool over
controlled inputs through a host-supplied model backend, so it makes no
provider request and needs no credential.

Two decisions rest on these numbers. Whether to maintain a search index
depends on how often an episode repeats a search another search has already
answered, and on how much time those searches take. Whether to change the
built-in coding workflow's assessment stages depends on what fraction of a
run's tokens those stages spend.

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
events. The report gives the ratio of assessment and repair input tokens to
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
queries. That cumulative figure is the denominator an index has to beat: an
index pays for itself only when it removes more episode time than it adds.

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
and the number of searches a completed task takes. An index that saves 200
milliseconds per call and adds one tool result to the model's context, or
returns worse candidates and causes one more search, is a loss. The
cumulative sequence figures and the per-episode figures in the log report
are stated for that reason.

The log reports say what a particular set of recorded episodes did. A
corpus of a few dozen episodes establishes the method and bounds the
repeat rate for those episodes. It supports no claim about episodes that
were not recorded.
