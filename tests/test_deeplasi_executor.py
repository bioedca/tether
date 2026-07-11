# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute a Deep-LASI re-analysis :class:`WizardPlan` (M7, PRD §7.8 / §9 M7).

Drives :func:`tether.gui.deeplasi_executor.execute_plan` over hand-built plans on the
committed UCKOPSB fixtures — the same acquisition exercised by ``test_reconstruct`` and
``test_analysis_import`` — proving the executor half of the M7 wizard (§11 M7 PR #5):

* a ``reconstruct`` plan entry decodes the ``.mat`` + ``.tdat`` + SMD and writes a
  round-trip-ready project, its movie provenance **hashed from the raw ``.tif``** (the
  ``molecule_key`` join key proves the real hash was used, not a stub);
* an ``analysis_only`` entry imports a coordinate-less SMD / ``.txt`` as the degraded,
  round-trip-disabled project;
* the plan's ``"tdat"`` coordinate choice falls back to the export-aligned ``.mat``
  coordinates (with a surfaced warning) when the ``.tdat`` colocalized count exceeds the
  export's traced count — never a fabricated alignment;
* the run is fail-soft (a bad acquisition is recorded, the batch continues) unless
  ``raise_on_error`` is set.

The real ``…010`` movie is the ~0.9 GB acquisition and is not committed, so a tiny
synthetic ``.tif`` with the export's frame count stands in — reconstruction imports the
already-integrated legacy traces and never re-opens the movie for pixels, so only the
movie's provenance (hash + frame count + geometry) is needed (cf. ``test_reconstruct``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")
pytest.importorskip("tifffile")

import h5py
import tifffile

import tether.gui.deeplasi_executor as executor_module
from tether.gui.deeplasi_executor import execute_plan
from tether.gui.deeplasi_wizard import PlannedAcquisition, WizardMode, WizardPlan
from tether.imaging.extract import molecule_key
from tether.io.deeplasi import DeepLasiTraces, read_deeplasi_mat
from tether.io.intake import AcquisitionFileSet
from tether.io.recover import RecoveredCoordinates
from tether.io.tdat import read_tdat
from tether.project.analysis_import import read_analysis_only_marker
from tether.project.correct import METHOD_APPARENT_UNAVAILABLE

_FIXTURES = Path(__file__).parent / "fixtures"
_MAT = _FIXTURES / "deeplasi_export_slice.mat"
_TXT = _FIXTURES / "deeplasi_traces_slice.txt"
_TDAT = _FIXTURES / "tdat_coloc_slice.tdat"
_SMD4 = _FIXTURES / "smd_4mol.hdf5"

_CATEGORIES = ("dynamic", "static", "noise")


# --------------------------------------------------------------------------- #
# helpers — build a plan directly + a stand-in movie
# --------------------------------------------------------------------------- #


def _write_movie(path: Path, n_frames: int, *, height: int = 8, width: int = 8) -> Path:
    """A tiny uncompressed big-endian ``>u2`` stand-in for the (uncommitted) …010 movie."""
    data = np.zeros((n_frames, height, width), dtype=">u2")
    tifffile.imwrite(path, data, photometric="minisblack", byteorder=">")
    return path


def _fileset(
    key: str = "recon010",
    *,
    movie: Path | None = None,
    tdat: Path | None = None,
    mat: Path | None = None,
    txt: Path | None = None,
    smd: Path | None = None,
) -> AcquisitionFileSet:
    return AcquisitionFileSet(
        key=key,
        condition_id="uckopsb",
        video_index="010",
        movie=movie,
        tdat=tdat,
        mat=mat,
        txt=txt,
        smd=smd,
    )


def _planned(
    fileset: AcquisitionFileSet,
    mode: WizardMode,
    *,
    coordinate_source: str = "",
    output_name: str | None = None,
    categories: tuple[str, ...] = (),
) -> PlannedAcquisition:
    return PlannedAcquisition(
        fileset=fileset,
        mode=mode,
        coordinate_source=coordinate_source,
        output_name=output_name or f"{fileset.key}.tether",
        categories=categories,
        rationale="test",
        warnings=(),
    )


def _plan(*acquisitions: PlannedAcquisition) -> WizardPlan:
    return WizardPlan(
        acquisitions=tuple(acquisitions), skipped=(), unpaired=(), ignored=(), shared_maps=()
    )


def _read_molecules(path: Path) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return f["molecules"]["table"][:]


def _decode(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


# --------------------------------------------------------------------------- #
# §9 M7 acceptance — a mixed reconstruct + analysis-only batch
# --------------------------------------------------------------------------- #


def test_reconstruct_and_analysis_only_batch(tmp_path: Path) -> None:
    """A plan with both modes writes both projects; each has the right round-trip status."""
    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset("recon010", movie=movie, mat=_MAT, tdat=_TDAT, txt=_TXT, smd=_SMD4),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
        categories=_CATEGORIES,
    )
    ao = _planned(_fileset("ao_smd", smd=_SMD4), WizardMode.ANALYSIS_ONLY)

    report = execute_plan(_plan(recon, ao), tmp_path / "out")

    assert report.ok
    assert report.n_ok == 2
    assert report.n_failed == 0

    r, a = report.executed
    # the reconstruct entry → a round-trip-ready project
    assert r.key == "recon010"
    assert r.ok
    assert r.coordinate_source == "mat"
    assert r.reconstruct is not None
    assert r.reconstruct.n_molecules == export.n_molecules == 4
    assert r.reconstruct.n_curated == 1  # only smd_4mol[0] is in the committed first-4 slice
    assert r.reconstruct.n_categories == 3
    assert r.output_path == tmp_path / "out" / "recon010.tether"
    assert r.output_path.exists()
    assert read_analysis_only_marker(r.output_path) is None  # round-trip-capable

    # the analysis-only entry → a degraded, round-trip-disabled project
    assert a.key == "ao_smd"
    assert a.ok
    assert a.analysis_only is not None
    assert a.analysis_only.n_molecules == 4
    assert a.analysis_only.source_kind == "smd"
    marker = read_analysis_only_marker(a.output_path)
    assert marker is not None
    assert marker.round_trip_available is False


def test_reconstruct_with_smd_but_no_txt_still_runs(tmp_path: Path) -> None:
    """The curated-match reference falls back to the ``.mat`` when no ``.txt`` is present.

    Without the exact-match ``.txt`` the SMD cross-checks against the ``.mat`` ``donc``/
    ``accc`` (a ~5e-6 storage difference under the matcher's tight default tolerance), so
    the run must still complete — the curated selection just under-populates, never errors.
    """
    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, smd=_SMD4),  # SMD present, no .txt
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )

    report = execute_plan(_plan(recon), tmp_path / "out")

    e = report.executed[0]
    assert e.ok
    assert e.reconstruct is not None
    assert e.reconstruct.n_molecules == 4
    # the .mat reference differs from the SMD by ~5e-6 (> the matcher's atol=1e-6), so the
    # curated selection under-populates to zero — proving why the exact-match .txt matters
    # (with the .txt present the same acquisition curates 1, cf. the batch test).
    assert e.reconstruct.n_curated == 0


