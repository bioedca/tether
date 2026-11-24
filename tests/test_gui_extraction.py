# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The extraction page of the Prepare section (#350), headless.

The first tests use the Qt-free helpers the page calls, ``tether.services.preview``: the
options form covers every extraction field and refuses bad input with the ``ExtractionError``
message; the stage rows give every stage an outcome; the overlays put the spots and the
control-point residuals in the raw camera frame; and the donor-side note is the one
``tether extract`` prints. The ``pytest-qt``
tests then drive the page: a preview on a small synthetic movie, a failed stage, a refused
input, an extraction and its donor-side result, and the page inside the shell's Prepare
section. A fake movie viewer stands in for napari, so those run on every operating system;
the one test with the real napari viewer carries ``needs_gl``. No test reads a pixel.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from _gui_harness import needs_gl, needs_qt  # noqa: E402
from tether.gui import extraction as ex  # noqa: E402
from tether.imaging.side_check import DonorSideCheck  # noqa: E402
from tether.project.extract import ExtractionSummary, ExtractOptions  # noqa: E402
from tether.project.preview import STAGES, preview_extraction  # noqa: E402
from tether.services import RequestError  # noqa: E402
from tether.services import preview as sp  # noqa: E402

_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_SHAPE = (64, 96)  # 48-px halves, donor on the left
_SPOTS = ex.VIEW_NAMES[sp.SPOTS]
_RESIDUALS = ex.VIEW_NAMES[sp.RESIDUALS]
_N_FRAMES = 12
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = np.array([[61.0, 12.0], [73.0, 40.0], [65.0, 52.0]])  # +1 px in its half


def _movie_array(spots: bool = True) -> np.ndarray:
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    frame = np.full(_SHAPE, _BG)
    if spots:
        for x, y in np.vstack([_DONOR, _ACCEPTOR]):
            frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    return np.ascontiguousarray(np.broadcast_to(frame, (_N_FRAMES, *_SHAPE)), dtype=">u2")


def _write_movie(path, spots: bool = True):
    tifffile.imwrite(path, _movie_array(spots), photometric="minisblack", byteorder=">")
    return path


def _nearest(points: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """The distance from each target to its nearest point."""
    return np.min(np.linalg.norm(points[None, :, :] - targets[:, None, :], axis=2), axis=1)


# --- the options form -------------------------------------------------------------------


def test_the_form_has_one_row_per_extraction_field_in_order() -> None:
    from tether.services.profile import ExtractionFields

    names = [f.name for f in dataclasses.fields(ExtractionFields)]
    assert [spec.name for spec in sp.FORM_FIELDS] == names
    # Every ExtractOptions field is a row, the channel layout as its four profile fields.
    options = {f.name for f in dataclasses.fields(ExtractOptions)}
    assert options <= set(names)


def test_the_written_out_choices_are_the_cores() -> None:
    from tether import extract_choices as choices
    from tether.imaging import layout

    rows = {spec.name: spec for spec in sp.FORM_FIELDS}
    assert rows["donor_side"].choices == choices.DONOR_SIDES
    assert rows["detection_mode"].choices == choices.DETECTION_MODES
    assert rows["prealign"].choices == choices.PREALIGN_MODES
    assert rows["colocalization"].choices == choices.COLOCALIZATION_POLICIES
    assert rows["channel_layout"].choices == layout.LAYOUT_KINDS
    halves = {side for positions in layout._HALF_POSITIONS.values() for side in positions}
    assert set(rows["channel_layout_donor"].choices) == halves


def test_the_default_form_is_the_default_options() -> None:
    profile = sp.profile_from_texts(sp.default_texts())
    assert profile.extract_options() == ExtractOptions()
    assert profile.detection_mode is None  # no explicit choice, as on the command line


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("window", "20", "window must be a positive odd integer"),
        ("window", "abc", "window: 'abc' is not a whole number"),
        ("pair_tol", "nan", "pair_tol: 'nan' is not a finite number"),
        ("rms_gate", "", "rms_gate: a value is required"),
        ("ring_outer", "5", "radii must satisfy"),
        ("channel_layout_donor_rect", "1, 2, 3", "is not four whole numbers"),
        ("donor_side", "middle", "donor_side must be 'left' or 'right'"),
    ],
)
def test_the_form_refuses_bad_input_with_the_extraction_error(name, text, message) -> None:
    with pytest.raises(RequestError, match=message):
        sp.profile_from_texts({name: text})


