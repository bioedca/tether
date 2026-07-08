# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-condition apparent-E histogram overlay (M6, FR-ANALYZE; Appendix C A1).

Covers the M6-owned PRD §7.7 "per-condition overlays" clause
(:func:`~tether.analysis.histogram.per_condition_apparent_e_histograms`): each
condition binned on **one shared axis** so ≥2 conditions overlay directly, each
density-normalized independently for cross-condition shape comparison
[McCann2010], annotated with its molecule count ``N``. All headless (no Qt) → runs
in the base CI matrix; the store is seeded as post-extraction data under the
M0-frozen schema, with condition ids / categories / curation labels written onto
``/molecules`` directly (mirrors ``test_analysis_query._seed``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis import (  # noqa: E402
    DEFAULT_NBINS,
    DEFAULT_RANGE,
    ConditionHistogram,
    Histogram1D,
    PerConditionHistograms,
    per_condition_apparent_e_histograms,
)
from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.project import Project  # noqa: E402
from tether.project.labels import CurationLabel  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21
_FRAMES = 30


def _distinct_coords(n: int) -> np.ndarray:
    return np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _constant_e_traces(e_values: list[float], t: int) -> tuple[np.ndarray, np.ndarray]:
    """(n, t) donor/acceptor whose corrected apparent E is ``e_values[i]`` every frame."""
    n = len(e_values)
    donor = np.empty((n, t), dtype="float64")
    acceptor = np.empty((n, t), dtype="float64")
    for i, e in enumerate(e_values):
        donor[i, :] = (1.0 - e) * 1000.0
        acceptor[i, :] = e * 1000.0
    return donor, acceptor


def _build_store(
    tmp_path: Path,
    *,
    condition_of: list[str],
    e_values: list[float],
    category_of: list[str] | None = None,
    rejected: list[bool] | None = None,
    name: str = "exp.tether",
) -> tuple[Project, list[str]]:
    """A ``.tether`` whose molecules carry known apparent-E, split across conditions.

    ``condition_of[i]`` / ``e_values[i]`` describe molecule row ``i``; optional
    ``category_of`` / ``rejected`` set the per-trace category and a sticky reject.
    Conditions / categories / curation labels are written onto ``/molecules``
    directly after a single-movie extraction, and every analysis window is the full
    trace extent so the pooled apparent-E is exactly ``e_values``.
    """
    n = len(condition_of)
    if len(e_values) != n:
        raise ValueError("condition_of and e_values must be the same length")
    donor, acceptor = _constant_e_traces(e_values, _FRAMES)
    coords = _distinct_coords(n)
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=_FRAMES,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    path = tmp_path / name
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )
    with h5py.File(path, "r+") as f:
        table = f["molecules"]["table"][:]
        for i in range(n):
            table["condition_id"][i] = condition_of[i]
            table["source_filename"][i] = f"{condition_of[i]}_010.tif"
            table["analysis_window"][i] = (0, _FRAMES)
            if category_of is not None:
                table["category"][i] = category_of[i]
            if rejected is not None and rejected[i]:
                table["curation_label"][i] = int(CurationLabel.REJECT)
        f["molecules"]["table"][:] = table
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


# --- the M6 gate: ≥2 conditions overlay on one A1 axis ------------------------


def test_two_conditions_overlay_on_one_shared_axis(tmp_path: Path) -> None:
    # A: 3 molecules near E=0.3; B: 2 molecules near E=0.8.
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "A", "B", "B"],
        e_values=[0.30, 0.31, 0.29, 0.80, 0.79],
    )
    overlay = per_condition_apparent_e_histograms(proj)

    assert isinstance(overlay, PerConditionHistograms)
    assert overlay.n_conditions == 2
    assert set(overlay.condition_ids) == {"A", "B"}
    # Every condition shares the exact same grid -> they overlay on one axis.
    assert overlay.bin_edges.shape == (DEFAULT_NBINS + 1,)
    for ch in overlay.histograms:
        assert isinstance(ch, ConditionHistogram)
        assert isinstance(ch.histogram, Histogram1D)
        np.testing.assert_array_equal(ch.histogram.bin_edges, overlay.bin_edges)
    # The two populations peak in different bins (E≈0.3 vs E≈0.8).
    centers = overlay.bin_centers
    peak_a = centers[np.argmax(overlay["A"].histogram.counts)]
    peak_b = centers[np.argmax(overlay["B"].histogram.counts)]
    assert peak_a == pytest.approx(0.3, abs=0.05)
    assert peak_b == pytest.approx(0.8, abs=0.05)
    assert peak_b > peak_a


