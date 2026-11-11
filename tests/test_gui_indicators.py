# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the interface icons, the busy indicator and the progress row (#677).

All ``pytest-qt``, headless:

* every interface icon of the brand kit renders in both themes (so Qt's SVG plugins
  are present), ink on light and chalk on dark, and the shell's menu items show
  their icon beside their text and change it with the theme;
* the busy indicator shows the task name while the one-click idealize runs, lights
  the wavefronts in turn, stops with the task, and stays still under reduced motion;
* the progress row fills its dots from left to right and assistive technology reads
  its value through ``QAccessibleValueInterface``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from tether.gui import indicators, theme

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_ICONS = (
    Path(__file__).resolve().parents[1] / "src" / "tether" / "gui" / "assets" / "brand" / "icons"
)


@pytest.fixture
def scheme(monkeypatch):
    """Set what the 'operating system' reports; restore the session theme after."""
    reported = {"name": "light"}
    monkeypatch.setattr(theme, "system_theme_name", lambda: reported["name"])
    yield reported
    theme.set_mode("system")
    monkeypatch.undo()
    theme.set_mode("system")


def _lightness(qicon) -> float:
    """Mean lightness (0 black … 1 white) of the opaque pixels of a 24 px icon."""
    from pyqtgraph.Qt import QtCore

    image = qicon.pixmap(QtCore.QSize(24, 24)).toImage()
    values = [
        image.pixelColor(x, y).lightnessF()
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 128
    ]
    assert values, "the icon drew no opaque pixel"
    return float(np.mean(values))


# --- the icon loader ----------------------------------------------------------


@pytest.mark.parametrize("name", theme.THEME_NAMES)
def test_the_icon_names_are_the_files_of_the_kit(name: str) -> None:
    on_disk = sorted(p.stem for p in (_ICONS / name).glob("*.svg"))
    assert on_disk == sorted(theme.ICON_NAMES)


def test_every_icon_renders_in_both_themes(qapp) -> None:
    # A null or empty icon here means the SVG plugins are missing from the build.
    assert theme.missing_icons() == []


def test_an_icon_is_ink_on_light_and_chalk_on_dark(qapp) -> None:
    for name in theme.ICON_NAMES:
        assert _lightness(theme.icon(name, theme.theme("light"))) < 0.3, name
        assert _lightness(theme.icon(name, theme.theme("dark"))) > 0.7, name


def test_an_unknown_icon_name_is_refused(qapp) -> None:
    with pytest.raises(ValueError, match="icon must be one of"):
        theme.icon("settings")


def test_icon_renders_is_false_for_an_icon_that_draws_nothing(qapp, tmp_path) -> None:
    from pyqtgraph.Qt import QtGui

    blank = tmp_path / "blank.svg"
    blank.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>')
    assert not theme.icon_renders(QtGui.QIcon(str(blank)))
    assert not theme.icon_renders(QtGui.QIcon(str(tmp_path / "absent.svg")))
    assert not theme.icon_renders(QtGui.QIcon())


# --- the menus of the shell ----------------------------------------------------


def test_each_menu_item_shows_its_icon_and_its_text(qapp, qtbot, scheme) -> None:
    from tether.gui.shell import TetherShell

    theme.set_mode("light")
    with TetherShell() as shell:
        qtbot.addWidget(shell.window)
        names = {name for _action, name in shell.menu_icons}
        assert names == {"project", "curate", "hand-off", "import", "analyze", "conditions"}
        for action, name in shell.menu_icons:
            assert action.text().strip("&"), name  # the text stays
            assert theme.icon_renders(action.icon()), name
            assert _lightness(action.icon()) < 0.3, name
        theme.set_mode("dark")
        for action, name in shell.menu_icons:
            assert _lightness(action.icon()) > 0.7, name
        theme.set_mode("light")
        for action, name in shell.menu_icons:
            assert _lightness(action.icon()) < 0.3, name


def test_a_closed_shell_no_longer_follows_the_theme(qapp, qtbot, scheme) -> None:
    from tether.gui.shell import TetherShell

    theme.set_mode("light")
    shell = TetherShell()
    qtbot.addWidget(shell.window)
    action, _name = shell.menu_icons[0]
    shell.close()
    theme.set_mode("dark")
    assert _lightness(action.icon()) < 0.3


# --- the busy indicator ----------------------------------------------------------


