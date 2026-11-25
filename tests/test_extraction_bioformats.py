# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction through the live Bio-Formats runner (#1398, ADR-0147, ADR-0149).

The ``bioformats / live`` job runs it.

No redistributable cellSens ``.vsi`` movie is known (ADR-0147), so the movie here is an
OME-TIFF that tifffile writes, read by Bio-Formats' own reader through the runner, as a
``.vsi`` is. Extraction chooses its reader with the registry, which would send an OME-TIFF
to the TIFF reader, so each test sends it to the cellSens adapter
(``BIOFORMATS_ADAPTER``) instead; everything after the adapter is
the path a ``.vsi`` takes. The same frames read by the TIFF reader must extract to the same
traces, bit for bit.

The second test measures what one ``read_frames`` call through the runner costs on the
64-frame blocks that extraction reads (``READ_BLOCK_FRAMES``), on a 512 x 512 ``uint16``
movie, against the TIFF reader on the same frames (#1398). It records the numbers in the
job summary and as a notice in the log, and asserts only that the frames are the same: the
time of a shared runner is not a pass or fail condition.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from typing import TYPE_CHECKING

import pytest

from _bioformats_gate import requires_bioformats

pytestmark = pytest.mark.bioformats

np = pytest.importorskip("numpy")
tifffile = pytest.importorskip("tifffile")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")

from tether.io.adapters import open_movie_source  # noqa: E402
from tether.io.bioformats import BIOFORMATS_ADAPTER, open_bioformats_source  # noqa: E402
from tether.io.frames import READ_BLOCK_FRAMES  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

_SHAPE = (64, 96)  # two 48-px halves: room for the default 21-px aperture
_N_FRAMES = 12
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_ACCEPTOR = _DONOR + np.array([49.0, 0.0])
_WINDOW = "21"


def _spot_movie() -> np.ndarray:
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    rng = np.random.default_rng(1398)
    frames = np.empty((_N_FRAMES, *_SHAPE), dtype=np.uint16)
    for t in range(_N_FRAMES):
        frame = np.full(_SHAPE, 80.0)
        for x, y in np.vstack([_DONOR, _ACCEPTOR]):
            frame += 400.0 * (1.0 + 0.05 * t) * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / 4.5)
        frames[t] = np.round(frame + rng.normal(0.0, 3.0, _SHAPE)).astype(np.uint16)
    return frames


def _through_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send every movie that extraction opens to the cellSens adapter (the runner)."""
    monkeypatch.setattr("tether.io.frames.select_adapter", lambda _path: BIOFORMATS_ADAPTER)


@requires_bioformats
def test_the_runner_and_the_tiff_reader_extract_the_same_traces(tmp_path: Path) -> None:
    from tether.cli import main
    from tether.imaging.extract import read_molecules, read_traces

    frames = _spot_movie()
    ome = tmp_path / "movie.ome.tif"
    tifffile.imwrite(ome, frames.astype(">u2"), ome=True, metadata={"axes": "TYX"})
    tif = tmp_path / "movie.tif"
    tifffile.imwrite(tif, frames.astype("<u2"), photometric="minisblack")
    out_runner, out_tiff = tmp_path / "runner.tether", tmp_path / "tiff.tether"
    with pytest.MonkeyPatch.context() as mp:
        _through_the_runner(mp)
        assert main(["extract", str(ome), "-o", str(out_runner), "--window", _WINDOW]) == 0
    assert main(["extract", str(tif), "-o", str(out_tiff), "--window", _WINDOW]) == 0

    assert len(read_molecules(out_runner)) == 3
    a, b = read_traces(out_runner), read_traces(out_tiff)
    assert sorted(a) == sorted(b)
    for name in a:
        assert a[name].dtype == b[name].dtype, name
        np.testing.assert_array_equal(a[name], b[name], err_msg=name)
    np.testing.assert_array_equal(
        read_molecules(out_runner)["donor_xy"], read_molecules(out_tiff)["donor_xy"]
    )


#: The measured movie: 512 x 512 uint16, as the issue asks, and four blocks of 64 frames.
_BENCH_FRAMES, _BENCH_SIDE = 4 * READ_BLOCK_FRAMES, 512
#: The block sizes timed through the runner, to separate the cost of a call from that of a frame.
_BLOCKS = (1, 16, READ_BLOCK_FRAMES, 4 * READ_BLOCK_FRAMES)


def _seconds_per_frame(source, block: int, n_frames: int) -> tuple[float, float]:
    """Read ``n_frames`` in blocks of ``block``; return (median s per call, s per frame)."""
    calls = []
    start_all = time.perf_counter()
    for start in range(0, n_frames, block):
        t0 = time.perf_counter()
        np.asarray(source.read_frames(start, min(start + block, n_frames))).sum()
        calls.append(time.perf_counter() - t0)
    total = time.perf_counter() - start_all
    return statistics.median(calls), total / n_frames


@requires_bioformats
def test_the_cost_of_a_runner_read_is_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rng = np.random.default_rng(1398)
    frames = rng.integers(0, 4000, size=(_BENCH_FRAMES, _BENCH_SIDE, _BENCH_SIDE)).astype("<u2")
    ome = tmp_path / "bench.ome.tif"
    tifffile.imwrite(ome, frames, ome=True, metadata={"axes": "TYX"})
    tif = tmp_path / "bench.tif"
    tifffile.imwrite(tif, frames, photometric="minisblack")

    results: dict[str, object] = {
        "frames": _BENCH_FRAMES,
        "height": _BENCH_SIDE,
        "width": _BENCH_SIDE,
        "dtype": "uint16",
    }
    with open_movie_source(tif) as tiff_source:
        _seconds_per_frame(tiff_source, READ_BLOCK_FRAMES, _BENCH_FRAMES)  # warm the page cache
        tiff_call, tiff_frame = _seconds_per_frame(tiff_source, READ_BLOCK_FRAMES, _BENCH_FRAMES)
    with open_bioformats_source(ome) as source:
        np.testing.assert_array_equal(source.read_frames(0, READ_BLOCK_FRAMES), frames[:64])
        _seconds_per_frame(source, READ_BLOCK_FRAMES, _BENCH_FRAMES)  # warm the JVM and cache
        runner: dict[int, tuple[float, float]] = {
            block: _seconds_per_frame(source, block, _BENCH_FRAMES) for block in _BLOCKS
        }
    block_call, block_frame = runner[READ_BLOCK_FRAMES]
    results["tiff_s_per_64_block"] = round(tiff_call, 5)
    results["tiff_frames_per_s"] = round(1.0 / tiff_frame, 1)
    results["runner_s_per_64_block"] = round(block_call, 5)
    results["runner_frames_per_s"] = round(1.0 / block_frame, 1)
    results["runner_over_tiff_per_frame"] = round(block_frame / tiff_frame, 2)
    results["runner_by_block"] = {
        str(block): {"s_per_call": round(call, 5), "frames_per_s": round(1.0 / per, 1)}
        for block, (call, per) in runner.items()
    }
    line = json.dumps(results, sort_keys=True)
    # The live job runs pytest with -q, which hides a passing test's output: write the numbers
    # to the job summary and as a notice in the log, where the pull request can quote them.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"### read_frames through the Bio-Formats runner (#1398)\n\n```json\n{line}\n```\n"
            )
    with capsys.disabled():
        print(f"\n::notice title=bioformats read cost (#1398)::{line}")
    assert block_frame > 0 and tiff_frame > 0
