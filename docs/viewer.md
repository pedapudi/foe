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

The left pane lists episodes as a tree by lineage. Each entry shows the
program name, the outcome once the episode has ended, and token usage. The
right pane shows the conversation of the selected episode, derived from its
log by the rule in [log-format.md](log-format.md#derived-messages). It
holds the system prompt and tool schemas from `request/header`, each inbox
item, and each assistant turn. Each tool call appears with the text the
model received and the canonical value the log stores. A compaction appears
as one system row placed at the cut, stating how many messages the summary
replaced, with the continuation message the model receives behind an
expander; the rows above it stay visible, faded and marked `compacted`,
because the pane shows the log and the log keeps them. Budget consumption,
sandbox status, and the outcome come from the same log. Everything the
bundle shows is computed in the browser from the events; the server
contributes only the tree, which tells the bundle which episodes exist and
in what order.

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
