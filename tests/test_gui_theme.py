# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the light and dark window themes (#675, PRD §7.3).

Three layers:

* **Tokens (no Qt).** Every text token reaches 4.5 : 1 and every data-colour token
  3 : 1 against its background (WCAG 2.x), in both themes and both trace palettes;
  the ``safe`` palette stays distinguishable under simulated protanopia,
  deuteranopia and tritanopia.
* **No fixed colour.** An AST scan of ``src/tether/gui`` finds no colour value
  outside ``tether.gui.theme``; the scan is shown to catch the lines it replaced.
* **``pytest-qt``, headless.** The application starts in the scheme the system
  reports, **View → Theme** re-colours the window, the plots and the napari viewer
  of an open shell, section titles use Red Hat Display, and a missing font file
  leaves a working shell on the system font.

The colour-vision check uses the Machado, Oliveira & Fernandes (2009) severity-1.0
matrices (doi:10.1109/TVCG.2009.113; values as in colorspacious 1.1.2,
``cvd.machado_et_al_2009_matrix``) and CIEDE2000 (Sharma, Wu & Dalal 2005,
doi:10.1002/col.20070). The floor of 10 CIEDE2000 units between two colours drawn in
one plot is the hard constraint Palettailor uses for categorical palettes (Lu et al.
2021, doi:10.1109/TVCG.2020.3030406). It is a design floor chosen here, not a law of
perception.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

from tether.gui import theme

_GUI = Path(__file__).resolve().parents[1] / "src" / "tether" / "gui"
_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")
_HAS_NAPARI = _HAS_QT and importlib.util.find_spec("napari") is not None
# Same gate as test_movie_panel: macOS offscreen Qt has no GL context for napari.
_NO_GL = sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "offscreen"

TEXT_FLOOR = 4.5  # WCAG 2.x, 1.4.3 (text)
LINE_FLOOR = 3.0  # WCAG 2.x, 1.4.11 (graphical objects)
CVD_FLOOR = 10.0  # CIEDE2000 between two colours drawn in one plot


# --- tokens and contrast (no Qt) ---------------------------------------------


def test_contrast_ratio_matches_the_wcag_end_points() -> None:
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert theme.rgba("#4477aa", 120) == (0x44, 0x77, 0xAA, 120)


@pytest.mark.parametrize("name", theme.THEME_NAMES)
def test_every_text_token_reaches_4_5_to_1(name: str) -> None:
    t = theme.theme(name)
    pairs = [
        ("text", "window"),
        ("text", "surface"),
        ("text-muted", "window"),
        ("text-muted", "surface"),
        ("selection-text", "selection"),
        ("plot-axis", "plot-background"),  # axis labels and tick numbers are text
    ]
    for fg, bg in pairs:
        assert theme.contrast_ratio(t.color(fg), t.color(bg)) >= TEXT_FLOOR, (name, fg, bg)
    # The state tokens are text: the read-only banner (caution) and the error line.
    for state in ("accept", "reject", "caution", "info"):
        for bg in ("window", "surface"):
            ratio = theme.contrast_ratio(t.state(state), t.color(bg))
            assert ratio >= TEXT_FLOOR, (name, state, bg)
    # plot-grid and border are decoration, deliberately faint; they are not tested.


@pytest.mark.parametrize("palette", list(theme.TRACE_PALETTES))
@pytest.mark.parametrize("name", theme.THEME_NAMES)
def test_every_trace_token_reaches_3_to_1(name: str, palette: str) -> None:
    t = theme.theme(name)
    for token in theme.TRACE_TOKENS:
        ratio = theme.contrast_ratio(t.trace(token, palette), t.color("plot-background"))
        assert ratio >= LINE_FLOOR, (name, palette, token, round(ratio, 2))


