# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's FRET histogram against the Ha laboratory's own output (#796, ADR-0086).

The reference is the ligand-free SAM/SAH riboswitch snapshot set of Fig. 1 of Liao et al.,
Nucleic Acids Res. 51:8957 (2023), doi:10.1093/nar/gkad633, deposited as Zenodo
10.5281/zenodo.8088172 (CC-BY-4.0). From the folder ``20200129 RNA only condition/RNA only/`` of
the Fig. 1 archive, ``tests/fixtures/ha_sam_sah/`` holds verbatim copies of the 53 short
``helN.traces`` files (20 frames each), the laboratory's FRET values ``frets.dat``, its normalised
histogram ``test3.dat`` and the record its script wrote of its parameters, ``counts2.txt``.

Two comparisons, because the rule that turns the traces into ``frets.dat`` was not found
(ADR-0086 records the search):

* **Exact.** Tether's histogram of ``frets.dat`` equals ``test3.dat`` to the precision of the
  file, after one exact conversion of MATLAB's ``hist`` convention.
* **Peaks.** Tether's FRET values from the traces themselves give a histogram whose two peaks
  are within :data:`PEAK_TOLERANCE` of the two peaks of ``test3.dat``.

The reader lives in ``tests/_ha_traces.py``; the product has no ``.traces`` importer (PRD N4).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from _ha_traces import Traces, read_column_file, read_counts, read_traces
from reference_data import fetch
from tether.analysis.histogram import apparent_e_histogram
from tether.fret.efficiency import corrected_fret

FIXTURES = Path(__file__).parent / "fixtures" / "ha_sam_sah"
TRACE_NAMES = [f"hel{n}.traces" for n in range(1, 54)]
#: The largest distance allowed between a peak of Tether's histogram of the traces and the same
#: peak of ``test3.dat``: one bin of the laboratory's histogram. Measured on 2026-11-13: 0.0006
#: for both peaks (ADR-0086). The maintainer accepts this value on the pull request.
PEAK_TOLERANCE = 0.015
#: The file precision of ``test3.dat``: ``save -ascii`` writes 8 significant digits.
FILE_PRECISION = 5e-9


def _path(name: str) -> Path:
    """The verified fixture: ``fetch.path_for`` checks its SHA-256 against the manifest."""
    stem = name.split(".")[0]
    return fetch.path_for(f"ha-sam-sah-{stem}")


@pytest.fixture(scope="module")
def counts():
    return read_counts(_path("counts2.txt"))


@pytest.fixture(scope="module")
def lab_histogram() -> np.ndarray:
    return read_column_file(_path("test3.dat"), 2)


@pytest.fixture(scope="module")
def traces() -> dict[str, Traces]:
    return {name: read_traces(_path(name)) for name in TRACE_NAMES}


def _bins(centres: np.ndarray) -> tuple[int, tuple[float, float]]:
    """Tether's ``bins`` and ``value_range`` for bins centred on the laboratory's centres."""
    width = float(centres[1] - centres[0])
    assert np.allclose(np.diff(centres), width, atol=1e-12), "the lab's bins are uniform"
    return centres.size, (float(centres[0]) - width / 2, float(centres[-1]) + width / 2)


