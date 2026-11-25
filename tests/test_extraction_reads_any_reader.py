# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction reads every registered reader through ``read_frames`` (#1388, ADR-0149).

An ND2 time series written by ``tests/_nd2_synth.py`` and the TIFF export of the same
frames go through ``tether extract``, the service command that the application runs, and
``tether batch``; each gives the same traces, bit for bit. The frames of a non-TIFF movie
are read in blocks into a temporary file, so the read holds about one block in memory and
its result does not depend on the block size. A reader that extraction does not list is
refused by name.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

from _nd2_synth import write_nd2  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.io import adapters  # noqa: E402
from tether.io.adapters import (  # noqa: E402
    MovieAdapter,
    UnsupportedContainerError,
    open_movie_source,
)
from tether.io.frames import (  # noqa: E402
    READ_BLOCK_FRAMES,
    FrameView,
    open_extraction_movie,
    spool_frames,
)
from tether.io.movie import EXTRACTED_CONTAINERS  # noqa: E402

_SHAPE = (64, 96)  # two 48-px halves: room for the default 21-px aperture
_N_FRAMES = 12
_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = _DONOR + np.array([49.0, 0.0])  # +1 px in the acceptor half
_WINDOW = "21"


def _movie_frames() -> np.ndarray:
    """A two-channel movie of Gaussian spots that changes from frame to frame (uint16)."""
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    rng = np.random.default_rng(1388)
    frames = np.empty((_N_FRAMES, *_SHAPE), dtype=np.uint16)
    for t in range(_N_FRAMES):
        frame = np.full(_SHAPE, _BG)
        for x, y in np.vstack([_DONOR, _ACCEPTOR]):
            frame += (
                _AMP
                * (1.0 + 0.05 * t)
                * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
            )
        frames[t] = np.round(frame + rng.normal(0.0, 3.0, _SHAPE)).astype(np.uint16)
    return frames


_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """One movie as an ND2 original and as its (big-endian) TIFF export, in two folders."""
    frames = _movie_frames()
    (tmp_path / "nd2").mkdir()
    (tmp_path / "tif").mkdir()
    # The names follow the acquisition convention (PRD Appendix A), so the condition and
    # the video index parsed from them are compared too.
    nd2 = write_nd2(tmp_path / "nd2" / f"{_NAME}.nd2", frames)
    tif = tmp_path / "tif" / f"{_NAME}.tif"
    tifffile.imwrite(tif, frames.astype(">u2"), photometric="minisblack", byteorder=">")
    return nd2, tif


def _assert_same_extraction(a: Path, b: Path) -> None:
    traces_a, traces_b = read_traces(a), read_traces(b)
    assert sorted(traces_a) == sorted(traces_b)
    for name in traces_a:
        assert traces_a[name].dtype == traces_b[name].dtype, name
        np.testing.assert_array_equal(traces_a[name], traces_b[name], err_msg=name)
    molecules_a, molecules_b = read_molecules(a), read_molecules(b)
    np.testing.assert_array_equal(molecules_a["donor_xy"], molecules_b["donor_xy"])
    # One condition, parsed from the movie's name with its container suffix stripped.
    assert set(molecules_a["condition_id"]) == set(molecules_b["condition_id"])
    assert len(set(molecules_a["condition_id"])) == 1


# --- extraction: the ND2 and its TIFF export give the same traces ------------------------


def test_nd2_is_an_extracted_container() -> None:
    assert EXTRACTED_CONTAINERS == ("TIFF", "ND2", "CZI")


