# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's ZMW detector against Deep-LASI's own ``findPart`` mode 5 (#924, ADR-0143).

Deep-LASI at the pin of ``scripts/reference/matlab/pins.toml`` ran its own ``findPart`` in MATLAB
on two detection images (``scripts/reference/matlab/deep_lasi.py``, task ``findpart``):

* ``public.csv``, a crop of a detection image that Deep-LASI made and saved in the public
  ``Figure_2.zip`` of Zenodo record 7561162 (CC-BY-4.0), made by
  ``scripts/make_deeplasi_findpart_images.py``;
* ``synthetic.csv``, seven Gaussian spots, three of them on or next to the image edge.

``tests/fixtures/reference/deep-lasi/findpart-zmw/provenance.json`` is the record of that run, and
``tests/test_matlab_reference.py`` checks the SHA-256 of every frozen file. For each image and each
threshold of the run, the spots Deep-LASI found must be the spots Tether's
:func:`tether.imaging.detect.detect_spots_zmw` finds, to ``TOLERANCE_PX``.

What is compared is the mode, not the shared tail: the run passes ``findPart`` a background mask
of length 1, so its border width ``z`` is 1, and no max-pixel refinement. Tether's own tail
(snap, border, minimum separation) is a separate, documented choice of ADR-0022, so the test
takes Tether's candidates before that tail (``border_margin=0``, ``min_separation=0``,
``refine=False``) and applies Deep-LASI's ``z = 1`` tail to them (:func:`deeplasi_tail`).

The run also called mode 4 (local-variance) once; its frozen output is the error MATLAB itself
gave, the reason ADR-0143 keeps that mode refused.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.imaging.detect import detect_spots_zmw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "tests" / "fixtures" / "deep_lasi_findpart_images"
FROZEN = ROOT / "tests" / "fixtures" / "reference" / "deep-lasi" / "findpart-zmw"
MATLAB_DIR = ROOT / "scripts" / "reference" / "matlab"
PINS = MATLAB_DIR / "pins.toml"
IMAGE_NAMES = ("public", "synthetic")
#: Both sides compute the same centroids in double precision; the margin covers the order of
#: the sums only (MATLAB's regionprops differs from NumPy by at most 1.5e-14 on these images).
TOLERANCE_PX = 1e-9


def read_image(name: str) -> np.ndarray:
    return np.loadtxt(IMAGES / f"{name}.csv", delimiter=",", dtype=np.float64, ndmin=2)


def read_xy(path: Path) -> np.ndarray:
    """Deep-LASI's ``[row, col]`` (1-based) rows, as the task wrote them."""
    lines = path.read_text(encoding="ascii").splitlines()
    assert lines[0] == "row,col"
    rows = [tuple(float(v) for v in line.split(",")) for line in lines[1:] if line]
    return np.asarray(rows, dtype=np.float64).reshape(-1, 2)


def deeplasi_tail(xy: np.ndarray, shape: tuple[int, int], z: int = 1) -> np.ndarray:
    """``findPart.m:63-73`` and ``:103`` with border width ``z`` and no refinement.

    ``xy`` is ``[row, col]``, 1-based. It keeps the rows with at least one other spot farther
    than ``z`` (``XY(max(D>z),:)``, which removes a lone spot), drops spots closer than ``z`` to
    the first row or column or past ``size - z``, and keeps the first of each repeated row.
    """
    if xy.shape[0] == 0:
        return xy
    distance = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    xy = xy[(distance > z).any(axis=0)]
    height, width = shape
    inside = ~((xy.min(axis=1) < z) | (xy[:, 0] > height - z) | (xy[:, 1] > width - z))
    xy = xy[inside]
    _, first = np.unique(xy, axis=0, return_index=True)
    return xy[np.sort(first)]


def tether_candidates(image: np.ndarray, threshold: float) -> np.ndarray:
    """Tether's mode-5 spots before its own tail, as 1-based ``[row, col]``."""
    xy = detect_spots_zmw(
        image, threshold=threshold, min_separation=0.0, border_margin=0, refine=False
    )
    return np.column_stack([xy[:, 1], xy[:, 0]]) + 1.0


def _sorted(xy: np.ndarray) -> np.ndarray:
    return xy[np.lexsort((xy[:, 1], xy[:, 0]))] if xy.shape[0] else xy


