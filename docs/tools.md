# Tools

A tool is a function the model may call during an episode. It has a
specification, which is what the model sees and what identity hashes, and an
implementation, which runs when the model calls it. [design.md](design.md)
defines the specification (`ToolSpec`), the declared effect, and how the
registry checks effects against grants. This document specifies where tools
come from, the contract for tools that are executables, the four built-in
coding tools, and the budget that bounds what one model turn's results
show.

## Where tools come from

The `tools` list in the configuration names every tool the model may call.
Each name resolves against three sources, checked in this order.

1. **Built-in tools.** Implemented in the runtime. There are eleven: the
   four coding tools `read`, `grep`, `edit`, and `bash` specified below;
   `block`, by which the model reports a blocking condition; `spawn`, which
   starts a child episode, and `wait`, which blocks until every child this
   episode started has ended; `steer`, which sends a message to a running
   child, and `notify`, which sends one to the episode that started this
   one; and `send` and `team`, which address a teammate through the lead
   and list the team's roster. [design.md](design.md) and
   [log-format.md](log-format.md) specify `block`, spawning, waiting, and
   teams. One further tool, `return`, is synthesized rather than named in
   `tools`: a `done_when.returns` schema adds it to the registry, and
   [config.md](config.md) specifies that key.
2. **Configured executables.** Entries in `tool_defs`, each naming an
   executable by absolute path. Any program with a command line becomes a
   tool without modification.
3. **Host tools.** Implemented by the process that launched the episode and
   called over the [host protocol](protocol.md). The runtime emits a
   `host/tool-call` event and records the host's answer as the result.

A name that resolves in two sources is an error at construction, and so is
a name that resolves in none. `foe tools` lists the built-in tools; `foe
tools --config FILE` lists the resolved set for a document with each tool's
source. The second form resolves every path the document names, as
`foe plan` does, so a document whose grants or `tool_defs` name a path that
does not exist is refused with the key and the path.

Every tool returns a canonical value, which is JSON, and may return a
rendered string. The log stores the canonical value in full. The model
receives the rendered string when present and a compact rendering of the
value otherwise. The rendering, and only the rendering, is bounded by the
turn budget specified below.

## Configured executables

A `tool_defs` entry declares an executable, its description, and optional
fields listed in [config.md](config.md). The model calls the tool with one
argument, `args`, a list of strings. The runtime then runs the executable
under this contract.

- **Argument vector.** The executable receives `args` as its argument
  vector, one element per string, with no shell in between. Quoting,
  globbing, and variable expansion do not happen.
- **Standard input.** Connected to `/dev/null`, except when the tool is the
  verifier named in `done_when.verify`, in which case the candidate result
  is written to standard input as JSON.
- **Working directory.** The entry's `cwd` when present, otherwise the first
  `read` root.
- **Environment.** Constructed by the runtime. Nothing is inherited from
  the process that started the episode.
- **Network.** Closed unless the entry sets `network` to `true`. Where the
  kernel supports it, the restriction is enforced by the sandbox.
- **Timeout.** The entry's `timeout_seconds`, default 120. On expiry the
  executable and every process it started are killed, and the result says
  so.
- **Exit code.** Reported in the result as data. A non-zero exit is a
  result rather than a tool error. The model decides what a failing linter or a
  failing test run means.
- **Output.** Standard output and standard error are captured separately,
  each up to 1 MiB. Output beyond that limit is written to a file under the
  episode's `spill/` directory, and the captured text ends with a line
  naming that file.

The executable's content is hashed into the program's identity, so a
replaced binary at the same path changes identity.

## Built-in coding tools

The four coding tools live in the `foe-code` crate, which exposes two
functions. `foe_code::all()` returns every coding tool; `foe_code::readonly()`
returns only `read` and `grep`. The `bash` tool is compiled only when the
crate's `exec` feature is enabled, which it is by default. A build without
that feature contains no code path that starts a process.

Each tool reaches files and processes only through the capability handles
the runtime passes at dispatch: a reader bounded to the directories the
`read` roots named, a writer bounded to the directories the `write` roots
named, and an executor. Each handle holds those directories open, so an
operation resolves under the directory the runtime opened rather than under
the pathname as it now stands. A relative path in an argument is taken from
the first `read` root, and paths in results are shown relative to it.

| tool | effect | arguments | limits | canonical value |
|---|---|---|---|---|
| `read` | reads | `path`; `offset`, the first line to show, 1-indexed, default 1; `limit`, the maximum lines to show | 2,000 lines or 51,200 characters per call, whichever comes first; binary files are refused | `path`, `offset`, `total_lines`, `shown`, `truncated`, `content` |
| `grep` | reads | `pattern`; `path`, a directory or file, default the first read root; `glob`; `ignore_case`; `literal`; `context`, lines before and after each match; `limit`, matches to render, default 100 | 500 characters per line; the search stops after 10,000 matches; `.gitignore` and `.ignore` files apply | `pattern`, `root`, `matches`, `files`, `searched_files`, `complete`, `hits`, each with `path`, `line`, `text`, `context` |
| `edit` | writes | `path`; `edits`, a list of `{old_text, new_text}` | each `old_text` occurs exactly once; matches do not overlap; the result differs from the original | `path`, `edits`, `added`, `removed`, `diff` |
| `bash` | execs | `command`; `timeout_seconds`, default 120 | the last 2,000 lines or 51,200 characters of output are collected; the rest is spilled | `command`, `exit_code`, `timed_out`, `duration_ms`, `stdout`, `stderr`, `truncated`, `spill` |

