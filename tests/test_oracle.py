# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the extraction-vs-Deep-LASI acceptance oracle (M1 S9; PRD §9 M1).

Four layers:

* **pure scorer** (numpy only, always run): ``match_coordinates`` /
  ``coordinate_rms`` / ``evaluate_extraction`` on hand-built arrays — perfect
  agreement → recall 1 / r 1 / rms 0; planted miss drops recall; planted noise
  drops Pearson; the RMS gate; the zero-pad exclusion.
* **real-slice** (needs scipy for the ``.mat``): the Pearson machinery on the
  committed 4-molecule Deep-LASI slice — the ``.txt`` corrected traces correlate
  with the ``.mat`` corrected traces to r ≈ 1 (real numbers, no movie needed).
* **wiring**: :func:`~tether.project.oracle.evaluate_project` plumbs
  ``/molecules`` (``donor_xy`` + ``frame_range``) and ``/traces`` into the scorer
  (the readers are monkeypatched, so no h5py/disk needed).
* **data-present, gated** (``@pytest.mark.large``; skipped without the gated
  UCKOPSB movie + ``.tmap`` + ``.tdat`` + ``.mat``): the full native extraction
  (imported ``.tmap`` + ``.tdat``-decoded mode-2 detection) meets the ADR-0022 M1
  gate — recall ≥ 95 % @ 2 px and donor Pearson r ≥ 0.95.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.io.deeplasi import DeepLasiExport  # noqa: E402
from tether.project.oracle import (  # noqa: E402
    OracleResult,
    coordinate_rms,
    evaluate_extraction,
    evaluate_project,
    match_coordinates,
    pooled_pearson,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SLICE_MAT = FIXTURES / "deeplasi_export_slice.mat"
SLICE_TXT = FIXTURES / "deeplasi_traces_slice.txt"

requires_scipy = pytest.mark.skipif(
    importlib.util.find_spec("scipy") is None,
    reason="scipy not installed",
)


# --- helpers -----------------------------------------------------------------


def _signal(n_mol: int, n_frames: int, *, seed: int = 0) -> np.ndarray:
    """Deterministic, per-molecule-distinct, non-constant per-frame series."""
    rng = np.random.default_rng(seed)
    base = rng.normal(size=(n_mol, n_frames)) * 50.0
    # add a per-molecule offset + a frame ramp so every row has real variance
    base += np.arange(n_mol)[:, None] * 1000.0
    base += np.linspace(0, 200, n_frames)[None, :]
    return base.astype(np.float64)


def _make_export(
    donor_xy: np.ndarray,
    donor: np.ndarray,
    acceptor: np.ndarray,
) -> DeepLasiExport:
    """A minimal :class:`DeepLasiExport` with raw == corrected == ``donor``/``acceptor``."""
    donor_xy = np.asarray(donor_xy, dtype=np.float64)
    return DeepLasiExport(
        donor_xy=donor_xy,
        acceptor_xy=donor_xy + 8.0,
        donor_raw=donor,
        acceptor_raw=acceptor,
        donor_corrected=donor,
        acceptor_corrected=acceptor,
        donor_background=np.zeros_like(donor),
        acceptor_background=np.zeros_like(acceptor),
        movie_name="synthetic.tif",
        movie_path="/tmp",
        exported_by="pytest",
    )


# --- pure: match_coordinates -------------------------------------------------


def test_match_coordinates_perfect_pairs_all() -> None:
    xy = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]])
    matches = match_coordinates(xy, xy.copy(), tol_px=1.0)
    assert len(matches) == 3
    assert all(d == pytest.approx(0.0) for _, _, d in matches)
    # each gt and ext index used exactly once
    assert sorted(g for g, _, _ in matches) == [0, 1, 2]
    assert sorted(e for _, e, _ in matches) == [0, 1, 2]


def test_match_coordinates_is_unique_and_greedy() -> None:
    gt = np.array([[0.0, 0.0]])
    ext = np.array([[0.1, 0.0], [0.2, 0.0]])  # both within 1px; nearest wins, unique
    matches = match_coordinates(gt, ext, tol_px=1.0)
    assert len(matches) == 1
    assert matches[0][1] == 0  # the 0.1px neighbour, not 0.2


