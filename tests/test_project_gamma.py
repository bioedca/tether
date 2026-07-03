# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-level γ across the acceptor-bleach step (M3, FR-CORRECT).

Locks :func:`tether.project.gamma.compute_gamma`: given per-channel ``bleach_frames``
(PR #74), the applied ``/molecules.alpha`` (PR #75), and ``/traces``, it must write
the **per-molecule** γ (own value, or the population-median fallback) into the frozen
``/molecules.gamma``, stamp ``/settings/gamma`` provenance, enforce the
``background → α → γ`` prerequisite order, and **withhold** below
``min_qualifying_traces`` — all additive (schema-guard green: the only new group is
``/settings/gamma``). Headless; runs in the base CI matrix.
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
from tether.project.gamma import compute_gamma  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


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


def _build_store(path: Path, donor: np.ndarray, acceptor: np.ndarray) -> None:
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
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


def _step_trace(
    *,
    n: int,
    acceptor_pb: int,
    donor_pb: int,
    gamma_true: float,
    alpha: float,
    seed: int,
    donor_lo: float = 600.0,
    donor_hi: float = 1000.0,
    noise: float = 1.5,
) -> tuple[np.ndarray, np.ndarray]:
    """A donor/acceptor pair with a clean acceptor-bleach step recovering ``gamma_true``.

    Mirrors the construction unit-tested in ``test_fret_gamma``: donor rises
    ``donor_lo → donor_hi`` at ``acceptor_pb`` (dequenching), acceptor drops to pure
    leakage ``α·donor``; ``A_hi = gamma_true·Δdonor + α·donor_lo``.
    """
    rng = np.random.default_rng(seed)
    donor = np.empty(n, dtype=np.float64)
    donor[:acceptor_pb] = donor_lo
    donor[acceptor_pb:donor_pb] = donor_hi
    donor[donor_pb:] = 0.0
    a_hi = gamma_true * (donor_hi - donor_lo) + alpha * donor_lo
    acceptor = np.empty(n, dtype=np.float64)
    acceptor[:acceptor_pb] = a_hi
    acceptor[acceptor_pb:donor_pb] = alpha * donor_hi
    acceptor[donor_pb:] = 0.0
    donor = donor + rng.normal(0.0, noise, n)
    acceptor = acceptor + rng.normal(0.0, noise, n)
    return donor, acceptor


def _gamma_store(
    path: Path,
    *,
    n_mol: int,
    gamma_true: float = 1.2,
    alpha: float = 0.1,
    n: int = 120,
    acc: int = 40,
    don: int = 100,
    start: int = 0,
    donpb_overrides: dict[int, int] | None = None,
) -> None:
    """Build a store of ``n_mol`` acceptor-bleach-step traces, with α + bleach_frames set.

    ``acc``/``don`` are **absolute** frames; ``start`` sets a non-zero ``frame_range``
    start to exercise the absolute→local conversion. ``donpb_overrides`` maps a
    molecule index to a shorter ``donor_pb`` (a too-short post-segment → that molecule
    fails the γ gate, exercising the median fallback).
    """
    donpb_overrides = donpb_overrides or {}
    donor_rows, acceptor_rows, donpbs = [], [], []
    for i in range(n_mol):
        dpb = donpb_overrides.get(i, don)
        d, a = _step_trace(
            n=n, acceptor_pb=acc, donor_pb=dpb, gamma_true=gamma_true, alpha=alpha, seed=i + 1
        )
        donor_rows.append(d)
        acceptor_rows.append(a)
        donpbs.append(dpb)
    _build_store(path, np.stack(donor_rows), np.stack(acceptor_rows))
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        for i in range(n_mol):
            if start:
                table["frame_range"][i] = (start, n)
            table["bleach_frames"][i] = (donpbs[i], acc)  # (donor_pb, acceptor_pb), absolute
            table["alpha"][i] = alpha
        f["molecules"][TABLE][:] = table


def _all_names(f: h5py.File) -> list[str]:
    names: list[str] = []
    f.visit(names.append)
    return sorted(names)


def test_writes_per_molecule_gamma(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1)

    summary = compute_gamma(path)
    assert summary.n_molecules == 12
    assert summary.n_qualifying == 12
    assert summary.n_fallback == 0
    assert summary.applied is True
    assert summary.gamma == pytest.approx(1.2, abs=0.1)
    assert summary.source == "acceptor-bleach-step"

    table = read_molecules(path)
    # Per-molecule γ (each near the true value), NOT a single dataset factor.
    assert np.all(table["gamma"] == pytest.approx(1.2, abs=0.2))


def test_stamps_settings_gamma_provenance(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1)
    compute_gamma(path)
    with h5py.File(path, "r") as f:
        grp = f["settings/gamma"]
        assert grp.attrs["source"] == "acceptor-bleach-step"
        assert bool(grp.attrs["withheld"]) is False
        assert grp.attrs["n_qualifying"] == 12
        assert grp.attrs["n_fallback"] == 0
        assert grp.attrs["half_window"] == 3
        assert grp.attrs["min_window_frames"] == 20
        assert float(grp.attrs["ceiling"]) == 5.0
        assert grp.attrs["min_qualifying_traces"] == 10
        assert grp.attrs["intensity_quantity"] == "corrected"
        assert float(grp.attrs["gamma"]) == pytest.approx(1.2, abs=0.1)
        assert "app_version" in grp.attrs
        assert "created_utc" in grp.attrs


def test_median_fallback_for_nonqualifying(tmp_path: Path) -> None:
    # 10 clean steps + 2 with too-short post-segments: the 2 take the median fallback,
    # the 10 keep their own γ, and n_fallback counts exactly the 2.
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.3, alpha=0.1, donpb_overrides={10: 55, 11: 55})
    summary = compute_gamma(path)
    assert summary.n_qualifying == 10
    assert summary.n_fallback == 2
    assert summary.applied is True

    table = read_molecules(path)
    assert not np.any(np.isnan(table["gamma"][:12]))
    # The 2 fallback molecules carry exactly the dataset median.
    assert table["gamma"][10] == pytest.approx(summary.gamma)
    assert table["gamma"][11] == pytest.approx(summary.gamma)


