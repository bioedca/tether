# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The reconciliation of a new trace generation with the one before it (#923, ADR-0136).

One movie, extracted three times, gives each case the issue names:

* generation 1 (high detection threshold) finds the two bright spots. Its stored position
  of the second one is moved by 2 px, as if that extraction had found it elsewhere;
* generation 2 (low threshold) finds the bright spot that **stayed** (carried), the spot
  that **moved more than the limit** (new, and its generation-1 partner lost) and the dim
  spot it now detects (**new**);
* generation 3 (high threshold again) loses the dim spot (**lost**).

The tests cover the three lists, the carried labels with the original curator and time,
the confirmation (who and when), the counts of ``tether status``, and the service command
that ``tether reextract`` and a recipe step run.
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

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from _generation_store import (  # noqa: E402
    DONOR,
    HIGH,
    LOW,
    SHAPE,
    T0,
    T1,
    first_generation,
    key_at,
    movie_file,
    movie_id,
    options,
    reextract,
    text,
)
from tether.cli import main  # noqa: E402
from tether.io.generations import carried_molecule_keys, read_generation  # noqa: E402
from tether.project import generation_reconcile as gr  # noqa: E402
from tether.project import labels  # noqa: E402
from tether.project.core import Project  # noqa: E402
from tether.project.extract import extract_movie  # noqa: E402
from tether.project.readiness import read_project_readiness  # noqa: E402
from tether.services import ReextractRequest, run  # noqa: E402
from tether.services.profile import ExtractionProfile, write_profile  # noqa: E402


def test_the_lists_hold_a_spot_that_stayed_one_that_moved_and_a_new_one(tmp_path) -> None:
    project, movie, stayed, moved = first_generation(tmp_path)
    reextract(project, movie, LOW)

    rec = gr.reconcile_generation(project, movie_id(project))
    assert rec is not None and (rec.generation, rec.previous) == (2, 1)
    assert [m.molecule_key for m in rec.carried] == [stayed]
    carried = rec.carried[0]
    # The label, category and window moved, and the original curator and time are kept.
    assert carried.curation_label == labels.CurationLabel.ACCEPT
    assert carried.category == "folded"
    assert carried.analysis_window == (2, 9)
    assert (carried.labeler, carried.labelled_utc) == ("original", T0)
    assert carried.waiting and carried.confirmed_by == ""

    # The moved spot is new (a fresh key, no labels), and so is the dim spot.
    new = {m.molecule_key: m for m in rec.new}
    assert moved not in new and len(new) == 2
    assert {m.curation_label for m in new.values()} == {labels.CurationLabel.UNCURATED}
    assert all(m.labeler == "" for m in new.values())
    assert any(np.allclose(m.donor_xy, DONOR[2], atol=0.3) for m in new.values())

    # Its generation-1 partner is lost, and it and its label stay readable.
    assert [m.molecule_key for m in rec.lost] == [moved]
    lost = rec.lost[0]
    assert lost.curation_label == labels.CurationLabel.REJECT
    assert (lost.labeler, lost.labelled_utc) == ("original", T0)
    old = read_generation(project, movie_id(project), 1).molecules
    assert moved in {text(k) for k in old["molecule_key"]}
    assert {text(row["molecule_key"]) for row in labels.read_labels(project)} >= {moved}

    assert rec.counts == gr.ReconciliationCounts(carried=1, new=2, lost=1, waiting=1, movies=1)


def test_a_spot_the_new_generation_does_not_find_is_lost(tmp_path) -> None:
    project, movie, _stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    dim = key_at(project, *DONOR[2])
    labels.reject(project, dim, labeler="second", timestamp=T1)

    reextract(project, movie, HIGH)

    rec = gr.reconcile_generation(project, movie_id(project))
    assert rec is not None and (rec.generation, rec.previous) == (3, 2)
    assert [m.molecule_key for m in rec.lost] == [dim]
    assert (rec.lost[0].labeler, rec.lost[0].curation_label) == ("second", -1)
    assert rec.new == ()
    assert len(rec.carried) == 2
    assert dim in {
        text(k) for k in read_generation(project, movie_id(project), 2).molecules["molecule_key"]
    }


def test_a_movie_extracted_once_has_nothing_to_reconcile(tmp_path) -> None:
    movie = movie_file(tmp_path)
    project = tmp_path / "once.tether"
    extract_movie(movie, project, options=options(HIGH))
    assert gr.reconcile_generation(project, movie_id(project)) is None
    assert gr.reconcile_project(project) == ()
    assert gr.reconciliation_counts(project) == gr.ReconciliationCounts()
    with pytest.raises(KeyError):
        gr.reconcile_generation(project, "no-such-movie")


