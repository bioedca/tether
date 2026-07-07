# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-integrated cross-condition drift advisory (M5, FR-ML; PRD §7.5, §9 M5).

Locks :mod:`tether.project.drift`: a project's ``/features`` group by ``condition_id``, and the §9
M5 drift-flag acceptance at the store layer — a deliberately mismatched source/target condition
(shifted FRET, or shifted SNR) raises the advisory while a matched pair does not; unknown
conditions and a missing feature table are refused loudly. Trains no model (drift is model-free), so
it needs only SciPy (KS test) + h5py (the store), not scikit-learn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

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
from tether.io.schema import create_project  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.drift import condition_feature_matrices, cross_condition_drift  # noqa: E402
from tether.project.features import compute_features  # noqa: E402

_WINDOW = 21
# Two filenames differing only in the tRNA concentration (a condition-key field; the 35 pM sample
# concentration is provenance, not part of condition_id — tether.io.filename), so the two movies
# land in two distinct conditions.
_FILE_A = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
_FILE_B = "Bla_UCKOPSB_T-box_35pM_tRNA_300nM_011.tif"


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


def _condition_traces(
    n: int, *, seed: int, fret: float, total: float = 2000.0, noise: float = 8.0
) -> tuple[np.ndarray, np.ndarray]:
    """``n`` donor/acceptor traces at apparent FRET ~``fret``; SNR set by ``noise``.

    Donor ~ Normal((1-fret)*total, noise); acceptor = total - donor + Normal(0, noise), so the total
    intensity is ~constant (high SNR shrinks with larger ``noise``) and apparent E ~ ``fret``. The
    per-condition feature distribution is thus a tight cluster whose location (``fret_mean``) and
    spread/SNR (``snr``) are the knobs the drift test moves.
    """
    rng = np.random.default_rng(seed)
    t = 40
    donor = np.empty((n, t), dtype=np.float64)
    acceptor = np.empty((n, t), dtype=np.float64)
    d_mean = (1.0 - fret) * total
    for i in range(n):
        d = rng.normal(d_mean, noise, size=t)
        donor[i] = d
        acceptor[i] = (total - d) + rng.normal(0.0, noise, size=t)
    return donor, acceptor


def _write_movie(
    path: Path,
    movie_id: str,
    sha_hex: str,
    filename: str,
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    create: bool,
) -> None:
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
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
        movie_id=movie_id,
        sha256=sha_hex,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    if create:
        create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=parse_filename(filename),
        registration_map=_reg_map(),
    )


def _two_condition_store(path: Path, *, a: dict, b: dict, n: int = 60) -> tuple[Project, str, str]:
    """Two-condition ``.tether`` (A from ``_FILE_A``, B from ``_FILE_B``); features written.

    ``a`` / ``b`` are ``_condition_traces`` kwargs (``seed``, ``fret``, ``noise``). Returns the
    project and the two ``condition_id`` values (read back from ``/molecules``).
    """
    da, aa = _condition_traces(n, **a)
    db, ab = _condition_traces(n, **b)
    _write_movie(path, "mov-A", f"{1:064x}", _FILE_A, da, aa, create=True)
    _write_movie(path, "mov-B", f"{2:064x}", _FILE_B, db, ab, create=False)
    proj = Project.open(path)
    compute_features(proj)

    mols = read_molecules(path)
    conditions = [c.decode() if isinstance(c, bytes) else str(c) for c in mols["condition_id"]]
    cond_a, cond_b = conditions[0], conditions[-1]
    assert cond_a != cond_b  # two distinct conditions
    return proj, cond_a, cond_b


def test_matched_conditions_do_not_flag(tmp_path) -> None:
    # Same FRET + SNR spec, different draws -> no advisory (§9 M5: "a matched pair does not").
    proj, cond_a, cond_b = _two_condition_store(
        tmp_path / "p.tether",
        a={"seed": 1, "fret": 0.5},
        b={"seed": 2, "fret": 0.5},
    )
    report = cross_condition_drift(proj, cond_a, cond_b)
    assert report.drifted is False
    assert report.drifted_features == ()


def test_shifted_fret_condition_flags(tmp_path) -> None:
    # Condition A ~ 0.3 FRET, B ~ 0.7 FRET -> the FRET-range axis drifts (§9 M5: "a mismatched pair
    # raises the advisory").
    proj, cond_a, cond_b = _two_condition_store(
        tmp_path / "p.tether",
        a={"seed": 1, "fret": 0.3},
        b={"seed": 2, "fret": 0.7},
    )
    report = cross_condition_drift(proj, cond_a, cond_b)
    assert report.drifted is True
    assert "fret_mean" in report.drifted_features


def test_shifted_snr_condition_flags(tmp_path) -> None:
    # Same FRET, very different noise -> the SNR axis drifts (the other of PRD §7.5's named axes).
    proj, cond_a, cond_b = _two_condition_store(
        tmp_path / "p.tether",
        a={"seed": 1, "fret": 0.5, "noise": 8.0},
        b={"seed": 2, "fret": 0.5, "noise": 160.0},
    )
    report = cross_condition_drift(proj, cond_a, cond_b)
    assert report.drifted is True
    assert "snr" in report.drifted_features


def test_condition_feature_matrices_groups_every_molecule(tmp_path) -> None:
    # Grouping covers all featured molecules (labeled or not), one matrix per condition, column
    # count = the engineered feature schema.
    from tether.ml.features import FEATURE_NAMES  # noqa: PLC0415

    proj, cond_a, cond_b = _two_condition_store(
        tmp_path / "p.tether",
        a={"seed": 1, "fret": 0.5},
        b={"seed": 2, "fret": 0.5},
        n=45,
    )
    matrices = condition_feature_matrices(proj)
    assert set(matrices) == {cond_a, cond_b}
    assert matrices[cond_a].shape == (45, len(FEATURE_NAMES))
    assert matrices[cond_b].shape == (45, len(FEATURE_NAMES))


def test_unknown_condition_raises(tmp_path) -> None:
    proj, cond_a, _ = _two_condition_store(
        tmp_path / "p.tether",
        a={"seed": 1, "fret": 0.5},
        b={"seed": 2, "fret": 0.5},
    )
    with pytest.raises(KeyError, match="no featured molecules"):
        cross_condition_drift(proj, cond_a, "no_such_condition")


def test_before_compute_features_raises(tmp_path) -> None:
    path = tmp_path / "p.tether"
    donor, acceptor = _condition_traces(20, seed=1, fret=0.5)
    _write_movie(path, "mov-A", f"{1:064x}", _FILE_A, donor, acceptor, create=True)
    proj = Project.open(path)
    with pytest.raises(KeyError, match="features"):
        cross_condition_drift(proj, "any", "other")
    with pytest.raises(KeyError, match="features"):
        condition_feature_matrices(proj)
