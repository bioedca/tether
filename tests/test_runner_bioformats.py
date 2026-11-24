# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The live Bio-Formats runner (#907, ADR-0147): the ``bioformats / live`` job.

These tests start ``src/tether/io/_bioformats_runner.py`` with the Python of the optional
stack (``$TETHER_BIOFORMATS_PYTHON``) and read movies through Java and Bio-Formats. No
redistributable cellSens ``.vsi`` movie is known (ADR-0147), so the movies here are OME-TIFF
files that tifffile writes: Bio-Formats reads them with its own reader, and the frames must
come back exactly, with the geometry, the byte order and the metadata of the file. The
``.vsi`` route itself is checked on the maintainer's workstation
(``tests/test_vsi_direct_read.py``).

The job runs in the base environment (the client side needs NumPy and tifffile); only the
runner process uses the stack.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from _bioformats_gate import requires_bioformats

pytestmark = pytest.mark.bioformats

np = pytest.importorskip("numpy")
tifffile = pytest.importorskip("tifffile")

from tether.io.adapters import ORIGINS, MovieSource  # noqa: E402
from tether.io.bioformats import (  # noqa: E402
    BioformatsUnavailableError,
    open_bioformats_source,
    runner_info,
)

if TYPE_CHECKING:
    from pathlib import Path

_N, _H, _W = 24, 32, 40


def _ome_movie(tmp_path: Path, dtype: str, name: str = "movie.ome.tif") -> tuple[Path, np.ndarray]:
    rng = np.random.default_rng(907)
    frames = rng.integers(0, 4000, size=(_N, _H, _W)).astype(dtype)
    path = tmp_path / name
    tifffile.imwrite(
        path,
        frames,
        ome=True,
        metadata={
            "axes": "TYX",
            "PhysicalSizeX": 0.16,
            "PhysicalSizeXUnit": "µm",
            "PhysicalSizeY": 0.16,
            "PhysicalSizeYUnit": "µm",
            "TimeIncrement": 0.1,
            "TimeIncrementUnit": "s",
            "Channel": {"Name": ["Cy3"]},
        },
    )
    return path, frames


@requires_bioformats
def test_the_runner_reports_bio_formats_and_java() -> None:
    info = runner_info()
    assert info["bioformats"].startswith("8.0."), info
    assert int(info["java"].split(".")[0]) >= 17, info
    assert info["python"].startswith("3.12."), info


# The gate replaces a test by a stand-in without arguments where the stack is required and
# absent, so the live tests loop over their cases instead of taking `parametrize` arguments.


@requires_bioformats
def test_a_movie_comes_back_exactly(tmp_path: Path) -> None:
    for dtype in (">u2", "<u2", "u1", "<f4"):
        path, frames = _ome_movie(tmp_path, dtype, name=f"movie-{dtype[-2:]}{len(dtype)}.ome.tif")
        with open_bioformats_source(path) as source:
            assert isinstance(source, MovieSource)
            assert source.shape == frames.shape
            assert np.dtype(source.pixel_type) == frames.dtype, dtype
            assert source.format == "OME-TIFF"
            for start, stop in [(0, 1), (7, 11), (_N - 1, _N), (0, _N), (5, 5)]:
                got = source.read_frames(start, stop)
                np.testing.assert_array_equal(got, frames[start:stop])


@requires_bioformats
def test_the_metadata_comes_from_the_file(tmp_path: Path) -> None:
    path, _ = _ome_movie(tmp_path, "<u2")
    with open_bioformats_source(path) as source:
        meta = source.metadata
    assert meta.pixel_size.origin == "file"
    assert meta.pixel_size.value == pytest.approx(0.16)
    assert meta.frame_time.origin == "file"
    assert meta.frame_time.value == pytest.approx(0.1)
    assert meta.channels.value == ("Cy3",)
    for name in ("frame_time", "pixel_size", "channels"):
        assert getattr(meta, name).origin in ORIGINS


@requires_bioformats
def test_a_range_outside_is_refused(tmp_path: Path) -> None:
    path, _ = _ome_movie(tmp_path, "<u2")
    with open_bioformats_source(path) as source:
        for start, stop in [(-1, 2), (3, 2), (0, _N + 1)]:
            with pytest.raises(IndexError):
                source.read_frames(start, stop)


@requires_bioformats
def test_a_closed_source_refuses_a_read(tmp_path: Path) -> None:
    path, _ = _ome_movie(tmp_path, "<u2")
    source = open_bioformats_source(path)
    source.read_frames(0, 1)
    source.close()
    source.close()
    with pytest.raises(ValueError, match="closed"):
        source.read_frames(0, 1)


@requires_bioformats
def test_color_pixels_are_refused(tmp_path: Path) -> None:
    from tether.io.adapters import InvalidPixelError

    path = tmp_path / "rgb.ome.tif"
    tifffile.imwrite(
        path, np.zeros((3, 16, 16, 3), dtype="u1"), ome=True, photometric="rgb",
        metadata={"axes": "TYXS"},
    )  # fmt: skip
    with pytest.raises(InvalidPixelError, match="color"):
        open_bioformats_source(path)


@requires_bioformats
def test_a_file_bio_formats_cannot_read_is_refused(tmp_path: Path) -> None:
    from tether.io.adapters import UnsupportedContainerError

    path = tmp_path / "noise.vsi"
    path.write_bytes(b"not a cellSens file at all")
    with pytest.raises(UnsupportedContainerError):
        open_bioformats_source(path)


@requires_bioformats
def test_a_missing_jar_is_the_install_message(tmp_path: Path, monkeypatch) -> None:
    path, _ = _ome_movie(tmp_path, "<u2")
    monkeypatch.setenv("TETHER_BIOFORMATS_JAR", str(tmp_path / "absent.jar"))
    with pytest.raises(BioformatsUnavailableError, match="micromamba create"):
        open_bioformats_source(path)


@requires_bioformats
def test_the_runner_imports_no_tether(tmp_path: Path) -> None:
    """The stack holds no ``tether``; the runner must not need one (it runs under ``-I``)."""
    import subprocess

    from tether.io import bioformats

    code = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe'); import tether"
    done = subprocess.run(
        [bioformats.bioformats_python(), "-I", "-c", code, str(bioformats.RUNNER_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0 and "No module named 'tether'" in done.stderr, done.stderr
    assert sys.executable != str(bioformats.bioformats_python())
