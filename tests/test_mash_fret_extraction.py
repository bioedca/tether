# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's extraction against simulated truth from the MASH-FRET simulator (#714, ADR-0088).

MASH-FRET v1.3.6 (the pin of #700) made four two-channel videos with known molecule positions
and noise-free intensities: ``easy``, ``low-snr``, ``dense`` and ``uneven`` (a TIRF-like
background). ``scripts/reference/matlab/mash_fret_extraction_set.py`` holds every parameter and
seed; ``tests/fixtures/reference/mash-fret/<scenario>/provenance.json`` is the record of the
run, and ``tests/test_matlab_reference.py`` checks the SHA-256 of every frozen file.

The test runs Tether's own pipeline (:func:`tether.project.extract.extract_movie`, all
defaults) on each video and compares it with the truth:

* **detection** — of the molecules whose 21-px aperture fits in the channel, how many Tether
  found (a Tether molecule within ``MATCH_PX`` of the true donor position), and how many Tether
  molecules have no true molecule within ``MATCH_PX`` (false detections);
* **position** — the distance from each found molecule to its true position, in the donor
  channel and, through Tether's channel mapping, in the acceptor channel;
* **intensity** — for each found molecule with no other molecule within ``ISOLATION_PX`` (in
  ``dense``, for each found molecule), the
  ratio of Tether's corrected intensity summed over the video to the expected image counts
  (``ETA`` x the ideal photon count), and the Pearson correlation of the two traces, in each
  channel. The ratio is below 1 because the 3-px disk does not hold the whole PSF.

The limits in ``LIMITS`` come from the first measurement (ADR-0088 gives the values and the
margins) and are frozen. Two fault injections — an aperture moved by 2 px and a channel mapping
moved by 2 px — must each break a limit; they are the demonstrations of #714's third criterion.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

import tether.imaging.extract as imaging_extract  # noqa: E402
import tether.project.extract as project_extract  # noqa: E402
from tether.imaging.aperture import aperture_in_frame  # noqa: E402
from tether.imaging.extract import read_molecules, read_traces  # noqa: E402
from tether.project.extract import extract_movie  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mash_fret  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "reference" / "mash-fret"
SCENARIOS = ("easy", "low-snr", "dense", "uneven")

#: Quantum efficiency of MASH-FRET's "P" camera model (getDefault_S, camprm row 1; K = 1).
ETA = 0.95
#: A Tether molecule is the true one when its donor position is this close (px).
MATCH_PX = 1.5
#: A molecule counts for intensity only when no other one is this close: the ring's outer
#: radius (8 px) plus about one PSF width.
ISOLATION_PX = 10.0

# Frozen limits (ADR-0088), from the first measurement at 7ec756a. found: at least this many of
# the eligible molecules (the measured count); false: at most this many (the measured count);
# donor_err / acceptor_err: the largest position error (px), the measured largest + 0.1 px,
# rounded up to 0.05; gain: the measured median intensity ratio +/- 0.03, for each channel;
# min_r: the lowest per-trace Pearson correlation, the measured lowest - 0.05, rounded down to
# 0.05. Every molecule Tether missed has a true neighbour within 7.4 px (ADR-0088).
LIMITS: dict[str, dict] = {
    "easy": {
        "found": 4,
        "false": 0,
        "donor_err": 0.6,
        "acceptor_err": 0.55,
        "gain": {"donor": (0.887, 0.947), "acceptor": (0.883, 0.943)},
        "min_r": {"donor": 0.8, "acceptor": 0.75},
    },
    "low-snr": {
        "found": 6,
        "false": 0,
        "donor_err": 1.1,
        "acceptor_err": 3.2,
        "gain": {"donor": (0.845, 0.905), "acceptor": (0.904, 0.964)},
        "min_r": {"donor": 0.25, "acceptor": 0.3},
    },
    "dense": {
        "found": 7,
        "false": 1,
        "donor_err": 1.1,
        "acceptor_err": 2.2,
        "gain": {"donor": (1.048, 1.108), "acceptor": (0.701, 0.761)},
        "min_r": {"donor": 0.35, "acceptor": 0.4},
    },
    "uneven": {
        "found": 7,
        "false": 0,
        "donor_err": 0.75,
        "acceptor_err": 0.75,
        "gain": {"donor": (0.935, 0.995), "acceptor": (0.862, 0.922)},
        "min_r": {"donor": 0.75, "acceptor": 0.65},
    },
}