def test_an_empty_optional_row_is_none() -> None:
    texts = {"detection_threshold": "", "min_separation": "4.5", "detection_mode": "intensity"}
    profile = sp.profile_from_texts(texts)
    options = profile.extract_options()
    assert profile.detection_threshold is None
    assert options.min_separation == 4.5 and options.detection_mode == "intensity"


# --- the stage rows and the overlays -------------------------------------------------------


def test_without_a_preview_every_stage_is_not_run() -> None:
    rows = sp.stage_rows(None)
    assert [row[0] for row in rows] == list(STAGES)
    assert {row[1] for row in rows} == {"not run"}


def test_a_completed_preview_gives_every_stage_an_ok_outcome_and_a_detail() -> None:
    preview = preview_extraction(_movie_array())
    rows = sp.stage_rows(preview)
    assert [row[:2] for row in rows] == [(name, "ok") for name in STAGES]
    details = {row[0]: row[2] for row in rows}
    assert details["detect"] == "3 donor, 3 acceptor spots"
    assert details["pair"] == "3 control-point pairs"
    assert details["colocalize"].startswith("3 molecules (partner-in-both 3")
    assert details["extract"] == f"3 traces of {_N_FRAMES} frames"


def test_a_failed_stage_says_why_and_later_stages_are_not_run() -> None:
    preview = preview_extraction(_movie_array(spots=False))
    rows = sp.stage_rows(preview)
    failed = [row for row in rows if row[1] == "failed"]
    assert len(failed) == 1 and failed[0][2]  # the message of the stage
    after = rows[[row[0] for row in rows].index(failed[0][0]) + 1 :]
    assert after and {row[1] for row in after} == {"not run"}


def test_the_overlays_put_spots_and_residuals_in_the_camera_frame() -> None:
    preview = preview_extraction(_movie_array())
    views = ex.preview_overlays(preview, _SHAPE, aperture_radius=3.0)
    assert list(views) == [_SPOTS, _RESIDUALS]
    spots = views[_SPOTS]
    assert np.all(_nearest(spots.donor_xy, _DONOR) < 0.5)
    assert np.all(_nearest(spots.acceptor_xy, _ACCEPTOR) < 0.5)  # in the right half
    residuals = views[_RESIDUALS]
    # Both sets of the residual view lie in the acceptor half, at the acceptor spots.
    assert np.all(_nearest(residuals.acceptor_xy, _ACCEPTOR) < 0.5)
    assert np.all(_nearest(residuals.donor_xy, _ACCEPTOR) < 0.5)
    distances = np.linalg.norm(residuals.donor_xy - residuals.acceptor_xy, axis=1)
    rms = float(np.sqrt(np.mean(distances**2)))
    assert rms == pytest.approx(preview.registration.rms_residual, abs=1e-9)
    assert sp.residual_summary(preview).startswith("Control-point residuals: 3 pairs")


def test_an_imported_map_has_spots_but_no_residuals() -> None:
    registration = preview_extraction(_movie_array()).registration
    preview = preview_extraction(_movie_array(), registration=registration)
    views = ex.preview_overlays(preview, _SHAPE, aperture_radius=3.0)
    assert list(views) == [_SPOTS]
    assert sp.residual_summary(preview) == ""
    assert [row[1] for row in sp.stage_rows(preview)][2:4] == ["skipped", "skipped"]


# --- the donor-side note -----------------------------------------------------------------


def _summary(verdict: str | None, **kwargs) -> ExtractionSummary:
    from pathlib import Path

    check = None
    if verdict is not None:
        check = DonorSideCheck(verdict=verdict, n_molecules=9, n_consistent=1, n_swapped=8)
    fields = {
        "output_path": Path("out.tether"),
        "movie_id": "m",
        "calibration_id": "c",
        "n_molecules": 9,
        "n_control_points": 9,
        "rms_residual": 0.1,
        "low_confidence_registration": False,
        "molecule_tags": (),
        "registration_source": "native",
        "detection_mode": "wavelet",
        "donor_side_check": check,
    }
    fields.update(kwargs)
    return ExtractionSummary(**fields)


def test_swapped_halves_are_a_warning_with_the_evidence_and_the_remedy() -> None:
    level, text = sp.donor_side_note(_summary("swapped"), donor_side="left")
    assert level == "warning"
    assert "8 of 9 bleach events" in text
    assert "--donor-side right" in text


