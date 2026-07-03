# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-level correction-method resolution + apparent-E fallback (M3, FR-CORRECT).

Locks :func:`tether.project.correct.compute_corrected_fret`: given the applied
``/molecules.alpha`` (PR #75) and ``/molecules.gamma`` (PR #76), it must stamp each
molecule's ``correction_method`` + ``correction_confidence``, fall back to apparent E
(never a NaN factor) on total correction failure, honor the apparent-E toggle and
manual α/γ overrides, and stamp ``/settings/correction`` provenance — all additive
(schema-guard green: the only new group is ``/settings/correction``). Headless; runs in
the base CI matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

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
from tether.io.schema import TABLE, create_project  # noqa: E402
from tether.project.correct import (  # noqa: E402
    METHOD_APPARENT_TOGGLE,
    METHOD_APPARENT_UNAVAILABLE,
    METHOD_CORRECTED,
    METHOD_MANUAL,
    compute_corrected_fret,
)

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21
_T = 60


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


def _build_store(path: Path, n_mol: int, t: int = _T) -> None:
    """A minimal ``.tether`` with ``n_mol`` molecules (flat traces; content irrelevant).

    ``compute_corrected_fret`` reads only ``/molecules`` (frame_range, alpha, gamma),
    so the trace values do not matter — only that valid molecule rows exist.
    """
    donor = np.full((n_mol, t), 500.0, dtype="float64")
    acceptor = np.full((n_mol, t), 300.0, dtype="float64")
    coords = _distinct_coords(n_mol)
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n_mol, dtype=bool),
        donor_index=np.arange(n_mol, dtype=np.intp),
        acceptor_index=np.full(n_mol, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.zeros((n_mol, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n_mol, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )


def _set_factors(path: Path, *, alpha: float, gamma: float) -> None:
    """Write a scalar ``alpha`` and ``gamma`` into every ``/molecules`` row."""
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        table["alpha"][:] = alpha
        table["gamma"][:] = gamma
        f["molecules"][TABLE][:] = table


def _methods(path: Path) -> list[str]:
    table = read_molecules(path)
    out = []
    for v in table["correction_method"]:
        out.append(v.decode("utf-8") if isinstance(v, bytes) else str(v))
    return out


def _all_names(f: h5py.File) -> list[str]:
    names: list[str] = []
    f.visit(names.append)
    return sorted(names)


# --- corrected path ----------------------------------------------------------


def test_all_corrected_when_factors_present(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)

    summary = compute_corrected_fret(path)
    assert summary.n_molecules == 6
    assert summary.n_corrected == 6
    assert summary.n_manual == 0
    assert summary.n_apparent == 0
    assert summary.total_failure is False
    assert summary.source == "accurate-fret"

    table = read_molecules(path)
    assert _methods(path) == [METHOD_CORRECTED] * 6
    assert np.all(table["correction_confidence"] == 1.0)
    # Factors are untouched on the corrected path (no override).
    assert np.all(table["alpha"] == pytest.approx(0.09))
    assert np.all(table["gamma"] == pytest.approx(1.2))


def test_alpha_zero_is_a_valid_correction(tmp_path: Path) -> None:
    # α = 0 (no leakage) is physical; only γ > 0 is required to correct.
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.0, gamma=1.3)
    summary = compute_corrected_fret(path)
    assert summary.n_corrected == 6
    assert _methods(path) == [METHOD_CORRECTED] * 6


# --- total-failure -> apparent-E ---------------------------------------------


def test_total_failure_falls_to_apparent_never_nan(tmp_path: Path) -> None:
    # Both factors withheld (NaN sentinel from an upstream withhold) -> every molecule
    # is stamped apparent-E and NO NaN factor or NaN confidence is written (PRD §7.2).
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=np.nan, gamma=np.nan)

    summary = compute_corrected_fret(path)
    assert summary.n_molecules == 6
    assert summary.n_apparent == 6
    assert summary.n_corrected == 0
    assert summary.total_failure is True

    table = read_molecules(path)
    assert _methods(path) == [METHOD_APPARENT_UNAVAILABLE] * 6
    assert np.all(table["correction_confidence"] == 0.0)
    assert not np.any(np.isnan(table["correction_confidence"]))