def test_n_annotation_counts_per_condition(tmp_path: Path) -> None:
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "A", "B", "B"],
        e_values=[0.30, 0.31, 0.29, 0.80, 0.79],
    )
    overlay = per_condition_apparent_e_histograms(proj)
    assert overlay.molecule_counts == {"A": 3, "B": 2}
    assert overlay.total_molecules == 5
    assert overlay["A"].n_molecules == 3
    assert overlay["B"].n_molecules == 2
    # n_samples = molecules × window frames (all finite here).
    assert overlay["A"].n_samples == 3 * _FRAMES
    assert overlay["B"].n_samples == 2 * _FRAMES


def test_each_condition_density_normalized_independently(tmp_path: Path) -> None:
    # Unequal N (3 vs 2): independent density normalization makes both integrate to
    # 1, so the *shapes* compare regardless of population size (the §7.7 rationale).
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "A", "B", "B"],
        e_values=[0.30, 0.31, 0.29, 0.80, 0.79],
    )
    overlay = per_condition_apparent_e_histograms(proj, density=True)
    assert overlay.density is True
    width = np.diff(overlay.bin_edges)
    for ch in overlay.histograms:
        assert float(np.sum(ch.histogram.counts * width)) == pytest.approx(1.0)


def test_counts_mode_is_not_normalized(tmp_path: Path) -> None:
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "B"],
        e_values=[0.3, 0.3, 0.8],
    )
    overlay = per_condition_apparent_e_histograms(proj, density=False)
    assert overlay.density is False
    # Raw counts: each condition's total = its molecules × frames.
    assert float(overlay["A"].histogram.counts.sum()) == pytest.approx(2 * _FRAMES)
    assert float(overlay["B"].histogram.counts.sum()) == pytest.approx(1 * _FRAMES)


# --- ordering (deterministic — NFR-REPRO) -------------------------------------


def test_default_order_is_store_first_seen(tmp_path: Path) -> None:
    # Rows interleave B before A's first row is 0 -> first-seen order is A, B, C.
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "B", "A", "C", "B"],
        e_values=[0.2, 0.5, 0.25, 0.9, 0.55],
    )
    overlay = per_condition_apparent_e_histograms(proj)
    assert overlay.condition_ids == ("A", "B", "C")


def test_requested_condition_ids_set_overlay_order(tmp_path: Path) -> None:
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "B", "C"],
        e_values=[0.2, 0.5, 0.9],
    )
    assert per_condition_apparent_e_histograms(proj, condition_ids=["C", "A"]).condition_ids == (
        "C",
        "A",
    )
    # A single requested condition overlays only that one.
    single = per_condition_apparent_e_histograms(proj, condition_ids=["B"])
    assert single.condition_ids == ("B",)
    assert single.n_conditions == 1


def test_requested_absent_condition_is_omitted_not_error(tmp_path: Path) -> None:
    proj, _ = _build_store(tmp_path, condition_of=["A", "B"], e_values=[0.3, 0.8])
    overlay = per_condition_apparent_e_histograms(proj, condition_ids=["A", "ghost", "B"])
    assert overlay.condition_ids == ("A", "B")  # the non-existent condition drops out


def test_requested_condition_ids_generator_is_not_exhausted(tmp_path: Path) -> None:
    # A one-shot generator is consumed by the query filter AND the ordering; the
    # function must materialize it once so ordering still sees every id.
    proj, _ = _build_store(tmp_path, condition_of=["A", "B"], e_values=[0.3, 0.8])
    overlay = per_condition_apparent_e_histograms(proj, condition_ids=(c for c in ("B", "A")))
    assert overlay.condition_ids == ("B", "A")


def test_empty_condition_ids_is_inert_like_none(tmp_path: Path) -> None:
    # An empty selection is inert (overlays every condition in store order), matching
    # query_molecules where an empty filter is a no-op — not a match-nothing trap.
    proj, _ = _build_store(tmp_path, condition_of=["A", "B"], e_values=[0.3, 0.8])
    every = per_condition_apparent_e_histograms(proj).condition_ids
    assert per_condition_apparent_e_histograms(proj, condition_ids=[]).condition_ids == every
    assert (
        per_condition_apparent_e_histograms(proj, condition_ids=(c for c in [])).condition_ids
        == every
    )


# --- two-stage AND: queried ∩ accepted / query filter passthrough -------------


