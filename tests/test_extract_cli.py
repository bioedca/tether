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
from pathlib import Path

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
from tether.imaging.register import PolyTransform2D, TmapChannel  # noqa: E402
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


# --- imported .tmap registration path (S9 PR-C1; §7.1 "native AND imported") --
#
# There is no committed .tmap (the MCOS format is impractical to author, and
# read_tmap's real decode is covered data-present in test_register.py). The
# imported branch is exercised by reconstructing TmapChannel objects in memory --
# the same shape read_tmap returns -- encoding the same +1 px map the native fit
# recovers, and monkeypatching the decode at the extract.py seam.


def _synthetic_tmap_channels(
    *, acceptor_rotation=None, acceptor_flip=None
) -> dict[int, TmapChannel]:
    """Decoded-.tmap channels for the synthetic movie: donor=left, acceptor=right.

    The acceptor's reference->channel transform is the +1 px x translation that
    relates the synthetic halves (1-based MATLAB frame), so an imported extraction
    must match the native fit (apply-both parity, §7.1 / §9 M1). Crops are the L/R
    halves of ``_SHAPE`` ([[y1, x1], [y2, x2]] as Deep-LASI stores them).

    ``acceptor_rotation`` / ``acceptor_flip`` default to identity (empty, as the
    real UCKOPSB .tmap stores them); pass a non-identity value to exercise the
    refuse-non-identity-geometry guard.
    """
    eye = np.eye(3)
    identity = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=eye,
        norm_uv=eye,
    )
    ref_to_acceptor = PolyTransform2D(  # u = x + 1, v = y
        a=np.array([1.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=eye,
        norm_uv=eye,
    )
    acceptor_to_ref = PolyTransform2D(  # x = u - 1, y = v
        a=np.array([-1.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=eye,
        norm_uv=eye,
    )
    no_particles = np.zeros((0, 2))
    donor = TmapChannel(
        channel_id=0,
        crop=np.array([[1, 1], [64, 48]]),  # left half
        map_particles=no_particles,
        ref_to_channel=identity,  # reference->reference; unused by the builder
        channel_to_ref=identity,
    )
    acceptor = TmapChannel(
        channel_id=1,
        crop=np.array([[1, 49], [64, 96]]),  # right half
        map_particles=no_particles,
        ref_to_channel=ref_to_acceptor,
        channel_to_ref=acceptor_to_ref,
        rotation=np.array([]) if acceptor_rotation is None else np.asarray(acceptor_rotation),
        flip=np.array([]) if acceptor_flip is None else np.asarray(acceptor_flip),
    )
    return {0: donor, 1: acceptor}


def _stub_tmap(tmp_path) -> object:
    """A stub .tmap file; its bytes are never decoded (read_tmap is monkeypatched)."""
    path = tmp_path / "map.tmap"
    path.write_bytes(b"stub .tmap; decode is monkeypatched at the extract seam")
    return path


def test_extract_imported_tmap_creates_valid_project(tmp_path, monkeypatch) -> None:
    import tether.project.extract as ext

    channels = _synthetic_tmap_channels()
    monkeypatch.setattr(ext, "read_tmap", lambda _path: channels)
    movie = _make_movie(tmp_path)
    tmap = _stub_tmap(tmp_path)
    out = tmp_path / "imported.tether"

    summary = ext.extract_movie(movie, out, tmap=tmap, options=ExtractOptions(window=_WINDOW))

    assert assert_is_compatible_project(out) == 1
    assert summary.registration_source == "imported"
    assert summary.n_molecules == 3
    # An imported bead map is trusted: no sample control points -> unknown residual,
    # so it is never flagged low-confidence (the molecule-domain scatter of a bead
    # map is colocalization, not registration, error -- ADR-0014).
    assert summary.n_control_points == 0
    assert np.isnan(summary.rms_residual)
    assert summary.low_confidence_registration is False
    assert summary.molecule_tags == ()

    mols = read_molecules(out)
    got = {tuple(np.round(xy).astype(int)) for xy in mols["donor_xy"]}
    assert got == {tuple(c.astype(int)) for c in _DONOR_CENTERS}
    assert not any(LOW_CONFIDENCE_TAG in _as_str(t) for t in mols["tags"])

    # /settings records the imported source + the .tmap filename (NFR-REPRO); the
    # extraction pipeline itself stays native.
    with h5py.File(out, "r") as f:
        profile = json.loads(_as_str(f["/settings/extraction"].attrs["profile_json"]))
        assert profile["registration_source"] == "imported"
        assert profile["tmap_source"] == "map.tmap"
        assert profile["registration_rms_px"] is None  # NaN -> null
        assert profile["pipeline"] == "native"


def test_imported_tmap_matches_native_apply_both_parity(tmp_path, monkeypatch) -> None:
    # §7.1 / §9 M1: the imported .tmap warps to the same coordinates as a native
    # fit, so the two extractions yield the same molecules.
    movie = _make_movie(tmp_path)
    native_out = tmp_path / "native.tether"
    native = extract_movie(movie, native_out, options=ExtractOptions(window=_WINDOW))

    import tether.project.extract as ext

    monkeypatch.setattr(ext, "read_tmap", lambda _path: _synthetic_tmap_channels())
    imported_out = tmp_path / "imported.tether"
    imported = ext.extract_movie(
        movie, imported_out, tmap=_stub_tmap(tmp_path), options=ExtractOptions(window=_WINDOW)
    )

    assert imported.n_molecules == native.n_molecules == 3
    nat, imp = read_molecules(native_out), read_molecules(imported_out)
    np.testing.assert_allclose(
        np.array(sorted(imp["donor_xy"].tolist())),
        np.array(sorted(nat["donor_xy"].tolist())),
        atol=0.01,
    )
    # The donor-anchored acceptor read positions agree within a sub-pixel tolerance.
    np.testing.assert_allclose(
        np.array(sorted(imp["acceptor_xy"].tolist())),
        np.array(sorted(nat["acceptor_xy"].tolist())),
        atol=0.5,
    )


def test_cli_extract_with_tmap_succeeds(tmp_path, monkeypatch, capsys) -> None:
    import tether.project.extract as ext

    monkeypatch.setattr(ext, "read_tmap", lambda _path: _synthetic_tmap_channels())
    movie = _make_movie(tmp_path)
    tmap = _stub_tmap(tmp_path)
    out = tmp_path / "cli_imported.tether"

    rc = main(
        ["extract", str(movie), "-o", str(out), "--tmap", str(tmap), "--window", str(_WINDOW)]
    )

    assert rc == 0
    assert assert_is_compatible_project(out) == 1
    output = capsys.readouterr().out
    assert "Extracted 3 molecule(s)" in output
    assert "imported from" in output


def test_extract_tmap_not_found_errors(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--tmap", str(tmp_path / "nope.tmap")])
    assert rc == 1
    assert "tmap not found" in capsys.readouterr().err
    assert not out.exists()


def test_extract_undecodable_tmap_errors(tmp_path, monkeypatch, capsys) -> None:
    # A decode failure maps to a clean .tmap-centric ExtractionError (-> exit 1),
    # never a raw traceback, and nothing is written.
    import tether.project.extract as ext

    def _boom(_path):
        raise ValueError("not a Deep-LASI .tmap (no 'm' cell)")

    monkeypatch.setattr(ext, "read_tmap", _boom)
    movie = _make_movie(tmp_path)
    tmap = _stub_tmap(tmp_path)
    out = tmp_path / "out.tether"

    rc = main(["extract", str(movie), "-o", str(out), "--tmap", str(tmap)])
    assert rc == 1
    assert "could not use --tmap" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(
    "geometry",
    [{"acceptor_rotation": np.array([90.0])}, {"acceptor_flip": np.array([1, 0])}],
    ids=["rotation", "flip"],
)
def test_extract_imported_tmap_nonidentity_geometry_refused(
    tmp_path, monkeypatch, capsys, geometry
) -> None:
    # A .tmap whose channel carries a non-identity rotation OR flip is refused
    # loudly (the imported path applies only crop geometry so far), never silently
    # mis-split. Both axes are covered so a flip regression can't slip through.
    import tether.project.extract as ext

    channels = _synthetic_tmap_channels(**geometry)
    monkeypatch.setattr(ext, "read_tmap", lambda _path: channels)
    movie = _make_movie(tmp_path)
    tmap = _stub_tmap(tmp_path)
    out = tmp_path / "out.tether"

    rc = main(["extract", str(movie), "-o", str(out), "--tmap", str(tmap)])
    assert rc == 1
    assert "rotation/flip" in capsys.readouterr().err
    assert not out.exists()


# --- selectable detection mode + threshold (S9 PR-C3c; PRD §11.2, ADR-0021) --
#
# The three detectors themselves are unit-tested in test_detect.py; here we lock
# that the extraction pipeline *selects* the mode/threshold and records the choice
# into /settings/extraction (NFR-REPRO). The synthetic movie's three clean
# Gaussians are detected identically by all three modes, so the molecule count is
# a stable invariant across modes (the per-detector behaviour differs only on the
# real, textured fixture, exercised by the gated oracle in PR-C3d).


def _extraction_profile(path) -> dict:
    """Read the JSON ``/settings/extraction`` provenance profile from a ``.tether``."""
    with h5py.File(path, "r") as f:
        return json.loads(_as_str(f["/settings/extraction"].attrs["profile_json"]))


def test_extract_default_records_wavelet_mode(tmp_path) -> None:
    # The default pipeline is the historical à trous detector, recorded verbatim.
    movie = _make_movie(tmp_path)
    out = tmp_path / "default.tether"
    summary = extract_movie(movie, out, options=ExtractOptions(window=_WINDOW))
    assert summary.n_molecules == 3
    profile = _extraction_profile(out)
    assert profile["detection_mode"] == "wavelet"
    assert profile["detection_threshold"] is None  # None -> JSON null


@pytest.mark.parametrize("mode", ["intensity", "bandpass"])
def test_extract_alternate_mode_is_recorded(tmp_path, mode) -> None:
    # A non-default mode + explicit threshold round-trips into /settings and still
    # produces a valid project with the same three synthetic molecules.
    movie = _make_movie(tmp_path)
    out = tmp_path / f"{mode}.tether"
    summary = extract_movie(
        movie,
        out,
        options=ExtractOptions(window=_WINDOW, detection_mode=mode, detection_threshold=0.4),
    )
    assert assert_is_compatible_project(out) == 1
    assert summary.n_molecules == 3
    profile = _extraction_profile(out)
    assert profile["detection_mode"] == mode
    assert profile["detection_threshold"] == pytest.approx(0.4)


def test_extract_detection_mode_threshold_reach_the_detector(tmp_path, monkeypatch) -> None:
    # Prove the selected mode/threshold actually flow to the detector (the test
    # would otherwise pass even if _detect_channels ignored the options and ran
    # the default). Spy on the dispatch seam and capture its kwargs.
    import tether.project.extract as ext

    calls = []
    real = ext.detect_spots_by_mode

    def spy(image, **kwargs):
        calls.append(kwargs)
        return real(image, **kwargs)

    monkeypatch.setattr(ext, "detect_spots_by_mode", spy)
    movie = _make_movie(tmp_path)
    out = tmp_path / "spy.tether"
    ext.extract_movie(
        movie,
        out,
        options=ExtractOptions(detection_mode="bandpass", detection_threshold=0.7),
    )
    # Called once per half (donor + acceptor), both with the selected mode/threshold.
    assert len(calls) == 2
    assert all(c["mode"] == "bandpass" for c in calls)
    assert all(c["threshold"] == pytest.approx(0.7) for c in calls)


def test_extract_rejects_bad_detection_mode(tmp_path, capsys) -> None:
    # Programmatic: a bad mode fails fast with a clean ExtractionError.
    with pytest.raises(ExtractionError, match="detection_mode"):
        ExtractOptions(detection_mode="quantum")
    # Via the CLI it routes through ExtractOptions -> exit 1 (not argparse's exit 2),
    # the same operator-actionable contract as --donor-side.
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--detection-mode", "quantum"])
    assert rc == 1
    assert "detection_mode" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("bad", ["-0.1", "1.0", "1.5"])
def test_extract_rejects_out_of_range_detection_threshold(tmp_path, capsys, bad) -> None:
    # The detectors' domain is [0, 1); the boundary 1.0 and either side are refused.
    with pytest.raises(ExtractionError, match="detection_threshold"):
        ExtractOptions(detection_threshold=float(bad))
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--detection-threshold", bad])
    assert rc == 1
    assert "detection_threshold" in capsys.readouterr().err
    assert not out.exists()


def test_cli_detection_mode_threshold_flags_flow(tmp_path) -> None:
    # The CLI flags reach /settings/extraction end-to-end.
    movie = _make_movie(tmp_path)
    out = tmp_path / "cli_mode.tether"
    rc = main(
        [
            "extract",
            str(movie),
            "-o",
            str(out),
            "--window",
            str(_WINDOW),
            "--detection-mode",
            "intensity",
            "--detection-threshold",
            "0.55",
        ]
    )
    assert rc == 0
    profile = _extraction_profile(out)
    assert profile["detection_mode"] == "intensity"
    assert profile["detection_threshold"] == pytest.approx(0.55)


# --- imported .tdat detection-mode/threshold auto-apply (S9 PR-C3c-decode-A/B) --
#
# A movie carries no record of which findPart method/threshold detected it; a
# sibling Deep-LASI .tdat does (temp/ParticleDetectionMode + the per-channel MCOS
# DetectionThreshold). --tdat reads both and applies them, so a native
# re-extraction matches how the data was actually detected (NFR-REPRO). The
# .tdat-decode itself is unit-tested in test_tdat.py; here we lock the extract
# wiring + provenance for both a plain-leaf .tdat (mode only, no MCOS blob) and the
# committed MCOS-carrying fixture (mode + decoded threshold).

_TDAT_FIXTURE = Path(__file__).parent / "fixtures" / "tdat_coloc_slice.tdat"
_TDAT_FIXTURE_THRESHOLD = 0.330097  # reference (donor) channel DetectionThreshold


def _write_tdat(path, *, mode_code: float = 2.0) -> object:
    """Write a minimal .tdat carrying only ``temp/ParticleDetectionMode`` (no MCOS).

    Enough for the detection-settings reader (mode 2 == intensity); with no MCOS
    ``Channel`` blob the decoded threshold is ``None``. The heavier
    coordinate/correction/threshold decode is covered in test_tdat.py.
    """
    with h5py.File(path, "w") as f:
        temp = f.create_group("temp")
        ds = temp.create_dataset("ParticleDetectionMode", data=np.array([[mode_code]]))
        ds.attrs["MATLAB_class"] = np.bytes_("double")
    return path


def test_extract_tdat_applies_decoded_mode(tmp_path) -> None:
    # A .tdat with ParticleDetectionMode == 2 (intensity) overrides the default
    # wavelet; the applied mode is reported and recorded with its provenance.
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat", mode_code=2.0)
    out = tmp_path / "tdat.tether"
    summary = extract_movie(movie, out, tdat=tdat, options=ExtractOptions(window=_WINDOW))
    assert assert_is_compatible_project(out) == 1
    assert summary.n_molecules == 3
    assert summary.detection_mode == "intensity"
    profile = _extraction_profile(out)
    assert profile["detection_mode"] == "intensity"
    assert profile["tdat_source"] == "data.tdat"
    # This .tdat carries no MCOS ``Channel`` blob, so no threshold is decoded: the
    # detector keeps its faithful default and None is recorded, not a fabricated
    # value. (The MCOS-carrying case is test_extract_tdat_applies_decoded_threshold.)
    assert profile["detection_threshold"] is None


def test_extract_tdat_preserves_caller_threshold(tmp_path) -> None:
    # A plain-leaf .tdat (no MCOS blob) supplies only the mode, decoding
    # threshold=None, so a caller-supplied threshold must SURVIVE the apply, not be
    # wiped to None -- guarding the ``else`` branch of the _apply_tdat_detection
    # threshold conditional against a regression that nulled it on the .tdat path.
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat", mode_code=2.0)  # intensity
    out = tmp_path / "keep.tether"
    summary = extract_movie(
        movie, out, tdat=tdat, options=ExtractOptions(window=_WINDOW, detection_threshold=0.3)
    )
    assert summary.detection_mode == "intensity"  # mode comes from the .tdat
    profile = _extraction_profile(out)
    assert profile["detection_mode"] == "intensity"
    assert profile["detection_threshold"] == pytest.approx(0.3)  # caller's threshold kept
    assert profile["tdat_source"] == "data.tdat"


def test_extract_tdat_applies_decoded_threshold(tmp_path) -> None:
    # An MCOS-carrying .tdat (the committed UCKOPSB fixture) supplies BOTH the mode
    # and the mapping-reference channel's decoded DetectionThreshold; the threshold
    # overrides the caller's and is recorded with its provenance (NFR-REPRO).
    movie = _make_movie(tmp_path)
    out = tmp_path / "tdat_threshold.tether"
    summary = extract_movie(
        movie,
        out,
        tdat=_TDAT_FIXTURE,
        options=ExtractOptions(window=_WINDOW, detection_threshold=0.3),  # overridden by the .tdat
    )
    assert summary.detection_mode == "intensity"
    profile = _extraction_profile(out)
    assert profile["detection_mode"] == "intensity"
    assert profile["detection_threshold"] == pytest.approx(_TDAT_FIXTURE_THRESHOLD)
    assert profile["tdat_source"] == "tdat_coloc_slice.tdat"


def test_extract_accepts_zero_detection_threshold(tmp_path) -> None:
    # 0.0 is the inclusive lower bound of the detectors' [0, 1) domain: it must be
    # ACCEPTED (locks `0.0 <=`, not `0.0 <`). The round-trip uses the default wavelet
    # mode (where the threshold is inert-but-recorded), so it is detector-independent.
    ExtractOptions(detection_threshold=0.0)  # must not raise
    movie = _make_movie(tmp_path)
    out = tmp_path / "zero.tether"
    extract_movie(movie, out, options=ExtractOptions(window=_WINDOW, detection_threshold=0.0))
    assert _extraction_profile(out)["detection_threshold"] == 0.0


def test_cli_extract_with_tdat_succeeds(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat", mode_code=3.0)  # bandpass
    out = tmp_path / "cli_tdat.tether"
    rc = main(
        ["extract", str(movie), "-o", str(out), "--window", str(_WINDOW), "--tdat", str(tdat)]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "Extracted 3 molecule(s)" in output
    assert "detection: mode 'bandpass' from" in output
    assert _extraction_profile(out)["detection_mode"] == "bandpass"


def test_extract_tdat_mode_reaches_the_detector(tmp_path, monkeypatch) -> None:
    # Prove the .tdat-decoded mode actually flows to the detector, not just the
    # provenance dict (the spy seam mirrors the --detection-mode flow test).
    import tether.project.extract as ext

    calls = []
    real = ext.detect_spots_by_mode
    monkeypatch.setattr(
        ext, "detect_spots_by_mode", lambda image, **kw: (calls.append(kw), real(image, **kw))[1]
    )
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat", mode_code=2.0)
    ext.extract_movie(movie, tmp_path / "spy.tether", tdat=tdat)
    assert len(calls) == 2  # donor + acceptor halves
    assert all(c["mode"] == "intensity" for c in calls)


def test_cli_tdat_and_detection_mode_are_mutually_exclusive(tmp_path, capsys) -> None:
    # --tdat and --detection-mode both set the detection method; combining them is
    # ambiguous and refused (exit 1, nothing written).
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat")
    out = tmp_path / "out.tether"
    rc = main(
        ["extract", str(movie), "-o", str(out), "--tdat", str(tdat), "--detection-mode", "wavelet"]
    )
    assert rc == 1
    assert "cannot be combined with --tdat" in capsys.readouterr().err
    assert not out.exists()


def test_cli_tdat_and_detection_threshold_are_mutually_exclusive(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat")
    out = tmp_path / "out.tether"
    rc = main(
        ["extract", str(movie), "-o", str(out), "--tdat", str(tdat), "--detection-threshold", "0.3"]
    )
    assert rc == 1
    assert "cannot be combined with --tdat" in capsys.readouterr().err
    assert not out.exists()


def test_extract_tdat_not_found_errors(tmp_path, capsys) -> None:
    movie = _make_movie(tmp_path)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--tdat", str(tmp_path / "nope.tdat")])
    assert rc == 1
    assert "tdat not found" in capsys.readouterr().err
    assert not out.exists()


def test_extract_tdat_unsupported_mode_errors(tmp_path, capsys) -> None:
    # A .tdat saved with an unported findPart mode (4 local-variance / 5 ZMW) is
    # refused with a clean .tdat-centric error -- never silently mis-detected.
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat", mode_code=4.0)
    out = tmp_path / "out.tether"
    rc = main(["extract", str(movie), "-o", str(out), "--tdat", str(tdat)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not use --tdat" in err
    assert "not supported" in err
    assert not out.exists()


def test_extract_tdat_composes_with_tmap(tmp_path, monkeypatch) -> None:
    # --tdat (detection) and --tmap (registration) are independent and compose: the
    # run imports registration from the .tmap AND the detection mode from the .tdat.
    import tether.project.extract as ext

    monkeypatch.setattr(ext, "read_tmap", lambda _path: _synthetic_tmap_channels())
    movie = _make_movie(tmp_path)
    tdat = _write_tdat(tmp_path / "data.tdat", mode_code=2.0)
    out = tmp_path / "both.tether"
    summary = ext.extract_movie(
        movie, out, tmap=_stub_tmap(tmp_path), tdat=tdat, options=ExtractOptions(window=_WINDOW)
    )
    assert summary.registration_source == "imported"
    assert summary.detection_mode == "intensity"
    profile = _extraction_profile(out)
    assert profile["tmap_source"] == "map.tmap"
    assert profile["tdat_source"] == "data.tdat"
