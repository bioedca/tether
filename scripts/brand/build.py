# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the Tether brand kit (mark 42a) and copy the files the repository uses into place.

    python scripts/brand/build.py             # write build/brand-kit/, then install
    python scripts/brand/build.py --no-install --out DIR

The full kit (129 files: both themes of every icon, the GitHub social preview, the light
installer variants) goes to ``--out``. ``INSTALL`` below names the files that the repository
keeps and where each one goes. CI does not run this script; ``README.md`` beside it gives the
packages it needs and the versions that last reproduced the committed files.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import brandkit as b  # noqa: E402
from brandkit import CHALK, EDGE, GROUND, INK, PAPER, f, svg  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6 import QtCore, QtGui, QtSvg  # noqa: E402

REPO = b.REPO
KIT = REPO / "build" / "brand-kit"  # set again from --out in main()
MARKER = ".tether-brand-kit"  # written into the output folder; only such a folder is replaced
_app = QtGui.QGuiApplication([])

DARK_EDGE = "#2b3036"
TAGLINE = "Provenance-first single-molecule FRET analysis"


def write(rel: str, text: str) -> Path:
    p = KIT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def raster(svg_text: str, w: int, h: int | None = None) -> Image.Image:
    h = h or w
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg_text.encode()))
    img = QtGui.QImage(w, h, QtGui.QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QtCore.QRectF(0, 0, w, h))
    p.end()
    ba = QtCore.QByteArray()
    buf = QtCore.QBuffer(ba)
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


def png(rel: str, svg_text: str, w: int, h: int | None = None) -> Image.Image:
    im = raster(svg_text, w, h)
    p = KIT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    im.save(p, optimize=True)
    return im


# --- the app icon at each optical size -------------------------------------------
def icon_svg(dark: bool, small: bool, edge: bool = True, tiny: bool = False) -> str:
    ink = CHALK if dark else INK
    if dark:
        t = (
            f'<rect x="1" y="1" width="198" height="198" rx="44" fill="{GROUND}"'
            + (f' stroke="{DARK_EDGE}" stroke-width="2"' if edge else "")
            + "/>"
        )
    else:
        t = b.tile(False, edge=edge)
    if tiny:
        g = b.glyph_tiny(ink)
    elif small:
        g = f'<g transform="{b.SMALL_XF}">{b.glyph_small(ink)}</g>'
    else:
        g = f'<g transform="{b.ICON_XF}">{b.glyph(ink)}</g>'
    return svg(t + g, title="Tether")


def icon_16px(dark: bool, fitted: bool = True) -> Image.Image:
    """The 16 px icon: the tiny mark fitted to the pixel grid, or (``fitted=False``) as drawn."""
    if not fitted:
        return raster(icon_svg(dark, small=True, tiny=True, edge=False), 16)
    ink = CHALK if dark else INK
    t = f'<rect x="1" y="1" width="198" height="198" rx="44" fill="{GROUND if dark else PAPER}"/>'
    return raster(svg(t + b.glyph_16px(ink), title="Tether"), 16)


def icon_for(size: int, dark: bool) -> Image.Image:
    """Pick the optical size: the tiny mark up to 20 px (fitted to the grid at 16 px), the
    small mark up to 32 px, mark 42a without grid at 48 px, mark 42a with grid above."""
    if size == 16:
        return icon_16px(dark)
    if size <= 20:
        return raster(icon_svg(dark, small=True, tiny=True, edge=False), size)
    if size <= 32:
        return raster(icon_svg(dark, small=True), size)
    if size <= 48:
        ink = CHALK if dark else INK
        t = icon_svg(dark, False).split("<g transform")[0].replace("</svg>", "")
        s = t + f'<g transform="{b.ICON_XF}">{b.glyph(ink, grid=False)}</g></svg>'
        return raster(s, size)
    return raster(icon_svg(dark, small=False), size)


def macos_icon(size: int, dark: bool) -> Image.Image:
    """macOS grid: the tile is 824/1024 of the canvas, centred."""
    ink = CHALK if dark else INK
    fill = GROUND if dark else PAPER
    edge = DARK_EDGE if dark else EDGE
    inner = (
        f'<g transform="translate(100 100) scale(0.8047) translate(-100 -100)">'
        f'<rect x="1" y="1" width="198" height="198" rx="45" fill="{fill}" '
        f'stroke="{edge}" stroke-width="1.5"/>'
        + (
            f'<g transform="{b.SMALL_XF}">{b.glyph_small(ink)}</g>'
            if size <= 32
            else f'<g transform="{b.ICON_XF}">{b.glyph(ink, grid=size > 64)}</g>'
        )
        + "</g>"
    )
    return raster(svg(inner), size)


