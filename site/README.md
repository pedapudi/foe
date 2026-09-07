# The landing page

`site/public` is the page GitHub Pages serves. `site/build` produces it.

```sh
python3 site/build/build.py
```

The build reads the repository rather than restating it: the brand lockup and
the favicon come from `docs/brand`, the colour tokens from two of the themes
in `view/src/tokens.css`, and every log line, event row, figure and number on
the page from the fixtures in `view/fixtures`. Nothing on the page is invented,
and a change to a fixture changes the page.

## What the build writes

| file | what it is |
|---|---|
| `public/index.html` | the whole page: markup, stylesheet and scripts in one file |
| `public/favicon.svg` | the brand mark, drawn in the brand accent |
| `public/*.woff2` | the typeface the page sets, copied from `view/fonts` |
| `public/install.sh` | a copy of the repository's installer, so `foe.sh/install.sh` resolves |

The page fetches nothing. Every style, script, font and image is either inline
or a file beside it, so it renders the same offline as on the network.

## Choosing the palette and the typeface

Both are named at build time and read out of `view/src/tokens.css`, so the page
and the viewer cannot drift apart.

```sh
FOE_LIGHT=paper FOE_DARK=espresso python3 site/build/build.py
FOE_TYPEFACE=technical-ia-writer python3 site/build/build.py
```

The palette defaults to `paper` on a light ground and `espresso` on a dark one.
The typeface defaults to `technical-inconsolata`, which sets Inconsolata for
prose, data and code alike. `technical-ia-writer` is the other one the build
knows: iA Writer Mono for prose and JetBrains Mono for data and code. Only the
faces `view/fonts` carries can be chosen, because the page performs no network
fetch, and every one of them draws the box-drawing characters the transcripts
use at the same advance as its letters, so the connector column stays aligned.

## Publishing

`site/public` is the published tree. GitHub Pages serves it from the branch and
folder named in the repository's Pages settings. No workflow builds or deploys
it; a person runs the build and pushes the result.

Run `site/publish.sh` to build the page and push the result to the `gh-pages`
branch, which is the tree Pages serves.
