# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The movie adapter registry and the refusals of the TIFF adapter (#901, ADR-0113).

What the per-adapter conformance module does not cover: the registry's choice
rules and its refusal of an unknown container, the lossy-codec and pixel
refusals, the metadata origins, and that the interface is Qt-free.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("tifffile")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether.io import adapters  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    InvalidPixelError,
    LossyCodecError,
    MetadataValue,
    MovieAdapter,
    MovieMetadata,
    UnsupportedContainerError,
    open_movie_source,
    register_adapter,
    registered_adapters,
    select_adapter,
)
from tether.io.movie import TIFF_ADAPTER, MovieReader, open_movie  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"


def _stack(n: int = 5) -> np.ndarray:
    return np.arange(n * 8 * 8, dtype=np.uint16).reshape(n, 8, 8)


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry copy that a test may add to; the real one is restored afterwards."""
    registered_adapters()  # load the built-ins into the real registry first
    monkeypatch.setattr(adapters, "_REGISTRY", list(adapters._REGISTRY))


# --- the registry ---------------------------------------------------------------


def test_tiff_is_the_first_registered_adapter() -> None:
    assert registered_adapters()[0] is TIFF_ADAPTER


def test_unknown_container_lists_readers_and_names_bio_formats(tmp_path: Path) -> None:
    path = tmp_path / "movie.sif"
    path.write_bytes(b"Andor Technology Multi-Channel File\n" + b"\x00" * 64)
    with pytest.raises(UnsupportedContainerError) as err:
        select_adapter(path)
    msg = str(err.value)
    assert ".sif" in msg
    assert "TIFF (.tif, .tiff)" in msg, "the message lists the containers Tether reads"
    assert "Bio-Formats" in msg, "the message names the route for other containers"


def test_unknown_container_is_refused_on_the_extraction_path(tmp_path: Path) -> None:
    path = tmp_path / "movie.sif"
    path.write_bytes(b"Andor Technology Multi-Channel File\n" + b"\x00" * 64)
    with pytest.raises(UnsupportedContainerError, match="Bio-Formats"):
        open_movie(path)


def test_a_tiff_name_on_other_content_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "movie.tif"
    path.write_bytes(b"this is not a tiff")
    with pytest.raises(UnsupportedContainerError, match="content is not a TIFF"):
        open_movie(path)


def test_a_missing_file_is_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_movie_source(tmp_path / "absent.tif")


def test_registry_refuses_a_duplicate_name_or_extension(scratch_registry: None) -> None:
    clash_name = MovieAdapter("TIFF", (".x",), lambda h: False, MovieReader)
    clash_ext = MovieAdapter("Other", (".tif",), lambda h: False, MovieReader)
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(clash_name)
    with pytest.raises(ValueError, match="already claims"):
        register_adapter(clash_ext)


def test_a_registration_before_the_first_read_sees_the_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex on PR #1101: an early registration must not skip or displace TIFF."""
    monkeypatch.setattr(adapters, "_REGISTRY", [])
    monkeypatch.setattr(adapters, "_BUILTINS_LOADED", False)
    with pytest.raises(ValueError, match="already claims"):
        register_adapter(MovieAdapter("Early", (".tif",), lambda h: False, MovieReader))
    register_adapter(MovieAdapter("Later", (".later",), lambda h: False, MovieReader))
    assert [a.name for a in registered_adapters()] == ["TIFF", "CZI", "ND2", "cellSens VSI", "Later"]


def test_a_failed_first_load_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """The built-ins count as loaded only once they are in the registry."""
    monkeypatch.setattr(adapters, "_REGISTRY", [])
    monkeypatch.setattr(adapters, "_BUILTINS_LOADED", False)
    real = adapters._register
    calls: list[str] = []

    def _fail_once(adapter: MovieAdapter) -> None:
        calls.append(adapter.name)
        if len(calls) == 1:
            raise RuntimeError("simulated import-time failure")
        real(adapter)

    monkeypatch.setattr(adapters, "_register", _fail_once)
    with pytest.raises(RuntimeError, match="simulated"):
        registered_adapters()
    assert [a.name for a in registered_adapters()] == ["TIFF", "CZI", "ND2", "cellSens VSI"]