def build_marks() -> None:
    for name, ink in (("black", INK), ("white", CHALK)):
        write(f"mark/tether-mark-{name}.svg", svg(b.glyph(ink), title="Tether"))
        write(f"mark/tether-mark-small-{name}.svg", svg(b.glyph_small(ink), title="Tether"))
        write(f"mark/tether-mark-tiny-{name}.svg", svg(b.glyph_tiny(ink), title="Tether"))
    for dark in (False, True):
        t = "dark" if dark else "light"
        ink = CHALK if dark else INK
        write(
            f"mark/tether-tile-{t}.svg",
            svg(b.tile(dark) + b.glyph(ink) + b.wordmark(ink), title="Tether"),
        )
        write(f"mark/tether-icon-{t}.svg", icon_svg(dark, small=False, edge=not dark))
        write(f"mark/tether-icon-small-{t}.svg", icon_svg(dark, small=True, edge=not dark))
        write(
            f"mark/tether-icon-tiny-{t}.svg", icon_svg(dark, small=True, tiny=True, edge=not dark)
        )
        png(
            f"mark/tether-tile-{t}-1024.png",
            svg(b.tile(dark) + b.glyph(ink) + b.wordmark(ink)),
            1024,
        )


# --- lockups ---------------------------------------------------------------------
def lockup(ink: str, grid: bool = True) -> str:
    """Horizontal lockup: mark at the left, TETHER at the right. 560 x 160."""
    # the mark's ink box is x 37..163, y 41.5..136; put it in a 120-unit-high band
    xf = "translate(14 8) scale(1.2) translate(-37 -34)"
    g = f'<g transform="{xf}">{b.glyph(ink, grid=grid)}</g>'
    cap = 0.7  # Red Hat Display cap height / em
    size = 62
    base = 80 + size * cap / 2
    w = f'<path fill="{ink}" d="{b.text_path("TETHER", size, 196, base, 22, "Bold")}"/>'
    return svg(g + w, 560, 160, title="Tether")


def wordmark_only(ink: str) -> str:
    w = b.text_width("TETHER", 62, 22) - 22
    d = b.text_path("TETHER", 62, 0, 44, 22, "Bold")
    return svg(f'<path fill="{ink}" d="{d}"/>', w, 45, title="Tether")


def build_lockups() -> None:
    for t, ink in (("light", INK), ("dark", CHALK)):
        write(f"lockup/tether-lockup-{t}.svg", lockup(ink))
        write(f"lockup/tether-lockup-{t}-plain.svg", lockup(ink, grid=False))
        write(f"lockup/tether-wordmark-{t}.svg", wordmark_only(ink))
    # README header: one file per GitHub colour scheme
    shutil.copy(KIT / "lockup/tether-lockup-light.svg", KIT / "github/readme-header-light.svg")
    shutil.copy(KIT / "lockup/tether-lockup-dark.svg", KIT / "github/readme-header-dark.svg")


# --- application icons -----------------------------------------------------------
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def save_ico(rel: str, frames: dict[int, Image.Image]) -> None:
    p = KIT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    sizes = sorted(frames)
    big = frames[sizes[-1]]
    big.save(
        p,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=[frames[s] for s in sizes[:-1]],
    )


def build_app_icons() -> None:
    for dark in (True, False):
        t = "dark" if dark else "light"
        frames = {s: icon_for(s, dark) for s in ICO_SIZES}
        save_ico(f"app-icon/windows/tether-{t}.ico", frames)
        for s in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
            im = icon_for(s, dark)
            p = KIT / f"app-icon/png-{t}/tether-{s}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            im.save(p, optimize=True)
        for s in (16, 24, 32, 48, 64, 128, 256, 512):
            p = KIT / f"app-icon/linux-{t}/hicolor/{s}x{s}/apps/tether.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            icon_for(s, dark).save(p, optimize=True)
        write(f"app-icon/linux-{t}/hicolor/scalable/apps/tether.svg", icon_svg(dark, small=False))
        big = macos_icon(1024, dark)
        p = KIT / f"app-icon/macos/tether-{t}.icns"
        p.parent.mkdir(parents=True, exist_ok=True)
        big.save(
            p,
            format="ICNS",
            append_images=[macos_icon(s, dark) for s in (16, 32, 64, 128, 256, 512)],
        )
        big.save(KIT / f"app-icon/macos/tether-{t}-1024.png", optimize=True)


