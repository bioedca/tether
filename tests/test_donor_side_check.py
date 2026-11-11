# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The advisory donor-side check (#646, ADR-0070).

Four layers, each a behaviour the issue names:

* the per-molecule event rule on hand-built traces (acceptor-first, donor-first, no
  second bleach, no bleach) and the movie verdict at its thresholds;
* the repository's trace fixtures read both ways: silent with the stored labels and a
  warning with the channels exchanged wherever there are enough bleach events, and
  "not enough bleach events" where there are not (the large tier adds the real
  281-molecule fixture and the kinSoftChallenge sets);
* a synthetic movie **extracted both ways** through :func:`extract_movie` and the
  ``tether extract`` command: the verdict, the stored provenance, the message and its
  remedy, and that the check neither alters a stored value nor fails an extraction;
* the batch summary listing a flagged movie, including on a resumed run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.imaging.side_check import (  # noqa: E402
    EVENT_CONSISTENT,
    EVENT_NONE,
    EVENT_SWAPPED,
    EVENT_TOGETHER,
    EVENT_UNCLEAR,
    SIDE_CHECK_MAJORITY,
    SIDE_CHECK_MIN_EVENTS,
    DonorSideCheck,
    check_donor_side,
    classify_bleach_event,
    side_check_verdict,
    votes_needed,
)

FIXTURES = Path(__file__).parent / "fixtures"
_N = 200  # frames in the hand-built traces


def _trace_pair(
    rng: np.random.Generator,
    *,
    t_acceptor: int | None,
    t_donor: int | None,
    level: float = 300.0,
    noise: float = 20.0,
) -> tuple[np.ndarray, np.ndarray]:
    """One background-subtracted molecule under donor excitation, E = 0.5, γ = 1.

    ``t_acceptor`` / ``t_donor`` are the bleach frames (``None`` = no bleach in view).
    Before either bleach both channels read ``level``; after an acceptor-first bleach
    the donor reads ``2 * level`` and the acceptor background; after the donor
    bleaches both read background.
    """
    t = np.arange(_N)
    t_d = _N if t_donor is None else t_donor
    t_a = _N if t_acceptor is None else t_acceptor
    fret_on = t < min(t_a, t_d)
    donor = np.where(fret_on, level, 2.0 * level) * (t < t_d)
    acceptor = np.where(fret_on, level, 0.0)
    return donor + rng.normal(0, noise, _N), acceptor + rng.normal(0, noise, _N)