def test_a_failure_part_way_through_the_builtins_leaves_none_of_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after the second built-in failed does not meet the first one again (#903)."""
    monkeypatch.setattr(adapters, "_REGISTRY", [])
    monkeypatch.setattr(adapters, "_BUILTINS_LOADED", False)
    real = adapters._register
    failed: list[str] = []

    def _fail_on_czi_once(adapter: MovieAdapter) -> None:
        if adapter.name == "CZI" and not failed:
            failed.append(adapter.name)
            raise RuntimeError("simulated failure of the second built-in")
        real(adapter)

    monkeypatch.setattr(adapters, "_register", _fail_on_czi_once)
    with pytest.raises(RuntimeError, match="second built-in"):
        registered_adapters()
    assert adapters._REGISTRY == []
    assert [a.name for a in registered_adapters()] == ["TIFF", "CZI", "ND2", "cellSens VSI"]


def test_concurrent_first_reads_all_see_tiff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_REGISTRY", [])
    monkeypatch.setattr(adapters, "_BUILTINS_LOADED", False)
    barrier = threading.Barrier(8)
    seen: list[tuple[str, ...]] = []

    def _read() -> None:
        barrier.wait()
        seen.append(tuple(a.name for a in registered_adapters()))

    threads = [threading.Thread(target=_read) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert seen == [("TIFF", "CZI", "ND2", "cellSens VSI")] * 8


def test_registry_refuses_a_malformed_extension(scratch_registry: None) -> None:
    with pytest.raises(ValueError, match="lower-case"):
        register_adapter(MovieAdapter("Upper", (".FOO",), lambda h: False, MovieReader))


def test_extension_breaks_a_content_tie(scratch_registry: None, tmp_path: Path) -> None:
    """Two adapters accept the bytes; the one that claims the extension is chosen."""
    other = MovieAdapter("TIFF-variant", (".tvar",), TIFF_ADAPTER.sniff, MovieReader)
    register_adapter(other)
    path = tmp_path / "movie.tvar"
    tifffile.imwrite(path, _stack())
    assert select_adapter(path) is other
    plain = tmp_path / "movie.tif"
    tifffile.imwrite(plain, _stack())
    assert select_adapter(plain) is TIFF_ADAPTER


def test_a_new_adapter_is_selected_by_content(scratch_registry: None, tmp_path: Path) -> None:
    opened: list[Path] = []

    def _open(p: Path) -> MovieReader:
        opened.append(p)
        return MovieReader(FIXTURE)

    register_adapter(MovieAdapter("Fake", (".fake",), lambda h: h[:4] == b"FAKE", _open))
    path = tmp_path / "anything.bin"
    path.write_bytes(b"FAKE" + b"\x00" * 32)
    assert select_adapter(path).name == "Fake"
    open_movie_source(path).close()
    assert opened == [path]


def test_open_movie_refuses_a_non_tiff_source(scratch_registry: None, tmp_path: Path) -> None:
    """Extraction reads the TIFF memory map, so another adapter's source is refused there."""

    class _Other:
        closed = False

        def close(self) -> None:
            self.closed = True

    made: list[_Other] = []

    def _open(p: Path) -> _Other:
        made.append(_Other())
        return made[-1]

    register_adapter(MovieAdapter("Fake", (".fake",), lambda h: h[:4] == b"FAKE", _open))  # type: ignore[arg-type]
    path = tmp_path / "movie.fake"
    path.write_bytes(b"FAKE" + b"\x00" * 32)
    with pytest.raises(UnsupportedContainerError, match="reads only TIFF"):
        open_movie(path)
    assert made[0].closed


# --- the TIFF adapter's refusals ------------------------------------------------


def _set_first_page_compression(path: Path, code: int) -> None:
    """Rewrite the first page's Compression tag in place (no codec package needed)."""
    with tifffile.TiffFile(path) as tif:
        offset = tif.pages[0].tags["Compression"].valueoffset
        fmt = tif.byteorder + "H"
    data = bytearray(path.read_bytes())
    data[offset : offset + 2] = struct.pack(fmt, code)
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(("code", "name"), [(7, "JPEG"), (34712, "JPEG2000"), (50001, "WEBP")])
def test_a_lossy_codec_is_refused_by_name(tmp_path: Path, code: int, name: str) -> None:
    path = tmp_path / "lossy.tif"
    tifffile.imwrite(path, _stack())
    _set_first_page_compression(path, code)
    with pytest.raises(LossyCodecError, match=rf"the {name} codec, which can be lossy"):
        open_movie(path)


def test_a_lossless_compressed_tiff_keeps_its_old_refusal(tmp_path: Path) -> None:
    path = tmp_path / "lzw.tif"
    tifffile.imwrite(path, _stack())
    _set_first_page_compression(path, 5)  # LZW: lossless, but not memory-mappable
    with pytest.raises(ValueError, match="not memory-mappable") as err:
        open_movie(path)
    assert not isinstance(err.value, LossyCodecError)


def test_a_palette_tiff_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "palette.tif"
    colormap = np.zeros((3, 256), dtype=np.uint16)
    tifffile.imwrite(path, _stack().astype(np.uint8), photometric="palette", colormap=colormap)
    with pytest.raises(InvalidPixelError, match="palette"):
        open_movie(path)


def test_an_rgb_tiff_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "rgb.tif"
    tifffile.imwrite(path, np.zeros((3, 8, 8, 3), dtype=np.uint8), photometric="rgb")
    with pytest.raises(InvalidPixelError, match="3 samples"):
        open_movie(path)


def test_a_complex_pixel_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "complex.tif"
    tifffile.imwrite(path, np.zeros((4, 8, 8), dtype=np.complex64), photometric="minisblack")
    with pytest.raises(InvalidPixelError, match="complex64"):
        open_movie(path)


@pytest.mark.parametrize("dtype", ["<u1", ">i2", "<u2", ">f4"])
def test_the_pixel_type_is_recorded(tmp_path: Path, dtype: str) -> None:
    path = tmp_path / "typed.tif"
    tifffile.imwrite(path, _stack().astype(dtype))
    with open_movie(path) as movie:
        assert np.dtype(movie.pixel_type) == np.dtype(dtype)
        assert movie.pixel_type == movie.dtype.str


# --- metadata origins -----------------------------------------------------------


def test_frame_time_from_the_file_has_origin_file(tmp_path: Path) -> None:
    path = tmp_path / "timed.tif"
    tifffile.imwrite(path, _stack(), imagej=True, metadata={"axes": "TYX", "finterval": 0.1})
    with open_movie(path) as movie:
        meta = movie.metadata
    assert meta.frame_time == MetadataValue(0.1, "file")
    assert meta.pixel_size.origin == "unknown"
    assert meta.channels.origin == "unknown"


def test_the_reference_movie_has_no_known_metadata() -> None:
    with open_movie(FIXTURE) as movie:
        assert movie.metadata == MovieMetadata()


def test_a_user_value_has_origin_user() -> None:
    meta = MovieMetadata(frame_time=MetadataValue.from_file(0.05)).with_user(pixel_size=0.16)
    assert meta.frame_time == MetadataValue(0.05, "file")
    assert meta.pixel_size == MetadataValue(0.16, "user")
    assert meta.channels == MetadataValue.unknown()
    with pytest.raises(TypeError, match="unknown metadata field"):
        meta.with_user(exposure=0.1)


@pytest.mark.parametrize(
    ("value", "origin"), [(None, "file"), (None, "user"), (1.0, "unknown"), (1.0, "guess")]
)
def test_metadata_value_refuses_a_contradiction(value: float | None, origin: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        MetadataValue(value, origin)  # type: ignore[arg-type]


# --- Qt-free --------------------------------------------------------------------


def test_the_interface_and_registry_are_qt_free() -> None:
    code = (
        "import sys\n"
        "import tether.io.adapters as a, tether.io.movie\n"
        "a.registered_adapters()\n"
        "qt = [m for m in sys.modules if m.split('.')[0] in "
        "('PySide6', 'PyQt5', 'PyQt6', 'qtpy', 'napari')]\n"
        "assert not qt, qt\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
