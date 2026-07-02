# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared analysis ``.tether`` readers (M2 S8, FR-ANALYZE; ``tether.analysis._store``).

Direct coverage for the contract both analysis views depend on: quantity
resolution, the missing-trace-layer error path, curation filtering, the
``molecule_keys`` intersection, the analysis-window fallback, and the empty store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.analysis._store import QUANTITY_KEYS, resolve_quantity, windowed_channels  # noqa: E402
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

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


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


# --- resolve_quantity --------------------------------------------------------


def test_resolve_quantity_maps_known_keys() -> None:
    assert resolve_quantity("corrected") == ("donor_corrected", "acceptor_corrected")
    assert resolve_quantity("raw") == ("donor_raw", "acceptor_raw")
    assert set(QUANTITY_KEYS) == {"corrected", "raw"}


def test_resolve_quantity_unknown_raises() -> None:
    with pytest.raises(ValueError, match="intensity_quantity must be one of"):
        resolve_quantity("bogus")


# --- windowed_channels -------------------------------------------------------


def test_missing_trace_layer_raises(tmp_path) -> None:
    donor = np.full((2, 8), 300.0)
    acceptor = np.full((2, 8), 200.0)
    path = tmp_path / "s.tether"
    proj, _ = _build_store(path, donor, acceptor)
    with h5py.File(path, "r+") as f:  # drop a layer to hit the missing-layer guard
        del f["traces"]["donor_corrected"]
    with pytest.raises(ValueError, match="layer"):
        windowed_channels(proj, None, "corrected", False)


def test_molecule_keys_intersection_selects_subset(tmp_path) -> None:
    donor = np.stack([np.full(8, 100.0 * (i + 1)) for i in range(3)])
    acceptor = np.full((3, 8), 50.0)
    proj, keys = _build_store(tmp_path / "s.tether", donor, acceptor)
    pairs = windowed_channels(proj, [keys[0], keys[2]], "corrected", False)
    assert len(pairs) == 2  # store order, rows 0 and 2
    assert float(pairs[0][0][0]) == pytest.approx(100.0)
    assert float(pairs[1][0][0]) == pytest.approx(300.0)


def test_excludes_rejected_unless_included(tmp_path) -> None:
    donor = np.stack([np.full(8, 100.0 * (i + 1)) for i in range(2)])
    acceptor = np.full((2, 8), 50.0)
    proj, keys = _build_store(tmp_path / "s.tether", donor, acceptor)
    proj.reject(keys[0], labeler="tester")
    assert len(windowed_channels(proj, None, "corrected", False)) == 1
    assert len(windowed_channels(proj, None, "corrected", True)) == 2


def test_empty_store_returns_empty(tmp_path) -> None:
    path = tmp_path / "empty.tether"
    create_project(path, overwrite=True)
    proj = Project.open(path)
    assert windowed_channels(proj, None, "corrected", False) == []


def test_analysis_window_fallback_to_frame_range(tmp_path) -> None:
    donor = np.full((1, 12), 400.0)
    acceptor = np.full((1, 12), 100.0)
    path = tmp_path / "s.tether"
    proj, _ = _build_store(path, donor, acceptor)
    with h5py.File(path, "r+") as f:  # unset the analysis window -> should fall back
        table = f["molecules"]["table"][:]
        frame_range = tuple(int(v) for v in table["frame_range"][0])
        table["analysis_window"][0] = (0, 0)
        f["molecules"]["table"][:] = table
    pairs = windowed_channels(proj, None, "corrected", False)
    assert len(pairs) == 1
    assert pairs[0][0].shape[0] == frame_range[1] - frame_range[0]


def test_windowed_slice_respects_analysis_window(tmp_path) -> None:
    donor = np.full((1, 20), 400.0)
    acceptor = np.full((1, 20), 100.0)
    path = tmp_path / "s.tether"
    proj, _ = _build_store(path, donor, acceptor)
    with h5py.File(path, "r+") as f:
        table = f["molecules"]["table"][:]
        table["analysis_window"][0] = (3, 11)  # 8 frames
        f["molecules"]["table"][:] = table
    pairs = windowed_channels(proj, None, "corrected", False)
    assert pairs[0][0].shape[0] == 8
