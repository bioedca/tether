# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether batch --into PROJECT.tether``: every movie of a batch in one project (#1151).

The acceptance criteria of #1151, each driven through the real command line on synthetic
movies (the real extract, append and correct stages; idealization needs the sidecar, so it
is off or stubbed):

* ``tether batch <folder> --into P.tether`` gives one project holding every movie;
* running the same command again appends no movie twice;
* ``--dry-run --into P.tether`` writes nothing and prints one line for each movie with the
  project it goes to;
* ``--into`` together with ``--out-dir`` is a usage error.

Then the run's own rules: correct and idealize run once over the whole project, again when
a movie is appended and when an earlier run left them unfinished; ``--policy fail`` keeps
an over-gate movie out of the project; two copies of one file go in once; a movie that is
not in the project has its project stages ``blocked``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

for _module in ("h5py", "numpy", "scipy", "skimage", "tifffile"):
    pytest.importorskip(_module)

import h5py  # noqa: E402

import test_extract_cli as helpers  # noqa: E402
from tether.cli import main  # noqa: E402
from tether.idealize.supervisor import SidecarSupervision  # noqa: E402
from tether.project import lock  # noqa: E402
from tether.project.batch import (  # noqa: E402
    POLICY_FAIL,
    STAGE_CORRECT,
    STAGE_EXTRACT,
    STAGE_IDEALIZE,
    STATUS_BLOCKED,
    STATUS_DEFERRED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NOT_REQUESTED,
    STATUS_SKIPPED,
    MovieJob,
    run_batch_into,
)
from tether.project.batch_inputs import describe_append, movie_in_project  # noqa: E402
from tether.project.extract import (  # noqa: E402
    OVER_GATE_ERROR,
    ExtractionError,
    ExtractOptions,
    add_movie_to_project,
    extract_movie,
)
from tether.services.base import RequestRefused  # noqa: E402
from tether.services.batch import BatchRequest, plan, run  # noqa: E402


def _movie(folder: Path, name: str, amp: float) -> Path:
    """A synthetic movie; ``amp`` makes its content, and so its identity, its own."""
    folder.mkdir(parents=True, exist_ok=True)
    return helpers._make_movie(folder, name=name, acceptor_amp=amp)


def _folder(tmp_path: Path, n: int = 3) -> Path:
    data = tmp_path / "data"
    for i in range(n):
        _movie(data, f"movie_{i:03d}.tif", 300.0 + 40.0 * i)
    return data


def _movies(project: Path) -> list[str]:
    with h5py.File(project, "r") as f:
        return sorted(
            (v.decode() if isinstance(v, bytes) else str(v)) for v in f["movies/table"]["uri"]
        )


def _n_molecules(project: Path) -> int:
    with h5py.File(project, "r") as f:
        return int(f["molecules/table"].shape[0])


def _batch_attrs(project: Path) -> dict[str, object]:
    with h5py.File(project, "r") as f:
        return dict(f["settings/batch"].attrs)


# --- the acceptance criteria, through the command line ------------------------


def test_a_folder_goes_into_one_project_that_holds_every_movie(tmp_path: Path, capsys) -> None:
    data = _folder(tmp_path)
    project = tmp_path / "P.tether"

    rc = main(["batch", str(data), "--into", str(project), "--no-idealize", "--quiet"])

    out = capsys.readouterr().out
    assert rc == 0, out
    assert sorted(Path(u).name for u in _movies(project)) == [
        "movie_000.tif",
        "movie_001.tif",
        "movie_002.tif",
    ]
    assert _n_molecules(project) == 9  # three molecules in each movie
    assert not list(tmp_path.glob("data/*.tether"))  # no project for each movie
    assert "3 movie(s), 3 ok, 0 failed" in out
    assert out.count("extract=done correct=done") == 3
    attrs = _batch_attrs(project)
    assert attrs["extract_status"] == STATUS_DONE
    assert attrs["correct_status"] == STATUS_DONE
    log = tmp_path / "P-batch-log.jsonl"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    events = [(r["movie"], r["stage"], r["status"]) for r in records]
    assert ("movie_000", "extract", "done") in events
    assert ("(batch)", "correct", "done") in events  # the project's stage, logged once


