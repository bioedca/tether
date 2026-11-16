# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The second ``.traces`` variant: a frame index in each frame, acceptor first (#798).

The fixture is one movie of the trinucleotide-repeat hairpin data of the Weninger laboratory:
``15msec/(CTG)14/20200125/cascade110.traces`` of the archive ``10072022_All_hairpin_traces.zip``
of Zenodo 10.5281/zenodo.7853531 (a copy of Dryad doi:10.5061/dryad.ht76hdrkc, CC0-1.0), the data
of Xu *et al.*, J. Mol. Biol. 435:168086 (2023), doi:10.1016/j.jmb.2023.168086. It is committed
verbatim under ``tests/fixtures/weninger_hairpin/``.

What the variant changes, and what each test catches:

* Every frame starts with a frame index that the header does not count. A reader that ignores it
  shifts every channel by one value in each frame.
* The laboratory's MATLAB reader takes each molecule's pair acceptor first. A reader that takes
  the donor first gives E -> 1 - E.

The reference for both is the laboratory's own reader, ``keith_Folder_view_tir`` in
``MATLAB_code_for_opening_dot_traces_files.pdf`` of the same archive, which
:func:`_matlab_reader` follows statement by statement. The reader under test is
``tests/_ha_traces.py``; the product has no ``.traces`` importer (PRD N4).

