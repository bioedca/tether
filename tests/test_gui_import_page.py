# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The import page of the Prepare section (#913), headless.

The first tests use the Qt-free steps the page calls, ``tether.services.movie_import``: a movie
description with each origin, a pair check, the frame view of a layout after frame steps, the
``extract`` requests of a recipe, and the source repair and the frame time of a project. The
``pytest-qt`` tests then drive the page: add (by the method and by a drop), pair, layout and
swap with the frame view before apply, the frame steps, a recipe, the use in the extraction
page, the link again of a moved source and a frame time from you that stays not verified until
confirmed, and the page as the Import dock of the shell's Prepare section. No test reads a pixel.
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from _gui_harness import build_project, needs_qt  # noqa: E402
from tether.services import RequestRefused  # noqa: E402
from tether.services import movie_import as mi  # noqa: E402
from tether.services.base import CommandFailed  # noqa: E402

_FRAMES, _H, _W = 6, 32, 64


def _write_movie(path, frames: int = _FRAMES, dtype: str = "uint16", seed: int = 913):
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 1000, size=(frames, _H, _W)).astype(dtype)
    tifffile.imwrite(path, data, photometric="minisblack")
    return path


def _project_with_moved_movie(tmp_path):
    """A project whose one movie was recorded at ``moved.tif`` and now is elsewhere."""
    project = build_project(tmp_path, name="repair.tether")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    movie = _write_movie(elsewhere / "moved.tif")
    digest = hashlib.sha256(movie.read_bytes()).hexdigest()
    with h5py.File(project.path, "r+") as f:
        table = f["movies/table"]
        row = table[0]
        row["uri"] = b"moved.tif"
        row["sha256"] = digest.encode()
        table[0] = row
    return project, movie


# --- the Qt-free steps ------------------------------------------------------------------


def test_a_movie_is_described_with_its_reader_shape_pixel_type_and_origins(tmp_path) -> None:
    movie = mi.describe_movie(_write_movie(tmp_path / "a.tif"))
    assert movie.reader.startswith("TIFF")
    assert movie.shape == (_FRAMES, _H, _W)
    assert movie.pixel_type == "uint16"
    assert movie.frame_time is None
    by_name = {m.name: m for m in movie.metadata}
    assert by_name["Frame time (s)"].origin == "unknown"
    assert by_name["Frame time (s)"].value == ""
    assert mi.read_first_frame(movie.path).shape == (_H, _W)


def test_a_file_no_reader_opens_is_refused_with_its_name(tmp_path) -> None:
    junk = tmp_path / "junk.tif"
    junk.write_bytes(b"not a movie at all")
    with pytest.raises(RequestRefused, match="junk.tif"):
        mi.describe_movie(junk)


def test_a_pair_must_have_the_same_frames_and_pixel_type(tmp_path) -> None:
    donor = mi.describe_movie(_write_movie(tmp_path / "d.tif"))
    acceptor = mi.describe_movie(_write_movie(tmp_path / "a.tif", seed=1))
    mi.check_movie_pair(donor, acceptor)
    short = mi.describe_movie(_write_movie(tmp_path / "s.tif", frames=_FRAMES + 2))
    with pytest.raises(RequestRefused, match="frame count"):
        mi.check_movie_pair(donor, short)
    other = mi.describe_movie(_write_movie(tmp_path / "f.tif", dtype="float32"))
    with pytest.raises(RequestRefused, match="pixel type"):
        mi.check_movie_pair(donor, other)
    with pytest.raises(RequestRefused, match="itself"):
        mi.check_movie_pair(donor, donor)


