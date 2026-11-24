# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The CZI movie adapter (#903, ADR-0144): values, metadata, refusals, lazy import.

The adapter conformance module runs every interface test on ``movie_zstd_64x64x64.czi``.
This module adds what is specific to CZI:

* the committed files written by ZEISS libCZI (``scripts/make_czi_fixtures.py``): an
  uncompressed movie, and one file for each refusal;
* a JPEG / JPEG XR / LZW case, made here by changing the codec field of each directory entry
  of the uncompressed movie, since libCZI 6.1.0 writes none of them;
* the public ZEN movie of Zenodo 7015307 (CC-BY-4.0), compared bit for bit with its reference
  TIFF export by ZEISS pylibCZIrw 6.1.0. These two tests download the file, so they run in the
  weekly reference-data job (``reference_download``) and skip elsewhere.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import threading
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

pytest.importorskip("czifile")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from reference_data.fetch import path_for  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    InvalidPixelError,
    LossyCodecError,
    UnsupportedContainerError,
    open_movie_source,
    select_adapter,
)
from tether.io.czi import CziMovie, _read_metadata  # noqa: E402

CZI = Path(__file__).resolve().parent / "fixtures" / "czi"


def _frames(seed: int, n: int, size: int) -> np.ndarray:
    """The seeded frames of ``scripts/make_czi_fixtures.py``."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 4000, size=(n, size, size)).astype("<u2")


# --- the committed movies -----------------------------------------------------------------


def test_uncompressed_movie_values_and_metadata() -> None:
    with open_movie_source(CZI / "movie_raw_16x16x4.czi") as source:
        assert source.shape == (4, 16, 16)
        assert source.pixel_type == "<u2"
        np.testing.assert_array_equal(source.read_frames(0, 4), _frames(903, 4, 16))
        meta = source.metadata
    assert (meta.pixel_size.value, meta.pixel_size.origin) == (0.1, "file")
    assert (meta.channels.value, meta.channels.origin) == (("Cy5",), "file")
    # libCZI writes no time increment, and Tether does not invent one.
    assert meta.frame_time.origin == "unknown"


def test_zstd_movie_metadata() -> None:
    with open_movie_source(CZI / "movie_zstd_64x64x64.czi") as source:
        meta = source.metadata
    assert meta.pixel_size.value == pytest.approx(0.16, rel=1e-12)
    assert meta.channels.value == ("Cy3",)


def test_concurrent_reads_return_their_own_frames() -> None:
    frames = _frames(901, 64, 64)
    errors: list[str] = []
    with open_movie_source(CZI / "movie_zstd_64x64x64.czi") as source:

        def _read(start: int) -> None:
            for _ in range(5):
                got = source.read_frames(start, start + 4)
                if not np.array_equal(got, frames[start : start + 4]):
                    errors.append(f"frames {start} to {start + 3}")

        threads = [threading.Thread(target=_read, args=(s,)) for s in range(0, 64, 8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    assert errors == []


def test_importing_tether_io_does_not_import_czifile() -> None:
    """ADR-0113 rule 6: only the adapter's open imports the reader package."""
    code = (
        "import sys\n"
        "import tether.io\n"
        "tether.io.registered_adapters()\n"
        "assert 'czifile' not in sys.modules\n"
        "assert 'xarray' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# --- refusals -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "error", "match"),
    [
        ("two_channels.czi", UnsupportedContainerError, "holds 2 channels"),
        ("two_scenes.czi", UnsupportedContainerError, "holds 2 scenes"),
        ("z_stack.czi", UnsupportedContainerError, "holds 2 Z planes"),
        ("two_tiles.czi", UnsupportedContainerError, "holds 2 mosaic tiles"),
        ("gap_in_time.czi", UnsupportedContainerError, "missing or repeated"),
        ("rgb.czi", InvalidPixelError, "BGR24 is not one real intensity sample"),
    ],
)
def test_a_file_that_is_not_one_movie_is_refused(
    name: str, error: type[Exception], match: str
) -> None:
    assert select_adapter(CZI / name).name == "CZI"
    with pytest.raises(error, match=match):
        open_movie_source(CZI / name)