def _matlab_hist(values: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """``hist(values, centres) / numel(values)`` from Tether's histogram, converted exactly.

    MATLAB's ``hist`` with bin centres sets the edges at the midpoints and puts every value below
    the first edge in the first bin and every value above the last in the last bin; Tether's
    fixed range drops them. Moving them back is the one conversion. The edge closure differs too
    (``hist`` closes each bin on the right, ``np.histogram`` on the left), so the caller requires
    that no value lies exactly on an inner edge.
    """
    bins, value_range = _bins(centres)
    histogram = apparent_e_histogram(values, bins=bins, value_range=value_range, density=False)
    counts = histogram.counts.copy()
    counts[0] += np.count_nonzero(values < histogram.bin_edges[0])
    counts[-1] += np.count_nonzero(values > histogram.bin_edges[-1])
    return counts / values.size


def _tether_values(traces: dict[str, Traces], counts, *, swap: bool = False) -> np.ndarray:
    """Tether's FRET value of each molecule of the 53 snapshots.

    Per molecule: the mean donor and acceptor over the green-excitation frames that
    ``counts2.txt`` names, and Tether's leakage-corrected FRET at the laboratory's leakage
    (``corrected_fret`` with ``alpha`` from the file and ``gamma=1``; the script records no γ).
    A molecule is kept when its mean donor + acceptor under green excitation lies between the
    two intensity cutoffs of the file and its mean acceptor under red excitation is above the
    lower cutoff, so that both dyes are present. This selection uses the laboratory's recorded
    thresholds; it is Tether's reading of them, not a reconstruction of the script.
    """
    low = counts.parameters["icutoff"]
    high = counts.parameters["icut2"]
    alpha = counts.parameters["leakage"]
    green = slice(counts.green_frames[0] - 1, counts.green_frames[1])
    red = slice(counts.red_frames[0] - 1, counts.red_frames[1])
    values = []
    for t in traces.values():
        donor, acceptor = (t.acceptor, t.donor) if swap else (t.donor, t.acceptor)
        d = donor[:, green].mean(axis=1)
        a = acceptor[:, green].mean(axis=1)
        keep = (d + a > low) & (d + a < high) & (acceptor[:, red].mean(axis=1) > low)
        values.append(corrected_fret(d[keep], a[keep], alpha=alpha, gamma=1.0))
    return np.concatenate(values)


def _peaks(centres: np.ndarray, density: np.ndarray, split: float) -> tuple[float, float]:
    """The low and the high peak: the vertex of the parabola through the largest bin of the
    3-bin moving average and its two neighbours, on each side of ``split``."""
    smooth = np.convolve(density, np.ones(3) / 3, mode="same")
    width = float(centres[1] - centres[0])
    found = []
    for window in (centres < split, centres >= split):
        index = np.flatnonzero(window)
        i = int(index[np.argmax(smooth[index])])
        left, mid, right = smooth[i - 1], smooth[i], smooth[i + 1]
        found.append(float(centres[i]) + 0.5 * width * (left - right) / (left - 2 * mid + right))
    return found[0], found[1]


# --- The reader ---------------------------------------------------------------------------------


def test_the_reader_opens_all_53_files_with_the_molecule_counts_of_the_lab(traces, counts):
    for name, t in traces.items():
        assert t.frames == 20, name
        assert t.donor.shape == t.acceptor.shape == (t.molecules, 20), name
        size = _path(name).stat().st_size
        assert size == 6 + 2 * t.frames * 2 * t.molecules, name
        # The laboratory's script read the same files and wrote each one's molecule count.
        assert t.molecules == counts.files[name][1], name


def test_the_reader_refuses_a_file_whose_size_is_not_the_header_size(tmp_path):
    good = _path("hel1.traces").read_bytes()
    for broken in (good[:-2], good + b"\0\0", good[:4]):
        path = tmp_path / "broken.traces"
        path.write_bytes(broken)
        with pytest.raises(ValueError, match=r"B"):
            read_traces(path)


def test_the_donor_is_the_first_channel_of_each_molecule(traces, counts):
    """Under red excitation the donor is not excited and the acceptor is: the donor channel falls
    to its background and the acceptor channel keeps its direct-excitation signal. The first
    channel of each pair behaves as the donor in every file, so the order is donor first."""
    green = slice(counts.green_frames[0] - 1, counts.green_frames[1])
    red = slice(counts.red_frames[0] - 1, counts.red_frames[1])
    for name, t in traces.items():
        donor_kept = t.donor[:, red].mean() / t.donor[:, green].mean()
        acceptor_kept = t.acceptor[:, red].mean() / t.acceptor[:, green].mean()
        assert donor_kept < 0.4 < acceptor_kept, (name, donor_kept, acceptor_kept)


# --- The laboratory's own files -----------------------------------------------------------------


def test_the_counts_file_accounts_for_every_value_of_frets_dat(counts):
    frets = read_column_file(_path("frets.dat"), 1)
    # 56 files: the 53 snapshots and the three long movies hel54 to hel56, not committed.
    assert len(counts.files) == 56
    assert sum(both for *_, both in counts.files.values()) == frets.size == 46950
    assert all(high + low == both for _, _, high, low, both in counts.files.values())


def test_tether_histogram_of_frets_dat_equals_test3_dat(lab_histogram):
    frets = read_column_file(_path("frets.dat"), 1)
    centres, lab = lab_histogram[:, 0], lab_histogram[:, 1]
    _, value_range = _bins(centres)
    inner = np.linspace(*value_range, centres.size + 1)[1:-1]
    assert not np.isin(frets, inner).any(), "a value on an inner edge needs the closure rule"
    np.testing.assert_allclose(_matlab_hist(frets, centres), lab, rtol=0, atol=FILE_PRECISION)


def test_the_outer_bins_need_the_conversion(lab_histogram):
    """Without moving the out-of-range values back, the first and last bins miss the file."""
    frets = read_column_file(_path("frets.dat"), 1)
    centres, lab = lab_histogram[:, 0], lab_histogram[:, 1]
    bins, value_range = _bins(centres)
    plain = apparent_e_histogram(frets, bins=bins, value_range=value_range, density=False)
    miss = np.abs(plain.counts / frets.size - lab)
    assert miss[0] > FILE_PRECISION and miss[-1] > FILE_PRECISION


# --- Tether's values from the traces ------------------------------------------------------------


def test_tether_histogram_of_the_traces_has_the_peaks_of_test3_dat(traces, counts, lab_histogram):
    centres, lab = lab_histogram[:, 0], lab_histogram[:, 1]
    split = counts.parameters["fcutoff1"]
    values = _tether_values(traces, counts)
    lab_low, lab_high = _peaks(centres, lab, split)
    low, high = _peaks(centres, _matlab_hist(values, centres), split)
    assert abs(low - lab_low) <= PEAK_TOLERANCE, (low, lab_low)
    assert abs(high - lab_high) <= PEAK_TOLERANCE, (high, lab_high)


def test_swapped_channels_miss_the_peaks_of_test3_dat(traces, counts, lab_histogram):
    """The demonstration that the comparison sees the channel order: with donor and acceptor
    swapped in every molecule, at least one peak moves far outside the tolerance."""
    centres, lab = lab_histogram[:, 0], lab_histogram[:, 1]
    split = counts.parameters["fcutoff1"]
    lab_low, lab_high = _peaks(centres, lab, split)
    values = _tether_values(traces, counts, swap=True)
    low, high = _peaks(centres, _matlab_hist(values, centres), split)
    assert max(abs(low - lab_low), abs(high - lab_high)) > 5 * PEAK_TOLERANCE
