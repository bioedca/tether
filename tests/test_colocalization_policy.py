# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The colocalization policy end to end: default, choice, storage, counts (#920, ADR-0127).

A new extraction keeps a molecule only when an acceptor spot was detected at its mapped
position (``partner-in-both``) unless the user selects ``donor-anchored``. The project
stores the acceptor-detection flag of every molecule and the rule of every movie, an
existing project reads as donor-anchored with its molecules unchanged, and the molecule
count of each rule is available before a run, from the library and from ``tether extract``.

The synthetic movie has four donor spots; the fourth one's acceptor is dark (no acceptor
spot anywhere near its mapped position), so the two rules keep 3 and 4 molecules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

import h5py  # noqa: E402
import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tether import services  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.extract_choices import (  # noqa: E402
    COLOCALIZATION_POLICIES,
    DEFAULT_COLOCALIZATION_POLICY,
    DONOR_ANCHORED,
    PARTNER_IN_BOTH,
)
from tether.imaging.extract import (  # noqa: E402
    ACCEPTOR_DETECTED_NOT_RECORDED,
    read_acceptor_detected,
    read_colocalization_policies,
    read_molecules,
    read_movie_settings,
)
from tether.project import lock  # noqa: E402
from tether.project.export import export_subset_tether  # noqa: E402
from tether.project.extract import (  # noqa: E402
    ExtractionError,
    ExtractOptions,
    add_movie_to_project,
    count_molecules_by_rule,
    extract_movie,
)

_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_SHAPE = (64, 96)  # 48-px halves
_N_FRAMES = 12
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0], [36.0, 26.0]])
_SHIFT = np.array([49.0, 0.0])  # acceptor copy: +1 px in x inside its own half
_DARK = 3  # the row of _DONOR whose acceptor is dark
_COUNTS = {PARTNER_IN_BOTH: 3, DONOR_ANCHORED: 4}


def _write_movie(path: Path, donor: np.ndarray = _DONOR, dark: tuple[int, ...] = (_DARK,)) -> Path:
    lit = np.array([xy for i, xy in enumerate(donor) if i not in dark])
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    frame = np.full(_SHAPE, _BG)
    for x, y in np.vstack([donor, lit + _SHIFT]):
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    stack = np.broadcast_to(frame, (_N_FRAMES, *_SHAPE))
    tifffile.imwrite(path, np.ascontiguousarray(stack, dtype=">u2"), byteorder=">")
    return path