# Machado et al. (2009), severity 1.0, applied to linear sRGB.
_MACHADO = {
    "protanopia": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deuteranopia": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
    "tritanopia": [
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ],
}
_SRGB_TO_XYZ = np.array(  # IEC 61966-2-1, D65
    [[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]
)


def _lab(value: str, vision: str) -> tuple[float, float, float]:
    srgb = np.array(theme.rgba(value)[:3]) / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    if vision != "normal":
        linear = np.clip(np.array(_MACHADO[vision]) @ linear, 0.0, 1.0)
    xyz = _SRGB_TO_XYZ @ linear / _SRGB_TO_XYZ.sum(axis=1)  # relative to the D65 white

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = (f(float(v)) for v in xyz)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _ciede2000(lab1: tuple[float, ...], lab2: tuple[float, ...]) -> float:
    """CIEDE2000 colour difference (Sharma, Wu & Dalal 2005, eqs. 1-22)."""
    (l1, a1, b1), (l2, a2, b2) = lab1, lab2
    c_bar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dh = h2p - h1p
    if c1p * c2p == 0:
        dh = 0.0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    d_l, d_c = l2 - l1, c2p - c1p
    d_h = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    l_bar, cp_bar = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_bar = (h1p + h2p) / 2
    else:
        h_bar = (h1p + h2p + (360 if h1p + h2p < 360 else -360)) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(h_bar - 30))
        + 0.24 * math.cos(math.radians(2 * h_bar))
        + 0.32 * math.cos(math.radians(3 * h_bar + 6))
        - 0.20 * math.cos(math.radians(4 * h_bar - 63))
    )
    d_theta = 30 * math.exp(-(((h_bar - 275) / 25) ** 2))
    r_c = 2 * math.sqrt(cp_bar**7 / (cp_bar**7 + 25**7))
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / math.sqrt(20 + (l_bar - 50) ** 2)
    s_c, s_h = 1 + 0.045 * cp_bar, 1 + 0.015 * cp_bar * t
    r_t = -math.sin(math.radians(2 * d_theta)) * r_c
    return math.sqrt(
        (d_l / s_l) ** 2 + (d_c / s_c) ** 2 + (d_h / s_h) ** 2 + r_t * (d_c / s_c) * (d_h / s_h)
    )


@pytest.mark.parametrize(
    ("lab1", "lab2", "expected"),
    [  # Sharma, Wu & Dalal (2005), Table 1, pairs 1, 7, 17 and 18
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((50.0, 2.5, 0.0), (61.0, -5.0, 29.0), 22.8977),
    ],
)
def test_ciede2000_reproduces_the_published_test_pairs(lab1, lab2, expected) -> None:
    assert _ciede2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)


# The colours drawn in one plot: the intensity plot holds donor, acceptor and total;
# the FRET plot holds FRET and the idealized line.
_SAME_PLOT = [
    ("donor", "acceptor"),
    ("donor", "total"),
    ("acceptor", "total"),
    ("fret", "idealized"),
]


def _worst_pair(colours: dict[str, str]) -> tuple[float, str]:
    return min(
        (_ciede2000(_lab(colours[a], vision), _lab(colours[b], vision)), f"{vision} {a}/{b}")
        for vision in ("normal", *_MACHADO)
        for a, b in _SAME_PLOT
    )


@pytest.mark.parametrize("name", theme.THEME_NAMES)
def test_safe_palette_stays_distinct_under_colour_vision_deficiency(name: str) -> None:
    worst, where = _worst_pair(dict(theme.TRACE_PALETTES["safe"][name]))
    assert worst >= CVD_FLOOR, (name, where, round(worst, 1))


def test_the_check_rejects_the_totals_first_proposed_in_the_issue() -> None:
    # #675 proposed #68717a (light) and #8d969c (dark) for "total"; under simulated
    # deuteranopia each is almost the acceptor's colour, so the check must fail them.
    for name, total in (("light", "#68717a"), ("dark", "#8d969c")):
        colours = dict(theme.TRACE_PALETTES["safe"][name], total=total)
        worst, where = _worst_pair(colours)
        assert worst < 3.0 and where == "deuteranopia acceptor/total", (name, worst, where)


# --- no fixed colour outside the theme module --------------------------------

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_CSS_COLOUR = re.compile(
    r"(?:color|background|border)[\w-]*\s*:[^;{}]*(?:#[0-9a-fA-F]{3,8}\b|rgba?\(|"
    r"\b(?:red|green|blue|black|white|gr[ae]y|yellow|orange)\b)",
    re.IGNORECASE,
)
_COLOUR_CALLS = {"mkPen", "mkBrush", "mkColor", "QColor", "setBackground", "setForeground"}
_COLOUR_KWARGS = {
    "pen",
    "brush",
    "color",
    "colour",
    "face_color",
    "border_color",
    "edge_color",
    "background",
    "foreground",
    "symbolPen",
    "symbolBrush",
    "fillBrush",
}


def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.Tuple):
        return bool(node.elts) and all(_is_literal(e) for e in node.elts)
    return False


