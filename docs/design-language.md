# Design language

foe's viewer follows the zicato design language in full, including its
chrome. This document states what that means for foe and where the
canonical definitions live. Where this document and those sources disagree,
the sources are authoritative.

| source | defines |
|---|---|
| `zicato/docs/design/DESIGN-LANGUAGE.md` | the colour role contract, the sixteen themes, the twelve typefaces in three modes, the spacing scale, the top bar, the pickers, the line-art figure conventions, the render discipline |
| `zicato/src/zicato/dashboard/static/css/console.css` | the authoritative per-theme token blocks and the typeface token map; the only place raw hex appears |
| `zicato/src/zicato/dashboard/static/js/ui.js` and `typefacedropdown.js` | the swatch preview tuples and the picker behavior |
| `zicato/src/zicato/dashboard/static/fonts/` | the woff2 files of the two monos the viewer self-hosts beside Inconsolata |
| `diastil/docs/HOUSE-STYLE.md` | the drawing register for figures |

## Colour

Every colour is a role token. No mark, panel, or text sets a hue directly.
A theme is a set of tokens swapped by one attribute on the root element, so
changing theme is a re-skin with no re-render.

| token | role |
|---|---|
| `--v2-paper` | the ground; everything sits on it |
| `--v2-panel` | a surface one step lifted off the ground |
| `--v2-ink`, `--v2-ink-soft`, `--v2-ink-faint` | text and neutral strokes in three weights |
| `--v2-rule`, `--v2-rule-soft` | separators and outlines |
| `--v2-accent` | the one structural or interactive emphasis |
| `--v2-good`, `--v2-good-soft` | the better outcome and its tinted fill |
| `--v2-bad`, `--v2-bad-soft` | the worse outcome and its tinted fill |
| `--v2-caution` | a limit reached |
| `--v2-flat` | unchanged or recognized |

Three rules govern their use.

- `good` and `bad` are earned by direction and never by identity. A child
  episode is not coloured for being a child. An outcome is `good` when it is
  `completed`, `bad` when it is `failed`, `caution` when it is `exhausted`,
  and `flat` when it is `blocked`, because `blocked` is a recognized state
  rather than a failure. A running episode is neutral.
- The accent appears once per figure, on the element that carries the
  meaning. A selected episode, a current step, or a focused control takes
  it. Nothing decorative does.
- Panels are bounded by `--v2-rule`. No panel carries an accent-coloured
  left rail or border; the accent is reserved for signal.

The sixteen themes are copied from `console.css` without modification:
`monokai`, `solarized-dark`, `solarized-light`, `google-light`,
`google-dark`, `lunaria-light`, `lunaria-eclipse`, `belafonte-day`,
`belafonte-night`, `paper`, `zenburn`, `selenized-black`, `relaxed`,
`espresso`, `dracula`, `ubuntu`. Each is scoped as
`:root[data-theme="<id>"]`. The swatch dropdown shows a six-chip preview per
theme in the order ground, surface, ink, improve, regress, accent, using the
preview tuples from `ui.js`, including the substituted preview accent for
`lunaria-eclipse`.

The default is `google-light` on a machine asking for a light ground and
`google-dark` on one asking for a dark ground, decided by
`prefers-color-scheme`. The two are the light and the dark ground of one
palette, so a reader on either sees the same design. A stored choice under
`foe.theme` wins over both, and a theme the host page stamped on the root
wins over the stored one. `tokens.css` repeats the two palettes on a root
carrying no theme, so the first paint already matches the theme the bundle
then applies.

## Type

Typography is a separate axis from colour. A typeface is selected by
`[data-typeface]` on the root and resolves four tokens: `--v2-sans` for body
text, `--v2-mono` for data, labels, axis text, and code, `--n-font-head` for
headings, and `--n-font-paper` for long prose. Twelve typefaces are grouped
into three modes of four, and the bare mode name on the root selects that
mode's first face.

| mode | first face | body | data | headings |
|---|---|---|---|---|
| `technical` | Inconsolata (the default) | Inconsolata | Inconsolata | Inconsolata |
| `editorial` | Source Serif | Source Serif 4 | Source Serif 4 | Source Serif 4 |
| `display` | Space Grotesk | Space Grotesk | JetBrains Mono | Archivo Narrow |