# --- the .tether document icon ---------------------------------------------------
def document_svg(small: bool = False) -> str:
    page = (
        '<path d="M44 14h78l40 40v122a10 10 0 0 1-10 10H44'
        'a10 10 0 0 1-10-10V24a10 10 0 0 1 10-10z" '
        f'fill="{PAPER}" stroke="{INK}" stroke-width="{8 if small else 5}" '
        'stroke-linejoin="round"/>'
        f'<path d="M122 14v30a10 10 0 0 0 10 10h30" fill="none" stroke="{INK}" '
        f'stroke-width="{8 if small else 5}" stroke-linejoin="round"/>'
    )
    if small:
        xf = "translate(98 122) scale(0.62) translate(-100 -93.5)"
        g = f'<g transform="{xf}">{b.glyph_small(INK)}</g>'
    else:
        g = f'<g transform="translate(98 120) scale(0.78) translate(-100 -89)">{b.glyph(INK)}</g>'
    return svg(page + g, title="Tether project")


def build_document_icon() -> None:
    write("file-icon/tether-document.svg", document_svg())
    write("file-icon/tether-document-small.svg", document_svg(small=True))
    frames = {s: raster(document_svg(small=s <= 48), s) for s in ICO_SIZES}
    save_ico("file-icon/tether-document.ico", frames)
    for s in (32, 64, 256):
        frames[s].save(KIT / f"file-icon/tether-document-{s}.png", optimize=True)


# --- installer images ------------------------------------------------------------
def win_welcome() -> str:
    w, h = 164, 314
    bg = f'<rect width="{w}" height="{h}" fill="{GROUND}"/>'
    fld = b.field_dots(w, h, CHALK, 13.67, 0.75, 0.22, x0=6.83, y0=6.5)
    # clear the field behind the mark and the wordmark
    clear = f'<rect x="14" y="58" width="136" height="170" rx="16" fill="{GROUND}"/>'
    xf = "translate(82 132) scale(0.98) translate(-100 -89)"
    g = f'<g transform="{xf}">{b.glyph(CHALK, grid=False)}</g>'
    wm = f'<path fill="{CHALK}" d="{b.text_path("TETHER", 15, 82 + 3, 212, 6, "Bold", "middle")}"/>'
    rule = b.beam_rule(w, 290, CHALK, n=13, rmax=2.6, rmin=0.8)
    return svg(bg + fld + clear + g + wm + rule, w, h)


def win_header() -> str:
    w, h = 150, 57
    bg = f'<rect width="{w}" height="{h}" fill="#ffffff"/>'
    xf = "translate(26 28.5) scale(0.36) translate(-100 -93.5)"
    g = f'<g transform="{xf}">{b.glyph_small(INK)}</g>'
    wm = f'<path fill="{INK}" d="{b.text_path("TETHER", 14.5, 50, 33.6, 4.6, "Bold")}"/>'
    return svg(bg + g + wm, w, h)


def mac_welcome() -> str:
    """Transparent, mid-grey mark: readable under light and dark installer windows."""
    w, h = 1227, 600
    grey = "#7d868d"
    fld = b.field_dots(430, h, grey, 30, 1.9, 0.30, x0=20, y0=15)
    xf = "translate(205 330) scale(3.0) translate(-100 -89)"
    g = f'<g opacity="0.34" transform="{xf}">{b.glyph(grey, grid=False)}</g>'
    return svg(fld + g, w, h)


def build_installer() -> None:
    write("installer/windows-welcome.svg", win_welcome())
    png("installer/windows-welcome-164x314.png", win_welcome(), 164, 314)
    png("installer/windows-welcome-328x628.png", win_welcome(), 328, 628)
    write("installer/windows-header.svg", win_header())
    png("installer/windows-header-150x57.png", win_header(), 150, 57)
    png("installer/windows-header-300x114.png", win_header(), 300, 114)
    icon_for(256, True).save(KIT / "installer/windows-icon-256.png", optimize=True)
    write("installer/macos-welcome.svg", mac_welcome())
    png("installer/macos-welcome-1227x600.png", mac_welcome(), 1227, 600)


# --- social cards ----------------------------------------------------------------
SOCIAL_REF = (1280, 640)  # the canvas the mark, word and tagline positions are drawn for


