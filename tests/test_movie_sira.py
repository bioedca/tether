# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The SIRA movie adapter (#905, ADR-0152): values, row order, offsets, metadata, refusals.

The adapter conformance module runs every interface test on a synthetic 64-frame movie of
``tests/_sira_synth.py``. This module adds what is specific to SIRA:

* a committed extract of a test movie of MASH-FRET (GPL-3.0): its frames equal, bit for
  bit, the reference TIFF of the same file as MASH-FRET's own ``readSira.m`` reads it;
* a committed movie that MASH-FRET's own ``writeSiraFile.m`` wrote from frames with
  negative values, so that it stores offsets: Tether gives back the frames it was given;
* the synthetic writer's variants: each refusal, a frame that is not square;
* two whole MASH-FRET test movies: their frames equal, bit for bit, the digests of
  ``readSira.m``. These tests download the files, so they run in the weekly
  reference-data job (``reference_download``) and skip elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from _sira_synth import HEADER, sira_bytes, write_sira  # noqa: E402
from reference_data.fetch import path_for  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    UnsupportedContainerError,
    open_movie_source,
    select_adapter,
)
from tether.io.sira import SiraMovie  # noqa: E402

SIRA = Path(__file__).resolve().parent / "fixtures" / "sira"


def _frames(n: int = 3, h: int = 4, w: int = 7, seed: int = 905) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(-500, 16000, size=(n, h, w)).astype("<f4")


# --- the committed MASH-FRET files ----------------------------------------------------------


def test_the_mash_fret_extract_equals_its_reference_tiff_bit_for_bit() -> None:
    import tifffile

    reference = tifffile.imread(SIRA / "1chan1exc_16_reference.tif")
    with open_movie_source(SIRA / "1chan1exc_16.sira") as source:
        assert source.pixel_type == "<f4"
        assert source.shape == (16, 64, 64)
        got = source.read_frames(0, 16)
        meta = source.metadata
    assert reference.dtype == np.dtype("<f4")
    assert got.view("<u4").tobytes() == reference.view("<u4").tobytes()
    assert meta.frame_time.value == pytest.approx(0.1) and meta.frame_time.origin == "file"
    assert {meta.pixel_size.origin, meta.channels.origin} == {"unknown"}


def test_the_offsets_that_mash_fret_wrote_are_taken_off() -> None:
    """``writeSiraFile.m`` stored three frames in halves, two with negative values."""
    base = np.arange(20, dtype="<f4").reshape(4, 5)
    written = np.stack([(base - 7) / 2, (base - 9) / 2, (base + 10) / 2])
    with SiraMovie(SIRA / "mash_offsets.sira") as movie:
        assert movie.version == "tether-905"
        np.testing.assert_array_equal(movie.read_frames(0, 3), written)
        np.testing.assert_array_equal(movie.read_frames(1, 2), written[1:2])


# --- the synthetic writer -------------------------------------------------------------------


def test_a_frame_that_is_not_square_keeps_its_rows_and_columns(tmp_path: Path) -> None:
    frames = _frames()
    with SiraMovie(write_sira(tmp_path / "m.sira", frames)) as movie:
        assert movie.shape == (3, 4, 7)
        np.testing.assert_array_equal(movie.read_frames(0, 3), frames)


def test_the_frame_time_is_read_and_one_frame_has_none(tmp_path: Path) -> None:
    with SiraMovie(write_sira(tmp_path / "m.sira", _frames(), frame_time=0.033)) as movie:
        assert movie.metadata.frame_time.value == pytest.approx(0.033)
    with SiraMovie(write_sira(tmp_path / "one.sira", _frames(n=1))) as movie:
        assert movie.metadata.frame_time.origin == "unknown"


@pytest.mark.parametrize("frame_time", [0.0, -1.0, float("nan"), float("inf")])
def test_a_frame_time_that_is_not_finite_and_positive_is_unknown(
    tmp_path: Path, frame_time: float
) -> None:
    path = write_sira(tmp_path / "m.sira", _frames(), frame_time=frame_time)
    with SiraMovie(path) as movie:
        assert movie.metadata.frame_time.origin == "unknown"


@pytest.mark.parametrize("version", ["1.003.41", "1.1.2 (prev. commit: 352a251)", " dummy"])
def test_the_versions_of_the_current_layout_are_read(tmp_path: Path, version: str) -> None:
    line = b"MASH-FRET exported binary graphic Version: " + version.encode()
    with SiraMovie(write_sira(tmp_path / "m.sira", _frames(), line=line)) as movie:
        assert movie.version == version.strip()