def test_movie_metadata_is_hashed_from_the_tif(tmp_path: Path) -> None:
    """The reconstructed molecule_keys key on the real ``.tif`` hash — not a stub value."""
    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT), WizardMode.RECONSTRUCT, coordinate_source="mat"
    )

    report = execute_plan(_plan(recon), tmp_path / "out")

    assert report.ok
    expected_sha = hashlib.sha256(movie.read_bytes()).hexdigest()
    mols = _read_molecules(report.executed[0].output_path)
    # molecule_key = sha256(movie_hash | quantized donor_xy): recomputing it with the real
    # tif's hash reproduces every stored key ⇒ the executor hashed the actual movie file.
    expected_keys = [molecule_key(expected_sha, export.donor_xy[i]) for i in range(4)]
    assert [_decode(v) for v in mols["molecule_key"]] == expected_keys


# --------------------------------------------------------------------------- #
# coordinate source — honour .tdat where aligned, fall back honestly otherwise
# --------------------------------------------------------------------------- #


def test_tdat_coordinate_source_falls_back_to_mat_when_counts_differ(tmp_path: Path) -> None:
    """The committed ``.tdat`` (250 colocalized) can't align to the 4-molecule export → mat."""
    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, tdat=_TDAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="tdat",
    )

    report = execute_plan(_plan(recon), tmp_path / "out")

    e = report.executed[0]
    assert e.ok
    assert e.coordinate_source == "mat"  # fell back — never fabricated a .tdat→traced join
    assert any(".tdat" in w for w in e.warnings)
    # the stored coordinates are the export-aligned .mat coordinates
    mols = _read_molecules(e.output_path)
    np.testing.assert_array_equal(mols["donor_xy"], export.donor_xy)