def test_the_same_command_again_appends_no_movie_twice(tmp_path: Path, capsys) -> None:
    data = _folder(tmp_path)
    project = tmp_path / "P.tether"
    command = ["batch", str(data), "--into", str(project), "--no-idealize", "--quiet"]
    assert main(command) == 0
    before = (_movies(project), _n_molecules(project))
    capsys.readouterr()

    rc = main(command)

    out = capsys.readouterr().out
    assert rc == 0, out
    assert (_movies(project), _n_molecules(project)) == before
    assert out.count("extract=skipped correct=skipped") == 3


def test_a_movie_added_to_the_folder_is_appended_and_the_project_corrected_again(
    tmp_path: Path, capsys
) -> None:
    data = _folder(tmp_path, n=2)
    project = tmp_path / "P.tether"
    command = ["batch", str(data), "--into", str(project), "--no-idealize", "--quiet"]
    assert main(command) == 0
    _movie(data, "movie_late.tif", 520.0)
    capsys.readouterr()

    rc = main(command)

    out = capsys.readouterr().out
    assert rc == 0, out
    assert len(_movies(project)) == 3
    assert _n_molecules(project) == 9
    # The earlier movies are skipped, and the project's correction covers the new one.
    assert out.count("extract=skipped correct=done") == 2
    assert out.count("extract=done correct=done") == 1


