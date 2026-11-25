# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction reads cellSens ``.vsi`` movies through ``read_frames`` (#1398, ADR-0147, ADR-0149).

The ``.vsi`` here is served by the Java-free stand-in runner
(``tests/_fake_bioformats_runner.py``), which speaks the protocol of the real one, so this
runs in the base matrix. A ``.vsi`` movie and a TIFF of the same frames go through
``tether extract``, the service command that the application runs, and ``tether batch``;
each gives the same traces, bit for bit. The stand-in serves big-endian frames, as
Bio-Formats does for a big-endian file, and the TIFF is little-endian, so the byte order
is checked as well. A ``.vsi`` beside its TIFF export in one batch folder is refused by the
rule of #1388, and the reader's own refusals (a lossy codec, two channels, no stack) reach
the user unchanged. The real runner is the ``bioformats / live`` job
(``tests/test_extraction_bioformats.py``).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

from tether.cli import main  # noqa: E402
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.io import bioformats  # noqa: E402
from tether.io.adapters import LossyCodecError, UnsupportedContainerError  # noqa: E402
from tether.io.bioformats import BioformatsMovieSource, BioformatsUnavailableError  # noqa: E402
from tether.io.frames import open_extraction_movie  # noqa: E402
from tether.io.movie import EXTRACTED_CONTAINERS  # noqa: E402

FAKE_RUNNER = Path(__file__).resolve().with_name("_fake_bioformats_runner.py")

_SHAPE = (64, 96)  # two 48-px halves: room for the default 21-px aperture
_N_FRAMES = 12
_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = _DONOR + np.array([49.0, 0.0])  # +1 px in the acceptor half
_WINDOW = "21"
#: A name in the acquisition convention (PRD Appendix A), so the condition parsed from it
#: is compared too: ``x.vsi`` and ``x.tif`` must give one condition.
_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"


def _movie_frames() -> np.ndarray:
    """A two-channel movie of Gaussian spots that changes from frame to frame (uint16)."""
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    rng = np.random.default_rng(1398)
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


def _write_vsi(path: Path, frames: np.ndarray | None = None, **meta: object) -> Path:
    """A ``.vsi`` for the stand-in runner: a small TIFF, its frames and its metadata beside it."""
    tifffile.imwrite(path, np.zeros((4, 4), dtype="u1"))  # a .vsi begins with a TIFF header
    if frames is not None:
        np.save(path.with_name(path.name + ".frames.npy"), frames)
    if meta:
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bioformats.BIOFORMATS_PYTHON_ENV, sys.executable)
    monkeypatch.setattr(bioformats, "RUNNER_SCRIPT", FAKE_RUNNER)


@pytest.fixture
def pair(tmp_path: Path, fake_runner: None) -> tuple[Path, Path]:
    """One movie as a ``.vsi`` (big-endian frames) and as a little-endian TIFF, in two folders."""
    frames = _movie_frames()
    (tmp_path / "vsi").mkdir()
    (tmp_path / "tif").mkdir()
    vsi = _write_vsi(tmp_path / "vsi" / f"{_NAME}.vsi", frames.astype(">u2"))
    tif = tmp_path / "tif" / f"{_NAME}.tif"
    tifffile.imwrite(tif, frames.astype("<u2"), photometric="minisblack", byteorder="<")
    return vsi, tif


def _assert_same_extraction(a: Path, b: Path) -> None:
    traces_a, traces_b = read_traces(a), read_traces(b)
    assert sorted(traces_a) == sorted(traces_b)
    for name in traces_a:
        assert traces_a[name].dtype == traces_b[name].dtype, name
        np.testing.assert_array_equal(traces_a[name], traces_b[name], err_msg=name)
    molecules_a, molecules_b = read_molecules(a), read_molecules(b)
    np.testing.assert_array_equal(molecules_a["donor_xy"], molecules_b["donor_xy"])
    assert set(molecules_a["condition_id"]) == set(molecules_b["condition_id"])
    assert len(set(molecules_a["condition_id"])) == 1


# --- the .vsi and a TIFF of the same frames give the same traces --------------------------


def test_cellsens_vsi_is_an_extracted_container() -> None:
    assert EXTRACTED_CONTAINERS == ("TIFF", "ND2", "CZI", "cellSens VSI")