def test_a_confirmation_removes_the_mark_and_records_who_and_when(tmp_path) -> None:
    project, movie, stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    before = labels.read_labels(project)
    assert carried_molecule_keys(project) == {stayed}

    result = Project(project).confirm_carried([stayed], labeler="reviewer", timestamp=T1)

    assert result.confirmed == (stayed,) and result.already == ()
    assert (result.labeler, result.timestamp) == ("reviewer", T1)
    assert carried_molecule_keys(project) == set()
    carried = gr.reconcile_generation(project, movie_id(project)).carried[0]
    assert not carried.waiting
    assert (carried.confirmed_by, carried.confirmed_utc) == ("reviewer", T1)
    # The original curator is still the one who labelled it, and the label is unchanged.
    assert (carried.labeler, carried.labelled_utc) == ("original", T0)
    assert carried.curation_label == labels.CurationLabel.ACCEPT
    after = labels.read_labels(project)
    assert len(after) == len(before) + 1
    for name in before.dtype.names:
        np.testing.assert_array_equal(after[: len(before)][name], before[name])
    row = after[-1]
    assert (text(row["molecule_key"]), text(row["labeler"]), text(row["source"])) == (
        stayed,
        "reviewer",
        labels.LABEL_SOURCE_HUMAN,
    )
    assert int(row["label_value"]) == labels.CurationLabel.ACCEPT

    # A second confirmation writes nothing.
    again = gr.confirm_carried(project, [stayed], labeler="reviewer", timestamp=T1)
    assert again.confirmed == () and again.already == (stayed,)
    assert len(labels.read_labels(project)) == len(after)


def test_a_confirmation_does_not_become_the_curator_of_the_next_generation(tmp_path) -> None:
    # Labelled by "original" in generation 1, confirmed in 2, carried again into 3.
    project, movie, stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    gr.confirm_carried(project, [stayed], labeler="reviewer", timestamp=T1)
    reextract(project, movie, LOW)

    carried = {
        m.molecule_key: m for m in gr.reconcile_generation(project, movie_id(project)).carried
    }
    assert carried[stayed].waiting
    assert (carried[stayed].labeler, carried[stayed].labelled_utc) == ("original", T0)


def test_a_confirmed_reject_keeps_its_reason(tmp_path) -> None:
    project, movie, _stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    dim = key_at(project, *DONOR[2])
    labels.reject(
        project,
        dim,
        reason=labels.REJECT_REASON_OTHER,
        note="too dim",
        labeler="second",
        timestamp=T0,
    )
    reextract(project, movie, LOW)
    assert dim in carried_molecule_keys(project)

    gr.confirm_carried(project, [dim], labeler="reviewer", timestamp=T1)

    record = labels.reject_record_of(project, dim)
    assert record is not None
    assert (text(record["reason"]), text(record["note"])) == (labels.REJECT_REASON_OTHER, "too dim")
    assert (text(record["labeler"]), text(record["timestamp"])) == ("reviewer", T1)
    carried = {
        m.molecule_key: m for m in gr.reconcile_generation(project, movie_id(project)).carried
    }
    assert (carried[dim].labeler, carried[dim].confirmed_by) == ("second", "reviewer")


def test_an_uncurated_carried_molecule_is_confirmed_too(tmp_path) -> None:
    project, movie, _stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    reextract(project, movie, LOW)
    rec = gr.reconcile_generation(project, movie_id(project))
    uncurated = [m.molecule_key for m in rec.carried if m.curation_label == 0]
    assert uncurated and all(m.waiting for m in rec.carried)

    gr.confirm_carried(project, uncurated, labeler="reviewer", timestamp=T1)

    rec = gr.reconcile_generation(project, movie_id(project))
    assert {m.molecule_key for m in rec.waiting} == {
        m.molecule_key for m in rec.carried if m.curation_label != 0
    }
    assert all(m.curation_label == 0 for m in rec.carried if m.molecule_key in uncurated)


def test_an_accept_of_a_carried_molecule_confirms_it(tmp_path) -> None:
    project, movie, stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    labels.reject(project, stayed, labeler="reviewer", timestamp=T1)
    carried = gr.reconcile_generation(project, movie_id(project)).carried[0]
    assert (carried.confirmed_by, carried.confirmed_utc) == ("reviewer", T1)
    assert (carried.labeler, carried.labelled_utc) == ("original", T0)


def test_a_key_that_is_not_carried_is_refused_and_nothing_is_written(tmp_path) -> None:
    project, movie, stayed, moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    before = labels.read_labels(project)
    with pytest.raises(KeyError, match="not a carried molecule"):
        gr.confirm_carried(project, [stayed, moved])
    with pytest.raises(ValueError, match="UTC offset"):
        gr.confirm_carried(project, [stayed], timestamp="2026-01-01T00:00:00")
    assert len(labels.read_labels(project)) == len(before)
    assert carried_molecule_keys(project) == {stayed}