def test_the_busy_indicator_lights_the_wavefronts_in_turn(qapp, qtbot, monkeypatch) -> None:
    monkeypatch.setattr(indicators, "prefers_reduced_motion", lambda: False)
    busy = indicators.BusyIndicator()
    qtbot.addWidget(busy)
    assert busy.isHidden() and not busy.is_running()
    busy.start("Idealizing mol_0")
    assert busy.is_running() and not busy.isHidden()
    assert busy.label.text() == "Idealizing mol_0"
    assert busy.mark.accessibleName() == "Idealizing mol_0"
    assert busy.mark.is_animating()
    assert busy.mark.wavefront_opacities()[0] == 1.0
    assert min(busy.mark.wavefront_opacities()) < 1.0  # one lit, two dim
    qtbot.waitUntil(lambda: busy.mark.phase == 1, timeout=3000)
    qtbot.waitUntil(lambda: busy.mark.phase == 2, timeout=3000)
    busy.stop()
    assert not busy.is_running() and busy.isHidden()
    assert not busy.mark.is_animating()
    assert busy.label.text() == ""


def test_the_busy_mark_is_an_indeterminate_progress_bar(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtGui

    busy = indicators.BusyIndicator()
    qtbot.addWidget(busy)
    busy.show()
    busy.start("Idealizing mol_0")
    iface = QtGui.QAccessible.queryAccessibleInterface(busy.mark)
    assert iface.role() == QtGui.QAccessible.Role.ProgressBar
    value = iface.valueInterface()
    assert value.minimumValue() == 0 and value.maximumValue() == 0
    assert iface.text(QtGui.QAccessible.Text.Name) == "Idealizing mol_0"
    busy.stop()


def test_under_reduced_motion_the_busy_mark_stays_still(qapp, qtbot, monkeypatch) -> None:
    monkeypatch.setattr(indicators, "prefers_reduced_motion", lambda: True)
    busy = indicators.BusyIndicator()
    qtbot.addWidget(busy)
    busy.start("Idealizing mol_0")
    assert busy.label.text() == "Idealizing mol_0"
    assert not busy.mark.is_animating()
    assert busy.mark.wavefront_opacities() == (1.0, 1.0, 1.0)  # the still mark
    qtbot.wait(800)  # three steps of the sequence
    assert busy.mark.phase == 0
    busy.stop()


def test_the_busy_mark_is_drawn_in_the_ink_of_the_theme(qapp, qtbot, scheme) -> None:
    theme.install(qapp)
    mark = indicators.BusyMark()
    mark.setFixedSize(48, 48)
    qtbot.addWidget(mark)

    def darkest_and_lightest() -> tuple[float, float]:
        image = mark.grab().toImage()
        values = [
            image.pixelColor(x, y).lightnessF()
            for y in range(image.height())
            for x in range(image.width())
        ]
        return min(values), max(values)

    theme.set_mode("light")
    assert darkest_and_lightest()[0] < 0.2  # ink marks on a light ground
    theme.set_mode("dark")
    assert darkest_and_lightest()[1] > 0.8  # chalk marks on a dark ground


def test_the_one_click_idealize_shows_the_busy_indicator(qapp, qtbot, monkeypatch) -> None:
    from tether.gui.shell import TetherShell
    from tether.gui.trace_dock import TraceView

    monkeypatch.setattr(indicators, "prefers_reduced_motion", lambda: False)
    rng = np.random.default_rng(0)
    traces = [TraceView(rng.random(20), rng.random(20), molecule_key=f"mol_{i}") for i in range(2)]
    release = threading.Event()

    def slow_idealizer(_key: str):
        release.wait(10)
        return np.full(20, 0.5)

    with TetherShell(idealizer=slow_idealizer) as shell:
        qtbot.addWidget(shell.window)
        shell.set_molecules(traces)
        busy = shell.busy_indicator
        assert not busy.is_running()
        shell._idealize_current()
        try:
            assert busy.is_running()
            assert busy.task_name == "Idealizing mol_0"
            assert busy.mark.is_animating()
        finally:
            release.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)
        assert not busy.is_running() and busy.isHidden()
        assert not busy.mark.is_animating()


# --- the progress row --------------------------------------------------------------