def social(w: int, h: int, tagline: str = TAGLINE) -> str:
    """The social card at ``w`` x ``h``: the dot field fills the canvas, and the lockup
    (mark, word, tagline) is drawn on the 1280 x 640 reference and scaled to the canvas.

    The scale is the smaller of the two canvas-to-reference ratios, never above 1, and the
    lockup stays centred, so a narrower card keeps the right margin of the reference in
    proportion (#683: at 1200 px the unscaled tagline ran past the edge). At the reference
    size the drawing is unchanged.
    """
    rw, rh = SOCIAL_REF
    bg = f'<rect width="{w}" height="{h}" fill="{GROUND}"/>'
    fld = b.field_dots(w, h, CHALK, 40, 2.0, 0.20, x0=20, y0=20)
    cy = rh / 2
    clear = (
        f'<rect x="150" y="{f(cy - 215)}" width="{rw - 300}" height="430" rx="40" fill="{GROUND}"/>'
    )
    xf = f"translate(350 {f(cy)}) scale(2.55) translate(-100 -89)"
    g = f'<g transform="{xf}">{b.glyph(CHALK, grid=False)}</g>'
    wm = f'<path fill="{CHALK}" d="{b.text_path("TETHER", 104, 560, cy + 14, 38, "Bold")}"/>'
    tl = (
        f'<path fill="{CHALK}" opacity="0.78" '
        f'd="{b.text_path(tagline, 29, 564, cy + 78, 0.6, "Medium")}"/>'
        if tagline
        else ""
    )
    lockup = clear + g + wm + tl
    k = min(1.0, w / rw, h / rh)
    if (w, h) != (rw, rh):
        fit = f"translate({f(w / 2)} {f(h / 2)}) scale({f(k)}) translate({f(-rw / 2)} {f(-rh / 2)})"
        lockup = f'<g transform="{fit}">{lockup}</g>'
    return svg(bg + fld + lockup, w, h)


def build_social() -> None:
    write("github/social-preview.svg", social(1280, 640))
    png("github/social-preview-1280x640.png", social(1280, 640), 1280, 640)
    png("docs/social-card-1200x630.png", social(1200, 630), 1200, 630)


# --- documentation site ----------------------------------------------------------
def favicon_svg() -> str:
    inner = (
        f"<style>.t{{fill:{GROUND}}}.i{{fill:{CHALK}}}.s{{stroke:{CHALK}}}"
        f"@media (prefers-color-scheme: dark){{.t{{fill:{PAPER}}}.i{{fill:{INK}}}"
        f".s{{stroke:{INK}}}}}</style>"
    )
    g = b.glyph_tiny("X")
    g = g.replace('fill="X"', 'class="i"').replace('stroke="X"', 'class="s"')
    inner += f'<rect class="t" x="1" y="1" width="198" height="198" rx="44"/>{g}'
    return svg(inner, title="Tether")


BRAND_CSS = """/* Tether brand palette for Material for MkDocs. Set `primary: custom` and
   `accent: custom` in mkdocs.yml, then load this file with `extra_css`.
   The file loads no font and no remote asset. */
:root {
  --tether-ink: #121417;
  --tether-paper: #fbfbfa;
  --tether-ground: #0d0f12;
  --tether-chalk: #f2f4f5;
  --tether-edge: #d5dadd;
}
[data-md-color-scheme="default"] {
  --md-primary-fg-color: #121417;
  --md-primary-fg-color--light: #343a40;
  --md-primary-fg-color--dark: #0d0f12;
  --md-primary-bg-color: #fbfbfa;
  --md-primary-bg-color--light: #b6bdc2;
  --md-accent-fg-color: #4b535a;
  --md-accent-fg-color--transparent: rgba(18, 20, 23, 0.08);
  --md-default-bg-color: #fbfbfa;
  --md-default-fg-color: #121417;
  --md-default-fg-color--light: #4b535a;
  --md-default-fg-color--lighter: #8d969c;
  --md-default-fg-color--lightest: #e6e9eb;
  --md-typeset-a-color: #121417;
  --md-footer-bg-color: #121417;
  --md-footer-bg-color--dark: #0d0f12;
}
[data-md-color-scheme="slate"] {
  --md-primary-fg-color: #0d0f12;
  --md-primary-fg-color--light: #1f2327;
  --md-primary-fg-color--dark: #0d0f12;
  --md-primary-bg-color: #f2f4f5;
  --md-primary-bg-color--light: #8d969c;
  --md-accent-fg-color: #b6bdc2;
  --md-accent-fg-color--transparent: rgba(242, 244, 245, 0.10);
  --md-default-bg-color: #14171b;
  --md-default-fg-color: #f2f4f5;
  --md-default-fg-color--light: #b6bdc2;
  --md-default-fg-color--lighter: #8d969c;
  --md-default-fg-color--lightest: #2b3036;
  --md-typeset-a-color: #f2f4f5;
  --md-code-bg-color: #1b1f24;
  --md-footer-bg-color: #0d0f12;
  --md-footer-bg-color--dark: #08090b;
}
/* Monochrome links: the underline identifies a link, not a colour. */
.md-typeset a { text-decoration: underline; text-decoration-thickness: 0.06em; \
text-underline-offset: 0.18em; }
.md-typeset a:hover, .md-typeset a:focus { text-decoration-thickness: 0.12em; }
.md-typeset .headerlink, .md-typeset a.md-button { text-decoration: none; }
[data-md-color-scheme="slate"] .md-header { border-bottom: 1px solid #2b3036; }
.md-header__button.md-logo img, .md-header__button.md-logo svg { height: 1.5rem; width: auto; }
/* The home page lockup: one image for each colour scheme. */
.tether-hero { margin: 0.4rem 0 1.2rem; }
.tether-hero img { height: 4.6rem; width: auto; }
[data-md-color-scheme="default"] .tether-hero .on-dark,
[data-md-color-scheme="slate"] .tether-hero .on-light { display: none; }
/* The beam-profile rule: a row of dots that replaces the <hr> line. */
.md-typeset hr {
  border: 0; height: 9px; margin: 2rem 0;
  background: radial-gradient(circle at center, currentColor 0 1.6px, transparent 1.9px) \
center / 14px 9px repeat-x;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 30%, #000 70%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 30%, #000 70%, transparent);
  opacity: 0.55;
}
"""