def test_the_registry_chooses_sira_by_its_header(tmp_path: Path) -> None:
    path = tmp_path / "movie.dat"
    path.write_bytes(sira_bytes(_frames()))
    assert select_adapter(path).name == "SIRA"
    named = tmp_path / "movie.sira"
    named.write_bytes(b"not a SIRA file at all")
    with pytest.raises(UnsupportedContainerError, match="the name says SIRA"):
        select_adapter(named)


def test_extraction_does_not_read_sira_yet(tmp_path: Path) -> None:
    from tether.io.frames import open_extraction_movie
    from tether.io.movie import EXTRACTED_CONTAINERS

    assert "SIRA" not in EXTRACTED_CONTAINERS
    path = write_sira(tmp_path / "m.sira", _frames())
    with (
        pytest.raises(UnsupportedContainerError, match="extraction does not read SIRA yet"),
        open_extraction_movie(path),
    ):
        pass


# --- refusals ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b"SIRA exported binary graphic", "older header 'SIRA exported binary graphic'"),
        (b"MASH smFRET exported binary graphic Version: 1.003.45", "older header 'MASH smFRET"),
        (HEADER.replace(b"1.3.6", b"1.003"), "version 1.003,"),
        (HEADER.replace(b"1.3.6", b"1.003.40"), "version 1.003.40,"),
    ],
)
def test_an_older_layout_is_refused(tmp_path: Path, line: bytes, message: str) -> None:
    path = write_sira(tmp_path / "m.sira", _frames(), line=line)
    with pytest.raises(UnsupportedContainerError, match=message):
        SiraMovie(path)


@pytest.mark.parametrize(
    "dims", [(7, 4, 0), (7, 2.5, 3), (float("nan"), 4, 3), (-7, 4, 3), (7, 4, 2.0**25)]
)
def test_a_damaged_header_is_refused(tmp_path: Path, dims: tuple[float, float, float]) -> None:
    path = write_sira(tmp_path / "m.sira", _frames(), dims=dims)
    with pytest.raises(UnsupportedContainerError, match="damaged header"):
        SiraMovie(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda b: b[:-1], "shorter than the 3 frames"),
        (lambda b: b + b"\0\0\0\0", "4 bytes after its last frame"),
        (lambda b: b[:60], "header cut short"),
        (lambda b: HEADER, "header line with no end"),
        (lambda b: b"MASH-FRET exported something else\r" + b[50:], "the MASH-FRET header"),
    ],
)
def test_a_file_that_is_not_one_whole_movie_is_refused(tmp_path: Path, change, message) -> None:
    path = tmp_path / "m.sira"
    path.write_bytes(change(sira_bytes(_frames())))
    with pytest.raises(UnsupportedContainerError, match=message):
        SiraMovie(path)


@pytest.mark.parametrize("offset", [-1.0, float("nan"), float("inf")])
def test_an_offset_that_is_not_finite_and_positive_is_refused(tmp_path: Path, offset) -> None:
    frames = _frames()
    data = bytearray(sira_bytes(frames))
    data[-4:] = np.float32(offset).tobytes()
    path = tmp_path / "m.sira"
    path.write_bytes(bytes(data))
    with pytest.raises(UnsupportedContainerError, match="in frame 2, not finite and 0 or more"):
        SiraMovie(path)


def test_a_file_that_shrinks_after_it_opened_is_refused_not_read_in_part(tmp_path: Path) -> None:
    path = write_sira(tmp_path / "m.sira", _frames())
    with SiraMovie(path) as movie:
        path.write_bytes(path.read_bytes()[:-40])
        with pytest.raises(UnsupportedContainerError, match="ended inside frame 2"):
            movie.read_frames(0, 3)


def test_the_repr_names_the_file_and_its_shape(tmp_path: Path) -> None:
    with SiraMovie(write_sira(tmp_path / "m.sira", _frames())) as movie:
        assert repr(movie) == "SiraMovie('m.sira', frames=3, height=4, width=7, dtype='<f4')"


# --- the whole MASH-FRET test movies (weekly reference-data job) ----------------------------


@pytest.mark.reference_download
@pytest.mark.parametrize(
    ("dataset", "name"),
    [("mash-fret-1chan1exc-sira", "1chan1exc.sira"), ("mash-fret-2020-sira", "2020.sira")],
)
def test_a_mash_fret_test_movie_equals_its_reference_bit_for_bit(dataset: str, name: str) -> None:
    ref = json.loads((SIRA / "reference.json").read_text("utf-8"))[name]
    with open_movie_source(path_for(dataset)) as source:
        got = source.read_frames(0, source.n_frames)
        assert source.metadata.frame_time.value == pytest.approx(ref["frame_time"])
    assert list(got.shape) == ref["shape"]
    assert hashlib.sha256(got.tobytes()).hexdigest() == ref["pixel_sha256"]