def _with_codec(tmp_path: Path, codec: int) -> Path:
    """A copy of the uncompressed movie whose directory says every frame uses ``codec``."""
    import czifile

    path = tmp_path / "movie.czi"
    shutil.copyfile(CZI / "movie_raw_16x16x4.czi", path)
    with czifile.CziFile(path) as czi:
        offsets = [e.offset for e in czi.subblock_directory]
    data = bytearray(path.read_bytes())
    for offset in offsets:
        # DV entry: schema (2 B), pixel type (4 B), file position (8 B), file part (4 B),
        # then the compression (int32).
        struct.pack_into("<i", data, offset + 18, codec)
    path.write_bytes(bytes(data))
    return path


@pytest.mark.parametrize(("codec", "name"), [(1, "JPEG"), (4, "JPEG XR")])
def test_a_codec_that_can_be_lossy_is_refused_by_name(
    tmp_path: Path, codec: int, name: str
) -> None:
    with pytest.raises(LossyCodecError, match=f"use {name} compression"):
        open_movie_source(_with_codec(tmp_path, codec))


def test_a_codec_that_tether_does_not_read_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedContainerError, match="the codec LZW"):
        open_movie_source(_with_codec(tmp_path, 2))


def test_a_damaged_file_is_refused_not_read_in_part(tmp_path: Path) -> None:
    path = tmp_path / "movie.czi"
    path.write_bytes((CZI / "movie_raw_16x16x4.czi").read_bytes()[:600])
    with pytest.raises(UnsupportedContainerError, match="CZI file only as one movie"):
        open_movie_source(path)


def test_a_file_czifile_cannot_parse_is_refused(tmp_path: Path, monkeypatch) -> None:
    import czifile

    def broken(path: Path) -> None:
        raise ValueError("not a CZI segment")

    monkeypatch.setattr(czifile, "CziFile", broken)
    with pytest.raises(UnsupportedContainerError, match="cannot be parsed"):
        CziMovie(CZI / "movie_raw_16x16x4.czi")


# --- the structure rules, on directory entries that libCZI does not write -----------------

_Pixel = namedtuple("_Pixel", "name dtype samples")
_GRAY16 = _Pixel("GRAY16", "<u2", 1)