An option is named by the families it sets. One family in every role gives a
name of one family, as `inconsolata` does. Two families give both names
joined by a plus sign, as `ia writer + jetbrains` does. The one option that
sets three names the two that carry the most text, its body face and its
data face. Each family is named as short as it stays unambiguous among the
twelve, so no name repeats a word like Mono that every technical face
carries.

The typeface picker is a trigger over a grouped popover. The trigger is a
micro-specimen setting `Aa` in the option's body face beside `01` in its
data face, then the option's first family, then a caret; the two faces are
set inline, so the specimen shows the option it names whatever the page is
currently set in. The popover is three mode headers, each over four faces,
twelve in total, plus an S, M, L size control whose three controls are each
set at the size they select. Every option in the popover names itself in
full, in its own face, over one specimen line in the face that mode uses for
its content. A technical face is a face for reading code, so its specimen is
a line of code, `let outcome = episode.run();`; an editorial or display face
sets a sentence, `One bounded release of work.` No specimen is a run of
digits or a placeholder word.

Six woff2 files are self-hosted: both weights of Inconsolata, of iA Writer
Mono, and of JetBrains Mono, which are the families the default face and
the other technical and display faces set. `view/fonts/README.md` records
where each file came from. The viewer performs no network fetch for any
font: every other family resolves against the machine's own copy when it has
one and to a system fallback otherwise. This is the one departure from
zicato, which loads those families from a font service, and it exists
because the viewer runs on loopback and in environments with no network.

The brand wordmark is drawn from outlined paths inside the lockup, so it
does not follow the chosen typeface and never reflows. `--v2-brand-mono`
sets any place that spells the product name as text.

The brand mark, the wordmark's construction, and the brand accent are
specified in [brand/README.md](brand/README.md). The brand accent is a
separate token from `--v2-accent` and appears on the mark's peak dot only.

Base size is `13px` scaled by the page-scale pill. Numbers in columns or
that animate use `font-variant-numeric: tabular-nums`.

## Spacing

One rhythm governs every gap, and `tokens.css` states it once. The names and
the values are zicato's, from the section of its design language that bakes
a single scale onto the root in place of a density control; the page-scale
pill is the sizing control.

| token | value | role |
|---|---|---|
| `--dt-rail` | `288px` | the sidebar's width, which a grip then resizes |
| `--dt-pad-x`, `--dt-pad-y` | `56px`, `40px` | padding around a detail surface |
| `--dt-section-gap` | `30px` | between two sections |
| `--dt-panel-pad-x`, `--dt-panel-pad-y` | `19px`, `17px` | a panel's inner padding |
| `--dt-row-gap` | `30px` | between two items of a row |
| `--dt-card-min`, `--dt-card-gap`, `--dt-card-pad` | `270px`, `18px`, `16px` | a card grid's column, gap, and inner padding |

zicato's scale measures a scrolling document and its smallest step is
sixteen pixels. The viewer's regions are panes a few hundred pixels wide
whose interiors need steps under that, so four more tokens name them, and
every interior spacing in the stylesheet is one of the four.

| token | value | role |
|---|---|---|
| `--dt-gap-line` | `3px` | between two lines of one row |
| `--dt-gap-item` | `6px` | between two controls, marks, or words set side by side |
| `--dt-gap-edge` | `10px` | between a pane's edge and what it holds |
| `--dt-gap-group` | `14px` | between two groups inside one pane |

Radii are tokens on the same terms: `--dt-radius-panel` at `4px`,
`--dt-radius-card` at `5px`, `--dt-radius-mark` at `3px` for a bar in a
figure, `--dt-radius-hovercard` at `6px`, and `--dt-radius-pill` full-round.
A hairline is always `1px solid var(--v2-rule)`, or `--v2-rule-soft` for a
fainter inner rule.

No panel scrolls sideways and no child escapes one. A two-column layout of
labels and values that cannot hold both at its narrow end stacks the value
under its label rather than breaking a word across two lines: the details
pane is a query container and its rows answer to the pane's own width, so a
grip that narrows the pane re-lays the rows out.

## Chrome

The top bar is sticky, blurred, and hairline-bottomed. Left to right:

1. an up control that moves one level up the episode tree;
2. the brand: the lockup from [brand/README.md](brand/README.md), a variant
   tag reading `viewer`, and the research-preview tag;
3. breadcrumbs in mono and faint ink, from the root episode to the
   selected one;
4. a flex spacer;
5. the colour swatch dropdown and the typeface switch;
6. the page-scale pill: a range input from 70% to 150% in 5% steps with a
   percent readout and a reset control, applied by `zoom` on the root so the
   page reflows rather than scales;
7. the status pill: a dot and a word for the connection state, and a run
   badge with a pulse when an episode is in flight. The pulse is the only
   keyframe animation in the chrome and is disabled under
   `prefers-reduced-motion`.

The lockup is sized by height, so that the wordmark's ascenders match the
cap height of the surrounding chrome text. It strokes in `currentColor` and
fills its core with `--foe-accent`, which is a token separate from
`--v2-accent` and takes `#C7791A` on a light ground and `#E8A43E` on a dark
one. Those two values and the theme blocks are the only raw colours in the
stylesheet.

The research-preview tag states that the product is a research preview. It
is two stacked words, `research` over `preview`, set in mono at 9.5 pixels,
uppercase, with letter-spacing of 0.1em, in `--v2-ink-faint`, behind a
one-pixel left border in `--v2-rule` with 7 pixels of padding. It takes no
pointer events and no selection and carries `role="note"`, because it
states a fact and is never a control.

### Grips

Every divider between two regions is a grip. A grip is a hairline in
`--v2-rule` with a rounded 28-by-4 pill centred on it in `--v2-ink-faint`,
which takes `--v2-accent` on hover and while dragging. Its cursor is
`col-resize` for a vertical divider and `row-resize` for a horizontal one.

A grip is focusable and carries `role="separator"` with the current size as
`aria-valuenow`. The arrow keys move it 16 pixels, Home and End take it to
its two limits, and a double click returns it to whatever derives it. A drag
takes pointer capture and applies at most one size per animation frame. Each
region declares a minimum, so no region collapses.

A region whose height follows its content opens at that height and keeps
following it until a grip sets a size. Only sizes a grip has set are stored,
so a region left alone stays derived.

Theme, typeface, font size, page scale, and pane sizes persist in
`localStorage` under `foe.theme`, `foe.typeface`, `foe.fontsize`,
`foe.scale`, and `foe.panes`, and one function applies each so that every
control that changes a value stays in step.

## Figures

Figures are line art in the diastil drawing register.

- Hairline strokes between 0.9 and 1.6 pixels. Structure in
  `--v2-ink-faint`. Depth by layered opacity rather than by shading or
  gradient.
- Every stroked element carries `vector-effect: non-scaling-stroke`, so a
  hairline stays one whatever scales the figure around it. Without it a
  figure drawn at one width and shown at another thickens every line with
  the drawing, and the register the figures share comes apart exactly when
  a reader leans in.
- Dashed strokes for envelopes and guides. In foe, a fork edge is dashed and
  a spawn edge is solid, because a fork is a counterfactual sibling and a
  spawn is a contributing child.
- A bar is `rx: 3`, filled with a token at reduced opacity, and lifts to
  full opacity while the pointer is on it. A hovercard names every mark.
- A figure whose marks nest separates them by position rather than by
  drawing more of them. A lane is a band of one row that holds one channel,
  and the order of the lanes down a row is the order of containment. Two
  marks that fall on one position take successive heights of their lane
  instead of overprinting; the height is a tie-break and carries no
  quantity, so the marks keep the position they were measured at.
- One accented element per figure, the one that is the argument. Where the
  argument is a row of a list, the accent is a spine: a line of 2.0 to 2.4
  pixels down the row's leading edge. A filled row would be the loudest
  element on a page that is otherwise hairlines, and in a figure whose rows
  hold bars a fill competes with the bars inside it.
- A state a row carries is drawn as a mark. A word inside a bordered
  rectangle puts a border and a fill on a page whose every other element is
  a hairline, and it says less than a mark of the same size. The order of
  preference is a drawn mark first, plain text in faint mono second, and a
  bordered rectangle only where neither serves. A quantity stays text,
  because a reader wants the number. A value drawn from a set of more than
  about three stays text, because an alphabet that large is learned rather
  than read. A control a person presses keeps its border, because a border
  is how a control states that it is pressable.
