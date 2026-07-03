# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Store-level photobleach detection + auto analysis window (M3, FR-CORRECT).

Locks :func:`tether.project.photobleach.compute_photobleach`: it must populate the
frozen ``/molecules`` ``bleach_frames`` and set the auto ``analysis_window`` to
``(start, first-bleach-of-summed)`` exactly as the headless detector computes it,
while leaving a curator's manual window untouched (manual override wins). All
headless -> runs in the base CI matrix; no schema change (schema-guard green).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.fret.photobleach import detect_photobleach  # noqa: E402
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
from tether.project.core import Project  # noqa: E402
from tether.project.photobleach import compute_photobleach  # noqa: E402

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


def _build_store(path: Path, donor: np.ndarray, acceptor: np.ndarray) -> tuple[Project, list[str]]:
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
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


def _step(
    rng: np.random.Generator, n: int, k: int, level: float, noise: float = 50.0
) -> np.ndarray:
    d = rng.normal(0.0, noise, size=n)
    d[:k] += level
    return d


def _traces_with_known_bleach(
    n_frames: int = 100,
) -> tuple[np.ndarray, np.ndarray, list[int], list[int]]:
    rng = np.random.default_rng(7)
    donor_k = [70, 55, n_frames, 40]  # third molecule's donor never bleaches
    acceptor_k = [50, 85, 90, n_frames]  # fourth molecule's acceptor never bleaches
    donor = np.stack([_step(rng, n_frames, k, 1200.0) for k in donor_k])
    acceptor = np.stack([_step(rng, n_frames, k, 900.0) for k in acceptor_k])
    return donor, acceptor, donor_k, acceptor_k


def test_compute_photobleach_populates_frames_and_auto_window(tmp_path: Path) -> None:
    donor, acceptor, donor_k, acceptor_k = _traces_with_known_bleach()
    path = tmp_path / "pb.tether"
    _build_store(path, donor, acceptor)

    summary = compute_photobleach(path)
    assert summary.n_molecules == 4
    assert summary.intensity_quantity == "corrected"

    table = read_molecules(path)
    for i in range(4):
        res = detect_photobleach(donor[i], acceptor[i])
        # Stored frames/window match the headless detector exactly (start = 0).
        assert tuple(int(x) for x in table["bleach_frames"][i]) == (res.donor_pb, res.acceptor_pb)
        assert tuple(int(x) for x in table["analysis_window"][i]) == (0, res.sum_pb)
        # ...and the detector recovers the injected steps within the ±2 tolerance.
        assert abs(res.donor_pb - donor_k[i]) <= 2
        assert abs(res.acceptor_pb - acceptor_k[i]) <= 2


def test_never_bleaching_channel_leaves_full_window(tmp_path: Path) -> None:
    donor, acceptor, _, _ = _traces_with_known_bleach(n_frames=100)
    path = tmp_path / "pb.tether"
    _build_store(path, donor, acceptor)
    compute_photobleach(path)
    table = read_molecules(path)
    # Molecule 2's donor never bleaches -> its bleach frame is the trace end (100).
    assert int(table["bleach_frames"][2][0]) == 100
    # Molecule 3's acceptor never bleaches -> acceptor frame is the trace end.
    assert int(table["bleach_frames"][3][1]) == 100


def test_manual_window_is_not_overwritten(tmp_path: Path) -> None:
    donor, acceptor, _, _ = _traces_with_known_bleach()
    path = tmp_path / "pb.tether"
    _build_store(path, donor, acceptor)

    # A curator narrows molecule 0's window (!= the extraction default) *before*
    # detection runs.
    manual = (5, 30)
    with h5py.File(path, "r+") as f:
        table = f["molecules"][TABLE][:]
        table["analysis_window"][0] = manual
        f["molecules"][TABLE][:] = table

    compute_photobleach(path)

    table = read_molecules(path)
    # Manual window preserved; bleach frames still detected for that molecule.
    assert tuple(int(x) for x in table["analysis_window"][0]) == manual
    res0 = detect_photobleach(donor[0], acceptor[0])
    assert tuple(int(x) for x in table["bleach_frames"][0]) == (res0.donor_pb, res0.acceptor_pb)
    # A different molecule that was left at the default gets the auto window.
    res1 = detect_photobleach(donor[1], acceptor[1])
    assert tuple(int(x) for x in table["analysis_window"][1]) == (0, res1.sum_pb)


def test_rejects_unknown_intensity_quantity(tmp_path: Path) -> None:
    donor, acceptor, _, _ = _traces_with_known_bleach()
    path = tmp_path / "pb.tether"
    _build_store(path, donor, acceptor)
    with pytest.raises(ValueError, match="intensity_quantity"):
        compute_photobleach(path, intensity_quantity="nonsense")


def _all_names(f: h5py.File) -> list[str]:
    names: list[str] = []
    f.visit(names.append)  # every group/dataset name in the tree, not just the root
    return sorted(names)


def test_schema_guard_no_new_groups(tmp_path: Path) -> None:
    # The writer must only populate frozen fields — never add a group/dataset,
    # including one nested inside /molecules or /traces (hence the full-tree walk).
    donor, acceptor, _, _ = _traces_with_known_bleach()
    path = tmp_path / "pb.tether"
    _build_store(path, donor, acceptor)
    with h5py.File(path, "r") as f:
        before = _all_names(f)
    compute_photobleach(path)
    with h5py.File(path, "r") as f:
        after = _all_names(f)
    assert before == after


def test_dark_summed_trace_leaves_default_window(tmp_path: Path) -> None:
    # A molecule whose summed signal is bleached from frame 0 (sum_pb == 0) must
    # NOT get a zero-length (0, 0) window — that reads as "unset" downstream and
    # would widen to the full extent. The window stays at the extraction default
    # and the (0, 0) bleach_frames record the dark trace (CodeRabbit #74).
    rng = np.random.default_rng(11)
    n = 80
    dark = rng.normal(0.0, 5.0, size=(1, n))  # donor & acceptor both ~0 throughout
    path = tmp_path / "pb.tether"
    _build_store(path, dark, dark.copy())
    compute_photobleach(path)
    table = read_molecules(path)
    assert tuple(int(x) for x in table["analysis_window"][0]) == (0, n)  # default, not (0, 0)
    assert tuple(int(x) for x in table["bleach_frames"][0]) == (0, 0)  # bleached from frame 0