def test_the_frame_view_reads_the_layout_in_the_frame_after_the_steps() -> None:
    frame = np.zeros((_H, _W))
    side = mi.frame_view(frame, mi.LayoutChoice())
    assert side.donor_rect == (1, 1, _H, _W // 2)
    assert side.acceptor_rect == (1, _W // 2 + 1, _H, _W)
    turned = mi.frame_view(frame, mi.LayoutChoice(kind="top-bottom", donor="top"), "rotate 90")
    assert turned.frame.shape == (_W, _H)
    assert turned.donor_rect == (1, 1, _W // 2, _H)
    swapped = mi.frame_view(frame, mi.LayoutChoice().swapped())
    assert swapped.donor_rect == side.acceptor_rect
    pair = mi.frame_view(frame, mi.LayoutChoice(), pair=True)
    assert pair.donor_rect == pair.acceptor_rect == (1, 1, _H, _W)


def test_a_layout_or_steps_that_do_not_fit_are_refused_with_the_reason() -> None:
    frame = np.zeros((_H, _W))
    with pytest.raises(RequestRefused, match="frame_steps"):
        mi.frame_view(frame, mi.LayoutChoice(), "spin 3")
    outside = mi.LayoutChoice(
        kind="rectangles", donor=None, donor_rect=(1, 1, 8, 8), acceptor_rect=(1, 60, 8, 67)
    )
    with pytest.raises(RequestRefused, match="outside"):
        mi.frame_view(frame, outside)
    overlap = mi.LayoutChoice(
        kind="rectangles", donor=None, donor_rect=(1, 1, 8, 8), acceptor_rect=(4, 4, 11, 11)
    )
    assert "overlap" in mi.frame_view(frame, overlap).warning


def test_an_extract_request_holds_the_layout_and_the_steps_and_refuses_a_pair(tmp_path) -> None:
    layout = mi.LayoutChoice(kind="top-bottom", donor="bottom")
    request = mi.extract_request("m.tif", "m.tether", layout, "rotate 90")
    assert (request.channel_layout, request.channel_layout_donor) == ("top-bottom", "bottom")
    assert request.frame_steps == "rotate 90"
    with pytest.raises(RequestRefused, match="movie pair"):
        mi.extract_request("d.tif", "d.tether", layout, acceptor_movie="a.tif")
    with pytest.raises(RequestRefused, match="no applied movie"):
        mi.save_recipe(tmp_path / "r.json", [])


def test_a_moved_source_is_found_missing_and_linked_again_by_its_checksum(tmp_path) -> None:
    project, movie = _project_with_moved_movie(tmp_path)
    (source,) = [s for s in mi.read_project_sources(project.path) if s.role == "movie"]
    assert source.missing == "file missing"
    project.acquire_lock()
    try:
        wrong = _write_movie(tmp_path / "wrong.tif", seed=2)
        with pytest.raises(CommandFailed, match="checksum"):
            mi.relink_source(project, source.source_id, wrong)
        linked = mi.relink_source(project, source.source_id, movie)
    finally:
        project.release_lock()
    assert linked.missing is None
    assert linked.path.resolve() == movie.resolve()


def test_a_frame_time_from_you_is_not_verified_until_you_confirm_it(tmp_path) -> None:
    project = build_project(tmp_path, name="times.tether")
    (unknown,) = mi.read_frame_times(project.path)
    assert (unknown.seconds, unknown.state) == (0.0, "unknown")
    with pytest.raises(CommandFailed):  # no writer lock is held
        mi.give_frame_time(project, unknown.movie_id, 0.05)
    project.acquire_lock()
    try:
        given = mi.give_frame_time(project, unknown.movie_id, 0.05)
        assert (given.origin, given.verified, given.state) == (
            "user",
            False,
            "from you, not verified",
        )
        with pytest.raises(CommandFailed):
            mi.give_frame_time(project, unknown.movie_id, -1.0)
        confirmed = mi.confirm_frame_time(project, unknown.movie_id)
    finally:
        project.release_lock()
    assert confirmed.state == "from you, confirmed"


# --- the page ---------------------------------------------------------------------------


@pytest.fixture
def page(qtbot):
    pytest.importorskip("pytestqt")
    from tether.gui.import_page import ImportPage

    used: list = []
    lines: list[str] = []
    built = ImportPage(status=lines.append, use_movie=lambda p, t: used.append((p, t)))
    qtbot.addWidget(built.widget)
    built.used = used  # type: ignore[attr-defined]
    yield built
    built.close()


def _add(page, qtbot, *paths) -> None:
    assert page.add_movies(paths)
    qtbot.waitUntil(lambda: not page.busy, timeout=10_000)


@needs_qt
def test_added_movies_show_their_details_and_a_bad_file_is_named(page, qtbot, tmp_path) -> None:
    junk = tmp_path / "junk.tif"
    junk.write_bytes(b"not a movie")
    _add(page, qtbot, _write_movie(tmp_path / "a.tif"), junk, _write_movie(tmp_path / "b.tif"))
    assert [e.name for e in page.entries] == ["a.tif", "b.tif"]
    assert "Added 2 movie(s)" in page.message and "junk.tif" in page.message
    details = {setting: (value, origin) for setting, value, origin in page.details}
    assert details["Shape"] == (f"{_FRAMES} frames of {_H} x {_W} px", "from the file")
    assert details["Pixel type"] == ("uint16", "from the file")
    assert details["Frame time (s)"] == ("", "unknown")


@needs_qt
def test_a_drop_of_files_adds_the_movies(page, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore, QtGui

    movie = _write_movie(tmp_path / "dropped.tif")
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(movie))])
    pos = QtCore.QPointF(5, 5)
    action = QtCore.Qt.DropAction.CopyAction
    buttons, mods = QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier
    page.widget.dropEvent(QtGui.QDropEvent(pos, action, mime, buttons, mods))
    qtbot.waitUntil(lambda: not page.busy, timeout=10_000)
    assert [e.name for e in page.entries] == ["dropped.tif"]


@needs_qt
def test_each_layout_change_shows_on_the_frame_view_before_it_is_applied(
    page, qtbot, tmp_path
) -> None:
    _add(page, qtbot, _write_movie(tmp_path / "a.tif"))
    assert page.view.donor_rect == (1, 1, _H, _W // 2)
    page.set_layout(mi.LayoutChoice(kind="top-bottom", donor="bottom"))
    assert page.view.donor_rect == (_H // 2 + 1, 1, _H, _W)
    assert page.entries[0].applied is False  # shown, not applied
    assert page.swap_roles()
    assert page.view.donor_rect == (1, 1, _H // 2, _W)
    assert page.apply()
    entry = page.entries[0]
    assert entry.applied and entry.layout == mi.LayoutChoice(kind="top-bottom", donor="top")


@needs_qt
def test_frame_steps_show_on_the_view_and_bad_steps_are_not_applied(page, qtbot, tmp_path) -> None:
    _add(page, qtbot, _write_movie(tmp_path / "a.tif"))
    page.set_layout(mi.LayoutChoice(), "rotate 90")
    assert page.view.frame.shape == (_W, _H)
    assert page.apply() and page.entries[0].frame_steps == "rotate 90"
    page.set_layout(mi.LayoutChoice(), "spin 3")
    assert page.view is None
    assert not page.apply()
    assert "Not applied" in page.message and page.entries[0].frame_steps == "rotate 90"


@needs_qt
def test_two_movies_pair_as_donor_and_acceptor_and_a_bad_pair_is_refused(
    page, qtbot, tmp_path
) -> None:
    _add(
        page,
        qtbot,
        _write_movie(tmp_path / "d.tif"),
        _write_movie(tmp_path / "a.tif", seed=1),
        _write_movie(tmp_path / "s.tif", frames=_FRAMES + 2),
    )
    rows = page.movie_list
    rows.item(0).setSelected(True)
    rows.item(2).setSelected(True)
    assert not page.pair_selected()
    assert "frame count" in page.message
    rows.clearSelection()
    rows.item(0).setSelected(True)
    rows.item(1).setSelected(True)
    assert page.pair_selected()
    pair = page.entries[0]
    assert (pair.donor.name, pair.acceptor.name) == ("d.tif", "a.tif")
    assert page.view.donor_rect == page.view.acceptor_rect == (1, 1, _H, _W)
    assert page.swap_roles() and page.entries[0].donor.name == "a.tif"
    detail_names = [setting for setting, _v, _o in page.details]
    assert "Donor file: Reader" in detail_names and "Acceptor file: Reader" in detail_names
    rows.setCurrentRow(0)
    assert page.unpair_selected()
    assert [e.name for e in page.entries] == ["a.tif", "d.tif", "s.tif"]


@needs_qt
def test_a_recipe_holds_one_extract_step_per_applied_movie(page, qtbot, tmp_path) -> None:
    from tether.services.recipe import read_recipe

    _add(page, qtbot, _write_movie(tmp_path / "a.tif"), _write_movie(tmp_path / "b.tif"))
    page.movie_list.setCurrentRow(1)
    page.set_layout(mi.LayoutChoice(kind="side-by-side", donor="right"), "flip h")
    assert page.apply()
    written = page.save_recipe(tmp_path / "import.json")
    assert written is not None
    assert len(json.loads(written.read_text(encoding="utf-8"))["steps"]) == 1
    (step,) = read_recipe(written).steps
    assert step.request.movie == str(tmp_path / "b.tif")
    assert (step.request.channel_layout_donor, step.request.frame_steps) == ("right", "flip h")


@needs_qt
def test_use_in_extraction_gives_the_movie_and_its_layout(page, qtbot, tmp_path) -> None:
    _add(page, qtbot, _write_movie(tmp_path / "a.tif"))
    assert not page.use_in_extraction()  # not applied yet
    page.set_layout(mi.LayoutChoice(kind="top-bottom", donor="bottom"))
    assert page.apply() and page.use_in_extraction()
    ((path, texts),) = page.used
    assert path == tmp_path / "a.tif"
    assert texts["channel_layout"] == "top-bottom" and texts["channel_layout_donor"] == "bottom"
    page.set_layout(mi.LayoutChoice(kind="top-bottom", donor="bottom"), "rotate 90")
    assert page.apply() and not page.use_in_extraction()
    assert "frame steps" in page.message


@needs_qt
def test_a_missing_source_is_linked_again_from_the_page(page, qtbot, tmp_path) -> None:
    project, movie = _project_with_moved_movie(tmp_path)
    page.set_project(project.path, None)
    (source,) = [s for s in page.sources if s.role == "movie"]
    assert source.missing == "file missing"
    assert not page.relink(source.source_id, movie)  # read-only: refused
    project.acquire_lock()
    try:
        page.set_project(project.path, project)
        assert page.relink(source.source_id, movie)
        qtbot.waitUntil(lambda: not page.busy, timeout=10_000)
    finally:
        project.release_lock()
    assert "Linked" in page.message
    assert [s.missing for s in page.sources if s.role == "movie"] == [None]


@needs_qt
def test_the_page_is_the_import_dock_of_prepare_and_asks_for_the_frame_time(section_shell) -> None:
    shell = section_shell("prepare")
    names = [dock.windowTitle() for dock in shell.section_docks("prepare")]
    assert names == ["Import"]
    page = shell.import_page
    assert shell.section_docks("prepare")[0].widget() is page.widget
    (time,) = page.frame_times
    assert time.state == "unknown"
    assert page.give_frame_time(time.movie_id, 0.1)
    assert page.frame_times[0].state == "from you, not verified"
    assert page.time_table.item(0, 2).text() == "from you, not verified"
    assert page.confirm_frame_time(time.movie_id)
    assert page.frame_times[0].state == "from you, confirmed"
