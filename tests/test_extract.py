# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-frame background + Sum integration -> tagged ``.tether`` traces (M1 S8).

Locks the first writer of extraction *data* into a ``.tether``:

* :func:`~tether.imaging.extract.molecule_key` — the cross-file content identity
  (movie ``sha256`` + quantized ``donor_xy``), deterministic and jitter-stable;
* :func:`~tether.imaging.extract.extract_molecules` — donor + acceptor Sum
  integration (reusing the M0.5 ``integrate_traces``) + cached temporal-mean patches;
* :func:`~tether.imaging.extract.write_extraction` — the additive-data writer:
  ``/movies`` + ``/molecules`` (coords, provisional ``condition_id``, imprinted
  registration tags, the apparent-E NaN/-1 substrate) + the six ``/traces`` arrays
  (zero-padded to the experiment-max frame count) + ``/patches`` + ``/settings``,
  all under the M0 schema freeze (proved additive via ``diff_manifest``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.imaging.aperture import aperture_masks  # noqa: E402
from tether.imaging.calibrate import LOW_CONFIDENCE_TAG, RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MOLECULE_KEY_QUANTUM_PX,
    MoleculeTraces,
    MovieMetadata,
    extract_molecules,
    molecule_key,
    read_molecules,
    read_patches,
    read_traces,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import build_manifest, create_project, diff_manifest, introspect  # noqa: E402

_BG = 80.0
_AMP = 300.0
_SHAPE = (64, 64)
_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")


# --- synthetic fixtures ------------------------------------------------------


def _channel(centers: np.ndarray, *, n_frames: int = 15) -> np.ndarray:
    """A constant-in-time ``(T, H, W)`` channel with a top-hat disk per centre."""
    frame = np.full(_SHAPE, _BG, dtype=np.float64)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in np.atleast_2d(centers):
        frame[np.hypot(rows - y, cols - x) <= 3] += _AMP  # 29 px disk == aperture disk
    return np.broadcast_to(frame, (n_frames, *_SHAPE)).copy()


def _molecules(donor: np.ndarray, acceptor: np.ndarray | None = None) -> ColocalizedMolecules:
    donor = np.atleast_2d(np.asarray(donor, dtype=np.float64))
    acceptor = donor.copy() if acceptor is None else np.atleast_2d(np.asarray(acceptor, float))
    n = donor.shape[0]
    return ColocalizedMolecules(
        donor_xy=donor,
        acceptor_xy=acceptor,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )


def _reg_map(rms_residual: float = 0.1) -> RegistrationMap:
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
        rms_residual=rms_residual,
        n_control_points=100,
    )


def _movie(movie_id: str, *, sha: str, n_frames: int = 15) -> MovieMetadata:
    return MovieMetadata(
        movie_id=movie_id,
        sha256=sha,
        n_frames=n_frames,
        height=_SHAPE[0],
        width=_SHAPE[1],
        uri=f"{movie_id}.tif",
        pixel_dtype=">u2",
        byteorder=">",
        frame_time=0.1,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )


def _extract_into(
    project: Path,
    *,
    movie_id: str,
    sha: str,
    donor: np.ndarray,
    n_frames: int = 15,
    reg: RegistrationMap | None = None,
) -> tuple[ColocalizedMolecules, MoleculeTraces, list[str]]:
    mols = _molecules(donor)
    donor_movie = _channel(mols.donor_xy, n_frames=n_frames)
    acceptor_movie = _channel(mols.acceptor_xy, n_frames=n_frames)
    traces = extract_molecules(donor_movie, acceptor_movie, mols)
    ids = write_extraction(
        project,
        movie=_movie(movie_id, sha=sha, n_frames=n_frames),
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=reg,
    )
    return mols, traces, ids


# --- molecule_key ------------------------------------------------------------


def test_molecule_key_deterministic_and_hex() -> None:
    key = molecule_key("abc123", np.array([20.0, 30.0]))
    assert key == molecule_key("abc123", np.array([20.0, 30.0]))  # pure content hash
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


def test_molecule_key_absorbs_subpixel_jitter_below_quantum() -> None:
    # Jitter under half the quantum rounds to the same grid cell -> identical key
    # (a split/subset file re-locating the same molecule still joins).
    base = np.array([20.0, 30.0])
    eps = MOLECULE_KEY_QUANTUM_PX / 4
    assert molecule_key("m", base) == molecule_key("m", base + eps)


