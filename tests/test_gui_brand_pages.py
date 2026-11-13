# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The brand on the start page and in the About box (#689).

* **Start page.** With no project open, the page shows the start art (which carries
  the word mark) and the tagline, drawn from the active theme, in the light and the
  dark theme. The art is painted on the page and is no widget, so assistive technology
  never meets it; the page describes the name and the tagline in words instead.
* **About box.** It shows the tile at 96 px, the name and the tagline, the version, the
  two licences, the citation with the concept DOI of ``CITATION.cff``, and links to the
  documentation and the repository. **Copy** copies that text and never the picture.

All ``pytest-qt`` and headless, on a real :class:`TetherShell`.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui import theme
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    s.window.resize(900, 760)
    yield s
    s.close()
    theme.set_mode("system")


def _visible(image) -> bool:
    return any(
        image.pixelColor(x, y).alpha() > 0
        for y in range(0, image.height(), 4)
        for x in range(0, image.width(), 4)
    )


# --- the drawings ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["light", "dark"])
@pytest.mark.parametrize("art", ["start-art", "tile"])
def test_each_brand_drawing_renders_in_each_theme(qapp, art: str, name: str) -> None:
    from tether.gui import theme

    pixmap = theme.brand_pixmap(art, 96, theme.theme(name), device_pixel_ratio=2.0)
    assert not pixmap.isNull()
    assert pixmap.deviceIndependentSize().width() == pytest.approx(96)
    assert pixmap.width() == 192  # drawn at twice the size for a dense screen
    assert _visible(pixmap.toImage())


def test_an_unknown_brand_drawing_is_refused() -> None:
    from tether.gui import theme

    with pytest.raises(ValueError, match="brand art"):
        theme.brand_pixmap("splash", 96)


# --- the start page ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["light", "dark"])
def test_the_start_page_shows_the_art_and_the_tagline(shell, name: str) -> None:
    from tether.gui import theme
    from tether.gui.start_page import ART_WIDTH, TAGLINE

    theme.set_mode(name)
    page = shell.start_page
    assert shell.start_page_visible
    assert page.tagline.text() == TAGLINE == "Provenance-first single-molecule FRET analysis"
    art = page.art_pixmap()
    assert art.toImage() == theme.brand_pixmap("start-art", ART_WIDTH, theme.theme(name)).toImage()
    # The art reaches the screen: the painted page carries its pixels in the art rect.
    page.widget.resize(800, 700)
    rect = page.art_rect()
    assert rect.width() == ART_WIDTH
    shot = page.widget.grab().toImage()
    x, y = int(rect.left()) + 3, int(rect.top()) + 3
    assert shot.pixelColor(x, y) == art.toImage().pixelColor(3, 3)


def test_the_art_follows_a_change_of_theme(shell) -> None:
    from tether.gui import theme

    theme.set_mode("light")
    page = shell.start_page
    light_art = page.art_pixmap().toImage()
    theme.set_mode("dark")
    assert page.art_pixmap().toImage() != light_art


def test_the_art_is_hidden_from_assistive_technology(shell) -> None:
    from pyqtgraph.Qt import QtGui, QtWidgets

    from tether.gui.start_page import TAGLINE

    page = shell.start_page
    pictures = []
    for child in page.widget.findChildren(QtWidgets.QWidget):
        interface = QtGui.QAccessible.queryAccessibleInterface(child)
        if interface is not None and interface.role() == QtGui.QAccessible.Role.Graphic:
            pictures.append(child)
    assert pictures == []  # the art is painted, so no picture widget carries it
    page_interface = QtGui.QAccessible.queryAccessibleInterface(page.widget)
    described = page_interface.text(QtGui.QAccessible.Text.Description)
    assert described == f"Tether. {TAGLINE}."


def test_a_narrow_window_draws_the_art_smaller(shell) -> None:
    from tether.gui.start_page import ART_WIDTH

    page = shell.start_page
    page.widget.resize(300, 700)
    rect = page.art_rect()
    assert rect.width() < ART_WIDTH
    assert rect.left() >= 0
    assert rect.right() <= 300
    assert rect.height() == pytest.approx(rect.width() / 2)


# --- the About box -------------------------------------------------------------------


def test_the_about_box_shows_the_tile_version_licences_and_doi(shell) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether import __version__
    from tether.gui import about

    box = shell.show_about()
    try:
        tile = box.tile.pixmap()
        assert not tile.isNull()
        assert tile.deviceIndependentSize().width() == pytest.approx(about.TILE_SIZE) == 96
        rows = dict(box.rows)
        assert rows["Version"] == __version__
        assert "GPL-3.0-or-later" in rows["License"]
        assert "CC BY 4.0" in rows["License"]
        assert about.CONCEPT_DOI in rows["Cite"]
        assert rows["Documentation"] == about.docs_url()
        assert rows["Source code"] == about.REPOSITORY_URL
        shown = [label.text() for label in box.dialog.findChildren(QtWidgets.QLabel)]
        assert "Provenance-first single-molecule FRET analysis" in shown
    finally:
        box.dialog.close()


def test_copy_copies_the_text_and_not_the_picture(shell) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether import __version__
    from tether.gui import about

    box = shell.show_about()
    try:
        box.copy_button.click()
        clipboard = QtWidgets.QApplication.clipboard()
        copied = clipboard.text()
        assert copied == box.text()
        for piece in (
            f"Version: {__version__}",
            "GPL-3.0-or-later",
            "CC BY 4.0",
            f"https://doi.org/{about.CONCEPT_DOI}",
            about.REPOSITORY_URL,
            "Provenance-first single-molecule FRET analysis",
        ):
            assert piece in copied
        assert not clipboard.mimeData().hasImage()
    finally:
        box.dialog.close()


def test_the_about_links_open_in_the_browser(shell, monkeypatch) -> None:
    from pyqtgraph.Qt import QtCore, QtWidgets

    from tether.gui import about

    opened: list[str] = []
    monkeypatch.setattr(about, "open_external", lambda url: opened.append(url) or True)
    box = shell.show_about()
    try:
        links = [
            label
            for label in box.dialog.findChildren(QtWidgets.QLabel)
            if label.textFormat() == QtCore.Qt.TextFormat.RichText
        ]
        assert len(links) == 2
        for label in links:
            label.linkActivated.emit(re.search(r'href="([^"]+)"', label.text()).group(1))
        assert opened == [about.docs_url(), about.REPOSITORY_URL]
    finally:
        box.dialog.close()


def test_the_doi_and_the_repository_are_the_ones_citation_cff_names() -> None:
    from tether.gui import about

    cff = (_REPO / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(r'^doi:\s*"([^"]+)"', cff, re.MULTILINE).group(1) == about.CONCEPT_DOI
    repo = re.search(r'^repository-code:\s*"([^"]+)"', cff, re.MULTILINE).group(1)
    assert repo == about.REPOSITORY_URL
    citing = (_REPO / "docs" / "citing.md").read_text(encoding="utf-8")
    assert "Campos-Chávez E, Mondragon A. *Tether*. Zenodo." in citing
    assert f"https://doi.org/{about.CONCEPT_DOI}" in citing
