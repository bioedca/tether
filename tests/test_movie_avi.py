# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The AVI movie adapter (#906, ADR-0154): values, row order, metadata, refusals.

The adapter conformance module runs every interface test on a synthetic 64-frame movie of
``tests/_avi_synth.py``. This module adds what is specific to AVI:

* three committed movies, two written by FFmpeg (``Y800``, and a top-down 8-bit DIB) and one
  bottom-up DIB of ``tests/_avi_synth.py``: their frames equal, bit for bit, the reference
  TIFF of each file as FFmpeg 6.1.1 decodes it (``scripts/make_avi_fixtures.py``);
* four committed FFmpeg files that Tether refuses: FFV1, Motion JPEG, an RGB 3-3-2 color
  table and 24-bit color;
* the synthetic writer's variants: OpenDML extensions, ``rec`` lists, another stream, and
  each refusal of a damaged or incomplete file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from _avi_synth import GREY_TABLE, avi_bytes, write_avi  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    InvalidPixelError,
    LossyCodecError,
    UnsupportedContainerError,
    open_movie_source,
    select_adapter,
)
from tether.io.avi import AviMovie  # noqa: E402

AVI = Path(__file__).resolve().parent / "fixtures" / "avi"
READABLE = ("gray_y800_37x23x8", "gray_dib_top_down_37x23x8", "gray_dib_bottom_up_37x23x8")


def _frames(n: int = 3, h: int = 5, w: int = 7, seed: int = 906) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, size=(n, h, w), dtype="u1")


# --- the committed files --------------------------------------------------------------------


@pytest.mark.parametrize("name", READABLE)
def test_a_committed_movie_equals_its_ffmpeg_reference_bit_for_bit(name: str) -> None:
    import tifffile

    reference = tifffile.imread(AVI / f"{name}_reference.tif")
    with open_movie_source(AVI / f"{name}.avi") as source:
        assert isinstance(source, AviMovie)
        assert source.pixel_type == "|u1"
        assert source.shape == (8, 23, 37)
        got = source.read_frames(0, 8)
    assert reference.dtype == np.dtype("u1")
    assert got.tobytes() == reference.tobytes()


@pytest.mark.parametrize(
    ("name", "error", "match"),
    [
        ("ffv1", UnsupportedContainerError, "FFV1, a lossless codec that Tether does not decode"),
        ("mjpeg", LossyCodecError, r"Motion JPEG \(MJPG\), which can be lossy"),
        ("pal8_rgb332", InvalidPixelError, "not the gray ramp"),
        ("bgr24", InvalidPixelError, "24-bit color value"),
    ],
)
def test_a_committed_ffmpeg_file_is_refused_by_name(
    name: str, error: type[Exception], match: str
) -> None:
    path = AVI / f"{name}.avi"
    assert select_adapter(path).name == "AVI"
    with pytest.raises(error, match=match):
        open_movie_source(path)


def test_every_metadata_value_is_unknown() -> None:
    """An AVI's frame rate is a playback rate, not the acquisition interval (ADR-0154)."""
    with open_movie_source(AVI / "gray_y800_37x23x8.avi") as source:
        meta = source.metadata
    for name in ("frame_time", "pixel_size", "channels"):
        assert getattr(meta, name).origin == "unknown", name


# --- the synthetic variants that Tether reads ------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"bottom_up": True}, id="dib-bottom-up"),
        pytest.param({"bottom_up": False}, id="dib-top-down"),
        pytest.param({"fourcc": b"Y800"}, id="y800"),
        pytest.param({"fourcc": b"GREY"}, id="gray"),
        pytest.param({"fourcc": b"Y8  "}, id="y8"),
        pytest.param({"chunk_id": b"00dc"}, id="dc-chunks"),
        pytest.param({"rec": True}, id="rec-lists"),
        pytest.param({"per_riff": 2}, id="opendml-avix"),
        pytest.param({"streams": (b"auds",)}, id="with-an-audio-stream"),
        pytest.param({"extra": (b"ix00" + b"\x04\x00\x00\x00" + b"\x00" * 4,)}, id="odml-index"),
    ],
)
def test_a_readable_variant_gives_its_frames(tmp_path: Path, kwargs: dict[str, object]) -> None:
    frames = _frames(n=5)
    path = write_avi(tmp_path / "movie.avi", frames, **kwargs)
    with open_movie_source(path) as source:
        assert source.shape == frames.shape
        np.testing.assert_array_equal(source.read_frames(0, 5), frames)
        np.testing.assert_array_equal(source.read_frames(3, 5), frames[3:5])


def test_an_odd_width_frame_is_padded_in_a_dib_and_not_in_y800(tmp_path: Path) -> None:
    frames = _frames(w=9)
    dib = avi_bytes(frames)
    y800 = avi_bytes(frames, fourcc=b"Y800")
    # A frame chunk of the DIB holds 5 rows of 12 bytes, one of Y800 5 rows of 9.
    for data, size in ((dib, 60), (y800, 45)):
        at = data.index(b"00db", data.index(b"movi"))
        assert int.from_bytes(data[at + 4 : at + 8], "little") == size
    for name, data in (("dib.avi", dib), ("y800.avi", y800)):
        (tmp_path / name).write_bytes(data)
        with open_movie_source(tmp_path / name) as source:
            np.testing.assert_array_equal(source.read_frames(0, 3), frames)


