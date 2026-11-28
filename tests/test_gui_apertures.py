# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Apertures and background view of the Prepare page (#922), headless.

The tab reviews the movie and the options of the extraction page on a background worker:
it draws two apertures per spot, shows the counts of the spots at an edge and of the
overlapping ones, shows the preview traces of the selected molecules, reviews again when an
option changes, and writes the same report as the Qt-free core. No test reads a pixel.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")
pytest.importorskip("matplotlib")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from _gui_harness import needs_qt  # noqa: E402
from tether.gui import extraction as ex  # noqa: E402
from tether.project.extract import ExtractOptions  # noqa: E402
from tether.services import apertures as sa  # noqa: E402

_SHAPE = (64, 96)
#: Spot 0 is at the left edge; spots 3 and 4 are 9 px apart, so a 5 px disk overlaps.
_DONOR = np.array([[4.0, 30.0], [12.0, 12.0], [24.0, 50.0], [34.0, 20.0], [34.0, 29.0]])
_ACCEPTOR = _DONOR + [49.0, 0.0]


def _write_movie(path):
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    frames = []
    for t in range(10):
        frame = np.full(_SHAPE, 80.0)
        for i, (x, y) in enumerate(np.vstack([_DONOR, _ACCEPTOR])):
            amp = 400.0 * (1.0 + 0.05 * ((t + i) % 3))
            frame += amp * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / 4.5)
        frames.append(frame)
    tifffile.imwrite(path, np.asarray(frames, dtype="<u2"), photometric="minisblack")
    return path


@pytest.fixture
def page(qtbot):
    messages: list[str] = []
    section = ex.ExtractionSection(status=messages.append, panel_factory=lambda: None)
    qtbot.addWidget(section.widget)
    section.messages = messages
    yield section
    section.close()


def _wait(qtbot, tab) -> None:
    qtbot.waitUntil(lambda: not tab.busy, timeout=60_000)


@pytest.mark.gui
@needs_qt
def test_the_view_is_part_of_the_prepare_page(page) -> None:
    assert page.view_names == ["&Stages", "&Apertures and background"]
    assert page.views.currentIndex() == 0
    page.show_view(1)
    assert page.views.currentWidget() is page.apertures.widget
    assert page.apertures.last_review is None and page.apertures.n_drawn == 0


