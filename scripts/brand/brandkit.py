# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry and colour tokens of the Tether brand kit, built from mark 42a (#671).

Every file in the kit comes from the numbers in this module; ``build.py`` writes the files.
The mark is drawn on a 200 x 200 viewBox. ``docs/brand.md`` gives the rules for using it.

The word TETHER is set in Red Hat Display and converted to outlines here, so no kit file loads
a font. The fonts are read from the copies the application ships
(``src/tether/gui/assets/brand/fonts``).
"""

from __future__ import annotations

import math
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

REPO = Path(__file__).resolve().parents[2]
FONT_DIR = REPO / "src" / "tether" / "gui" / "assets" / "brand" / "fonts"

# --- colour tokens ---------------------------------------------------------------
INK = "#121417"  # ink on the light ground
PAPER = "#fbfbfa"  # light ground
EDGE = "#d5dadd"  # edge of the light tile
GROUND = "#0d0f12"  # dark ground
CHALK = "#f2f4f5"  # ink on the dark ground

# --- mark 42a geometry -----------------------------------------------------------
WT = 1.45  # "bold" line weight
MOL = (100.0, 124.0)
MOL_R = 11.875
ARC_R = (56.4, 42.3, 28.2)
ARC_SPAN = 0.52
ARC_STROKE = 6.96
DOT_Y = 49.0
DOT_X = tuple(range(40, 161, 15))
GRID_R = 0.9
GRID_OPACITY = 0.38


def f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def dot_r(x: float) -> float:
    return (1.3 + 3.3 * math.exp(-(((x - 100) / 40) ** 2))) * 1.35 * math.sqrt(WT)


def arc_d(cx: float, cy: float, r: float, span: float = ARC_SPAN) -> str:
    dx, dy = r * math.sin(span), r * math.cos(span)
    return f"M{f(cx - dx)} {f(cy - dy)}A{f(r)} {f(r)} 0 0 1 {f(cx + dx)} {f(cy - dy)}"


def _near_arc(x: float, y: float, r: float, limit: float) -> bool:
    cx, cy = MOL
    d = math.hypot(x - cx, y - cy)
    ang = math.atan2(x - cx, cy - y)  # 0 = straight up
    if abs(ang) <= ARC_SPAN:
        return abs(d - r) < limit
    ex = cx + math.copysign(r * math.sin(ARC_SPAN), ang)
    ey = cy - r * math.cos(ARC_SPAN)
    return math.hypot(x - ex, y - ey) < limit


def grid_points() -> list[tuple[int, int]]:
    pts = []
    for x in range(40, 161, 15):
        for y in range(19, 140, 15):
            if y <= 51:
                continue
            if math.hypot(x - MOL[0], y - MOL[1]) < 13:
                continue
            if any(_near_arc(x, y, r, 5.0) for r in ARC_R):
                continue
            pts.append((x, y))
    return pts


def glyph(ink: str = INK, grid: bool = True) -> str:
    """The full mark 42a (surface dots, arcs, molecule, pixel grid). No tile."""
    out = []
    if grid:
        g = "".join(f'<circle cx="{x}" cy="{y}" r="{GRID_R}"/>' for x, y in grid_points())
        out.append(f'<g fill="{ink}" opacity="{GRID_OPACITY}">{g}</g>')
    d = "".join(f'<circle cx="{x}" cy="{f(DOT_Y)}" r="{f(dot_r(x))}"/>' for x in DOT_X)
    mol = f'<circle cx="{f(MOL[0])}" cy="{f(MOL[1])}" r="{f(MOL_R)}"/>'
    out.append(f'<g fill="{ink}">{d}{mol}</g>')
    a = "".join(f'<path d="{arc_d(*MOL, r)}"/>' for r in ARC_R)
    out.append(
        f'<g fill="none" stroke="{ink}" stroke-width="{f(ARC_STROKE)}" '
        f'stroke-linecap="round">{a}</g>'
    )
    return "".join(out)


# --- the small mark: for 24 px to 32 px ------------------------------------------
S_DOTS = ((44, 5.6), (72, 9.2), (100, 12.0), (128, 9.2), (156, 5.6))
S_DOT_Y = 46.0
S_MOL = (100.0, 136.0)
S_MOL_R = 17.0
S_ARC_R = (64.0, 39.0)
S_ARC_STROKE = 11.5


def glyph_small(ink: str = INK) -> str:
    """The small mark: five dots, two arcs, no grid. For 24 px to 32 px."""
    d = "".join(f'<circle cx="{x}" cy="{f(S_DOT_Y)}" r="{f(r)}"/>' for x, r in S_DOTS)
    a = "".join(f'<path d="{arc_d(*S_MOL, r)}"/>' for r in S_ARC_R)
    mol = f'<circle cx="{f(S_MOL[0])}" cy="{f(S_MOL[1])}" r="{f(S_MOL_R)}"/>'
    return (
        f'<g fill="{ink}">{d}{mol}</g>'
        f'<g fill="none" stroke="{ink}" stroke-width="{f(S_ARC_STROKE)}" '
        f'stroke-linecap="round">{a}</g>'
    )


# --- the tiny mark: for 16 px and 20 px. Drawn on the 16 px grid (1 px = 12.5 units). ---
T_DOTS = ((50, 9.0), (100, 13.5), (150, 9.0))
T_DOT_Y = 40.0
T_MOL = (100.0, 157.0)
T_MOL_R = 18.0
T_ARC_R = (76.0, 45.0)
T_ARC_STROKE = 14.5
T_ARC_SPAN = 0.72


def glyph_tiny(ink: str = INK) -> str:
    """The tiny mark: three dots, two arcs. It fills the tile; use it with no transform."""
    d = "".join(f'<circle cx="{x}" cy="{f(T_DOT_Y)}" r="{f(r)}"/>' for x, r in T_DOTS)
    a = "".join(f'<path d="{arc_d(*T_MOL, r, T_ARC_SPAN)}"/>' for r in T_ARC_R)
    mol = f'<circle cx="{f(T_MOL[0])}" cy="{f(T_MOL[1])}" r="{f(T_MOL_R)}"/>'
    return (
        f'<g fill="{ink}">{d}{mol}</g>'
        f'<g fill="none" stroke="{ink}" stroke-width="{f(T_ARC_STROKE)}" '
        f'stroke-linecap="round">{a}</g>'
    )


# --- the 16 px icon: the tiny mark fitted to the pixel grid (#671) ----------------
# At 16 px every curve of the tiny mark falls between pixels, so the dots and arcs render as
# grey smears. This version puts the dots and arcs on whole pixels. Each cell is
# (row, first column, width) on the 16 x 16 grid. The molecule stays a circle: its slightly
# soft edge makes it read larger than the centre dot, as in the vector mark.
PX = 200 / 16
PX_CELLS = (
    (2, 7, 2), (3, 7, 2),  # centre surface dot, 2 x 2
    (3, 3, 1), (3, 12, 1),  # the two side dots, 1 x 1
    (5, 6, 4), (6, 4, 2), (6, 10, 2), (7, 3, 1), (7, 12, 1),  # outer wavefront
    (8, 7, 2), (9, 6, 1), (9, 9, 1),  # inner wavefront
)  # fmt: skip
PX_MOL = (8.0, 12.0)
PX_MOL_R = 1.3


def glyph_16px(ink: str = INK) -> str:
    """The tiny mark on the 16 px grid. Use it only for the 16 px raster, with no transform."""
    cells = "".join(
        f'<rect x="{f(c * PX)}" y="{f(r * PX)}" width="{f(w * PX)}" height="{f(PX)}"/>'
        for r, c, w in PX_CELLS
    )
    mx, my = PX_MOL
    mol = f'<circle cx="{f(mx * PX)}" cy="{f(my * PX)}" r="{f(PX_MOL_R * PX)}"/>'
    return f'<g fill="{ink}">{cells}{mol}</g>'


ICON_XF = "translate(100 100) scale(1.22) translate(-100 -89)"  # mark 42a, no wordmark
SMALL_XF = "translate(100 100) scale(1.2) translate(-100 -93.5)"


def tile(dark: bool, rx: float = 44, edge: bool = True) -> str:
    if dark:
        return f'<rect x="1" y="1" width="198" height="198" rx="{f(rx)}" fill="{GROUND}"/>'
    stroke = f' stroke="{EDGE}" stroke-width="2"' if edge else ""
    return f'<rect x="1" y="1" width="198" height="198" rx="{f(rx)}" fill="{PAPER}"{stroke}/>'


# --- text as outlines (Red Hat Display, SIL OFL 1.1) -----------------------------
_FONTS: dict[str, tuple[TTFont, hb.Font]] = {}


def _font(weight: str):
    if weight not in _FONTS:
        path = FONT_DIR / f"RedHatDisplay-{weight}.ttf"
        tt = TTFont(path)
        face = hb.Face(path.read_bytes())
        _FONTS[weight] = (tt, hb.Font(face))
    return _FONTS[weight]


def text_width(text: str, size: float, spacing: float = 0.0, weight: str = "Bold") -> float:
    tt, hbf = _font(weight)
    upem = tt["head"].unitsPerEm
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbf, buf, {"kern": True, "liga": False})
    adv = sum(p.x_advance for p in buf.glyph_positions) * size / upem
    return adv + spacing * len(text)


def text_path(
    text: str,
    size: float,
    x: float,
    baseline: float,
    spacing: float = 0.0,
    weight: str = "Bold",
    anchor: str = "start",
) -> str:
    """Return the SVG path data of ``text`` as outlines.

    ``spacing`` is CSS letter-spacing: it is added after each glyph, the last one included,
    so a centred run is centred on its advance box, as a browser does it.
    """
    tt, hbf = _font(weight)
    upem = tt["head"].unitsPerEm
    scale = size / upem
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbf, buf, {"kern": True, "liga": False})
    total = sum(p.x_advance for p in buf.glyph_positions) * scale + spacing * len(text)
    if anchor == "middle":
        x -= total / 2
    elif anchor == "end":
        x -= total
    gs = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    parts = []
    pen_x = x
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        name = order[info.codepoint]
        sp = SVGPathPen(gs, ntos=f)
        tp = TransformPen(
            sp,
            (scale, 0, 0, -scale, pen_x + pos.x_offset * scale, baseline - pos.y_offset * scale),
        )
        gs[name].draw(tp)
        parts.append(sp.getCommands())
        pen_x += pos.x_advance * scale + spacing
    return "".join(parts)


def wordmark(ink: str = INK) -> str:
    """TETHER as on the 42a tile: size 17, letter-spacing 7, centred on x = 103.5, baseline 180."""
    return f'<path fill="{ink}" d="{text_path("TETHER", 17, 103.5, 180, 7, "Bold", "middle")}"/>'


def svg(inner: str, w: float = 200, h: float = 200, px: int | None = None, title: str = "") -> str:
    size = f' width="{px}" height="{round(px * h / w)}"' if px else ""
    t = f"<title>{title}</title>" if title else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {f(w)} {f(h)}"{size} role="img">'
        f"{t}{inner}</svg>\n"
    )


def field_dots(
    w: float, h: float, ink: str, pitch: float, r: float, op: float, x0=None, y0=None
) -> str:
    """The pixel-grid field as explicit circles (no SVG pattern, so every renderer draws it)."""
    x0 = pitch / 2 if x0 is None else x0
    y0 = pitch / 2 if y0 is None else y0
    cs = []
    y = y0
    while y < h:
        x = x0
        while x < w:
            cs.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}"/>')
            x += pitch
        y += pitch
    return f'<g fill="{ink}" opacity="{op}">{"".join(cs)}</g>'


def beam_rule(
    w: float, y: float, ink: str, n: int = 21, rmax: float = 3.2, rmin: float = 0.9
) -> str:
    """The beam-profile dot row as a horizontal rule."""
    cs = []
    for i in range(n):
        x = w * (i + 0.5) / n
        u = (i - (n - 1) / 2) / ((n - 1) / 2)
        r = rmin + (rmax - rmin) * math.exp(-((u / 0.45) ** 2))
        cs.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}"/>')
    return f'<g fill="{ink}">{"".join(cs)}</g>'