def test_fresh_store_nan_factors_is_total_failure(tmp_path: Path) -> None:
    # A never-corrected store carries the extraction default α = γ = NaN
    # (extract.py writes np.nan, not a zero-fill), so the non-finite factors route
    # through the `np.isfinite` guard to apparent E (not a spurious "corrected").
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)  # no _set_factors: alpha=gamma=NaN default
    summary = compute_corrected_fret(path)
    assert summary.n_apparent == 4
    assert summary.total_failure is True
    assert _methods(path) == [METHOD_APPARENT_UNAVAILABLE] * 4


def test_zero_gamma_boundary_is_total_failure(tmp_path: Path) -> None:
    # γ == 0.0 is finite but non-physical: it exercises the `eff_gamma > 0.0` half of
    # the guard (isfinite True, 0.0 > 0.0 False) — distinct from the NaN-isfinite path
    # above — and must still fall to apparent E, never a divide-by-zero corrected E.
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)
    _set_factors(path, alpha=0.09, gamma=0.0)
    summary = compute_corrected_fret(path)
    assert summary.n_apparent == 4
    assert summary.total_failure is True
    assert _methods(path) == [METHOD_APPARENT_UNAVAILABLE] * 4


def test_negative_gamma_is_total_failure(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)
    _set_factors(path, alpha=0.09, gamma=-0.5)
    summary = compute_corrected_fret(path)
    assert summary.n_apparent == 4
    assert _methods(path) == [METHOD_APPARENT_UNAVAILABLE] * 4


# --- apparent-E toggle -------------------------------------------------------


def test_apparent_e_only_toggle_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)

    toggled = compute_corrected_fret(path, apparent_e_only=True)
    assert toggled.apparent_e_only is True
    assert toggled.n_apparent == 6
    assert toggled.n_corrected == 0
    assert toggled.total_failure is True  # nothing corrected while toggled
    assert _methods(path) == [METHOD_APPARENT_TOGGLE] * 6

    # Re-running without the toggle restores the corrected methods (round-trip).
    restored = compute_corrected_fret(path, apparent_e_only=False)
    assert restored.n_corrected == 6
    assert _methods(path) == [METHOD_CORRECTED] * 6


# --- manual override ---------------------------------------------------------


def test_manual_override_persists_factors_and_stamps_manual(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)

    summary = compute_corrected_fret(path, alpha_override=0.05, gamma_override=1.5)
    assert summary.n_manual == 6
    assert summary.n_corrected == 0
    assert summary.total_failure is False

    table = read_molecules(path)
    assert _methods(path) == [METHOD_MANUAL] * 6
    assert np.all(table["correction_confidence"] == 1.0)
    # The override is persisted as the effective applied factor.
    assert np.all(table["alpha"] == pytest.approx(0.05))
    assert np.all(table["gamma"] == pytest.approx(1.5))


def test_manual_override_rescues_total_failure(tmp_path: Path) -> None:
    # No estimated factors (withheld) + a manual per-condition α/γ recovery -> corrected
    # via method="manual" (the §7.2 recovery action, headless).
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=5)
    _set_factors(path, alpha=np.nan, gamma=np.nan)
    summary = compute_corrected_fret(path, alpha_override=0.08, gamma_override=1.3)
    assert summary.n_manual == 5
    assert summary.total_failure is False
    table = read_molecules(path)
    assert np.all(table["alpha"] == pytest.approx(0.08))
    assert np.all(table["gamma"] == pytest.approx(1.3))


def test_partial_alpha_override_without_gamma_stays_apparent(tmp_path: Path) -> None:
    # An α override with γ still missing does not complete the pair -> apparent E,
    # not manual (both factors are required to correct).
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=5)
    _set_factors(path, alpha=np.nan, gamma=np.nan)
    summary = compute_corrected_fret(path, alpha_override=0.08)
    assert summary.n_apparent == 5
    assert summary.n_manual == 0
    assert _methods(path) == [METHOD_APPARENT_UNAVAILABLE] * 5