The peak check against the paper is advisory: it reports and never fails (#798, item 4).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from _ha_traces import read_traces
from reference_data import fetch
from tether.analysis.histogram import apparent_e_histogram
from tether.fret.efficiency import corrected_fret

FIXTURE = "weninger-hairpin-cascade110"
#: The leakage that the laboratory's MATLAB reader subtracts: ``leakage = 0.06``.
LEAKAGE = 0.06
#: The value the MATLAB reader gives a frame whose donor + corrected acceptor is not positive.
MATLAB_EMPTY_FRAME = 0.01
#: The CTG hairpin's two FRET values in the paper (advisory check only).
PAPER_PEAKS = (0.74, 0.53)
#: Advisory check: the frames used are those whose donor + corrected acceptor is above this
#: number of counts, so that frames after a dye has bleached are left out.
ADVISORY_MIN_TOTAL = 300.0


def _path() -> Path:
    """The verified fixture: ``fetch.path_for`` checks its SHA-256 against the manifest."""
    return fetch.path_for(FIXTURE)


@pytest.fixture(scope="module")
def raw() -> bytes:
    return _path().read_bytes()


def _matlab_reader(raw: bytes) -> dict[str, np.ndarray | int]:
    """``keith_Folder_view_tir`` of the laboratory, statement by statement.

    ``len=fread(fid,1,'int32')``, ``Ntraces=fread(fid,1,'int16')``, then
    ``raw=fread(fid,Ntraces*len,'int16')`` into ``Data=zeros(Ntraces+1,len)`` in MATLAB's column
    order, so each column is one frame. ``time1=Data(1,:)``, ``acceptor(i,:)=Data(i*2,:)`` and
    ``donor(i,:)=Data(i*2+1,:)``. Then ``acceptor_1 = acceptor - leakage*donor`` and the FRET is
    ``acceptor_1/(donor+acceptor_1)``, or 0.01 where that sum is not positive.

    The script reads ``Ntraces*len`` values although the file holds ``(Ntraces+1)*len``, so the
    last frames stay zero in ``Data``: :data:`complete` is the number of frames it reads in full.
    """
    frames = int(np.frombuffer(raw, dtype="<i4", count=1)[0])
    channels = int(np.frombuffer(raw, dtype="<i2", count=1, offset=4)[0])
    values = np.frombuffer(raw, dtype="<i2", count=channels * frames, offset=6)
    data = np.zeros((channels + 1) * frames)
    data[: values.size] = values
    data = data.reshape((channels + 1, frames), order="F")
    rows = np.arange(1, channels // 2 + 1)
    acceptor = data[rows * 2 - 1]  # MATLAB's Data(i*2,:), counted from 1
    donor = data[rows * 2]  # MATLAB's Data(i*2+1,:)
    acceptor_1 = acceptor - LEAKAGE * donor
    total = donor + acceptor_1
    with np.errstate(divide="ignore", invalid="ignore"):
        fret = np.where(total <= 0, MATLAB_EMPTY_FRAME, acceptor_1 / total)
    return {
        "time": data[0],
        "donor": donor,
        "acceptor": acceptor,
        "fret": fret,
        "total": total,
        "complete": values.size // (channels + 1),
    }


def _agrees_with_matlab(donor: np.ndarray, acceptor: np.ndarray, matlab: dict) -> bool:
    """Tether's leakage-corrected FRET (γ = 1) equals the MATLAB FRET on the frames it read."""
    n = matlab["complete"]
    tether = corrected_fret(donor[:, :n], acceptor[:, :n], alpha=LEAKAGE, gamma=1.0)
    valid = matlab["total"][:, :n] > 0
    return bool(np.allclose(tether[valid], matlab["fret"][:, :n][valid], rtol=0, atol=1e-12))


# --- The reader ---------------------------------------------------------------------------------


def test_the_reader_returns_the_frame_index_and_the_header_counts(raw):
    t = read_traces(_path(), order="acceptor-first")
    frames = int(np.frombuffer(raw, dtype="<i4", count=1)[0])
    channels = int(np.frombuffer(raw, dtype="<i2", count=1, offset=4)[0])
    assert (frames, channels) == (872, 26)
    assert t.frames == frames and t.molecules == channels // 2 == 13
    assert t.donor.shape == t.acceptor.shape == (13, 872)
    # The header does not count the frame index: one more int16 value in each frame.
    assert len(raw) == 6 + 2 * frames * (channels + 1) == 47_094
    assert t.frame_index is not None
    np.testing.assert_array_equal(t.frame_index, np.arange(1, frames + 1))


def test_the_reader_finds_each_variant_from_the_file_size():
    """The Ha-laboratory files of #796 have no frame index; this file has one."""
    ha = read_traces(fetch.path_for("ha-sam-sah-hel1"), order="donor-first")
    assert ha.frame_index is None
    assert read_traces(_path(), order="acceptor-first").frame_index is not None


def test_the_reader_refuses_a_size_of_neither_variant(raw, tmp_path):
    for broken in (raw[:-2], raw + b"\0\0", raw[: -2 * 27]):
        path = tmp_path / "broken.traces"
        path.write_bytes(broken)
        with pytest.raises(ValueError, match="with a frame index"):
            read_traces(path, order="acceptor-first")


def test_the_reader_refuses_an_unstated_channel_order():
    with pytest.raises(ValueError, match="order must be one of"):
        read_traces(_path(), order="donor")  # type: ignore[arg-type]


# --- Against the laboratory's MATLAB reader -----------------------------------------------------


def test_the_reader_and_tether_fret_follow_the_laboratory_matlab_reader(raw):
    matlab = _matlab_reader(raw)
    n = matlab["complete"]
    # 26 x 872 values read into columns of 27: 839 whole frames, then a part of frame 840.
    assert n == 839
    t = read_traces(_path(), order="acceptor-first")
    np.testing.assert_array_equal(matlab["time"][:n], t.frame_index[:n])
    np.testing.assert_array_equal(matlab["donor"][:, :n], t.donor[:, :n])
    np.testing.assert_array_equal(matlab["acceptor"][:, :n], t.acceptor[:, :n])
    assert _agrees_with_matlab(t.donor, t.acceptor, matlab)
    # Where the sum is not positive the script writes 0.01. Tether gives NaN only where it is
    # exactly zero and keeps the ratio where it is negative, so those frames are left out above.
    empty = matlab["total"][:, :n] <= 0
    assert empty.any()
    np.testing.assert_array_equal(matlab["fret"][:, :n][empty], MATLAB_EMPTY_FRAME)


def test_reading_the_file_as_the_variant_without_a_frame_index_fails(raw):
    """The demonstration for the frame index: a reader that takes the header's channel count as
    the width of a frame shifts every channel, and the FRET no longer follows the script."""
    matlab = _matlab_reader(raw)
    channels, frames = 26, 872
    values = np.frombuffer(raw, dtype="<i2", count=channels * frames, offset=6)
    per_channel = values.reshape(frames, channels).T.astype(np.float64)
    acceptor, donor = per_channel[0::2], per_channel[1::2]
    assert not _agrees_with_matlab(donor, acceptor, matlab)
    # The frame indices 1, 2, 3, ... now sit inside the intensities.
    assert values[0] == 1 and values[channels + 1] == 2


def test_the_swapped_channel_order_fails(raw):
    """The demonstration for the order: donor first gives other values, and E -> 1 - E."""
    matlab = _matlab_reader(raw)
    swapped = read_traces(_path(), order="donor-first")
    assert not _agrees_with_matlab(swapped.donor, swapped.acceptor, matlab)
    right = read_traces(_path(), order="acceptor-first")
    with np.errstate(divide="ignore", invalid="ignore"):
        e = corrected_fret(right.donor, right.acceptor, alpha=0.0, gamma=1.0)
        e_swapped = corrected_fret(swapped.donor, swapped.acceptor, alpha=0.0, gamma=1.0)
    finite = np.isfinite(e) & np.isfinite(e_swapped)
    np.testing.assert_allclose(e_swapped[finite], 1.0 - e[finite], rtol=0, atol=1e-12)


# --- Advisory: the paper's two CTG values -------------------------------------------------------


def test_advisory_tether_histogram_beside_the_papers_ctg_values(capsys):
    """Reports where Tether's FRET histogram of this one movie lies beside the paper's CTG values
    0.74 and 0.53, and never fails on it (#798, item 4).

    Per frame: Tether's leakage-corrected FRET at the MATLAB reader's leakage (γ = 1), for the
    frames whose donor + corrected acceptor is above :data:`ADVISORY_MIN_TOTAL`; Tether's
    histogram with bins of 0.025. The paper uses its own methods (a Chung-Kennedy filter, K-means
    and SPARTAN) on many movies, so this is no comparison of results.
    """
    t = read_traces(_path(), order="acceptor-first")
    donor, acceptor = t.donor, t.acceptor
    total = donor + acceptor - LEAKAGE * donor
    values = corrected_fret(donor, acceptor, alpha=LEAKAGE, gamma=1.0)[total > ADVISORY_MIN_TOTAL]
    histogram = apparent_e_histogram(values, bins=56, value_range=(-0.2, 1.2), density=False)
    centres = (histogram.bin_edges[:-1] + histogram.bin_edges[1:]) / 2
    upper = centres > 0.3  # above the donor-only frames
    peak = float(centres[upper][np.argmax(histogram.counts[upper])])
    near = {p: float(np.mean(np.abs(values - p) <= 0.05)) for p in PAPER_PEAKS}
    with capsys.disabled():
        print(
            f"\n[advisory #798] {values.size} frames; highest bin above 0.3 at E = {peak:.4f}; "
            + "; ".join(f"{100 * f:.1f} % of frames within 0.05 of {p}" for p, f in near.items())
        )
    assert values.size > 0 and np.isfinite(peak)