- A mark reuses the shape the trajectory gives the same meaning, so one
  grammar covers the timeline and the conversation. `src/marks.ts` holds
  every mark as geometry in one box, and the stylesheet gives each one its
  role colour; no mark names a colour of its own. A mark stands `1.2em`
  tall in the line it joins and strokes `currentColor` at 1.2 pixels, which
  does not scale with the box. A ring must keep a hole of at least three
  device pixels at the lowest page scale, because a smaller hole fills in
  and the ring reads as a dot. The word a mark replaces is its accessible
  name and the heading of the hovercard it opens.
- Small mono labels with leader ticks. A legend only when two channels
  appear. A caption that states what to see.
- A gridline only where a length is read off the figure. A figure whose
  marks are placed rather than measured carries none. Where gridlines
  appear they are `0.6` pixels in `--v2-rule-soft`, one per axis tick.
- No chart frames and no three-dimensional effects.

### The hovercard

One card explains every mark of every figure, and `src/render/hovercard.ts`
is the only place it is built. A figure that appended a tooltip of the
browser's own would give a reader an unthemed box after a delay in the one
place the page most needs to answer at once, so no figure does.

The card is three lines: the name of the mark, one line of context, and one
line of detail. It opens on the pointer entering a mark and closes on the
pointer leaving it. It stands below the pointer, or above it when it would
otherwise leave its host at the bottom, and it never crosses the host's
right edge. It takes no pointer events, so it never stands between the
pointer and the mark under it.

A quantity a reader could not derive by eye names where it came from. A
figure that draws one interval and reports another gives both, because a
reader who sees one number and a bar of a different length has no way to
tell which is the measurement. A quantity nothing measured is stated as
absent; it is never drawn as zero.

Every figure is one SVG element built by `src/render/svg.ts`, which fits it
to its host: `width="100%"`, an explicit `viewBox`, an explicit
`preserveAspectRatio`, and `role="img"` with a label. No figure has a fixed
pixel width that could exceed its pane, and no pane scrolls sideways to
reach one. The viewer does not pan or zoom a figure; it reflows it and
redraws.

Two shapes differ in what happens between a resize and the redraw that
follows it. A drawing whose marks must keep their shape scales uniformly and
anchors at its top left, so a dot stays round. A bar whose only mark is a
rectangle stretches instead, because a bar carries its meaning in its length
alone. A stretching figure that needs a round glyph puts that glyph in a
separate 1:1 overlay.

## Render discipline

The viewer redraws a region only when its content changed. A live event
that changes one episode patches that episode's rows and leaves scroll
position, selection, and expander state untouched. A heartbeat with no new
events redraws nothing.

## Voice

Interface text is declarative and short. A count accompanies every rate. A
state that was not observed is shown as absent rather than as zero.

## Departures

The viewer follows the sources named at the top of this document except in
the four places below, each of which exists because the viewer is a pane
layout over an append-only log rather than a scrolling dashboard.

- **No font is fetched.** zicato loads its editorial and display families
  from a font service. The viewer runs on loopback and from a single file,
  so it self-hosts the six faces it guarantees and lets every other family
  resolve against the machine's own copy or a system fallback.
- **The interior rhythm has four steps zicato does not name.** zicato's
  scale is adopted whole, and the four tokens under the "Spacing" heading
  are added below its smallest step for the interiors of panes.
- **Options are named rather than indexed.** zicato labels its typefaces
  with identifiers whose meaning lives in that project. The viewer names
  each option by the families it sets, because a name in this repository
  says what the thing is.
- **A picker's trigger names one family.** zicato's trigger carries the
  option's full label. The viewer's carries a micro-specimen and the
  option's first family, so that the control in the top bar stays no wider
  than the colour picker beside it; the popover carries the full name.

## Size

The chrome adds to the bundle. The JavaScript and CSS together stay under
150 KB compressed. The six self-hosted font files are separate assets,
served by the live server and inlined into the static export, and are not
counted against that budget. The static export is therefore larger than the
bundle by the size of the fonts, and that is accepted.
