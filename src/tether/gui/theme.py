# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Light and dark window themes from one set of colour tokens (#675, PRD §7.3).

This is the only module under :mod:`tether.gui` that holds colour values. Every
other GUI module asks it for a colour by **name**, so the light and the dark theme
differ only in the tokens read here, and ``tests/test_gui_theme.py`` proves that no
other module carries a fixed colour.

Two sources feed it:

* the brand tokens in ``assets/brand/tokens.json`` (#671) — the window, text, border,
  selection and plot tokens of each theme, and the ``accept``/``reject``/``caution``/
  ``info`` state tokens;
* the **data colours** below (:data:`TRACE_PALETTES`), which are not brand colours and
  so are not in ``tokens.json``. ``classic`` keeps today's green/red/blue convention;
  ``safe`` is the colour-blind-safe set that #636 will make the default.

How the theme reaches the window:

* :func:`install` gives the ``QApplication`` the Fusion style, a ``QPalette`` and one
  short style sheet, loads Red Hat Display for headings, and follows the operating
  system's light/dark scheme (``QStyleHints.colorSchemeChanged``) while the mode is
  ``"system"``.
* :func:`set_mode` is what **View → Theme** calls (``system``/``light``/``dark``). The
  choice lasts for the session until the settings store (#330) can keep it.
* Plots and the napari viewer are not drawn from the ``QPalette``, so each dock
  :func:`subscribe`\\ s and re-colours itself; :func:`style_graphics` does the
  pyqtgraph part.

Qt is imported lazily inside the functions that need it, so importing this module
costs no Qt, matching the rest of :mod:`tether.gui`.
"""

from __future__ import annotations

import json
import logging
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pyqtgraph.Qt import QtGui

__all__ = [
    "BRAND_ART",
    "DEFAULT_TRACE_PALETTE",
    "ICON_NAMES",
    "MODES",
    "THEME_NAMES",
    "TRACE_PALETTES",
    "TRACE_TOKENS",
    "TRANSPARENT",
    "Theme",
    "brand_pixmap",
    "contrast_ratio",
    "current",
    "heading_font",
    "icon",
    "icon_renders",
    "install",
    "load_fonts",
    "missing_icons",
    "mode",
    "relative_luminance",
    "rgba",
    "set_mode",
    "style_graphics",
    "subscribe",
    "system_theme_name",
    "theme",
    "unsubscribe",
]

_log = logging.getLogger(__name__)

THEME_NAMES = ("light", "dark")
#: The three items of **View → Theme**. ``system`` follows the operating system.
MODES = ("system", "light", "dark")
TRACE_TOKENS = ("donor", "acceptor", "fret", "total", "idealized")

#: No colour. A named value rather than a literal so other modules hold no colour.
TRANSPARENT = "transparent"

# --- data colours (not brand colours, so not in tokens.json) ----------------------
# Each value reaches 3 : 1 against its theme's plot-background (WCAG 2.x, the
# non-text contrast floor); tests/test_gui_theme.py checks it.


def _palette_row(*values: str) -> Mapping[str, str]:
    return MappingProxyType(dict(zip(TRACE_TOKENS, values, strict=True)))


#: ``{palette: {theme: {token: "#rrggbb"}}}``, tokens in :data:`TRACE_TOKENS` order.
TRACE_PALETTES: Mapping[str, Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {
        # The colours Tether drew before #675 (PRD §7.3, Appendix C D1): donor green
        # (0, 170, 0), acceptor red (220, 45, 45), FRET blue (40, 90, 220), total
        # grey (140, 140, 140). Only the idealized line changes: the old near-black
        # (20, 20, 20) was invisible on a dark plot (#617), so it is ink on light and
        # chalk on dark. Not colour-blind safe (green against red); #636 owns that.
        "classic": {
            "light": _palette_row("#00aa00", "#dc2d2d", "#285adc", "#8c8c8c", "#121417"),
            "dark": _palette_row("#00aa00", "#dc2d2d", "#285adc", "#8c8c8c", "#f2f4f5"),
        },
        # Light: Paul Tol's "bright" set (green, purple, blue; values as published in
        # tol-colors 2.2.0, colorsets.bright). Dark: the Okabe-Ito set (bluish green,
        # reddish purple, sky blue; Wong 2011, doi:10.1038/nmeth.1618; bokeh 3.6.3
        # palettes.Colorblind8). Total and idealized are brand neutrals. The totals
        # proposed in #675 (#68717a light, #8d969c dark) failed the colour-vision check
        # against the acceptor (CIEDE2000 2.0 and 2.5 under simulated deuteranopia), so
        # they were moved to neutral-700 and neutral-200; see docs/reference/gui-theme.md.
        "safe": {
            "light": _palette_row("#228833", "#aa3377", "#4477aa", "#343a40", "#121417"),
            "dark": _palette_row("#009e73", "#cc79a7", "#56b4e9", "#d5dadd", "#f2f4f5"),
        },
    }
)
#: The palette the window draws until #636 makes ``safe`` the default.
DEFAULT_TRACE_PALETTE = "classic"


@cache
def _tokens() -> dict[str, Any]:
    path = resources.files("tether.gui") / "assets" / "brand" / "tokens.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Theme:
    """One resolved theme: its ``name`` and its colours by token name (Qt-free)."""

    name: str
    colors: Mapping[str, str]
    states: Mapping[str, str]
    traces: Mapping[str, Mapping[str, str]]
    trace_palette: str = DEFAULT_TRACE_PALETTE

    def color(self, token: str) -> str:
        """The ``#rrggbb`` value of a window or plot token (``"text"``, ``"plot-axis"``…)."""
        return self.colors[token]

    def state(self, token: str) -> str:
        """The value of a state token: ``accept``, ``reject``, ``caution`` or ``info``."""
        return self.states[token]

    def trace(self, token: str, palette: str | None = None) -> str:
        """The data colour of ``token`` (:data:`TRACE_TOKENS`) in the active palette."""
        return self.traces[palette or self.trace_palette][token]


def theme(name: str, *, trace_palette: str = DEFAULT_TRACE_PALETTE) -> Theme:
    """Build the :class:`Theme` called ``name`` (``"light"`` or ``"dark"``)."""
    if name not in THEME_NAMES:
        raise ValueError(f"theme must be one of {THEME_NAMES}, got {name!r}")
    if trace_palette not in TRACE_PALETTES:
        raise ValueError(f"trace palette must be one of {tuple(TRACE_PALETTES)}")
    tokens = _tokens()
    return Theme(
        name=name,
        colors=MappingProxyType(dict(tokens[name])),
        states=MappingProxyType(dict(tokens["state"][name])),
        traces=MappingProxyType({p: TRACE_PALETTES[p][name] for p in TRACE_PALETTES}),
        trace_palette=trace_palette,
    )


# --- colour arithmetic (WCAG 2.x) -------------------------------------------------


def rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """``#rrggbb`` as an ``(r, g, b, alpha)`` tuple, the form pyqtgraph brushes take."""
    text = value.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), int(alpha))