The limits in the table are constants in the crate, and every tool
description sent to the model is formatted from the same constants.

### What a tool says it acted on

Each tool writes one line after it has run, naming what the call acted on
and what came of it, which the log records as `tool/result.subject`. It is
for a person reading a list of calls; `rendered` is the separate thing the
model received.

| tool | subject on success | subject on failure |
|---|---|---|
| `read` | `src/parser.rs lines 1–6 of 42`, the span actually shown | the error, which names the file and what went wrong |
| `grep` | `3 match(es) in 2 file(s) under src`, what the search found | the error, which names the pattern or the root |
| `edit` | `src/parser.rs: 2 edit(s), +2 -2 lines`, the same line the rendering leads with | the error, which names the file and which edit failed |
| `bash` | `cargo test -p parser · exit 0 in 1.50s`, the command and how it ended | the error, which names why the process could not start |

A tool reports what the call did rather than what it was asked for, because
the arguments are already in the log: `grep` states how many matches it
found rather than echoing its pattern. On failure the line is the error
message itself, which is where the field earns most, since only the tool
knows the outcome.

The model is never asked for this line and never sees it. It appears in no
tool's parameters, description or instruction, so it reaches neither
`ToolSpec::schema()` nor the system prompt. A model asked to supply it
would be a weaker model dropping it or filling it with noise, and correct
tool use would come to depend on prose that has nothing to do with the
call. A configured executable and a host tool state no subject, and the
field is then absent.

### Shared line fitting

Every bound on how much text a result carries is measured by one function,
`foe_core::fitting`. It answers how many lines from a sequence, taken in the
order given, fit within a line count and a character count. A line is taken
whole or not at all, so a cut on this boundary never splits a character, and
each line counts the newline that follows it. `read` fits the head of its
window; `bash` fits the tail of its output, because the end of a build or
test run usually carries the verdict. The turn budget below fits whichever
end of a rendering carries its information.

### `read`

`offset` and `limit` select a range of lines. The rendered form numbers each
line as `N<TAB>text`, with `N` counted from the start of the file. That
shape is also what tells the turn budget it is looking at a window of a
file. A carriage return before a newline is dropped from the rendering. When lines remain after the window, the rendering ends with
a notice that names the next offset, for example
`[Showing lines 1-2000 of 8431. Use offset=2001 to continue.]`. `limit`
caps the window below the line limit; `truncated` is true whenever lines
remain beyond the window, whatever the cause.

A single line longer than the character limit cannot be shown whole. The
tool then returns no content and a notice naming the line's size and a
`bash` command that shows a slice of it with `sed` and `head -c`.

A file that contains a NUL byte or is not valid UTF-8 is reported as binary
with its size in bytes, and the call is an error. An offset past the end of
the file is an error that states the file's line count.

### `grep`

Searching runs in process through the `grep-searcher`, `grep-regex`,
`grep-matcher`, and `ignore` libraries; no process is started. The tree
below `path` is walked with the rules of `.gitignore` and `.ignore` files
applied, whether or not the directory is a git checkout, and hidden entries
are skipped. Each file is streamed through the reader, so search memory does
not grow with file size. A symbolic link that leaves the read roots is
skipped rather than followed. Files that contain a NUL byte are skipped.

`pattern` uses Rust regex syntax. With `literal` true, the pattern is
matched as a fixed string. `glob` restricts the search to files whose path
matches a gitignore-style glob such as `*.rs` or `src/**/*.ts`.

Hits are sorted by path and then by line number, so the same search over the
same files yields the same result regardless of directory order. A line
longer than 500 characters is cut at 500 and followed by a marker that
states how many characters were removed.

The canonical value holds every hit collected. The rendered form states the
match count and the file count, then lists hits up to `limit`. A matching
line renders as `path:line:text`; a context line renders as
`path:line-text`. When more matches exist than `limit`, the first line says
so and suggests refining the pattern.

### `edit`

Every `old_text` is located in the file as it was before the call. The
edits are therefore independent of each other's results, and their order in
the list does not matter. Each `old_text` must occur exactly once; the
located spans must not overlap; and the result must differ from the
original. When any check fails, nothing is written, and the error names the
failing edit by its index in the list and, for a match failure, the number
of occurrences found.

Matching is exact. Whitespace, indentation, and letter case must match the
file. There is no fuzzy matching, and none is planned, because an edit that
lands somewhere other than where the model pointed corrupts a file silently.

