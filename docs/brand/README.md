# foe — brand

> A point, an expanding shell, six spikes of light, and a dashed limit the
> burst never crosses.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="foe-lockup-dark.svg">
  <img alt="foe" src="foe-lockup-light.svg" width="420">
</picture>

## The name

**foe**, lowercase, pronounced as the English word. A foe is 10<sup>51</sup>
ergs, the unit astronomers use for the energy a core-collapse supernova
releases: fifty-one ergs, abbreviated. One star, one event, one bounded
release of everything it has. An episode of foe is the same shape: one task,
one run, one outcome, and then it is over.

The second reading, the friendly coding agent, is the one a person hears
first, and it is welcome. The etymology is the unit.

The name is always lowercase. In code, paths, identifiers, and shell
examples it is `foe`. The command is `foe`. The package is `foe`.

## The mark

The mark is a supernova as a telescope records one: a bright point, the
shell of ejecta expanding around it, and the six diffraction spikes that a
telescope's support struts draw across every bright star. It is built from
the center outward, and every element outward of the core is fainter than
the one inside it.

1. **The core.** A filled dot at the center, the one colored element. It is
   the event: the task, the single release.
2. **The spikes.** Six long rays at sixty-degree intervals, the diffraction
   pattern of a bright point. Between them, six shorter rays at half weight,
   and twelve hair-fine rays between those. The burst is dense at the core
   and thins outward.
3. **The shell.** Two thin rings, the outer fainter than the inner: the
   ejecta expanding, and the record that persists after the burst.
4. **The limit.** A dashed circle outside everything. The longest spike
   stops short of it. This is the declared budget and permissions: the bound
   inside which everything the episode does stays.

The argument of the mark is the gap between the longest spike and the
dashed circle. Remove the circle and the burst is any star; with it, the
burst is a bounded one.

The composition is radial, and that is what separates it from its siblings
at a glance: zicato's mark is one horizontal stroke, diastil's is three
lines converging on a drop.

### Geometry

The mark lives in `viewBox="0 0 120 120"`, centered at `(60, 60)`.

| element | radius | stroke | opacity |
|---|---|---|---|
| core dot | 5 | filled, accent | 1.0 |
| six spikes, at 90° + k·60° | from 11 to 46 | 1.8 | 1.0 |
| six rays between them | from 11 to 30 | 1.2 | 0.6 |
| twelve hair rays between those | from 11 to 22 | 0.9 | 0.35 |
| inner shell | 26 | 1.2 | 0.42 |
| outer shell | 38 | 1.0 | 0.28 |
| limit | 52 | 1.4, dashed `4 5` | 0.55 |

All strokes have round caps. The rays begin at radius 11 so that the core
sits in clear space. The favicon keeps the core, the six spikes at heavier
weight, and a solid limit ring, and drops everything that would vanish at
sixteen pixels.

## The wordmark

`foe` in DejaVu Sans Mono, lowercase, x-height 44 units on the lockup's
baseline at `y = 84`, beginning at `x = 140`, so that the word's optical
center sits level with the mark's. The glyphs are embedded as outlined
paths, so the lockup renders identically on a machine with no fonts
installed. In a running interface, the wordmark sets in the brand mono
token, which is fixed and does not follow the user's typeface choice, so the
mark never reflows.

## Color

The mark carries no hard-coded ink. Every adaptive asset strokes with
`currentColor` and fills the core with `var(--foe-accent)`, so the mark is
dark on a light ground and light on a dark one by following the text color
around it.

| token | light | dark | role |
|---|---|---|---|
| ink | `currentColor` | `currentColor` | spikes, rays, shells, limit, wordmark |
| `--foe-accent` | `#C7791A` | `#E8A43E` | the core, and nothing else |

The accent is amber: the color of a remnant's glow, and distinct from
zicato's green and diastil's blue so the three marks are told apart at a
glance. It appears on the core and nowhere else. Interface chrome uses the
theme's own `--v2-accent` for selection and focus; the brand accent belongs
to the mark alone.

## Assets

| file | use |
|---|---|
| `foe-lockup.svg` | adaptive mark and wordmark; inherits `currentColor` and `--foe-accent` |
| `foe-lockup-light.svg`, `foe-lockup-dark.svg` | the same with fixed colors, for places that cannot supply tokens, such as a README |
| `foe-wordmark.svg` | adaptive wordmark alone; inherits `currentColor` |
| `foe-wordmark-light.svg`, `foe-wordmark-dark.svg` | the same with fixed colors |
| `foe-mark.svg` | adaptive mark alone |
| `foe-mark-mono.svg` | mark alone with the core in ink, for single-color print |
| `foe-favicon.svg` | core, six spikes, and a solid ring in a 64-unit square |
| `foe-tile.svg` | the mark centered on the paper ground, for a square avatar |

The wordmark files carry the lockup's own glyphs at the lockup's own size,
cropped to the word with one core diameter of clear space on every side, so
the wordmark alone and the wordmark in the lockup are the same drawing. The
wordmark sets in a single color, so it needs no mono variant the way the mark
does.

The assets are generated from the geometry table by a short script rather
than drawn by hand, so a change to one radius regenerates every file.

## Rules

- Clear space around the lockup is one core diameter on every side.
- The mark is never stretched, outlined, filled, given a gradient, rotated,
  or animated, with the one exception below. The dashed limit is never
  removed.
- The accent appears on the core only. No second accent.
- The wordmark is never set in a proportional face and never capitalized.
- The mark may appear without the wordmark. The wordmark may appear without
  the mark. Neither is altered to fit.

One animation is allowed. A progress indicator in a terminal may pulse a
single-glyph text rendering of the mark through the eleven frames `·` `✶`
`✷` `✸` `⊛` `◎` `⊛` `✸` `✷` `✶` `·`, one frame per redraw, in the accent
color. The dot is the core, the stars are the spikes growing, `⊛` is the
shell closing around them, and `◎` at the peak is the whole mark. Every
surface that pulses the mark draws this sequence, so the pulse is the same
drawing wherever it appears. The drawn mark of the assets above never
moves.

## Voice

Lowercase, declarative, short. A claim comes with its number: a budget, a
test count, a byte count. The product's promise is a bounded run with a
complete record, and the voice never promises more than the log can show.
