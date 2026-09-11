# Direct search and episode resource measurements

The measurements recorded on 2026-09-10 support a bounded runtime worker
allocation and establish direct-scan costs for later index comparisons.
They measure no index implementation, fuzzy ranking, watcher, or cache-hit
speedup. The [observations](observations.json) retain per-query durations,
query arguments, resource samples, and mutation checks.

The generated corpus contains 10,000 files and 47,444,568 bytes on ext4.
Its seed is 1. Its manifest SHA-256 is
`661c32016ea02fba4ba9c8f1b7a78f8df633270b30209588a197a729b6834ea6`.
The manifest includes each relative path, byte count, and content digest.
Both worker configurations used that corpus. Its manifest was checked after
restoring the mutation files between configurations.

## Search cost

Five isolated repetitions were recorded for each query class. The table
shows milliseconds. Eviction requests removal of file contents from the
page cache; directory metadata remains cached.

| Query | Warm median | Warm p95 | Content-evicted median | Content-evicted p95 |
| --- | ---: | ---: | ---: | ---: |
| Absent literal | 35 | 37 | 420 | 426 |
| Rare literal | 35 | 38 | 425 | 428 |
| Three-pattern alternation | 35 | 36 | 422 | 427 |
| One-subtree search | below 1 | below 1 | 4 | 5 |

Twenty mixed queries spent 796 milliseconds in search after warm-up. With
contents evicted before the first query, they spent 1,190 milliseconds.
The first query accounted for 425 milliseconds of that second sequence.
Evicting before every isolated query therefore describes a different
workload from a session that retains file-cache reuse.

One hundred distinct absent queries still spent about 3.4 seconds scanning.
An index could reuse inventory and postings across those distinct patterns.
Exact repetition counts therefore cannot bound its possible benefit.
Construction, update, invalidation, and fallback costs remain unmeasured.

## Runtime workers

The host exposed enough processors for the default runtime to allocate
32 asynchronous workers per episode. The tested bound uses two. The table
reports observed thread peaks and total wall time, including startup and
logging. These are individual runs, so small latency differences establish
no throughput improvement.

| Workload | Host-sized workers: threads / seconds | Two workers: threads / seconds |
| --- | ---: | ---: |
| 100 mixed queries | 35 / 4.724 | 5 / 4.696 |
| Two episodes, 100 mixed queries each | 70 / 4.977 | 10 / 5.017 |
| 100 distinct absent queries | 35 / 3.615 | 5 / 3.606 |
| Two episodes, 100 distinct queries each | 70 / 3.984 | 10 / 3.906 |

With two workers, the mixed 100-query run reached approximately 65 MiB of
resident memory. The distinct absent-query run reached approximately
19 MiB. Both use direct scanning. These measurements include request
history and tool-result storage; they do not attribute memory growth to
search alone. Some descriptor samples were denied and remain null.

The mutation sequence observed match counts `0, 1, 0, 1, 0, 1` after
creation, replacement, restoration, ignoring the file, and removing that
ignore rule. Each search used the same pattern. These observations establish
fresh direct-scan results under those changes.

## Recorded workflow usage

A separate corpus contains 30 episode logs and nine `grep` calls. Those
calls account for 1,227 milliseconds, or 0.3 percent of recorded tool time.
That corpus supports a conclusion about its own workload only.

Its four workflow runs contain nine model-node firings. Implementation
consumed 71,818 input tokens, assessment consumed 68,840, and one repair
consumed 354,859. Assessment and repair together account for 85.5 percent
of model-node input, dominated by that single repair. This is insufficient
evidence for removing an assessment stage.

Reported input includes cache-read tokens. Existing prefix markers alone
establish no measured cost or latency saving. The report withholds ratios
when a model-child log is missing, and represents that child's usage as
unknown. A comparison of prefix changes still requires matched executions.

## Reproduction and remaining comparisons

Build a release binary, then run from the repository root:

```sh
cargo build --locked --release -p foe
python3 evals/episode_cost/grep_cost_curve.py --foe target/release/foe --generated-files 10000 --repeats 5 --keep /absolute/unused/scan --json /absolute/scan.json
python3 evals/episode_cost/search_reuse.py --foe target/release/foe --corpus /absolute/unused/scan/generated --keep /absolute/unused/reuse
```

The scripts retain configurations, logs, and observations. The reuse script
also writes its mutation fixture into the generated corpus. Reproduce each
configuration with a fresh corpus or restore those files and verify the
manifest before comparison.

The retained binary digests identify the measured artifacts. The worker
comparison changes only runtime allocation in the executable. A 100,000-file
corpus, randomized repeated concurrency trials, exact-result equivalence
against an index, fuzzy relevance, and index maintenance costs remain in
[the search measurement issue](https://github.com/pedapudi/foe/issues/196).