def test_molecule_key_distinguishes_distinct_molecules_and_movies() -> None:
    assert molecule_key("m", [20.0, 30.0]) != molecule_key("m", [25.0, 30.0])
    assert molecule_key("m1", [20.0, 30.0]) != molecule_key("m2", [20.0, 30.0])


def test_molecule_key_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="finite"):
        molecule_key("m", [np.nan, 1.0])
    with pytest.raises(ValueError, match=r"\[x, y\] pair"):
        molecule_key("m", [1.0, 2.0, 3.0])


# --- extract_molecules -------------------------------------------------------


def test_extract_molecules_integrates_both_channels() -> None:
    n_psf = int(aperture_masks()[0].sum())
    # Distinct donor vs acceptor coords: each channel must be integrated at its OWN
    # positions (the acceptor channel has its spots only at acceptor_xy, so reading
    # it at donor_xy would give background-only and fail the acceptor assertion).
    mols = _molecules(
        np.array([[20.0, 20.0], [40.0, 30.0]]), np.array([[30.0, 20.0], [50.0, 30.0]])
    )
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols)
    assert traces.n_molecules == 2
    assert traces.n_frames == 15
    # constant top-hat -> corrected == amp * n_psf, uncorrected == (bg+amp)*n_psf
    np.testing.assert_allclose(traces.donor.intensity, _AMP * n_psf)
    np.testing.assert_allclose(traces.acceptor.intensity, _AMP * n_psf)
    np.testing.assert_allclose(traces.donor.total, (_BG + _AMP) * n_psf)
    assert traces.donor.valid.all() and traces.acceptor.valid.all()


def test_extract_molecules_patches_are_temporal_mean_crops() -> None:
    mols = _molecules(np.array([[20.0, 20.0]]))
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols)
    assert traces.donor_patches.shape == (1, 21, 21)
    assert traces.donor_patches.dtype == np.float32
    # centre pixel sits on the disk (bg + amp); a corner is pure background.
    assert traces.donor_patches[0, 10, 10] == pytest.approx(_BG + _AMP)
    assert traces.donor_patches[0, 0, 0] == pytest.approx(_BG)


def test_extract_molecules_empty() -> None:
    mols = _molecules(np.empty((0, 2)))
    traces = extract_molecules(_channel(np.empty((0, 2))), _channel(np.empty((0, 2))), mols)
    assert traces.n_molecules == 0
    assert traces.donor_patches.shape == (0, 21, 21)


def test_extract_molecules_rejects_mismatched_frame_counts() -> None:
    mols = _molecules(np.array([[20.0, 20.0]]))
    with pytest.raises(ValueError, match="frame counts differ"):
        extract_molecules(
            _channel(mols.donor_xy, n_frames=10), _channel(mols.donor_xy, n_frames=12), mols
        )


# --- write_extraction: round-trip --------------------------------------------


def test_write_extraction_round_trip(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    donor = np.array([[20.0, 20.0], [40.0, 30.0], [15.0, 45.0]])
    mols, traces, ids = _extract_into(project, movie_id="mov1", sha="sha-1", donor=donor)

    table = read_molecules(project)
    assert table.shape == (3,)
    np.testing.assert_allclose(np.asarray(table["donor_xy"].tolist()), donor)
    # provisional condition id from the filename parser (validated at M4)
    assert all(_as_str(c) == _PARSED.condition_id for c in table["condition_id"])
    assert all(_as_str(c) == _PARSED.condition_id for c in table["condition_id_provisional"])
    assert all(_as_str(s) == _PARSED.source_filename for s in table["source_filename"])
    # frame_range delimits the native extent; analysis window defaults to full
    for fr in table["frame_range"]:
        assert tuple(int(v) for v in fr) == (0, 15)
    for aw in table["analysis_window"]:
        assert tuple(int(v) for v in aw) == (0, 15)
    # molecule_key matches the standalone helper
    for i in range(3):
        assert _as_str(table["molecule_key"][i]) == molecule_key("sha-1", donor[i])
    # ids round-trip and are unique
    assert [_as_str(m) for m in table["molecule_id"]] == ids
    assert len(set(ids)) == 3 and all(m.startswith("mol-") for m in ids)

    # traces: six arrays, all (3, 15); corrected matches integrate_traces
    arrays = read_traces(project)
    assert set(arrays) == {
        f"{ch}_{q}" for ch in ("donor", "acceptor") for q in ("raw", "corrected", "background")
    }
    for arr in arrays.values():
        assert arr.shape == (3, 15)
    np.testing.assert_allclose(
        arrays["donor_corrected"], traces.donor.intensity, rtol=1e-5, atol=1e-2
    )
    np.testing.assert_allclose(arrays["acceptor_raw"], traces.acceptor.total, rtol=1e-5, atol=1e-2)

    patches = read_patches(project)
    assert patches["donor"].shape == (3, 21, 21)
    np.testing.assert_array_equal(patches["donor"], traces.donor_patches)


def test_write_extraction_apparent_e_substrate(tmp_path: Path) -> None:
    # No corrections / bleach at extraction: NaN alpha/gamma, -1 bleach, uncurated.
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="m", sha="s", donor=np.array([[20.0, 20.0]]))
    row = read_molecules(project)[0]
    assert np.isnan(row["alpha"]) and np.isnan(row["gamma"]) and row["delta"] == 0.0
    assert np.isnan(row["quality_class"]) and np.isnan(row["correction_confidence"])
    assert tuple(int(v) for v in row["bleach_frames"]) == (-1, -1)
    assert int(row["curation_label"]) == 0
    assert _as_str(row["correction_method"]) == "" and _as_str(row["category"]) == ""


