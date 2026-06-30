# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether extract`` CLI + native extraction pipeline (M1 S9 PR-B).

End-to-end structural coverage of :func:`tether.project.extract.extract_movie`
and the ``tether extract`` argparse handler, on a *synthetic* dual-channel
big-endian TIFF (donor/acceptor Gaussians related by a known +1 px translation).
This locks the wiring open_movie -> split -> detect -> prealign+pair -> fit ->
colocalize -> integrate -> write_extraction and that it yields a valid ``.tether``.

The real recall/Pearson/RMS extraction-vs-Deep-LASI acceptance oracle (PRD §8
NFR-VALID(a)) needs the gated full-movie fixture + full Deep-LASI export and lands
in the follow-up PR (S9 PR-C); these synthetic tests deliberately assert
structure/round-trip, not scientific accuracy.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether.cli import build_parser, main  # noqa: E402
from tether.imaging.calibrate import LOW_CONFIDENCE_TAG  # noqa: E402
from tether.imaging.extract import read_molecules, read_patches, read_traces  # noqa: E402
from tether.io.schema import assert_is_compatible_project  # noqa: E402
from tether.project.extract import ExtractionError, ExtractOptions, extract_movie  # noqa: E402

_BG = 80.0
_AMP = 400.0
_SIGMA = 1.5
_SHAPE = (64, 96)  # 48-px-wide halves: room for the default 21-px aperture
_N_FRAMES = 12
_WINDOW = 21  # aperture/crop-box side (default); ring_outer=8 needs window >= 16

# Donor spots live in the left half (full-frame x == within-half x);
# the acceptor copy is +1 px in x in *its* half (full-frame x = within + 48).
_DONOR_CENTERS = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR_CENTERS = np.array([[61.0, 12.0], [73.0, 40.0], [65.0, 52.0]])


def _as_str(value: object) -> str:
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


def _gaussian_frame(centers: np.ndarray) -> np.ndarray:
    frame = np.full(_SHAPE, _BG, dtype=np.float64)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in centers:
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    return frame


def _write_movie(path, donor_centers=_DONOR_CENTERS, acceptor_centers=_ACCEPTOR_CENTERS) -> None:
    """Write a synthetic dual-channel big-endian uint16 movie (donor | acceptor)."""
    frame = _gaussian_frame(np.vstack([donor_centers, acceptor_centers]))
    stack = np.broadcast_to(frame, (_N_FRAMES, *_SHAPE))
    be = np.ascontiguousarray(stack, dtype=">u2")
    tifffile.imwrite(path, be, photometric="minisblack", byteorder=">")


def _make_movie(tmp_path, *, acceptor_centers=_ACCEPTOR_CENTERS, name=None) -> object:
    movie = tmp_path / (name or "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
    _write_movie(movie, _DONOR_CENTERS, acceptor_centers)
    return movie


# --- the pipeline ------------------------------------------------------------


def test_extract_movie_creates_valid_project(tmp_path) -> None:
    movie = _make_movie(tmp_path)
    out = tmp_path / "video10.tether"

    summary = extract_movie(movie, out, options=ExtractOptions(window=_WINDOW))

    # A valid, schema-compatible project was written.
    assert out.exists()
    assert assert_is_compatible_project(out) == 1

    # All three colocalized molecules survived the crop-box guardrail.
    assert summary.n_molecules == 3
    assert summary.n_control_points >= 2
    assert summary.low_confidence_registration is False
    assert summary.molecule_tags == ()

    mols = read_molecules(out)
    assert len(mols) == 3
    # Donor coordinates round-trip to the placed centers (snap is exact).
    got = {tuple(np.round(xy).astype(int)) for xy in mols["donor_xy"]}
    expected = {tuple(c.astype(int)) for c in _DONOR_CENTERS}
    assert got == expected
    # Provisional condition id + provenance are populated from the filename
    # (HDF5 string fields round-trip as bytes).
    assert all(_as_str(cid) for cid in mols["condition_id_provisional"])
    assert all(_as_str(name).endswith("_010.tif") for name in mols["source_filename"])
    # Apparent-E substrate: no correction yet (M3).
    assert np.all(np.isnan(mols["alpha"]))
    assert not any(LOW_CONFIDENCE_TAG in _as_str(t) for t in mols["tags"])

    # Traces are zero-padded to the experiment max-T (here == native T).
    traces = read_traces(out)
    assert traces["donor_corrected"].shape == (3, _N_FRAMES)
    assert traces["acceptor_corrected"].shape == (3, _N_FRAMES)

    # Cached curation patches at the requested window.
    patches = read_patches(out)
    assert patches["donor"].shape == (3, _WINDOW, _WINDOW)
    assert patches["acceptor"].shape == (3, _WINDOW, _WINDOW)

    # The registration was persisted and the single movie row links to it; the
    # effective tunables + app version are stamped into /settings/extraction.
    with h5py.File(out, "r") as f:
        assert summary.calibration_id in f["/calibration"]
        movies = f["/movies/table"]
        assert movies.shape[0] == 1
        assert _as_str(movies["calibration_id"][0]) == summary.calibration_id
        assert _as_str(movies["sha256"][0])  # non-empty content hash

        profile = json.loads(_as_str(f["/settings/extraction"].attrs["profile_json"]))
        assert profile["pipeline"] == "native"
        assert profile["registration_source"] == "native"
        assert profile["window"] == _WINDOW
        assert profile["app_version"]  # version stamped (NFR-REPRO)


def test_cli_main_extract_succeeds(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"

    rc = main(["extract", str(movie), "-o", str(out), "--window", str(_WINDOW)])

    assert rc == 0
    assert assert_is_compatible_project(out) == 1
    assert "Extracted 3 molecule(s)" in capsys.readouterr().out


# --- error handling ----------------------------------------------------------


def test_extract_refuses_existing_output_without_overwrite(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    out.write_bytes(b"not a project")

    rc = main(["extract", str(movie), "-o", str(out), "--window", str(_WINDOW)])
    assert rc == 1
    assert "output exists" in capsys.readouterr().err

    # --overwrite replaces it with a real project.
    rc = main(["extract", str(movie), "-o", str(out), "--window", str(_WINDOW), "--overwrite"])
    assert rc == 0
    assert assert_is_compatible_project(out) == 1


def test_extract_missing_movie_reports_error(tmp_path, capsys) -> None:
    out = tmp_path / "out.tether"
    rc = main(["extract", str(tmp_path / "nope.tif"), "-o", str(out)])
    assert rc == 1
    assert "movie not found" in capsys.readouterr().err
    assert not out.exists()


def test_extract_movie_rejects_bad_option() -> None:
    with pytest.raises(ExtractionError):
        ExtractOptions(donor_side="middle")


def test_extract_subcommand_requires_output() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["extract", "movie.tif"])  # missing -o/--output


def test_extract_rejects_even_window(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--window", "20"])
    assert rc == 1
    assert "odd" in capsys.readouterr().err
    assert not out.exists()


def test_extract_rejects_unreadable_movie(tmp_path, capsys) -> None:
    bogus = tmp_path / "bogus_010.tif"
    bogus.write_bytes(b"this is not a tiff")
    out = tmp_path / "out.tether"
    rc = main(["extract", str(bogus), "-o", str(out)])
    assert rc == 1
    assert "could not extract" in capsys.readouterr().err
    assert not out.exists()


def test_extract_rejects_bad_donor_side(tmp_path, capsys) -> None:
    # Bad --donor-side routes through ExtractOptions -> ExtractionError -> exit 1
    # (not argparse's usage exit 2), the documented operator-actionable contract.
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--donor-side", "upside"])
    assert rc == 1
    assert "donor_side" in capsys.readouterr().err
    assert not out.exists()


def test_extract_too_few_control_points_errors(tmp_path, capsys) -> None:
    # One spot per half -> at most one matched pair -> registration cannot fit.
    movie = tmp_path / "single_010.tif"
    _write_movie(movie, np.array([[16.0, 32.0]]), np.array([[64.0, 32.0]]))
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out)])
    assert rc == 1
    assert "registration failed" in capsys.readouterr().err
    assert not out.exists()


