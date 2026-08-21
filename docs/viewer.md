# Viewer

The viewer renders an episode directory: the log of one episode and the
logs of every descendant under `children/`. It has two halves. A browser
bundle, built from `view/` into `view/dist/viewer.js` and
`view/dist/viewer.css`, renders the page. The `foe-view` crate embeds that
bundle into the binary at build time and supplies it with events in one of
two modes: live, over a loopback HTTP server, and static, as one
self-contained HTML file. Both modes load the same page, so the live page is
a replay that has not finished.

## What the viewer shows

Below the top bar the page has four regions.

- The **episodes** region, at the top of the left column, lists episodes as
  a tree by lineage.
- The **details** region, below it, describes the selected episode.
- The **trajectory** region, at the top of the right column, draws when
  each episode ran and what it did.
- The **main** region, below the trajectory, holds one tab at a time for
  the selected episode: **conversation**, **raw events**, **diff**,
  **workflow**, and **statistics**. The digits `1` to `5` select them in
  that order. The workflow tab is present only for an episode whose
  program declares a graph; the other four are always present.

Every divider between two regions is a grip that resizes them, by drag, by
the arrow keys, or to a limit with Home and End; a double click returns a
grip to its default. `docs/design-language.md` specifies the grip.
The sizes persist in `localStorage` under `foe.panes` as one object with
the sidebar width in pixels and the two row splits as a fraction of the
column each divides. The object names only the sizes the reader has moved,
so a size left alone stays derived across a reload. The sidebar opens at
300 pixels and the details region at 30 percent of the left column.

The trajectory opens at the height its rows need: the axis, the rows, and
the padding around them, held at or above the shortest pane and at or below
half the right column. A run of one episode therefore opens a region the
height of one episode rather than a fixed share of mostly empty ground, and
a spawn during a live run grows the region as it adds a row. Once a grip has
moved the trajectory the stored size wins and the row count no longer
changes it; a double click on a grip drops the stored size and returns the
region to what derives it. Every region declares a minimum, so none of them
collapses.

The episodes tree gives each episode a row about 40 pixels tall: a dot
coloured by outcome, the program name at the page's base size, the episode
id in mono beside it, and a second line reading the outcome word with the
code of a `blocked` outcome or the limit of an `exhausted` one. A spawned
child hangs under its parent on a solid connector and a fork under its
origin on a dashed one, both two pixels wide in `--v2-ink-faint`.

The details region states the outcome, the model calls and tokens consumed
against the budget `episode/start.program.budget` declares, the sandbox
mode and Landlock ABI, the lineage, the event count, the start time, the
duration, and the task. Its text wraps and its numbers are tabular; the
region scrolls as a whole when its content exceeds it, and nothing inside
it has a scrollbar of its own.

The conversation is derived from the log by the rule in
[log-format.md](log-format.md#derived-messages). It holds the system prompt
and tool schemas from `request/header`, each inbox item, and each assistant
turn.

An assistant turn assembled from `assistant/chunk` events is marked `live`
in the accent while its response is still arriving. A turn still assembling
when `episode/end` is read is a stream that was cut off, so it is marked
`interrupted` in `--v2-caution` instead, alongside the turns whose
`assistant/message` reports truncation. A finished log therefore never
shows a turn as live. Each tool call appears with the text the model received and the
canonical value the log stores. A compaction appears as one system row
placed at the cut, stating how many messages the summary replaced, with the
continuation message the model receives behind an expander; the rows above
it stay visible, faded and marked `compacted`, because the pane shows the
log and the log keeps them. Everything the bundle shows is computed in the
browser from the events; the server contributes only the tree, which tells
the bundle which episodes exist and in what order.

## The trajectory

The trajectory draws one row per episode, in the order the tree lists them,
indented by lineage. The row label is the program name with the episode id
in mono, and the selected row carries the figure's one accent.

The x axis is wall-clock time, taken from each event's `time`. A control in
the region's header switches it to log position, where x is the event's
`seq`. Both axes map linearly onto the same plot area, so switching moves
the marks and changes nothing else. The axis carries small mono labels on
leader ticks at round offsets from the start of the run, and no gridlines.
The figure fits the region's width and reflows when the region is resized;
it neither pans nor zooms, and it redraws only when a digest of what it
would draw changes.

A row has two lanes. The episode's own events sit on the lifetime line, and
tool calls sit in a lane a few pixels below it. The lanes exist because
duration alone does not separate the two channels: on a run bound by the
model every tool call is milliseconds against a span of tens of seconds, so
its segment is drawn at its minimum width and is the same hairline as a
request tick. Position separates them where length cannot. A request tick
rises from the lifetime line rather than crossing it, so requests read above
the line and tool calls below it.

| mark | lane | drawn from | form |
|---|---|---|---|
| lifetime | line | `episode/start` to `episode/end` | a hairline bar, continued as a dashed extension to the current time while the episode runs |
| model request | line | `model/request` | a thin tick rising from the line |
| compaction | line | `compaction/start` | a small open diamond in `--v2-caution` |
| retry | line | `request/retry` | a cross in `--v2-bad` |
| spawn | line | `spawn/start` | a small ring, and the origin of the child's connector |
| outcome | line | `episode/end` | a glyph at the end of the bar |
| tool call | tool | `tool/result` | a segment whose width is `duration_ms`, ending at the result |

A tool call slow enough to have a visible length keeps it; a shorter one is
drawn at the minimum width, so no call disappears. On the sequence axis a
tool call is a tick rather than a segment, because a duration in
milliseconds has no length in log positions, and the lane still holds.

The outcome glyph is coloured by direction: a filled dot in `--v2-good` for
`completed`, a cross in `--v2-bad` for `failed`, a triangle in
`--v2-caution` for `exhausted`, and a flat bar in `--v2-flat` for
`blocked`. A running episode ends in an open ring. Hovering the glyph names
the outcome with the code of a `blocked` outcome or the limit of an
`exhausted` one, and the message the outcome carries.

A connector runs from the parent's `spawn/start` mark to the start of the
child's bar, solid. A fork's connector runs from the origin's position at
the fork boundary to the start of the fork's bar, dashed.

Hovering a mark opens a hovercard naming the mark, its `seq`, its time, its
duration when it has one, and one line of detail: a tool call's arguments, a
retry's step and delay, or a spawned child's program. Clicking a mark
selects its episode and brings the conversation to that log position, where
the row is marked until another is.
Clicking a row label selects that episode, and `j` and `k` move between
rows.

## The workflow view

The workflow tab draws the graph an episode declares and the run that went
through it, in one figure. Both halves are drawn, because the argument of a
declared workflow is that the graph bounds what the model may do while the
model chooses freely inside it: a figure of the firings alone would show
the choices and lose the bound.

The declaration comes from `episode/start`, whose `program` is the resolved
configuration with the task removed and whose `workflow` key holds the node
declaration that [workflow.md](workflow.md) specifies. The run comes from
`workflow/node-start`, `workflow/node-end`, `workflow/branch`, and
`workflow/recovery` in the same log.

| what is drawn | where it comes from |
|---|---|
| a box per declared node, with its name and its kind | `workflow.nodes` |
| an edge per declared edge | each node's `follows` and `followed_by`, the built-in `task` source, and each `branches` label's successors |
| a label per declared branch label | each node's `branches` |
| a mark per firing, along the bottom of its node's box | `workflow/node-start` |
| a fire count on the box | the number of `workflow/node-start` events for that node |
| a glyph, an action, and a cause where a recovery happened | `workflow/recovery` |

Weight over that structure is what the run did.

- A node that never fired is drawn in neutral ink inside a dashed outline,
  so that its absence from the run is visible.
- An edge that carried a value is solid; an edge declared and never
  traversed is faint and dashed. An edge carried a value when a
  `workflow/node-start` on its target lists, among its `inputs`, the `seq`
  of an event the source produced, or when a `workflow/branch` on its
  source names its target among the successors of the chosen label.
- Every declared label of a choice point is drawn, whether or not a firing
  chose it. A label with no successor is drawn as a short stub ending in a
  small open square, because the workflow ends along that path.
- A node takes its outcome direction from its last `workflow/node-end`: an
  error is `--v2-bad`, a clean end is `--v2-good`, and a node still running
  or never fired is neutral.
- A node that fired more than once carries its fire count, because a cycle
  bounded by `max_fires` is legal.

The labels a firing chose carry the figure's one accent, because the choice
inside a bounded graph is what the figure argues. The firing mark of the
selected child episode is drawn in heavier ink rather than in the accent.

A model node's firing is a child episode. Clicking its mark selects that
episode, exactly as clicking a mark in the trajectory does.

Hovering any element opens a hovercard. A node names its kind, its firings,
and its bound. An edge says whether a value crossed it. A label says
whether a firing chose it and where it leads. A firing names its duration,
the label it chose, and its child episode or the error it ended with. A
recovery names the firing that failed, its cause, and the action taken.

### Laying out the graph

`src/workflow.ts` computes the layout as a pure function of the graph and
the pane's width and returns positions; `src/render/workflow.ts` draws
them. No force simulation is involved, so the same workflow always draws
the same way.

A depth-first walk from the nodes with no incoming edge, taking sources and
successors in name order, names every edge that closes a cycle. Rank is
then the longest path of the remaining edges that reaches a node, and the
column at each rank is ordered by name. An edge that closes a cycle is
routed under the rows and drawn from right to left. The built-in `task`
source imposes no order on the graph, so it stands alone in a column ahead
of every node.

The gap between two columns holds the branch labels of the left one and is
never narrower than the longest of them needs. A pane too narrow for the
figure shrinks the boxes to their minimum; below that the figure keeps its
own width and the pane scrolls it sideways.

## The statistics view

The statistics tab draws six figures over the selected episode, or over
that episode together with every episode under it. A control in the tab's
header switches between the two, because a child spends from the budget
pool its root holds: the tree is the scope a declared limit actually
bounds. Every quantity is derived in the browser from events the log
already carries.

Two rules govern the presentation.

- Every figure states the arithmetic behind a number a reader could not
  derive by eye. Hovering it opens a hovercard giving the quantity's
  definition and the values it was computed from, so a cache hit rate of
  4.3 percent shows the 2,560 and the 60,041 behind it.
- A quantity no event in the scope measured is shown as absent rather than
  as zero. A run with no cache-read figure has no hit rate, and drawing it
  as zero percent would assert a measurement that was not made. A share
  short of the whole never rounds to the whole either: a run that spent
  99.76 percent of its wall clock inside model requests reads 99.8 percent.

### The quantities

A step is one `model/request` and the answer it received, matched by
`request_id`. A retried attempt is a step of its own, and a compaction's
own summarization call, whose `request_id` starts with `cmp_`, is a step
marked as such.

| quantity | definition |
|---|---|
| time to first token | milliseconds from a step's `model/request` to the first `assistant/chunk` of that request whose `chunk.kind` is not `error`. A request answered only by an error produced no token, so the quantity is absent for it. |
| total latency | milliseconds from a step's `model/request` to its `assistant/message`. Absent for a request no message answered. |
| output rate | the step's `usage.output` divided by its total latency in seconds. |
| input tokens per step | the step's `usage.input`. |
| model time | the total, over every step, of the interval from its `model/request` to the last event that request produced: its `assistant/message`, or its last `assistant/chunk` when no message came. |
| tool time | the total of `duration_ms` over every `tool/result`. |
| retry backoff | the total of `delay_ms` over every `request/retry`. |
| wall clock | the scope root's `episode/start` to its `episode/end`, or to the current clock while it runs. |
| unaccounted time | the wall clock less model time, tool time, and retry backoff. |
| cache hit rate | the total of `usage.cache_read` divided by the total of `usage.input`, both over every `assistant/message` in the scope. Absent when no answer reported a cache-read figure. |
| tool calls by name | `tool/result` events grouped by `name`, each with its count, the total of its `duration_ms`, and its error count. |

Budget consumption is counted the way the runtime's own pool counts it, so
that the figure and the runtime never disagree.

| limit in `program.budget` | what is counted against it |
|---|---|
| `model_calls` | one per `model/request`, retried attempts included |
| `tokens` | `usage.input` plus `usage.output` over every `assistant/message` |
| `seconds` | the scope root's wall clock |
| `max_episodes` | the episodes in the scope, the root itself included |
| `max_depth` | the deepest lineage below the scope's root |

A limit the program does not declare has no row. `max_concurrent` and
`loop_threshold` have no row either: neither bounds a quantity the log
accumulates, so neither has a consumption to draw.

### The figures

**Where the wall clock went** is one bar divided into model time, tool
time, retry backoff, and unaccounted time, with the largest share accented
because which share dominates is what the figure argues. When the episodes
of the scope ran at the same time the intervals sum past the wall clock;
the bar then divides by that sum and says so, rather than drawing a share
above one.

**Context growth** plots input tokens against step position, one line per
episode of the scope, with the longest line accented. The declared token
limit is a dashed envelope across the plot, and the top of the plot is the
larger of that limit and the highest point. Context growth against a
declared limit is what decides whether a run completes, and the curve makes
visible what a total conceals. A compaction's own call is left out, because
its input is the summarization prompt rather than the context.

**Per step** is one row per step with its time to first token, its total
latency, its output rate, and a bar whose filled head is the wait for the
first token and whose tail is the wait for the whole answer. The slowest
answered step is accented. Clicking a row brings the conversation to that
request.

**Cache reads** is one proportion bar of cache-read tokens against total
input tokens, or the absent word when no answer reported the figure.

**Budget** is one row per declared limit with what the scope spent, the
share of the limit, and a hairline mark of that share which takes
`--v2-caution` once the limit is reached.

**Tool calls** is one row per tool name with its call count, the total
duration its results report, its error count, and a bar of that duration
against the longest.

## Rendering text

The bundle renders four kinds of rich text. Every element is built with
`document.createElement` and every string of model output is set as a text
node, so no text from a log is ever parsed as markup by the browser.

**Markdown.** An assistant message is Markdown once the message is
complete. The parser is written for this viewer and takes no dependency. It
covers ATX headings, paragraphs, bullet and ordered lists with nesting,
fenced code, block quotes, pipe tables with alignment, thematic breaks, and
the inline set of emphasis, strong emphasis, strikethrough, code spans,
links, hard line breaks, and mathematics. A link shows its target in a
tooltip and is not navigable, because the page runs on loopback or from a
file and never leaves the log. While a message is still streaming its text
is shown as it arrives, because a half-written fence or table would parse
as something the model did not mean.

**Code.** A fenced block is coloured by a hand-written tokenizer that knows
Rust, Python, TypeScript and JavaScript, JSON, shell, Go, the C family,
TOML, YAML, and Markdown; a language it does not know stays plain. The
tokenizer recognizes five roles, and concatenating its output reproduces
the input byte for byte, so colouring never changes a character. Strings
take `--v2-good` at reduced opacity, keywords `--v2-accent`, comments
`--v2-ink-faint`, numbers `--v2-caution` at reduced opacity, and everything
else `--v2-ink`. A block sets in `--v2-mono` on a `--v2-panel` ground inside
a `--v2-rule` hairline, names its language in faint mono at the top right,
and carries a copy control.

**Diffs.** The unified diff the `edit` tool returns is read into numbered
lines. An added line is tinted `--v2-good-soft` and a removed line
`--v2-bad-soft`; line numbers are faint mono, one column for the file
before the edit and one for the file after, and each line's number advances
only on the side it belongs to.

**Mathematics.** `$…$` and `\(…\)` are inline expressions and `$$…$$` and
`\[…\]` are display expressions. A display expression alone in a paragraph
becomes a block. A single dollar opens an expression only when a closing
dollar follows on the same line with no space beside either delimiter, so a
price or a shell variable stays literal. Expressions are converted to
MathML, which Chrome, Firefox, and Safari lay out natively, so the page
ships no math font and fetches nothing. An expression that is not valid TeX
is shown as its source in mono. The converter is Temml, vendored under
`view/vendor/`, and `view/README.md` states why it is the one exception to
the rule that the bundle has no dependencies.

**Tool results.** The shape of a `tool/result`'s `rendered` text is decided
from the text alone. Text carrying diff hunks renders as a diff; text that
parses as JSON renders as pretty-printed, coloured JSON; text whose lines
all begin with a number and a tab renders as numbered source, coloured by
the extension of the `path` in the result's canonical value; everything
else stays preformatted in mono.

## The page

Both modes serve one HTML document. The stylesheet and the script are
inlined, the fonts the stylesheet names are inlined as `data:` URIs, and a
`window.__FOE__` object is assigned in a script element that precedes the
bundle. The bundle reads that object once, synchronously, and mounts into
the element with id `app`.

```html
<script>window.__FOE__ = { "mode": "live", "base": "", "token": "…" }</script>
<script>window.__FOE__ = { "mode": "static", "episodes": { "<id>": [ …events ] }, "tree": {…} }</script>
```

In the JSON written into the page, every `<` is written as the escape
`<`. Without that, the characters `</script>` inside a tool result or a
task would end the script element and the rest of the log would render as
page text.

## The episode tree

The tree is the projection `foe_view::project(dir)` computes and the one
value the server knows that the bundle does not derive itself. It is one
object with a `roots` list. Each node carries the fields of `episode/start`
that identify the episode and its lineage, the outcome when the log has an
`episode/end`, and the sum of `usage` over every `assistant/message`.

```json
{ "roots": [ {
  "id": "ep_8f3a", "parent_id": null, "fork_origin": null, "team_id": null,
  "name": "fixer",
  "outcome": { "kind": "completed", "value": "…" },
  "usage": { "input": 9120, "output": 100, "cache_read": 8000 },
  "children": [ … ]
} ] }
```

`name` is `program.name` from `episode/start` and is null when the program
has no name. Children are the episodes whose directories lie under the
parent's `children/`, sorted by directory name. A directory holds one root
log, so `roots` has one element once that log exists and none before it
does.

## Live mode

`foe_view::serve(dir, port)` binds `127.0.0.1:port`, or an ephemeral port
when that one is taken, and prints one line to standard error:

```
foe viewer: http://127.0.0.1:41873/?token=3f9c…
```

That line is the only place a running episode prints the token.
`foe view DIR --serve` additionally prints the URL as the first line of its
standard output, for the process that started it. Opening the URL loads the
page in live mode. The bundle then fetches `/episodes` every two seconds
until every episode it knows about has ended, because a parent can spawn a
child at any point before its own end, and it opens one event stream per
episode.

The server reads every log on a 250 millisecond timer. Each tick discovers
new child directories and reads whatever each log appended since the last
tick, using `foe_log::fold::read_from`, which returns only complete lines.
A partial line that a writer is still appending becomes an event on a later
tick. Every open event stream is woken when any log grew and sends its
episode's new lines.

### Endpoints

| request | token accepted from | response |
|---|---|---|
| `GET /?token=T` | query | the page in live mode |
| `GET /episodes` | `X-Foe-Token` header | the episode tree as JSON |
| `GET /events?episode=ID` | header or `?token=` | server-sent events for one episode |
| `GET /fonts/NAME.woff2` | `X-Foe-Token` header | one embedded font, `font/woff2`, cacheable for a year |

Any other path answers 404. A method other than `GET` answers 405. A request
that is not HTTP or whose headers exceed 16 KB answers 400. Every response
closes its connection; the server understands neither request bodies nor
persistent connections, because the bundle needs neither.

The event stream sends each log event as one message: `id:` is the event's
`seq` and `data:` is the event's JSON on one line. A client that reconnects
with a `Last-Event-ID` header receives only events after that `seq`. A
stream stays open after `episode/end` until the client closes it; the
bundle closes it on seeing that event. A comment line `: keep-alive` is
sent every fifteen seconds, so a closed connection is noticed within that
time. An unknown episode id yields a stream that carries events once a log
with that id appears.

### The token

Each run draws 128 bits from `/dev/urandom` and presents them as 32 hex
characters. The loopback interface is reachable by every process of every
user on the machine, and a log holds the task, source text, and tool
output. The token is what makes the server readable only by whoever can
read the process's standard error.

A request presents the token in the `X-Foe-Token` header. A browser cannot
attach a header to a page navigation or to an `EventSource`, so the page and
the event stream also accept `?token=` in the URL. Every other route refuses
the query form, so that the token appears in as few URLs as possible; URLs
are written to browser history and to access logs, while headers stay out
of both. The page
is served with `Referrer-Policy: no-referrer`, so the URL carrying the token
is never sent as a referrer. A request without the token, or with a wrong
one, answers 401. The comparison takes the same time whatever the first
differing character, so response timing reveals nothing about the token.

### The Origin rule

A request that carries an `Origin` header whose value differs from the
server's own origin, `http://127.0.0.1:<port>`, answers 403 before the
token is checked. A page open in the same browser, served from any other
site, can send requests to loopback addresses. Browsers attach `Origin` to
every such cross-origin request and to none of the viewer's own same-origin
`GET` requests, so the rule costs the viewer nothing and refuses every
request a foreign page could make, with or without the token. A request
without an `Origin` header, such as one from a command-line client, passes
this check and is then subject to the token. A page served from a hostname
that resolves to `127.0.0.1` sends no `Origin` header either; the token
refuses it.

## Static mode

`foe_view::export(dir)` returns the page as one string with every log under
`dir` inlined as JSON arrays keyed by episode id, together with the tree.
The binary writes that string to a file. The file makes no network request:
script, stylesheet, fonts, and events are all inside it. Its size is the
bundle plus the fonts plus the logs, and `assistant/chunk` and
`model/request` events make the logs several times the size of the
conversation they describe; see [log-format.md](log-format.md#size). The
export fails, naming the file, when any log under `dir` is missing or
malformed.

## Embedding the bundle

`crates/view/build.rs` copies `view/dist/viewer.js`, `view/dist/viewer.css`,
and the four font files under `view/fonts/` into the crate's build output,
where `include_str!` and `include_bytes!` embed them. The bundle is built
with `pnpm install && pnpm build` in `view/`; a rebuild of `foe-view` then
picks it up. When the script or stylesheet is absent, the build script
writes a placeholder that renders a page naming that command, so the crate
compiles on a machine without Node. When a font file is absent, that font
is left out: the server answers 404 for it, the stylesheet's reference to it
is left as written, and the browser falls back to the next family in the
stylesheet's font stack.

## Public interface

| call | returns |
|---|---|
| `foe_view::project(dir)` | the episode tree for `dir` |
| `foe_view::serve(dir, port).await` | a `Server` with `addr` and `token`; `Server::wait().await` runs until the server stops |
| `foe_view::export(dir)` | the static page as a `String` |

`serve` spawns its tasks on the tokio runtime that calls it. The crate
depends on `foe-log` and tokio and on nothing in `foe-core`; a program that
only needs to read and serve logs takes on nothing of the runtime.