def test_match_coordinates_two_truths_one_spot_matches_once() -> None:
    gt = np.array([[0.0, 0.0], [0.3, 0.0]])  # two truths near one spot
    ext = np.array([[0.1, 0.0]])
    matches = match_coordinates(gt, ext, tol_px=1.0)
    assert len(matches) == 1  # the spot is consumed by its nearest truth


def test_match_coordinates_respects_tolerance() -> None:
    gt = np.array([[0.0, 0.0]])
    ext = np.array([[2.0, 0.0]])
    assert match_coordinates(gt, ext, tol_px=1.0) == []


def test_match_coordinates_empty_and_shape_guards() -> None:
    assert match_coordinates(np.empty((0, 2)), np.array([[1.0, 1.0]])) == []
    assert match_coordinates(np.array([[1.0, 1.0]]), np.empty((0, 2))) == []
    with pytest.raises(ValueError, match="must be"):
        match_coordinates(np.array([[1.0, 2.0, 3.0]]), np.array([[1.0, 2.0, 3.0]]))
    with pytest.raises(ValueError, match="must be"):  # 3-D input (not just wrong width)
        match_coordinates(np.zeros((1, 2, 2)), np.zeros((1, 2, 2)))
    with pytest.raises(ValueError, match="tol_px must be positive"):
        match_coordinates(np.array([[0.0, 0.0]]), np.array([[0.0, 0.0]]), tol_px=0.0)


# --- pure: coordinate_rms ----------------------------------------------------


def test_coordinate_rms_zero_on_perfect_and_known_value() -> None:
    xy = np.array([[10.0, 20.0], [30.0, 40.0]])
    matches = match_coordinates(xy, xy.copy())
    assert coordinate_rms(xy, xy.copy(), matches) == pytest.approx(0.0)
    # a single planted residual of (0.3, 0.4) → distance 0.5
    ext = xy.copy()
    ext[0] += np.array([0.3, 0.4])
    matches = match_coordinates(xy, ext)
    assert coordinate_rms(xy, ext, matches) == pytest.approx(np.sqrt((0.25 + 0.0) / 2))


def test_coordinate_rms_nan_without_matches() -> None:
    assert np.isnan(coordinate_rms(np.empty((0, 2)), np.empty((0, 2)), []))


# --- pure: evaluate_extraction -----------------------------------------------


def test_evaluate_extraction_perfect_meets_acceptance() -> None:
    xy = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]])
    donor = _signal(3, 64, seed=1)
    acceptor = _signal(3, 64, seed=2)
    gt = _make_export(xy, donor, acceptor)
    res = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy())
    assert isinstance(res, OracleResult)
    assert res.recall == pytest.approx(1.0)
    assert res.n_matched == 3
    assert res.coord_rms_px == pytest.approx(0.0)
    assert res.donor_pearson_median == pytest.approx(1.0)
    assert res.acceptor_pearson_median == pytest.approx(1.0)
    assert res.donor_pearson_pooled == pytest.approx(1.0)
    assert res.meets_recall and res.meets_pearson and res.meets_rms
    assert res.meets_acceptance()
    # summary is JSON-friendly
    s = res.summary()
    assert s["meets_acceptance"] is True and s["n_matched"] == 3


def test_evaluate_extraction_planted_miss_drops_recall() -> None:
    xy = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]])
    donor = _signal(3, 32, seed=3)
    acceptor = _signal(3, 32, seed=4)
    gt = _make_export(xy, donor, acceptor)
    ext_xy = xy.copy()
    ext_xy[2] += np.array([5.0, 5.0])  # shift one beyond 1px → unmatched
    res = evaluate_extraction(gt, ext_xy, donor.copy(), acceptor.copy())
    assert res.n_matched == 2
    assert res.recall == pytest.approx(2 / 3)
    assert not res.meets_recall
    assert not res.meets_acceptance()