def _fixed_colours(source: str) -> list[str]:
    """Every fixed colour value in ``source``, as ``"line: reason"`` strings."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _HEX.fullmatch(node.value) or _CSS_COLOUR.search(node.value):
                found.append(f"{line}: colour string {node.value!r}")
        elif isinstance(node, ast.Tuple) and 3 <= len(node.elts) <= 4:
            values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            if len(values) == len(node.elts) and all(
                type(v) is int and 0 <= v <= 255 for v in values
            ):
                found.append(f"{line}: RGB tuple {tuple(values)}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _COLOUR_CALLS and any(_is_literal(a) for a in node.args):
                found.append(f"{line}: {name}() with a literal colour")
            for kw in node.keywords:
                if kw.arg in _COLOUR_KWARGS and _is_literal(kw.value):
                    found.append(f"{line}: {kw.arg}= literal colour")
        elif isinstance(node, ast.Attribute) and node.attr == "GlobalColor":
            found.append(f"{line}: Qt.GlobalColor")
    return found


def test_no_gui_module_outside_the_theme_holds_a_fixed_colour() -> None:
    offenders = {
        str(path.relative_to(_GUI)): hits
        for path in sorted(_GUI.rglob("*.py"))
        if path.name != "theme.py" and (hits := _fixed_colours(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


@pytest.mark.parametrize(
    "line",
    [  # the lines #675 replaced, as they stood on main at 4094720
        "_DONOR_RGB = (0, 170, 0)",
        '_DONOR_HEX = "#00aa00"',
        "bars = pg.BarGraphItem(x0=0.0, brush=(120, 120, 120, 160))",
        'label.setStyleSheet("QLabel { background: #fff3cd; color: #664d03; }")',
        "pen = pg.mkPen((40, 90, 220), width=1)",
        "c = QtCore.Qt.GlobalColor.red",
    ],
)
def test_the_scan_catches_the_colours_it_replaced(line: str) -> None:
    assert _fixed_colours(line)


def test_the_scan_ignores_issue_numbers_and_colour_map_names() -> None:
    assert _fixed_colours('"""Fixes #617 and #636."""\nadd_image(d, colormap="gray")') == []


# --- the running window (pytest-qt, headless) --------------------------------


@pytest.fixture
def system_scheme(monkeypatch):
    """Set what the 'operating system' reports; restore the session theme after."""
    reported = {"name": "light"}
    monkeypatch.setattr(theme, "system_theme_name", lambda: reported["name"])
    yield reported
    theme.set_mode("system")
    monkeypatch.undo()
    theme.set_mode("system")


def _background(graphics) -> str:
    return graphics.backgroundBrush().color().name()


def _window_colour(app) -> str:
    from pyqtgraph.Qt import QtGui

    return app.palette().color(QtGui.QPalette.ColorRole.Window).name()


@_needs_qt
@pytest.mark.parametrize("scheme", theme.THEME_NAMES)
def test_the_application_starts_in_the_system_scheme(qapp, qtbot, system_scheme, scheme) -> None:
    from tether.gui.shell import TetherShell

    system_scheme["name"] = scheme
    theme.set_mode("system")
    with TetherShell() as shell:
        qtbot.addWidget(shell.window)
        expected = theme.theme(scheme)
        sheet = qapp.styleSheet()  # a style sheet wraps the style; look beneath it
        qapp.setStyleSheet("")
        assert qapp.style().name().lower() == "fusion"
        qapp.setStyleSheet(sheet)
        assert _window_colour(qapp) == expected.color("window")
        assert _background(shell.trace_dock.graphics) == expected.color("plot-background")
        assert shell.theme_actions["system"].isChecked()


@_needs_qt
def test_the_system_mode_follows_a_change_of_the_os_scheme(qapp, qtbot, system_scheme) -> None:
    from tether.gui.shell import TetherShell

    theme.set_mode("system")
    with TetherShell() as shell:
        qtbot.addWidget(shell.window)
        from pyqtgraph.Qt import QtCore

        system_scheme["name"] = "dark"
        qapp.styleHints().colorSchemeChanged.emit(QtCore.Qt.ColorScheme.Dark)
        assert _background(shell.trace_dock.graphics) == theme.theme("dark").color(
            "plot-background"
        )


@_needs_qt
def test_view_theme_recolours_the_open_window_and_plots(qapp, qtbot, system_scheme) -> None:
    from pyqtgraph.Qt import QtGui

    from tether.gui.histogram_dock import HistogramDock
    from tether.gui.overlap_dock import OverlapDock
    from tether.gui.shell import TetherShell

    with TetherShell() as shell, HistogramDock() as hist, OverlapDock() as overlap:
        qtbot.addWidget(shell.window)
        dock = shell.trace_dock
        for name in ("dark", "light", "dark"):
            shell.theme_actions[name].trigger()
            t = theme.theme(name)
            assert theme.mode() == name
            assert _window_colour(qapp) == t.color("window")
            for graphics in (dock.graphics, hist.graphics, overlap.graphics):
                assert _background(graphics) == t.color("plot-background")
            axis = dock.fret_plot.getAxis("left")
            assert axis.textPen().color().name() == t.color("plot-axis")
            pens = {
                "donor": dock.donor_curve.opts["pen"],
                "acceptor": dock.acceptor_curve.opts["pen"],
                "idealized": dock.idealization_curve.opts["pen"],
            }
            for token, pen in pens.items():
                assert QtGui.QPen(pen).color().name() == t.trace(token)
            assert hist.curve.opts["pen"].color().name() == t.trace("fret")
        shell.theme_actions["light"].trigger()
        for graphics in (dock.graphics, hist.graphics, overlap.graphics):
            assert _background(graphics) != "#000000"  # no black plot in the light theme