def _load_set_script():
    import importlib.util

    path = ROOT / "scripts" / "reference" / "matlab" / "mash_fret_extraction_set.py"
    spec = importlib.util.spec_from_file_location("tether_mash_extraction_set", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Measured:
    n_eligible: int
    n_found: int
    n_false: int
    donor_err: np.ndarray
    acceptor_err: np.ndarray
    gain: dict[str, np.ndarray]
    corr: dict[str, np.ndarray]


def _pairwise(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])


def measure(name: str, work: Path, *, isolated_only: bool = True) -> Measured:
    """Run Tether's extraction on the video of ``name`` and compare it with the truth.

    ``isolated_only`` limits the intensity comparison to molecules with no other one within
    ``ISOLATION_PX``; the ``dense`` scenario turns it off, because crowding is what it tests.
    """
    run = FIXTURES / name
    video = _mash_fret.read_sira(run / f"{name}.sira.gz")
    n_frames, height, width = video.frames.shape
    half = width // 2
    truth = _mash_fret.read_truth(run, name, channel_width=half, n_frames=n_frames)
    movie = _mash_fret.write_movie_tiff(video, work / f"{name}.tif")
    project = work / f"{name}.tether"
    extract_movie(movie, project, overwrite=True)
    molecules = read_molecules(project)
    traces = read_traces(project)

    eligible = aperture_in_frame(truth.donor_xy, shape=(height, half))
    found_xy = np.asarray(molecules["donor_xy"], dtype=float).reshape(-1, 2)
    if len(found_xy) == 0:
        empty = np.empty(0)
        return Measured(int(eligible.sum()), 0, 0, empty, empty, {}, {})
    dist = _pairwise(found_xy, truth.donor_xy)
    nearest = dist.argmin(axis=1)
    matched = dist[np.arange(len(found_xy)), nearest] <= MATCH_PX
    hit = np.zeros(len(truth.donor_xy), dtype=bool)
    hit[nearest[matched]] = True

    acceptor_xy = np.asarray(molecules["acceptor_xy"], dtype=float).reshape(-1, 2)
    rows = np.flatnonzero(matched)
    donor_err = np.hypot(*(found_xy[rows] - truth.donor_xy[nearest[rows]]).T)
    acceptor_err = np.hypot(*(acceptor_xy[rows] - truth.acceptor_xy[nearest[rows]]).T)

    others = _pairwise(truth.donor_xy, truth.donor_xy)
    np.fill_diagonal(others, np.inf)
    isolated = others.min(axis=1) > ISOLATION_PX
    gain: dict[str, list[float]] = {"donor": [], "acceptor": []}
    corr: dict[str, list[float]] = {"donor": [], "acceptor": []}
    for row in rows:
        mol = nearest[row]
        if isolated_only and not isolated[mol]:
            continue
        for channel, ideal in (("donor", truth.donor_ideal), ("acceptor", truth.acceptor_ideal)):
            got = traces[f"{channel}_corrected"][row, :n_frames]
            want = ETA * ideal[mol]
            gain[channel].append(float(got.sum() / want.sum()))
            corr[channel].append(float(np.corrcoef(got, want)[0, 1]))
    return Measured(
        n_eligible=int(eligible.sum()),
        n_found=int((hit & eligible).sum()),
        n_false=int((~matched).sum()),
        donor_err=donor_err,
        acceptor_err=acceptor_err,
        gain={k: np.asarray(v) for k, v in gain.items()},
        corr={k: np.asarray(v) for k, v in corr.items()},
    )


def violations(result: Measured, limits: dict) -> list[str]:
    """Every frozen limit ``result`` breaks, as text; empty when it keeps them all."""
    found: list[str] = []
    if result.n_found < limits["found"]:
        found.append(f"found {result.n_found} of {result.n_eligible}, limit {limits['found']}")
    if result.n_false > limits["false"]:
        found.append(f"false detections {result.n_false}, limit {limits['false']}")
    # Every comparison is written so that NaN breaks it: a NaN (a constant trace gives a NaN
    # correlation) must never read as a kept limit.
    for what in ("donor_err", "acceptor_err"):
        values = getattr(result, what)
        worst = float(values.max()) if values.size else np.inf
        if not worst <= limits[what]:
            found.append(f"{what} {worst:.3f} px, limit {limits[what]}")
    for channel in ("donor", "acceptor"):
        ratios = result.gain.get(channel, np.empty(0))
        low, high = limits["gain"][channel]
        median = float(np.median(ratios)) if ratios.size else np.nan
        if not low <= median <= high:
            found.append(f"{channel} gain {median:.3f}, limit [{low}, {high}]")
        rs = result.corr.get(channel, np.empty(0))
        lowest = float(rs.min()) if rs.size else -np.inf
        if not lowest >= limits["min_r"][channel]:
            found.append(f"{channel} lowest r {lowest:.3f}, limit {limits['min_r'][channel]}")
    return found