def relative_luminance(value: str) -> float:
    """WCAG 2.x relative luminance of an sRGB ``#rrggbb`` colour (0 black … 1 white)."""

    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b, _ = rgba(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2.x contrast ratio of two colours, from 1 (same) to 21 (black on white)."""
    hi, lo = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- session state ----------------------------------------------------------------

_state: dict[str, Any] = {"mode": "system", "app": None, "applied": None}
_subscribers: list[Callable[[], Callable[[Theme], None] | None]] = []
#: Where the heading font files are read from (a test points it at an empty folder).
_FONT_DIR = resources.files("tether.gui") / "assets" / "brand" / "fonts"
_FONT_FILES = ("RedHatDisplay-Regular.ttf", "RedHatDisplay-Medium.ttf", "RedHatDisplay-Bold.ttf")
_fonts: dict[str, Any] = {}


def mode() -> str:
    """The selected mode: ``"system"``, ``"light"`` or ``"dark"``."""
    return str(_state["mode"])


def system_theme_name() -> str:
    """``"dark"`` when the operating system reports a dark scheme, else ``"light"``.

    ``QStyleHints.colorScheme`` (Qt 6.5+) answers ``Unknown`` where the platform does
    not say (the offscreen platform, some Linux desktops); that falls back to light.
    """
    from pyqtgraph.Qt import QtCore, QtGui

    app = QtGui.QGuiApplication.instance()
    if app is None:
        return "light"
    scheme = app.styleHints().colorScheme()
    return "dark" if scheme == QtCore.Qt.ColorScheme.Dark else "light"


def current() -> Theme:
    """The active :class:`Theme`, resolved from :func:`mode`."""
    selected = mode()
    return theme(system_theme_name() if selected == "system" else selected)


def subscribe(callback: Callable[[Theme], None]) -> None:
    """Call ``callback(theme)`` after each change of the active theme.

    A bound method is held by weak reference, so subscribing does not keep a dock
    alive; still :func:`unsubscribe` in ``close()``. A plain function is held strongly.
    """

    def strong() -> Callable[[Theme], None]:
        return callback

    bound = hasattr(callback, "__self__")
    _subscribers.append(weakref.WeakMethod(callback) if bound else strong)  # type: ignore[arg-type]


def unsubscribe(callback: Callable[[Theme], None]) -> None:
    """Stop calling ``callback``; a callback that was never subscribed is ignored."""
    _subscribers[:] = [ref for ref in _subscribers if ref() not in (None, callback)]


def _notify(active: Theme) -> None:
    for ref in list(_subscribers):
        callback = ref()
        if callback is None:
            _subscribers.remove(ref)
            continue
        try:
            callback(active)
        except RuntimeError as exc:  # its Qt widget was deleted without unsubscribe
            if "already deleted" not in str(exc):
                raise
            _subscribers.remove(ref)


def set_mode(selected: str) -> Theme:
    """Select ``"system"``, ``"light"`` or ``"dark"`` and re-colour every open window."""
    if selected not in MODES:
        raise ValueError(f"theme mode must be one of {MODES}, got {selected!r}")
    _state["mode"] = selected
    return _refresh()


def _refresh() -> Theme:
    active = current()
    if active.name != _state["applied"]:
        if _state["app"] is not None:
            _apply(active)
        _state["applied"] = active.name
        _notify(active)
    return active


def install(app: Any | None = None) -> Theme:
    """Give the ``QApplication`` the active theme; safe to call more than once.

    Sets the Fusion style (so the three operating systems draw one window), the
    palette and style sheet of :func:`current`, loads the heading font, and connects
    ``QStyleHints.colorSchemeChanged`` so the ``"system"`` mode follows the OS.
    """
    from pyqtgraph.Qt import QtWidgets

    app = app or QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError("theme.install() needs a QApplication")
    active = current()
    if _state["app"] is not app:
        _state["app"] = app
        app.setStyle("Fusion")
        load_fonts()
        app.styleHints().colorSchemeChanged.connect(_on_system_scheme_changed)
        _apply(active)
        _state["applied"] = active.name
        _notify(active)
    return active


def _on_system_scheme_changed(*_args: object) -> None:
    if mode() == "system":
        _refresh()


def _apply(active: Theme) -> None:
    app = _state["app"]
    app.setPalette(_palette(active))
    app.setStyleSheet(_style_sheet(active))


def _palette(active: Theme) -> QtGui.QPalette:
    from pyqtgraph.Qt import QtGui

    role = QtGui.QPalette.ColorRole
    group = QtGui.QPalette.ColorGroup
    c = active.color
    roles = {
        role.Window: c("window"),
        role.WindowText: c("text"),
        role.Base: c("surface"),
        role.AlternateBase: c("window"),
        role.Text: c("text"),
        role.Button: c("surface"),
        role.ButtonText: c("text"),
        role.ToolTipBase: c("surface"),
        role.ToolTipText: c("text"),
        role.PlaceholderText: c("text-muted"),
        role.Highlight: c("selection"),
        role.HighlightedText: c("selection-text"),
        role.Accent: c("selection"),  # Q3: no accent colour; ink marks the selection
        role.Link: active.state("info"),
        role.LinkVisited: active.state("info"),
        role.Light: c("surface"),
        role.Midlight: c("window"),
        role.Mid: c("border"),
        role.Dark: c("border"),
        role.Shadow: c("border"),
    }
    palette = QtGui.QPalette()
    for r, value in roles.items():
        palette.setColor(r, QtGui.QColor(value))
    for r in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(group.Disabled, r, QtGui.QColor(c("text-muted")))
    return palette


def _style_sheet(active: Theme) -> str:
    # Short on purpose (#675): the palette does most of the work. A widget opts in to
    # a state colour with the dynamic property ``tetherRole``.
    caution, reject = active.state("caution"), active.state("reject")
    return (
        f'QLabel[tetherRole="caution"] {{ color: {caution}; border: 1px solid {caution}; '
        "padding: 6px; }\n"
        f'QLabel[tetherRole="error"] {{ color: {reject}; }}\n'
    )


# --- the heading font -------------------------------------------------------------


def load_fonts() -> str | None:
    """Register Red Hat Display with Qt once; return its family, or ``None``.

    A file that is missing or that Qt refuses leaves the heading font at the system
    font, logged in one line; the application starts normally either way.
    """
    if "family" in _fonts:
        return _fonts["family"]
    from pyqtgraph.Qt import QtGui

    family: str | None = None
    failed: list[str] = []
    for name in _FONT_FILES:
        try:
            data = (_FONT_DIR / name).read_bytes()
        except OSError:
            failed.append(name)
            continue
        font_id = QtGui.QFontDatabase.addApplicationFontFromData(data)
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            family = families[0]
        else:
            failed.append(name)
    if failed:
        # One missing weight would leave Qt to fake it from another, so any failure
        # gives the system font, as the warning and the docs say.
        family = None
        _log.warning(
            "Red Hat Display not fully loaded (%s); headings use the system font",
            ", ".join(failed),
        )
    _fonts["family"] = family
    return family


def heading_font(point_size: float | None = None) -> QtGui.QFont:
    """The font for titles and headings: Red Hat Display Medium, else the system font.

    Body text, tables and numbers keep the system font; Red Hat Display is a display
    face (#675, Q4).
    """
    from pyqtgraph.Qt import QtGui

    font = QtGui.QFont(QtGui.QGuiApplication.font())
    family = load_fonts()
    if family is not None:
        font.setFamilies([family])
    font.setWeight(QtGui.QFont.Weight.Medium)
    if point_size is not None:
        font.setPointSizeF(point_size)
    elif font.pointSizeF() > 0:  # -1 when the system font is set in pixels
        font.setPointSizeF(font.pointSizeF() + 1.0)
    return font


# --- pyqtgraph --------------------------------------------------------------------


def style_graphics(graphics: Any, active: Theme | None = None) -> None:
    """Colour a ``pyqtgraph.GraphicsLayoutWidget`` and every plot in it from ``active``.

    Sets the background, the axis lines and labels (``plot-axis``), the grid lines
    (``plot-grid``, at full opacity so the token is the colour seen), the title and
    the legend text. pyqtgraph 0.14 draws the grid with the axis tick pen.
    """
    import pyqtgraph as pg

    active = active or current()
    axis, grid, text = active.color("plot-axis"), active.color("plot-grid"), active.color("text")
    graphics.setBackground(active.color("plot-background"))
    for item in list(graphics.ci.items):
        if not isinstance(item, pg.PlotItem):
            continue
        for name in ("left", "bottom", "right", "top"):
            ax = item.getAxis(name)
            ax.setPen(axis)
            ax.setTextPen(axis)
            if ax.grid is not False:
                ax.setGrid(255)
            ax.setTickPen(grid if ax.grid is not False else axis)
        item.titleLabel.setAttr("color", text)
        if item.titleLabel.text:
            item.titleLabel.setText(item.titleLabel.text)
        if item.legend is not None:
            item.legend.setLabelTextColor(text)
            for _sample, label in item.legend.items:  # setLabelTextColor only stores it
                label.setText(label.text)


# --- interface icons (#677) -------------------------------------------------------

#: The interface icons of the brand kit (#671), one SVG file per theme in
#: ``assets/brand/icons/<theme>/<name>.svg``: eleven section and action icons and the
#: busy mark. Each is ink on the light theme and chalk on the dark theme (Q3).
ICON_NAMES = (
    "project",
    "import",
    "movie",
    "extract",
    "correct",
    "curate",
    "idealize",
    "analyze",
    "conditions",
    "export",
    "hand-off",
    "busy",
)
_ICON_DIR = resources.files("tether.gui") / "assets" / "brand" / "icons"


def icon(name: str, active: Theme | None = None) -> QtGui.QIcon:
    """The interface icon ``name`` (:data:`ICON_NAMES`) for ``active`` (default: current).

    The icon does not follow a later change of theme by itself: a widget that shows
    one :func:`subscribe`\\ s and sets it again, as the shell's menus do. An icon is
    never the only sign of a function; every menu item keeps its text.
    """
    from pyqtgraph.Qt import QtGui

    if name not in ICON_NAMES:
        raise ValueError(f"icon must be one of {ICON_NAMES}, got {name!r}")
    active = active or current()
    return QtGui.QIcon(str(_ICON_DIR / active.name / f"{name}.svg"))


def icon_renders(qicon: QtGui.QIcon, size: int = 24) -> bool:
    """True when ``qicon`` draws at least one visible pixel at ``size`` px.

    ``QIcon.isNull`` is not enough: an icon made from a file name is not null even
    when no Qt plugin can read the file. Without the SVG plugins (``qsvgicon``,
    ``qsvg``) the pixmap comes back empty, and this returns False.
    """
    from pyqtgraph.Qt import QtCore

    if qicon.isNull():
        return False
    image = qicon.pixmap(QtCore.QSize(size, size)).toImage()
    return any(
        image.pixelColor(x, y).alpha() > 0
        for y in range(image.height())
        for x in range(image.width())
    )


def missing_icons() -> list[str]:
    """``theme/name`` of every interface icon that does not render; empty when all do.

    Needs a ``QGuiApplication``. The installer smoke test calls it in the installed
    environment, which proves that the bundle carries Qt's SVG plugins (#677).
    """
    return [
        f"{theme_name}/{icon_name}"
        for theme_name in THEME_NAMES
        for icon_name in ICON_NAMES
        if not icon_renders(icon(icon_name, theme(theme_name)))
    ]


#: The brand drawings a window may show, one SVG per theme (``<name>-<theme>.svg``).
BRAND_ART = ("start-art", "tile")
_BRAND_DIR = resources.files("tether.gui") / "assets" / "brand"


def brand_pixmap(
    name: str,
    width: int,
    active: Theme | None = None,
    *,
    device_pixel_ratio: float = 1.0,
) -> QtGui.QPixmap:
    """The brand drawing ``name`` (:data:`BRAND_ART`) of ``active``, ``width`` px wide (#689).

    The height follows the drawing's view box. The pixmap is drawn at
    ``device_pixel_ratio`` times the size and carries that ratio, so it stays sharp on
    a high-density screen. Like :func:`icon`, it does not follow a later change of
    theme by itself. An unreadable drawing gives a null pixmap, never an exception.
    """
    from pyqtgraph.Qt import QtCore, QtGui, QtSvg

    if name not in BRAND_ART:
        raise ValueError(f"brand art must be one of {BRAND_ART}, got {name!r}")
    active = active or current()
    data = (_BRAND_DIR / f"{name}-{active.name}.svg").read_bytes()
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(data))
    view = renderer.viewBoxF()
    if not renderer.isValid() or view.width() <= 0 or width <= 0:
        return QtGui.QPixmap()
    height = width * view.height() / view.width()
    pixmap = QtGui.QPixmap(
        max(1, round(width * device_pixel_ratio)), max(1, round(height * device_pixel_ratio))
    )
    pixmap.fill(QtGui.QColor(TRANSPARENT))
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap
