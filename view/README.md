# Viewer bundle

This directory holds the browser application that renders foe episode logs.
It is written in TypeScript without a framework and compiled by esbuild into
two files, `dist/viewer.js` and `dist/viewer.css`. The two files together
stay under 150 KB after gzip compression; the build fails when they exceed
that budget. The only input the application reads is the log format defined
in [`docs/log-format.md`](../docs/log-format.md).

The compiled files are checked in. Cargo and Bazel embed them directly, so a
person building or installing the foe binary needs no Node.js package manager.
Node.js and pnpm are development requirements only when TypeScript sources or
viewer tests change.

The Rust crate `crates/view` embeds the two files into an HTML page. It does
so in two modes, described below, and both modes run the same code.

## The one dependency

`vendor/temml.min.js` is Temml, an MIT-licensed converter from TeX to
MathML, and it is the only third-party code in the bundle.

Assistant messages carry mathematics, and rendering it needs either a
converter or a picture. Every other route costs more than this one. A
JavaScript layout engine such as KaTeX ships its own fonts, which the static
export would have to inline. Rendering to an image loses the text. Leaving
the TeX as source makes an expression unreadable. Temml converts TeX to
MathML, which Chrome, Firefox, and Safari lay out natively with the fonts
the machine already has, so the page ships no math font and fetches nothing.

The vendored file is the published `dist/temml.min.js` with one line
appended that exports the module object, so that esbuild bundles it;
`vendor/README.md` records the change and `vendor/temml-LICENSE.txt` carries
the license. `src/render/markup.ts` calls the converter only for text that
carries a mathematics delimiter, and an expression Temml rejects is shown as
its source in mono. Temml is 49 KB of the bundle's gzipped size.

## Building and testing

Requirements: Node 22 and pnpm 10. The `packageManager` field in
`package.json` pins the pnpm version.

```
cd view
pnpm install --frozen-lockfile
pnpm build          # writes dist/viewer.js and dist/viewer.css, prints gzipped sizes
pnpm test           # bundles test/*.test.ts into .test-build/ and runs node --test
pnpm typecheck      # tsc --noEmit
pnpm fixtures       # regenerates fixtures/*.jsonl from fixtures/generate.mjs
```

`pnpm build` also fails when `viewer.js` contains the text `</script` or
`viewer.css` contains `</style`, because the host page inlines each file
into the matching element.

The tests cover the derived-messages rule, the episode fold, the lineage
helpers and the per-row measure, the trajectory layout, the workflow graph
and its layout, the statistics, the pane sizes, the appearance catalogue,
the system prompt reader, the Markdown parser, the syntax tokenizer, and the
unified diff reader. Each of those modules is pure and reads no document, so
the tests run under `node --test` with no browser.

The fixtures are twelve episode logs.

| fixture | what it exercises |
|---|---|
| `root.jsonl` | a root episode that spawns a team member and survives an interrupted request |
| `child.jsonl` | the spawned child, which receives a peer message |
| `fork.jsonl` | a fork of the root seeded at seq 12 that ends blocked |
| `compact.jsonl` | an episode compacted before its fourth request |
| `overlap-parent.jsonl` | a parent that spawns two children and keeps working while they run |
| `overlap-child.jsonl` | the surveyor, which starts after the parent and ends before it, with a compaction and a retry |
| `rich.jsonl` | the writer, whose one assistant turn holds a table, a fenced Rust block, and inline and display mathematics, with an `edit` result carrying a unified diff and a `read` result carrying numbered source |
| `retries-exhausted.jsonl` | a run against a live model whose five attempts each failed in transport, ending `blocked: recovery-exhausted` with five streams cut off before any response was assembled |
| `workflow.jsonl` | an episode running a declared graph: a node that fires twice, a choice point whose labels include one the model never chose and one with no successors, a node no firing reached, and a tool failure that a recovery decision retried |
| `workflow-propose-1.jsonl`, `workflow-propose-2.jsonl` | the two firings of the graph's `propose` node, each a child episode that returns a plan and a branch label |
| `workflow-apply-1.jsonl` | the firing of the graph's `apply` node, a child episode that reads a file and finishes with a sentence |

Seven of the twelve are literal in `generate.mjs`. The four workflow logs
are written by the runtime. `workflowRun` assembles a configuration, a
scripted model program, and a scripted verification program in a temporary
directory, runs `target/release/foe` over them, and copies the logs here
with that directory's path replaced by `/home/user/project` and
`/home/user/tools`. Everything else in those four files is the bytes the
runtime wrote, episode ids and timestamps included. Those change whenever
the fixtures are regenerated, so no test reads either from a literal.
Regenerating without the release binary present leaves the four files as
they are and says so, so the seven literal ones regenerate on a machine
with no Rust toolchain.