def build_docs() -> None:
    write("docs/logo.svg", svg(b.glyph_small(CHALK), title="Tether"))
    write("docs/favicon.svg", favicon_svg())
    frames = {s: icon_for(s, True) for s in (16, 32, 48)}
    save_ico("docs/favicon.ico", frames)
    icon_for(180, True).save(KIT / "docs/apple-touch-icon-180.png", optimize=True)
    write("docs/brand.css", BRAND_CSS)
    for t in ("light", "dark"):
        shutil.copy(KIT / f"lockup/tether-lockup-{t}.svg", KIT / f"docs/lockup-{t}.svg")


# --- section icons (24 x 24, 2 px stroke, round caps) ----------------------------
def _i(body: str, ink: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
        f'fill="none" stroke="{ink}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>\n'
    )


def _d(x: float, y: float, r: float) -> str:
    return f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="currentColor" stroke="none"/>'


ICONS: dict[str, tuple[str, str]] = {
    "extract": (
        "Extract: one bright spot on the pixel grid",
        "".join(_d(x, y, 1.15) for x in (5, 12, 19) for y in (5, 12, 19) if (x, y) != (12, 12))
        + _d(12, 12, 3.6),
    ),
    "curate": (
        "Curate: accept a molecule",
        '<path d="M5.2 8.2a8 8 0 0 1 13.6 0"/><path d="M5.2 15.8a8 8 0 0 0 13.6 0"/>'
        '<path d="M8.4 12.2l2.6 2.6 4.8-5.2"/>',
    ),
    "correct": (
        "Correct: set the two correction factors",
        '<path d="M8 4v3.2M8 13.2V20M16 4v6.8M16 16.8V20"/>' + _d(8, 10.2, 2.7) + _d(16, 13.8, 2.7),
    ),
    "idealize": ("Idealize: a step path through the states", '<path d="M3 16.5h5v-9h6v9h7"/>'),
    "analyze": (
        "Analyze: a histogram with the beam-profile shape",
        '<path d="M4 19v-2.5M8 19v-7M12 19V5M16 19v-7M20 19v-2.5"/>',
    ),
    "hand-off": (
        "Hand-off: transfer from Tether to tMAVEN",
        _d(4.6, 12, 2.6)
        + '<path d="M10 8.2a6.2 6.2 0 0 1 0 7.6"/><path d="M13.6 6a9.6 9.6 0 0 1 0 12"/>'
        + '<circle cx="19.6" cy="12" r="2.1"/>',
    ),
    "import": ("Import", '<path d="M12 4v10.5M7.8 10.6l4.2 4.2 4.2-4.2M5 17.2V20h14v-2.8"/>'),
    "export": ("Export", '<path d="M12 15V4.5M7.8 8.4L12 4.2l4.2 4.2M5 17.2V20h14v-2.8"/>'),
    "project": (
        "Project: a .tether file",
        '<path d="M7 3h7l4.5 4.5V19a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
        + _d(8.6, 11, 0.9)
        + _d(11.75, 11, 1.5)
        + _d(14.9, 11, 0.9)
        + _d(11.75, 16.4, 1.9),
    ),
    "conditions": (
        "Conditions: groups of molecules",
        _d(5.2, 6, 1.9)
        + _d(5.2, 12, 1.9)
        + _d(5.2, 18, 1.9)
        + '<path d="M10 6h10M10 12h7M10 18h9"/>',
    ),
    "movie": (
        "Movie: a camera frame with spots",
        '<rect x="3" y="5" width="18" height="14" rx="2.5"/>'
        + _d(9.2, 10.6, 2.1)
        + _d(15.4, 14.4, 1.3),
    ),
    "busy": (
        "Busy: wavefronts go to the molecule",
        '<path d="M5.6 9.2a8.6 8.6 0 0 1 12.8 0"/><path d="M8.4 12.6a5 5 0 0 1 7.2 0"/>'
        + _d(12, 17.6, 2.3),
    ),
}


