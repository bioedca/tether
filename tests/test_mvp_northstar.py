# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end MVP north-star smoke (M2 S10, PRD §3.2 / §9 M2).

Drives the PRD §3.2 north-star capability headlessly through the real store layer:
**open a dataset → browse/curate with logged accept/reject → one-click vbFRET →
apparent-E histogram**. The one heavyweight step — the tMAVEN vbFRET sidecar — is
faked with a canned :class:`~tether.idealize.IdealizationResult` (the
``test_project_idealize`` pattern), so the whole path runs in the base CI matrix;
live vbFRET parity is gated separately by ``test_parity_sidecar``.

The store builder (:func:`_build_store`) is copied from ``test_project_idealize`` /
``test_analysis_histogram`` — the proven way to stand up a ``.tether`` with
controlled ``/molecules`` + ``/traces`` without the imaging pipeline. This ties the
independently-tested M2 legs (curation logging S5, one-click idealize S6, histogram
S8) into one path, which is exactly what "MVP integration pass" means.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")

from tether.analysis.histogram import population_apparent_e_histogram  # noqa: E402
from tether.idealize import IdealizationResult, StateModel, read_smd  # noqa: E402
from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    read_traces,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.idealize import list_idealizations, read_idealization  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


# --- store builder (copied from test_project_idealize; controlled traces) -----


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


def _build_store(
    path: Path,
    donor_intensity: np.ndarray,
    acceptor_intensity: np.ndarray,
    *,
    coords: np.ndarray | None = None,
) -> tuple[Project, list[str]]:
    """Write a ``.tether`` with controlled donor/acceptor *corrected* traces."""
    donor_intensity = np.asarray(donor_intensity, dtype="float64")
    acceptor_intensity = np.asarray(acceptor_intensity, dtype="float64")
    n, t = donor_intensity.shape
    coords = _distinct_coords(n) if coords is None else np.asarray(coords, dtype="float64")
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor_intensity),
        acceptor=_integrated(acceptor_intensity),
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


def _fake_runner(smd_path, *, nstates, model_type="vbconhmm", **_kw) -> IdealizationResult:
    """A canned two-level vbFRET result aligned to the SMD (no live sidecar)."""
    smd = read_smd(smd_path)
    n, t = smd.n_molecules, smd.n_frames
    means = np.linspace(0.3, 0.7, nstates) if nstates > 1 else np.array([0.5])
    pre = smd.pre_list if smd.pre_list is not None else np.zeros(n, dtype=int)
    post = smd.post_list if smd.post_list is not None else np.full(n, t, dtype=int)
    idealized = np.full((n, t), np.nan)
    for i in range(n):
        lo, hi = int(pre[i]), int(post[i])
        idealized[i, lo:hi] = means[0]
    model = StateModel(
        model_type=model_type,
        nstates=nstates,
        means=means,
        variances=np.full(nstates, 0.01),
        tmatrix=np.eye(nstates),
        norm_tmatrix=np.eye(nstates) * 0.9,
        elbo=1.0,
        dtype="FRET",
        idealized=idealized,
        ran=np.arange(n, dtype="int64"),
    )
    return IdealizationResult(
        model=model,
        state_paths={},
        dwells=[],
        model_path=Path(smd_path),
        status={"ok": True},
        molecule_keys=smd.molecule_keys,
    )


# --- the north-star path -----------------------------------------------------


def test_northstar_open_curate_idealize_histogram(tmp_path: Path) -> None:
    """Open → curate (accept/reject) → one-click vbFRET → apparent-E histogram."""
    n_mol, t = 3, 20
    # Constant traces: apparent E = A / (D + A) = 600 / 1000 = 0.6 for every frame.
    donor = np.full((n_mol, t), 400.0)
    acceptor = np.full((n_mol, t), 600.0)
    proj, keys = _build_store(tmp_path / "northstar.tether", donor, acceptor)

    # (1) OPEN / BROWSE — the store is the browsed dataset; the three molecules and
    # their corrected traces round-trip out of the .tether.
    assert len(keys) == n_mol
    assert read_traces(proj.path)["donor_corrected"].shape == (n_mol, t)

    # (2) CURATE — accept two, reject one; each writes a provenance-stamped /labels row.
    proj.accept(keys[0], labeler="tester")
    proj.accept(keys[1], labeler="tester")
    proj.reject(keys[2], labeler="tester")
    assert [proj.curation_label(k) for k in keys] == [1, 1, -1]

    # (3) ONE-CLICK vbFRET — idealize the accepted subset (fake sidecar), written
    # additively to /idealization/{model}.
    stored = proj.idealize(
        molecule_keys=keys[:2], nstates=2, model_name="vbfret", _runner=_fake_runner
    )
    assert stored.n_molecules == 2
    assert "vbfret" in list_idealizations(proj)
    reread = read_idealization(proj, "vbfret")
    assert reread.idealized is not None
    assert reread.molecule_keys == keys[:2]

    # (4) APPARENT-E HISTOGRAM — the rejected molecule is excluded by default (§7.5),
    # so the pooled population is the two accepted molecules only.
    hist = population_apparent_e_histogram(proj)
    assert hist.n_molecules == 2
    assert hist.n_samples == 2 * t
    assert float(hist.counts.sum()) > 0.0
    # The pooled apparent-E is 0.6; its mass lands in the bin covering 0.6, not 0.1.
    centers = hist.bin_centers
    assert hist.counts[np.argmin(np.abs(centers - 0.6))] > 0.0
    assert hist.counts[np.argmin(np.abs(centers - 0.1))] == 0.0

    # Including the rejected molecule restores all three (the reject is a reversible
    # filter, not a deletion).
    with_rejected = population_apparent_e_histogram(proj, include_rejected=True)
    assert with_rejected.n_molecules == 3


def test_northstar_unreject_restores_molecule(tmp_path: Path) -> None:
    """A rejected molecule is reversible — un-rejecting returns it to the histogram."""
    donor = np.full((2, 12), 400.0)
    acceptor = np.full((2, 12), 600.0)
    proj, keys = _build_store(tmp_path / "reversible.tether", donor, acceptor)

    proj.reject(keys[0], labeler="tester")
    assert population_apparent_e_histogram(proj).n_molecules == 1
    proj.unreject(keys[0], labeler="tester")
    assert proj.curation_label(keys[0]) == 0
    assert population_apparent_e_histogram(proj).n_molecules == 2