`retries-exhausted.jsonl` is a recorded log rather than a generated one, so
the tests read the bytes a provider failure wrote. Its last `request/retry`
has no `model/request` after it, which is a shape the runtime does not
write: a retry now sits immediately before the attempt it announces, as
[log-format.md](../docs/log-format.md#open-obligations) requires. The
viewer renders the file all the same, because a reader accepts every log it
is handed. `pnpm fixtures` leaves it alone.

Three screenshots of the static export at 1512 by 792 sit beside the source,
each in one of the two default themes. `proof-light.png` and
`proof-dark.png` show the `overlap-parent` fixture and its two children in
`google-light` and `google-dark`: the tree with its per-row measure and the
spine on the selected row, the rail that carries depth in the trajectory's
label column, the trajectory's request spans, a retry with the backoff it
imposed, and a turn holding a diff and a fenced block. `proof-one-episode.png`
shows a recorded run of this repository in `google-light`, with one root
episode and no children, which is the ordinary case and the one where the
trajectory's height is derived from a single row; its four request spans
differ in length by a factor of seven, its thirteen tool calls stand in
three fans of two, six, and five, and its system prompt is open.

The `messages` list recorded in each `model/request` event is written by
hand in the generator, so the tests compare it with the list the bundle
derives.

## The page contract

The host page sets `window.__FOE__` before the bundle runs and provides an
element with id `app`. When that element is absent the bundle mounts into
`document.body`.

```html
<div id="app"></div>
<script>window.__FOE__ = { mode: "static", episodes: { "<id>": [ ...events ] }, tree: { roots: [] } };</script>
<script>/* viewer.js */</script>
```

```html
<script>window.__FOE__ = { mode: "live", base: "", token: "..." };</script>
```

`episodes` maps an episode id to the list of its log events, each an object
with `seq`, `time`, `type`, and `data`. `tree` is optional in static mode and
gives the display order; it has the shape returned by `GET /episodes`. When
it is absent, episodes appear in the order of the `episodes` object.
Lineage itself is always derived from each log's `episode/start` event.

### Static mode

All events arrive at once. `assistant/chunk` events contribute nothing and
the conversation shows each `assistant/message` directly.

### Live mode

The bundle talks to the server that serves the page, at `base` (an empty
string means the page's own origin). It sends `token` as the `X-Foe-Token`
request header on every `fetch`. The browser's `EventSource` cannot set
headers, so on the server-sent-events endpoint alone the token travels as
the `token` query parameter. The server must accept `?token=` there and
nowhere else.

`assistant/chunk` events build the assistant row token by token until the
matching `assistant/message` replaces it.

## Endpoints the server must provide

| method and path | authentication | response |
|---|---|---|
| `GET /episodes` | `X-Foe-Token` header | `{"roots":[Node,...]}` where Node is `{"id":string,"children":[Node,...]}`; other fields are ignored |
| `GET /events?episode=<id>` | `X-Foe-Token` header or `?token=` | a `text/event-stream` of the episode's log |
| `GET /fonts/<name>.woff2` | `X-Foe-Token` header | one of the six font files under `fonts/`; see Design language |

The event stream sends one log line per message, in the form
`id: <seq>` followed by `data: <one event as JSON>`. The `id` field lets the
browser resume from `Last-Event-ID` after a reconnect; the bundle also
ignores any event whose `seq` is at or below the last one it holds for that
episode, so a replay from the start is safe. The server may send comment
lines (`: keep-alive`) at any interval; they change nothing on the page. The
stream stays open after `episode/end` until the client closes it, which the
bundle does as soon as it sees that event.

The bundle requests `/episodes` when the page loads and again every two
seconds until every known episode has ended, because a parent can spawn a
child at any time before its own end. It opens one event stream per episode
id it learns about.

## What the page shows

The top bar carries an up control that moves to the parent or fork origin of
the selected episode, the brand lockup and a `viewer` tag, the
research-preview tag, breadcrumbs from the root to the selected episode, the
colour theme picker, the typeface picker, the page scale control, and a
status pill. The status pill shows the connection state and, in live mode,
the number of episodes without an outcome.

Below the top bar are three regions, described in
[`docs/viewer.md`](../docs/viewer.md): the episode tree over a details
panel in the left column, and the trajectory over the tabs in the right
column. Every divider is a grip that resizes the regions it separates, by
drag, by the arrow keys in 16-pixel steps, or to a limit with Home and End;
a double click returns it to whatever derives it. The trajectory's height
is derived from the pixels its rows take until a grip sets it. `foe.panes`
stores only the sizes a grip has set.

The **episodes** region draws the tree as a line-art figure. A spawned
child hangs under its `parent_id` with a solid edge; a fork hangs under its
`fork_origin` with a dashed edge. Each row shows a dot coloured by outcome,
the program name, the episode id, and a second line reading the outcome
word with a `blocked` code or an `exhausted` limit.

The **details** region shows the selected episode's model calls and tokens
consumed against the budget declared in `episode/start.program.budget`, the
Landlock ABI, the fork origin, parent, team, timing, and task. Its text
wraps and the region scrolls as a whole.

The **trajectory** region draws one row per episode, stacking the channels
the episode's work nests into: model requests above the lifetime bar, the
bar itself with markers for compactions, retries, spawns, and the outcome,
then the lanes of a declared graph the episode ran, then a lane holding a
segment per tool call sized by `duration_ms`. Position is what tells a tool
call from a model request on a run where every call is too short to draw a
length, and calls issued at one instant take successive heights of the tool
lane so that a batch is countable. A header control switches the x axis
between wall-clock time and log position. Hovering a mark opens the
hovercard; clicking one selects that episode and brings the conversation to
that log position. `src/trajectory.ts` holds the placement rules and
`src/render/trajectory.ts` draws them.

The main region has five tabs.

- **conversation**: one row per event that contributes to the dialogue.
  The latest `request/header` appears as a collapsed system prompt row, with
  each tool schema behind its own expander. An `inbox/item` is a user row
  with a badge for its `source`. An `assistant/message` renders its text as
  Markdown and lists its tool calls. A turn is marked `live` while its
  response is still arriving and `interrupted` once the episode has ended,
  whether the response reported truncation or the stream was cut off
  before a response was assembled. A
  `tool/result` renders `rendered` by its shape, as a diff, as JSON, as
  numbered source, or as preformatted text, and keeps the canonical `value`
  behind an expander; `is_error`, `synthetic`, and `spill` show markers.
  A `compaction/summary` is a system row inserted at its `first_kept_seq`,
  ahead of the rows the model still sees, stating how many dialogue rows
  the summary replaced and holding the continuation message behind an
  expander; the rows above it are faded and marked `compacted` by the
  stylesheet alone. Every other event type, including the reserved ones
  and types this bundle does not know, appears as a compact row with the
  event type as its label and the payload behind an expander.
- **raw events**: a table of every event with seq, time, and type. The
  filter matches the seq, the type, or any text in the payload. Clicking a
  row expands its payload.
- **diff**: for two episodes that share a fork prefix. The shared events are
  shown once, collapsed, under the label `shared, seq 0-N`; the two
  suffixes follow side by side. Two episodes share a prefix when one is a
  fork of the other or both descend by forking from a common origin; the
  shared length is the smallest seed boundary on the path between them.
- **workflow**: the graph the episode declares, drawn with the run over it.
  Every declared node, edge, and branch label is drawn whether or not the
  run reached it; what fired is solid, accented, and coloured by outcome
  direction. Present only for an episode whose program declares a graph.
  `src/workflow.ts` reads the graph and places it and
  `src/render/workflow.ts` draws it.
- **statistics**: six figures over the selected episode, or over that
  episode and its descendants. Every number a reader could not derive by
  eye carries a hovercard with its definition and the values behind it, and
  a quantity no event measured reads as absent rather than as zero.
  `src/statistics.ts` derives the quantities and places the figures and
  `src/render/statistics.ts` draws them.

New events patch only the rows they create or change. Scroll position
follows the end while the reader is at the end and stays put otherwise.
Expanders keep their open state across updates. The tree, the details
panel, and the trajectory redraw only when a digest of their content
changes.

Keyboard: `j` and `k` move the cursor through the episode rows, `Enter`
selects the cursor, `c` marks the cursor for comparison, `/` opens the raw
events tab and focuses the filter, and `1` to `5` switch tabs in the order
the tab strip lists them. A focused grip answers the arrow keys, Home, and
End.

## Rendering Markdown, code, diffs, and mathematics

`src/render/markdown.ts` parses Markdown into a tree and
`src/render/markup.ts` builds elements from it, so no string of model output
reaches the browser as markup. `src/render/highlight.ts` colours code with a
hand-written tokenizer whose output concatenates back to its input.
`src/render/unified-diff.ts` reads the diff the `edit` tool returns into
numbered lines. `src/render/shape.ts` decides what kind of text a tool
result holds. Mathematics converts to MathML through the vendored Temml.
`docs/viewer.md` specifies what each of them covers.

## The derived-messages rule

`src/messages.ts` implements the rule in `docs/log-format.md` under
"Derived messages". An inbox item enters the list at the position of the
`model/request` that lists its seq in `consumed`. This placement keeps a
steering message that arrived while a tool ran after that tool's result,
and puts the items consumed by the request being built at the end. After a
`compaction/summary`, the list opens with the task and the continuation
message, rendered as `docs/compaction.md` specifies, and continues from the
summary's `first_kept_seq`; a request whose id starts with `cmp_` and its
response contribute nothing.

The bundle assumes the recorded `messages` use these shapes:

```json
{ "role": "user", "content": [ ...content blocks ] }
{ "role": "assistant", "text": "...", "tool_calls": [ ...tool calls ] }
{ "role": "tool", "call_id": "...", "name": "...", "rendered": "...", "is_error": false }
```

## Design language

The bundle follows [`docs/design-language.md`](../docs/design-language.md).

Every colour on the page is a `--v2-*` role token. The sixteen theme blocks
in `src/tokens.css` are copied from zicato's
`src/zicato/dashboard/static/css/console.css`; the only change is the
selector, re-scoped from `#variant-root[data-variant="T"][data-t-theme="<id>"]`
to `:root[data-theme="<id>"]`. That file is the only place a raw hex colour
appears in the stylesheet, and it also holds the two values of
`--foe-accent`, the brand accent that fills the lockup's core:
`#C7791A` on the five light-ground themes and `#E8A43E` on the other
eleven. The swatch preview strips in `src/chrome.ts`
carry the tuples from zicato's `ui.js` `COLOR_THEMES`, including the
substituted preview accent for `lunaria-eclipse`. When no theme is stored
and the host page has stamped none, `prefers-color-scheme` selects
`google-light` on a machine asking for a light ground and `google-dark` on
one asking for a dark ground. `src/tokens.css` repeats those two palettes on
a root carrying no theme, so the first paint already matches.

Typefaces are a separate axis. `[data-typeface]` on the root names one of
twelve faces, four per mode, and resolves `--v2-sans`, `--v2-mono`,
`--n-font-head`, and `--n-font-paper`. The default face is Inconsolata in
every role. Three families are self-hosted, both weights each: Inconsolata,
iA Writer Mono, and JetBrains Mono. The six woff2 files under `fonts/` are
declared in `src/tokens.css` with `font-display: swap` and the path
`/fonts/<name>.woff2`; `fonts/README.md` records where each file came
from. The live server
serves that path and the static export replaces it with a data URI. The
bundle performs no network fetch for any font. This is a departure from
zicato, which loads the editorial and display families from a font service;
here those families resolve when the machine has them and fall back to the
listed system faces otherwise, because the viewer runs on loopback and in
environments without a network.

Each typeface option in the picker names itself in its own face over one
specimen line: a line of code for a technical face and a sentence for an
editorial or display face. The three text-size controls are each set at the
size they select.

Theme, typeface, text size, page scale, and region sizes persist in
`localStorage` under `foe.theme`, `foe.typeface`, `foe.fontsize`,
`foe.scale`, and `foe.panes`. One function applies each, in `src/chrome.ts`
for the four appearance settings and in `src/panes.ts` for the region
sizes, and every control that changes a value calls that function.

## Layout of this directory

```
build.mjs                     esbuild driver; size and inlining checks
src/main.ts                   entry point: settings, then the application
src/app.ts                    state, regions, keyboard, digest-gated redraws
src/chrome.ts                 top bar, pickers, persisted settings
src/appearance.ts             themes, typefaces, sizes, and their names
src/brand.ts                  the lockup and the research-preview tag
src/panes.ts                  region sizes, the grips, and their persistence
src/trajectory.ts             where every mark of the timeline goes
src/workflow.ts               a declared graph, its run, and its layout
src/statistics.ts             every quantity the statistics tab shows
src/source.ts                 static and live event sources
src/fold.ts                   one episode log to rows, a summary, marks, and firings
src/messages.ts               the derived-messages rule
src/lineage.ts                the tree, the shared fork prefix, the per-row measure
src/prompt.ts                 a system prompt read back into its sections
src/render/conversation.ts    the dialogue rows
src/render/trajectory.ts      the timeline figure
src/render/tree.ts            the episode tree and the details panel
src/render/workflow.ts        the declared graph with the run over it
src/render/statistics.ts      the six statistics figures
src/render/hovercard.ts       the one card every figure explains a mark in
src/render/raw.ts             the raw events table
src/render/diff.ts            two forked episodes side by side
src/render/markdown.ts        the Markdown parser
src/render/markup.ts          elements for Markdown, code, diffs, and math
src/render/highlight.ts       the syntax tokenizer
src/render/unified-diff.ts    the unified diff reader
src/render/shape.ts           what kind of text a tool result holds
src/render/svg.ts             building SVG, and the two shapes a figure takes
src/viewer.css                the stylesheet, on role tokens only
src/tokens.css                theme, typeface, spacing, radius, and brand tokens
vendor/                       Temml and its license
fonts/                        the six self-hosted woff2 files and their note
fixtures/                     fixture logs and their generator
test/                         unit tests
```