def test_apparent_toggle_takes_precedence_over_override(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)
    _set_factors(path, alpha=0.09, gamma=1.2)
    summary = compute_corrected_fret(
        path, apparent_e_only=True, alpha_override=0.05, gamma_override=1.5
    )
    assert summary.n_apparent == 4
    assert summary.n_manual == 0
    assert _methods(path) == [METHOD_APPARENT_TOGGLE] * 4
    # The override must NOT be persisted when the toggle wins.
    table = read_molecules(path)
    assert np.all(table["alpha"] == pytest.approx(0.09))
    assert np.all(table["gamma"] == pytest.approx(1.2))


@pytest.mark.parametrize("bad_gamma", [0.0, -1.0, np.nan, np.inf])
def test_gamma_override_must_be_positive_finite(tmp_path: Path, bad_gamma: float) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)
    _set_factors(path, alpha=0.09, gamma=1.2)
    with pytest.raises(ValueError, match="gamma_override"):
        compute_corrected_fret(path, gamma_override=bad_gamma)


def test_alpha_override_must_be_finite(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)
    _set_factors(path, alpha=0.09, gamma=1.2)
    with pytest.raises(ValueError, match="alpha_override"):
        compute_corrected_fret(path, alpha_override=np.nan)


# --- provenance + schema-guard + robustness ----------------------------------


def test_stamps_settings_correction_provenance(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)
    compute_corrected_fret(path)
    with h5py.File(path, "r") as f:
        grp = f["settings/correction"]
        assert grp.attrs["source"] == "accurate-fret"
        assert bool(grp.attrs["apparent_e_only"]) is False
        assert grp.attrs["n_molecules"] == 6
        assert grp.attrs["n_corrected"] == 6
        assert grp.attrs["n_manual"] == 0
        assert grp.attrs["n_apparent"] == 0
        assert bool(grp.attrs["total_failure"]) is False
        assert np.isnan(float(grp.attrs["alpha_override"]))
        assert np.isnan(float(grp.attrs["gamma_override"]))
        assert "app_version" in grp.attrs
        assert "created_utc" in grp.attrs


def test_settings_records_overrides(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=4)
    _set_factors(path, alpha=0.09, gamma=1.2)
    compute_corrected_fret(path, alpha_override=0.05, gamma_override=1.5)
    with h5py.File(path, "r") as f:
        grp = f["settings/correction"]
        assert float(grp.attrs["alpha_override"]) == pytest.approx(0.05)
        assert float(grp.attrs["gamma_override"]) == pytest.approx(1.5)
        assert grp.attrs["n_manual"] == 4


def test_only_new_group_is_settings_correction(tmp_path: Path) -> None:
    # schema-guard: the writer may add ONLY the additive /settings/correction group
    # (the /molecules columns it fills are already-frozen fields, not new structure).
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)
    with h5py.File(path, "r") as f:
        before = _all_names(f)
    compute_corrected_fret(path)
    with h5py.File(path, "r") as f:
        after = _all_names(f)
    assert set(after) - set(before) == {"settings/correction"}


def test_skips_invalid_frame_range(tmp_path: Path) -> None:
    # A molecule with an invalid frame_range (end <= start) is not analysable: it is
    # excluded from the counts and its correction_method is left at the default.
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        table["frame_range"][3] = (10, 10)  # end == start -> skipped
        f["molecules"][TABLE][:] = table

    summary = compute_corrected_fret(path)
    assert summary.n_molecules == 5
    assert summary.n_corrected == 5
    methods = _methods(path)
    assert methods[3] == ""  # skipped row untouched (extraction default "")
    assert [m for i, m in enumerate(methods) if i != 3] == [METHOD_CORRECTED] * 5


def test_recompute_overwrites_settings(tmp_path: Path) -> None:
    path = tmp_path / "c.tether"
    _build_store(path, n_mol=6)
    _set_factors(path, alpha=0.09, gamma=1.2)
    compute_corrected_fret(path)
    compute_corrected_fret(path, apparent_e_only=True)
    with h5py.File(path, "r") as f:
        assert bool(f["settings/correction"].attrs["apparent_e_only"]) is True