@pytest.mark.gui
@needs_qt
def test_a_review_draws_marks_and_counts_the_spots(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    page.set_field("disk_radius", "5")
    tab = page.apertures
    assert tab.review()
    assert tab.busy  # on the worker: the window keeps its event loop
    _wait(qtbot, tab)
    review = tab.last_review
    assert review is not None
    assert (review.n_spots, review.n_edge, review.n_overlap, review.n_molecules) == (5, 1, 2, 4)
    assert tab.counts_label.text() == review.summary()
    assert tab.n_drawn == 2 * review.n_spots
    texts = [tab.spot_list.item(i).text() for i in range(tab.spot_list.count())]
    assert sum("at an edge" in text for text in texts) == 1
    assert sum("overlaps a neighbour" in text for text in texts) == 2


@pytest.mark.gui
@needs_qt
def test_selected_molecules_show_their_preview_traces(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    tab = page.apertures
    tab.review()
    _wait(qtbot, tab)
    tab.select_molecules([0, 2])
    assert tab.selected_molecules() == [0, 2]
    assert tab.trace_names == ["0 donor", "0 acceptor", "2 donor", "2 acceptor"]
    # A spot at an edge has no trace, so it cannot be selected.
    edge = next(
        tab.spot_list.item(i)
        for i in range(tab.spot_list.count())
        if "at an edge" in tab.spot_list.item(i).text()
    )
    edge.setSelected(True)
    assert not edge.isSelected()
    # Nothing was written beside the movie.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["movie.tif"]


@pytest.mark.gui
@needs_qt
def test_a_changed_option_reviews_again(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    tab = page.apertures
    tab.inputs_changed()  # nothing on show: a change starts nothing
    assert not tab.busy
    tab.review()
    _wait(qtbot, tab)
    assert tab.last_review.n_overlap == 0
    page.fields["disk_radius"].setText("5")
    page.fields["disk_radius"].editingFinished.emit()
    assert tab.busy
    _wait(qtbot, tab)
    assert tab.last_review.disk_radius == 5.0 and tab.last_review.n_overlap == 2


@pytest.mark.gui
@needs_qt
def test_refused_inputs_and_a_failed_review_are_messages(page, qtbot, tmp_path) -> None:
    tab = page.apertures
    assert not tab.review()
    assert "Not reviewed" in tab.counts_label.text()
    assert "choose a movie first" in page.error_label.text()
    empty = tmp_path / "empty.tif"
    tifffile.imwrite(empty, np.full((4, *_SHAPE), 80, dtype="<u2"), photometric="minisblack")
    page.set_movie(empty)
    tab.review()
    _wait(qtbot, tab)
    assert tab.last_review is None
    assert tab.counts_label.text().startswith("Review failed: the preview stopped at")
    with pytest.raises(ValueError, match="review the apertures first"):
        tab.save_report(tmp_path / "r.json")


@pytest.mark.gui
@needs_qt
def test_the_saved_report_is_the_report_of_the_core(page, qtbot, tmp_path) -> None:
    movie = _write_movie(tmp_path / "movie.tif")
    page.set_movie(movie)
    tab = page.apertures
    tab.review()
    _wait(qtbot, tab)
    tab.select_molecules([1])
    written = tab.save_report(tmp_path / "page.json", tmp_path / "page.png")
    assert [p.name for p in written] == ["page.json", "page.png"]
    review, frame = sa.review_movie(movie, ExtractOptions())
    sa.write_report(review, frame, tmp_path / "core.json", tmp_path / "core.png", molecules=[1])
    assert (tmp_path / "page.json").read_bytes() == (tmp_path / "core.json").read_bytes()
    assert (tmp_path / "page.png").read_bytes() == (tmp_path / "core.png").read_bytes()
    assert [t["molecule"] for t in json.loads((tmp_path / "page.json").read_text())["traces"]] == [
        1
    ]


@pytest.mark.gui
@needs_qt
def test_a_movie_chosen_by_the_file_picker_reviews_again(page, qtbot, tmp_path) -> None:
    first = _write_movie(tmp_path / "first.tif")
    page.set_movie(first)
    tab = page.apertures
    tab.review()
    _wait(qtbot, tab)
    assert tab.last_review.movie == "first.tif"
    # The file picker sets the field's text; no Enter, no focus change.
    page.set_movie(_write_movie(tmp_path / "second.tif"))
    assert tab.busy
    _wait(qtbot, tab)
    assert tab.last_review.movie == "second.tif"


@pytest.mark.gui
@needs_qt
def test_each_pixel_centre_is_at_its_integer_coordinate(page) -> None:
    from pyqtgraph.Qt import QtCore

    # The review's (x, y) are pixel centres; pyqtgraph paints pixel [0, 0] over (0, 0)-(1, 1).
    image = page.apertures._image_item
    centre = image.mapToParent(QtCore.QPointF(0.5, 0.5))
    assert (centre.x(), centre.y()) == (0.0, 0.0)


@pytest.mark.gui
@needs_qt
def test_a_corrected_input_reviews_again_after_a_refused_one(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    tab = page.apertures
    tab.review()
    _wait(qtbot, tab)
    assert tab.last_review is not None
    field = page.fields["disk_radius"]
    field.setText("7")  # larger than ring_inner (6): the form refuses it
    field.editingFinished.emit()
    _wait(qtbot, tab)
    assert tab.last_review is None
    assert "Not reviewed" in tab.counts_label.text()
    field.setText("5")
    field.editingFinished.emit()
    assert tab.busy  # the ask still stands: the corrected input reviews again
    _wait(qtbot, tab)
    assert tab.last_review is not None and tab.last_review.disk_radius == 5.0


@pytest.mark.gui
@needs_qt
def test_a_failed_save_names_the_file_and_the_files_written(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    tab = page.apertures
    tab.review()
    _wait(qtbot, tab)
    blocked = tmp_path / "page.png"
    blocked.mkdir()  # a folder where the PNG goes: the PNG cannot be written
    written = tab.save_report(tmp_path / "page.json", blocked)
    assert written == [tmp_path / "page.json"]
    assert (tmp_path / "page.json").is_file()
    message = page.messages[-1]
    assert message.startswith("Aperture report not saved: could not write page.png (")
    assert message.endswith("already written: page.json")
    # A JSON that cannot be written stops the save before the PNG.
    blocked_json = tmp_path / "other.json"
    blocked_json.mkdir()
    assert tab.save_report(blocked_json, tmp_path / "other.png") == []
    assert "could not write other.json" in page.messages[-1]
    assert page.messages[-1].endswith("already written: none")
    assert not (tmp_path / "other.png").exists()


@pytest.mark.gui
@needs_qt
def test_a_scratch_folder_left_behind_does_not_fail_the_save(
    page, qtbot, tmp_path, monkeypatch
) -> None:
    import shutil

    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    tab = page.apertures
    tab.review()
    _wait(qtbot, tab)
    calls = []
    real_rmtree = shutil.rmtree

    def rmtree(path, **kw):  # the cleanup of the throwaway JSON's folder ignores its errors
        calls.append(kw)
        real_rmtree(path, **kw)

    monkeypatch.setattr(shutil, "rmtree", rmtree)
    written = tab.save_report(tmp_path / "page.json", tmp_path / "page.png")
    assert written == [tmp_path / "page.json", tmp_path / "page.png"]
    assert calls == [{"ignore_errors": True}]
    assert page.messages[-1] == "Aperture report written: page.json, page.png"
