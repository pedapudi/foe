# Assembles site-foe/index.html and site-foe/artifact.html from parts/ and the
# data the generators read out of the repository.
import base64, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "public")
REPO = os.path.dirname(os.path.dirname(HERE))    # the repository root

os.makedirs(OUT, exist_ok=True)
for script in ("gen.py", "term.py", "wf.py", "team.py"):
    subprocess.run([sys.executable, os.path.join(HERE, script)], check=True, capture_output=True)
# The observability section reads a second run: the declared graph and the
# three child episodes it fired, rather than the run the opening screen shows.
for script, name in (("gen.py", "run-wf"), ("term.py", "term-wf")):
    subprocess.run([sys.executable, os.path.join(HERE, script)], check=True,
                   capture_output=True, env=dict(os.environ, FOE_RUN=name))

data = {name: open(os.path.join(HERE, name + ".json")).read()
        for name in ("run", "term", "wf", "team", "run-wf", "term-wf")}

# The palette is two of the themes in foe's own view/src/tokens.css, taken
# token for token so the page and the viewer cannot drift apart. Name a
# different pair here to change the whole page.
LIGHT_THEME = os.environ.get("FOE_LIGHT", "paper")
DARK_THEME = os.environ.get("FOE_DARK", "espresso")
BRAND_ACCENT = {"light": "#C7791A", "dark": "#E8A43E"}


def palette(theme, ground, indent):
    css = open(os.path.join(REPO, "view/src/tokens.css")).read()
    # The light-ground themes also appear in a shared --foe-accent rule, so
    # the block this reads is the one that carries the role tokens.
    at, body = 0, ""
    while True:
        at = css.index(':root[data-theme="%s"] {' % theme, at) + 1
        body = css[at:css.index("}", at)]
        if "--v2-paper" in body:
            break
    tokens = re.findall(r"(--v2-[a-z-]+):\s*(#[0-9A-Fa-f]{6})", body)
    assert len(tokens) == 15, (theme, len(tokens))
    tokens.append(("--foe-accent", BRAND_ACCENT[ground]))
    lines, row = [], []
    for name, value in tokens:
        row.append("%s:%s;" % (name, value))
        if len(row) == 4:
            lines.append(indent + " ".join(row))
            row = []
    if row:
        lines.append(indent + " ".join(row))
    return "\n".join(lines)


# The tab icon is the brand mark, from docs/brand/foe-favicon.svg. The served
# page links the file beside it; the artifact copy carries it inline, since it
# travels as one file and fetches nothing.
favicon = open(os.path.join(REPO, "docs/brand/foe-favicon.svg")).read()
favicon = favicon.replace("currentColor", "#C7791A")
favicon_uri = ("data:image/svg+xml;base64,"
               + base64.b64encode(favicon.encode()).decode())
open(os.path.join(OUT, "favicon.svg"), "w").write(favicon)

lockup = open(os.path.join(REPO, "docs/brand/foe-lockup.svg")).read()
lockup = re.sub(r"\s*\n\s*", "", lockup).strip()
lockup = lockup.replace('<svg xmlns="http://www.w3.org/2000/svg" ', '<svg focusable="false" ')

# The typeface is one of foe's own, from the catalogue in view/src/tokens.css.
# Only the faces foe self-hosts can be used, because the page fetches nothing:
# technical-inconsolata sets one face everywhere; technical-ia-writer reads
# prose in iA Writer Mono and data in JetBrains Mono.
TYPEFACES = {
    # (stack, family, [(file, weight)], directory the file comes from)
    "technical-inconsolata": {
        "prose": ('"Inconsolata", ui-monospace, "SF Mono", Menlo, Consolas, monospace', "Inconsolata",
                  [("Inconsolata-Regular.woff2", "400"), ("Inconsolata-Bold.woff2", "700")],
                  os.path.join(REPO, "view/fonts")),
        "code": ('"Inconsolata", ui-monospace, "SF Mono", Menlo, Consolas, monospace', None, [], None),
    },
    "technical-ia-writer": {
        "prose": ('"iA Writer Mono", "iA Writer Mono S", ui-monospace, Menlo, monospace', "iA Writer Mono",
                  [("iAWriterMonoS-Regular.woff2", "400"), ("iAWriterMonoS-Bold.woff2", "700")],
                  os.path.join(REPO, "view/fonts")),
        "code": ('"JetBrains Mono", ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace', "JetBrains Mono",
                 [("JetBrainsMono-Regular.woff2", "400"), ("JetBrainsMono-Bold.woff2", "700")],
                 os.path.join(REPO, "view/fonts")),
    },
}
TYPEFACE = TYPEFACES[os.environ.get("FOE_TYPEFACE", "technical-inconsolata")]

FACE = ('@font-face{font-family:"%s";src:url(%s) format("woff2");'
        'font-weight:%s;font-style:normal;font-display:swap}')


