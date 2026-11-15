# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The headless stage preview reproduces ``extract_movie`` stage for stage (#348).

The central claim is bit-identity: for the same movie and :class:`ExtractOptions`, the
preview's molecules and traces equal the ones ``extract_movie`` builds in memory, bit for
bit, on the native fit, the similarity prealign, an over-gate fit and an imported map. The
other tests pin what a preview panel reads: the stage order, the parameters each stage
records, the over-gate outcome, a failure as a yielded stage, and that the preview writes
nothing and imports no Qt.
"""

from __future__ import annotations

import builtins
import dataclasses
import subprocess
import sys
import warnings

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

import tether.project.extract as ext  # noqa: E402
from tether import __version__  # noqa: E402
from tether.imaging import (  # noqa: E402
    LOW_CONFIDENCE_TAG,
    ChannelGeometry,
    PolyTransform2D,
    TmapChannel,
    read_traces,
    registration_map_from_tmap,
)
from tether.io.movie import open_movie  # noqa: E402
from tether.project.extract import ExtractOptions  # noqa: E402
from tether.project.preview import (  # noqa: E402
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_OVER_GATE,
    OUTCOME_SKIPPED,
    STAGES,
    iter_extraction_stages,
    preview_extraction,
)

_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_SHAPE = (64, 96)  # 48-px halves
_N_FRAMES = 12
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = np.array([[61.0, 12.0], [73.0, 40.0], [65.0, 52.0]])  # +1 px in its half
# A +2 px shift plus a +1 px outlier: all pair, residual ~0.34 px (over a 0.2 px gate).
_OVER_GATE_ACCEPTOR = np.array([[62.0, 12.0], [74.0, 40.0], [67.0, 52.0]])
_TRACE_FIELDS = ("intensity", "total", "background", "valid")


def _movie_array(acceptor: np.ndarray = _ACCEPTOR, donor: np.ndarray = _DONOR) -> np.ndarray:
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    frame = np.full(_SHAPE, _BG)
    for x, y in np.vstack([donor, acceptor]):
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    return np.ascontiguousarray(np.broadcast_to(frame, (_N_FRAMES, *_SHAPE)), dtype=">u2")


def _write_movie(path, acceptor: np.ndarray = _ACCEPTOR):
    tifffile.imwrite(path, _movie_array(acceptor), photometric="minisblack", byteorder=">")
    return path


def _tmap_channels() -> dict[int, TmapChannel]:
    """Decoded-.tmap channels for the synthetic movie: left donor, right acceptor, +1 px."""

    def poly(dx: float) -> PolyTransform2D:
        return PolyTransform2D(
            a=np.array([dx, 1.0, 0.0, 0.0, 0.0, 0.0]),
            b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
            norm_xy=np.eye(3),
            norm_uv=np.eye(3),
        )

    none = np.zeros((0, 2))
    return {
        0: TmapChannel(0, np.array([[1, 1], [64, 48]]), none, poly(0.0), poly(0.0)),
        1: TmapChannel(1, np.array([[1, 49], [64, 96]]), none, poly(1.0), poly(-1.0)),
    }


def _run_extract_movie(tmp_path, monkeypatch, movie, options, **kwargs):
    """Run ``extract_movie`` and capture the molecules and traces it builds in memory."""
    seen: dict[str, object] = {}

    def spy(name):
        real = getattr(ext, name)

        def wrapped(*args, **kw):
            seen[name] = real(*args, **kw)
            return seen[name]

        monkeypatch.setattr(ext, name, wrapped)

    spy("colocalize")
    spy("extract_molecules")
    out = tmp_path / "run.tether"
    summary = ext.extract_movie(movie, out, options=options, **kwargs)
    return summary, seen["colocalize"], seen["extract_molecules"], out


def _assert_bit_identical(preview, molecules, traces, out) -> None:
    assert preview.completed
    for name in ("donor_xy", "acceptor_xy", "acceptor_detected", "donor_index", "acceptor_index"):
        got, want = getattr(preview.molecules, name), getattr(molecules, name)
        assert got.dtype == want.dtype and got.shape == want.shape
        assert got.tobytes() == want.tobytes(), name
    for channel in ("donor", "acceptor"):
        for name in _TRACE_FIELDS:
            got = getattr(getattr(preview.traces, channel), name)
            want = getattr(getattr(traces, channel), name)
            assert got.dtype == want.dtype and got.tobytes() == want.tobytes(), (channel, name)
        got = getattr(preview.traces, f"{channel}_patches")
        assert got.tobytes() == getattr(traces, f"{channel}_patches").tobytes()
    # And what the run stored is the preview's arrays at the store's float32 width.
    stored = read_traces(out)
    np.testing.assert_array_equal(
        stored["donor_corrected"], preview.traces.donor.intensity.astype("<f4")
    )
    np.testing.assert_array_equal(
        stored["acceptor_corrected"], preview.traces.acceptor.intensity.astype("<f4")
    )


@pytest.mark.parametrize(
    ("options", "acceptor"),
    [
        (ExtractOptions(), _ACCEPTOR),
        (ExtractOptions(prealign="similarity"), _ACCEPTOR),
        (ExtractOptions(rms_gate=0.2), _OVER_GATE_ACCEPTOR),
    ],
    ids=["translation", "similarity", "over-gate"],
)
def test_native_preview_is_bit_identical_to_extract_movie(
    tmp_path, monkeypatch, options, acceptor
) -> None:
    movie_path = _write_movie(tmp_path / "Bla_010.tif", acceptor)
    summary, molecules, traces, out = _run_extract_movie(tmp_path, monkeypatch, movie_path, options)
    with open_movie(movie_path) as reader:
        preview = preview_extraction(reader.data, options=options)
        _assert_bit_identical(preview, molecules, traces, out)
    assert summary.n_molecules == preview.molecules.n_molecules == 3
    assert preview.registration.rms_residual == summary.rms_residual
    assert preview.registration.low_confidence is summary.low_confidence_registration


def test_imported_preview_is_bit_identical_to_extract_movie(tmp_path, monkeypatch) -> None:
    channels = _tmap_channels()
    monkeypatch.setattr(ext, "read_tmap", lambda _path: channels)
    tmap = tmp_path / "map.tmap"
    tmap.write_bytes(b"stub; the decode is monkeypatched")
    movie_path = _write_movie(tmp_path / "Bla_010.tif")
    options = ExtractOptions()
    _, molecules, traces, out = _run_extract_movie(
        tmp_path, monkeypatch, movie_path, options, tmap=tmap
    )
    registration = registration_map_from_tmap(
        channels, app_version=__version__, source_file="map.tmap"
    )
    with open_movie(movie_path) as reader:
        preview = preview_extraction(reader.data, options=options, registration=registration)
        _assert_bit_identical(preview, molecules, traces, out)
    assert [s.outcome for s in preview.stages[2:4]] == [OUTCOME_SKIPPED, OUTCOME_SKIPPED]
    assert preview.stage("split").parameters["geometry_source"] == "registration"
    assert preview.stage("register").parameters["source"] == "imported"


def test_stages_run_in_order_and_record_their_parameters() -> None:
    options = ExtractOptions(window=19, pair_tol=1.5, coloc_distance=2.5, bg_window=7)
    preview = preview_extraction(_movie_array(), options=options)
    assert tuple(s.name for s in preview.stages) == STAGES
    assert all(s.outcome == OUTCOME_OK for s in preview.stages)
    split = preview.stage("split").parameters
    assert split["geometry_source"] == "half-split" and split["donor_side"] == "left"
    assert split["donor_crop"] == [1, 1, 64, 48] and split["acceptor_crop"] == [1, 49, 64, 96]
    assert preview.stage("detect").parameters["detection_mode"] == "wavelet"
    assert preview.stage("prealign").parameters["prealign"] == "translation"
    assert preview.stage("pair").parameters == {"pair_tol": 1.5}
    assert preview.stage("register").parameters["rms_gate"] == options.rms_gate
    assert preview.stage("colocalize").parameters == {
        "policy": "donor-anchored",
        "window": 19,
        "coloc_distance": 2.5,
    }
    assert preview.stage("extract").parameters["bg_window"] == 7
    assert preview.stage("detect").outputs["donor_detection_image"].shape == (64, 48)
    with pytest.raises(TypeError):
        preview.stage("pair").parameters["pair_tol"] = 9.0  # read-only for the panel


def test_over_gate_is_a_stage_outcome_not_a_swallowed_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        preview = preview_extraction(
            _movie_array(_OVER_GATE_ACCEPTOR), options=ExtractOptions(rms_gate=0.2)
        )
    register = preview.stage("register")
    assert register.outcome == OUTCOME_OVER_GATE
    assert "exceeds the 0.2 px gate" in register.message
    assert register.outputs["molecule_tags"] == (LOW_CONFIDENCE_TAG,)
    assert preview.completed and preview.molecules.n_molecules == 3  # flagged, not dropped
    assert caught == []


def test_too_few_pairs_ends_the_preview_with_a_failed_stage() -> None:
    # One spot per half: at most one pair, and a fit needs two (as extract_movie refuses).
    movie = _movie_array(np.array([[64.0, 32.0]]), donor=np.array([[16.0, 32.0]]))
    preview = preview_extraction(movie)
    last = preview.stages[-1]
    assert (last.name, last.outcome) == ("pair", OUTCOME_FAILED)
    assert last.outputs["n_pairs"] < 2 and "need >= 2" in last.message
    assert not preview.completed and preview.traces is None and preview.registration is None


def test_a_primitive_failure_is_yielded_not_raised() -> None:
    narrow = np.zeros((3, 8, 1), dtype=">u2")  # one column: no two halves
    stages = list(iter_extraction_stages(narrow))
    assert [(s.name, s.outcome) for s in stages] == [("split", OUTCOME_FAILED)]
    assert "too narrow" in stages[0].message


def test_geometry_and_policy_are_arguments() -> None:
    left = ChannelGeometry(crop=(1, 1, 64, 48))
    right = ChannelGeometry(crop=(1, 49, 64, 96))
    preview = preview_extraction(_movie_array(), geometry=(left, right), policy="donor-anchored")
    assert preview.stage("split").parameters["geometry_source"] == "argument"
    assert preview.stage("colocalize").parameters["policy"] == "donor-anchored"
    swapped = preview_extraction(_movie_array(), geometry=(right, left))
    assert swapped.stage("split").parameters["donor_crop"] == [1, 49, 64, 96]
    # The whole transform is recorded, not only the crop: two geometries that differ
    # only in rotation or flip must not record the same settings.
    flipped = ChannelGeometry(crop=(1, 1, 64, 48), rotation_deg=180, flip=(1, 0))
    split = preview_extraction(_movie_array(), geometry=(flipped, right)).stage("split")
    assert split.parameters["donor_rotation_deg"] == 180
    assert split.parameters["donor_flip"] == [1, 0]
    assert split.parameters["acceptor_rotation_deg"] == 0
    assert split.parameters["acceptor_flip"] == [0, 0]
    with pytest.raises(ValueError, match="policy"):
        preview_extraction(_movie_array(), policy="partner-in-both")


def test_preview_writes_nothing(tmp_path, monkeypatch) -> None:
    import h5py

    real_open = builtins.open

    def read_only_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError(f"preview opened {file!r} for writing")
        return real_open(file, mode, *args, **kwargs)

    def no_hdf5(*args, **kwargs):
        raise AssertionError("preview opened an HDF5 file")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "open", read_only_open)
    monkeypatch.setattr(h5py, "File", no_hdf5)
    assert preview_extraction(_movie_array()).completed
    assert list(tmp_path.iterdir()) == []


def test_preview_imports_no_qt() -> None:
    code = (
        "import sys, tether.project.preview\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'PySide6', 'PyQt5', 'pyqtgraph', 'napari', 'qtpy'} or m.startswith('tether.gui'))\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603


def test_an_over_gate_supplied_map_is_reported_with_its_own_gate() -> None:
    # A supplied map can carry a measured residual over its own gate (a .tmap with
    # control points); the preview reports it and still extracts, it does not raise.
    imported = registration_map_from_tmap(_tmap_channels(), app_version=__version__)
    over = dataclasses.replace(imported, rms_residual=0.9, gate_px=0.4)
    preview = preview_extraction(_movie_array(), registration=over)
    register = preview.stage("register")
    assert register.outcome == OUTCOME_OVER_GATE
    assert "0.900 px exceeds the 0.4 px gate" in register.message
    assert register.outputs["molecule_tags"] == (LOW_CONFIDENCE_TAG,)
    assert preview.completed and preview.molecules.n_molecules == 3
