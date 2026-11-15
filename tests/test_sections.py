# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Qt-free section registry (#340): :mod:`tether.gui.sections`.

No test here carries the ``gui`` marker or needs a ``QApplication``. The registry is data,
and a small fake host stands in for the shell, as the fake panel does for the navigator in
``tests/test_roundtrip.py``. ``tests/test_sections_gui.py`` checks the same contract against
the real :class:`~tether.gui.shell.TetherShell`.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tether.gui.sections import (
    DockArea,
    DockSpec,
    DuplicateSectionError,
    MenuSpec,
    SectionHostLike,
    SectionRegistry,
    SectionSpec,
)

SHELL = Path(__file__).resolve().parents[1] / "src" / "tether" / "gui" / "shell.py"


class FakeHost:
    """The smallest object that satisfies :class:`SectionHostLike`."""

    def __init__(self) -> None:
        self.window = object()
        self.curation_project = None
        self.read_only_reason = None
        self.is_idealizing = False
        self.keymap = object()


def _widget(host: SectionHostLike) -> tuple[str, object]:
    return ("widget", host.window)


def _section(section_id: str, order: int, *, writes: bool = False, **extra) -> SectionSpec:
    return SectionSpec(
        id=section_id,
        title=section_id.capitalize(),
        order=order,
        central=_widget,
        writes_project=writes,
        **extra,
    )


def test_importing_the_module_costs_no_qt() -> None:
    code = (
        "import sys; import tether.gui.sections; "
        "bad = [m for m in ('PySide6', 'PyQt5', 'PyQt6', 'qtpy', 'pyqtgraph', 'napari') "
        "if m in sys.modules]; "
        "assert not bad, f'Qt was imported: {bad}'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_fake_host_satisfies_the_protocol_and_reaches_the_factories() -> None:
    host = FakeHost()
    assert isinstance(host, SectionHostLike)
    assert not isinstance(object(), SectionHostLike)

    dock = DockSpec(id="browser", title="Browser", area=DockArea.LEFT, factory=_widget)
    spec = _section("curate", 2, docks=(dock,))
    assert spec.central(host) == ("widget", host.window)
    assert spec.docks[0].factory(host) == ("widget", host.window)


def test_the_shell_declares_every_member_of_the_protocol() -> None:
    # Qt-free: read shell.py as source. tests/test_sections_gui.py checks the live object.
    tree = ast.parse(SHELL.read_text(encoding="utf-8"))
    shell_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TetherShell"
    )
    defined = {node.name for node in shell_class.body if isinstance(node, ast.FunctionDef)}
    wanted = {name for name in vars(SectionHostLike) if not name.startswith("_")}
    assert wanted == {"window", "curation_project", "read_only_reason", "is_idealizing", "keymap"}
    assert wanted <= defined, sorted(wanted - defined)


def test_a_second_section_with_the_same_id_is_refused() -> None:
    registry = SectionRegistry([_section("curate", 1)])
    with pytest.raises(DuplicateSectionError, match="id 'curate'"):
        registry.register(_section("curate", 2))
    assert registry.ids == ("curate",)


def test_a_second_section_with_the_same_order_is_refused() -> None:
    registry = SectionRegistry([_section("prepare", 1)])
    with pytest.raises(DuplicateSectionError, match="order 1"):
        registry.register(_section("curate", 1))
    assert len(registry) == 1


def test_the_order_does_not_depend_on_the_order_of_registration() -> None:
    specs = [_section("analyze", 3), _section("prepare", 1), _section("curate", 2)]
    forward = SectionRegistry(specs)
    backward = SectionRegistry(reversed(specs))
    assert forward.ids == backward.ids == ("prepare", "curate", "analyze")
    assert [spec.id for spec in forward] == list(forward.ids)
    assert forward.sections() == forward.sections()


def test_a_fourth_section_and_its_order_need_no_change_of_the_interface() -> None:
    # The plan change of 2026-11-14 (#890, #897): four sections and the guided-mode order.
    registry = SectionRegistry(
        [
            _section("prepare", 10, writes=True),
            _section("curate", 20, writes=True),
            _section("analyze", 30),
        ]
    )
    registry.register(_section("deposit", 40))
    assert registry.ids == ("prepare", "curate", "analyze", "deposit")
    # An order value between two others puts the section between them.
    registry.register(_section("review", 25))
    assert registry.ids == ("prepare", "curate", "review", "analyze", "deposit")


