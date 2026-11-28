# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction reads SIF movies through ``read_frames`` (#1497, ADR-0149, ADR-0150).

A synthetic SIF movie of ``tests/_sif_synth.py`` and a TIFF of the same frames go through
``tether extract`` and through the service command that the application runs; each gives
the same traces, bit for bit. The frames are noise, so no native registration fits on
them: both movies are extracted with one saved map, fitted on a spot movie of the same
geometry, and the intensity detector. The map and the options are the same for both
movies, so any difference in the traces is a difference in the frames that extraction read.

A ``tether batch`` folder gives ``.sif`` files, a SIF beside its TIFF export is refused by
the rule of #1388, and a SIF that the reader refuses fails with the reader's message.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
tifffile = pytest.importorskip("tifffile")

from _sif_synth import write_sif  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.imaging.calibrate import read_calibration, save_map  # noqa: E402
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.io.adapters import UnsupportedContainerError, open_movie_source  # noqa: E402
from tether.io.frames import open_extraction_movie  # noqa: E402
from tether.io.movie import EXTRACTED_CONTAINERS  # noqa: E402
from tether.project.batch_inputs import expand_movie_arguments  # noqa: E402
from tether.project.extract import ExtractOptions, extract_movie  # noqa: E402

# A lab movie name, so the condition parsed from it is a real one (#1388's _NAME).
_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"
_SHAPE = (64, 64)  # two 32-px halves
_N_FRAMES = 32
_WINDOW = 17  # the smallest odd window that holds the 8-px background ring
# The frames are noise: the intensity detector with a low threshold finds spots in them,
# and donor-anchored colocalization keeps each donor spot whatever the acceptor shows.
_OPTIONS = ["--detection-mode", "intensity", "--detection-threshold", "0.3"]
_OPTIONS += ["--colocalization", "donor-anchored", "--window", str(_WINDOW)]
# A SIF file the reader refuses (two image areas in each frame), and the reader's words.
_REFUSED = {"sub_images": 2}
_REFUSAL = "holds 2 image areas in each frame"


@pytest.fixture(scope="module")
def frames() -> np.ndarray:
    """Whole counts as Solis stores them: little-endian 32-bit floats."""
    rng = np.random.default_rng(1497)
    return rng.integers(0, 16000, size=(_N_FRAMES, *_SHAPE)).astype("<f4")


@pytest.fixture(scope="module")
def channel_map(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A map fitted natively on a spot movie of the same geometry, saved to ``.npz``."""
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
def original(tmp_path: Path, frames: np.ndarray) -> Path:
    """The SIF movie under a lab movie name, in a folder of its own."""
    path = tmp_path / "sif" / f"{_NAME}.sif"
    path.parent.mkdir()
    return write_sif(path, frames)


@pytest.fixture
def tiff_export(tmp_path: Path, frames: np.ndarray) -> Path:
    """The TIFF of the same frames, under the same name, in a folder of its own."""
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


# --- extraction: the SIF movie and its TIFF give the same traces ------------------------


def test_sif_is_an_extracted_container() -> None:
    assert "SIF" in EXTRACTED_CONTAINERS


def test_extraction_reads_the_sif_frames_through_read_frames(
    original: Path, frames: np.ndarray
) -> None:
    with open_extraction_movie(original) as movie:
        assert movie.container == "SIF"
        assert movie.shape == frames.shape
        np.testing.assert_array_equal(movie.data, frames)


def test_tether_extract_gives_the_same_traces_for_the_sif_and_its_tiff(
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
    """The application runs the ``extract`` service command; it reads SIF the same way."""
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


# --- tether batch ----------------------------------------------------------------------


def test_a_batch_folder_gives_the_sif_movie(original: Path) -> None:
    assert expand_movie_arguments([str(original.parent)]) == (original,)


def test_tether_batch_refuses_a_sif_beside_its_tiff_export(
    original: Path, tiff_export: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``x.sif`` beside ``x.tif`` is one movie in two containers: the rule of #1388."""
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


# --- a SIF the reader refuses ----------------------------------------------------------


def test_a_refused_sif_fails_with_the_readers_message(
    frames: np.ndarray, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_sif(tmp_path / "refused.sif", frames[:4], **_REFUSED)
    out = tmp_path / "refused.tether"
    assert main(["extract", str(path), "-o", str(out)]) != 0
    err = capsys.readouterr().err
    assert _REFUSAL in err
    assert "extraction does not read" not in err
    assert not out.exists()


def test_the_refusal_is_the_one_the_reader_raises(frames: np.ndarray, tmp_path: Path) -> None:
    """Extraction adds nothing to the reader's refusal: the two messages are one."""
    path = write_sif(tmp_path / "refused.sif", frames[:4], **_REFUSED)
    with pytest.raises(UnsupportedContainerError) as direct:
        open_movie_source(path)
    with pytest.raises(UnsupportedContainerError) as extracted, open_extraction_movie(path):
        pass  # pragma: no cover
    assert str(extracted.value) == str(direct.value)
