# foe — brand

> One light curve: quiet, a steep rise to a single peak, a long decay back to
> the baseline, and an end. A dashed limit above that the peak never crosses.

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

The mark is a supernova light curve, the plot of brightness against time by
which astronomers recognize the event. It is drawn as one continuous stroke
with one accent.

1. **The baseline.** A short flat run at the left: nothing has happened yet.
2. **The rise.** A steep climb to the peak, over a short span: the episode
   starts and reaches full work within a few steps.
3. **The peak.** The single accent dot sits at the maximum. It is the only
   colored element and it marks the one event the mark is about.
4. **The decay.** A long curve back toward the baseline, steep at first and
   flattening: the work settles and the record persists after the burst.
5. **The end.** A vertical tick where the curve meets the baseline again:
   the episode ends, and it does not resume.
6. **The limit.** A dashed hairline above the whole curve. The peak rises
   toward it and never crosses it. This is the declared budget and
   authority: the bound inside which everything the episode does stays.

The argument of the mark is the relationship between the peak and the
limit. Remove the dashed line and the curve is any burst; with it, the curve
is a bounded one.

### Geometry

The mark lives in the coordinate space `viewBox="14 8 262 94"`; the lockup
extends it to `14 8 410 94`. The curve is
`M28,80 L62,80 C70,80 74,30 80,26 C92,26 104,48 130,62 C160,74 200,79 236,80 L262,80`,
the end tick `M262,68 L262,92`, and the limit `M28,18 L262,18`. The accent
dot is at the peak `(80, 26)`.

Stroke width is `5` with round caps and joins, the same weight zicato's
mark uses, so the two read as kin when they appear together. The accent dot
has radius `5.5`. The limit is drawn at width `2.4`, dashed `6 7`, at 55%
opacity, so it reads as structure rather than as a second stroke.

## The wordmark

`foe` in DejaVu Sans Mono, lowercase, x-height 29 units on the lockup's
baseline at `y = 92`, beginning at `x = 318`, one em of clear space after
the end tick. The glyphs are embedded as outlined paths, so the lockup
renders identically on a machine with no fonts installed. In a running
interface, the wordmark sets in the brand mono token, which is fixed and
does not follow the user's typeface choice, so the mark never reflows.

## Color

The mark carries no hard-coded ink. Every adaptive asset strokes with
`currentColor` and fills the dot with `var(--foe-accent)`, so the mark is
black on a light ground and light on a dark one by following the text color
around it.

| token | light | dark | role |
|---|---|---|---|
| ink | `currentColor` | `currentColor` | the curve, the tick, the limit, the wordmark |
| `--foe-accent` | `#C7791A` | `#E8A43E` | the peak dot, and nothing else |

The accent is amber: the color of a remnant glow, and distinct from zicato's
green and diastil's blue so the three marks are told apart at a glance. It
appears on the peak and nowhere else. Interface chrome uses the theme's own
`--v2-accent` for selection and focus; the brand accent belongs to the mark
alone.

## Assets

| file | use |
|---|---|
| `foe-lockup.svg` | adaptive mark and wordmark; inherits `currentColor` and `--foe-accent` |
| `foe-lockup-light.svg`, `foe-lockup-dark.svg` | the same with fixed colors, for places that cannot supply tokens, such as a README |
| `foe-mark.svg` | adaptive mark alone |
| `foe-mark-mono.svg` | mark alone with the dot in ink, for single-color print |
| `foe-favicon.svg` | the curve and dot reduced to a 64-unit square |
| `foe-tile.svg` | the mark centered on the paper ground, for a square avatar |

## Rules

- Clear space around the lockup is one dot diameter on every side.
- The mark is never stretched, outlined, filled, given a gradient, rotated,
  or animated. The dashed limit is never removed.
- The accent appears on the peak only. No second accent.
- The wordmark is never set in a proportional face and never capitalized.
- The mark may appear without the wordmark. The wordmark may appear without
  the mark. Neither is altered to fit.

## Voice

Lowercase, declarative, short. A claim comes with its number: a budget, a
test count, a byte count. The product's promise is a bounded run with a
complete record, and the voice never promises more than the log can show.