def test_a_nan_breaks_a_limit() -> None:
    """A NaN position error, ratio or correlation is a broken limit, never a kept one."""
    good = Measured(
        n_eligible=6,
        n_found=4,
        n_false=0,
        donor_err=np.array([0.1, 0.2]),
        acceptor_err=np.array([0.1, 0.2]),
        gain={"donor": np.array([0.91, 0.92]), "acceptor": np.array([0.91, 0.92])},
        corr={"donor": np.array([0.9, 0.95]), "acceptor": np.array([0.9, 0.95])},
    )
    assert violations(good, LIMITS["easy"]) == []
    with np.errstate(invalid="ignore"):
        constant = np.corrcoef(np.ones(5), np.arange(5.0))[0, 1]
    assert np.isnan(constant)
    nan_r = replace(good, corr={**good.corr, "donor": np.array([constant, 0.95])})
    assert [v for v in violations(nan_r, LIMITS["easy"]) if v.startswith("donor lowest r")]
    nan_err = replace(good, acceptor_err=np.array([np.nan, 0.2]))
    assert [v for v in violations(nan_err, LIMITS["easy"]) if v.startswith("acceptor_err")]
    nan_gain = replace(good, gain={**good.gain, "acceptor": np.array([np.nan])})
    assert [v for v in violations(nan_gain, LIMITS["easy"]) if v.startswith("acceptor gain")]


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_frozen_run_is_the_committed_scenario(name: str) -> None:
    """The record of each video names the parameters and seed the set script holds now."""
    record = json.loads((FIXTURES / name / "provenance.json").read_text(encoding="utf-8"))
    script = _load_set_script()
    assert record["tool"]["name"] == "mash-fret"
    assert record["task"] == "simulate"
    assert record["params"] == {
        **_load_mash_driver().TASKS["simulate"]["params"],
        **script.params_for(name),
    }


def _load_mash_driver():
    import importlib.util

    path = ROOT / "scripts" / "reference" / "matlab" / "mash_fret.py"
    spec = importlib.util.spec_from_file_location("tether_mash_fret_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", SCENARIOS)
def test_video_converts_exactly(name: str, tmp_path: Path) -> None:
    """The .sira decodes to whole image counts, and the TIFF holds the same numbers."""
    import tifffile

    video = _mash_fret.read_sira(FIXTURES / name / f"{name}.sira.gz")
    path = _mash_fret.write_movie_tiff(video, tmp_path / "m.tif")
    with tifffile.TiffFile(path) as tif:
        assert tif.byteorder == ">"
        assert tif.pages[0].dtype == np.dtype("uint16")
        stored = tif.asarray()
    np.testing.assert_array_equal(stored.astype(np.float64), video.frames.astype(np.float64))


@pytest.mark.parametrize("name", SCENARIOS)
def test_extraction_keeps_the_frozen_limits(name: str, tmp_path: Path) -> None:
    result = measure(name, tmp_path, isolated_only=name != "dense")
    assert violations(result, LIMITS[name]) == []


def _shift_aperture(monkeypatch: pytest.MonkeyPatch) -> None:
    original = imaging_extract.integrate_traces

    def shifted(movie, coords, **kwargs):
        return original(movie, np.asarray(coords, dtype=float) + [2.0, 0.0], **kwargs)

    monkeypatch.setattr(imaging_extract, "integrate_traces", shifted)


def _break_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    original = project_extract.colocalize

    def broken(*args, **kwargs):
        molecules = original(*args, **kwargs)
        return replace(molecules, acceptor_xy=molecules.acceptor_xy + [0.0, 2.0])

    monkeypatch.setattr(project_extract, "colocalize", broken)


@pytest.mark.parametrize(
    ("fault", "expected"),
    [(_shift_aperture, "donor gain"), (_break_mapping, "acceptor_err")],
    ids=["aperture-moved-2px", "mapping-moved-2px"],
)
def test_a_known_fault_breaks_a_limit(fault, expected, monkeypatch, tmp_path) -> None:
    """The oracle is not blind: each fault of #714's third criterion breaks a frozen limit."""
    fault(monkeypatch)
    broken = violations(measure("easy", tmp_path), LIMITS["easy"])
    assert any(text.startswith(expected) for text in broken), broken