def test_withholds_below_min_qualifying(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=4, gamma_true=1.2, alpha=0.1)  # 4 < default 10
    summary = compute_gamma(path)
    assert summary.n_qualifying == 4
    assert summary.gamma is None
    assert summary.applied is False
    assert summary.n_fallback == 0

    table = read_molecules(path)
    assert np.all(np.isnan(table["gamma"]))  # NaN "no factor computed" sentinel
    with h5py.File(path, "r") as f:
        grp = f["settings/gamma"]
        assert bool(grp.attrs["withheld"]) is True
        assert np.isnan(float(grp.attrs["gamma"]))


def test_all_traces_rejected_yields_no_qualifying(tmp_path: Path) -> None:
    # Every post-segment too short (don−acc == 15 < 20) → 0 qualifying, γ withheld,
    # np.median([]) never called.
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1, acc=40, don=55)
    summary = compute_gamma(path)
    assert summary.n_molecules == 12
    assert summary.n_qualifying == 0
    assert summary.gamma is None
    assert summary.applied is False
    assert np.all(np.isnan(read_molecules(path)["gamma"]))


def test_requires_photobleach_frames(tmp_path: Path) -> None:
    # bleach_frames at the -1 undetected sentinel (compute_photobleach not run) → fail fast.
    path = tmp_path / "g.tether"
    d, a = _step_trace(n=120, acceptor_pb=40, donor_pb=100, gamma_true=1.2, alpha=0.1, seed=1)
    _build_store(path, np.stack([d] * 4), np.stack([a] * 4))
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        table["alpha"][:] = 0.1  # α present, but bleach_frames still (-1, -1)
        f["molecules"][TABLE][:] = table
    with pytest.raises(ValueError, match="compute_photobleach"):
        compute_gamma(path)


