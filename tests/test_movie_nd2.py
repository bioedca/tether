# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's own ND2 reader (#902, ADR-0148): public files, metadata origins, refusals.

The conformance module runs the shared tests on a synthetic ND2 time series. This
module adds what only ND2 has: a real public file read bit for bit against a
Bio-Formats TIFF export, its metadata with origins, a real multi-dimensional file
that must be refused, and one synthetic file for each other variant the reader
refuses. ``tests/fixtures/PROVENANCE.md`` records the files and the reference.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from _nd2_synth import encode, write_nd2
from tether.io.adapters import (
    InvalidPixelError,
    LossyCodecError,
    UnsupportedContainerError,
    open_movie_source,
    select_adapter,
)
from tether.io.movie import open_movie
from tether.io.nd2 import Nd2Movie, decode_metadata

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nd2"
REFERENCE = json.loads((FIXTURES / "reference.json").read_text(encoding="utf-8"))["files"]
TIRF_FRAME = FIXTURES / "221012-Exp-1-A1-1-BF.nd2"
Z_AND_C = FIXTURES / "ZC.nd2"


def _frames(n: int = 6, h: int = 8, w: int = 10, dtype: str = "<u2") -> np.ndarray:
    return np.random.default_rng(902).integers(0, 4000, size=(n, h, w)).astype(dtype)


# --- the public files -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_fixture_files_are_the_recorded_bytes(name: str) -> None:
    data = (FIXTURES / name).read_bytes()
    assert len(data) == REFERENCE[name]["size_bytes"]
    assert hashlib.sha256(data).hexdigest() == REFERENCE[name]["sha256"]


def test_public_frame_equals_the_bio_formats_export_bit_for_bit() -> None:
    ref = REFERENCE[TIRF_FRAME.name]
    with open_movie_source(TIRF_FRAME) as source:
        assert source.shape == tuple(ref["shape"])
        assert source.pixel_type == ref["pixel_type"]
        pixels = source.read_frames(0, source.n_frames)
    digest = hashlib.sha256(np.ascontiguousarray(pixels, dtype="<u2").tobytes()).hexdigest()
    assert digest == ref["pixels_sha256"], "the pixels differ from the Bio-Formats 7.3.1 export"


def test_public_frame_metadata_and_origins() -> None:
    expected = REFERENCE[TIRF_FRAME.name]["metadata"]
    with open_movie_source(TIRF_FRAME) as source:
        meta = source.metadata
    for field in ("frame_time", "pixel_size", "channels"):
        got = getattr(meta, field)
        want = expected[field]
        value = list(got.value) if isinstance(got.value, tuple) else got.value
        assert (value, got.origin) == (want["value"], want["origin"]), field


def test_public_z_and_channel_stack_is_refused_not_read_in_part() -> None:
    assert select_adapter(Z_AND_C).name == "ND2"
    with pytest.raises(UnsupportedContainerError, match="Z planes"):
        open_movie_source(Z_AND_C)


# --- a time series (synthetic, checked against two other readers) -------------------


def test_frame_time_comes_from_the_acquisition_times(tmp_path: Path) -> None:
    path = write_nd2(tmp_path / "t.nd2", _frames(), frame_ms=37.5, pixel_um=0.108)
    with Nd2Movie(path) as movie:
        meta = movie.metadata
    assert meta.frame_time.origin == "file"
    assert meta.frame_time.value == pytest.approx(0.0375)
    assert (meta.pixel_size.value, meta.pixel_size.origin) == (0.108, "file")
    assert (meta.channels.value, meta.channels.origin) == (("Cy3",), "file")


def test_uncalibrated_pixel_size_is_unknown(tmp_path: Path) -> None:
    path = write_nd2(tmp_path / "t.nd2", _frames(), pixel_um=None)
    with Nd2Movie(path) as movie:
        assert movie.metadata.pixel_size.origin == "unknown"


@pytest.mark.parametrize("dtype", ["<u1", "<u2", "<f4"])
def test_pixel_types(tmp_path: Path, dtype: str) -> None:
    frames = _frames(dtype=dtype) if dtype != "<f4" else _frames().astype("<f4") / 7
    path = write_nd2(tmp_path / "t.nd2", frames)
    with Nd2Movie(path) as movie:
        assert movie.pixel_type == np.dtype(dtype).str
        np.testing.assert_array_equal(movie.read_frames(0, movie.n_frames), frames)