def test_tether_extract_gives_the_same_traces_for_a_vsi_and_a_tiff(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    vsi, tif = pair
    out_vsi, out_tif = tmp_path / "from-vsi.tether", tmp_path / "from-tif.tether"
    assert main(["extract", str(vsi), "-o", str(out_vsi), "--window", _WINDOW]) == 0
    assert main(["extract", str(tif), "-o", str(out_tif), "--window", _WINDOW]) == 0
    assert len(read_molecules(out_vsi)) == 3
    _assert_same_extraction(out_vsi, out_tif)


def test_the_application_command_gives_the_same_traces(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    """The application runs the ``extract`` service command; it reads a ``.vsi`` the same way."""
    from tether.services.extract import ExtractRequest, run  # noqa: PLC0415

    vsi, tif = pair
    outs = []
    for movie in (vsi, tif):
        out = tmp_path / f"app-{movie.suffix[1:]}.tether"
        result = run(ExtractRequest(movie=str(movie), output=str(out), window=int(_WINDOW)))
        assert result.summary is not None
        assert result.summary.n_molecules == 3
        outs.append(out)
    _assert_same_extraction(*outs)


def test_the_vsi_metadata_is_recorded(pair: tuple[Path, Path], tmp_path: Path) -> None:
    """The frame time that the runner reports (50 ms) is stored; the plain TIFF has none."""
    import h5py  # noqa: PLC0415

    vsi, _ = pair
    out = tmp_path / "meta.tether"
    assert main(["extract", str(vsi), "-o", str(out), "--window", _WINDOW]) == 0
    with h5py.File(out, "r") as f:
        row = f["/movies/table"][0]
        assert row["frame_time"] == pytest.approx(0.05)
        assert int(row["n_frames"]) == _N_FRAMES


def test_extraction_reads_the_vsi_only_through_read_frames_in_blocks(
    pair: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    vsi, _ = pair
    calls: list[tuple[int, int]] = []
    real = BioformatsMovieSource.read_frames

    def _spy(self: BioformatsMovieSource, start: int, stop: int) -> np.ndarray:
        calls.append((start, stop))
        return real(self, start, stop)

    monkeypatch.setattr(BioformatsMovieSource, "read_frames", _spy)
    monkeypatch.setattr("tether.io.frames.READ_BLOCK_FRAMES", 5)
    with open_extraction_movie(vsi) as movie:
        assert movie.container == "cellSens VSI"
        assert movie.shape == (_N_FRAMES, *_SHAPE)
        assert movie.dtype == np.dtype(">u2")
        assert movie.byteorder == ">"
        np.testing.assert_array_equal(movie.data, _movie_frames())
    assert calls == [(0, 5), (5, 10), (10, 12)]


# --- tether batch ---------------------------------------------------------------------------


def test_tether_batch_extracts_a_vsi_folder(pair: tuple[Path, Path], tmp_path: Path) -> None:
    vsi, tif = pair
    out_vsi, out_tif = tmp_path / "batch-vsi", tmp_path / "batch-tif"
    for folder, out in ((vsi.parent, out_vsi), (tif.parent, out_tif)):
        assert main(["batch", str(folder), "-d", str(out), "--no-idealize"]) == 0
    _assert_same_extraction(out_vsi / f"{_NAME}.tether", out_tif / f"{_NAME}.tether")


def test_tether_batch_refuses_a_vsi_beside_its_tiff_export(
    pair: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rule of #1388: one name in two containers is refused, and both files are named."""
    vsi, tif = pair
    both = tmp_path / "both"
    both.mkdir()
    for source in (vsi, vsi.with_name(vsi.name + ".frames.npy"), tif):
        (both / source.name).write_bytes(source.read_bytes())
    out = tmp_path / "out"
    assert main(["batch", str(both), "-d", str(out), "--no-idealize"]) != 0
    err = capsys.readouterr().err
    assert "one name in two containers" in err
    assert f"{_NAME}.vsi" in err and f"{_NAME}.tif" in err
    assert not out.exists() or not any(out.iterdir())


# --- the checksum covers the frame files (ADR-0153) -------------------------------------------


def _write_ets(vsi: Path, name: str, content: bytes) -> Path:
    """An ``.ets`` frame file in the ``_<name>_`` folder beside ``vsi``, as cellSens writes it."""
    ets = vsi.with_name(f"_{vsi.stem}_") / name
    ets.parent.mkdir(parents=True, exist_ok=True)
    ets.write_bytes(content)
    return ets


def test_a_changed_frame_file_is_a_new_movie_to_batch_into(
    pair: tuple[Path, Path], tmp_path: Path
) -> None:
    """The frames of a ``.vsi`` are in its ``.ets`` files, so they are part of its checksum.

    The checksum is the identity that ``tether batch --into`` finds a movie by. If it
    covered only the ``.vsi`` file, a movie whose ``.ets`` files changed would be
    "already in the project" and keep the traces of its old frames.
    """
    import h5py  # noqa: PLC0415

    from tether.project.batch_inputs import movie_in_project  # noqa: PLC0415
    from tether.project.sources import file_sha256  # noqa: PLC0415

    vsi, _ = pair
    ets = _write_ets(vsi, "stack1/frame_t.ets", b"frames, first acquisition")
    out = tmp_path / "project.tether"
    assert main(["extract", str(vsi), "-o", str(out), "--window", _WINDOW]) == 0
    with h5py.File(out, "r") as f:
        row = f["/movies/table"][0]
        stored = row["sha256"].decode()
        movie_id = row["movie_id"].decode()
    plain = hashlib.sha256(vsi.read_bytes()).hexdigest()
    assert stored != plain
    # The source registry's checksum job reads the same identity, so it agrees with /movies.
    assert file_sha256(vsi).sha256 == stored
    assert movie_in_project(out, vsi) == movie_id

    ets.write_bytes(b"frames, changed after the extraction")
    assert hashlib.sha256(vsi.read_bytes()).hexdigest() == plain  # the .vsi did not change
    assert file_sha256(vsi).sha256 != stored
    assert movie_in_project(out, vsi) is None


def test_the_frame_file_checksum_is_fixed_by_content_and_names(tmp_path: Path) -> None:
    from tether.io.movie_identity import content_sha256, frame_files  # noqa: PLC0415

    def checksum(vsi: Path) -> str:
        return content_sha256(vsi, hashlib.sha256(vsi.read_bytes()).hexdigest())

    vsi = _write_vsi(tmp_path / "a.vsi")
    plain = hashlib.sha256(vsi.read_bytes()).hexdigest()
    assert frame_files(vsi) == ()
    assert checksum(vsi) == plain  # no frame folder: the file's own SHA-256
    _write_ets(vsi, "stack10001/frame_t.ets", b"second")
    _write_ets(vsi, "stack1/frame_t.ets", b"first")
    _write_ets(vsi, "stack1/notes.txt", b"not a frame file")
    folder = tmp_path / "_a_"
    assert [p.relative_to(folder).as_posix() for p in frame_files(vsi)] == [
        "stack1/frame_t.ets",
        "stack10001/frame_t.ets",
    ]
    with_frames = checksum(vsi)
    assert with_frames != plain

    (folder / "stack1" / "notes.txt").write_bytes(b"changed, and still not a frame file")
    assert checksum(vsi) == with_frames
    (folder / "stack1" / "frame_t.ets").rename(folder / "stack1" / "frame_u.ets")
    assert checksum(vsi) != with_frames  # a frame file's name is part of the identity
    (folder / "stack1" / "frame_u.ets").rename(folder / "stack1" / "frame_t.ets")
    assert checksum(vsi) == with_frames

    # A .vsi renamed with its folder, as cellSens needs, keeps its checksum, as a TIFF does.
    vsi.rename(tmp_path / "b.vsi")
    folder.rename(tmp_path / "_b_")
    assert checksum(tmp_path / "b.vsi") == with_frames

    # A TIFF never has frame files, so its checksum is the file's own SHA-256.
    tif = tmp_path / "a.tif"
    tifffile.imwrite(tif, np.zeros((4, 4), dtype="u1"))
    _write_ets(tmp_path / "a.vsi", "stack1/frame_t.ets", b"beside a TIFF")
    assert content_sha256(tif, "AB" * 32) == "ab" * 32


# --- the reader's own refusals reach the user ---------------------------------------------------


def test_a_lossy_vsi_is_refused_with_the_readers_message(tmp_path: Path, fake_runner: None) -> None:
    message = "movie.vsi: the frames are stored with JPEG, which can be lossy"
    vsi = _write_vsi(tmp_path / "movie.vsi", refuse=["lossy", message])
    with pytest.raises(LossyCodecError, match="stored with JPEG"), open_extraction_movie(vsi):
        pass  # pragma: no cover


def test_tether_extract_names_the_readers_refusal(
    tmp_path: Path, fake_runner: None, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "movie.vsi has 2 channels and 1 focal planes; Tether reads one channel"
    vsi = _write_vsi(tmp_path / "movie.vsi", refuse=["unsupported", message])
    assert main(["extract", str(vsi), "-o", str(tmp_path / "out.tether")]) != 0
    assert message in capsys.readouterr().err
    with pytest.raises(UnsupportedContainerError, match="2 channels"), open_extraction_movie(vsi):
        pass  # pragma: no cover


def test_a_vsi_without_the_stack_gives_the_install_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(bioformats.BIOFORMATS_PYTHON_ENV, raising=False)
    vsi = _write_vsi(tmp_path / "movie.vsi", _movie_frames())
    with (
        pytest.raises(BioformatsUnavailableError, match="micromamba create"),
        open_extraction_movie(vsi),
    ):
        pass  # pragma: no cover
    assert main(["extract", str(vsi), "-o", str(tmp_path / "out.tether")]) != 0
    err = capsys.readouterr().err
    assert "needs the optional Bio-Formats stack" in err
    assert "extraction does not read" not in err