def test_tdat_corrections_flow_through_to_apparent_e(tmp_path: Path) -> None:
    """A reconstruct reads the ``.tdat`` corrections; the real fixture's γ=0 → apparent-E."""
    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, tdat=_TDAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )

    report = execute_plan(_plan(recon), tmp_path / "out")

    e = report.executed[0]
    assert e.ok
    assert e.reconstruct is not None
    # DefaultGamma=0 in the committed .tdat ⇒ no usable γ ⇒ the apparent-E substrate
    assert e.reconstruct.corrections_applied is False
    mols = _read_molecules(e.output_path)
    assert {_decode(v) for v in mols["correction_method"]} == {METHOD_APPARENT_UNAVAILABLE}
    assert np.isnan(mols["gamma"]).all()


def test_tdat_corrections_are_forwarded_to_reconstruct(tmp_path: Path, monkeypatch) -> None:
    """The executor forwards the ``.tdat``'s ``TdatCorrections`` (not ``None``).

    The committed ``.tdat`` has ``DefaultGamma=0``, so ``corrections_applied`` alone can't
    distinguish "forwarded the corrections" from "forwarded ``None``" (both → apparent-E).
    Capturing the actual keyword proves the flow-through — and that a reconstruct *without*
    a ``.tdat`` forwards ``None``.
    """
    captured: dict[str, object] = {}
    real_reconstruct = executor_module.reconstruct_project

    def spy(output_path, **kwargs):
        captured["corrections"] = kwargs.get("corrections")
        return real_reconstruct(output_path, **kwargs)

    monkeypatch.setattr(executor_module, "reconstruct_project", spy)

    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, tdat=_TDAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )
    assert execute_plan(_plan(recon), tmp_path / "out").ok
    passed = captured["corrections"]
    expected = read_tdat(_TDAT).corrections
    assert passed is not None
    assert passed.gamma == expected.gamma == 0.0  # the real .tdat's factors, forwarded
    assert passed.alpha == expected.alpha  # Tether leakage = Deep-LASI beta
    assert passed.deeplasi_beta == expected.deeplasi_beta

    # a reconstruct with no .tdat forwards corrections=None
    captured.clear()
    movie2 = _write_movie(tmp_path / "mov2.tif", export.n_frames)
    recon2 = _planned(
        _fileset("no_tdat", movie=movie2, mat=_MAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )
    assert execute_plan(_plan(recon2), tmp_path / "out2").ok
    assert captured["corrections"] is None


def test_tdat_coordinates_used_when_counts_align(tmp_path: Path, monkeypatch) -> None:
    """When the ``.tdat`` colocalized count matches the export, its coordinates are used.

    The committed ``.tdat`` (250 colocalized) never aligns to the 4-molecule export, so the
    ``source == "tdat"`` honoured branch is reached by simulating an aligned ``.tdat``
    coordinate set — proving the plan's choice propagates end-to-end (no fallback warning,
    ``source == "tdat"`` on the summary, the ``.tdat`` coordinates written).
    """
    export = read_deeplasi_mat(_MAT)
    real_recover = executor_module.recover_coordinates

    def fake_recover(*, tdat=None, mat=None, prefer="tdat"):
        if tdat is not None:  # an aligned .tdat: colocalized count == the traced export
            return RecoveredCoordinates(
                donor_xy=export.donor_xy.copy(),
                acceptor_xy=export.acceptor_xy.copy(),
                source="tdat",
            )
        return real_recover(mat=mat, prefer=prefer)

    monkeypatch.setattr(executor_module, "recover_coordinates", fake_recover)

    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, tdat=_TDAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="tdat",
    )

    e = execute_plan(_plan(recon), tmp_path / "out").executed[0]
    assert e.ok
    assert e.coordinate_source == "tdat"  # honoured, not fallen back
    assert e.warnings == ()  # no fallback advisory
    mols = _read_molecules(e.output_path)
    np.testing.assert_array_equal(mols["donor_xy"], export.donor_xy)