def test_a_map_file_remedy_names_the_map_file() -> None:
    summary = _summary("swapped", registration_source="map-file")
    _level, text = sp.donor_side_note(summary, donor_side="left", map_file="beads.npz")
    assert "beads.npz" in text


@pytest.mark.parametrize("verdict", ["mixed", "insufficient", "consistent", "not-run"])
def test_every_other_verdict_is_a_note(verdict) -> None:
    level, text = sp.donor_side_note(_summary(verdict), donor_side="left")
    assert level == "note" and text


def test_no_check_is_no_note() -> None:
    assert sp.donor_side_note(_summary(None), donor_side="left") is None


# --- the page ----------------------------------------------------------------------------


class _FakePanel:
    """The part of ``NapariMoviePanel`` the page drives, without napari."""

    def __init__(self) -> None:
        from pyqtgraph.Qt import QtWidgets

        self.qt_window = QtWidgets.QWidget()
        self.movies: list[tuple[object, object, str]] = []
        self.active = -1
        self.closed = False

    def add_movie(self, reader, *, overlay=None, name=None) -> int:
        self.movies.append((reader, overlay, name))
        if self.active < 0:
            self.active = 0
        return len(self.movies) - 1

    def set_active_movie(self, index: int) -> None:
        self.active = index

    def clear_movies(self) -> None:
        self.movies.clear()
        self.active = -1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def page(qtbot):
    messages: list[str] = []
    opened: list[object] = []
    panels: list[_FakePanel] = []

    def factory() -> _FakePanel:
        panels.append(_FakePanel())
        return panels[-1]

    section = ex.ExtractionSection(
        status=messages.append, open_project=lambda p: opened.append(p) or p, panel_factory=factory
    )
    qtbot.addWidget(section.widget)
    section.messages, section.opened, section.panels = messages, opened, panels
    yield section
    section.close()


def _wait(qtbot, section) -> None:
    qtbot.waitUntil(lambda: not section.busy, timeout=60_000)


def _table(section) -> list[tuple[str, str, str]]:
    table = section.stage_table
    return [
        tuple(table.item(row, col).text() for col in range(3)) for row in range(table.rowCount())
    ]


@pytest.mark.gui
@needs_qt
def test_the_page_has_every_form_row_and_shows_every_stage(page) -> None:
    assert set(page.fields) == {spec.name for spec in sp.FORM_FIELDS}
    assert [row[:2] for row in _table(page)] == [(name, "not run") for name in STAGES]