def build_gui() -> None:
    for name, (_desc, body) in ICONS.items():
        for t, ink in (("light", INK), ("dark", CHALK)):
            write(f"gui/icons/{t}/{name}.svg", _i(body.replace("currentColor", ink), ink))
    for t, dark in (("light", False), ("dark", True)):
        write(f"gui/splash-{t}.svg", splash(dark))
        png(f"gui/splash-{t}-640x360.png", splash(dark), 640, 360)
        png(f"gui/splash-{t}-1280x720.png", splash(dark), 1280, 720)
    # start-page art: the mark on a pixel field
    for t, ink, ground in (("light", INK, PAPER), ("dark", CHALK, GROUND)):
        w, h = 480, 240
        fld = b.field_dots(w, h, ink, 15, 0.9, 0.30, x0=7.5, y0=7.5)
        clear = f'<rect x="165" y="32" width="150" height="150" rx="20" fill="{ground}"/>'
        xf = "translate(240 106) scale(1.0) translate(-100 -89)"
        g = f'<g transform="{xf}">{b.glyph(ink, grid=False)}</g>'
        wm = (
            f'<path fill="{ink}" d="{b.text_path("TETHER", 17, 243.5, 218, 7, "Bold", "middle")}"/>'
        )
        clear2 = f'<rect x="170" y="196" width="140" height="32" rx="8" fill="{ground}"/>'
        bg = f'<rect width="{w}" height="{h}" fill="{ground}"/>'
        write(f"gui/start-art-{t}.svg", svg(bg + fld + clear + g + clear2 + wm, w, h))


def splash(dark: bool) -> str:
    """Splash window art, 640 x 360. Qt draws the version text in the lower left corner."""
    w, h = 640, 360
    ink, ground = (CHALK, GROUND) if dark else (INK, PAPER)
    bg = f'<rect width="{w}" height="{h}" fill="{ground}"/>'
    fld = b.field_dots(w, h, ink, 20, 1.1, 0.22, x0=10, y0=10)
    clear = f'<rect x="70" y="60" width="500" height="220" rx="24" fill="{ground}"/>'
    xf = "translate(176 170) scale(1.28) translate(-100 -89)"
    g = f'<g transform="{xf}">{b.glyph(ink, grid=False)}</g>'
    wm = f'<path fill="{ink}" d="{b.text_path("TETHER", 50, 284, 178, 18, "Bold")}"/>'
    tagline = b.text_path(TAGLINE, 14.2, 286, 212, 0.3, "Medium")
    tl = f'<path fill="{ink}" opacity="0.75" d="{tagline}"/>'
    rule = b.beam_rule(w, 330, ink, n=41, rmax=3.0, rmin=0.8)
    return svg(bg + fld + clear + g + wm + tl + rule, w, h)