@pytest.mark.parametrize(
    "exc",
    [ValueError("simulated non-two-colour .tdat"), KeyError(1)],
    ids=["ValueError", "KeyError"],
)
def test_unrecoverable_tdat_coordinates_fall_back_to_mat(
    tmp_path: Path, monkeypatch, exc: Exception
) -> None:
    """A ``.tdat`` coordinate recovery raising ``ValueError`` **or** ``KeyError`` → ``.mat``.

    A non-two-colour ``.tdat`` raises ``ValueError``; a degenerate one (declared
    ``ChannelsWithData`` but an empty ``ParticlesColocalized``) raises ``KeyError`` — both
    must fall back to the mandatory ``.mat`` coordinates, not fail the acquisition.
    """
    export = read_deeplasi_mat(_MAT)
    real_recover = executor_module.recover_coordinates

    def fake_recover(*, tdat=None, mat=None, prefer="tdat"):
        if tdat is not None:
            raise exc
        return real_recover(mat=mat, prefer=prefer)

    monkeypatch.setattr(executor_module, "recover_coordinates", fake_recover)

    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, tdat=_TDAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="tdat",
    )

    e = execute_plan(_plan(recon), tmp_path / "out").executed[0]
    assert e.ok
    assert e.coordinate_source == "mat"
    assert any("could not be recovered" in w for w in e.warnings)


def test_undecodable_tdat_degrades_to_mat_apparent_e(tmp_path: Path, monkeypatch) -> None:
    """An undecodable ``.tdat`` still reconstructs from the ``.mat`` (apparent-E), not fails.

    ``read_tdat`` raises on the unported Deep-LASI ``findPart`` modes 4/5, yet a
    ``.mat``-sourced reconstruct needs the ``.tdat`` only for its corrections — so the
    acquisition must degrade to apparent-E with a surfaced warning, never be recorded as a
    hard failure (it is fully reconstructable from the movie + ``.mat``).
    """

    def boom(_path):
        raise ValueError("Deep-LASI ParticleDetectionMode 4 is not supported")

    monkeypatch.setattr(executor_module, "read_tdat", boom)

    export = read_deeplasi_mat(_MAT)
    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, tdat=_TDAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )

    e = execute_plan(_plan(recon), tmp_path / "out").executed[0]
    assert e.ok  # reconstructed from the .mat despite the undecodable .tdat
    assert e.reconstruct is not None
    assert e.reconstruct.n_molecules == 4
    assert e.reconstruct.corrections_applied is False  # no corrections → apparent-E
    assert e.coordinate_source == "mat"
    assert any("could not be decoded" in w for w in e.warnings)
    mols = _read_molecules(e.output_path)
    assert {_decode(v) for v in mols["correction_method"]} == {METHOD_APPARENT_UNAVAILABLE}


def test_curated_reference_ignores_a_mismatched_txt(tmp_path: Path, monkeypatch) -> None:
    """A ``.txt`` whose molecule count differs from the export is not used as the reference.

    The SMD match must be against an export-row-aligned reference; a ``.txt`` covering a
    different molecule set is skipped for the ``.mat`` fallback rather than raising in the
    matcher (its row count would not equal ``recovered.n_molecules``).
    """
    export = read_deeplasi_mat(_MAT)
    real_read_txt = executor_module.read_deeplasi_txt

    def fake_read_txt(path):
        t = real_read_txt(path)  # drop a molecule so the .txt count != the export count
        return DeepLasiTraces(
            donor_corrected=t.donor_corrected[:-1], acceptor_corrected=t.acceptor_corrected[:-1]
        )

    monkeypatch.setattr(executor_module, "read_deeplasi_txt", fake_read_txt)

    movie = _write_movie(tmp_path / "mov.tif", export.n_frames)
    recon = _planned(
        _fileset(movie=movie, mat=_MAT, txt=_TXT, smd=_SMD4),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )

    e = execute_plan(_plan(recon), tmp_path / "out").executed[0]
    assert e.ok  # the mismatched .txt is ignored; matched against the .mat instead
    assert e.reconstruct is not None
    assert e.reconstruct.n_curated == 0  # .mat reference (~5e-6 diff) → no match under atol


# --------------------------------------------------------------------------- #
# analysis-only source handling
# --------------------------------------------------------------------------- #