@pytest.mark.gui
@needs_qt
def test_bad_options_are_refused_under_the_form_without_starting(page, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    page.set_field("window", "20")
    assert page.preview() is False
    assert not page.busy
    assert "window must be a positive odd integer" in page.error_label.text()
    page.set_field("window", "21")
    page.set_registration("map-file")
    assert page.preview() is False
    assert "choose the Channel map file" in page.error_label.text()


@pytest.mark.gui
@needs_qt
def test_a_preview_fills_the_stages_and_the_two_overlays(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    assert page.preview() is True
    _wait(qtbot, page)
    assert page.error_label.text() == ""
    assert [row[:2] for row in _table(page)] == [(name, "ok") for name in STAGES]
    assert page.overlay_views == (_SPOTS, _RESIDUALS)
    (panel,) = page.panels
    from tether.gui.movie_panel import MovieOverlay

    assert [name for _r, _o, name in panel.movies] == list(page.overlay_views)
    assert all(isinstance(overlay, MovieOverlay) for _r, overlay, _n in panel.movies)
    assert all(overlay.aperture_radius == 3.0 for _r, overlay, _n in panel.movies)
    page._view_combo.setCurrentIndex(1)
    assert panel.active == 1
    # A second preview replaces the overlays rather than adding to them.
    assert page.preview() is True
    _wait(qtbot, page)
    assert len(panel.movies) == 2 and len(page.panels) == 1


@pytest.mark.gui
@needs_qt
def test_a_failed_stage_shows_why_and_the_page_stays(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "blank.tif", spots=False))
    assert page.preview() is True
    _wait(qtbot, page)
    rows = _table(page)
    assert any(outcome == "failed" and detail for _n, outcome, detail in rows)
    assert rows[-1][1] == "not run"
    assert page.preview() is True  # the page still works
    _wait(qtbot, page)


@pytest.mark.gui
@needs_qt
def test_an_unreadable_movie_is_a_message_not_a_traceback(page, qtbot, tmp_path) -> None:
    page.set_movie(tmp_path / "missing.tif")
    assert page.preview() is True
    _wait(qtbot, page)
    assert "Preview failed: movie not found" in page.error_label.text()
    assert {row[1] for row in _table(page)} == {"not run"}


@pytest.mark.gui
@needs_qt
def test_a_map_file_preview_skips_the_fit(page, qtbot, tmp_path) -> None:
    from tether.imaging.calibrate import save_map

    registration = preview_extraction(_movie_array()).registration
    map_path = save_map(registration, tmp_path / "beads.npz")
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    page.set_registration("map-file", map_path)
    assert page.preview() is True
    _wait(qtbot, page)
    assert [row[1] for row in _table(page)] == ["ok", "ok", "skipped", "skipped", "ok", "ok", "ok"]
    assert page.overlay_views == (_SPOTS,)


@pytest.mark.gui
@needs_qt
def test_an_extraction_shows_its_summary_and_donor_side_note(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    output = tmp_path / "new.tether"
    assert page.extract(output) is True
    _wait(qtbot, page)
    assert output.is_file()
    assert page.last_result.summary.n_molecules == 3
    assert page.result_label.text().startswith("Extracted 3 molecule(s) into new.tether")
    # Twelve flat frames have no bleach event: the check says so, as a note.
    assert page.donor_side_label.text().startswith("Donor side: not enough bleach events")
    assert page.open_result() == output
    assert page.opened == [output]
    # An existing project is refused, never replaced.
    assert page.extract(output) is True
    _wait(qtbot, page)
    assert page.last_result is None
    assert page.result_label.text().startswith("Extraction failed:")


@pytest.mark.gui
@needs_qt
def test_swapped_halves_are_a_warning_on_the_page(page, qtbot, tmp_path, monkeypatch) -> None:
    import tether.services as services
    from tether.services import ExtractResult

    seen = []

    def fake_run(request):
        seen.append(request)
        return ExtractResult(summary=_summary("swapped", output_path=tmp_path / "x.tether"))

    monkeypatch.setattr(services, "run", fake_run)
    page.set_movie(tmp_path / "movie.tif")
    page.set_field("window", "23")
    assert page.extract(tmp_path / "x.tether") is True
    _wait(qtbot, page)
    assert seen[0].window == 23 and seen[0].map_path is None and seen[0].tmap is None
    text = page.donor_side_label.text()
    assert text.startswith("Warning: 8 of 9 bleach events")
    assert "--donor-side right" in text


@pytest.mark.gui
@needs_qt
def test_one_task_at_a_time(page, qtbot, tmp_path) -> None:
    page.set_movie(_write_movie(tmp_path / "movie.tif"))
    assert page.preview() is True
    assert page.preview() is False
    assert page.messages[-1].startswith("Preview unavailable")
    _wait(qtbot, page)


@pytest.mark.gui
@needs_qt
@needs_gl
def test_the_real_viewer_shows_the_overlays_as_napari_layers(qtbot, tmp_path) -> None:
    section = ex.ExtractionSection(status=lambda _m: None)
    qtbot.addWidget(section.widget)
    try:
        section.set_movie(_write_movie(tmp_path / "movie.tif"))
        assert section.preview() is True
        _wait(qtbot, section)
        panel = section.panel
        assert panel is not None and panel.n_movies == 2
        overlay = ex.preview_overlays(section.last_preview, _SHAPE, aperture_radius=3.0)
        spots = overlay[_SPOTS]
        np.testing.assert_allclose(panel.donor_layer.data, spots.donor_xy[:, ::-1])
        section._view_combo.setCurrentIndex(1)
        assert panel.active_index == 1
        residuals = overlay[_RESIDUALS]
        np.testing.assert_allclose(panel.acceptor_layer.data, residuals.acceptor_xy[:, ::-1])
    finally:
        section.close()


# --- the page in the shell ---------------------------------------------------------------


@pytest.mark.gui
@needs_qt
def test_the_page_is_the_prepare_section_of_the_shell(section_shell) -> None:
    shell = section_shell("prepare")
    assert shell.section_page("prepare") is shell.extraction.widget
    assert shell.sections.may_write("prepare")
    assert shell.extraction.busy is False
    # The page scrolls inside itself, so it does not raise the window's minimum size.
    assert shell.section_page("prepare").minimumSizeHint().width() < 200