TOKENS = {
    "note": "Tether brand tokens from mark 42a. Monochrome. Data colours are not brand colours.",
    "brand": {"ink": INK, "paper": PAPER, "edge": EDGE, "ground": GROUND, "chalk": CHALK},
    "neutral": {
        "0": "#fbfbfa",
        "50": "#f2f4f5",
        "100": "#e6e9eb",
        "200": "#d5dadd",
        "300": "#b6bdc2",
        "400": "#8d969c",
        "500": "#68717a",
        "600": "#4b535a",
        "700": "#343a40",
        "800": "#1f2327",
        "850": "#14171b",
        "900": "#121417",
        "950": "#0d0f12",
    },
    "light": {
        "window": "#f2f4f5",
        "surface": "#fbfbfa",
        "text": "#121417",
        "text-muted": "#4b535a",
        "border": "#d5dadd",
        "selection": "#121417",
        "selection-text": "#fbfbfa",
        "plot-background": "#fbfbfa",
        "plot-axis": "#4b535a",
        "plot-grid": "#d5dadd",
    },
    "dark": {
        "window": "#0d0f12",
        "surface": "#14171b",
        "text": "#f2f4f5",
        "text-muted": "#b6bdc2",
        "border": "#2b3036",
        "selection": "#f2f4f5",
        "selection-text": "#0d0f12",
        "plot-background": "#0d0f12",
        "plot-axis": "#b6bdc2",
        "plot-grid": "#2b3036",
    },
    "state": {
        "light": {
            "accept": "#1a7f4b",
            "reject": "#c0392f",
            "caution": "#8a5a00",
            "info": "#2456d6",
        },
        "dark": {"accept": "#4cc38a", "reject": "#ff7a6b", "caution": "#e3b341", "info": "#7aa2ff"},
    },
    "mark": {
        "viewBox": 200,
        "tile_radius": 44,
        "line_weight": 1.45,
        "clear_space": "0.25 of the tile width on each side",
        "minimum_size_px": {
            "mark 42a with grid": 64,
            "mark 42a without grid": 48,
            "small mark": 16,
        },
    },
    "type": {
        "wordmark": "Red Hat Display 700, letter-spacing 0.41 em, capitals, as outlines",
        "interface": "the system font of the platform",
    },
}


def build_tokens() -> None:
    write("tokens/tokens.json", json.dumps(TOKENS, indent=2) + "\n")
    lines = [":root {"]
    for k, v in TOKENS["brand"].items():
        lines.append(f"  --tether-{k}: {v};")
    for k, v in TOKENS["neutral"].items():
        lines.append(f"  --tether-neutral-{k}: {v};")
    lines.append("}")
    write("tokens/tokens.css", "\n".join(lines) + "\n")


# --- where the repository keeps each file (#671) ----------------------------------
GUI = "src/tether/gui/assets/brand"  # in the wheel: the application reads these
PKG = "packaging/images"  # the installer images
DOCS = "docs/assets/brand"  # the documentation site and docs/brand.md
THEMES = ("light", "dark")


def _install_map() -> list[tuple[str, str]]:
    """(kit path, repository path) for every file the repository keeps.

    The application icon is the dark tile (maintainer decision Q2 on #671), so only the
    dark app-icon set is kept; the light set stays in the full kit.
    """
    m: list[tuple[str, str]] = []
    for s in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        m.append((f"app-icon/png-dark/tether-{s}.png", f"{GUI}/app-icon/tether-{s}.png"))
    m.append(("app-icon/windows/tether-dark.ico", f"{GUI}/app-icon/tether.ico"))
    m.append(("app-icon/macos/tether-dark.icns", f"{GUI}/app-icon/tether.icns"))
    for d in [f"{s}x{s}" for s in (16, 24, 32, 48, 64, 128, 256, 512)] + ["scalable"]:
        name = "tether.svg" if d == "scalable" else "tether.png"
        rel = f"hicolor/{d}/apps/{name}"
        m.append((f"app-icon/linux-dark/{rel}", f"{GUI}/app-icon/{rel}"))
    for name in (
        "tether-document.svg",
        "tether-document-small.svg",
        "tether-document.ico",
        "tether-document-32.png",
        "tether-document-64.png",
        "tether-document-256.png",
    ):
        m.append((f"file-icon/{name}", f"{GUI}/document/{name}"))
    for t in THEMES:
        for name in sorted(ICONS):
            m.append((f"gui/icons/{t}/{name}.svg", f"{GUI}/icons/{t}/{name}.svg"))
        m.append((f"gui/start-art-{t}.svg", f"{GUI}/start-art-{t}.svg"))
        for suffix in (".svg", "-640x360.png", "-1280x720.png"):
            m.append((f"gui/splash-{t}{suffix}", f"{GUI}/splash-{t}{suffix}"))
        m.append((f"mark/tether-tile-{t}.svg", f"{GUI}/tile-{t}.svg"))
    m.append(("tokens/tokens.json", f"{GUI}/tokens.json"))

    for name in (
        "windows-welcome-164x314.png",
        "windows-header-150x57.png",
        "windows-icon-256.png",
        "macos-welcome-1227x600.png",
    ):
        m.append((f"installer/{name}", f"{PKG}/{name}"))

    for name in (
        "logo.svg",
        "favicon.svg",
        "favicon.ico",
        "apple-touch-icon-180.png",
        "lockup-light.svg",
        "lockup-dark.svg",
        "social-card-1200x630.png",
        "brand.css",
    ):
        m.append((f"docs/{name}", f"{DOCS}/{name}"))
    for ink in ("black", "white"):
        m.append((f"mark/tether-mark-{ink}.svg", f"{DOCS}/mark-{ink}.svg"))
    m.append(("mark/tether-icon-dark.svg", f"{DOCS}/icon-dark.svg"))  # the application icon
    for t in THEMES:
        m.append((f"mark/tether-tile-{t}.svg", f"{DOCS}/tile-{t}.svg"))
        for kind in ("icon-small", "icon-tiny"):
            m.append((f"mark/tether-{kind}-{t}.svg", f"{DOCS}/{kind}-{t}.svg"))
    return m