def test_evaluate_extraction_noise_breaks_pearson() -> None:
    xy = np.array([[10.0, 20.0], [30.0, 40.0]])
    donor = _signal(2, 200, seed=5)
    acceptor = _signal(2, 200, seed=6)
    gt = _make_export(xy, donor, acceptor)
    rng = np.random.default_rng(99)
    noisy_donor = rng.normal(size=donor.shape) * 50.0  # uncorrelated with gt
    res = evaluate_extraction(gt, xy.copy(), noisy_donor, acceptor.copy())
    assert res.recall == pytest.approx(1.0)  # coords still match
    assert res.donor_pearson_median < 0.95
    assert not res.meets_pearson
    assert not res.meets_acceptance()


def test_acceptor_pearson_is_diagnostic_not_gated() -> None:
    # ADR-0022 reframe: the Pearson gate is DONOR-only. A perfect donor + a fully
    # noise acceptor (the dark/low-FRET population donor-anchoring keeps) must still
    # meet acceptance — the acceptor Pearson is reported, never gated.
    xy = np.array([[10.0, 20.0], [30.0, 40.0]])
    donor = _signal(2, 200, seed=51)
    acceptor = _signal(2, 200, seed=52)
    gt = _make_export(xy, donor, acceptor)
    rng = np.random.default_rng(7)
    noisy_acceptor = rng.normal(size=acceptor.shape) * 50.0  # uncorrelated with gt
    res = evaluate_extraction(gt, xy.copy(), donor.copy(), noisy_acceptor)
    assert res.donor_pearson_median >= 0.95  # donor is perfect
    assert res.acceptor_pearson_median < 0.95  # acceptor is noise (diagnostic)
    assert res.meets_pearson  # donor-only gate → still met
    assert res.meets_acceptance()  # recall + donor Pearson + RMS all pass


def test_evaluate_extraction_rms_gate() -> None:
    xy = np.array([[10.0, 20.0]])
    donor = _signal(1, 16, seed=7)
    acceptor = _signal(1, 16, seed=8)
    gt = _make_export(xy, donor, acceptor)
    over = evaluate_extraction(
        gt, xy.copy(), donor.copy(), acceptor.copy(), registration_rms_px=0.6
    )
    assert not over.meets_rms and not over.meets_acceptance()
    ok = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy(), registration_rms_px=0.4)
    assert ok.meets_rms and ok.meets_acceptance()
    nan = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy())  # imported-tmap → N/A
    assert nan.meets_rms  # nan ⇒ not measured ⇒ does not block


def test_invalid_matched_traces_do_not_mask_failure() -> None:
    # Two degenerate (constant → nan Pearson) matched traces + one perfect one must
    # NOT pass on the single good trace: nan counts as no-agreement (0), not dropped.
    xy = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]])
    good = _signal(1, 64, seed=13)[0]
    donor = np.vstack([np.full(64, 7.0), np.full(64, 9.0), good])  # 2 flat, 1 varying
    acceptor = donor.copy()
    gt = _make_export(xy, donor, acceptor)
    res = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy())
    assert res.recall == pytest.approx(1.0)  # all three coords match
    # median over {nan→0, nan→0, 1.0} == 0.0 → gate fails (no masking)
    assert res.donor_pearson_median == pytest.approx(0.0)
    assert not res.meets_pearson
    assert not res.meets_acceptance()


def test_summary_is_strict_json_safe() -> None:
    import json

    xy = np.array([[10.0, 20.0]])
    donor = _signal(1, 16, seed=14)
    acceptor = _signal(1, 16, seed=15)
    gt = _make_export(xy, donor, acceptor)
    res = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy())  # rms unmeasured
    summary = res.summary()
    assert summary["registration_rms_px"] is None  # nan → None, not NaN
    assert summary["acceptor_recall"] is None  # acceptor not supplied → unmeasured
    assert summary["precision"] == pytest.approx(1.0)  # 1 matched / 1 extracted
    # strict JSON (allow_nan=False) must not raise — no NaN/Infinity leaked
    json.dumps(summary, allow_nan=False)


# --- reporting: donor-channel precision --------------------------------------


