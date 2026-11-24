# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction reads CZI movies through ``read_frames`` (#1390, ADR-0144, ADR-0149).

The committed Zstd fixture ``tests/fixtures/czi/movie_zstd_64x64x64.czi`` and a TIFF of the
same frames go through ``tether extract`` and through the service command that the
application runs; each gives the same traces, bit for bit. The fixture's frames are noise
(``scripts/make_czi_fixtures.py``), so no native registration fits on them: both movies
are extracted with one saved map, fitted on a spot movie of the same geometry, and the
intensity detector. The map and the options are the same for both movies, so any
difference in the traces is a difference in the frames that extraction read.

A ``tether batch`` folder gives ``.czi`` files, a CZI beside its TIFF export is refused
by the rule of #1388, and a CZI that the reader refuses fails with the reader's message.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("czifile")
tifffile = pytest.importorskip("tifffile")

from tether.cli import main  # noqa: E402
from tether.imaging.calibrate import read_calibration, save_map  # noqa: E402
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.io.adapters import open_movie_source  # noqa: E402
from tether.io.czi import CziMovie  # noqa: E402
from tether.io.frames import open_extraction_movie  # noqa: E402
from tether.project.batch_inputs import expand_movie_arguments  # noqa: E402
from tether.project.extract import ExtractOptions, extract_movie  # noqa: E402

CZI = Path(__file__).resolve().parent / "fixtures" / "czi"
MOVIE = CZI / "movie_zstd_64x64x64.czi"

# A lab movie name, so the condition parsed from it is a real one (#1388's _NAME).
_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"
_SHAPE = (64, 64)  # the fixture's frame: two 32-px halves
_WINDOW = 17  # the smallest odd window that holds the 8-px background ring
# The fixture is noise: the intensity detector with a low threshold finds spots in it,
# and donor-anchored colocalization keeps each donor spot whatever the acceptor shows.
_OPTIONS = ["--detection-mode", "intensity", "--detection-threshold", "0.3"]
_OPTIONS += ["--colocalization", "donor-anchored", "--window", str(_WINDOW)]


@pytest.fixture(scope="module")
def frames() -> np.ndarray:
    with CziMovie(MOVIE) as movie:
        return movie.read_frames(0, movie.n_frames)


@pytest.fixture(scope="module")
def channel_map(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A map fitted natively on a spot movie of the fixture's geometry, saved to ``.npz``."""
    folder = tmp_path_factory.mktemp("map")
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    donor = np.array([[8.0, 10.0], [16.0, 30.0], [24.0, 50.0], [10.0, 44.0]])
    frame = np.full(_SHAPE, 80.0)
    for x, y in np.vstack([donor, donor + np.array([33.0, 0.0])]):
        frame += 400.0 * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * 1.5**2))
    stack = np.ascontiguousarray(np.broadcast_to(frame, (12, *_SHAPE)), dtype=">u2")
    calibration = folder / "calibration.tif"
    tifffile.imwrite(calibration, stack, photometric="minisblack", byteorder=">")
    project = folder / "calibration.tether"
    summary = extract_movie(calibration, project, options=ExtractOptions(window=_WINDOW))
    return save_map(read_calibration(project, summary.calibration_id), folder / "map.npz")


@pytest.fixture
def original(tmp_path: Path) -> Path:
    """The committed fixture under a lab movie name, in a folder of its own."""
    path = tmp_path / "czi" / f"{_NAME}.czi"
    path.parent.mkdir()
    path.write_bytes(MOVIE.read_bytes())
    return path


@pytest.fixture
def tiff_export(tmp_path: Path, frames: np.ndarray) -> Path:
    """The TIFF of the fixture's frames, under the same name, in a folder of its own."""
    path = tmp_path / "tif" / f"{_NAME}.tif"
    path.parent.mkdir()
    tifffile.imwrite(path, frames, photometric="minisblack")
    return path


def _assert_same_extraction(a: Path, b: Path) -> None:
    traces_a, traces_b = read_traces(a), read_traces(b)
    assert sorted(traces_a) == sorted(traces_b)
    for name in traces_a:
        assert traces_a[name].dtype == traces_b[name].dtype, name
        np.testing.assert_array_equal(traces_a[name], traces_b[name], err_msg=name)
    mols_a, mols_b = read_molecules(a), read_molecules(b)
    np.testing.assert_array_equal(mols_a["donor_xy"], mols_b["donor_xy"])
    np.testing.assert_array_equal(mols_a["acceptor_xy"], mols_b["acceptor_xy"])
    # One condition, parsed from the movie's name with its container suffix stripped.
    assert set(mols_a["condition_id"]) == set(mols_b["condition_id"])
    assert len(set(mols_a["condition_id"])) == 1


