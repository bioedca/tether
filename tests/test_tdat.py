# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode tests for the Deep-LASI TIRFdata ``.tdat`` reader (PRD §7.8, App B; M0.5 S6).

Locks the colocalized-coordinate decode and — critically — the Appendix-B
correction-factor remap (Deep-LASI's ``Alpha``/``Beta`` naming is inverted
relative to Tether's), against a real 250-molecule slice of the UCKOPSB ``.tdat``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("numpy")

import h5py  # noqa: E402  (guarded by importorskip above)
import numpy as np  # noqa: E402

from tether.io import read_detection_settings, read_tdat, remap_correction_factors  # noqa: E402
from tether.io.tdat import Tdat, TdatDetectionSettings  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tdat_coloc_slice.tdat"


@pytest.fixture(scope="module")
def tdat() -> Tdat:
    return read_tdat(FIXTURE)


def _write_minimal_tdat(
    path: Path,
    table: np.ndarray | None,
    *,
    channels: list[float] | None = None,
    reference: float = 1.0,
    detection_mode: float | None = None,
) -> None:
    """Write a minimal ``temp`` struct for decoder tests.

    ``table`` is an ``(N, 17)`` findColoc matrix (written MATLAB-transposed behind
    a cell object reference); ``None`` writes the MATLAB empty-array marker (a
    non-reference dims stub). ``channels`` / ``reference`` populate
    ``ChannelsWithData`` / ``MappingReferenceChannel`` (MATLAB 1-based).
    ``detection_mode`` writes ``temp/ParticleDetectionMode`` when given; ``None``
    omits the leaf (so the reader falls back to the wavelet class default).
    """
    if channels is None:
        channels = [1.0, 2.0]
    with h5py.File(path, "w") as f:
        temp = f.create_group("temp")
        if table is None:
            # MATLAB stores an empty [] as a small non-reference uint64 dims marker.
            temp.create_dataset("ParticlesColocalized", data=np.array([0, 0], dtype=np.uint64))
        else:
            refs = f.create_group("#refs#")
            ds = refs.create_dataset("a", data=np.asarray(table, dtype=np.float64).T)
            pc = temp.create_dataset("ParticlesColocalized", shape=(1, 1), dtype=h5py.ref_dtype)
            pc[0, 0] = ds.ref
        for name in ("DefaultAlpha", "DefaultBeta", "DefaultGamma"):
            temp.create_dataset(name, data=np.array([[0.0]]))
        temp.create_dataset(
            "ChannelsWithData", data=np.array(channels, dtype=np.float64).reshape(-1, 1)
        )
        temp.create_dataset("MappingReferenceChannel", data=np.array([[reference]]))
        if detection_mode is not None:
            temp.create_dataset("ParticleDetectionMode", data=np.array([[detection_mode]]))


def _coloc_row(
    ch1: tuple[float, float, float],
    ch2: tuple[float, float, float] | None,
    flags: tuple[int, int, int, int],
    nfile: float = 1.0,
) -> list[float]:
    """One 17-column findColoc row: X1 Y1 #1 | X2 Y2 #2 | … | bCh1..4 | nFile."""
    row = [0.0] * 17
    row[0:3] = list(ch1)
    if ch2 is not None:
        row[3:6] = list(ch2)
    row[12:16] = [float(b) for b in flags]
    row[16] = nfile
    return row


def particles_table() -> np.ndarray:
    """A two-molecule single-channel (N, 17) findColoc table (ch1 only)."""
    return np.asarray(
        [
            _coloc_row((10.0, 20.0, 1.0), None, (1, 0, 0, 0)),
            _coloc_row((30.0, 40.0, 2.0), None, (1, 0, 0, 0)),
        ],
        dtype=np.float64,
    )


def test_decode_shape_and_channels(tdat: Tdat) -> None:
    coloc = tdat.colocalization
    assert coloc.n_molecules == 250
    assert coloc.channel_present.tolist() == [True, True, False, False]
    assert sorted(coloc.coords) == [0, 1]
    assert tdat.channels_with_data == (0, 1)
    assert tdat.reference_channel == 0
    assert coloc.coords[0].shape == (250, 2)
    assert coloc.coords[1].shape == (250, 2)
    # absent channels carry no coordinate entry at all
    assert 2 not in coloc.coords
    assert 3 not in coloc.coords


def test_first_molecule_coords_are_xy_zero_based(tdat: Tdat) -> None:
    coloc = tdat.colocalization
    # findColoc row 0 stores [row, col] (1-based): donor (485, 15), acceptor (487, 23).
    # Tether flips to [x, y] = [col, row] (PRD §11.1) and converts 1-based -> 0-based:
    # donor [15, 485] - 1 = [14, 484]; acceptor [23, 487] - 1 = [22, 486].
    assert coloc.coords[0][0].tolist() == pytest.approx([14.0, 484.0], abs=1e-6)
    assert coloc.coords[1][0].tolist() == pytest.approx([22.0, 486.0], abs=1e-6)
    # detection indices are source bookkeeping, kept 1-based and integer.
    assert coloc.detection_index[0][:3].tolist() == [9, 13, 17]
    assert coloc.detection_index[1][:3].tolist() == [31, 33, 27]
    assert coloc.detection_index[0].dtype == np.int64
    assert coloc.file_index.tolist() == [1] * 250


def test_coords_within_frame_and_finite(tdat: Tdat) -> None:
    for xy in tdat.colocalization.coords.values():
        assert xy.dtype == np.float64
        assert np.all(np.isfinite(xy))
        assert xy.min() >= 0.0  # 0-based: no negative pixels
        # This acquisition splits the chip left/right, so each channel half is
        # 256 px wide: x = col < 256 (the convention check — [row, col] would put
        # row, which reaches ~512, in x), y = row < 512.
        assert xy[:, 0].max() < 256.0
        assert xy[:, 1].max() < 512.0


def test_donor_acceptor_not_swapped(tdat: Tdat) -> None:
    # Matched molecules sit at near-identical positions in each cropped half, so the
    # donor->acceptor offset is just the few-pixel channel registration; a frame
    # swap/flip would blow this up to hundreds of pixels.
    donor = tdat.colocalization.coords[0]
    acceptor = tdat.colocalization.coords[1]
    assert np.median(np.linalg.norm(acceptor - donor, axis=1)) < 15.0


def test_factors_decoded_and_remapped(tdat: Tdat) -> None:
    corr = tdat.corrections
    # This acquisition stores zero default factors.
    assert (corr.deeplasi_alpha, corr.deeplasi_beta, corr.deeplasi_gamma) == (0.0, 0.0, 0.0)
    assert (corr.alpha, corr.delta, corr.gamma) == (0.0, 0.0, 0.0)


def test_appendix_b_remap_is_not_a_naming_passthrough() -> None:
    # The load-bearing correctness check (PRD App B / §7.8): Deep-LASI's Beta is
    # leakage and its Alpha is direct excitation — the OPPOSITE of Tether's naming.
    corr = remap_correction_factors(deeplasi_alpha=0.5, deeplasi_beta=0.1, deeplasi_gamma=1.2)
    assert corr.alpha == 0.1  # Tether leakage = Deep-LASI Beta (NOT Deep-LASI Alpha)
    assert corr.delta == 0.0  # direct excitation inert without ALEX (Deep-LASI Alpha dropped)
    assert corr.gamma == 1.2  # gamma is gamma
    # Deep-LASI originals retained for provenance.
    assert corr.deeplasi_alpha == 0.5
    assert corr.deeplasi_beta == 0.1


def test_empty_colocalization_returns_zero(tmp_path: Path) -> None:
    path = tmp_path / "empty.tdat"
    _write_minimal_tdat(path, table=None)
    result = read_tdat(path)
    assert result.colocalization.n_molecules == 0
    assert result.colocalization.coords == {}
    assert not result.colocalization.channel_present.any()
    # factors and channel layout still decode from a coordinate-less file.
    assert result.channels_with_data == (0, 1)
    assert result.reference_channel == 0


def test_minimal_referenced_table_decodes(tmp_path: Path) -> None:
    path = tmp_path / "ref.tdat"
    _write_minimal_tdat(path, table=particles_table())
    coloc = read_tdat(path).colocalization
    assert coloc.n_molecules == 2
    # rows store [row, col] -> flipped to [x, y] and 1-based -> 0-based:
    # (10, 20) -> [19, 9], (30, 40) -> [39, 29].
    assert np.allclose(coloc.coords[0], [[19.0, 9.0], [39.0, 29.0]])
    assert coloc.channel_present.tolist() == [True, False, False, False]


def test_invalid_channel_metadata_raises(tmp_path: Path) -> None:
    # A corrupt MappingReferenceChannel (fractional / out-of-range) must be
    # rejected, not silently rounded into a bogus channel id.
    fractional = tmp_path / "frac.tdat"
    _write_minimal_tdat(fractional, table=particles_table(), reference=2.5)
    with pytest.raises(ValueError, match="MappingReferenceChannel"):
        read_tdat(fractional)
    # a near-integer must also be rejected (exact check, not tolerant rounding)
    near = tmp_path / "near.tdat"
    _write_minimal_tdat(near, table=particles_table(), reference=1.0000001)
    with pytest.raises(ValueError, match="integer channel index"):
        read_tdat(near)
    out_of_range = tmp_path / "oor.tdat"
    _write_minimal_tdat(out_of_range, table=particles_table(), channels=[1.0, 9.0])
    with pytest.raises(ValueError, match="ChannelsWithData"):
        read_tdat(out_of_range)


def test_malformed_coloc_table_raises(tmp_path: Path) -> None:
    # A non-null reference to a 2-D table with the wrong column count is corrupt
    # input and must fail loudly rather than decode as fewer molecules.
    path = tmp_path / "malformed.tdat"
    bad = np.zeros((2, 16), dtype=np.float64)  # 16 columns, not 17
    _write_minimal_tdat(path, table=bad)
    with pytest.raises(ValueError, match="expected 17 columns"):
        read_tdat(path)


def test_partial_colocalization_row_is_filtered(tmp_path: Path) -> None:
    # A two-channel file with one fully-colocalized row and one row missing the
    # acceptor: only the complete row is published, and no channel ever exposes a
    # placeholder (post-1-based-conversion negative) coordinate.
    path = tmp_path / "partial.tdat"
    table = np.asarray(
        [
            _coloc_row((10.0, 20.0, 1.0), (11.0, 28.0, 1.0), (1, 1, 0, 0)),  # complete
            _coloc_row((30.0, 40.0, 2.0), None, (1, 0, 0, 0)),  # acceptor missing
        ],
        dtype=np.float64,
    )
    _write_minimal_tdat(path, table=table)
    coloc = read_tdat(path).colocalization
    assert coloc.n_molecules == 1
    assert coloc.channel_present.tolist() == [True, True, False, False]
    # [row, col] -> [x, y], 1-based -> 0-based: donor (10, 20) -> [19, 9];
    # acceptor (11, 28) -> [27, 10].
    assert np.allclose(coloc.coords[0], [[19.0, 9.0]])
    assert np.allclose(coloc.coords[1], [[27.0, 10.0]])
    for xy in coloc.coords.values():
        assert xy.min() >= 0.0  # no fabricated negative placeholder coords


def test_not_a_tdat_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a.tdat"
    with h5py.File(bad, "w") as f:
        f.create_dataset("something", data=[1, 2, 3])
    with pytest.raises(ValueError, match="not a Deep-LASI TIRFdata"):
        read_tdat(bad)


def test_corrections_dataclass_is_frozen() -> None:
    corr = remap_correction_factors(0.0, 0.0, 0.0)
    with pytest.raises(AttributeError):
        corr.alpha = 1.0  # type: ignore[misc]


# --- particle-detection mode decode (PR-C3c-decode-A; ADR-0021) --------------
#
# temp/ParticleDetectionMode is a plain ``double`` leaf holding the Deep-LASI
# findPart method code (1 wavelet, 2 intensity, 3 bandpass); it maps to the Tether
# ParticleDetectionMode string so an import reproduces the detection method.
# The per-channel DetectionThreshold (an MCOS property) is decoded in a follow-up.


def test_detection_mode_decoded_from_fixture(tdat: Tdat) -> None:
    # The real UCKOPSB acquisition was detected with mode 2 (intensity).
    assert tdat.detection.mode == "intensity"
    # threshold stays None (the MCOS Channel decode is PR-C3c-decode-B).
    assert tdat.detection.threshold is None


def test_read_detection_settings_matches_read_tdat(tdat: Tdat) -> None:
    # The lightweight reader and the full read_tdat agree on the detection settings.
    light = read_detection_settings(FIXTURE)
    assert light == TdatDetectionSettings(mode="intensity", threshold=None)
    assert light == tdat.detection


@pytest.mark.parametrize(
    ("code", "expected"),
    [(1.0, "wavelet"), (2.0, "intensity"), (3.0, "bandpass")],
)
def test_detection_mode_code_maps_to_string(tmp_path: Path, code: float, expected: str) -> None:
    path = tmp_path / f"mode{code:g}.tdat"
    _write_minimal_tdat(path, table=particles_table(), detection_mode=code)
    assert read_tdat(path).detection.mode == expected
    assert read_detection_settings(path).mode == expected


def test_detection_mode_absent_defaults_to_wavelet(tmp_path: Path) -> None:
    # A .tdat without the leaf decodes to the Deep-LASI class default (TRACERdata.m).
    path = tmp_path / "no_mode.tdat"
    _write_minimal_tdat(path, table=particles_table(), detection_mode=None)
    assert read_tdat(path).detection.mode == "wavelet"
    assert read_detection_settings(path).mode == "wavelet"


@pytest.mark.parametrize("code", [0.0, 4.0, 5.0, 99.0])
def test_unsupported_detection_mode_raises(tmp_path: Path, code: float) -> None:
    # Modes 4 (local-variance) / 5 (ZMW) are not ported; an out-of-range code is
    # refused, never silently mapped to a wrong detector.
    path = tmp_path / f"bad{code:g}.tdat"
    _write_minimal_tdat(path, table=particles_table(), detection_mode=code)
    with pytest.raises(ValueError, match="not supported"):
        read_tdat(path)
    with pytest.raises(ValueError, match="not supported"):
        read_detection_settings(path)


def test_non_integer_detection_mode_raises(tmp_path: Path) -> None:
    # A fractional mode code is corruption, rejected (not truncated to a real mode).
    path = tmp_path / "frac.tdat"
    _write_minimal_tdat(path, table=particles_table(), detection_mode=2.5)
    with pytest.raises(ValueError, match="integer mode code"):
        read_detection_settings(path)


def test_read_detection_settings_not_a_tdat_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a.tdat"
    with h5py.File(bad, "w") as f:
        f.create_dataset("something", data=[1, 2, 3])
    with pytest.raises(ValueError, match="not a Deep-LASI TIRFdata"):
        read_detection_settings(bad)


def test_decoded_modes_are_exactly_the_detection_enum(tmp_path: Path) -> None:
    # Cross-lock: the decoder's three mode strings are precisely the frozen
    # ParticleDetectionMode members (so io's literals can't drift from the enum).
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    from tether.imaging.detect import ParticleDetectionMode  # noqa: PLC0415

    decoded = set()
    for code in (1.0, 2.0, 3.0):
        path = tmp_path / f"m{code:g}.tdat"
        _write_minimal_tdat(path, table=particles_table(), detection_mode=code)
        decoded.add(read_detection_settings(path).mode)
    assert decoded == {m.value for m in ParticleDetectionMode}


def test_detection_settings_dataclass_is_frozen() -> None:
    settings = TdatDetectionSettings(mode="wavelet")
    assert settings.threshold is None
    with pytest.raises(AttributeError):
        settings.mode = "intensity"  # type: ignore[misc]
