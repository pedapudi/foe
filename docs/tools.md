# Tools

A tool is a function the model may call during an episode. It has a
specification, which is what the model sees and what identity hashes, and an
implementation, which runs when the model calls it. [design.md](design.md)
defines the specification (`ToolSpec`), the declared effect, and how the
registry checks effects against grants. This document specifies where tools
come from, the contract for tools that are executables, and the four
built-in coding tools.

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
value otherwise.

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

The executable's content is hashed into the program's identity. Registry
construction opens the file and verifies its content. Every call executes a
duplicate of that file descriptor. Replacing the configured path after
construction cannot redirect a call to a different file. A script sees the
descriptor path as `$0`; it should locate sibling files from its configured
working directory.

## Built-in coding tools

The four coding tools live in the `foe-code` crate, which exposes two
functions. `foe_code::all()` returns every coding tool; `foe_code::readonly()`
returns only `read` and `grep`. The `bash` tool is compiled only when the
crate's `exec` feature is enabled, which it is by default. A build without
that feature contains no code path that starts a process.

Each tool reaches files and processes only through the capability handles
the runtime passes at dispatch: a reader bounded to opened `read` directory
descriptors, a writer bounded to opened `write` directory descriptors, and
an executor. A relative path in an argument is taken from the first `read`
root, and paths in results are shown relative to it.

| tool | effect | arguments | limits | canonical value |
|---|---|---|---|---|
| `read` | reads | `path`; `offset`, the first line to show, 1-indexed, default 1; `limit`, the maximum lines to show | 2,000 lines or 50 KiB per call, whichever comes first; binary files are refused | `path`, `offset`, `total_lines`, `shown`, `truncated`, `content` |
| `grep` | reads | `pattern`; `path`, a directory or file, default the first read root; `glob`; `ignore_case`; `literal`; `context`, lines before and after each match; `limit`, matches to render, default 100 | 500 characters per line; the search stops after 10,000 matches; `.gitignore` and `.ignore` files apply | `pattern`, `root`, `matches`, `files`, `searched_files`, `complete`, `hits`, each with `path`, `line`, `text`, `context` |
| `edit` | writes | `path`; `edits`, a list of `{old_text, new_text}` | each `old_text` occurs exactly once; matches do not overlap; the result differs from the original | `path`, `edits`, `added`, `removed`, `diff` |
| `bash` | execs | `command`; `timeout_seconds`, default 120 | the last 2,000 lines or 50 KiB of output are shown; the rest is spilled | `command`, `exit_code`, `timed_out`, `duration_ms`, `stdout`, `stderr`, `truncated`, `spill` |

The limits in the table are constants in the crate, and every tool
description sent to the model is formatted from the same constants.

### Shared truncation

`read` and `bash` cut long output with one pair of rules. A cut keeps whole
lines: a line is shown entirely or omitted, so a cut never splits a UTF-8
sequence. The byte measure counts each line plus one byte for its newline.
`read` keeps the head of its window; `bash` keeps the tail of its output,
because the end of a build or test run usually carries the verdict.

### `read`

The rendered form numbers each line as `N<TAB>text`, with `N` counted from
the start of the file. A carriage return before a newline is dropped from
the rendering. When lines remain after the window, the rendering ends with
a notice that names the next offset, for example
`[Showing lines 1-2000 of 8431. Use offset=2001 to continue.]`. `limit`
caps the window below the line limit; `truncated` is true whenever lines
remain beyond the window, whatever the cause.

A single line longer than the byte limit cannot be shown whole. The tool
then returns no content and a notice naming the line's size and a `bash`
command that shows a slice of it with `sed` and `head -c`.

A file that contains a NUL byte or is not valid UTF-8 is reported as binary
with its size in bytes, and the call is an error. An offset past the end of
the file is an error that states the file's line count.

### `grep`

Searching runs in process through the `grep-searcher`, `grep-regex`,
`grep-matcher`, and `ignore` libraries; no process is started. The reader
walks the tree relative to an opened read-root descriptor. Rules from
`.gitignore` and `.ignore` files apply whether or not the directory is a git
checkout, and hidden entries are skipped. Symbolic links are skipped. Files
that contain a NUL byte are skipped.

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

Standard output and standard error are rendered in that order, separated by
a `--- stderr ---` line when standard error is non-empty. The combined text
is cut to its last 2,000 lines or 50 KiB. When a cut happens, the full text
is written to a file named `CALL_ID-bash.txt` under the episode's `spill/`
directory, and the first line of the rendering names that file. The last
line states the exit code and duration, or the timeout.

A non-zero exit is a result. The call is an error only when the arguments
are invalid, the executor refuses the request, or the tool was dispatched
without the handles it needs.