def test_write_extraction_imprints_low_confidence_tag(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="m", sha="s", donor=np.array([[20.0, 20.0]]), reg=_reg_map(2.0))
    assert _as_str(read_molecules(project)[0]["tags"]) == LOW_CONFIDENCE_TAG
    # a within-gate map imprints no tag
    project2 = tmp_path / "p2.tether"
    create_project(project2)
    _extract_into(
        project2, movie_id="m", sha="s", donor=np.array([[20.0, 20.0]]), reg=_reg_map(0.1)
    )
    assert _as_str(read_molecules(project2)[0]["tags"]) == ""


def test_write_extraction_writes_movie_and_settings(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="mov1", sha="sha-1", donor=np.array([[20.0, 20.0]]))
    with h5py.File(project, "r") as f:
        movies = f["movies"]["table"][:]
        assert movies.shape == (1,)
        assert _as_str(movies["movie_id"][0]) == "mov1"
        assert _as_str(movies["sha256"][0]) == "sha-1"
        assert int(movies["n_frames"][0]) == 15
        np.testing.assert_array_equal(movies["donor_crop"][0], [1, 1, 64, 64])
        s = f["settings"]["extraction"].attrs
        assert int(s["window"]) == 21 and int(s["n_psf"]) == 29 and int(s["bg_window"]) == 10
        assert float(s["molecule_key_quantum_px"]) == pytest.approx(MOLECULE_KEY_QUANTUM_PX)
        assert _as_str(s["app_version"])


# --- write_extraction: zero-pad-to-max-T across movies -----------------------


def test_write_extraction_zero_pads_to_experiment_max_frames(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    # movie A: 2 molecules, 10 frames; then movie B: 1 molecule, 20 frames.
    _extract_into(
        project, movie_id="A", sha="sA", donor=np.array([[20.0, 20.0], [40.0, 30.0]]), n_frames=10
    )
    _, traces_b, _ = _extract_into(
        project, movie_id="B", sha="sB", donor=np.array([[25.0, 25.0]]), n_frames=20
    )

    arrays = read_traces(project)
    corrected = arrays["donor_corrected"]
    assert corrected.shape == (3, 20)  # padded to the max (B's 20)
    # movie A's two rows: their [10:20] tail is the zero pad
    np.testing.assert_array_equal(corrected[:2, 10:20], 0.0)
    assert np.all(corrected[:2, :10] != 0.0)
    # movie B's row: all 20 frames valid (matches its own integration)
    np.testing.assert_allclose(corrected[2], traces_b.donor.intensity[0], rtol=1e-5, atol=1e-2)

    # frame_range records each molecule's native extent inside the pad
    fr = read_molecules(project)["frame_range"]
    assert [tuple(int(v) for v in r) for r in fr] == [(0, 10), (0, 10), (0, 20)]


def test_write_extraction_pads_new_short_movie_rows(tmp_path: Path) -> None:
    # Reverse order: long movie first, then a shorter one -> new rows get the pad.
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="A", sha="sA", donor=np.array([[20.0, 20.0]]), n_frames=20)
    _extract_into(project, movie_id="B", sha="sB", donor=np.array([[25.0, 25.0]]), n_frames=12)
    corrected = read_traces(project)["donor_corrected"]
    assert corrected.shape == (2, 20)
    np.testing.assert_array_equal(corrected[1, 12:20], 0.0)  # short movie's tail pad
    assert np.all(corrected[1, :12] != 0.0)