def font_files():
    """Every (file, weight, source directory) the chosen typeface ships."""
    for role in ("prose", "code"):
        stack, family, files, where = TYPEFACE[role]
        for name, weight in files:
            yield name, weight, family, where


def faces(source):
    return "\n".join(FACE % (family, source(name), weight)
                      for name, weight, family, _ in font_files())


# A face the build no longer ships would otherwise sit in the served tree
# for ever, so the fonts are the ones this typeface names and no others.
keep = {name for name, _, _, _ in font_files()}
for stale in os.listdir(OUT):
    if stale.endswith(".woff2") and stale not in keep:
        os.remove(os.path.join(OUT, stale))
for name, _, _, where in font_files():
    shutil.copyfile(os.path.join(where, name), os.path.join(OUT, name))


def datauri(name):
    """The font as one URI: the artifact travels as a single file."""
    raw = open(os.path.join(OUT, name), "rb").read()
    return "data:font/woff2;base64," + base64.b64encode(raw).decode()


parts = sorted(os.listdir(os.path.join(HERE, "parts")))
page = "".join(open(os.path.join(HERE, "parts", p)).read() for p in parts)

MARK = ('<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" fill="none" '
        'stroke="currentColor" stroke-width="1.3" stroke-linecap="round" '
        'stroke-linejoin="round">%s</svg>')
REPLAY = MARK % '<path d="M13.2 8a5.2 5.2 0 1 1-1.7-3.85"/><path d="M13.4 2.1v2.9h-2.9"/>'
COPY = MARK % ('<rect x="5.6" y="5.6" width="8" height="8" rx="1.4"/>'
               '<path d="M10.4 3.4a1.7 1.7 0 0 0-1.6-1.1H4.1A1.8 1.8 0 0 0 2.3 4v4.7c0 .8.5 1.4 1.2 1.6"/>')
SUN = MARK % ('<circle cx="8" cy="8" r="3.1"/>'
              '<path d="M8 1.4v1.5M8 13.1v1.5M1.4 8h1.5M13.1 8h1.5"/>'
              '<path d="M3.3 3.3l1.1 1.1M11.6 11.6l1.1 1.1M12.7 3.3l-1.1 1.1M4.4 11.6l-1.1 1.1"/>')
MOON = MARK % '<path d="M13.4 9.6A5.9 5.9 0 0 1 6.4 2.6a5.9 5.9 0 1 0 7 7z"/>'

page = page.replace("__LIGHT__", palette(LIGHT_THEME, "light", "  "))
page = page.replace("__DARKI__", palette(DARK_THEME, "dark", "    "))
page = page.replace("__DARK__", palette(DARK_THEME, "dark", "  "))
page = page.replace("__LOCKUP__", lockup)
assert "__REPLAY__" in page and "__COPY__" in page and "__SUN__" in page
page = (page.replace("__REPLAY__", REPLAY).replace("__COPY__", COPY)
            .replace("__SUN__", SUN).replace("__MOON__", MOON))
for name, text in data.items():
    token = "__%s__" % name.upper().replace("-", "")
    assert page.count(token) == 1, token
    page = page.replace(token, text)
for token in ("__LOCKUP__", "__RUN__", "__TERM__", "__WF__", "__TEAM__", "__RUNWF__", "__TERMWF__"):
    assert token not in page, token

local = faces(lambda n: '"%s"' % n)
inline = faces(lambda n: '"%s"' % datauri(n))
stacks = "  --prose:%s;\n  --code:%s;" % (TYPEFACE["prose"][0], TYPEFACE["code"][0])
page = page.replace("__FACES__", stacks)

index = page.replace("__FONTS__", local).replace("__FAVICON__", "favicon.svg")
open(os.path.join(OUT, "index.html"), "w").write(index)

# The artifact copy is one file with nothing beside it: the fonts travel inline
# and the document skeleton the host supplies is removed.
art = page.replace("__FONTS__", inline).replace("__FAVICON__", favicon_uri)
for drop in ("<!doctype html>\n", '<html lang="en">\n', "<head>\n",
             '<meta charset="utf-8">\n',
             '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
             "</head>\n", "<body>\n", "</body>\n", "</html>\n"):
    assert drop in art, drop
    art = art.replace(drop, "", 1)
assert art.lstrip().startswith("<title>"), art[:60]
open(os.path.join(OUT, "artifact.html"), "w").write(art)


def kb(p):
    return os.path.getsize(p) / 1024.0


print("index.html    %8.1f KB" % kb(os.path.join(OUT, "index.html")))
print("artifact.html %8.1f KB" % kb(os.path.join(OUT, "artifact.html")))
print("fonts         %8.1f KB" % sum(kb(os.path.join(OUT, n)) for n in os.listdir(OUT) if n.endswith(".woff2")))
print("index + fonts %8.1f KB" % (kb(os.path.join(OUT, "index.html"))
                                  + sum(kb(os.path.join(OUT, n)) for n in os.listdir(OUT) if n.endswith(".woff2"))))