INSTALL = _install_map()

# The SVGs in DOCS that docs/brand.md shows get a display size in pixels (the width; the
# height follows the viewBox). The kit files have only a viewBox, so a browser would stretch
# them to the full page width. The drawing does not change.
PAGE_PX = {
    f"{DOCS}/lockup-light.svg": 420,
    f"{DOCS}/lockup-dark.svg": 420,
    f"{DOCS}/mark-black.svg": 160,
    f"{DOCS}/mark-white.svg": 160,
    **{f"{DOCS}/tile-{t}.svg": 160 for t in THEMES},
    f"{DOCS}/icon-dark.svg": 128,
    **{f"{DOCS}/icon-small-{t}.svg": 64 for t in THEMES},
    **{f"{DOCS}/icon-tiny-{t}.svg": 64 for t in THEMES},
}


def with_px(text: str, px: int) -> str:
    """Give an SVG written by ``svg()`` a width of ``px`` and the height its viewBox implies."""
    head, sep, rest = text.partition('" role="img">')
    view = head.rsplit('viewBox="', 1)[1].split()
    w, h = float(view[2]), float(view[3])
    assert sep and ' width="' not in head, "expected an svg() header without a size"
    return f'{head}" width="{px}" height="{round(px * h / w)}{sep}{rest}'


REVIEW_16PX = "scripts/brand/icon-16px-before-after.png"


def review_16px() -> Image.Image:
    """The 16 px icon at 8x zoom: as drawn (left) and fitted to the pixel grid (right).

    Top row the dark tile, bottom row the light tile, on a mid-grey ground, no smoothing.
    """
    zoom, gap = 8, 8
    cell = 16 * zoom
    out = Image.new("RGBA", (2 * cell + 3 * gap, 2 * cell + 3 * gap), (128, 128, 128, 255))
    for row, dark in enumerate((True, False)):
        for col, fitted in enumerate((False, True)):
            im = icon_16px(dark, fitted).resize((cell, cell), Image.Resampling.NEAREST)
            out.alpha_composite(im, (gap + col * (cell + gap), gap + row * (cell + gap)))
    return out


def install() -> None:
    for src, dst in INSTALL:
        target = REPO / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        if dst in PAGE_PX:
            text = (KIT / src).read_text(encoding="utf-8")
            target.write_text(with_px(text, PAGE_PX[dst]), encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(KIT / src, target)
    review_16px().save(REPO / REVIEW_16PX, optimize=True)
    print(f"installed {len(INSTALL)} files and {REVIEW_16PX}")


def main(argv: list[str] | None = None) -> int:
    global KIT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=KIT, help="folder for the full kit")
    ap.add_argument(
        "--no-install", action="store_true", help="write the kit only; change no repo file"
    )
    args = ap.parse_args(argv)
    KIT = args.out.resolve()
    if KIT.exists():
        if any(KIT.iterdir()) and not (KIT / MARKER).is_file():
            print(f"refusing to replace {KIT}: this script did not write it", file=sys.stderr)
            return 2
        shutil.rmtree(KIT)
    (KIT / "github").mkdir(parents=True)
    (KIT / MARKER).write_text("written by scripts/brand/build.py\n", encoding="utf-8")
    build_marks()
    build_lockups()
    build_app_icons()
    build_document_icon()
    build_installer()
    build_social()
    build_docs()
    build_gui()
    build_tokens()
    for wt in ("Regular", "Medium", "Bold"):
        (KIT / "font").mkdir(exist_ok=True)
        shutil.copy(b.FONT_DIR / f"RedHatDisplay-{wt}.ttf", KIT / "font")
    shutil.copy(b.FONT_DIR / "OFL.txt", KIT / "font/OFL-1.1.txt")
    n = sum(1 for p in KIT.rglob("*") if p.is_file() and p.name != MARKER)
    print(f"wrote {n} files to {KIT}")
    if not args.no_install:
        install()
    return 0


if __name__ == "__main__":
    sys.exit(main())