def test_each_section_declares_whether_it_may_write() -> None:
    registry = SectionRegistry([_section("prepare", 1, writes=True), _section("analyze", 2)])
    assert registry.may_write("prepare") is True
    assert registry.may_write("analyze") is False
    with pytest.raises(KeyError, match="no section"):
        registry.may_write("deposit")
    with pytest.raises(TypeError, match="writes_project"):
        SectionSpec(id="x", title="X", order=1, central=_widget)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="writes_project must be a bool"):
        SectionSpec(id="x", title="X", order=1, central=_widget, writes_project=1)


def test_the_three_shell_dock_areas_are_expressible_without_special_cases() -> None:
    docks = (
        DockSpec(id="browser", title="Browser", area=DockArea.LEFT, factory=_widget),
        DockSpec(id="overlap", title="Overlap", area=DockArea.RIGHT, factory=_widget),
        DockSpec(
            id="histogram", title="Population histogram", area=DockArea.BOTTOM, factory=_widget
        ),
    )
    spec = _section("curate", 1, docks=docks)
    assert [dock.area for dock in spec.docks] == [DockArea.LEFT, DockArea.RIGHT, DockArea.BOTTOM]
    # Every Qt area name that shell.py uses today is the qt_name of one DockArea.
    used = set(
        re.findall(r"DockWidgetArea\.(\w+DockWidgetArea)", SHELL.read_text(encoding="utf-8"))
    )
    assert used == {"LeftDockWidgetArea", "RightDockWidgetArea", "BottomDockWidgetArea"}
    assert used <= {area.qt_name for area in DockArea}
    assert DockArea.TOP.qt_name == "TopDockWidgetArea"


def test_a_spec_is_frozen_and_keeps_its_docks_and_menus_as_tuples() -> None:
    menu = MenuSpec(id="curation", title="&Curation")
    spec = _section("curate", 1, menus=[menu], keymap_scope="curation")
    assert spec.menus == (menu,)
    assert spec.docks == ()
    assert menu.populate is None
    with pytest.raises(FrozenInstanceError):
        spec.order = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: _section("Curate", 1), "section id"),
        (lambda: _section("", 1), "section id"),
        (
            lambda: SectionSpec(id="c", title=" ", order=1, central=_widget, writes_project=False),
            "title",
        ),
        (lambda: _section("c", True), "order must be an int"),
        (lambda: _section("c", 1.0), "order must be an int"),
        (
            lambda: SectionSpec(id="c", title="C", order=1, central=None, writes_project=False),
            "central",
        ),
        (lambda: _section("c", 1, keymap_scope=""), "keymap_scope"),
        (lambda: _section("c", 1, docks=("browser",)), "docks must be DockSpec"),
        (lambda: _section("c", 1, menus=("file",)), "menus must be MenuSpec"),
        (
            lambda: _section(
                "c",
                1,
                docks=(
                    DockSpec(id="d", title="D", area=DockArea.LEFT, factory=_widget),
                    DockSpec(id="d", title="E", area=DockArea.RIGHT, factory=_widget),
                ),
            ),
            "dock id 'd' twice",
        ),
        (
            lambda: _section(
                "c", 1, menus=(MenuSpec(id="m", title="M"), MenuSpec(id="m", title="N"))
            ),
            "menu id 'm' twice",
        ),
        (lambda: DockSpec(id="d", title="D", area="left", factory=_widget), "must be a DockArea"),
        (lambda: DockSpec(id="d", title="D", area=DockArea.LEFT, factory=None), "factory"),
        (lambda: MenuSpec(id="m", title="M", populate="no"), "populate"),
    ],
)
def test_an_invalid_spec_is_refused(build, message) -> None:
    with pytest.raises(ValueError, match=message):
        build()


def test_register_refuses_an_object_that_is_not_a_spec() -> None:
    with pytest.raises(TypeError, match="SectionSpec"):
        SectionRegistry().register("curate")  # type: ignore[arg-type]


def test_lookup_by_id() -> None:
    spec = _section("curate", 1)
    registry = SectionRegistry([spec])
    assert registry.get("curate") is spec
    assert "curate" in registry
    assert "analyze" not in registry
    with pytest.raises(KeyError, match="no section with id 'analyze'"):
        registry.get("analyze")