def test_a_frame_range_read_touches_only_those_frames(tmp_path: Path) -> None:
    frames = _frames(n=40, h=64, w=64)
    path = write_nd2(tmp_path / "t.nd2", frames)
    with Nd2Movie(path) as movie:
        tracemalloc.start()
        try:
            got = movie.read_frames(30, 31)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    np.testing.assert_array_equal(got, frames[30:31])
    assert peak < 4 * frames[0].nbytes


# --- refusals -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"components": 2}, InvalidPixelError, "2 components"),
        ({"compression": 0}, UnsupportedContainerError, "compressed"),
        ({"compression": 1}, LossyCodecError, "lossy"),
        ({"loops": [(1, 3), (2, 2)]}, UnsupportedContainerError, "XY positions"),
        ({"loops": [(4, 6)]}, UnsupportedContainerError, "Z planes"),
        ({"row_padding": 4}, UnsupportedContainerError, "padded"),
        ({"version": b"Ver2.0"}, UnsupportedContainerError, "format Ver2.0"),
        ({"drop_frame": 3}, UnsupportedContainerError, "frame 3 of 6 is missing"),
        ({"bits": 12}, InvalidPixelError, "12-bit"),
    ],
)
def test_variants_that_cannot_be_read_fully_are_refused(
    tmp_path: Path, kwargs: dict[str, object], error: type[Exception], match: str
) -> None:
    path = write_nd2(tmp_path / "v.nd2", _frames(), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(error, match=match):
        Nd2Movie(path)


def test_the_first_nd2_format_is_refused_as_jpeg_2000(tmp_path: Path) -> None:
    path = tmp_path / "old.nd2"
    path.write_bytes(b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"\x00" * 256)
    assert select_adapter(path).name == "ND2"
    with pytest.raises(LossyCodecError, match="JPEG 2000"):
        open_movie_source(path)


def test_a_cut_file_is_refused(tmp_path: Path) -> None:
    path = write_nd2(tmp_path / "t.nd2", _frames())
    data = path.read_bytes()
    path.write_bytes(data[: len(data) - 4096])
    with pytest.raises(UnsupportedContainerError, match="incomplete copy"):
        Nd2Movie(path)


def test_a_renamed_nd2_is_found_by_content(tmp_path: Path) -> None:
    path = write_nd2(tmp_path / "movie.dat", _frames())
    assert select_adapter(path).name == "ND2"


def test_the_tiff_only_opener_refuses_nd2_with_a_message(tmp_path: Path) -> None:
    """``open_movie`` gives the TIFF memory map; an ND2 is refused there (#1388 reads it)."""
    path = write_nd2(tmp_path / "t.nd2", _frames())
    with pytest.raises(UnsupportedContainerError, match="reads only TIFF"):
        open_movie(path)


def test_a_refusal_closes_the_file(tmp_path: Path) -> None:
    """The file handle is closed when the open is refused (no leak on Windows)."""
    path = write_nd2(tmp_path / "v.nd2", _frames(), components=2)
    with pytest.raises(InvalidPixelError):
        Nd2Movie(path)
    path.unlink()  # a handle left open would make this fail on Windows


# --- the metadata decoder -------------------------------------------------------------


def test_decoder_reads_every_value_type() -> None:
    tree = {
        "flag": True,
        "count": 7,
        "big": 2**40,
        "ratio": 0.25,
        "text": "Cy5 µm",
        "blob": b"\x01\x02",
        "items": [1, 2, 3],
        "child": {"name": "x"},
    }
    assert decode_metadata(encode("root", tree)) == {"root": tree}


def test_decoder_inflates_a_compressed_chunk() -> None:
    import zlib

    raw = encode("root", {"a": 1})
    data = bytes([76, 0]) + b"\x00" * 10 + zlib.compress(raw)
    assert decode_metadata(data) == {"root": {"a": 1}}


def test_decoder_refuses_an_unknown_type() -> None:
    with pytest.raises(UnsupportedContainerError, match="type 10"):
        decode_metadata(bytes([10, 1]) + b"\x00\x00" + struct.pack("<I", 0))


def test_reading_nd2_imports_no_reader_package(tmp_path: Path) -> None:
    path = write_nd2(tmp_path / "t.nd2", _frames())
    code = (
        "import sys\n"
        "from tether.io.adapters import open_movie_source\n"
        f"open_movie_source({str(path)!r}).read_frames(0, 1)\n"
        "banned = ('nd2', 'dask', 'PySide6', 'napari')\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in banned]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