def _movie(tmp_path: Path, name: str = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif") -> Path:
    return _write_movie(tmp_path / name)


def _dark_row_mask(project: Path) -> np.ndarray:
    """``True`` on the stored row of the dark-acceptor donor."""
    donor_xy = read_molecules(project)["donor_xy"]
    return np.all(np.isclose(donor_xy, _DONOR[_DARK], atol=0.6), axis=1)


# --- the policy values and the default ----------------------------------------------


def test_the_two_rules_and_the_default() -> None:
    assert COLOCALIZATION_POLICIES == (PARTNER_IN_BOTH, DONOR_ANCHORED)
    assert DEFAULT_COLOCALIZATION_POLICY == PARTNER_IN_BOTH
    assert ExtractOptions().colocalization == PARTNER_IN_BOTH
    assert services.ExtractRequest(movie="m", output="o").colocalization == PARTNER_IN_BOTH
    assert services.BatchRequest(movies=("m",), out_dir="d").colocalization == PARTNER_IN_BOTH


def test_an_unknown_rule_is_refused() -> None:
    with pytest.raises(ExtractionError, match="colocalization must be one of"):
        ExtractOptions(colocalization="any-channel")


# --- a new extraction: the rule, the stored flag, the stored rule ---------------------


def test_a_new_extraction_keeps_only_molecules_with_a_detected_acceptor(tmp_path) -> None:
    out = tmp_path / "partner.tether"
    summary = extract_movie(_movie(tmp_path), out)
    assert summary.colocalization == PARTNER_IN_BOTH
    assert summary.n_molecules == 3
    assert summary.rule_counts == _COUNTS
    assert not _dark_row_mask(out).any()
    assert read_acceptor_detected(out).tolist() == [1, 1, 1]
    (policy,) = read_colocalization_policies(out).values()
    assert policy == PARTNER_IN_BOTH


def test_donor_anchored_keeps_the_dark_acceptor_and_stores_its_flag(tmp_path) -> None:
    out = tmp_path / "anchored.tether"
    options = ExtractOptions(colocalization=DONOR_ANCHORED)
    summary = extract_movie(_movie(tmp_path), out, options=options)
    assert summary.colocalization == DONOR_ANCHORED
    assert summary.n_molecules == 4
    assert summary.rule_counts == _COUNTS
    flags = read_acceptor_detected(out)
    dark = _dark_row_mask(out)
    assert dark.sum() == 1
    assert flags[dark].tolist() == [0]
    assert flags[~dark].tolist() == [1, 1, 1]
    (profile,) = read_movie_settings(out).values()
    assert profile["colocalization"] == DONOR_ANCHORED


def _writer_given(monkeypatch, edit) -> None:
    """Make ``extract_movie`` hand the shared writer a settings profile changed by ``edit``."""
    from tether.imaging import extract as writer_mod  # noqa: PLC0415
    from tether.project import extract as extract_mod  # noqa: PLC0415

    def write(*args, settings=None, **kwargs):
        return writer_mod.write_extraction(*args, settings=edit(settings), **kwargs)

    monkeypatch.setattr(extract_mod, "write_extraction", write)


@pytest.mark.parametrize("edit", [lambda s: None, lambda s: {"window": 21}])
def test_the_writer_stamps_the_rule_of_the_molecules(tmp_path, monkeypatch, edit) -> None:
    """A caller that passes no rule still gets the rule its molecules were kept by."""
    _writer_given(monkeypatch, edit)
    out = tmp_path / "partner.tether"
    extract_movie(_movie(tmp_path), out)
    (policy,) = read_colocalization_policies(out).values()
    assert policy == PARTNER_IN_BOTH


def test_the_writer_refuses_a_rule_its_molecules_were_not_kept_by(tmp_path, monkeypatch) -> None:
    _writer_given(monkeypatch, lambda s: {**s, "colocalization": DONOR_ANCHORED})
    out = tmp_path / "conflict.tether"
    with pytest.raises((ValueError, ExtractionError), match="colocalization"):
        extract_movie(_movie(tmp_path), out)


# --- an existing project --------------------------------------------------------------


def _make_legacy(project: Path) -> None:
    """Strip what #920 adds, leaving a project as one extracted before it looks."""
    with h5py.File(project, "r+") as f:
        del f["molecules/acceptor_detected"]
        grp = f["settings/extraction"]
        profile = json.loads(grp.attrs["profile_json"])
        profile.pop("colocalization")
        grp.attrs["profile_json"] = json.dumps(profile, sort_keys=True)
        movies = grp["movies"]
        for movie_id in list(movies.attrs):
            record = json.loads(movies.attrs[movie_id])
            record.pop("colocalization")
            movies.attrs[movie_id] = json.dumps(record, sort_keys=True)


def test_an_existing_project_reads_as_donor_anchored_and_unrecorded(tmp_path) -> None:
    out = tmp_path / "legacy.tether"
    extract_movie(_movie(tmp_path), out, options=ExtractOptions(colocalization=DONOR_ANCHORED))
    _make_legacy(out)
    assert set(read_colocalization_policies(out).values()) == {DONOR_ANCHORED}
    assert read_acceptor_detected(out).tolist() == [ACCEPTOR_DETECTED_NOT_RECORDED] * 4


def test_appending_to_an_existing_project_leaves_its_molecules_unchanged(tmp_path) -> None:
    out = tmp_path / "legacy.tether"
    extract_movie(_movie(tmp_path), out, options=ExtractOptions(colocalization=DONOR_ANCHORED))
    _make_legacy(out)
    before = read_molecules(out)
    (old_movie,) = read_colocalization_policies(out)

    second = _write_movie(
        tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif", _DONOR + [2.0, 3.0]
    )
    with lock.publish_lock(out) as guard:
        summary = add_movie_to_project(out, second, publish_guard=guard)

    after = read_molecules(out)
    for name in before.dtype.names:  # every field of every old row, unchanged
        np.testing.assert_array_equal(after[name][: len(before)], before[name], err_msg=name)
    flags = read_acceptor_detected(out)
    assert flags[: len(before)].tolist() == [ACCEPTOR_DETECTED_NOT_RECORDED] * len(before)
    assert flags[len(before) :].tolist() == [1] * summary.n_molecules
    policies = read_colocalization_policies(out)
    assert policies[old_movie] == DONOR_ANCHORED
    assert policies[summary.movie_id] == PARTNER_IN_BOTH


def test_a_flag_column_longer_than_the_molecules_is_refused(tmp_path) -> None:
    out = tmp_path / "corrupt.tether"
    extract_movie(_movie(tmp_path), out)
    with h5py.File(out, "r+") as f:
        f["molecules/acceptor_detected"].resize((10,))
    second = _write_movie(
        tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif", _DONOR + [2.0, 3.0]
    )
    with lock.publish_lock(out) as guard, pytest.raises(ExtractionError, match="acceptor_detected"):
        add_movie_to_project(out, second, publish_guard=guard)


# --- the subset export carries the flag ------------------------------------------------


def test_a_subset_carries_the_flag_of_its_molecules(tmp_path) -> None:
    project = tmp_path / "anchored.tether"
    extract_movie(_movie(tmp_path), project, options=ExtractOptions(colocalization=DONOR_ANCHORED))
    dark = _dark_row_mask(project)
    keys = [
        str(k.decode() if isinstance(k, bytes) else k)
        for k in read_molecules(project)["molecule_key"]
    ]
    wanted = [keys[int(np.flatnonzero(dark)[0])], keys[int(np.flatnonzero(~dark)[0])]]
    out = tmp_path / "subset.tether"
    export_subset_tether(project, out, molecule_keys=wanted, include_rejected=True)
    sub_keys = [
        str(k.decode() if isinstance(k, bytes) else k) for k in read_molecules(out)["molecule_key"]
    ]
    flags = dict(zip(sub_keys, read_acceptor_detected(out).tolist(), strict=True))
    assert flags == {wanted[0]: 0, wanted[1]: 1}


# --- the counts before the run -----------------------------------------------------------


def test_the_counts_come_before_the_run_and_nothing_is_written(tmp_path) -> None:
    movie = _movie(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert count_molecules_by_rule(movie) == _COUNTS
    assert count_molecules_by_rule(
        movie, options=ExtractOptions(colocalization=DONOR_ANCHORED)
    ) == (_COUNTS)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_count_that_cannot_register_is_a_clean_error(tmp_path) -> None:
    movie = _write_movie(tmp_path / "Bla_010.tif", _DONOR[:1], dark=())
    with pytest.raises(ExtractionError, match="the pair stage failed"):
        count_molecules_by_rule(movie)


def test_cli_count_only_prints_both_counts_and_writes_nothing(tmp_path, capsys) -> None:
    movie = _movie(tmp_path)
    out = tmp_path / "never.tether"
    assert main(["extract", str(movie), "-o", str(out), "--count-only"]) == 0
    text = capsys.readouterr().out
    assert "partner-in-both: 3 (selected)" in text
    assert "donor-anchored: 4" in text
    assert not out.exists()


def test_cli_extract_names_the_rule_and_the_other_count(tmp_path, capsys) -> None:
    movie = _movie(tmp_path)
    out = tmp_path / "anchored.tether"
    argv = ["extract", str(movie), "-o", str(out), "--colocalization", "donor-anchored"]
    assert main(argv) == 0
    text = capsys.readouterr().out
    assert "Extracted 4 molecule(s)" in text
    assert "colocalization: donor-anchored kept 4 (partner-in-both would keep 3)" in text


def test_cli_batch_passes_the_rule_to_every_extraction(tmp_path, capsys) -> None:
    movie = _movie(tmp_path)
    out_dir = tmp_path / "batch"
    argv = ["batch", str(movie), "-d", str(out_dir), "--no-idealize"]
    assert main([*argv, "--colocalization", "donor-anchored"]) == 0
    (project,) = out_dir.glob("*.tether")
    assert set(read_colocalization_policies(project).values()) == {DONOR_ANCHORED}
    assert len(read_molecules(project)) == 4


@pytest.mark.parametrize(
    ("argv", "code"),
    [(["extract", "m.tif", "-o", "o.tether"], 1), (["batch", "a.tif", "-d", "out"], 2)],
)
def test_cli_refuses_the_rule_flag_beside_a_profile(tmp_path, capsys, argv, code) -> None:
    # A profile carries the rule (#349), so the flag beside it is refused like the other
    # option flags, even at its default; the rule is then changed in the profile.
    profile = tmp_path / "profile.json"
    services.write_profile(services.ExtractionProfile(colocalization=DONOR_ANCHORED), profile)
    flags = ["--profile", str(profile), "--colocalization", "partner-in-both"]
    assert main([*argv, *flags]) == code
    assert "--profile cannot be combined with --colocalization" in capsys.readouterr().err