def test_rejected_molecules_excluded_by_default_included_on_toggle(tmp_path: Path) -> None:
    # Condition A has one rejected molecule; the default filter drops it, and
    # include_rejected=True brings it back (the §7.5 toggle threaded per condition).
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "B"],
        e_values=[0.3, 0.3, 0.8],
        rejected=[True, False, False],
    )
    default = per_condition_apparent_e_histograms(proj)
    assert default.molecule_counts == {"A": 1, "B": 1}
    kept = per_condition_apparent_e_histograms(proj, include_rejected=True)
    assert kept.molecule_counts == {"A": 2, "B": 1}


def test_all_rejected_condition_still_appears_with_n_zero(tmp_path: Path) -> None:
    # A condition whose molecules are all rejected is still queried (query_molecules
    # does not curate), so it appears in the overlay with N=0 and an all-zero curve —
    # the honest "this condition has molecules but none survived the filter", never a
    # silent drop and never a NaN (the docstring guarantee).
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "B"],
        e_values=[0.3, 0.3, 0.8],
        rejected=[True, True, False],
    )
    overlay = per_condition_apparent_e_histograms(proj)
    assert overlay.condition_ids == ("A", "B")  # A is not silently dropped
    assert overlay.molecule_counts == {"A": 0, "B": 1}
    a_counts = overlay["A"].histogram.counts
    assert np.all(np.isfinite(a_counts))  # zeros, never NaN
    assert float(a_counts.sum()) == 0.0


def test_category_filter_passthrough_narrows_each_condition(tmp_path: Path) -> None:
    proj, _ = _build_store(
        tmp_path,
        condition_of=["A", "A", "B", "B"],
        e_values=[0.3, 0.3, 0.8, 0.8],
        category_of=["docked", "free", "docked", "free"],
    )
    overlay = per_condition_apparent_e_histograms(proj, categories=["docked"])
    assert overlay.molecule_counts == {"A": 1, "B": 1}


def test_bare_string_condition_ids_raises(tmp_path: Path) -> None:
    # A bare str is iterable; reject it (forwarded to query_molecules' guard) rather
    # than iterate characters and silently match nothing.
    proj, _ = _build_store(tmp_path, condition_of=["A"], e_values=[0.3])
    with pytest.raises(TypeError, match="iterable of strings"):
        per_condition_apparent_e_histograms(proj, condition_ids="A")  # type: ignore[arg-type]


# --- edges, handles, exports --------------------------------------------------


def test_empty_project_yields_empty_overlay_with_grid(tmp_path: Path) -> None:
    path = create_project(tmp_path / "empty.tether")
    overlay = per_condition_apparent_e_histograms(path)
    assert overlay.n_conditions == 0
    assert overlay.condition_ids == ()
    assert overlay.total_molecules == 0
    assert overlay.molecule_counts == {}
    # The shared grid is present even with nothing to overlay.
    assert overlay.bin_edges.shape == (DEFAULT_NBINS + 1,)
    assert overlay.value_range == DEFAULT_RANGE


def test_unconditioned_molecules_never_overlaid(tmp_path: Path) -> None:
    proj, _ = _build_store(tmp_path, condition_of=["A", ""], e_values=[0.3, 0.8])
    overlay = per_condition_apparent_e_histograms(proj)
    # The empty-condition molecule is excluded (a condition-centric view).
    assert overlay.condition_ids == ("A",)
    assert overlay.total_molecules == 1


def test_getitem_returns_condition_and_missing_raises(tmp_path: Path) -> None:
    proj, _ = _build_store(tmp_path, condition_of=["A", "B"], e_values=[0.3, 0.8])
    overlay = per_condition_apparent_e_histograms(proj)
    assert overlay["A"].condition_id == "A"
    with pytest.raises(KeyError):
        _ = overlay["nope"]


def test_accepts_path_and_project_handle(tmp_path: Path) -> None:
    proj, _ = _build_store(tmp_path, condition_of=["A", "B"], e_values=[0.3, 0.8])
    from_handle = per_condition_apparent_e_histograms(proj)
    from_path = per_condition_apparent_e_histograms(proj.path)
    assert from_handle.condition_ids == from_path.condition_ids
    for a, b in zip(from_handle.histograms, from_path.histograms, strict=True):
        np.testing.assert_array_equal(a.histogram.counts, b.histogram.counts)


def test_symbols_exported() -> None:
    import tether.analysis as analysis

    for name in (
        "per_condition_apparent_e_histograms",
        "PerConditionHistograms",
        "ConditionHistogram",
    ):
        assert name in analysis.__all__
        assert hasattr(analysis, name)
