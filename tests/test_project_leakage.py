# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-level leakage α from post-acceptor-bleach tails (M3, FR-CORRECT).

Locks :func:`tether.project.leakage.compute_leakage_alpha`: given per-channel
``bleach_frames`` (written by PR #74's detector) and ``/traces``, it must write the
dataset-median leakage factor into the frozen ``/molecules.alpha`` for every
processed molecule, stamp ``/settings/leakage`` provenance, and **withhold** the
factor below ``min_qualifying_traces`` — all additive (schema-guard green: the only
new group is the additive ``/settings/leakage``). Headless; runs in the base CI
matrix.
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
from tether.project.leakage import compute_leakage_alpha  # noqa: E402

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


def _build_store(path: Path, donor: np.ndarray, acceptor: np.ndarray) -> list[str]:
    """Write a ``.tether`` with controlled donor/acceptor *corrected* traces."""
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
    return [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]


def _leaky_trace(
    *, n: int, acceptor_pb: int, donor_pb: int, alpha: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    donor = rng.normal(1000.0, 4.0, n)
    donor[donor_pb:] = rng.normal(0.0, 4.0, n - donor_pb)
    acceptor = np.empty(n, dtype=np.float64)
    acceptor[:acceptor_pb] = rng.normal(600.0, 4.0, acceptor_pb)
    tail = slice(acceptor_pb, donor_pb)
    acceptor[tail] = alpha * donor[tail] + rng.normal(0.0, 4.0, donor_pb - acceptor_pb)
    acceptor[donor_pb:] = rng.normal(0.0, 4.0, n - donor_pb)
    return donor, acceptor


def _cohort_store(
    path: Path, *, n_mol: int, alpha: float, n: int = 120, acc: int = 30, don: int = 110
) -> None:
    """Build a store of ``n_mol`` leaky traces + set their bleach_frames directly.

    The per-channel bleach frames are the detector's (PR #74) job and are separately
    tested; here they are set directly so the leakage estimate is exercised in
    isolation over a controlled tail.
    """
    donor = np.stack(
        [
            _leaky_trace(n=n, acceptor_pb=acc, donor_pb=don, alpha=alpha, seed=i + 1)[0]
            for i in range(n_mol)
        ]
    )
    acceptor = np.stack(
        [
            _leaky_trace(n=n, acceptor_pb=acc, donor_pb=don, alpha=alpha, seed=i + 1)[1]
            for i in range(n_mol)
        ]
    )
    _build_store(path, donor, acceptor)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        for i in range(n_mol):
            table["bleach_frames"][i] = (don, acc)  # (donor_pb, acceptor_pb), absolute (start=0)
        f["molecules"][TABLE][:] = table


def _all_names(f: h5py.File) -> list[str]:
    names: list[str] = []
    f.visit(names.append)
    return sorted(names)


def test_writes_dataset_median_alpha_to_all_molecules(tmp_path: Path) -> None:
    path = tmp_path / "leak.tether"
    _cohort_store(path, n_mol=12, alpha=0.1)

    summary = compute_leakage_alpha(path)
    assert summary.n_molecules == 12
    assert summary.n_qualifying == 12
    assert summary.applied is True
    assert summary.alpha == pytest.approx(0.1, abs=0.02)
    assert summary.source == "post-acceptor-bleach-tail"

    table = read_molecules(path)
    # The single per-condition factor is written to every processed molecule.
    assert np.allclose(table["alpha"], summary.alpha)


def test_stamps_settings_leakage_provenance(tmp_path: Path) -> None:
    path = tmp_path / "leak.tether"
    _cohort_store(path, n_mol=12, alpha=0.1)
    compute_leakage_alpha(path)
    with h5py.File(path, "r") as f:
        grp = f["settings/leakage"]
        assert grp.attrs["source"] == "post-acceptor-bleach-tail"
        assert grp.attrs["withheld"] == np.False_ or grp.attrs["withheld"] is False
        assert grp.attrs["n_qualifying"] == 12
        assert grp.attrs["min_window_frames"] == 20
        assert float(grp.attrs["ceiling"]) == 0.3
        assert grp.attrs["min_qualifying_traces"] == 10
        assert grp.attrs["intensity_quantity"] == "corrected"
        assert 0.08 <= float(grp.attrs["alpha"]) <= 0.12
        assert "app_version" in grp.attrs
        assert "created_utc" in grp.attrs


def test_withholds_below_min_qualifying(tmp_path: Path) -> None:
    path = tmp_path / "leak.tether"
    _cohort_store(path, n_mol=4, alpha=0.1)  # 4 < default 10

    summary = compute_leakage_alpha(path)
    assert summary.n_qualifying == 4
    assert summary.alpha is None
    assert summary.applied is False

    table = read_molecules(path)
    # No factor written — alpha stays at its extraction default (NaN = "no factor
    # computed", the total-failure/apparent-E sentinel PR4 consumes).
    assert np.all(np.isnan(table["alpha"]))
    with h5py.File(path, "r") as f:
        grp = f["settings/leakage"]
        assert bool(grp.attrs["withheld"]) is True
        assert np.isnan(float(grp.attrs["alpha"]))


def test_recompute_overwrites_settings(tmp_path: Path) -> None:
    path = tmp_path / "leak.tether"
    _cohort_store(path, n_mol=12, alpha=0.1)
    compute_leakage_alpha(path)
    # A second pass with a lower min_window recomputes; the stamp reflects the latest.
    compute_leakage_alpha(path, min_window_frames=15)
    with h5py.File(path, "r") as f:
        assert f["settings/leakage"].attrs["min_window_frames"] == 15


def test_only_new_group_is_settings_leakage(tmp_path: Path) -> None:
    # The writer may add ONLY the additive /settings/leakage provenance group — no
    # other group/dataset, and nothing nested into /molecules or /traces.
    path = tmp_path / "leak.tether"
    _cohort_store(path, n_mol=12, alpha=0.1)
    with h5py.File(path, "r") as f:
        before = _all_names(f)
    compute_leakage_alpha(path)
    with h5py.File(path, "r") as f:
        after = _all_names(f)
    assert set(after) - set(before) == {"settings/leakage"}


def test_rejects_unknown_intensity_quantity(tmp_path: Path) -> None:
    path = tmp_path / "leak.tether"
    _cohort_store(path, n_mol=4, alpha=0.1)
    with pytest.raises(ValueError, match="intensity_quantity"):
        compute_leakage_alpha(path, intensity_quantity="nonsense")