def _population(rng, n_acceptor_first: int, n_donor_first: int = 0):
    rows = [_trace_pair(rng, t_acceptor=60, t_donor=140) for _ in range(n_acceptor_first)]
    rows += [_trace_pair(rng, t_acceptor=None, t_donor=80) for _ in range(n_donor_first)]
    donor = np.array([r[0] for r in rows])
    acceptor = np.array([r[1] for r in rows])
    return donor, acceptor


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is absent or a Git-LFS pointer stub (not real data)."""
    if not path.exists():
        return True
    if path.stat().st_size > 4096:
        return False
    return path.read_bytes()[:64].startswith(b"version https://git-lfs")


def _smd_channels(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with h5py.File(path, "r") as f:
        raw = f["dataset/data/raw"][:]
    return raw[:, :, 0], raw[:, :, 1]


# --- the per-molecule event rule ------------------------------------------------------


def test_acceptor_first_bleach_votes_for_the_labels_and_flips_when_swapped() -> None:
    donor, acceptor = _trace_pair(np.random.default_rng(1), t_acceptor=60, t_donor=140)
    assert classify_bleach_event(donor, acceptor) == EVENT_CONSISTENT
    # The same molecule with its channels exchanged: the "donor" drops, the
    # "acceptor" rises -- the swapped-halves signature.
    assert classify_bleach_event(acceptor, donor) == EVENT_SWAPPED


def test_donor_first_bleach_takes_both_down_and_casts_no_vote() -> None:
    donor, acceptor = _trace_pair(np.random.default_rng(2), t_acceptor=None, t_donor=80)
    assert classify_bleach_event(donor, acceptor) == EVENT_TOGETHER
    assert classify_bleach_event(acceptor, donor) == EVENT_TOGETHER


def test_a_second_bleach_too_close_to_the_end_casts_no_vote() -> None:
    # The acceptor drops and the donor rises at frame 150, but the donor reaches
    # background only 3 frames before the end: fewer than ``min_frames`` (5) to
    # tell a bleach from a FRET transition, so the molecule does not vote.
    rng = np.random.default_rng(3)
    t = np.arange(_N)
    donor = np.where(t < 150, 300.0, 360.0) * (t < _N - 3) + rng.normal(0, 20.0, _N)
    acceptor = np.where(t < 150, 300.0, 0.0) + rng.normal(0, 20.0, _N)
    assert classify_bleach_event(donor, acceptor) == EVENT_UNCLEAR
    assert classify_bleach_event(acceptor, donor) == EVENT_UNCLEAR
    # The same molecule with the donor bleaching 20 frames before the end votes.
    donor[_N - 20 :] = rng.normal(0, 20.0, 20)
    assert classify_bleach_event(donor, acceptor) == EVENT_CONSISTENT


def test_a_step_with_no_second_bleach_in_view_casts_no_vote() -> None:
    # The acceptor-side drop and donor rise are there, but the donor never reaches
    # background: a FRET transition looks like this. The single-step detector puts
    # both channels' change at the same frame, so the molecule reads as "together"
    # -- either way it casts no vote, in either orientation.
    donor, acceptor = _trace_pair(np.random.default_rng(3), t_acceptor=150, t_donor=None)
    assert classify_bleach_event(donor, acceptor) in {EVENT_TOGETHER, EVENT_UNCLEAR}
    assert classify_bleach_event(acceptor, donor) in {EVENT_TOGETHER, EVENT_UNCLEAR}


def test_a_trace_that_never_bleaches_is_no_event() -> None:
    donor, acceptor = _trace_pair(np.random.default_rng(4), t_acceptor=None, t_donor=None)
    assert classify_bleach_event(donor, acceptor) == EVENT_NONE


def test_a_step_without_a_rise_does_not_vote() -> None:
    rng = np.random.default_rng(5)
    donor, acceptor = _trace_pair(rng, t_acceptor=60, t_donor=140)
    donor[60:140] = 300.0 + rng.normal(0, 20.0, 80)  # no dequenching: the donor stays flat
    assert classify_bleach_event(donor, acceptor) == EVENT_UNCLEAR


def test_non_finite_or_short_traces_are_no_event() -> None:
    donor, acceptor = _trace_pair(np.random.default_rng(6), t_acceptor=60, t_donor=140)
    donor[10] = np.nan
    assert classify_bleach_event(donor, acceptor) == EVENT_NONE
    assert classify_bleach_event(np.ones(6), np.ones(6)) == EVENT_NONE


# --- the movie verdict ------------------------------------------------------------------


def test_majority_rounds_up_and_ties_at_the_threshold_count() -> None:
    assert votes_needed(20, 0.8) == 16
    assert votes_needed(21, 0.8) == 17
    assert side_check_verdict(4, 16, 20, 0.8) == "swapped"
    assert side_check_verdict(5, 15, 20, 0.8) == "mixed"
    assert side_check_verdict(16, 4, 20, 0.8) == "consistent"
    assert side_check_verdict(10, 9, 20, 0.8) == "insufficient"


def test_correct_labels_are_silent_and_swapped_labels_warn() -> None:
    donor, acceptor = _population(np.random.default_rng(7), 25, 10)
    ok = check_donor_side(donor, acceptor)
    assert ok.verdict == "consistent" and not ok.swapped
    assert (ok.n_consistent, ok.n_swapped, ok.n_together) == (25, 0, 10)

    flipped = check_donor_side(acceptor, donor)
    assert flipped.swapped
    assert (flipped.n_consistent, flipped.n_swapped) == (0, 25)
    message = flipped.describe(donor_side="left")
    assert "25 of 25 bleach events are consistent with swapped" in message
    assert "--donor-side right" in message
    assert "--donor-side left" in flipped.describe(donor_side="right")
    assert "--tmap" in flipped.describe(imported_map=True)


def test_too_few_events_reports_not_enough() -> None:
    donor, acceptor = _population(np.random.default_rng(8), SIDE_CHECK_MIN_EVENTS - 1, 5)
    for check in (check_donor_side(donor, acceptor), check_donor_side(acceptor, donor)):
        assert check.verdict == "insufficient" and not check.swapped
        assert check.describe().startswith("not enough bleach events to check")


def test_a_sample_with_no_acceptor_reports_not_enough() -> None:
    donor, acceptor = _population(np.random.default_rng(9), 0, 40)
    assert check_donor_side(donor, acceptor).verdict == "insufficient"
    assert check_donor_side(acceptor, donor).verdict == "insufficient"


def test_votes_split_without_a_majority_are_mixed() -> None:
    d1, a1 = _population(np.random.default_rng(10), 12)
    d2, a2 = _population(np.random.default_rng(11), 12)
    check = check_donor_side(np.vstack([d1, a2]), np.vstack([a1, d2]))
    assert (check.n_consistent, check.n_swapped) == (12, 12)
    assert check.verdict == "mixed" and not check.swapped
    assert "by eye" in check.describe()


def test_the_check_never_modifies_its_input() -> None:
    donor, acceptor = _population(np.random.default_rng(12), 25)
    before = donor.copy(), acceptor.copy()
    check_donor_side(donor, acceptor)
    assert np.array_equal(donor, before[0]) and np.array_equal(acceptor, before[1])


def test_record_round_trips_through_json() -> None:
    donor, acceptor = _population(np.random.default_rng(13), 22)
    check = check_donor_side(acceptor, donor)
    record = json.loads(json.dumps(check.to_dict()))
    assert record["verdict"] == "swapped" and record["n_events"] == 22
    assert record["min_events"] == SIDE_CHECK_MIN_EVENTS
    assert record["majority"] == SIDE_CHECK_MAJORITY
    assert DonorSideCheck.from_dict(record) == check


@pytest.mark.parametrize(
    "kwargs",
    [
        {"majority": 0.5},
        {"majority": 1.5},
        {"min_events": 0},
        {"step_z": 0.0},
        {"min_frames": 1},
        {"min_frames": 12, "level_frames": 10},
    ],
)
def test_invalid_thresholds_are_refused(kwargs) -> None:
    donor, acceptor = _population(np.random.default_rng(14), 2)
    with pytest.raises(ValueError):
        check_donor_side(donor, acceptor, **kwargs)


# --- the repository's trace fixtures, both ways -----------------------------------------


@pytest.mark.parametrize("name", ["smd_2mol.hdf5", "smd_4mol.hdf5"])
def test_small_fixtures_have_too_few_events_either_way(name) -> None:
    donor, acceptor = _smd_channels(FIXTURES / name)
    assert check_donor_side(donor, acceptor).verdict == "insufficient"
    assert check_donor_side(acceptor, donor).verdict == "insufficient"


@pytest.mark.large
def test_real_281_molecule_fixture_is_silent_correct_and_warns_swapped() -> None:
    path = FIXTURES / "large" / "smd_281mol.hdf5"
    if _is_lfs_pointer(path):
        pytest.skip("Git LFS fixture not materialised")
    donor, acceptor = _smd_channels(path)
    ok = check_donor_side(donor, acceptor)
    assert ok.verdict == "consistent"
    # The values ADR-0070 records for this fixture.
    assert (ok.n_consistent, ok.n_swapped, ok.n_together) == (150, 10, 72)
    flipped = check_donor_side(acceptor, donor)
    assert flipped.verdict == "swapped"
    assert (flipped.n_consistent, flipped.n_swapped) == (10, 150)


@pytest.mark.large
@pytest.mark.parametrize("level", ["level1", "level2", "level3"])
def test_kinsoft_sets_have_no_bleach_sequences_to_vote(level) -> None:
    # The simulated challenge traces stop before photobleaching, so their FRET
    # transitions must not be read as bleach events in either labelling.
    path = FIXTURES / "large" / "kinsoft_sim.hdf5"
    if _is_lfs_pointer(path):
        pytest.skip("Git LFS fixture not materialised")
    with h5py.File(path, "r") as f:
        idd, ida, length = f[level]["idd"][:], f[level]["ida"][:], f[level]["length"][:]
    kinds = [
        classify_bleach_event(idd[i, : length[i]], ida[i, : length[i]]) for i in range(length.size)
    ]
    c, s = kinds.count(EVENT_CONSISTENT), kinds.count(EVENT_SWAPPED)
    assert side_check_verdict(c, s, SIDE_CHECK_MIN_EVENTS, SIDE_CHECK_MAJORITY) == "insufficient"
    assert side_check_verdict(s, c, SIDE_CHECK_MIN_EVENTS, SIDE_CHECK_MAJORITY) == "insufficient"


# --- a synthetic movie extracted both ways ----------------------------------------------

_BG = 100.0
_SIGMA = 1.5
_HEIGHT = 88
_HALF = 116  # each half holds a 7 x 5 grid at 15 px spacing, 10 px clear of the edge
_FRAMES = 60


def _write_bleaching_movie(path: Path, *, seed: int = 0) -> None:
    """A dual-channel movie whose 35 molecules all bleach acceptor first, then donor.

    Donor on the left half, acceptor on the right half one pixel to the right (the
    translation the native registration recovers). E = 0.5 until the acceptor bleaches
    between frames 18 and 28; the donor then doubles and bleaches between 40 and 50.
    """
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(seed)
    rows, cols = np.mgrid[0:_HEIGHT, 0 : 2 * _HALF]
    t = np.arange(_FRAMES)
    stack = np.full((_FRAMES, _HEIGHT, 2 * _HALF), _BG)
    for i in range(7):
        for j in range(5):
            x, y = 12.0 + 15 * i, 12.0 + 15 * j
            t_a, t_d = int(rng.integers(18, 29)), int(rng.integers(40, 51))
            spot_d = np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2 * _SIGMA**2))
            spot_a = np.exp(-((rows - y) ** 2 + (cols - (x + _HALF + 1)) ** 2) / (2 * _SIGMA**2))
            donor = np.where(t < t_a, 150.0, 300.0) * (t < t_d)
            acceptor = np.where(t < t_a, 150.0, 0.0)
            stack += donor[:, None, None] * spot_d + acceptor[:, None, None] * spot_a
    stack += rng.normal(0.0, 4.0, stack.shape)
    be = np.ascontiguousarray(np.clip(stack, 0, 65535), dtype=">u2")
    tifffile.imwrite(path, be, photometric="minisblack", byteorder=">")


@pytest.fixture
def bleaching_movie(tmp_path) -> Path:
    pytest.importorskip("skimage")
    movie = tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
    _write_bleaching_movie(movie)
    return movie


def _profile(path: Path) -> dict:
    with h5py.File(path, "r") as f:
        raw = f["/settings/extraction"].attrs["profile_json"]
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def test_extraction_is_silent_with_the_correct_side_and_warns_with_the_wrong_one(
    bleaching_movie, tmp_path
) -> None:
    from tether.project.extract import ExtractOptions, extract_movie

    right = extract_movie(bleaching_movie, tmp_path / "right.tether")
    assert right.donor_side_check is not None
    assert right.donor_side_check.verdict == "consistent"
    assert right.donor_side_check.n_events >= SIDE_CHECK_MIN_EVENTS
    assert _profile(tmp_path / "right.tether")["donor_side_check"]["verdict"] == "consistent"

    wrong = extract_movie(
        bleaching_movie,
        tmp_path / "wrong.tether",
        options=ExtractOptions(donor_side="right"),
    )
    assert wrong.donor_side_check.verdict == "swapped"
    assert wrong.donor_side_check.n_swapped >= SIDE_CHECK_MIN_EVENTS
    stored = _profile(tmp_path / "wrong.tether")["donor_side_check"]
    assert stored == wrong.donor_side_check.to_dict()


def test_the_check_alters_no_stored_value(bleaching_movie, tmp_path, monkeypatch) -> None:
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project import extract as extract_mod

    with_check = tmp_path / "with.tether"
    extract_mod.extract_movie(bleaching_movie, with_check)
    monkeypatch.setattr(
        extract_mod,
        "check_donor_side",
        lambda donor, acceptor: DonorSideCheck(verdict="insufficient"),
    )
    without_check = tmp_path / "without.tether"
    extract_mod.extract_movie(bleaching_movie, without_check)

    traces_a, traces_b = read_traces(with_check), read_traces(without_check)
    assert traces_a.keys() == traces_b.keys()
    for name in traces_a:
        assert np.array_equal(traces_a[name], traces_b[name]), name
    mols_a, mols_b = read_molecules(with_check), read_molecules(without_check)
    for field in ("donor_xy", "acceptor_xy", "frame_range", "bleach_frames", "analysis_window"):
        assert np.array_equal(mols_a[field], mols_b[field]), field


def test_a_failing_check_never_fails_the_extraction(bleaching_movie, tmp_path, monkeypatch) -> None:
    from tether.project import extract as extract_mod

    def boom(donor, acceptor):
        raise RuntimeError("check exploded")

    monkeypatch.setattr(extract_mod, "check_donor_side", boom)
    summary = extract_mod.extract_movie(bleaching_movie, tmp_path / "out.tether")
    assert summary.n_molecules > 0
    assert summary.donor_side_check.verdict == "not-run"
    assert "check exploded" in summary.donor_side_check.reason
    assert _profile(tmp_path / "out.tether")["donor_side_check"]["verdict"] == "not-run"


def test_cli_warns_on_stderr_with_the_evidence_and_the_remedy(
    bleaching_movie, tmp_path, capsys
) -> None:
    from tether.cli import main

    out = tmp_path / "wrong.tether"
    assert main(["extract", str(bleaching_movie), "-o", str(out), "--donor-side", "right"]) == 0
    captured = capsys.readouterr()
    assert "bleach events are consistent with swapped donor and acceptor halves" in captured.err
    assert "extract this movie again with --donor-side left" in captured.err

    out_ok = tmp_path / "right.tether"
    assert main(["extract", str(bleaching_movie), "-o", str(out_ok)]) == 0
    captured = capsys.readouterr()
    assert "donor side:" in captured.out and "agree with the donor side" in captured.out
    assert "swapped" not in captured.err


# --- the batch summary ------------------------------------------------------------------


def _record(verdict: str, n_consistent: int, n_swapped: int) -> dict:
    check = DonorSideCheck(verdict=verdict, n_consistent=n_consistent, n_swapped=n_swapped)
    return check.to_dict()


def _side_stub(records: dict[str, dict]):
    """A fake extract runner that writes a profile carrying a stored check record."""
    from tether.io.schema import create_project

    def run(movie_path, output_path, *, options=None, tmap=None, tdat=None, **kwargs):
        stem = Path(output_path).stem
        create_project(output_path, overwrite=True)
        with h5py.File(output_path, "r+") as f:
            grp = f.require_group("settings/extraction")
            profile = {"registration_rms_px": 0.2, "rms_gate": 0.5, "donor_side": "left"}
            if stem in records:
                profile["donor_side_check"] = records[stem]
            grp.attrs["profile_json"] = json.dumps(profile)
        return None

    return run


def _ok_correct(output_path, **kwargs):
    with h5py.File(output_path, "r+") as f:
        f.require_group("settings/correction")
    return "corrected"


def test_batch_summary_lists_only_the_flagged_movies(tmp_path) -> None:
    from tether.project.batch import MovieJob, run_batch

    records = {
        "flipped": _record("swapped", 3, 41),
        "fine": _record("consistent", 40, 2),
        "sparse": _record("insufficient", 2, 1),
    }
    jobs = [
        MovieJob(movie_path=tmp_path / f"{stem}.tif", output_path=tmp_path / f"{stem}.tether")
        for stem in ("flipped", "fine", "sparse", "old")
    ]
    summary = run_batch(jobs, idealize=False, _extract=_side_stub(records), _correct=_ok_correct)
    assert [r.job.label for r in summary.donor_side_flagged] == ["flipped"]
    report = summary.format_report()
    assert "halves look swapped in 1 movie(s)" in report
    assert "flipped: 41 of 44 bleach events are consistent with swapped" in report
    assert "--donor-side right" in report
    for quiet in ("fine:", "sparse:", "old:"):
        assert quiet not in report
    flagged_line = next(line for line in report.splitlines() if "flipped" in line)
    assert "halves-swapped?" in flagged_line
    assert summary.n_failed == 0  # advisory: the flagged movie still succeeds

    # A resumed run skips the extraction and still reports it, from the provenance.
    def never(*args, **kwargs):
        raise AssertionError("extract should be skipped on resume")

    resumed = run_batch(jobs, idealize=False, _extract=never, _correct=_ok_correct)
    assert [r.job.label for r in resumed.donor_side_flagged] == ["flipped"]