Two encodings are handled so that the model matches the text it saw through
`read`. A UTF-8 byte order mark at the start of the file is removed before
matching and restored on write. When every line ending in the file is CRLF,
the file and every `old_text` and `new_text` are normalized to LF for
matching, and the result is written with CRLF. A file with mixed line
endings is matched as it is. A file that is not valid UTF-8 is refused.

The result is written through the writer, which replaces the file
atomically. The canonical value carries a unified diff with three lines of
context; the rendered form is a one-line summary followed by that diff.

### `bash`

The tool runs `/bin/bash -c COMMAND` through the executor with the first
read root as the working directory and a fixed environment: `PATH` is
`/usr/local/bin:/usr/bin:/bin`, `HOME` is the working directory, and `LANG`
is `C.UTF-8`. Standard input is `/dev/null`, and the network is closed.
`timeout_seconds` defaults to 120 and is reduced to the episode's remaining
wall-clock budget when that is smaller.

The executor owns the process. On timeout it kills the whole process group,
so a command that backgrounded a child does not leave it running. The result
then has `timed_out` true and `exit_code` null.

The rendering opens with the exit status: the exit code and the duration,
or a statement that the command timed out or was killed by a signal. The
status leads so that a later cut of the middle of the rendering cannot
remove it. Standard output and standard error follow in that order,
separated by a `--- stderr ---` line when standard error is non-empty. The
combined text is cut to its last 2,000 lines or 51,200 characters. When a
cut happens, the full text is written to a file named `CALL_ID-bash.txt`
under the episode's `spill/` directory, and a line after the status names
that file.

A non-zero exit is a result. The call is an error only when the arguments
are invalid, the executor refuses the request, or the tool was dispatched
without the handles it needs.

## The turn budget

A tool result is re-sent to the model in every request after the step that
produced it, so a result of `n` characters in a step followed by `k` further
requests costs `n` times `k` characters of input. `read` and `bash` each
bound one call, so what is left unbounded is the turn: ten parallel reads
each within their own limit still arrive together. The renderings of one
model turn therefore share one budget.

The budget is 50,000 characters per turn. Every call of the turn may show an
equal part of it. A call whose rendering is shorter than its part leaves the
remainder to the others, and the remainder is divided again until no part
goes unused, so a turn of one large result and five small ones gives the
large one almost the whole budget. No result is held below a floor of 4,000
characters, so a turn of many calls may cost more than the turn budget, and
never more than 4,000 characters times the number of calls. These three
figures are constants in `crates/core/src/result_budget.rs`.

The budget is a bound rather than a saving. One call is already held below
50,000 characters by the limits of `read` and `bash`, so a turn of one large
call passes almost whole and ordinary work is untouched; what the budget
catches is a turn whose calls together would fill the context. A lower
budget would shorten ordinary results, and every result the model asks about
again costs a whole turn, which resends the entire transcript. A cut that
saves characters and costs a turn is a loss, so the budget is set where it
rarely binds.

The bound is the same for every turn. What a result costs is its size times
the number of requests that still follow, and that number is unknown when
the result is produced, so an early result cannot be bounded more tightly
than a late one on the evidence available. Tightening it afterwards would
rewrite an earlier turn, which the append-only rule forbids.

### What a cut keeps

A rendering that exceeds its part ends with a notice stating what was
removed and naming the call that shows it. No tool exists for retrieving the
removed part. The tool that produced the rendering can be called again, and
the notice says how, so recovery uses a tool the model already has rather
than one it must learn.

Which end is kept depends on the shape of the rendering, because the two
shapes carry their information in different places.

- A **numbered window** is a rendering whose first line begins with a
  decimal number and a tab, which is how `read` numbers a file. It is cut to
  its head alone, and the notice names the file line to resume at, taken
  from the number on the last line shown. A reader who wants more of a file
  wants the lines after the ones already shown, so a kept tail would spend
  the budget on lines nobody asked for and leave a hole in the middle. The
  notice reads:

  ```
  [Cut to fit this turn's result budget: 596 more lines, 54830 characters
  in all. Read the same path again with offset=338 to continue from here.]
  ```

- **Any other rendering** keeps its head and its tail, two thirds of the room
  going to the head. A command's output carries its verdict at both ends:
  `bash` states its exit status on the first line for this reason, and the
  end of a build or test run usually carries what failed. The notice reads:

  ```
  [Cut to fit this turn's result budget: 1092 of 1598 lines omitted here,
  51369 characters in all. Issue the call again, narrowed, for the part you
  need.]
  ```

Two properties hold. The canonical value is untouched, so the log, the
viewer, and any later analysis still see the whole result; the budget bounds
what the model reads and nothing else. The cut is applied before the result
is appended, so the request that first carries the result and every request
after it carry the same text. No earlier turn is ever rewritten, which is
what lets a provider reuse the key-value cache of the prefix.

The per-call limits of `read` and `bash` remain as bounds on what a tool
collects into its canonical value and into the log. Parallel calls no longer
multiply: six calls in one turn divide one budget rather than taking six
limits.
