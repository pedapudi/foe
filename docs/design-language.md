# Design language

foe's viewer follows the zicato design language in full, including its
chrome. This document states what that means for foe and where the
canonical definitions live. Where this document and those sources disagree,
the sources are authoritative.

| source | defines |
|---|---|
| `zicato/docs/design/DESIGN-LANGUAGE.md` | the colour role contract, the sixteen themes, the three typeface modes, the top bar, the pickers, the line-art figure conventions, the render discipline |
| `zicato/src/zicato/dashboard/static/css/console.css` | the authoritative per-theme token blocks and the typeface token map; the only place raw hex appears |
| `zicato/src/zicato/dashboard/static/js/ui.js` and `typefacedropdown.js` | the swatch preview tuples and the picker behavior |
| `zicato/src/zicato/dashboard/static/fonts/` | the self-hosted woff2 files for the default typeface mode |
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
`monokai` (default), `solarized-dark`, `solarized-light`, `google-light`,
`google-dark`, `lunaria-light`, `lunaria-eclipse`, `belafonte-day`,
`belafonte-night`, `paper`, `zenburn`, `selenized-black`, `relaxed`,
`espresso`, `dracula`, `ubuntu`. Each is scoped as
`:root[data-theme="<id>"]`. The swatch dropdown shows a six-chip preview per
theme in the order ground, surface, ink, improve, regress, accent, using the
preview tuples from `ui.js`, including the substituted preview accent for
`lunaria-eclipse`.

## Type

Typography is a separate axis from colour. A typeface mode is selected by
`[data-typeface]` on the root and resolves four tokens: `--v2-sans` for body
text, `--v2-mono` for data, labels, axis text, and code, `--n-font-head` for
headings, and `--n-font-paper` for long prose.

| mode | body | data | headings |
|---|---|---|---|
| `technical` (default) | iA Writer Mono | JetBrains Mono | iA Writer Mono |
| `editorial` | Source Serif 4 | Source Serif 4 | Source Serif 4 |
| `display` | Space Grotesk | JetBrains Mono | Archivo Narrow |

The typeface picker is a grouped popover: three mode headers, each over four
faces, twelve in total, plus an S, M, L size control whose three controls
are each set at the size they select. Every option names itself in its own
face, over one specimen line in the face that mode uses for its content. A
technical face is a face for reading code, so its specimen is a line of
code, `let outcome = episode.run();`; an editorial or display face sets a
sentence, `One bounded release of work.` No specimen is a run of digits or
a placeholder word.

The default mode's two monos are self-hosted as woff2 files copied from
zicato. The viewer performs no network fetch for any font: the editorial and
display families resolve to their listed stacks when the machine has them
and to system fallbacks otherwise. This is the one departure from zicato,
which loads those families from a font service, and it exists because the
viewer runs on loopback and in environments with no network.

The brand wordmark is drawn from outlined paths inside the lockup, so it
does not follow the chosen typeface and never reflows. `--v2-brand-mono`
sets any place that spells the product name as text.

The brand mark, the wordmark's construction, and the brand accent are
specified in [brand/README.md](brand/README.md). The brand accent is a
separate token from `--v2-accent` and appears on the mark's peak dot only.

Base size is `13px` scaled by the page-scale pill. Numbers in columns or
that animate use `font-variant-numeric: tabular-nums`.

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
its two limits, and a double click returns it to its default. A drag takes
pointer capture and applies at most one size per animation frame. Each
region declares a minimum, so no region collapses.

Theme, typeface, font size, page scale, and pane sizes persist in
`localStorage` under `foe.theme`, `foe.typeface`, `foe.fontsize`,
`foe.scale`, and `foe.panes`, and one function applies each so that every
control that changes a value stays in step.

## Figures

Figures are line art in the diastil drawing register.

- Hairline strokes between 0.9 and 1.6 pixels. Structure in
  `--v2-ink-faint`. Depth by layered opacity rather than by shading or
  gradient.
- Dashed strokes for envelopes and guides. In foe, a fork edge is dashed and
  a spawn edge is solid, because a fork is a counterfactual sibling and a
  spawn is a contributing child.
- One accented element per figure, the one that is the argument.
- Small mono labels with leader ticks. A legend only when two channels
  appear. A caption that states what to see.
- No gridlines for their own sake, no chart frames, no three-dimensional
  effects.

Figures fit their pane. The viewer does not pan or zoom a figure; it
reflows it.

## Render discipline

The viewer redraws a region only when its content changed. A live event
that changes one episode patches that episode's rows and leaves scroll
position, selection, and expander state untouched. A heartbeat with no new
events redraws nothing.

## Voice

Interface text is declarative and short. A count accompanies every rate. A
state that was not observed is shown as absent rather than as zero.

## Size

The chrome adds to the bundle. The JavaScript and CSS together stay under
150 KB compressed. The two self-hosted font files are separate assets,
served by the live server and inlined into the static export, and are not
counted against that budget. The static export is therefore larger than the
bundle by the size of the fonts, and that is accepted.