def test_a_dry_run_writes_nothing_and_names_the_project_of_each_movie(
    tmp_path: Path, capsys
) -> None:
    data = _folder(tmp_path)
    project = tmp_path / "P.tether"

    rc = main(["batch", str(data), "--into", str(project), "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0, out
    assert not project.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data"]
    lines = [line for line in out.splitlines() if line.startswith("  ")]
    assert len(lines) == 3
    assert all(f"-> {project}  [" in line for line in lines)
    assert lines[0].endswith("[new project: will be created with this movie]")
    assert lines[1].endswith("[will be appended]")
    assert "Project: correct and idealize will run over every movie" in out


def test_a_dry_run_names_the_movies_already_in_the_project(tmp_path: Path, capsys) -> None:
    data = _folder(tmp_path, n=2)
    project = tmp_path / "P.tether"
    assert main(["batch", str(data), "--into", str(project), "--no-idealize", "--quiet"]) == 0
    _movie(data, "movie_late.tif", 520.0)
    stamp = project.read_bytes()
    capsys.readouterr()

    rc = main(["batch", str(data), "--into", str(project), "--no-idealize", "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0, out
    assert project.read_bytes() == stamp  # nothing was written
    lines = {
        Path(line.split(" -> ")[0].strip()).name: line
        for line in out.splitlines()
        if line.startswith("  ")
    }
    assert "already in the project as movie" in lines["movie_000.tif"]
    assert "will be skipped" in lines["movie_001.tif"]
    assert lines["movie_late.tif"].endswith("[will be appended]")


def test_into_with_out_dir_is_a_usage_error_that_names_both(tmp_path: Path, capsys) -> None:
    movie = _movie(tmp_path, "movie_000.tif", 300.0)
    with pytest.raises(SystemExit) as exc:
        main(["batch", str(movie), "--into", str(tmp_path / "P.tether"), "-d", str(tmp_path)])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--into" in err
    assert "--out-dir" in err


def test_a_batch_needs_out_dir_or_into(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["batch", str(tmp_path / "m.tif")])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--out-dir" in err
    assert "--into" in err


# --- the request ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "words"),
    [
        ({"out_dir": "out", "into": "P.tether"}, "exactly one of --out-dir"),
        ({}, "exactly one of --out-dir"),
        ({"into": "P.tether", "overwrite": True}, "--overwrite cannot be combined with --into"),
    ],
)
def test_the_request_refuses_a_destination_it_cannot_use(
    tmp_path: Path, fields: dict[str, object], words: str
) -> None:
    movie = _movie(tmp_path, "movie_000.tif", 300.0)
    request = BatchRequest(movies=(str(movie),), idealize=False, **fields)
    for call in (plan, run):
        with pytest.raises(RequestRefused, match=words):
            call(request)
    assert not (tmp_path / "P.tether").exists()


def test_movies_with_one_file_name_keep_their_own_names_in_the_summary(tmp_path: Path) -> None:
    a = _movie(tmp_path / "day1", "movie_010.tif", 300.0)
    b = _movie(tmp_path / "day2", "movie_010.tif", 360.0)
    project = tmp_path / "P.tether"

    result = run(BatchRequest(movies=(str(a), str(b)), into=str(project), idealize=False))

    assert [r.job.label for r in result.summary.results] == [
        "day1__movie_010",
        "day2__movie_010",
    ]
    assert len(_movies(project)) == 2


# --- the runner ----------------------------------------------------------------


def _idealize_recorder(calls: list[dict[str, object]], *, fail: bool = False):
    def run_idealize(output_path, **kwargs):
        calls.append(kwargs)
        if fail:
            raise RuntimeError("idealize boom")
        with h5py.File(output_path, "r+") as f:
            f.require_group("idealization/vbconhmm")
        return SimpleNamespace(model_name="vbconhmm", nstates=2, molecule_keys=["a"])

    return run_idealize


def _into_jobs(project: Path, *movies: Path) -> list[MovieJob]:
    return [MovieJob(m, project, name=m.stem) for m in movies]


def test_correct_and_idealize_run_once_for_the_whole_project(tmp_path: Path) -> None:
    movies = [_movie(tmp_path, f"m{i}.tif", 300.0 + 40.0 * i) for i in range(3)]
    project = tmp_path / "P.tether"
    corrected: list[Path] = []
    idealized: list[dict[str, object]] = []

    def correct(path, **_kwargs):
        corrected.append(Path(path))
        with h5py.File(path, "r+") as f:
            f.require_group("settings/correction")
        return "stub"

    summary = run_batch_into(
        _into_jobs(project, *movies),
        project,
        _correct=correct,
        _idealize=_idealize_recorder(idealized),
    )

    assert corrected == [project]
    assert len(idealized) == 1
    for result in summary.results:
        assert result.stages[STAGE_EXTRACT].status == STATUS_DONE
        assert result.stages[STAGE_CORRECT].status == STATUS_DONE
        assert result.stages[STAGE_IDEALIZE].status == STATUS_DONE


def test_an_idealization_left_deferred_runs_on_the_next_run_over_the_stored_model(
    tmp_path: Path,
) -> None:
    first = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    calls: list[dict[str, object]] = []
    run_batch_into(_into_jobs(project, first), project, _idealize=_idealize_recorder(calls))
    assert len(calls) == 1
    second = _movie(tmp_path, "m1.tif", 360.0)

    # The sidecar is down when the second movie is appended: the model is now stale.
    supervision = SidecarSupervision(max_restarts=0, timeout=1.0)
    deferred = run_batch_into(
        _into_jobs(project, first, second),
        project,
        supervision=supervision,
        _probe=lambda _s: SimpleNamespace(available=False, detail="down"),
        _idealize=_idealize_recorder(calls),
    )
    assert [r.stages[STAGE_IDEALIZE].status for r in deferred.results] == [STATUS_DEFERRED] * 2
    assert _batch_attrs(project)["idealize_movies"] == ""
    assert len(calls) == 1

    # The next run appends nothing, yet it fits the model again over the stored one.
    resumed = run_batch_into(
        _into_jobs(project, first, second), project, _idealize=_idealize_recorder(calls)
    )
    assert [r.stages[STAGE_EXTRACT].status for r in resumed.results] == [STATUS_SKIPPED] * 2
    assert [r.stages[STAGE_IDEALIZE].status for r in resumed.results] == [STATUS_DONE] * 2
    assert calls[-1]["overwrite"] is True
    assert _batch_attrs(project)["idealize_movies"] == _batch_attrs(project)["correct_movies"]

    # And the run after that skips both stages.
    done = run_batch_into(
        _into_jobs(project, first, second), project, _idealize=_idealize_recorder(calls)
    )
    assert len(calls) == 2
    assert {r.stages[STAGE_CORRECT].status for r in done.results} == {STATUS_SKIPPED}
    assert {r.stages[STAGE_IDEALIZE].status for r in done.results} == {STATUS_SKIPPED}


def test_a_project_no_into_run_stamped_keeps_its_checkpoint(tmp_path: Path) -> None:
    # A project made by `tether extract` and corrected elsewhere gains no movie: the run
    # must not recompute it.
    movie = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    extract_movie(movie, project, options=ExtractOptions(window=helpers._WINDOW))
    with h5py.File(project, "r+") as f:
        f.require_group("settings/correction")

    def correct(*_args, **_kwargs):
        raise AssertionError("the correction of a curated project was recomputed")

    summary = run_batch_into(
        _into_jobs(project, movie),
        project,
        extract_options=ExtractOptions(window=helpers._WINDOW),
        idealize=False,
        _correct=correct,
    )

    assert summary.results[0].stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert summary.results[0].stages[STAGE_CORRECT].status == STATUS_SKIPPED


def test_two_copies_of_one_movie_go_in_once(tmp_path: Path) -> None:
    movie = _movie(tmp_path / "a", "m0.tif", 300.0)
    copy = tmp_path / "b" / "m0_copy.tif"
    copy.parent.mkdir()
    copy.write_bytes(movie.read_bytes())
    project = tmp_path / "P.tether"

    summary = run_batch_into(_into_jobs(project, movie, copy), project, idealize=False)

    extract = [r.stages[STAGE_EXTRACT] for r in summary.results]
    assert [s.status for s in extract] == [STATUS_DONE, STATUS_SKIPPED]
    assert "already in P.tether as movie" in extract[1].detail
    assert len(_movies(project)) == 1


def test_a_failed_movie_stays_out_and_the_others_go_in(tmp_path: Path) -> None:
    good = _movie(tmp_path, "m0.tif", 300.0)
    bad = tmp_path / "m1.tif"
    bad.write_bytes(b"not a movie")
    late = _movie(tmp_path, "m2.tif", 380.0)
    project = tmp_path / "P.tether"

    summary = run_batch_into(_into_jobs(project, good, bad, late), project, idealize=False)

    stages = {r.job.label: r.stages for r in summary.results}
    assert stages["m1"][STAGE_EXTRACT].status == STATUS_FAILED
    assert stages["m1"][STAGE_CORRECT].status == STATUS_BLOCKED
    assert stages["m0"][STAGE_CORRECT].status == STATUS_DONE
    assert stages["m2"][STAGE_EXTRACT].status == STATUS_DONE
    assert len(_movies(project)) == 2
    assert summary.n_failed == 1


def test_policy_fail_keeps_an_over_gate_movie_out_of_the_project(tmp_path: Path) -> None:
    good = _movie(tmp_path, "m0.tif", 300.0)
    warped = tmp_path / "warp.tif"
    helpers._write_movie(warped, helpers._DONOR_CENTERS, helpers._OVER_GATE_ACCEPTOR)
    project = tmp_path / "P.tether"
    options = ExtractOptions(rms_gate=0.2)
    # The good movie is under even this gate; the warped one (~0.34 px) is over it.

    summary = run_batch_into(
        _into_jobs(project, warped, good),
        project,
        policy=POLICY_FAIL,
        extract_options=options,
        idealize=False,
    )

    extract = {r.job.label: r.stages[STAGE_EXTRACT] for r in summary.results}
    assert extract["warp"].status == STATUS_FAILED
    assert extract["warp"].error == OVER_GATE_ERROR
    assert extract["m0"].status == STATUS_DONE  # the first good movie created the project
    assert [Path(u).name for u in _movies(project)] == ["m0.tif"]


def test_every_job_must_go_to_the_project_with_its_own_name(tmp_path: Path) -> None:
    project = tmp_path / "P.tether"
    with pytest.raises(ValueError, match="carry a name"):
        run_batch_into([MovieJob(tmp_path / "m.tif", project)], project)
    with pytest.raises(ValueError, match="must go to"):
        run_batch_into([MovieJob(tmp_path / "m.tif", tmp_path / "Q.tether", name="m")], project)


# --- the extraction refuses an over-gate movie before it writes -----------------


def test_extract_and_append_refuse_an_over_gate_movie_and_write_nothing(tmp_path: Path) -> None:
    warped = tmp_path / "warp.tif"
    helpers._write_movie(warped, helpers._DONOR_CENTERS, helpers._OVER_GATE_ACCEPTOR)
    options = ExtractOptions(rms_gate=0.2)
    fresh = tmp_path / "fresh.tether"
    with pytest.raises(ExtractionError, match="exceeds the gate"):
        extract_movie(warped, fresh, options=options, refuse_over_gate=True)
    assert not fresh.exists()

    project = tmp_path / "P.tether"
    extract_movie(_movie(tmp_path, "m0.tif", 300.0), project, options=options)
    before = project.read_bytes()
    with lock.publish_lock(project) as guard, pytest.raises(ExtractionError, match="gate"):
        add_movie_to_project(
            project, warped, options=options, publish_guard=guard, refuse_over_gate=True
        )
    assert project.read_bytes() == before


# --- the checkpoint helpers ----------------------------------------------------


def test_a_movie_of_a_size_the_project_does_not_hold_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "P.tether"
    first = _movie(tmp_path, "m0.tif", 300.0)
    extract_movie(first, project, options=ExtractOptions(window=helpers._WINDOW))
    other = tmp_path / "other.tif"
    other.write_bytes(b"\0" * 10)

    import tether.project.extract as extract_module  # noqa: PLC0415

    hashed: list[Path] = []
    real = extract_module._hash_movie
    monkeypatch.setattr(extract_module, "_hash_movie", lambda p: hashed.append(p) or real(p))

    assert movie_in_project(project, other) is None
    assert hashed == []
    assert movie_in_project(project, first) is not None
    assert hashed == [first]
    assert describe_append(tmp_path / "absent.tether", first, first=False) == "will be appended"


# --- the rest of the runner's paths ----------------------------------------------


def test_an_unknown_policy_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "P.tether"
    with pytest.raises(ValueError, match="policy must be one of"):
        run_batch_into(_into_jobs(project, tmp_path / "m.tif"), project, policy="maybe")


def test_progress_hears_of_each_movie_and_the_project_stages(tmp_path: Path, capsys) -> None:
    data = _folder(tmp_path, n=2)
    project = tmp_path / "P.tether"

    assert main(["batch", str(data), "--into", str(project), "--no-idealize"]) == 0

    err = capsys.readouterr().err
    assert f"tether batch: 2 movie(s) into {project}" in err
    assert "[1/2] movie_000: ok" in err
    assert "[2/2] movie_001: ok" in err
    assert "tether batch: correct done" in err


def test_progress_cannot_change_what_the_run_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tether.project.batch as batch_module  # noqa: PLC0415

    monkeypatch.setattr(
        batch_module,
        "_stored_movie_donor_side_check",
        lambda _project, _movie_id: {"verdict": "ok", "ratio": 1.0},
    )

    class Meddler:
        def movie_started(self, *_args) -> None:
            pass

        def movie_finished(self, _index, _total, result) -> None:
            result.donor_side["verdict"] = "swapped"

    project = tmp_path / "P.tether"
    movie = _movie(tmp_path, "m0.tif", 300.0)
    summary = run_batch_into(
        _into_jobs(project, movie), project, idealize=False, progress=Meddler()
    )

    assert summary.results[0].donor_side == {"verdict": "ok", "ratio": 1.0}


def test_a_low_confidence_movie_is_kept_with_a_flag_under_warn(tmp_path: Path) -> None:
    warped = tmp_path / "warp.tif"
    helpers._write_movie(warped, helpers._DONOR_CENTERS, helpers._OVER_GATE_ACCEPTOR)
    project = tmp_path / "P.tether"

    summary = run_batch_into(
        _into_jobs(project, warped),
        project,
        extract_options=ExtractOptions(rms_gate=0.2),
        idealize=False,
    )

    extract = summary.results[0].stages[STAGE_EXTRACT]
    assert extract.status == STATUS_DONE
    assert extract.detail.endswith("low-confidence registration (flagged)")


def test_a_project_from_a_newer_tether_takes_no_movie(tmp_path: Path, capsys) -> None:
    movie = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    extract_movie(movie, project, options=ExtractOptions(window=helpers._WINDOW))
    with h5py.File(project, "r+") as f:
        f.attrs["schema_version"] = 999
    later = _movie(tmp_path, "m1.tif", 360.0)
    before = project.read_bytes()

    summary = run_batch_into(_into_jobs(project, later), project, idealize=False)

    stages = summary.results[0].stages
    assert stages[STAGE_EXTRACT].status == STATUS_FAILED
    assert stages[STAGE_CORRECT].status == STATUS_BLOCKED
    assert project.read_bytes() == before
    assert describe_append(project, later, first=True).startswith("will fail:")


def test_a_project_pass_that_cannot_read_the_project_fails_its_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tether.project.batch as batch_module  # noqa: PLC0415

    def unreadable(_project):
        raise OSError("the project went away")

    monkeypatch.setattr(batch_module, "_movies_digest", unreadable)
    project = tmp_path / "P.tether"
    movie = _movie(tmp_path, "m0.tif", 300.0)

    summary = run_batch_into(_into_jobs(project, movie), project, idealize=False)

    correct = summary.results[0].stages[STAGE_CORRECT]
    assert correct.status == STATUS_FAILED
    assert "the project went away" in str(correct.error)
    assert summary.n_failed == 1


def test_a_project_another_writer_owns_is_not_corrected_or_stamped(tmp_path: Path) -> None:
    # Codex P1 on #1223: the project stages hold the project's writer lock, so a project
    # an app session (or another append) owns is never written by them.
    movie = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    run_batch_into(_into_jobs(project, movie), project, idealize=False)
    stamp = _batch_attrs(project)
    calls: list[Path] = []

    def correct(path, **_kwargs):
        calls.append(Path(path))
        return "stub"

    with lock.publish_lock(project):
        summary = run_batch_into(
            _into_jobs(project, movie), project, _correct=correct, _idealize=_idealize_recorder([])
        )

    assert calls == []
    stages = summary.results[0].stages
    assert stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert stages[STAGE_CORRECT].status == STATUS_FAILED
    assert "lock" in str(stages[STAGE_CORRECT].error).lower()
    assert stages[STAGE_IDEALIZE].status == STATUS_BLOCKED
    assert _batch_attrs(project) == stamp

    with lock.publish_lock(project):
        summary = run_batch_into(_into_jobs(project, movie), project, idealize=False)
    assert summary.results[0].stages[STAGE_CORRECT].status == STATUS_FAILED
    assert summary.results[0].stages[STAGE_IDEALIZE].status == STATUS_NOT_REQUESTED


def test_the_project_stages_carry_the_writer_lock_check(tmp_path: Path) -> None:
    movie = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    guards: list[object] = []

    def correct(path, **kwargs):
        guards.append(kwargs.get("write_guard"))
        kwargs["write_guard"]()
        return "stub"

    run_batch_into(_into_jobs(project, movie), project, idealize=False, _correct=correct)

    assert len(guards) == 1
    assert callable(guards[0])
    assert lock.read_lock(project) is None


class _Cut(BaseException):
    """Stands in for the run being killed partway (it escapes every ``except Exception``)."""


def test_a_run_cut_off_after_an_append_corrects_the_project_on_the_next_run(
    tmp_path: Path,
) -> None:
    # Codex P2 on #1223: a project no --into run has stamped, corrected elsewhere, gains a
    # movie and the run is cut off before its stamp. The next run appends nothing, but
    # must still correct and idealize again, since the stored results predate the movie.
    first = _movie(tmp_path, "m0.tif", 300.0)
    second = _movie(tmp_path, "m1.tif", 340.0)
    project = tmp_path / "P.tether"
    extract_movie(first, project, options=ExtractOptions(window=helpers._WINDOW))
    with h5py.File(project, "r+") as f:
        f.require_group("settings/correction")
        f.require_group("idealization/vbconhmm")

    def cut(*_args, **_kwargs):
        raise _Cut

    with pytest.raises(_Cut):
        run_batch_into(_into_jobs(project, first, second), project, _correct=cut)
    assert len(_movies(project)) == 2
    assert _batch_attrs(project)[f"{STAGE_CORRECT}_movies"] == ""

    corrected: list[Path] = []
    idealized: list[dict[str, object]] = []

    def correct(path, **_kwargs):
        corrected.append(Path(path))
        return "stub"

    summary = run_batch_into(
        _into_jobs(project, first, second),
        project,
        _correct=correct,
        _idealize=_idealize_recorder(idealized),
    )

    assert [r.stages[STAGE_EXTRACT].status for r in summary.results] == [STATUS_SKIPPED] * 2
    assert corrected == [project]
    assert len(idealized) == 1
    assert idealized[0].get("overwrite") is True
    attrs = _batch_attrs(project)
    assert attrs[f"{STAGE_CORRECT}_movies"] == attrs[f"{STAGE_IDEALIZE}_movies"] != ""


def test_a_refused_append_puts_the_stage_marks_back(tmp_path: Path) -> None:
    first = _movie(tmp_path, "m0.tif", 300.0)
    second = _movie(tmp_path, "m1.tif", 340.0)
    project = tmp_path / "P.tether"
    extract_movie(first, project, options=ExtractOptions(window=helpers._WINDOW))

    def refused(*_args, **_kwargs):
        raise ExtractionError("refused")

    summary = run_batch_into(_into_jobs(project, second), project, idealize=False, _append=refused)

    assert summary.results[0].stages[STAGE_EXTRACT].status == STATUS_FAILED
    with h5py.File(project, "r") as f:
        assert "batch" not in f["settings"]

    run_batch_into(_into_jobs(project, first), project, idealize=False)
    stamped = _batch_attrs(project)[f"{STAGE_CORRECT}_movies"]
    run_batch_into(_into_jobs(project, second), project, idealize=False, _append=refused)
    assert _batch_attrs(project)[f"{STAGE_CORRECT}_movies"] == stamped


def test_a_run_where_every_movie_fails_touches_no_project(tmp_path: Path) -> None:
    bad = tmp_path / "m0.tif"
    bad.write_bytes(b"not a movie")
    project = tmp_path / "P.tether"

    summary = run_batch_into(_into_jobs(project, bad), project, idealize=False)

    assert summary.results[0].stages[STAGE_CORRECT].status == STATUS_BLOCKED
    assert not project.exists()


@pytest.mark.parametrize(
    ("idealize", "prepare", "expected"),
    [
        (False, None, "no movie to append; correct complete: will be skipped"),
        (True, None, "no movie to append; idealize will run over the whole project"),
        (
            True,
            "stale",
            "no movie to append; correct and idealize will run over the whole project",
        ),
    ],
)
def test_a_dry_run_with_nothing_to_append_reads_the_project_checkpoint(
    tmp_path: Path, idealize: bool, prepare: str | None, expected: str
) -> None:
    from tether.project.batch_inputs import describe_project_stages  # noqa: PLC0415

    movie = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    run(BatchRequest(movies=(str(movie),), into=str(project), idealize=False))
    if prepare == "stale":
        with h5py.File(project, "r+") as f:
            f["settings/batch"].attrs["correct_movies"] = "another set of movies"

    assert describe_project_stages(project, idealize=idealize, appending=False) == expected
    absent = tmp_path / "absent.tether"
    assert describe_project_stages(absent, idealize=False, appending=False).startswith("will fail:")


def test_a_project_gone_before_its_stages_is_claimed_and_not_corrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CodeRabbit (Major) on #1223: the stages claim the project whenever a movie of the
    # run went in, so a path that is absent when they start is never read without the
    # writer lock; a project that is no longer there fails the stages and runs nothing.
    movie = _movie(tmp_path, "m0.tif", 300.0)
    project = tmp_path / "P.tether"
    claimed: list[Path] = []
    real_lock = lock.publish_lock

    def recording_lock(path, **kwargs):
        claimed.append(Path(path))
        return real_lock(path, **kwargs)

    monkeypatch.setattr(lock, "publish_lock", recording_lock)
    calls: list[Path] = []

    def correct(path, **_kwargs):
        calls.append(Path(path))
        return "stub"

    def extract_elsewhere(*_args, **_kwargs):
        return SimpleNamespace(n_molecules=1, movie_id="m0")

    summary = run_batch_into(
        _into_jobs(project, movie),
        project,
        idealize=False,
        _extract=extract_elsewhere,
        _correct=correct,
    )

    assert claimed == [project]
    assert calls == []
    stages = summary.results[0].stages
    assert stages[STAGE_EXTRACT].status == STATUS_DONE
    assert stages[STAGE_CORRECT].status == STATUS_FAILED
    assert "no longer there" in str(stages[STAGE_CORRECT].error)
    assert not project.exists()


def test_a_checkpoint_no_into_run_stamped_is_not_stamped_as_covering_the_movies(
    tmp_path: Path,
) -> None:
    # CodeRabbit (Major) on #1223: a movie appended by another route after the project
    # was corrected is not in what that correction saw, so a run that skips the stage on
    # that checkpoint must not stamp it as covering the project's movies.
    first = _movie(tmp_path, "m0.tif", 300.0)
    second = _movie(tmp_path, "m1.tif", 360.0)
    project = tmp_path / "P.tether"
    options = ExtractOptions(window=helpers._WINDOW)
    extract_movie(first, project, options=options)
    with h5py.File(project, "r+") as f:
        f.require_group("settings/correction")
    with lock.publish_lock(project) as guard:
        add_movie_to_project(project, second, options=options, publish_guard=guard)

    summary = run_batch_into(
        _into_jobs(project, first, second),
        project,
        extract_options=options,
        idealize=False,
        _correct=lambda *_a, **_k: pytest.fail("the curated project was recomputed"),
    )

    assert {r.stages[STAGE_EXTRACT].status for r in summary.results} == {STATUS_SKIPPED}
    assert {r.stages[STAGE_CORRECT].status for r in summary.results} == {STATUS_SKIPPED}
    attrs = _batch_attrs(project)
    assert "correct_movies" not in attrs
    assert "idealize_movies" not in attrs
