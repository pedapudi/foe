# Design language

foe's visible surfaces, which are the viewer and any figure in its
documents, follow the zicato design language and the diastil house style.
This document states what that means for foe and where the canonical
definitions live. Where this document and those sources disagree, the
sources are authoritative.

| source | defines |
|---|---|
| `zicato/docs/design/DESIGN-LANGUAGE.md` | the colour role contract, the typeface registers, the line-art figure conventions, the render discipline |
| `diastil/docs/HOUSE-STYLE.md` | the light-first default, the prescribed faces, the drawing register |
| `diastil/docs/BRAND.md` | one accent spent on signal; good and bad earned by direction |
| `diastil/src/chrome/tokens.css` | the complete token sheet for every theme; the only place raw hex appears |

## Colour

Every colour is a role token. No mark, panel, or text sets a hue directly.
A theme is a set of tokens swapped by one attribute on the root element, so
changing theme is a re-skin with no re-render.

| token | role |
|---|---|
| `--paper` | the ground; everything sits on it |
| `--panel` | a surface one step lifted off the ground |
| `--ink`, `--ink-soft`, `--ink-faint` | text and neutral strokes in three weights |
| `--rule`, `--rule-soft` | separators and outlines |
| `--accent` | the one structural or interactive emphasis |
| `--good`, `--bad` | the better and the worse outcome |
| `--caution` | a limit reached |
| `--flat` | unchanged |

Three rules govern their use.

- `good` and `bad` are earned by direction and never by identity. A child
  episode is not coloured for being a child. An outcome is `good` when it is
  `completed`, `bad` when it is `failed`, `caution` when it is `exhausted`,
  and `flat` when it is `blocked`, because `blocked` is a recognized state
  rather than a failure. A running episode is neutral.
- The accent appears once per figure, on the element that carries the
  meaning. A selected episode, a current step, or a focused control takes
  it. Nothing decorative does.
- Panels are bounded by `--rule`. No panel carries an accent-coloured left
  rail or border; the accent is reserved for signal.

The default theme is `paper`, light, from the diastil house style. The
default dark theme is `monokai`. Both token blocks are copied from
`diastil/src/chrome/tokens.css` without modification, and the viewer
follows `prefers-color-scheme` to choose between them.

## Type

Two faces, each with a fixed job.

| face | job |
|---|---|
| `"Source Sans 3", system-ui, sans-serif` | prose and controls |
| `"Source Code Pro", ui-monospace, monospace` | data: paths, identifiers, numbers, event types, code, keyboard keys |

The viewer embeds no font files and performs no font fetch; the stacks fall
back to system faces. Numbers set in the mono face use tabular figures.

## Figures

Figures are line art in the diastil drawing register.

- Hairline strokes between 0.9 and 1.6 pixels. Structure in `--ink-faint`.
  Depth by layered opacity rather than by shading or gradient.
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