def test_write_extraction_positional_join_preserved_across_movies(tmp_path: Path) -> None:
    # The core invariant: /molecules row i <-> /traces row i, preserved across the
    # append boundary. Each molecule's stored trace (over its own frame_range) equals
    # its own integration, and its molecule_key carries its own movie's sha256.
    project = tmp_path / "p.tether"
    create_project(project)
    _, tr_a, _ = _extract_into(
        project, movie_id="A", sha="sA", donor=np.array([[20.0, 20.0], [40.0, 30.0]]), n_frames=10
    )
    _, tr_b, _ = _extract_into(
        project, movie_id="B", sha="sB", donor=np.array([[25.0, 25.0]]), n_frames=14
    )
    table = read_molecules(project)
    corrected = read_traces(project)["donor_corrected"]
    assert table.shape == (3,) and corrected.shape == (3, 14)
    expected = [tr_a.donor.intensity[0], tr_a.donor.intensity[1], tr_b.donor.intensity[0]]
    for i, exp in enumerate(expected):
        t0, t1 = (int(v) for v in table["frame_range"][i])
        np.testing.assert_allclose(corrected[i, t0:t1], exp, rtol=1e-5, atol=1e-2)
    # row 2 (movie B) keys off movie B's sha + its own coord, not movie A's
    assert _as_str(table["molecule_key"][2]) == molecule_key("sB", np.array([25.0, 25.0]))
    assert _as_str(table["molecule_key"][0]) == molecule_key("sA", np.array([20.0, 20.0]))


# --- write_extraction: schema freeze + guards --------------------------------


def test_write_extraction_is_additive_only(tmp_path: Path) -> None:
    # The strongest schema-guard-equivalent: the written file's structure differs
    # from a fresh skeleton only by ADDED groups/datasets -> zero freeze violations.
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="m", sha="s", donor=np.array([[20.0, 20.0], [40.0, 30.0]]))
    with h5py.File(project, "r") as f:
        current = introspect(f)
    assert diff_manifest(build_manifest(), current) == []


def test_write_extraction_movies_are_write_once(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="dup", sha="s", donor=np.array([[20.0, 20.0]]))
    before_molecules = read_molecules(project).shape
    with h5py.File(project, "r") as f:
        before_movies = f["movies"]["table"].shape
    with pytest.raises(ValueError, match="write-once"):
        _extract_into(project, movie_id="dup", sha="s2", donor=np.array([[30.0, 30.0]]))
    # reject-before-mutate: the rejected re-extraction left the store unchanged
    assert read_molecules(project).shape == before_molecules
    with h5py.File(project, "r") as f:
        assert f["movies"]["table"].shape == before_movies


def test_write_extraction_rejects_foreign_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.h5"
    with h5py.File(foreign, "w") as f:
        f.create_group("not_tether")
    mols = _molecules(np.array([[20.0, 20.0]]))
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols)
    with pytest.raises(ValueError, match="not a .tether"):
        write_extraction(
            foreign, movie=_movie("m", sha="s"), molecules=mols, traces=traces, parsed=_PARSED
        )


def test_write_extraction_empty_molecules_writes_movie_only(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    mols = _molecules(np.empty((0, 2)))
    traces = extract_molecules(_channel(np.empty((0, 2))), _channel(np.empty((0, 2))), mols)
    ids = write_extraction(
        project, movie=_movie("m", sha="s"), molecules=mols, traces=traces, parsed=_PARSED
    )
    assert ids == []
    assert read_molecules(project).shape == (0,)
    with h5py.File(project, "r") as f:
        assert f["movies"]["table"].shape == (1,)  # the movie row is still written
        assert "donor_corrected" not in f["traces"]  # no trace datasets created yet


def test_write_extraction_rejects_row_mismatch(tmp_path: Path) -> None:
    project = tmp_path / "p.tether"
    create_project(project)
    mols = _molecules(np.array([[20.0, 20.0], [40.0, 30.0]]))
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols)
    fewer = _molecules(np.array([[20.0, 20.0]]))  # 1 molecule vs 2 trace rows
    with pytest.raises(ValueError, match="trace shape mismatch"):
        write_extraction(
            project, movie=_movie("m", sha="s"), molecules=fewer, traces=traces, parsed=_PARSED
        )