def test_tether_extract_gives_the_same_traces_for_nd2_and_its_tiff_export(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    nd2, tif = pair
    out_nd2, out_tif = tmp_path / "from-nd2.tether", tmp_path / "from-tif.tether"
    assert main(["extract", str(nd2), "-o", str(out_nd2), "--window", _WINDOW]) == 0
    assert main(["extract", str(tif), "-o", str(out_tif), "--window", _WINDOW]) == 0
    assert len(read_molecules(out_nd2)) == 3
    _assert_same_extraction(out_nd2, out_tif)


def test_the_application_command_gives_the_same_traces(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    """The application runs the ``extract`` service command; it reads ND2 the same way."""
    from tether.services.extract import ExtractRequest, run  # noqa: PLC0415

    nd2, tif = pair
    outs = []
    for movie in (nd2, tif):
        out = tmp_path / f"app-{movie.suffix[1:]}.tether"
        result = run(ExtractRequest(movie=str(movie), output=str(out), window=int(_WINDOW)))
        assert result.summary is not None
        assert result.summary.n_molecules == 3
        outs.append(out)
    _assert_same_extraction(*outs)


def test_the_molecule_count_reads_nd2(pair: tuple[Path, Path]) -> None:
    from tether.project.extract import ExtractOptions, count_molecules_by_rule  # noqa: PLC0415

    nd2, tif = pair
    options = ExtractOptions(window=int(_WINDOW))
    assert count_molecules_by_rule(nd2, options=options) == count_molecules_by_rule(
        tif, options=options
    )


def test_the_nd2_metadata_is_recorded(pair: tuple[Path, Path], tmp_path: Path) -> None:
    """The ND2's own frame time (100 ms) is stored, where the plain TIFF has none."""
    import h5py  # noqa: PLC0415

    nd2, _ = pair
    out = tmp_path / "meta.tether"
    assert main(["extract", str(nd2), "-o", str(out), "--window", _WINDOW]) == 0
    with h5py.File(out, "r") as f:
        row = f["/movies/table"][0]
        assert row["frame_time"] == pytest.approx(0.1)
        assert int(row["n_frames"]) == _N_FRAMES


def test_tether_batch_extracts_an_nd2_folder(pair: tuple[Path, Path], tmp_path: Path) -> None:
    nd2, tif = pair
    out_nd2, out_tif = tmp_path / "batch-nd2", tmp_path / "batch-tif"
    for folder, out in ((nd2.parent, out_nd2), (tif.parent, out_tif)):
        rc = main(["batch", str(folder), "-d", str(out), "--no-idealize"])
        assert rc == 0
    _assert_same_extraction(out_nd2 / f"{_NAME}.tether", out_tif / f"{_NAME}.tether")


def test_tether_batch_refuses_an_original_beside_its_export(
    pair: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nd2, tif = pair
    both = tmp_path / "both"
    both.mkdir()
    (both / nd2.name).write_bytes(nd2.read_bytes())
    (both / tif.name).write_bytes(tif.read_bytes())
    out = tmp_path / "out"
    rc = main(["batch", str(both), "-d", str(out), "--no-idealize"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "one name in two containers" in err
    assert not out.exists() or not any(out.iterdir())


# --- the block read ---------------------------------------------------------------------


@pytest.mark.parametrize("block", [1, 5, READ_BLOCK_FRAMES])
def test_the_spooled_frames_do_not_depend_on_the_block_size(
    pair: tuple[Path, Path], block: int
) -> None:
    nd2, _ = pair
    with open_movie_source(nd2) as source:
        spooled = spool_frames(source, block=block)
        expected = source.read_frames(0, source.n_frames)
    assert not spooled.flags.writeable
    assert spooled.dtype == expected.dtype
    np.testing.assert_array_equal(spooled, expected)


def test_the_block_read_holds_about_one_block_in_memory(tmp_path: Path) -> None:
    """Spooling a 40-frame movie in 4-frame blocks never holds the movie in memory."""
    frames = np.random.default_rng(7).integers(0, 4000, size=(40, 64, 64)).astype("<u2")
    path = write_nd2(tmp_path / "long.nd2", frames)
    block_bytes = 4 * frames[0].nbytes
    with open_movie_source(path) as source:
        tracemalloc.start()
        try:
            spooled = spool_frames(source, block=4)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert peak < 3 * block_bytes < frames.nbytes
    np.testing.assert_array_equal(spooled, frames)


def test_extraction_reads_the_nd2_only_through_read_frames_in_blocks(
    pair: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.io.nd2 import Nd2Movie  # noqa: PLC0415

    nd2, _ = pair
    calls: list[tuple[int, int]] = []
    real = Nd2Movie.read_frames

    def _spy(self: Nd2Movie, start: int, stop: int) -> np.ndarray:
        calls.append((start, stop))
        return real(self, start, stop)

    monkeypatch.setattr(Nd2Movie, "read_frames", _spy)
    monkeypatch.setattr("tether.io.frames.READ_BLOCK_FRAMES", 5)
    with open_extraction_movie(nd2) as movie:
        assert movie.container == "ND2"
        assert movie.shape == (_N_FRAMES, *_SHAPE)
        assert movie.byteorder == "<"
        assert movie.frame_time == pytest.approx(0.1)
        np.testing.assert_array_equal(movie.data, _movie_frames())
    assert calls == [(0, 5), (5, 10), (10, 12)]


def test_the_tiff_path_is_the_readers_own_memory_map(pair: tuple[Path, Path]) -> None:
    _, tif = pair
    with open_extraction_movie(tif) as movie:
        assert movie.container == "TIFF"
        assert isinstance(movie.data, np.memmap)
        assert movie.dtype == np.dtype(">u2")
        assert movie.byteorder == ">"


def test_a_reader_that_extraction_does_not_list_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []

    def _open(path: Path) -> object:
        opened.append(path)
        raise AssertionError("an unlisted reader must not be opened")

    fake = MovieAdapter("Fake", (".fake",), lambda head: head[:4] == b"FAKE", _open)  # type: ignore[arg-type]
    real = adapters.registered_adapters()
    monkeypatch.setattr(adapters, "registered_adapters", lambda: (*real, fake))
    path = tmp_path / "movie.fake"
    path.write_bytes(b"FAKE" + b"\x00" * 32)
    with (
        pytest.raises(UnsupportedContainerError, match="extraction does not read Fake"),
        open_extraction_movie(path),
    ):
        pass  # pragma: no cover
    assert opened == []


def test_a_failed_spool_closes_the_reader(tmp_path: Path, monkeypatch) -> None:
    path = write_nd2(tmp_path / "f.nd2", _movie_frames())

    def _fail(*_a: object, **_k: object) -> np.ndarray:
        raise OSError("disk gone")

    monkeypatch.setattr("tether.io.frames.spool_frames", _fail)
    with pytest.raises(OSError, match="disk gone"), open_extraction_movie(path):
        pass  # pragma: no cover
    path.unlink()  # a handle left open would make this fail on Windows


# --- the lazy view for the movie panel ---------------------------------------------------


def test_a_frame_view_reads_only_the_frames_an_index_touches(pair: tuple[Path, Path]) -> None:
    nd2, _ = pair
    frames = _movie_frames()
    with open_movie_source(nd2) as source:
        view = FrameView(source)
        assert view.shape == frames.shape
        assert view.dtype == np.dtype("<u2")
        assert view.ndim == 3
        assert len(view) == _N_FRAMES
        for index in (
            0,
            -1,
            np.int64(3),
            slice(2, 7),
            slice(None, None, 3),
            slice(None, None, -2),
            slice(9, 2, -3),
            (4, slice(10, 20), 5),
            (slice(1, 3), 0),
            (),
        ):
            np.testing.assert_array_equal(view[index], frames[index], err_msg=repr(index))
        np.testing.assert_array_equal(np.asarray(view), frames)
        with pytest.raises(IndexError):
            view[_N_FRAMES]


def test_the_movie_panel_service_gives_a_view_for_nd2_and_the_map_for_tiff(
    pair: tuple[Path, Path],
) -> None:
    from tether.services.movies import movie_file_suffixes, movie_frames  # noqa: PLC0415

    nd2, tif = pair
    with open_movie_source(nd2) as source:
        assert isinstance(movie_frames(source), FrameView)
    with open_movie_source(tif) as source:
        assert isinstance(movie_frames(source), np.memmap)
    with pytest.raises(TypeError, match="movie source"):
        movie_frames(np.zeros((2, 2, 2)))  # type: ignore[arg-type]
    assert ".nd2" in movie_file_suffixes()


# --- the edges of the block read and the view, on a source written here ------------------


class _Source:
    """A minimal ``MovieSource`` over an array, for the edges no real reader reaches."""

    def __init__(self, frames: np.ndarray, *, bad_block: bool = False) -> None:
        self.frames = frames
        self.path = Path("memory.src")
        self.shape = tuple(frames.shape)
        self.n_frames = frames.shape[0]
        self.pixel_type = frames.dtype.str
        self.bad_block = bad_block

    def read_frames(self, start: int, stop: int) -> np.ndarray:
        out = self.frames[start:stop]
        return out.astype(np.float32) if self.bad_block else out.copy()


def test_spool_refuses_a_block_size_below_one() -> None:
    with pytest.raises(ValueError, match="block must be >= 1"):
        spool_frames(_Source(np.zeros((2, 2, 2), "<u2")), block=0)  # type: ignore[arg-type]


def test_spool_of_a_movie_with_no_frames_is_an_empty_array() -> None:
    out = spool_frames(_Source(np.zeros((0, 4, 4), ">u2")))  # type: ignore[arg-type]
    assert out.shape == (0, 4, 4)
    assert out.dtype == np.dtype(">u2")


def test_spool_refuses_a_block_of_the_wrong_type() -> None:
    with pytest.raises(ValueError, match="came back as"):
        spool_frames(_Source(np.zeros((3, 2, 2), "<u2"), bad_block=True))  # type: ignore[arg-type]


def test_spool_keeps_a_big_endian_type_and_its_order() -> None:
    from tether.io.frames import _byteorder  # noqa: PLC0415, PLC2701

    frames = np.arange(24, dtype=">u2").reshape(2, 3, 4)
    out = spool_frames(_Source(frames), block=1)  # type: ignore[arg-type]
    assert out.dtype == np.dtype(">u2")
    np.testing.assert_array_equal(out, frames)
    assert _byteorder(out.dtype) == ">"
    assert _byteorder(np.dtype("<u2")) == "<"


class _FullDisk:
    """A temporary file whose writes fail with the given ``errno``."""

    def __init__(self, code: int) -> None:
        self.code = code

    def __enter__(self) -> _FullDisk:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, _data: object) -> int:
        raise OSError(self.code, "write failed")


def test_a_full_temporary_folder_is_named_with_the_size(monkeypatch) -> None:
    import errno  # noqa: PLC0415

    monkeypatch.setattr("tether.io.frames.tempfile.TemporaryFile", lambda: _FullDisk(errno.ENOSPC))
    with pytest.raises(OSError, match=r"no room in the temporary folder .* \(48 bytes\)") as err:
        spool_frames(_Source(np.zeros((3, 2, 4), "<u2")))  # type: ignore[arg-type]
    assert err.value.errno == errno.ENOSPC


def test_any_other_write_error_is_raised_as_it_is(monkeypatch) -> None:
    import errno  # noqa: PLC0415

    monkeypatch.setattr("tether.io.frames.tempfile.TemporaryFile", lambda: _FullDisk(errno.EIO))
    with pytest.raises(OSError, match="write failed"):
        spool_frames(_Source(np.zeros((3, 2, 4), "<u2")))  # type: ignore[arg-type]


def test_the_view_protocol_edges() -> None:
    frames = np.arange(5 * 3 * 2, dtype="<u2").reshape(5, 3, 2)
    view = FrameView(_Source(frames))  # type: ignore[arg-type]
    assert view.size == frames.size
    assert [f.tolist() for f in view] == [f.tolist() for f in frames]
    assert repr(view) == "FrameView('memory.src', frames=5, height=3, width=2)"
    np.testing.assert_array_equal(view[[0, 3]], frames[[0, 3]])
    assert view[3:1].shape == (0, 3, 2)
    assert np.asarray(view, dtype=np.float64).dtype == np.float64
    with pytest.raises(ValueError, match="no-copy"):
        view.__array__(copy=False)
