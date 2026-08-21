"""Generates every brand asset from the geometry in README.md.

Run from docs/brand with: uv run --with fonttools python3 generate.py
Add --proof (needs cairosvg) to also render proof-light.png and proof-dark.png.
The wordmark is outlined from DejaVu Sans Mono so no font is needed to view
the result.
"""
import math, sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

CX = CY = 60.0; CORE_R = 5.0; RAY_START = 11.0
# Six diffraction spikes, six shorter rays between, twelve hair rays between those.
RAYS = [*[(a, 46.0, 1.8, 1.00) for a in range(90, 450, 60)],
        *[(a, 30.0, 1.2, 0.60) for a in range(120, 480, 60)],
        *[(a, 22.0, 0.9, 0.35) for a in range(105, 465, 30)]]
SHELLS = [(26.0, 1.2, 0.42), (38.0, 1.0, 0.28)]
LIMIT = (52.0, 1.4, 0.55)

def pt(a, r): t = math.radians(a); return (round(CX + r*math.cos(t), 2), round(CY - r*math.sin(t), 2))
def mark(ink, accent, mono=False, off=(0,0), scale=1.0, small=False):
    g = [f'<g transform="translate({off[0]},{off[1]}) scale({scale})" fill="none" stroke="{ink}" stroke-linecap="round">']
    rays = [r for r in RAYS if not small or r[3] == 1.0]
    for a, L, w, o in rays:
        (x1,y1),(x2,y2) = pt(a, RAY_START), pt(a, L)
        g.append(f'  <path d="M{x1},{y1} L{x2},{y2}" stroke-width="{w if not small else 3.2}" opacity="{o}"/>')
    if not small:
        for r, w, o in SHELLS: g.append(f'  <circle cx="{CX}" cy="{CY}" r="{r}" stroke-width="{w}" opacity="{o}"/>')
    r, w, o = LIMIT
    g.append(f'  <circle cx="{CX}" cy="{CY}" r="{r}" stroke-width="{w if not small else 2.6}" opacity="{o}"' + ('' if small else ' stroke-dasharray="4 5"') + '/>')
    g.append(f'  <circle cx="{CX}" cy="{CY}" r="{CORE_R if not small else 8}" fill="{ink if mono else accent}" stroke="none"/>')
    g.append('</g>'); return "\n".join(g)

f = TTFont("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"); gs = f.getGlyphSet(); cmap = f.getBestCmap()
bp = BoundsPen(gs); gs[cmap[ord("x")]].draw(bp); xh = bp.bounds[3]
s = 44.0/xh; base_y = 84.0; x = 140.0; word = []
for ch in "foe":
    gname = cmap[ord(ch)]; pen = SVGPathPen(gs)
    gs[gname].draw(TransformPen(pen, (s,0,0,-s,x,base_y))); word.append(f'  <path d="{pen.getCommands()}"/>'); x += f["hmtx"][gname][0]*s
WORD = "\n".join(word); END_X = round(x,1)
def svg(vb, body, bg=None):
    rect = f'\n  <rect width="100%" height="100%" fill="{bg}"/>' if bg else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="foe">{rect}\n{body}\n</svg>\n'
ADAPT, ACC = "currentColor", "var(--foe-accent, #C7791A)"
LI, LA, LBG = "#15181C", "#C7791A", "#F2EEDE"; DI, DA, DBG = "#EDEAE4", "#E8A43E", "#1E1F1C"
LOCK = f"0 0 {END_X+14:.0f} 120"
files = {
  "foe-mark.svg":         svg("0 0 120 120", mark(ADAPT, ACC)),
  "foe-mark-mono.svg":    svg("0 0 120 120", mark(ADAPT, ADAPT, mono=True)),
  "foe-lockup.svg":       svg(LOCK, mark(ADAPT, ACC) + f'\n<g fill="{ADAPT}">\n{WORD}\n</g>'),
  "foe-lockup-light.svg": svg(LOCK, mark(LI, LA) + f'\n<g fill="{LI}">\n{WORD}\n</g>'),
  "foe-lockup-dark.svg":  svg(LOCK, mark(DI, DA) + f'\n<g fill="{DI}">\n{WORD}\n</g>'),
  "foe-tile.svg":         svg("0 0 320 320", mark(LI, LA, off=(40,40), scale=2.0), bg=LBG),
  "foe-favicon.svg":      svg("0 0 64 64", mark(ADAPT, ACC, scale=64/120, small=True)),
}
for n, b in files.items(): open(n, "w").write(b)
if "--proof" in sys.argv:
    import cairosvg
    cairosvg.svg2png(url="foe-lockup-light.svg", write_to="proof-light.png", output_width=900)
    cairosvg.svg2png(url="foe-lockup-dark.svg", write_to="proof-dark.png", output_width=900)
print("wrote", ", ".join(files), "|", LOCK)