def test_the_progress_row_fills_its_dots_from_left_to_right(qapp, qtbot) -> None:
    row = indicators.ProgressRow()
    qtbot.addWidget(row)
    assert row.dot_count == 20
    for value, filled in ((0, 0), (4, 0), (5, 1), (37, 7), (99, 19), (100, 20)):
        row.setValue(value)
        assert row.filled_dots() == filled, value
    row.setRange(0, 0)  # an indeterminate range fills nothing
    assert row.filled_dots() == 0


def test_the_progress_row_shows_the_number(qapp, qtbot, scheme) -> None:
    theme.set_mode("light")
    row = indicators.ProgressRow()
    qtbot.addWidget(row)
    row.resize(400, 24)
    row.setValue(37)
    assert row.text() == "37%"
    image = row.grab().toImage()
    text_width = row.fontMetrics().horizontalAdvance("100%") + 8
    row_width = row.width() - text_width
    centres = [c for c, _r in row._dots(row_width, row.height())]
    first, last = centres[0], centres[-1]
    filled = image.pixelColor(int(first.x()), int(first.y())).lightnessF()
    unfilled = image.pixelColor(int(last.x()), int(last.y())).lightnessF()
    assert filled < unfilled  # the first dot is ink, the last is dim
    # The number is drawn at the right, beside the dots.
    right = [
        image.pixelColor(x, y).lightnessF()
        for x in range(row_width, row.width())
        for y in range(row.height())
    ]
    assert min(right) < 0.5


def test_assistive_technology_reads_the_progress_value(qapp, qtbot) -> None:
    from pyqtgraph.Qt import QtGui

    row = indicators.ProgressRow()
    qtbot.addWidget(row)
    row.show()
    row.setValue(42)
    iface = QtGui.QAccessible.queryAccessibleInterface(row)
    assert iface.role() == QtGui.QAccessible.Role.ProgressBar
    value = iface.valueInterface()
    assert value.currentValue() == 42
    assert value.maximumValue() == 100
    assert iface.text(QtGui.QAccessible.Text.Value) == "42"


# --- reduced motion: the operating-system setting ------------------------------------


def test_prefers_reduced_motion_answers_on_this_platform() -> None:
    assert isinstance(indicators.prefers_reduced_motion(), bool)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows setting")
def test_the_windows_setting_is_read_without_error() -> None:
    assert isinstance(indicators._windows_reduced_motion(), bool)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS setting")
def test_the_macos_setting_is_read_without_error() -> None:
    assert isinstance(indicators._macos_reduced_motion(), bool)


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected"),
    [("false\n", 0, True), ("true\n", 0, False), ("", 1, False)],
)
def test_the_gnome_setting_turns_animation_off(monkeypatch, stdout, returncode, expected) -> None:
    seen: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(indicators.shutil, "which", lambda _name: "/usr/bin/gsettings")
    monkeypatch.setattr(indicators.subprocess, "run", fake_run)
    assert indicators._gnome_reduced_motion() is expected
    assert seen == [
        ["/usr/bin/gsettings", "get", "org.gnome.desktop.interface", "enable-animations"]
    ]


def test_without_gsettings_the_mark_moves(monkeypatch) -> None:
    monkeypatch.setattr(indicators.shutil, "which", lambda _name: None)
    assert indicators._gnome_reduced_motion() is False


def test_an_unreadable_setting_never_breaks_the_gui(monkeypatch) -> None:
    def broken() -> bool:
        raise OSError("no such library")

    for name in ("_windows_reduced_motion", "_macos_reduced_motion", "_gnome_reduced_motion"):
        monkeypatch.setattr(indicators, name, broken)
    assert indicators.prefers_reduced_motion() is False


def test_importing_the_module_costs_no_qt() -> None:
    code = (
        "import sys; import tether.gui.indicators; "
        "assert 'PySide6' not in sys.modules, 'Qt was imported'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_the_install_smoke_renders_the_icons_on_every_os() -> None:
    # The installer smoke test (packaging/scripts/install_smoke.sh) proves that the
    # INSTALLED build carries the SVG plugins: one call in the Windows branch, one in
    # the Unix branch (Linux and macOS).
    script = Path(__file__).resolve().parents[1] / "packaging/scripts/install_smoke.sh"
    code = [
        line
        for line in script.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    calls = [line for line in code if "missing_icons()" in line]
    assert len(calls) == 2
    assert all("QT_QPA_PLATFORM=offscreen" in line and "assert not m" in line for line in calls)