def test_requires_leakage_alpha(tmp_path: Path) -> None:
    # bleach_frames present but /molecules.alpha still NaN (compute_leakage_alpha not
    # run) → fail fast, enforcing the background → α → γ order.
    path = tmp_path / "g.tether"
    donor_rows, acceptor_rows = [], []
    for i in range(4):
        d, a = _step_trace(
            n=120, acceptor_pb=40, donor_pb=100, gamma_true=1.2, alpha=0.1, seed=i + 1
        )
        donor_rows.append(d)
        acceptor_rows.append(a)
    _build_store(path, np.stack(donor_rows), np.stack(acceptor_rows))
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        for i in range(4):
            table["bleach_frames"][i] = (100, 40)  # α left at NaN default
        f["molecules"][TABLE][:] = table
    with pytest.raises(ValueError, match="compute_leakage_alpha"):
        compute_gamma(path)


def test_rejects_unknown_intensity_quantity(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=4, gamma_true=1.2, alpha=0.1)
    with pytest.raises(ValueError, match="intensity_quantity"):
        compute_gamma(path, intensity_quantity="nonsense")


def test_missing_trace_layer_raises_keyerror(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=4, gamma_true=1.2, alpha=0.1)
    with h5py.File(path, "r+") as f:
        del f["traces"]["donor_corrected"]
    with pytest.raises(KeyError, match="run extraction first"):
        compute_gamma(path)


def test_only_new_group_is_settings_gamma(tmp_path: Path) -> None:
    # schema-guard: the writer may add ONLY the additive /settings/gamma group.
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1)
    with h5py.File(path, "r") as f:
        before = _all_names(f)
    compute_gamma(path)
    with h5py.File(path, "r") as f:
        after = _all_names(f)
    assert set(after) - set(before) == {"settings/gamma"}


def test_nonzero_frame_range_start_converts_correctly(tmp_path: Path) -> None:
    # A non-zero frame_range start exercises the absolute→local bleach-frame conversion;
    # the same γ must be recovered as with start=0.
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1, start=10)
    summary = compute_gamma(path)
    assert summary.applied is True
    assert summary.n_qualifying == 12
    assert summary.gamma == pytest.approx(1.2, abs=0.1)


def test_recompute_overwrites_settings(tmp_path: Path) -> None:
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1)
    compute_gamma(path)
    compute_gamma(path, half_window=2)
    with h5py.File(path, "r") as f:
        assert f["settings/gamma"].attrs["half_window"] == 2


def test_skips_invalid_frame_range_and_writes_correct_rows(tmp_path: Path) -> None:
    # A molecule with an invalid frame_range (end <= start) is skipped; the per-molecule
    # γ must still land on the CORRECT global rows (the local_i → processed_rows[local_i]
    # remapping), leaving the skipped row at its NaN default.
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        table["frame_range"][5] = (50, 50)  # end == start → skipped
        f["molecules"][TABLE][:] = table

    summary = compute_gamma(path)
    assert summary.n_molecules == 11  # the skipped molecule is not examined
    assert summary.n_qualifying == 11

    table = read_molecules(path)
    assert np.isnan(table["gamma"][5])  # skipped row untouched
    others = [i for i in range(12) if i != 5]
    assert np.all(table["gamma"][others] == pytest.approx(1.2, abs=0.2))


def test_never_bleach_donor_still_qualifies(tmp_path: Path) -> None:
    # donor_pb == trace end (donor never bleaches) → the post-step donor-only segment
    # runs to the end; the clamp keeps donor_pb_local == n_local and γ is recovered.
    path = tmp_path / "g.tether"
    _gamma_store(path, n_mol=12, gamma_true=1.2, alpha=0.1, n=120, acc=40, don=120)
    summary = compute_gamma(path)
    assert summary.applied is True
    assert summary.n_qualifying == 12
    assert summary.gamma == pytest.approx(1.2, abs=0.1)