# --- extraction: the CZI fixture and its TIFF give the same traces ----------------------


def test_extraction_reads_the_czi_frames_through_read_frames(frames: np.ndarray) -> None:
    with open_extraction_movie(MOVIE) as movie:
        assert movie.container == "CZI"
        assert movie.shape == frames.shape
        np.testing.assert_array_equal(movie.data, frames)


def test_tether_extract_gives_the_same_traces_for_the_czi_and_its_tiff(
    original: Path, tiff_export: Path, channel_map: Path, tmp_path: Path
) -> None:
    outs = []
    for movie in (original, tiff_export):
        out = tmp_path / f"from-{movie.suffix[1:]}.tether"
        args = ["extract", str(movie), "-o", str(out), "--map", str(channel_map), *_OPTIONS]
        assert main(args) == 0
        outs.append(out)
    assert len(read_molecules(outs[0])) > 0, "the comparison must hold molecules"
    _assert_same_extraction(*outs)


def test_the_application_command_gives_the_same_traces(
    original: Path, tiff_export: Path, channel_map: Path, tmp_path: Path
) -> None:
    """The application runs the ``extract`` service command; it reads CZI the same way."""
    from tether.services.extract import ExtractRequest, run  # noqa: PLC0415

    outs = []
    for movie in (original, tiff_export):
        out = tmp_path / f"app-{movie.suffix[1:]}.tether"
        request = ExtractRequest(
            movie=str(movie),
            output=str(out),
            map_path=str(channel_map),
            detection_mode="intensity",
            detection_threshold=0.3,
            colocalization="donor-anchored",
            window=_WINDOW,
        )
        result = run(request)
        assert result.summary is not None
        assert result.summary.n_molecules > 0
        outs.append(out)
    _assert_same_extraction(*outs)


def test_the_movie_table_records_the_czi(channel_map: Path, tmp_path: Path) -> None:
    """The movie row names the CZI and its frames; the frame time it lacks stays unset."""
    import h5py  # noqa: PLC0415

    out = tmp_path / "meta.tether"
    args = ["extract", str(MOVIE), "-o", str(out), "--map", str(channel_map), *_OPTIONS]
    assert main(args) == 0
    with h5py.File(out, "r") as f:
        row = f["/movies/table"][0]
    assert row["uri"].decode().endswith(".czi")
    assert (int(row["n_frames"]), int(row["height"]), int(row["width"])) == (64, *_SHAPE)
    assert row["pixel_dtype"] == b"uint16"
    assert row["frame_time"] == 0.0  # the fixture holds no time increment


# --- tether batch ----------------------------------------------------------------------


def test_a_batch_folder_gives_the_czi_movie(tmp_path: Path) -> None:
    (tmp_path / MOVIE.name).write_bytes(MOVIE.read_bytes())
    assert expand_movie_arguments([str(tmp_path)]) == (tmp_path / MOVIE.name,)


def test_tether_batch_refuses_a_czi_beside_its_tiff_export(
    original: Path, tiff_export: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``x.czi`` beside ``x.tif`` is one movie in two containers: the rule of #1388."""
    both = tmp_path / "both"
    both.mkdir()
    (both / original.name).write_bytes(original.read_bytes())
    (both / tiff_export.name).write_bytes(tiff_export.read_bytes())
    out = tmp_path / "out"
    assert main(["batch", str(both), "-d", str(out), "--no-idealize"]) != 0
    err = capsys.readouterr().err
    assert "one name in two containers" in err
    assert original.name in err
    assert tiff_export.name in err
    assert not out.exists() or not any(out.iterdir())


# --- a CZI the reader refuses ----------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "message"),
    [("two_channels.czi", "holds 2 channels"), ("z_stack.czi", "holds 2 ")],
)
def test_a_refused_czi_fails_with_the_readers_message(
    fixture: str, message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "refused.tether"
    assert main(["extract", str(CZI / fixture), "-o", str(out)]) != 0
    err = capsys.readouterr().err
    assert message in err
    assert "Tether reads a CZI file only as one movie" in err
    assert "extraction does not read" not in err
    assert not out.exists()


def test_the_refusal_is_the_one_the_reader_raises(tmp_path: Path) -> None:
    """Extraction adds nothing to the reader's refusal: the two messages are one."""
    from tether.io.adapters import UnsupportedContainerError  # noqa: PLC0415

    path = CZI / "two_channels.czi"
    with pytest.raises(UnsupportedContainerError) as direct:
        open_movie_source(path)
    with pytest.raises(UnsupportedContainerError) as extracted, open_extraction_movie(path):
        pass  # pragma: no cover
    assert str(extracted.value) == str(direct.value)