# --- the refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"compression": b"H264"}, LossyCodecError, r"H\.264 \(H264\), which can be lossy"),
        ({"compression": b"xvid"}, LossyCodecError, "Xvid"),
        ({"compression": b"HFYU"}, UnsupportedContainerError, "HuffYUV"),
        ({"compression": b"QQQQ"}, UnsupportedContainerError, "QQQQ, which Tether does not know"),
        ({"compression": b"\x00\x01\x02\x03"}, UnsupportedContainerError, "00010203"),
        ({"compression": 1}, UnsupportedContainerError, "run-length"),
        ({"compression": 3, "bits": 16}, InvalidPixelError, "16-bit color"),
        ({"bits": 16}, InvalidPixelError, "16-bit color"),
        ({"bits": 32}, InvalidPixelError, "32-bit color"),
        ({"bits": 4}, UnsupportedContainerError, "4-bit pixels"),
        ({"fourcc": b"Y800", "bits": 16}, UnsupportedContainerError, "16-bit pixels as Y800"),
        ({"table": GREY_TABLE[::-1]}, InvalidPixelError, "not the gray ramp"),
        ({"table": GREY_TABLE[: 4 * 128]}, InvalidPixelError, "not the gray ramp"),
        ({"table": b""}, InvalidPixelError, "not the gray ramp"),
        (
            {"length": 4},
            UnsupportedContainerError,
            "holds 3 frames and its stream header declares 4",
        ),
        (
            {"length": 2},
            UnsupportedContainerError,
            "holds 3 frames and its stream header declares 2",
        ),
        ({"sizes": {1: 0}}, UnsupportedContainerError, "no bytes for frame 1"),
        ({"sizes": {2: 17}}, UnsupportedContainerError, "stores frame 2 in 17 bytes"),
        ({"streams": (b"vids",)}, UnsupportedContainerError, "2 video streams"),
        ({"flags": 0x10 | 0x20}, UnsupportedContainerError, "sets AVIF_MUSTUSEINDEX"),
        ({"table": b"\x01\x01\x01"}, InvalidPixelError, "not the gray ramp"),
        (
            {"extra": (b"00pc" + b"\x04\x00\x00\x00" + b"\x00" * 4,)},
            UnsupportedContainerError,
            "changes its color table",
        ),
    ],
)
def test_a_file_that_cannot_be_read_whole_is_refused(
    tmp_path: Path, kwargs: dict[str, object], error: type[Exception], match: str
) -> None:
    path = write_avi(tmp_path / "movie.avi", _frames(), **kwargs)
    with pytest.raises(error, match=match):
        open_movie_source(path)


@pytest.mark.parametrize("cut", [1, 7, 100, 200])
def test_an_incomplete_copy_is_refused(tmp_path: Path, cut: int) -> None:
    data = avi_bytes(_frames(n=6))
    path = tmp_path / "movie.avi"
    path.write_bytes(data[:-cut])
    with pytest.raises(UnsupportedContainerError, match="incomplete copy|holds .* frames"):
        open_movie_source(path)


@pytest.mark.parametrize(
    ("tail", "match"),
    [(b"JUNK\x04\x00\x00\x00abcd", "chunk JUNK"), (b"\x00" * 5, "inside a chunk header")],
)
def test_bytes_after_the_riff_are_refused(tmp_path: Path, tail: bytes, match: str) -> None:
    path = tmp_path / "movie.avi"
    path.write_bytes(avi_bytes(_frames()) + tail)
    with pytest.raises(UnsupportedContainerError, match=match):
        open_movie_source(path)


def test_a_file_cut_short_after_it_opened_is_refused_on_read(tmp_path: Path) -> None:
    data = avi_bytes(_frames(n=4))
    path = tmp_path / "movie.avi"
    path.write_bytes(data)
    with open_movie_source(path) as source:
        np.testing.assert_array_equal(source.read_frames(0, 1), _frames(n=4)[:1])
        with path.open("r+b") as fh:
            fh.truncate(len(data) // 2)
        with pytest.raises(UnsupportedContainerError, match="ended inside frame"):
            source.read_frames(0, 4)


def test_a_riff_that_is_not_avi_is_not_chosen(tmp_path: Path) -> None:
    path = tmp_path / "sound.avi"
    path.write_bytes(b"RIFF\x04\x00\x00\x00WAVE")
    with pytest.raises(UnsupportedContainerError, match="the name says AVI"):
        select_adapter(path)


def test_repr_names_the_file_and_geometry(tmp_path: Path) -> None:
    path = write_avi(tmp_path / "movie.avi", _frames())
    with AviMovie(path) as source:
        assert repr(source) == "AviMovie('movie.avi', frames=3, height=5, width=7, dtype='|u1')"


def test_a_rec_list_inside_a_rec_list_is_refused(tmp_path: Path) -> None:
    """CodeRabbit on #1457: a nested rec list is refused with a reason, not a RecursionError."""
    import struct

    data = avi_bytes(_frames(n=1))
    frame_at = data.index(b"00db")
    frame = data[frame_at : frame_at + 8 + 5 * 8]  # 5 rows of 8 bytes (7 padded to 8)

    def _rec(body: bytes) -> bytes:
        return b"LIST" + struct.pack("<I", 4 + len(body)) + b"rec " + body

    nested = _rec(_rec(frame))
    movi_at = data.index(b"movi") - 8
    (movi_size,) = struct.unpack("<I", data[movi_at + 4 : movi_at + 8])
    movi = b"LIST" + struct.pack("<I", 4 + len(nested)) + b"movi" + nested
    rebuilt = data[:movi_at] + movi + data[movi_at + 8 + movi_size :]
    riff = b"RIFF" + struct.pack("<I", len(rebuilt) - 8) + rebuilt[8:]
    path = tmp_path / "movie.avi"
    path.write_bytes(riff)
    with pytest.raises(UnsupportedContainerError, match="rec list inside another rec list"):
        open_movie_source(path)