def test_precision_exposes_over_detection_flood() -> None:
    """A recall met by an over-detecting flood shows a low precision (C3d honesty)."""
    xy = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]])  # 3 truths
    donor = _signal(3, 32, seed=31)
    acceptor = _signal(3, 32, seed=32)
    gt = _make_export(xy, donor, acceptor)
    # Extract the 3 real spots + 3 spurious ones far from any truth (a flood).
    spurious = np.array([[100.0, 100.0], [120.0, 120.0], [140.0, 140.0]])
    ext_xy = np.vstack([xy, spurious])
    ext_donor = np.vstack([donor, _signal(3, 32, seed=33)])
    ext_acc = np.vstack([acceptor, _signal(3, 32, seed=34)])
    res = evaluate_extraction(gt, ext_xy, ext_donor, ext_acc)
    assert res.recall == pytest.approx(1.0)  # all 3 truths recovered
    assert res.n_extracted == 6
    assert res.n_matched == 3
    assert res.precision == pytest.approx(0.5)  # 3 of 6 extracted are real
    assert res.summary()["precision"] == pytest.approx(0.5)
    # precision is reporting-only: it never blocks acceptance
    assert res.meets_recall


def test_precision_nan_when_nothing_extracted() -> None:
    xy = np.array([[10.0, 20.0]])
    donor = _signal(1, 8, seed=35)
    acceptor = _signal(1, 8, seed=36)
    gt = _make_export(xy, donor, acceptor)
    res = evaluate_extraction(gt, np.empty((0, 2)), np.empty((0, 8)), np.empty((0, 8)))
    assert res.n_extracted == 0
    assert np.isnan(res.precision)
    assert res.summary()["precision"] is None


# --- reporting: per-channel acceptor recall ----------------------------------


def test_acceptor_channel_recall_when_supplied() -> None:
    """Per-channel acceptor coordinate recall vs ground_truth.acceptor_xy."""
    xy = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]])
    donor = _signal(3, 24, seed=41)
    acceptor = _signal(3, 24, seed=42)
    gt = _make_export(xy, donor, acceptor)  # acceptor_xy == donor_xy + 8
    # Four acceptor coords: three match the truths, one is spurious; drop a matching
    # one beyond 1px → recall 2/3 (of the 3 truths), precision 2/4 (of 4 extracted).
    ext_acc_xy = np.vstack([gt.acceptor_xy.copy(), np.array([[200.0, 200.0]])])
    ext_acc_xy[2] += np.array([5.0, 5.0])
    res = evaluate_extraction(
        gt, xy.copy(), donor.copy(), acceptor.copy(), extracted_acceptor_xy=ext_acc_xy
    )
    assert res.n_acceptor_extracted == 4
    assert res.n_acceptor_matched == 2
    assert res.acceptor_recall == pytest.approx(2 / 3)  # 2 of 3 truths
    assert res.acceptor_precision == pytest.approx(2 / 4)  # 2 of 4 extracted
    assert np.isfinite(res.acceptor_coord_rms_px)
    # donor gate is untouched by the acceptor channel
    assert res.recall == pytest.approx(1.0)
    assert res.meets_acceptance()  # per-channel acceptor metrics are NOT gates
    summary = res.summary()
    assert summary["acceptor_recall"] == pytest.approx(2 / 3, abs=1e-4)
    assert summary["acceptor_precision"] == pytest.approx(0.5)


def test_acceptor_channel_unmeasured_without_coords() -> None:
    xy = np.array([[10.0, 20.0]])
    donor = _signal(1, 12, seed=43)
    acceptor = _signal(1, 12, seed=44)
    gt = _make_export(xy, donor, acceptor)
    res = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy())
    assert res.n_acceptor_extracted == 0
    assert res.n_acceptor_matched == 0
    assert np.isnan(res.acceptor_recall)
    assert np.isnan(res.acceptor_precision)
    assert np.isnan(res.acceptor_coord_rms_px)


def test_evaluate_extraction_corrected_intensity_label() -> None:
    xy = np.array([[10.0, 20.0]])
    donor = _signal(1, 16, seed=9)
    acceptor = _signal(1, 16, seed=10)
    gt = _make_export(xy, donor, acceptor)
    res = evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy(), intensity="corrected")
    assert res.intensity == "corrected"
    assert res.donor_pearson_median == pytest.approx(1.0)
    with pytest.raises(ValueError, match="intensity must be"):
        evaluate_extraction(gt, xy.copy(), donor.copy(), acceptor.copy(), intensity="bogus")