def _record() -> dict:
    return json.loads((FROZEN / "provenance.json").read_text(encoding="utf-8"))


def _task_params() -> dict:
    spec = importlib.util.spec_from_file_location(
        "tether_ref_deep_lasi", MATLAB_DIR / "deep_lasi.py"
    )
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver.TASKS["findpart"]["params"]


def _cases() -> list[tuple[str, int, float]]:
    thresholds = _task_params()["thresholds"]
    return [(name, k, float(t)) for name in IMAGE_NAMES for k, t in enumerate(thresholds, start=1)]


def test_the_frozen_run_is_deep_lasi_at_its_pin_on_the_committed_images() -> None:
    record = _record()
    pin = tomllib.loads(PINS.read_text(encoding="utf-8"))["deep-lasi"]
    assert record["method"] == "matlab-batch"
    assert record["tool"]["name"] == "deep-lasi"
    assert record["tool"]["commit"] == pin["commit"]
    assert record["task"] == "findpart"
    assert record["params"] == _task_params()
    assert record["params"]["mode"] == 5
    assert record["params"]["refine"] is False
    assert record["inputs"]["images"] == {
        f"{name}.csv": hashlib.sha256((IMAGES / f"{name}.csv").read_bytes()).hexdigest()
        for name in IMAGE_NAMES
    }
    expected = {f"{n}_t{k}.csv" for n, k, _ in _cases()} | {"mode4_error.txt"}
    assert set(record["outputs"]) == expected


@pytest.mark.parametrize(("name", "k", "threshold"), _cases())
def test_tether_finds_the_spots_deep_lasi_mode_5_finds(name: str, k: int, threshold: float) -> None:
    image = read_image(name)
    reference = _sorted(read_xy(FROZEN / f"{name}_t{k}.csv"))
    ours = _sorted(deeplasi_tail(tether_candidates(image, threshold), image.shape))
    assert ours.shape == reference.shape, f"{name} t={threshold}: {ours.shape} vs {reference.shape}"
    if reference.shape[0]:
        assert np.max(np.abs(ours - reference)) <= TOLERANCE_PX


def test_the_comparison_is_not_empty() -> None:
    # A run that found nothing anywhere would pass the per-case test trivially.
    counts = {(n, k): read_xy(FROZEN / f"{n}_t{k}.csv").shape[0] for n, k, _ in _cases()}
    assert sum(counts.values()) >= 30
    # Every synthetic case keeps at least two spots (t = 0.5 keeps only (11, 11) and (11, 17)).
    assert all(counts[("synthetic", k)] >= 2 for _, k, _ in _cases())


def test_the_edge_padding_is_what_the_comparison_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fault injection: an erosion that pads with 1 (imerode's and Octave's bwmorph's
    # padding) keeps or moves the edge spots of the synthetic image, so the comparison above
    # would fail on it. MATLAB's bwmorph pads with 0, which is what Tether does.
    from scipy import ndimage

    import tether.imaging.detect as detect

    image = read_image("synthetic")
    threshold = _cases()[0][2]
    reference = _sorted(read_xy(FROZEN / "synthetic_t1.csv"))
    original = ndimage.binary_erosion

    def one_padded(binary, structure=None, border_value=0, **kwargs):
        return original(binary, structure=structure, border_value=1, **kwargs)

    monkeypatch.setattr(detect.ndimage, "binary_erosion", one_padded)
    broken = _sorted(deeplasi_tail(tether_candidates(image, threshold), image.shape))
    same = broken.shape == reference.shape and (
        not reference.shape[0] or np.max(np.abs(broken - reference)) <= TOLERANCE_PX
    )
    assert not same


def test_deep_lasi_mode_4_stops_with_an_error() -> None:
    message = (FROZEN / "mode4_error.txt").read_text(encoding="utf-8").strip()
    assert message != "no error"
    assert "'r'" in message


def test_deeplasi_tail_removes_a_lone_spot_and_the_far_edges() -> None:
    lone = np.array([[5.0, 5.0]])
    assert deeplasi_tail(lone, (10, 10)).shape == (0, 2)
    xy = np.array([[5.0, 5.0], [9.5, 3.0], [3.0, 3.0], [3.0, 3.0], [0.5, 4.0]])
    np.testing.assert_array_equal(deeplasi_tail(xy, (10, 10)), [[5.0, 5.0], [3.0, 3.0]])