def test_analysis_only_from_bare_txt(tmp_path: Path) -> None:
    """A bare Deep-LASI ``.txt`` (no SMD) imports as analysis-only."""
    ao = _planned(_fileset("ao_txt", txt=_TXT), WizardMode.ANALYSIS_ONLY)

    report = execute_plan(_plan(ao), tmp_path / "out")

    e = report.executed[0]
    assert e.ok
    assert e.analysis_only is not None
    assert e.analysis_only.source_kind == "txt"
    marker = read_analysis_only_marker(e.output_path)
    assert marker is not None
    assert marker.round_trip_available is False


def test_analysis_only_prefers_smd_over_txt(tmp_path: Path) -> None:
    """With both an SMD and a ``.txt`` present, the richer SMD source is used."""
    ao = _planned(_fileset("ao_both", smd=_SMD4, txt=_TXT), WizardMode.ANALYSIS_ONLY)

    report = execute_plan(_plan(ao), tmp_path / "out")

    assert report.ok
    assert report.executed[0].analysis_only.source_kind == "smd"


def test_analysis_only_seeds_categories(tmp_path: Path) -> None:
    """The plan's category seeds reach the analysis-only importer."""
    ao = _planned(_fileset("ao", smd=_SMD4), WizardMode.ANALYSIS_ONLY, categories=("a", "b"))

    report = execute_plan(_plan(ao), tmp_path / "out")

    assert report.executed[0].analysis_only.n_categories == 2


# --------------------------------------------------------------------------- #
# output naming + fail-soft / raise semantics
# --------------------------------------------------------------------------- #


def test_output_paths_named_by_plan(tmp_path: Path) -> None:
    """Each project is written at ``output_dir / output_name`` (created if absent)."""
    ao = _planned(
        _fileset("acq1", smd=_SMD4),
        WizardMode.ANALYSIS_ONLY,
        output_name="custom_name.tether",
    )
    out_dir = tmp_path / "nested" / "out"

    report = execute_plan(_plan(ao), out_dir)

    assert report.executed[0].output_path == out_dir / "custom_name.tether"
    assert report.executed[0].output_path.exists()


def test_fail_soft_records_error_and_continues(tmp_path: Path) -> None:
    """One bad acquisition is recorded (no partial output) while the batch continues."""
    export = read_deeplasi_mat(_MAT)
    bad_movie = _write_movie(tmp_path / "bad.tif", export.n_frames + 1)  # wrong frame count
    bad = _planned(
        _fileset("bad", movie=bad_movie, mat=_MAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )
    good = _planned(_fileset("good", smd=_SMD4), WizardMode.ANALYSIS_ONLY)

    report = execute_plan(_plan(bad, good), tmp_path / "out")

    assert not report.ok
    assert report.n_failed == 1
    assert report.n_ok == 1
    failed = report.failed[0]
    assert failed.key == "bad"
    assert not failed.ok
    assert "same movie" in failed.error  # reconstruct's frame-mismatch ValueError
    assert not failed.output_path.exists()  # atomic import ⇒ no partial output left behind
    assert report.succeeded[0].key == "good"
    assert report.succeeded[0].output_path.exists()


def test_raise_on_error_propagates_the_first_failure(tmp_path: Path) -> None:
    """``raise_on_error=True`` re-raises instead of recording the failure."""
    export = read_deeplasi_mat(_MAT)
    bad_movie = _write_movie(tmp_path / "bad.tif", export.n_frames + 1)
    bad = _planned(
        _fileset("bad", movie=bad_movie, mat=_MAT),
        WizardMode.RECONSTRUCT,
        coordinate_source="mat",
    )

    with pytest.raises(ValueError, match="same movie"):
        execute_plan(_plan(bad), tmp_path / "out", raise_on_error=True)


def test_overwrite_guards_then_replaces(tmp_path: Path) -> None:
    """A second run refuses to clobber (fail-soft) until ``overwrite=True``."""
    ao = _planned(_fileset("acq", smd=_SMD4), WizardMode.ANALYSIS_ONLY)

    assert execute_plan(_plan(ao), tmp_path / "out").ok

    clobber = execute_plan(_plan(ao), tmp_path / "out")
    assert not clobber.ok
    assert "FileExistsError" in clobber.failed[0].error

    assert execute_plan(_plan(ao), tmp_path / "out", overwrite=True).ok


def test_empty_plan_is_a_no_op(tmp_path: Path) -> None:
    """A plan with no runnable acquisitions runs nothing and is not ``ok``."""
    report = execute_plan(_plan(), tmp_path / "out")

    assert report.executed == ()
    assert report.n_ok == 0
    assert report.ok is False