def test_evaluate_extraction_valid_lengths_excludes_zero_pad() -> None:
    xy = np.array([[10.0, 20.0]])
    donor = _signal(1, 40, seed=11)
    acceptor = _signal(1, 40, seed=12)
    gt = _make_export(xy, donor, acceptor)
    # extracted is zero-padded to width 64; only the first 40 frames are valid
    ext_donor = np.zeros((1, 64))
    ext_donor[0, :40] = donor[0]
    ext_acc = np.zeros((1, 64))
    ext_acc[0, :40] = acceptor[0]
    res = evaluate_extraction(gt, xy.copy(), ext_donor, ext_acc, valid_lengths=np.array([40]))
    assert res.donor_pearson_median == pytest.approx(1.0)
    assert res.acceptor_pearson_median == pytest.approx(1.0)
    # the pooled path also honours valid_lengths (excludes the zero pad)
    assert res.donor_pearson_pooled == pytest.approx(1.0)
    assert res.acceptor_pearson_pooled == pytest.approx(1.0)


def test_pooled_pearson_handles_empty() -> None:
    assert np.isnan(pooled_pearson(np.zeros((1, 4)), np.zeros((1, 4)), []))


# --- real-slice: Pearson machinery on committed Deep-LASI numbers ------------


@requires_scipy
def test_real_slice_txt_correlates_with_mat() -> None:
    """The committed 4-mol slice: .txt corrected ≈ .mat corrected → r ≈ 1 (real data)."""
    from tether.io.deeplasi import read_deeplasi_mat, read_deeplasi_txt

    gt = read_deeplasi_mat(SLICE_MAT)
    txt = read_deeplasi_txt(SLICE_TXT)
    assert gt.n_molecules == txt.n_molecules == 4
    # Use the .mat as ground truth and the .txt-corrected as the "extracted" trace;
    # coordinates come from the .mat so all 4 molecules match (recall 1).
    res = evaluate_extraction(
        gt,
        gt.donor_xy,
        txt.donor_corrected,
        txt.acceptor_corrected,
        intensity="corrected",
    )
    assert res.recall == pytest.approx(1.0)
    assert res.n_matched == 4
    assert res.donor_pearson_median >= 0.99
    assert res.acceptor_pearson_median >= 0.99
    assert res.donor_pearson_pooled >= 0.99
    assert res.meets_pearson


# --- wiring: evaluate_project plumbs the readers -----------------------------


def test_evaluate_project_wires_molecules_and_traces(monkeypatch) -> None:
    xy = np.array([[10.0, 20.0], [30.0, 40.0]])
    donor = _signal(2, 50, seed=21)
    acceptor = _signal(2, 50, seed=22)
    gt = _make_export(xy, donor, acceptor)

    # /molecules structured array: donor_xy + acceptor_xy + frame_range (full extent)
    mols = np.zeros(
        2,
        dtype=[
            ("donor_xy", "<f8", (2,)),
            ("acceptor_xy", "<f8", (2,)),
            ("frame_range", "<i8", (2,)),
        ],
    )
    mols["donor_xy"] = xy
    mols["acceptor_xy"] = gt.acceptor_xy  # == xy + 8 (matches ground truth)
    mols["frame_range"] = np.array([[0, 50], [0, 50]])
    traces = {
        "donor_raw": donor.astype("<f4"),
        "acceptor_raw": acceptor.astype("<f4"),
        "donor_corrected": donor.astype("<f4"),
        "acceptor_corrected": acceptor.astype("<f4"),
    }

    # Patch the live sys.modules objects (via import_module, which returns them):
    # evaluate_project resolves the readers through its lazy `from ... import`
    # (fromlist → sys.modules), but another test (test_deeplasi's lazy-scipy import
    # contract) reimports tether.io.deeplasi and leaves the tether.io package's
    # `.deeplasi` attribute pointing at a stale module. Both the string-form setattr
    # and `import ... as` resolve via that parent attribute (the wrong object);
    # import_module returns the sys.modules entry that evaluate_project actually uses.
    extract_mod = importlib.import_module("tether.imaging.extract")
    deeplasi_mod = importlib.import_module("tether.io.deeplasi")
    monkeypatch.setattr(extract_mod, "read_molecules", lambda _p: mols)
    monkeypatch.setattr(extract_mod, "read_traces", lambda _p: traces)
    monkeypatch.setattr(deeplasi_mod, "read_deeplasi_mat", lambda _p: gt)

    res = evaluate_project("ignored.tether", "ignored.mat", intensity="raw")
    assert res.recall == pytest.approx(1.0)
    assert res.n_matched == 2
    assert res.donor_pearson_median == pytest.approx(1.0, abs=1e-4)  # f4 round-trip
    assert res.acceptor_pearson_median == pytest.approx(1.0, abs=1e-4)
    # evaluate_project now plumbs the stored acceptor_xy into the per-channel metric
    assert res.acceptor_recall == pytest.approx(1.0)
    assert res.n_acceptor_matched == 2