def test_a_project_held_by_another_writer_refuses_the_confirmation(tmp_path) -> None:
    from tether.project.lock import LockedError, LockIdentity

    project, movie, stayed, _moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    owner = Project(project, identity=LockIdentity(host="HOST-A", user="alice", pid=111))
    owner.acquire_lock()
    other = Project(project, identity=LockIdentity(host="HOST-B", user="bob", pid=222))
    with pytest.raises(LockedError):
        other.confirm_carried([stayed])
    assert carried_molecule_keys(project) == {stayed}
    assert owner.confirm_carried([stayed]).confirmed == (stayed,)
    owner.release_lock()


def test_status_shows_the_three_counts(tmp_path, capsys) -> None:
    project, movie, _stayed, _moved = first_generation(tmp_path)
    readiness = read_project_readiness(project)
    assert readiness.reconciliation == gr.ReconciliationCounts()
    reextract(project, movie, LOW)

    readiness = read_project_readiness(project)
    expected = {"movies": 1, "carried": 1, "new": 2, "lost": 1, "waiting": 1}
    assert readiness.to_dict()["reconciliation"] == expected

    assert main(["status", "--json", str(project)]) == 0
    assert json.loads(capsys.readouterr().out)["reconciliation"] == expected
    assert main(["status", str(project)]) == 0
    out = capsys.readouterr().out
    assert (
        "new trace generations: 1 movie(s), 1 carried (1 waiting for confirmation), 2 new, 1 lost"
    ) in out


def _profile(tmp_path: Path, threshold: float) -> Path:
    path = tmp_path / f"profile-{threshold}.json"
    write_profile(
        ExtractionProfile(window=21, detection_mode="intensity", detection_threshold=threshold),
        path,
    )
    return path


def test_the_service_command_makes_the_same_move(tmp_path, monkeypatch) -> None:
    project, movie, stayed, moved = first_generation(tmp_path)
    request = ReextractRequest(
        project=str(project),
        movie=str(movie),
        window=21,
        detection_mode="intensity",
        detection_threshold=LOW,
    )
    from tether.project.lock import lock_path

    real = gr.reconcile_generation
    locked: list[bool] = []

    def reconcile(path, movie):
        locked.append(lock_path(path).exists())
        return real(path, movie)

    monkeypatch.setattr(gr, "reconcile_generation", reconcile)
    result = run(request)
    # The lists are read under the lock of the extraction, so they are of its generation.
    assert locked == [True]
    assert result.summary.generation.generation == 2
    assert result.summary.n_carried == 1
    assert [m.molecule_key for m in result.reconciliation.carried] == [stayed]
    assert [m.molecule_key for m in result.reconciliation.lost] == [moved]
    assert result.reconciliation.counts == gr.reconciliation_counts(project)


def test_tether_reextract_prints_the_counts(tmp_path, capsys) -> None:
    project, movie, _stayed, _moved = first_generation(tmp_path)
    code = main(["reextract", str(project), str(movie), "--profile", str(_profile(tmp_path, LOW))])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "as generation 2 of movie" in out
    assert "1 carried (1 waiting for confirmation), 2 new, 1 lost" in out

    # A movie the project does not hold is refused, and the project is unchanged.
    other = tmp_path / "other"
    other.mkdir()
    stranger = other / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_011.tif"
    tifffile.imwrite(
        stranger, np.zeros((4, *SHAPE), dtype=">u2"), byteorder=">", photometric="minisblack"
    )
    assert main(["reextract", str(project), str(stranger)]) == 1
    assert "is not in" in capsys.readouterr().err
    assert gr.reconcile_generation(project, movie_id(project)).generation == 2


def test_a_recipe_step_makes_the_same_move(tmp_path) -> None:
    from tether.services import recipe as rc

    project, movie, stayed, _moved = first_generation(tmp_path)
    step = ReextractRequest(
        project=str(project),
        movie=str(movie),
        window=21,
        detection_mode="intensity",
        detection_threshold=LOW,
    )
    path = rc.write_recipe(tmp_path / "again.tether-recipe", [step])
    outcome = rc.run_recipe(rc.read_recipe(path))
    assert not outcome.failed, outcome.steps
    assert [s.state for s in outcome.steps] == ["done"]
    rec = gr.reconcile_generation(project, movie_id(project))
    assert rec is not None and rec.generation == 2
    assert [m.molecule_key for m in rec.carried] == [stayed]
    # The step is stored as a job of the project, so Save as recipe gives it back.
    saved = rc.recipe_from_project(project)
    assert [r.COMMAND for r in saved] == ["reextract"]