def _entry(t: int = 0, **changes: object) -> SimpleNamespace:
    """One directory entry of a 16 x 16 Gray16 movie frame at time point ``t``."""
    fields: dict[str, object] = {
        "dims": ("T", "C", "Y", "X", "S"),
        "start": (t, 0, 0, 0, 0),
        "shape": (1, 1, 16, 16, 1),
        "pyramid_type": 0,
        "is_pyramid": False,
        "scene_index": 0,
        "mosaic_index": 0,
        "file_part": 0,
        "compression": SimpleNamespace(name="UNCOMPRESSED"),
        "pixel_type": _GRAY16,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def _header(**changes: object) -> SimpleNamespace:
    fields: dict[str, object] = {"file_part": 0, "primary_file_guid": "a", "file_guid": "a"}
    fields.update(changes)
    return SimpleNamespace(**fields)


def _check(header: SimpleNamespace, entries: list[SimpleNamespace]) -> tuple:
    movie = object.__new__(CziMovie)
    movie._path = Path("movie.czi")
    return movie._check_structure(header, entries)


@pytest.mark.parametrize(
    ("header", "entries", "message"),
    [
        (_header(file_part=1), [_entry()], "one part of a multi-file acquisition"),
        (_header(primary_file_guid="b"), [_entry()], "one part of a multi-file acquisition"),
        (_header(), [], "holds no image subblock"),
        (
            _header(),
            [_entry(0), _entry(1, file_part=1)],
            "frames in another part of a multi-file acquisition",
        ),
        (_header(), [_entry(pyramid_type=1)], "image pyramid"),
        (_header(), [_entry(is_pyramid=True)], "image pyramid"),
        (
            _header(),
            [_entry(0), _entry(1, pixel_type=_Pixel("GRAY8", "u1", 1))],
            "mixes pixel types",
        ),
        (
            _header(),
            [_entry(0), _entry(1, dims=("T", "Y", "X"), start=(1, 0, 0), shape=(1, 16, 16))],
            "different dimensions",
        ),
        (
            _header(),
            [_entry(0, shape=(1, 2, 16, 16, 1))],
            "stores more than one of its channels in a subblock",
        ),
        (
            _header(),
            [
                _entry(0, dims=("T", "Q", "Y", "X"), start=(0, 0, 0, 0), shape=(1, 1, 16, 16)),
                _entry(1, dims=("T", "Q", "Y", "X"), start=(1, 1, 0, 0), shape=(1, 1, 16, 16)),
            ],
            "holds 2 values of the dimension Q",
        ),
        (
            _header(),
            [_entry(0), _entry(1, start=(1, 0, 8, 0, 0))],
            "frames of different size or position",
        ),
        (_header(), [_entry(0, shape=(2, 1, 16, 16, 1))], "more than one time point in a subblock"),
        (
            _header(),
            [
                _entry(dims=("Y", "X"), start=(0, 0), shape=(16, 16)),
                _entry(dims=("Y", "X"), start=(0, 0), shape=(16, 16)),
            ],
            "no time dimension but more than one subblock",
        ),
    ],
)
def test_a_directory_that_is_not_one_movie_is_refused(header, entries, message: str) -> None:
    with pytest.raises(UnsupportedContainerError, match=message):
        _check(header, entries)


def test_a_pixel_type_that_is_not_one_real_sample_is_refused() -> None:
    with pytest.raises(InvalidPixelError, match="UNKNOWN is not one real intensity sample"):
        _check(_header(), [_entry(pixel_type=_Pixel("UNKNOWN", "u1", 1))])


def test_a_single_frame_with_no_time_dimension_is_a_one_frame_movie() -> None:
    entry = _entry(dims=("Y", "X"), start=(0, 0), shape=(16, 16))
    assert _check(_header(), [entry]) == (entry,)


def test_frames_are_ordered_by_time_point_not_by_directory_order() -> None:
    entries = [_entry(2), _entry(0), _entry(1)]
    assert [e.start[0] for e in _check(_header(), entries)] == [0, 1, 2]


def _serving(segment: object) -> CziMovie:
    """A 1-frame 16 x 16 movie whose one subblock reads back as ``segment``."""
    movie = CziMovie(CZI / "movie_raw_16x16x4.czi")
    movie._entries = (SimpleNamespace(read_segment_data=lambda czi: segment),)
    movie._shape = (1, 16, 16)
    return movie


def test_a_deleted_subblock_is_refused_not_served() -> None:
    with _serving(object()) as movie, pytest.raises(ValueError, match="deleted subblock"):
        movie.read_frames(0, 1)


def test_a_subblock_of_the_wrong_size_is_refused_not_served() -> None:

    segment = _segment(np.zeros(8, dtype="<u2"), mask=None)
    with _serving(segment) as movie, pytest.raises(ValueError, match="decodes to 8 pixels"):
        movie.read_frames(0, 1)


def _segment(tile: np.ndarray, mask: np.ndarray | None) -> object:
    """A live subblock that decodes to ``tile`` and carries the validity ``mask``."""
    import czifile

    class _Segment(czifile.CziSubBlockSegmentData):
        __slots__ = ()

        def data(self) -> np.ndarray:  # type: ignore[override]
            return tile

        def mask(self) -> np.ndarray | None:  # type: ignore[override]
            return mask

    return object.__new__(_Segment)


def test_a_frame_with_pixels_its_mask_marks_as_not_acquired_is_refused() -> None:
    mask = np.ones((16, 16), dtype=bool)
    mask[0, :4] = False
    segment = _segment(np.zeros((16, 16), dtype="<u2"), mask=mask)
    with (
        _serving(segment) as movie,
        pytest.raises(
            UnsupportedContainerError, match="frame 0 that its validity mask marks as not acquired"
        ),
    ):
        movie.read_frames(0, 1)


def test_a_frame_whose_mask_covers_every_pixel_is_served() -> None:
    tile = np.arange(256, dtype="<u2").reshape(16, 16)
    segment = _segment(tile, mask=np.ones((16, 16), dtype=bool))
    with _serving(segment) as movie:
        np.testing.assert_array_equal(movie.read_frames(0, 1)[0], tile)


def test_the_repr_names_the_file_and_its_shape() -> None:
    with CziMovie(CZI / "movie_raw_16x16x4.czi") as movie:
        assert repr(movie) == (
            "CziMovie('movie_raw_16x16x4.czi', frames=4, height=16, width=16, dtype='<u2')"
        )


def test_the_extraction_path_still_reads_only_tiff() -> None:
    """``open_movie`` keeps its TIFF-only refusal until its callers read through read_frames."""
    from tether.io.movie import open_movie

    with pytest.raises(UnsupportedContainerError, match="extraction reads only TIFF"):
        open_movie(CZI / "movie_raw_16x16x4.czi")


# --- the metadata rules -------------------------------------------------------------------


def _xml(increment: str = "", x: str = "", y: str = "", channels: tuple[str, ...] = ()) -> str:
    t = f"<T><Positions><Interval><Increment>{increment}</Increment></Interval></Positions></T>"
    chans = "".join(f'<Channel Id="Channel:{i}" Name="{n}"/>' for i, n in enumerate(channels))
    dist = "".join(
        f'<Distance Id="{d}"><Value>{v}</Value></Distance>' for d, v in (("X", x), ("Y", y)) if v
    )
    return (
        "<ImageDocument><Metadata><Information><Image><Dimensions>"
        f"<Channels>{chans}</Channels>{t}</Dimensions></Image></Information>"
        f"<Scaling><Items>{dist}</Items></Scaling></Metadata></ImageDocument>"
    )


def test_metadata_reads_each_value_from_the_xml() -> None:
    meta = _read_metadata(ElementTree.fromstring(_xml("0.05", "1.6E-07", "1.6E-07", ("Cy3",))))
    assert (meta.frame_time.value, meta.frame_time.origin) == (0.05, "file")
    assert (meta.pixel_size.value, meta.pixel_size.origin) == (0.16, "file")
    assert (meta.channels.value, meta.channels.origin) == (("Cy3",), "file")


def test_a_file_with_no_xml_metadata_has_every_value_unknown() -> None:
    meta = _read_metadata(None)
    assert {meta.frame_time.origin, meta.pixel_size.origin, meta.channels.origin} == {"unknown"}


@pytest.mark.parametrize(
    ("xml", "field"),
    [
        (_xml("0"), "frame_time"),
        (_xml("-0.1"), "frame_time"),
        (_xml("nan"), "frame_time"),
        (_xml("fast"), "frame_time"),
        (_xml(x="1E-07", y="2E-07"), "pixel_size"),
        (_xml(x="1E-07"), "pixel_size"),
        (_xml(channels=("Cy3", "Cy5")), "channels"),
        (_xml(channels=("",)), "channels"),
    ],
)
def test_metadata_that_the_file_does_not_state_clearly_is_unknown(xml: str, field: str) -> None:
    value = getattr(_read_metadata(ElementTree.fromstring(xml)), field)
    assert (value.value, value.origin) == (None, "unknown")


# --- the public ZEN movie (weekly reference-data job) ---------------------------------------


@pytest.mark.reference_download
def test_the_public_zen_movie_equals_its_reference_tiff_bit_for_bit() -> None:
    reference = tifffile.imread(path_for("zeiss-czi-t2-ch1-reference"))
    with open_movie_source(path_for("zeiss-czi-t2-ch1")) as source:
        assert source.pixel_type == "<u2"
        got = source.read_frames(0, source.n_frames)
        meta = source.metadata
    assert got.dtype == reference.dtype
    assert got.shape == reference.shape == (2, 256, 256)
    assert got.tobytes() == reference.tobytes()
    assert (meta.frame_time.value, meta.frame_time.origin) == (0.172, "file")
    assert (meta.pixel_size.value, meta.pixel_size.origin) == (0.1, "file")
    assert (meta.channels.value, meta.channels.origin) == (("EGFP",), "file")


@pytest.mark.reference_download
def test_the_public_two_channel_zen_movie_is_refused() -> None:
    with pytest.raises(UnsupportedContainerError, match="holds 2 channels"):
        open_movie_source(path_for("zeiss-czi-t3-ch2"))
