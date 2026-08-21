# Self-hosted fonts

The viewer fetches no font over the network. It runs on loopback and from a
single file, so a font service is unreachable in the environments it is
built for. Every face the viewer can guarantee is therefore a woff2 file in
this directory: the live server answers `GET /fonts/<name>.woff2` from the
copy embedded in the binary, and the static export inlines the same bytes as
a `data:` URI. `crates/view/build.rs` names the files it embeds; a file
named there and absent here is left out, and the stylesheet's font stack
falls back to the next family.

The other nine faces the typeface picker offers resolve to the machine's own
copy when it has one and to a system fallback otherwise.
`docs/design-language.md` states that rule.

| file | family | weight |
|---|---|---|
| `Inconsolata-Regular.woff2` | Inconsolata | 400 |
| `Inconsolata-Bold.woff2` | Inconsolata | 700 |
| `iAWriterMonoS-Regular.woff2` | iA Writer Mono | 400 |
| `iAWriterMonoS-Bold.woff2` | iA Writer Mono | 700 |
| `JetBrainsMono-Regular.woff2` | JetBrains Mono | 400 |
| `JetBrainsMono-Bold.woff2` | JetBrains Mono | 700 |

The iA Writer Mono and JetBrains Mono files are copied unchanged from
zicato's `src/zicato/dashboard/static/fonts/`.

## Inconsolata

Inconsolata is the family the default typeface mode sets, so both of its
weights are self-hosted.

The two woff2 files were converted from TrueType originals of Inconsolata
version 3.000, whose `name` table records the following.

```
Copyright 2006 The Inconsolata Project Authors (https://github.com/cyrealtype/Inconsolata)
Version 3.000; ttfautohint (v1.8.3)
This Font Software is licensed under the SIL Open Font License, Version 1.1. This license is available with a FAQ at: http://scripts.sil.org/OFL
http://scripts.sil.org/OFL
```

Those four lines are name IDs 0, 5, 13, and 14 of the Windows-Unicode
records, read out of the file itself. Name ID 13 is the licence statement
the SIL Open Font License requires a font to carry; the full licence text is
not in the file, and the URL in name ID 14 is where the font's authors
publish it.

The originals are the two files:

| file | SHA-256 | bytes |
|---|---|---|
| `Inconsolata-Regular.ttf` | `127875d255d4c5973ca57267a43bb9d1c04397e6c7d236984a595b6cdcb12b7c` | 108,684 |
| `Inconsolata-Bold.ttf` | `263faa57f6c00c43a04e77df7abd5cb5cd4aae9f93507002c1217e02641fc7e6` | 109,728 |

The conversion changes the container and nothing else: every table is
carried over, and no glyph is dropped or subset. It was performed with
fontTools 4.63.0 and the Brotli bindings it uses for woff2, both installed
into a virtual environment, by setting the font's flavor and saving it:

```python
from fontTools.ttLib import TTFont

for style in ("Regular", "Bold"):
    font = TTFont(f"Inconsolata-{style}.ttf")
    font.flavor = "woff2"
    font.save(f"Inconsolata-{style}.woff2")
```

Repeating those steps on the two originals reproduces the two woff2 files.
