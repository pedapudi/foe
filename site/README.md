# The landing page

`site/public` is the page GitHub Pages serves. `site/build` produces it.

```sh
python3 site/build/build.py
```

The build reads the repository rather than restating it: the brand lockup and
the favicon come from `docs/brand`, the colour tokens from two of the themes
in `view/src/tokens.css` along with the eight colours that name an agent, and
every log line, event row, figure and number on
the page from the fixtures in `view/fixtures`. The declared graphs come from
`crates/cli/src/builtin-coding.json` and
`examples/self-extension/workflow-config.json`, and the team from
`examples/team/config.json`. Nothing on the page is invented, and a change to
one of those sources changes the page.

## What the build writes

| file | what it is |
|---|---|
| `public/index.html` | the whole page: markup, stylesheet and scripts in one file |
| `public/artifact.html` | the same page as one file, with the fonts inline and no document skeleton |
| `public/favicon.svg` | the brand mark, drawn in the brand accent |
| `public/*.woff2` | the typeface the page sets, copied from `view/fonts` |

Two files in `public` are sources that the build leaves untouched.
`public/install.sh` is the installer that the page's install command fetches,
and [docs/build.md](../docs/build.md) "Install" describes it. `public/CNAME`
names the custom domain.

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
fetch. `view/fonts` carries Inconsolata alone, so a build that selects
`technical-ia-writer` fails until its font files are added there. Every
self-hosted face draws the box-drawing characters the transcripts
use at the same advance as its letters, so the connector column stays aligned.

## Publishing

GitHub Pages serves the root of the `gh-pages` branch. `site/publish.sh` runs
the build, then copies `index.html`, `favicon.svg`, `install.sh`, `CNAME`, and
the font files from `site/public` to that root and pushes the branch. No
workflow builds or deploys the page; a person runs the script.
