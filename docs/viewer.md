# Viewer

The viewer renders an episode directory, which is the log of one episode
and the logs of every descendant under `children/`, or a directory of such
directories, whose episodes are shown side by side as independent runs.
"The episode tree" below states which of the two a directory is read as.
The viewer has two halves. A browser
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

The main region holds one episode, the selected one, however many the
viewer shows. Reading several episodes against each other is what the
other figures are for: the trajectory draws every episode as a row, the
statistics tab gives every root a row of its own, and the diff tab sets
the suffixes of two forked episodes side by side. A second conversation
pane would take width from those three and answer a narrower question than
any of them.

Every divider between two regions is a grip that resizes them, by drag, by
the arrow keys, or to a limit with Home and End; a double click returns a
grip to its default. `docs/design-language.md` specifies the grip.
The sizes persist in `localStorage` under `foe.panes` as one object with
the sidebar width in pixels and the two row splits as a fraction of the
column each divides. The object names only the sizes the reader has moved,
so a size left alone stays derived across a reload. The sidebar opens at
288 pixels, the rail width the spacing scale names, and the details
region at 30 percent of the left column.

The trajectory opens at the height its rows need: the axis, the pixels the
rows take, and the padding around them, held at or above the shortest pane
and at or below half the right column. A row is as tall as the channels it
holds, so the height follows the drawing rather than the row count. A run of
one episode therefore opens a region the height of one episode rather than a
fixed share of mostly empty ground, and a spawn during a live run grows the
region as it adds a row. Once a grip has moved the trajectory the stored
size wins and the rows no longer change it; a double click on a grip drops
the stored size and returns the region to what derives it. Every region
declares a minimum, so none of them collapses.

### Runs of one program

`episode/start.identity` is a hash over everything that shapes what the
model sees, which [design.md](design.md) specifies under "Programs and
identity". Two episodes of one program carry one identity and unrelated
episodes carry different ones, so it is what separates runs that may be
compared from episodes that merely sit in one directory.

Roots that carry one identity stand together in the episode list and in
the trajectory, and a hairline bracket spans their rows: down the left
edge of the episode list, and in the gutter between the label column and
the plot in the trajectory. A root's descendants are inside its bracket,
because they are part of that run. A program with a single root in view
gets no bracket, since a bracket around one row groups nothing, and an
episode whose `episode/start` has not been read yet has no identity and
stands alone until it has. Hovering a bracket names the program, the
number of runs, and the identity they share.

A rebuild of the runtime changes identity, because the runtime's version
and build hash are part of it. Two runs of one configuration separated by
a rebuild are therefore two programs here, and they are drawn as two.

The episodes tree gives each episode a row about 40 pixels tall: a dot
coloured by outcome, the program name at the page's base size, the episode
id in mono beside it, a second line reading the outcome word with the code
of a `blocked` outcome or the limit of an `exhausted` one, and under those a
measure of what the episode spent. A spawned child hangs under its parent on
a solid connector and a fork under its origin on a dashed one, both two
pixels wide in `--v2-ink-faint`.

The measure is a hairline bar whose fill is the episode's tokens as a
fraction of the largest token total among the episodes with the same parent,
itself included, so the biggest spender of every sibling group draws a full
bar. Hovering it gives the token count and the fraction. The bar is faint,
because the tree reads as names and outcomes first; the selected row's bar
takes the accent that marks the row.

The quantity is tokens rather than wall clock, because tokens are the one
spendable thing a tree divides without overlap. A parent and its
descendants draw on one budget pool, and the tokens each of them reports
are its own, so a group's bars are comparable and their sum is the group's
spending. Wall clock does not divide that way: siblings that ran at the
same time each hold the whole interval, so their durations sum past their
parent's and a bar of one against another would assert a division that did
not happen.

The details region states the outcome, the model calls, input tokens, and
output tokens consumed against the limits in
`episode/start.program.budget`. It also states the sandbox mode and Landlock
ABI, the program identity, the lineage, the event count, the start time, the
duration, and the task. The identity is set as the
first eight characters of its digest, with the whole hash in its tooltip. Its text wraps and its numbers are tabular; the
region scrolls as a whole when its content exceeds it, and nothing inside
it has a scrollbar of its own.