@_needs_qt
def test_the_idealized_line_is_clearly_visible_in_both_themes() -> None:
    # #617: the old near-black (20, 20, 20) line on pyqtgraph's black plot was 1.1 : 1.
    for name in theme.THEME_NAMES:
        t = theme.theme(name)
        assert theme.contrast_ratio(t.trace("idealized"), t.color("plot-background")) > 15


@_needs_qt
def test_state_roles_take_the_state_tokens(qapp, qtbot, system_scheme) -> None:
    from pyqtgraph.Qt import QtGui

    from tether.gui.shell import TetherShell

    with TetherShell() as shell:
        qtbot.addWidget(shell.window)
        for name in theme.THEME_NAMES:
            theme.set_mode(name)
            banner = shell.read_only_banner
            banner.setText("Read-only project: test")
            banner.style().unpolish(banner)
            banner.style().polish(banner)
            colour = banner.palette().color(QtGui.QPalette.ColorRole.WindowText).name()
            assert colour == theme.theme(name).state("caution")


@_needs_qt
@pytest.mark.skipif(not _HAS_NAPARI or _NO_GL, reason="napari Viewer needs Qt + a GL context")
def test_the_napari_viewer_follows_the_theme(qtbot, system_scheme) -> None:
    from tether.gui.movie_panel import MovieOverlay, NapariMoviePanel
    from tether.io.movie import open_movie

    fixture = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"
    overlay = MovieOverlay(donor_xy=[[10.0, 12.0]], acceptor_xy=[[40.0, 12.0]])
    with open_movie(fixture) as reader, NapariMoviePanel() as panel:
        panel.add_movie(reader, overlay=overlay)
        for name in ("dark", "light"):
            theme.set_mode(name)
            t = theme.theme(name)
            assert panel.viewer.theme == name
            donor = panel.donor_layer.face_color[0][:3]
            ring = panel.acceptor_aperture_layer.border_color[0][:3]
            assert np.allclose(donor * 255, theme.rgba(t.trace("donor"))[:3], atol=0.5)
            assert np.allclose(ring * 255, theme.rgba(t.trace("acceptor"))[:3], atol=0.5)


@_needs_qt
def test_section_titles_use_red_hat_display(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtGui

    from tether.gui.shell import TetherShell

    assert theme.load_fonts() == "Red Hat Display"
    with TetherShell() as shell:
        qtbot.addWidget(shell.window)
        for label in shell.section_titles:
            assert QtGui.QFontInfo(label.font()).family() == "Red Hat Display"


@_needs_qt
def test_a_missing_font_file_leaves_the_system_font(qapp, qtbot, monkeypatch, tmp_path, caplog):
    from pyqtgraph.Qt import QtGui

    from tether.gui.shell import TetherShell

    monkeypatch.setattr(theme, "_FONT_DIR", tmp_path)  # an empty folder
    monkeypatch.setattr(theme, "_fonts", {})
    with caplog.at_level(logging.WARNING, logger="tether.gui.theme"):
        assert theme.load_fonts() is None
    assert len(caplog.records) == 1 and "system font" in caplog.records[0].getMessage()
    system = QtGui.QGuiApplication.font().families()
    assert theme.heading_font().families() == system
    with TetherShell() as shell:  # starts normally
        qtbot.addWidget(shell.window)
        assert shell.section_titles[0].text() == "Movie"


@_needs_qt
def test_one_missing_font_file_also_leaves_the_system_font(qapp, monkeypatch, tmp_path, caplog):
    """A partial load must not keep Red Hat Display: the warning says the system font."""
    from pyqtgraph.Qt import QtGui

    regular = theme._FONT_FILES[0]
    (tmp_path / regular).write_bytes((theme._FONT_DIR / regular).read_bytes())
    monkeypatch.setattr(theme, "_FONT_DIR", tmp_path)  # only the first of the three files
    monkeypatch.setattr(theme, "_fonts", {})
    with caplog.at_level(logging.WARNING, logger="tether.gui.theme"):
        assert theme.load_fonts() is None
    assert len(caplog.records) == 1 and "system font" in caplog.records[0].getMessage()
    assert theme.heading_font().families() == QtGui.QGuiApplication.font().families()
