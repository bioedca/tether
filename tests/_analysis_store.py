# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared ``.tether`` store-builder for the analysis population tests.

The B1 TDP, B2 dwell, B3 transition-probability, and C1 state-number suites all need
the same fixture: a ``.tether`` seeded with molecules + traces + a persisted
``/idealization/{model}`` whose per-molecule Viterbi ``state_paths`` and state
``means`` are controlled by the test, with the molecules reading back **FRESH** (so
the §5.1 staleness filter keeps them) unless deliberately corrupted. This module is
the single source of that builder; the four ``tests/test_analysis_*.py`` suites import
:func:`build_store_with_model` from here rather than each carrying a near-verbatim copy.

It is a plain helper module (not a ``test_*`` file, so pytest never collects it) and,
under pytest's default ``prepend`` import mode with no ``tests/__init__.py``, is
importable as a top-level module (``from _analysis_store import ...``) on every OS in
the CI matrix. The four suites gate on ``pytest.importorskip("h5py")`` before importing
it, so the module-level tether/h5py imports here are only reached once those deps exist.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from tether.idealize import NO_STATE
from tether.imaging.aperture import IntegratedTraces
from tether.imaging.calibrate import RegistrationMap
from tether.imaging.coloc import ColocalizedMolecules
from tether.imaging.extract import (
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    read_traces,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D
from tether.imaging.split import ChannelGeometry
from tether.io.filename import parse_filename
from tether.io.schema import create_project
from tether.project import Project
from tether.project.idealize import input_provenance_hash, write_idealization_model
from tether.project.labels import CurationLabel

__all__ = [
    "MEANS",
    "PARSED",
    "WINDOW",
    "build_store_with_channels",
    "build_store_with_model",
    "e_traces",
    "fresh_input_hashes",
    "integrated",
    "reg_map",
    "to_str",
]

#: Parsed condition metadata stamped onto the seeded movie (a representative UCKOPSB name).
PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
#: Patch/aperture window size for the seeded traces.
WINDOW = 21
#: The three canonical state levels used by the analysis fixtures.
MEANS = np.array([0.2, 0.55, 0.85])


def reg_map() -> RegistrationMap:
    """An identity registration map (donor↔acceptor with no spatial transform)."""
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


def integrated(intensity: np.ndarray) -> IntegratedTraces:
    """Wrap a per-frame intensity array as :class:`IntegratedTraces` (flat background)."""
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def e_traces(e_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Donor/acceptor intensities whose ratio yields the given apparent-E matrix.

    ``NaN`` cells (outside a molecule's window) map to ``donor = acceptor = 0`` so the
    apparent E reads back ``NaN`` there.
    """
    e = np.asarray(e_matrix, dtype="float64")
    donor = (1.0 - e) * 1000.0
    acceptor = e * 1000.0
    nan = np.isnan(e)
    donor[nan] = 0.0
    acceptor[nan] = 0.0
    return donor, acceptor


def to_str(value: object) -> str:
    """Decode an ``h5py`` scalar (bytes) or coerce any value to ``str``."""
    return value.decode() if isinstance(value, bytes) else str(value)


def fresh_input_hashes(path: Path) -> list[str]:
    """Recompute each molecule's *current* provenance hash (so it reads back FRESH).

    Mirrors :func:`tether.project.idealize._stale_keys_for` exactly: recorded == current
    -> not stale. Returns hashes in store (molecule) order.
    """
    molecules = read_molecules(path)
    traces = read_traces(path)
    donor_all = np.asarray(traces["donor_corrected"], dtype="float64")
    acceptor_all = np.asarray(traces["acceptor_corrected"], dtype="float64")
    pre_all = molecules["analysis_window"]
    fr_all = molecules["frame_range"]
    hashes: list[str] = []
    for i in range(molecules.shape[0]):
        lo, hi = int(pre_all[i][0]), int(pre_all[i][1])
        if hi <= lo:
            lo, hi = int(fr_all[i][0]), int(fr_all[i][1])
        hashes.append(
            input_provenance_hash(
                donor_all[i, lo:hi],
                acceptor_all[i, lo:hi],
                quantity="corrected",
                alpha=float(molecules["alpha"][i]),
                gamma=float(molecules["gamma"][i]),
                correction_method=to_str(molecules["correction_method"][i]),
                pre=lo,
                post=hi,
            )
        )
    return hashes


def build_store_with_model(
    tmp_path: Path,
    state_matrix: np.ndarray,
    means: np.ndarray,
    *,
    windows: list[tuple[int, int]] | None = None,
    rejected: list[bool] | None = None,
    stale: list[bool] | None = None,
    model_name: str = "vbconhmm",
    name: str = "exp.tether",
) -> tuple[Project, list[str]]:
    """A ``.tether`` whose molecule ``i`` has apparent E = its idealized level per frame
    and a persisted ``/idealization/{model_name}`` with ``state_matrix[i]`` as its
    Viterbi path (NO_STATE outside the window) and state levels ``means``.

    ``input_hashes`` are the *real* current provenance hashes, so every molecule reads
    back FRESH — unless ``stale[i]`` corrupts its hash to force it STALE. ``windows``
    sets each molecule's ``analysis_window`` (default ``(0, n_frames)`` for every row);
    ``rejected[i]`` marks molecule ``i`` REJECTED for the §7.5 curation filter.
    """
    state_matrix = np.asarray(state_matrix, dtype="int64")
    means = np.asarray(means, dtype="float64")
    n, n_frames = state_matrix.shape
    # observed E follows the idealized level (NaN -> D=A=0 -> apparent NaN)
    e_matrix = np.full((n, n_frames), np.nan)
    on = state_matrix != NO_STATE
    e_matrix[on] = means[state_matrix[on]]
    donor, acceptor = e_traces(e_matrix)
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=integrated(donor),
        acceptor=integrated(acceptor),
        donor_patches=np.zeros((n, WINDOW, WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, WINDOW, WINDOW), dtype="float32"),
        window=WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=n_frames,
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
        parsed=PARSED,
        registration_map=reg_map(),
    )
    with h5py.File(path, "r+") as f:
        table = f["molecules"]["table"][:]
        for i in range(n):
            win = (0, n_frames) if windows is None else windows[i]
            table["analysis_window"][i] = win
            if rejected is not None and rejected[i]:
                table["curation_label"][i] = int(CurationLabel.REJECT)
        f["molecules"]["table"][:] = table

    molecules = read_molecules(path)
    keys = [to_str(k) for k in molecules["molecule_key"]]
    ids = [to_str(x) for x in molecules["molecule_id"]]

    idealized = np.full((n, n_frames), np.nan)
    idealized[on] = means[state_matrix[on]]

    hashes = fresh_input_hashes(path)
    if stale is not None:
        hashes = [f"STALE-{h}" if stale[i] else h for i, h in enumerate(hashes)]

    write_idealization_model(
        path,
        model_name=model_name,
        model_type=model_name,
        nstates=int(means.size),
        dtype="FRET",
        means=means,
        variances=np.full(means.size, 0.01),
        tmatrix=None,
        norm_tmatrix=None,
        elbo=1.0,
        idealized=idealized,
        state_paths=state_matrix,
        molecule_keys=keys,
        molecule_ids=ids,
        input_hashes=hashes,
        intensity_quantity="corrected",
        selected_by="fixed",
        elbo_by_nstates=None,
        app_version="test",
        created_utc="2026-01-01T00:00:00Z",
        overwrite=True,
        frac=np.full(means.size, 1.0 / means.size),
    )
    return Project.open(path), keys


def build_store_with_channels(
    tmp_path: Path,
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    windows: list[tuple[int, int]] | None = None,
    rejected: list[bool] | None = None,
    name: str = "chan.tether",
) -> tuple[Project, list[str]]:
    """A pre-idealization ``.tether`` seeded with **explicit** donor/acceptor channels.

    Unlike :func:`build_store_with_model` (which derives piecewise-constant channels from
    an idealized-E matrix and writes an ``/idealization`` model), this seeds each
    molecule ``i`` with the raw ``donor[i]`` / ``acceptor[i]`` per-frame intensities and
    writes **no** model — the substrate the pre-idealization channel views need
    (cross-correlation, the raw FRET cloud, the anticorrelation-event finder). ``windows``
    sets each molecule's ``analysis_window`` (default ``(0, n_frames)``); ``rejected[i]``
    marks molecule ``i`` REJECTED for the §7.5 curation filter. Returns the opened project
    and the molecule keys in store order.
    """
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    if donor.shape != acceptor.shape or donor.ndim != 2:
        raise ValueError(f"donor and acceptor must be matching (n, t) arrays, got {donor.shape}")
    n, n_frames = donor.shape
    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=integrated(donor),
        acceptor=integrated(acceptor),
        donor_patches=np.zeros((n, WINDOW, WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, WINDOW, WINDOW), dtype="float32"),
        window=WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=n_frames,
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
        parsed=PARSED,
        registration_map=reg_map(),
    )
    if windows is not None or rejected is not None:
        with h5py.File(path, "r+") as f:
            table = f["molecules"]["table"][:]
            for i in range(n):
                if windows is not None:
                    table["analysis_window"][i] = windows[i]
                if rejected is not None and rejected[i]:
                    table["curation_label"][i] = int(CurationLabel.REJECT)
            f["molecules"]["table"][:] = table

    keys = [to_str(k) for k in read_molecules(path)["molecule_key"]]
    return Project.open(path), keys