The conversation is derived from the log by the rule in
[log-format.md](log-format.md#derived-messages). It holds the system prompt
and tool schemas from `request/header`, each inbox item, and each assistant
turn.

The system prompt is the best available account of why an agent behaved as
it did, so it is rendered the way an assistant message is: as Markdown,
behind an expander whose summary names the reason the header was written,
the route, the number of tools, and the length of the prompt.

The prompt is shown under the names its author gave its parts.
`episode/start.program.instructions` maps a key to the text of one section,
and the runtime renders those sections in lexicographic order of their keys,
joined by a blank line, before it appends the instruction of every tool that
has one; [config.md](config.md#instructions) states that rule. The viewer
reads the rendered prompt back through the same rule and heads each section
with its key. The walk stops at the first section whose text is not where
the rule puts it, and the rest is rendered as one piece, so a prompt the
program did not compose is shown whole rather than split wrongly.

Each tool's schema is a table of its parameters, one row per parameter with
its type, whether it is required, and its description, under the tool's name
and the description the model was given. An enumerated parameter names its
options in its description. The schema's JSON says the same thing in a
shape that has to be parsed by eye before it can be read.

A `request/header` whose `reason` is not `initial` was written because the
prompt, the tool schemas, or the route differ from the header in effect. It
carries a list of what differs, above the prompt: the route it replaced,
each instruction section added, rewritten, or removed, whether the appended
tool instructions changed, and each tool added, removed, or redeclared.

Four states a row carries are drawn beside the row rather than written out
as a word, in the shapes [design-language.md](design-language.md) sets out
under "Figures". An assistant turn assembled from `assistant/chunk` events
carries an open ring with dashes ahead of it, in the accent, while its
response is still arriving. A turn still assembling when `episode/end` is
read is a stream that was cut off, so it carries the mirror of that shape
in `--v2-caution`: a hairline that runs and stops at an open ring. The
turns whose `assistant/message` reports truncation carry the same mark. A
finished log therefore never shows a turn as still arriving. A tool result
the tool reported as a failure carries a cross in `--v2-bad`, and a result
the runtime wrote without running the tool carries a dashed hairline in
`--v2-ink-soft`. Each mark reads out as the word it replaced and opens the
hovercard on that word with one line saying what the state means.

Where a message came from is one of the six words `inbox/item` allows, and
the file a spilled tool value sits in under `spill/` is a name a reader
copies, so both stay text in faint mono beside the rest of the row's
metadata. Each tool call appears with the text the model received and the
canonical value the log stores. A compaction appears as one system row
placed at the cut, stating how many messages the summary replaced, with the
continuation message the model receives behind an expander; the rows above
it stay visible, faded and marked `compacted`, because the pane shows the
log and the log keeps them. Everything the bundle shows is computed in the
browser from the events; the server contributes only the tree, which tells
the bundle which episodes exist and in what order.

## The trajectory

The trajectory draws one row per episode, in the order the tree lists them,
indented by lineage. The row label is the program name; the episode id
stands beside it in the sidebar and in the breadcrumbs, so the row does not
repeat it. The selected row carries the figure's one accent as a spine down
its leading edge.

A control in the region's header sets how the run is read. Its first three
settings are axes: they set what x measures and map linearly onto the same
plot area, so switching moves the marks and changes nothing else. Its
fourth, **causality**, replaces the timeline with a figure of a different
orientation, specified under "Causality" below.

- **wall clock** places a mark at its event's `time`. Two marks at one x
  happened at one moment, which is what a reader of a single tree wants.
- **elapsed** places a mark at the time since the start of its row's own
  root episode. Every root then begins at the left edge, and the axis
  spans the longest run rather than the interval between the runs. A child
  keeps its offset from its root, so a tree holds its shape and only whole
  trees are moved.
- **sequence** places a mark at its event's `seq`.

The figure opens on the axis its content calls for: elapsed when it holds
more than one root, because independent runs started days apart would each
draw as a sliver of an axis spanning those days, and wall clock inside one
tree, where simultaneity between rows is real. Once the reader has picked
a setting, that setting stays. The axis carries small mono labels on
leader ticks at round offsets from the start of the run, and each tick
carries a gridline down the plot in `--v2-rule-soft` at 0.6 pixels, because
a bar's length is read against the axis. The figure fits the region's width
and reflows when the region is resized; it neither pans nor zooms, and it
redraws only when a digest of what it would draw changes.

### Causality

The three axes measure *when* and run left to right. Causality shows *what
caused what* and runs top to bottom: left to right runs out of width at
about eleven columns and a real run has more, while downward scrolls the
way a long run already wants to and never needs horizontal room. Time runs
down and structure runs across.

The figure is built from the log's obligation pairs and never from an
inferred parent, so it draws no edge the log does not carry. `spawn/start`
names the tool call that opened a child, and `workflow/node-start` names
the child a model node ran; those two are the whole of what opens a lane.

A lane is earned. Two things earn one: an **episode**, which has its own
agent, budget and typed outcome and can outlive the call that made it, and
a **workflow**, which branches and loops. Everything else is a mark on the
lane it belongs to. A **step** — one model request and the tool calls it
produced, `step` on the log's own events — is a row on its episode's lane,
and every step the log names is a row whether or not a message answered
it. A **tool call** is a short tick off its step's row with its mark at the
end; no return edge is drawn, because the lane continuing past the tick is
the return, a call can neither diverge nor outlive its caller, and calls
are the largest count in the model. `spawn` is not special-cased: it is
the call whose tick opens a lane, because what it created can outlive it.

A workflow node the run entered more than once is one row and a loop edge
back up to it, not one row per firing. That is what lets the scoped
conversation show every pass over the node.

Each lane is one continuous line at its own column, from its first row to
its last, stretched to reach every curve that joins it; a lane of one row
gets a short stub, so its own elbow and its merge have ground between
them. A lane takes the lowest free column when it opens and releases it
when it closes, so column is occupancy and not tree depth — tree depth is
carried by the label's indent instead. The layout claims no room past its
own marks: it reports the width its strokes take and gives each row an
indent from wherever its reader sets the text column, so what stands
beside the drawing is the caller's decision and not the figure's. Edges
are cubic with their control points on the midline, except a loop, which returns to the column it left
and bows out of it. There are no arrowheads: time runs down, so direction
is unambiguous and a head on every edge would be noise.

The figure paints in three layers — the row highlight, the strokes, then
the labels — so a selected row never hides a line and a line never crosses
out a name.

Lane colour carries branch identity, cycled over five tones mixed from the
theme's own tokens so that it follows every theme, and none of the five is
an outcome hue. Hue carries the outcome and carries it only on the marks:
at the foot of a lane, a ring in `--v2-good` for `Completed`, in
`--v2-caution` for `Exhausted` and in `--v2-flat` for `Blocked`, and a
cross in `--v2-bad` for `Failed`. A tool call whose result reported a
failure takes that same cross on its tick, so a failure is visible at the
leaf without reading anything.

A row is named by its semantic role first and its durable identifier
second, never by a substring of free text: a workflow node by its own
name, a step of one call by the tool and its target (`read
src/parser.rs`), a step of several by the first call and a count (`read
src/parser.rs +2`), a delegation by `spawn` and the child's program name
(`spawn surveyor`), a step that called nothing by `answered`, and a step
whose request no message answered by `no answer`. `step N` rides alongside
in faint.

The tool name stands beside the target even though the tick beside it
already draws a mark. The redundancy is deliberate: a word is faster to
scan than a glyph, and the target is the thing a reader is looking for.
The target is the whole of the one short argument the call carries — a
path, a program name — because which directory a file sits in is part of
what identifies it; an argument with whitespace in it is free text and is
never shown, and a path too long to set has its middle elided and its
basename kept (`src/…/parser.rs`), never a trailing cut.

Selecting a row scopes the conversation to it — that node's own messages
and those of every node below it — rather than merely highlighting it. A
header names the scope and carries an escape back to the whole run. A
workflow node entered twice yields one section per pass, labelled `pass 1`
and `pass 2`, and each section is named by the node itself, so a graph of
four nodes reads `propose`, `check`, `revise`, `accept` and never four
sections all reading "workflow".

### The unified outline

The episode rail, the causal figure and the conversation are not three
things. They are one hierarchy — episode, step, call, message — read at
different depths, so one collapsible outline replaces all three and a
**depth control** moves between the readings. It is the arrangement the
viewer opens in.

| depth | what it shows |
| --- | --- |
| `episodes` | episodes alone, which is the rail |
| `steps` | workflow nodes and steps, which is the tree |
| `calls` | tool calls with their targets, which is the causal figure |
| `everything` | what the model said and what each tool returned |

A caret on any row opens that one branch one level past the current
reading, so a reader can sit at `steps`, open one step to see its calls,
and open one call to see its result without expanding the run. The depth
presets are the coarse control and the carets are the fine one.

The gutter nests and the text does not. Every label starts in one column,
because the lanes already draw the structure and a ragged left edge makes
a run tedious to skim: the eye reads the indent instead of the sequence.
The single exception is a tool call and its result, which step in one
level under the step that issued them, because a call is part of that step
rather than the next thing that happened. Exactly two label columns,
therefore. Prose and result bodies break even that and run the full width
from the text column, because a diff at depth five would otherwise lose
the room it needs; the inconsistency is deliberate, and code needs the
width.

Depth is counted against the visible set and never against the raw
hierarchy. Read at its coarsest a child episode is still an episode one
level in, even though the call that spawned it is not on the page.

A label stands in for children that are not on the page, so a step's label
defers to its calls once those are rows of their own: with them hidden it
reads `read src/parser.rs +1`, and with them shown it falls back to `step
1`, plus `attempt N of N` when the request retried, so that it does not
echo the line directly beneath it.

The lane geometry is the same geometry, computed over the visible rows, so
it is recomputed on every change of depth or caret. Rows are not one
height — a line of prose and a result body are taller than a node — so the
rows are laid out and measured first and the lanes are placed from the
heights they actually took, never from an assumed pitch. A row's mark sits
on its first line rather than at its vertical middle, so a row holding a
diff still has its mark beside the name it belongs to.

Two costs come with it and are not hidden. A child episode's rows sit
under the call that spawned it rather than interleaved by time, so reading
order is not global order; every row keeps its log position in the gutter,
which is the only way to see where order jumped. And one view reads at one
place in the hierarchy, so a reader cannot study one step's output while
the whole shape stays in view. That is why the other arrangement remains.

### Figure and conversation

The causal figure beside a conversation scoped to whatever is selected in
it: the arrangement the outline cannot be, because it holds two places in
the hierarchy at once. A control in the top bar chooses between the two,
and the choice is stored in `localStorage` under `foe.layout` beside the
theme, the typeface and the text size.

With the outline showing the run it stands in for the episode rail, the
trajectory region and the conversation tab together, and those are not
drawn; the raw events, diff, workflow and statistics tabs are readings of
something else and stay reachable below it.

### The channels of a row

An episode's work nests: a workflow node holds model requests, a request
holds a wait and a stream, one step issues a batch of tool calls, and a
parent holds children. A row separates those channels by position, because
duration does not separate them: on a run bound by the model every tool
call is milliseconds against a span of tens of seconds, so its segment is
drawn at its minimum width whatever lane it is in.

The order down a row is the order of containment.

1. Model requests sit above the lifetime line, so a request reads above
   the line and everything the episode did inside it reads below.
2. The lifetime line runs from `episode/start` to `episode/end` and carries
   the marks that belong to the episode as a whole.
3. The node band holds one lane per node of a declared graph that fired,
   and is absent for an episode that runs the free loop.
4. The tool lane holds one mark per `tool/result`, below every node lane.

A row is as tall as its own channels. A plain row is 24 pixels; a node band
adds 9 pixels per lane, and a stack of calls adds 6 pixels per height beyond
the first. The region's derived height follows the pixels the rows take
rather than the row count.

| mark | channel | drawn from | form |
|---|---|---|---|
| lifetime | line | `episode/start` to `episode/end` | a hairline bar, continued as a dashed extension to the current time while the episode runs |
| model request | above the line | `model/request` to its `assistant/message` | a bar in two parts, 4 and 6 units thick, on a hairline stem down to the line |
| compaction | line | `compaction/start` | a small open diamond in `--v2-caution` |
| retry | line | `request/retry` | a cross in `--v2-bad`, with the backoff it imposes running forward from it as a dashed segment |
| spawn | line | `spawn/start` | a small ring, and the origin of the child's connector |
| outcome | line | `episode/end` | a glyph at the end of the bar |
| node firing | node band | `workflow/node-start` to its `workflow/node-end` | a bar 5 units thick on the node's own lane |
| branch | node band | `workflow/branch` | a tick on the choosing node's lane, with the label it chose |
| recovery | node band | `workflow/recovery` | an open square in `--v2-caution` on the failed node's lane, with the action it applied |
| tool call | tool lane | `tool/result` | a segment 3.5 units thick whose width is `duration_ms`, ending at the result |

A model request is a bar rather than a tick, because how long an answer took
is most of what a run's shape consists of: one request of a four-request
episode can hold more than half its wall clock, and a tick would draw that
request exactly like the shortest one.

The bar has two parts, in the encoding the statistics view's per-step bars
use, so that a reader learns one grammar rather than two. The lower and
fainter part spans the whole answer, from the `model/request` to the
`assistant/message` that answers it. The taller part over it spans the wait
before the first token, from the `model/request` to the first
`assistant/chunk` of that request whose `chunk.kind` is not `error`. A
request answered only by errors produced no token, so it has the first part
and not the second. A request with no answer yet spans to its last chunk, so
a request still streaming shows the length it has reached.

The two parts have a length on the sequence axis as well, because the chunks
a request produced are events between its call and its answer. A tool call
has no such length: its duration is in milliseconds and the log records the
result alone, so on the sequence axis a tool call is a tick. A retry's
backoff has no length there either, for the same reason: a delay is a wait
between two events rather than a run of them. A tool call slow enough to
have a visible length on the time axis keeps it; a shorter one is drawn at
the minimum width, so no call disappears.

Every bar is `rx: 3` and filled at reduced opacity, and lifts to full
opacity while the pointer is on it.

### Telling one kind of mark from another

The ten kinds of mark in the table above can all fall on one row, and
colour does not separate them: hue carries direction alone, so a mark that
earned no direction is neutral whatever kind it is. `docs/design-language.md`, "What separates one kind of
mark from another", states why and gives the rule. Four channels carry kind
here.

The **lane** comes first: requests above the lifetime line, the marks of the
episode as a whole on it, node firings in the band below it, tool calls
below that. No two kinds share a lane. The **shape** comes next, and each
kind keeps its shape wherever the viewer draws it, so the grammar of the
timeline is the grammar of the conversation and of the workflow tab.
**Thickness** is the third: no two channels draw a mark of the same
thickness, and each mark is thinner than the lane that holds it.

**Ink weight** is the fourth, and it follows the size of the mark rather
than its kind, so that every mark carries about the same weight of ink. A
request's span is the longest bar of a row, so it fills `--v2-ink-faint` at
0.6; the wait over it is shorter and fills `--v2-ink-soft`; a node firing
covers several requests and fills `--v2-ink-soft` at 0.8; a tool call is
usually drawn at its minimum width and fills `--v2-ink` at 0.9. On a run
bound by the model the request bars therefore stay quiet while the tool
ticks stay visible, which is the reverse of what an even ink would give.
Structure that measures nothing takes `--v2-rule`: the lineage connectors,
the depth rail, and the bracket over the runs of one program.

The marks that do carry a direction keep it: a retry and its backoff in
`--v2-bad`, a compaction and a recovery in `--v2-caution`, a node firing
that ended in `--v2-good` or `--v2-bad`, and the outcome glyph. A run that
failed is therefore the only kind of row with red in it.

The outcome glyph is coloured by direction: a filled dot in `--v2-good` for
`completed`, a cross in `--v2-bad` for `failed`, a triangle in
`--v2-caution` for `exhausted`, and a flat bar in `--v2-flat` for
`blocked`. A running episode ends in an open ring.

### The node band

An episode whose program declares a graph spends its whole run inside that
graph, so the trajectory is where the timing of the graph belongs; the
workflow tab draws the topology. The band holds one lane per node that
fired, ordered by the first firing of each, which is the order the run
entered the nodes. A workflow that runs straight through therefore reads as
a staircase, and a cycle reads as a step back up to a lane already used. A
node the graph declares and the run never entered has no lane, because it
has no time to occupy; the workflow tab is where its absence is drawn.

Each lane is named in the label column, one indent deeper than the episode's
own label, in faint mono. A firing is a bar between its `workflow/node-start`
and the `workflow/node-end` that closes it, so its width is the interval the
log observed. The length the node itself reported in `duration_ms` is a
separate quantity, and the hovercard gives it beside the observed one. A
firing that has not ended reported no length at all, and the hovercard says
so rather than showing a zero.

A firing takes its colour direction from how it ended: `--v2-bad` for a
firing whose `workflow/node-end` carries an error, `--v2-good` for one that
ended cleanly, and neutral ink while it runs. That is the direction the
workflow tab gives the same firing, so the two figures share one grammar.

A model node's firing ran a child episode, which has a row of its own below.
Its bar is outlined rather than filled, because the work it stands for is
drawn in full on that row, and the connector to that row leaves the bar
rather than the spawn ring on the lifetime line. The firing's bar and the
child's lifetime bar are then the same interval, one under the other, so the
correspondence between the graph and the run is visible without hovering.

A branch's label and a recovery's action are written beside their marks. A
label is dropped where a firing of the same node overlaps the room it
needs, because a label set over a bar reads as neither; the hovercard names
every decision whatever is drawn.

### Calls issued together

A model that issues six reads in one turn produces six `tool/result` events
at one instant, which land on one x. Each call keeps that x and takes the
lowest height of the tool lane whose last call ended at least two pixels
before it starts. A run of calls one after another therefore stays on one
height, and a batch issued together fans into as many heights as it has
calls, so six parallel reads read as six. The height carries no quantity: it
is a tie-break, and the calls of one batch stack in log order with the
earliest nearest the line.

### Lineage

A connector runs from where the parent started the child to the start of the
child's bar: from the model node's firing when a firing names that child,
and from the parent's `spawn/start` mark otherwise. It drops at that x,
turns once in the gap above the child's row, and drops into it, so it
crosses an intervening row as a hairline rather than sweeping along it. It
is drawn in the separator ink at 0.9 pixels against a lifetime line of full
ink at 1.2, because structure recedes behind activity. A fork's connector
runs from the origin's position at the fork boundary and is dashed.

Depth is carried twice in the label column: each level indents the label by
16 pixels, and a rail of hairline segments stands at each ancestor's indent.
The nearest ancestor's segment turns into the row's own label; a further
one passes through the row when a deeper row follows it. Without the rail a
three-level tree reads as three indents rather than as a hierarchy.

The column is 26 percent of the pane. That share is held at or above 116
pixels plus one indent per level of the deepest row, and at or below both
230 pixels and 45 percent of the pane, because the plot is what the figure
is. A name still too long for what the column leaves it is set with an
ellipsis rather than run into the plot. Hovering it gives the whole name,
the episode id the row no longer prints, and the outcome.

### Reading a mark

Hovering any mark, firing, decision, or outcome glyph opens the hovercard
`src/render/hovercard.ts` defines, which every figure in the viewer shares.
The card names the mark, gives its `seq`, its time, its duration when it has
one, the wait before its first token when it is a request that received one,
and one line of detail: a tool call's arguments, a retry's step and attempt,
a spawned child's program, a firing's reported duration and the child it
ran, or a decision's cause and target. The card stands below the pointer,
or above it when it would otherwise leave the pane at the bottom, and never
crosses the pane's right edge.

Clicking a mark selects its episode and brings the conversation to that log
position, where the row is marked until another is. Clicking a model node's
firing selects the child episode it ran. Clicking a row label selects that
episode, and `j` and `k` move between rows.

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

The statistics tab draws nine figures over the selected episode, or over
that episode together with every episode under it. A control in the tab's
header switches between the two, because a child spends from the budget
pool its root holds: the tree is the scope a declared limit actually
bounds. Where the viewer holds more than one root, the control offers a
third setting, **every run**, described below; with one root that setting
is absent, because a collection of one has nothing to compare. Every
quantity is derived in the browser from events the log already carries.

Three rules govern the presentation.

- Every figure states the arithmetic behind a number a reader could not
  derive by eye. Hovering it opens a hovercard giving the quantity's
  definition and the values it was computed from, so a cache hit rate of
  4.3 percent shows the 2,560 and the 60,041 behind it.
- A quantity no event in the scope measured is shown as absent rather than
  as zero. A run with no cache-read figure has no hit rate, and drawing it
  as zero percent would assert a measurement that was not made. A share
  short of the whole never rounds to the whole either: a run that spent
  99.76 percent of its wall clock inside model requests reads 99.8 percent.
- A figure that mixes a measurement with a quantity computed from it says
  which is which. The token attribution below is the case that needs the
  rule: the input a provider reported is a measurement, and the division of
  that input among the parts of the request is not.

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
| `input_tokens` | `usage.input` over every `assistant/message` |
| `output_tokens` | `usage.output` over every `assistant/message` |
| `seconds` | the scope root's wall clock |
| `max_episodes` | the episodes in the scope, the root itself included |
| `max_depth` | the deepest lineage below the scope's root |

A limit the program does not declare has no row. `max_concurrent` and
`loop_threshold` have no row either: neither bounds a quantity the log
accumulates, so neither has a consumption to draw.

### Where the input tokens went

A request's input is one prompt built from several pieces of text, and the
log records every piece. A **part** is one such piece, and it has one of
six kinds.

| kind | where the text comes from |
|---|---|
| system prompt | `system` of the `request/header` the request's `header_seq` names |
| tool schemas | every tool's `name`, `description`, and `parameters` in that same header, concatenated |
| task and inbox items | the `content` of a `user` message of `model/request.messages`, the first of which is the task |
| assistant turns | the `text` and `tool_calls` of an `assistant` message of that list |
| tool results | the `rendered` text of a `tool` message of that list |
| summarization prompts | the one `user` message of a request whose `request_id` starts with `cmp_`, which is a transcript to summarize rather than an item the episode received |

Two requests name one part when they carry the same text. A tool result is
identified by its call id, which the log gives it; every other part is
identified by its text, so a rewritten system prompt is a second part and a
resent one is not. A part belongs to the episode whose log carries it, and
two episodes that send the same system prompt send it twice.

The size of a part is the length in characters of the text just named. No
event states how many tokens a part cost, so the tokens attributed to one
are computed, and the table below says how.

| quantity | definition |
|---|---|
| characters of a part | the length of the text the log records for it |
| characters of a request | the total over the parts the request carried |
| characters per token | a request's characters divided by the `usage.input` its answer reported. Absent for a request no answer reported an input count. |
| tokens of a part in one request | the part's characters divided by the request's characters, times the `usage.input` that request's answer reported. Absent for a request no answer reported an input count. |
| replay cost | the tokens of one part added over every request that carried it and reported an input count |
| characters sent | the part's characters times the number of requests that carried it, which stands in for replay cost where no answer reported an input count |
| unique input | input tokens carrying text no earlier request of the episode had sent |
| replayed input | input tokens carrying text an earlier request of the episode had already sent |
| cache-read tokens | the total of `usage.cache_read`, reported beside the input and never subtracted from it, because a cached token is an input token in the provider's accounting |

Apportioning a request's input by characters divides a measurement rather
than estimating one, so the parts of a request always add to the input its
answer reported. What the division cannot guarantee is the boundary between
two parts, because a schema and a paragraph of prose do not encode at the
same characters per token. The figure prints the range of that rate across
the requests it draws, so that a reader can see how far the parts of one
request could be from each other.

The unique and replayed shares are measured rather than apportioned
wherever the log permits it. Where one request carries everything the
request before it carried, the two differ by the text new to the later one,
so the difference between the two counts the provider reported is what that
text cost. The first request of an episode is the same measurement with
nothing before it. A request that dropped text its predecessor carried,
which a compaction does, breaks the chain; its own shares are apportioned
by characters, and the figure names how many requests that happened to.

An unanswered request is text with no cost attached. Its characters are
counted, its tokens are absent, and any total that spans it is a floor: the
figure prints such a total with a `≥` and ends its bar with a dashed
hairline. A request whose answer reported nothing never marks its text as
sent either, since counting it would move the tokens of the retry that
follows into the replayed share on the strength of a request whose own cost
is unknown.

### The figures

**Where the wall clock went** is one bar divided into model time, tool
time, retry backoff, and unaccounted time, with the largest share accented
because which share dominates is what the figure argues. When the episodes
of the scope ran at the same time the intervals sum past the wall clock;
the bar then divides by that sum and says so, rather than drawing a share
above one.

**Context growth** plots input tokens against step position, one line per
episode of the scope, with the longest line accented. The declared
input-token limit is a dashed envelope across the plot, and the top of the
plot is the larger of that limit and the highest point. The curve shows how
provider-reported request input approaches the declared limit. A compaction's
own call is left out because its input is the summarization prompt rather
than the context.

**Where the input came from** is one bar per request, divided into the
parts that request carried and as long as that request is large in
characters. Characters set the length because every request has them, and a
request whose answer reported no usage would otherwise draw nothing. The
six kinds are one neutral ink at six weights, running from the text the
program fixes to the text the run produced, and the kind that accounts for
the most of the scope takes the accent. Hovering a division names the part,
gives the arithmetic that attributed tokens to it, and says whether the
request was the first to send that text. Clicking a row brings the
conversation to that request.

**Replay cost** is one row per part, largest cost first, with the part's
size, the number of requests that carried it, its cost, and a bar of that
cost against the largest. The ranking is by cost rather than by size,
because a result of middling size carried by five requests outweighs a
larger one that arrived on the last turn. Where no answer in the scope
reported an input count the column becomes characters sent and the ranking
follows it. Parts beyond the twelfth are counted in the caption rather than
drawn. Clicking a row brings the conversation to the request that first
carried the part.

**Unique against replayed input** is one bar in two shares, with the larger
accented, and the cache-read total beside it rather than inside either
share. The caption says which of the two derivations produced the split.
The figure answers one question: whether an operator should attack the size
of the results a run puts in the transcript or the number of turns that
resend them.

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

**Every run** replaces the nine figures with one row per root episode: the
program name and episode id, the outcome, the model requests, the tokens,
the wall clock, the retries, and a bar of that run's tokens against the
largest run. Each row is counted over that root and its descendants alone.

No column of that table is a total across roots. A budget is a pool a root
reserves and hands down to its descendants, so adding two roots together
would state one pool where there are two, which is the same kind of claim
as reporting an unmeasured quantity as zero. Comparison is what the table
offers instead: the bar puts each run's spending against the largest run,
and the other columns stand side by side. A run whose answers reported no
usage at all has no token figure and no bar, for the same reason a run
with no cache-read figure has no hit rate. Clicking a row selects that
root.

## The raw events tab

The tab is a table of every event of the selected episode: its `seq`, its
time, and its type. Clicking a row opens the event's payload under it.

A payload written by this runtime is not anonymous JSON. A `model/request`
carries a conversation, an `assistant/message` carries a token count, a
`tool/result` carries a diff. Setting those as nested braces makes a reader
parse a shape the viewer already knows how to draw, so the payload is drawn
field by field, in the order the log wrote the keys, and a field whose shape
recurs is set the way the rest of the viewer sets it. `src/payload.ts` holds
the table of shapes and `src/render/payload.ts` builds the elements.

| event type | field | how it is set |
|---|---|---|
| `model/request` | `messages` | as messages: each one's role, then its own words |
| `inbox/item`, `team/message` | `content` | as content blocks, a text block being its text |
| `assistant/message` | `text` | as Markdown |
| `assistant/message` | `thinking` | one reasoning block per entry, its replay token beside it |
| `assistant/message` | `tool_calls` | each call's name over its arguments |
| `assistant/message`, `compaction/end` | `usage` | one line of labelled numbers |
| `assistant/chunk` | `chunk` | the fragment's kind over the text it carried |
| `tool/result`, `workflow/node-end` | `rendered` | by the text's own shape, as a diff, as JSON, or as numbered source |
| `request/header` | `system` | as Markdown |
| `request/header` | `tools` | one table of parameters per tool, as the conversation sets a schema |
| `episode/end`, `spawn/end` | `outcome` | the kind in the colour of its direction, then its other keys |
| `episode/start` | `task` | as preformatted text |
| `compaction/summary` | `summary` | as Markdown |

A field this table does not name, and every field of an event type it does
not name, goes to the structured renderer. An object or an array there opens
and closes and states how many keys or items it holds while closed, an
object also naming its first four keys. A value's kind is carried by its
colour, on the register the code tokenizer uses: a number in `--v2-caution`,
the words `true`, `false`, and `null` in `--v2-accent`, and a string in full
ink, because in a field list the key beside a string already says it is one.
A string of at most 140 characters holding no line break sets on its key's
line; a longer one sets its first line and its length over the whole text.
Keys keep the order the log wrote them; nothing is sorted. A node opens
without being asked when it is at most two levels down and would take at
most twelve lines open.

Two properties hold whatever the payload contains. Nothing is dropped: every
key yields a field, a field whose value does not have the shape its
rendering expects falls back to the structured renderer rather than being
drawn wrongly, every collapsed node opens to the literal value, and one
control at the foot of the panel holds the payload's own JSON text. And the
filter still searches that JSON text rather than the drawn elements, so a
query matches the seq, the type, or any value the payload holds, including
values inside nodes the reader has not opened.

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
parent's `children/`, sorted by directory name.

### What a directory holds

The directory the viewer is pointed at is read in one of two layouts, and
what is on disk decides which; no option selects a layout.

- A directory holding `episode.jsonl` is one **episode directory**. It is
  its own single root, and its descendants are the logs under `children/`.
  Every runner and every example passes a directory of this kind.
- A directory holding no log of its own is a **collection**. Each of its
  immediate subdirectories that holds an `episode.jsonl` is a root, sorted
  by directory name, and each root keeps its own descendants under its own
  `children/`. A subdirectory with no log, such as one holding notes, is
  passed over. This is the layout a log directory accumulates when several
  runs write into it, as `.foe` does.

`roots` therefore has one element for an episode directory and one per
entry for a collection. Both are empty until the first `episode/start` is
readable. Roots are independent: nothing is nested under a fabricated
parent, because a parent means a shared budget pool and a settled child,
and neither holds between two runs that merely share a directory.

A directory that is neither, such as a path that does not exist or one
holding no log anywhere, is read as an episode directory, so the failure
names the `episode.jsonl` that is missing. Live mode tolerates that
failure and retries, so a server started on an empty directory picks up
each run as its log appears.

An episode seeded from another log carries `fork_origin`, which names that
log's episode. The runtime writes the seeded episode under the origin's
own `children/` and gives it a `parent_id` as well, so the origin of a
fork is always a log in the same tree. No index from episode id to
directory is therefore needed, and none exists: an episode whose named
origin is not among the logs read is drawn as a root of its own.

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
export fails, naming the file, when a log under `dir` is malformed and
when `dir` is neither an episode directory nor a directory holding one.

## Embedding the bundle

`crates/view/build.rs` copies `view/dist/viewer.js`, `view/dist/viewer.css`,
and the six font files under `view/fonts/` into the crate's build output,
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