# --- registration branches ---------------------------------------------------


def test_extract_donor_side_right_uses_right_half(tmp_path) -> None:
    # With donor_side="right" the donor channel IS the right half, so the donor
    # coordinates are that half's spots (the acceptor pattern), not the left's.
    movie = _make_movie(tmp_path)
    out = tmp_path / "right.tether"
    summary = extract_movie(movie, out, options=ExtractOptions(donor_side="right"))
    assert summary.n_molecules == 3
    mols = read_molecules(out)
    got = {tuple(np.round(xy).astype(int)) for xy in mols["donor_xy"]}
    right_half = {(int(x - 48), int(y)) for x, y in _ACCEPTOR_CENTERS}  # within-half coords
    assert got == right_half
    assert got != {tuple(c.astype(int)) for c in _DONOR_CENTERS}


def test_extract_similarity_prealign_is_dispatched(tmp_path, monkeypatch) -> None:
    # Verify the similarity branch is actually selected (the test would otherwise
    # pass even if --prealign were ignored and the default translation path ran).
    import tether.project.extract as ext

    called = {}
    real = ext.estimate_similarity_prealign

    def spy(*args, **kwargs):
        called["similarity"] = True
        return real(*args, **kwargs)

    monkeypatch.setattr(ext, "estimate_similarity_prealign", spy)
    movie = _make_movie(tmp_path)
    out = tmp_path / "sim.tether"
    summary = ext.extract_movie(movie, out, options=ExtractOptions(prealign="similarity"))
    assert called.get("similarity") is True
    assert summary.n_molecules == 3


# A uniform ~+2 px x-shift (so the phase-correlation prealign locks on) plus a
# +1 px outlier on one spot: donor + [(+2,0),(+2,0),(+3,0)]. All three still pair,
# but the residual (~0.34 px) exceeds a deliberately tightened gate.
_OVER_GATE_ACCEPTOR = np.array([[62.0, 12.0], [74.0, 40.0], [67.0, 52.0]])


def test_low_confidence_registration_flags_not_drops(tmp_path) -> None:
    movie = _make_movie(tmp_path, acceptor_centers=_OVER_GATE_ACCEPTOR, name="warp_010.tif")
    out = tmp_path / "warp.tether"
    summary = extract_movie(movie, out, options=ExtractOptions(rms_gate=0.2))
    # ADR-0014 contract: an over-gate fit is flagged, never dropped.
    assert summary.low_confidence_registration is True
    assert summary.rms_residual > 0.2
    assert summary.molecule_tags == (LOW_CONFIDENCE_TAG,)
    assert summary.n_molecules == 3
    mols = read_molecules(out)
    assert all(LOW_CONFIDENCE_TAG in _as_str(t) for t in mols["tags"])