# --- data-present, gated: the full §9 M1 acceptance on the real UCKOPSB pair --


def _find_uckopsb() -> dict[str, Path] | None:
    """Locate the gated UCKOPSB movie + .tmap + .tdat + Deep-LASI .mat (sibling data)."""
    candidates = []
    env_dir = os.environ.get("TETHER_UCKOPSB_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "example-data" / "bla-uckopsb-tbox-video10")
    base = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"
    for src in candidates:
        movie = src / f"{base}.tif"
        mat = src / f"DeepLASI_MAT_export_{base}.mat"
        # The `.tmap`/`.tdat` filenames don't share the movie's `base` string, so
        # match by requiring the dataset directory to hold *exactly one* of each: an
        # ambiguous directory (a second, unrelated map/config added) must not silently
        # pair the movie with the wrong registration or detection settings — it falls
        # through to a skip instead.
        tmaps = sorted(src.glob("DeepLASI_MAP_*.tmap")) if src.is_dir() else []
        tdats = sorted(src.glob("*.tdat")) if src.is_dir() else []
        if movie.is_file() and mat.is_file() and len(tmaps) == 1 and len(tdats) == 1:
            return {"movie": movie, "mat": mat, "tmap": tmaps[0], "tdat": tdats[0]}
    return None


@pytest.mark.large
def test_extraction_meets_m1_acceptance_on_uckopsb(tmp_path) -> None:
    """M1 acceptance: native extraction reproduces Deep-LASI to the ADR-0022 tolerance.

    The committed M1 acceptance gate, run against the full UCKOPSB pair with the
    faithful configuration the movie was actually detected with: registration imported
    from the ``.tmap`` and the particle-detection mode + threshold decoded from the
    ``.tdat`` (mode-2 intensity @ 0.330097). Detection uses the mode's faithful
    ``min_separation`` (3 px, ADR-0022). Scored against the ADR-0022 gate — recall
    ≥ 95 % @ 2 px and **donor** per-molecule Pearson ≥ 0.95 (acceptor Pearson is
    diagnostic only). This is the instrument that closes M1 (was ``xfail`` under the
    original 1 px / 0.99 bars and the unfaithful wavelet detector; ADR-0020/0022).
    """
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    pytest.importorskip("h5py")
    pytest.importorskip("tifffile")
    found = _find_uckopsb()
    if found is None:
        pytest.skip("gated UCKOPSB movie + .tmap + .tdat + Deep-LASI .mat absent")

    from tether.project.extract import extract_movie

    out = tmp_path / "uckopsb.tether"
    summary = extract_movie(found["movie"], out, tmap=found["tmap"], tdat=found["tdat"])
    assert out.is_file()
    assert summary.registration_source == "imported"
    assert summary.detection_mode == "intensity"  # decoded from the .tdat

    res = evaluate_project(out, found["mat"], intensity="raw")
    # ADR-0022 gate: recall @ 2 px + donor Pearson ≥ 0.95 (RMS ≤ 0.5 px is locked by
    # test_register on the native .tmap fit; imported here → nan → N/A). Surface the
    # full metric dict (incl. the diagnostic acceptor Pearson/precision) on failure.
    assert res.meets_recall, f"recall {res.recall:.3f} < 0.95 ({res.summary()})"
    assert res.meets_pearson, (
        f"donor Pearson {res.donor_pearson_median:.3f} < 0.95 ({res.summary()})"
    )
    assert res.meets_acceptance(), f"M1 acceptance not met ({res.summary()})"