def test_write_extraction_rejects_frame_count_mismatch(tmp_path: Path) -> None:
    # movie.n_frames must equal the actual integrated trace width, or frame_range
    # would mislabel the zero-pad as valid native frames (or truncate real ones).
    project = tmp_path / "p.tether"
    create_project(project)
    mols = _molecules(np.array([[20.0, 20.0]]))
    traces = extract_molecules(
        _channel(mols.donor_xy, n_frames=12), _channel(mols.acceptor_xy, n_frames=12), mols
    )
    movie = _movie("m", sha="s", n_frames=20)  # claims 20 frames; the traces are 12
    with pytest.raises(ValueError, match="must describe the same movie"):
        write_extraction(project, movie=movie, molecules=mols, traces=traces, parsed=_PARSED)
    # reject-before-mutate: no molecule rows, no orphan /movies row, no trace datasets
    assert read_molecules(project).shape == (0,)
    with h5py.File(project, "r") as f:
        assert f["movies"]["table"].shape == (0,)
        assert "donor_corrected" not in f["traces"]


def test_write_extraction_rejects_inconsistent_window_before_mutating(tmp_path: Path) -> None:
    # A second movie with a different aperture window is rejected UP FRONT, so the
    # file is left untouched (no orphan /movies row, no desynced /patches) — the
    # reject-before-mutate / atomicity contract.
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="A", sha="sA", donor=np.array([[20.0, 20.0]]))  # window 21
    mols = _molecules(np.array([[25.0, 25.0]]))
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols, window=25)
    with pytest.raises(ValueError, match="extraction parameters differ"):
        write_extraction(
            project, movie=_movie("B", sha="sB"), molecules=mols, traces=traces, parsed=_PARSED
        )
    assert read_molecules(project).shape == (1,)  # only movie A survives
    with h5py.File(project, "r") as f:
        assert f["movies"]["table"].shape == (1,)  # no orphan movie row
        assert f["patches"]["donor"].shape == (1, 21, 21)  # /patches untouched


def test_write_extraction_rejects_inconsistent_aperture_params(tmp_path: Path) -> None:
    # Not just window: a later movie with a different disk_radius (or ring/bg_window)
    # is rejected, since its traces would be incomparable under one provenance record.
    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="A", sha="sA", donor=np.array([[20.0, 20.0]]))  # disk_radius 3
    mols = _molecules(np.array([[25.0, 25.0]]))
    traces = extract_molecules(
        _channel(mols.donor_xy), _channel(mols.acceptor_xy), mols, disk_radius=2.0
    )
    with pytest.raises(ValueError, match="extraction parameters differ"):
        write_extraction(
            project, movie=_movie("B", sha="sB"), molecules=mols, traces=traces, parsed=_PARSED
        )
    assert read_molecules(project).shape == (1,)  # only movie A survives


def test_write_extraction_rejects_malformed_trace_shape(tmp_path: Path) -> None:
    # The full-shape contract: a tampered trace array (here a truncated acceptor
    # total) is caught before any write, not silently stored at the wrong width.
    import dataclasses

    project = tmp_path / "p.tether"
    create_project(project)
    mols = _molecules(np.array([[20.0, 20.0]]))
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols)
    bad_acc = dataclasses.replace(traces.acceptor, total=traces.acceptor.total[:, :5])
    bad = dataclasses.replace(traces, acceptor=bad_acc)
    with pytest.raises(ValueError, match="trace shape mismatch"):
        write_extraction(
            project, movie=_movie("m", sha="s"), molecules=mols, traces=bad, parsed=_PARSED
        )
    assert read_molecules(project).shape == (0,)  # nothing written


def test_write_extraction_bad_settings_profile_rejected_before_mutating(tmp_path: Path) -> None:
    # A non-JSON-serializable settings profile is serialized up front, so it fails
    # before any append (no orphan /movies row) — the reject-before-mutate contract.
    project = tmp_path / "p.tether"
    create_project(project)
    mols = _molecules(np.array([[20.0, 20.0]]))
    traces = extract_molecules(_channel(mols.donor_xy), _channel(mols.acceptor_xy), mols)
    with pytest.raises(TypeError):  # a numpy array is not JSON-serializable
        write_extraction(
            project,
            movie=_movie("m", sha="s"),
            molecules=mols,
            traces=traces,
            parsed=_PARSED,
            settings={"threshold": np.arange(3)},
        )
    assert read_molecules(project).shape == (0,)
    with h5py.File(project, "r") as f:
        assert f["movies"]["table"].shape == (0,)


def test_write_extraction_returns_compatible_project(tmp_path: Path) -> None:
    from tether.io.schema import assert_is_compatible_project

    project = tmp_path / "p.tether"
    create_project(project)
    _extract_into(project, movie_id="m", sha="s", donor=np.array([[20.0, 20.0]]))
    assert assert_is_compatible_project(project) == 1  # still a valid, current project


def _as_str(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
