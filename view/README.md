# Viewer bundle

This directory holds the browser application that renders foe episode logs.
It is written in TypeScript without a framework and compiled by esbuild into
two files, `dist/viewer.js` and `dist/viewer.css`. The two files together
stay under 150 KB after gzip compression; the build fails when they exceed
that budget. The only input the application reads is the log format defined
in [`docs/log-format.md`](../docs/log-format.md).

The Rust crate `crates/view` embeds the two files into an HTML page. It does
so in two modes, described below, and both modes run the same code.

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

The tests cover the derived-messages rule against the fixture logs, the
episode fold, and the lineage helpers. The fixtures are three episode logs
written by `fixtures/generate.mjs`: a root episode that spawns a team member
and survives an interrupted request, the spawned child that receives a peer
message, and a fork of the root seeded at seq 12 that ends blocked. The
`messages` list recorded in each `model/request` event is written by hand in
the generator, so the tests compare it with the list the bundle derives.

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
| `GET /fonts/<name>.woff2` | `X-Foe-Token` header | one of the four font files under `fonts/`; see Design language |

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

The top bar carries an up control that moves to the parent or fork origin
of the selected episode, the wordmark, breadcrumbs from the root to the
selected episode, the colour theme picker, the typeface picker, the page
scale control, and a status pill. The status pill shows the connection
state and, in live mode, the number of episodes without an outcome.

The left pane draws the episode tree as a line-art figure. A spawned child
hangs under its `parent_id` with a solid edge; a fork hangs under its
`fork_origin` with a dashed edge. Each node shows the program name, the
episode id when it fits, and the outcome. A `blocked` outcome shows its
code and an `exhausted` outcome its limit. Below the tree, the selected
episode's panel shows model calls and tokens consumed against the budget
declared in `episode/start.program.budget`, the Landlock ABI, the fork
origin, parent, team, and timing.

The main pane has three tabs.

- **conversation**: one row per event that contributes to the dialogue.
  The latest `request/header` appears as a collapsed system prompt row, with
  each tool schema behind its own expander. An `inbox/item` is a user row
  with a badge for its `source`. An `assistant/message` shows its text and
  tool calls; `interrupted: true` shows a marker. A `tool/result` shows
  `rendered` and keeps the canonical `value` behind an expander as
  pretty-printed JSON; `is_error`, `synthetic`, and `spill` show markers.
  Every other event type, including the reserved ones and types this bundle
  does not know, appears as a compact row with the event type as its label
  and the payload behind an expander.
- **raw events**: a table of every event with seq, time, and type. The
  filter matches the seq, the type, or any text in the payload. Clicking a
  row expands its payload.
- **diff**: for two episodes that share a fork prefix. The shared events are
  shown once, collapsed, under the label `shared, seq 0–N`; the two
  suffixes follow side by side. Two episodes share a prefix when one is a
  fork of the other or both descend by forking from a common origin; the
  shared length is the smallest seed boundary on the path between them.

New events patch only the rows they create or change. Scroll position
follows the end while the reader is at the end and stays put otherwise.
Expanders keep their open state across updates. The tree and the episode
panel redraw only when a digest of their content changes.

Keyboard: `j` and `k` move the cursor in the tree, `Enter` selects the
cursor, `c` marks the cursor for comparison, `/` opens the raw events tab
and focuses the filter, and `1`, `2`, `3` switch tabs.

## The derived-messages rule

`src/messages.ts` implements the rule in `docs/log-format.md` under
"Derived messages". An inbox item enters the list at the position of the
`model/request` that lists its seq in `consumed`. This placement keeps a
steering message that arrived while a tool ran after that tool's result,
and puts the items consumed by the request being built at the end.

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
appears in the stylesheet. The swatch preview strips in `src/chrome.ts`
carry the tuples from zicato's `ui.js` `COLOR_THEMES`, including the
substituted preview accent for `lunaria-eclipse`. Monokai is the default;
when no theme is stored and the host page has stamped none, a light system
preference selects `paper` instead.

Typefaces are a separate axis. `[data-typeface]` on the root names one of
twelve faces, four per mode, and resolves `--v2-sans`, `--v2-mono`,
`--n-font-head`, and `--n-font-paper`. The default face pairs iA Writer
Mono for prose with JetBrains Mono for data. Those two families are
self-hosted: the four woff2 files under `fonts/` are copied from zicato's
`src/zicato/dashboard/static/fonts/` and declared in `src/tokens.css` with
`font-display: swap` and the path `/fonts/<name>.woff2`. The live server
serves that path and the static export replaces it with a data URI. The
bundle performs no network fetch for any font. This is a departure from
zicato, which loads the editorial and display families from a font service;
here those families resolve when the machine has them and fall back to the
listed system faces otherwise, because the viewer runs on loopback and in
environments without a network.

Theme, typeface, text size, and page scale persist in `localStorage` under
`foe.theme`, `foe.typeface`, `foe.fontsize`, and `foe.scale`. One function in
`src/chrome.ts` applies each, and every control that changes a value calls
that function.

## Layout of this directory

```
build.mjs            esbuild driver; size and inlining checks
src/main.ts          entry point: settings, then the application
src/app.ts           state, panes, keyboard, digest-gated redraws
src/chrome.ts        top bar, pickers, persisted settings
src/source.ts        static and live event sources
src/fold.ts          one episode log to rows and a summary
src/messages.ts      the derived-messages rule
src/lineage.ts       the tree and the shared fork prefix
src/render/          conversation, raw events, tree, diff
src/viewer.css       the stylesheet, on role tokens only
src/tokens.css       theme and typeface tokens
fonts/               the four self-hosted woff2 files
fixtures/            fixture logs and their generator
test/                unit tests
```
