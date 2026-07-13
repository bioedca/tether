# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader + staged-fixture checks for the kinSoftChallenge simulated datasets.

The raw ``.txt`` reader is exercised on a tiny committed sample (plain git, in the
required matrix); the packed gated-tier ``kinsoft_sim.hdf5`` structure is checked
under ``@pytest.mark.large`` (LFS, skipped on an unmaterialized pointer). A
cross-artifact identity test ties the committed sample to its row in the fixture.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tether.io.kinsoft import read_kinsoft_fixture, read_kinsoft_trace

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "kinsoft_trace_sample.txt"
FIXTURE = FIXTURES / "large" / "kinsoft_sim.hdf5"

# The committed sample is sim_dataset_Fig4/sim_level3_final_publish/trace_58.txt
# (level3), verbatim: 42 frames at dt = 0.2 s.
_SAMPLE_LEVEL = "level3"
_SAMPLE_INDEX = 57  # trace_58 -> 0-based index 57 within the numerically-sorted level


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is absent or a Git-LFS pointer stub (not real data)."""
    if not path.exists():
        return True
    if path.stat().st_size > 4096:
        return False
    return path.read_bytes()[:64].startswith(b"version https://git-lfs")


def test_read_kinsoft_trace_sample() -> None:
    tr = read_kinsoft_trace(SAMPLE)
    assert tr.idd.shape == tr.ida.shape == tr.iaa.shape == tr.fret_e.shape == (42,)
    assert tr.time.shape == (42,)
    assert tr.frame_time_s == pytest.approx(0.2)
    # First frame (verbatim from the file): 0.000 +4.521e+03 +3.442e+02 +5.176e+03 +0.071
    assert tr.idd[0] == pytest.approx(4521.0)
    assert tr.ida[0] == pytest.approx(344.2)
    assert tr.iaa[0] == pytest.approx(5176.0)
    assert tr.fret_e[0] == pytest.approx(0.071)
    # Time axis is uniform and increasing at the frame period.
    assert np.allclose(np.diff(tr.time), 0.2)
    # FRET E stays a proper efficiency.
    assert np.all((tr.fret_e >= -0.5) & (tr.fret_e <= 1.5))


def test_read_kinsoft_trace_rejects_wrong_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text("%t\tE\n0.0\t0.1\n0.2\t0.2\n")
    with pytest.raises(ValueError, match="5 columns"):
        read_kinsoft_trace(bad)


@pytest.mark.large
def test_kinsoft_fixture_structure() -> None:
    if _is_lfs_pointer(FIXTURE):
        pytest.skip("LFS large-tier fixture not materialized (default checkout)")
    levels = read_kinsoft_fixture(FIXTURE)
    assert set(levels) == {"level1", "level2", "level3"}
    expected = {
        "level1": ("Fig2", 75, 0.2),
        "level2": ("Fig3", 150, 0.1),
        "level3": ("Fig4", 250, 0.2),
    }
    for name, (figure, n, dt) in expected.items():
        lvl = levels[name]
        assert lvl.figure == figure
        assert lvl.n_traces == n
        assert lvl.frame_time_s == pytest.approx(dt)
        assert lvl.idd.shape == lvl.ida.shape == lvl.iaa.shape == lvl.fret_e.shape
        assert lvl.idd.shape[0] == n
        assert lvl.length.shape == (n,)
        assert int(lvl.length.max()) == lvl.idd.shape[1]  # max_len == the padded width
        assert np.all(lvl.length >= 1)
        # A trace shorter than max is zero-padded past its length in every column.
        i = int(np.argmin(lvl.length))
        n_i = int(lvl.length[i])
        if n_i < lvl.idd.shape[1]:
            for col in (lvl.idd, lvl.ida, lvl.iaa, lvl.fret_e):
                assert np.all(col[i, n_i:] == 0.0)


@pytest.mark.large
def test_kinsoft_fixture_provenance_attrs() -> None:
    if _is_lfs_pointer(FIXTURE):
        pytest.skip("LFS large-tier fixture not materialized (default checkout)")
    h5py = pytest.importorskip("h5py")
    with h5py.File(FIXTURE, "r") as f:
        assert f.attrs["format"] == "kinsoft-sim"
        assert f.attrs["license"] == "CC-BY-4.0"
        assert f.attrs["doi"] == "10.5281/zenodo.5701310"
        # Every level records the SHA-256 of its source zip (provenance).
        for name in ("level1", "level2", "level3"):
            assert len(str(f[name].attrs["source_sha256"])) == 64


@pytest.mark.large
def test_committed_sample_matches_fixture_row() -> None:
    """The plain-git sample is exactly its trace inside the packed fixture."""
    if _is_lfs_pointer(FIXTURE):
        pytest.skip("LFS large-tier fixture not materialized (default checkout)")
    sample = read_kinsoft_trace(SAMPLE)
    lvl = read_kinsoft_fixture(FIXTURE)[_SAMPLE_LEVEL]
    packed = lvl.trace(_SAMPLE_INDEX)
    assert packed.idd.shape == sample.idd.shape == (42,)
    for a, b in (
        (packed.idd, sample.idd),
        (packed.ida, sample.ida),
        (packed.iaa, sample.iaa),
        (packed.fret_e, sample.fret_e),
    ):
        assert np.allclose(a, b, rtol=0, atol=1e-3), "committed sample must equal its fixture row"
