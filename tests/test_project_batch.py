# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless batch runner — per-movie isolation, per-stage checkpoint, log/summary.

Locks :mod:`tether.project.batch` (M3 PR7-A, FR-BATCH §7.11): the extract → correct
→ idealize queue must isolate each movie (continue-on-error), checkpoint per stage
from the provenance each stage writes (a resume re-runs only the incomplete stages),
emit a structured log + an end-of-run summary that names failures, honor the
warn-vs-fail over-gate policy (§11.2), and stamp additive ``/settings/batch``
provenance (schema-guard green). Most tests drive injected stage runners that write
the real checkpoint groups; one integration test drives the real
:func:`~tether.project.batch.run_correct_stage` on a synthetic store (the
withheld-α → apparent-E path). Headless; runs in the base CI matrix.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

from tether.idealize.driver import SidecarError  # noqa: E402
from tether.idealize.supervisor import ProbeResult, SidecarSupervision  # noqa: E402
from tether.io.schema import SCHEMA_VERSION, create_project  # noqa: E402
from tether.project.batch import (  # noqa: E402
    POLICY_FAIL,
    POLICY_WARN,
    STAGE_CORRECT,
    STAGE_EXTRACT,
    STAGE_IDEALIZE,
    STATUS_BLOCKED,
    STATUS_DEFERRED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NOT_REQUESTED,
    STATUS_SKIPPED,
    STATUS_WARNING,
    BatchLog,
    MovieJob,
    run_batch,
    run_correct_stage,
)

# --- Marker-writing stub stage runners ---------------------------------------
#
# Each stub writes the *real* provenance group its real counterpart writes, so the
# batch runner's checkpoint probes (which open the .tether) are exercised for real.


def _add_group(path: Path, group_path: str) -> None:
    with h5py.File(path, "r+") as f:
        f.require_group(group_path)


def _extract_stub(*, fail: frozenset[str] = frozenset(), low_conf: frozenset[str] = frozenset()):
    def run(movie_path, output_path, *, options=None, tmap=None, tdat=None, overwrite=False):
        stem = Path(output_path).stem
        if stem in fail:
            raise RuntimeError(f"extract boom: {stem}")
        create_project(output_path, overwrite=True)
        _add_group(Path(output_path), "settings/extraction")
        return SimpleNamespace(n_molecules=3, low_confidence_registration=stem in low_conf)

    return run


def _raising_extract(movie_path, output_path, **kwargs):  # must never be called
    raise AssertionError(f"extract runner was invoked for {output_path!r} (expected skip)")


def _correct_stub(*, fail: frozenset[str] = frozenset()):
    def run(output_path):
        stem = Path(output_path).stem
        if stem in fail:
            raise RuntimeError(f"correct boom: {stem}")
        _add_group(Path(output_path), "settings/correction")
        return "α withheld; apparent-E fallback (3 mol)"

    return run


def _raising_correct(output_path):  # must never be called
    raise AssertionError(f"correct runner was invoked for {output_path!r} (expected skip)")


def _idealize_stub(*, fail: frozenset[str] = frozenset()):
    def run(output_path, **kwargs):
        stem = Path(output_path).stem
        if stem in fail:
            raise RuntimeError(f"idealize boom: {stem}")
        _add_group(Path(output_path), "idealization/vbconhmm")
        return SimpleNamespace(model_name="vbconhmm", nstates=2, molecule_keys=["m1", "m2"])

    return run


def _raising_idealize(output_path, **kwargs):  # must never be called
    raise AssertionError(f"idealize runner was invoked for {output_path!r} (expected skip)")


def _jobs(tmp_path: Path, *stems: str) -> list[MovieJob]:
    return [
        MovieJob(movie_path=tmp_path / f"{s}.tif", output_path=tmp_path / f"{s}.tether")
        for s in stems
    ]


def _run(jobs, **kw):
    kw.setdefault("_extract", _extract_stub())
    kw.setdefault("_correct", _correct_stub())
    kw.setdefault("_idealize", _idealize_stub())
    return run_batch(jobs, **kw)


# --- Policy validation -------------------------------------------------------


def test_invalid_policy_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="policy must be one of"):
        run_batch(_jobs(tmp_path, "a"), policy="bogus", _extract=_extract_stub())


# --- Isolation (continue-on-error) -------------------------------------------


def test_extract_failure_is_isolated(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "a", "b", "c")
    summary = _run(jobs, _extract=_extract_stub(fail=frozenset({"b"})))

    by_stem = {r.job.output_path.stem: r for r in summary.results}
    for good in ("a", "c"):
        assert by_stem[good].stages[STAGE_EXTRACT].status == STATUS_DONE
        assert by_stem[good].stages[STAGE_CORRECT].status == STATUS_DONE
        assert by_stem[good].stages[STAGE_IDEALIZE].status == STATUS_DONE
        assert by_stem[good].ok

    bad = by_stem["b"]
    assert bad.stages[STAGE_EXTRACT].status == STATUS_FAILED
    assert "extract boom" in bad.stages[STAGE_EXTRACT].error
    assert bad.stages[STAGE_CORRECT].status == STATUS_BLOCKED
    assert bad.stages[STAGE_IDEALIZE].status == STATUS_BLOCKED
    assert not bad.ok

    assert summary.n_movies == 3
    assert summary.n_ok == 2
    assert summary.n_failed == 1


def test_idealize_failure_isolated_then_resumable(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    first = _run(jobs, _idealize=_idealize_stub(fail=frozenset({"x"})))
    r = first.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.stages[STAGE_CORRECT].status == STATUS_DONE
    assert r.stages[STAGE_IDEALIZE].status == STATUS_FAILED
    assert not r.ok

    # Resume: extract + correct are checkpointed (must not re-run); idealize retries.
    second = run_batch(
        jobs,
        _extract=_raising_extract,
        _correct=_raising_correct,
        _idealize=_idealize_stub(),
    )
    r2 = second.results[0]
    assert r2.stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert r2.stages[STAGE_CORRECT].status == STATUS_SKIPPED
    assert r2.stages[STAGE_IDEALIZE].status == STATUS_DONE
    assert r2.ok


# --- Per-stage checkpoint / resume -------------------------------------------


def test_resume_reruns_only_the_failed_stage(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    first = _run(jobs, _correct=_correct_stub(fail=frozenset({"x"})))
    r = first.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.stages[STAGE_CORRECT].status == STATUS_FAILED
    assert r.stages[STAGE_IDEALIZE].status == STATUS_BLOCKED

    # Resume: extract already done → must be skipped (raising stub proves it); the
    # failed correct stage re-runs and idealize then proceeds.
    second = run_batch(
        jobs,
        _extract=_raising_extract,
        _correct=_correct_stub(),
        _idealize=_idealize_stub(),
    )
    r2 = second.results[0]
    assert r2.stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert r2.stages[STAGE_CORRECT].status == STATUS_DONE
    assert r2.stages[STAGE_IDEALIZE].status == STATUS_DONE


def test_full_rerun_skips_every_stage(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    _run(jobs)  # everything completes
    # Second pass: every runner would raise if invoked — all must be skipped.
    summary = run_batch(
        jobs,
        _extract=_raising_extract,
        _correct=_raising_correct,
        _idealize=_raising_idealize,
    )
    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert r.stages[STAGE_CORRECT].status == STATUS_SKIPPED
    assert r.stages[STAGE_IDEALIZE].status == STATUS_SKIPPED
    assert r.ok


# --- Forward-compatibility guard on resume (PRD §5.4) ------------------------


def _stamp_future_schema(path: Path) -> None:
    """Make an existing project look like it came from a newer Tether."""
    with h5py.File(path, "r+") as f:
        f.attrs["schema_version"] = SCHEMA_VERSION + 1


def test_resume_refuses_a_future_schema_output_and_isolates_it(tmp_path: Path) -> None:
    """A colleague's newer ``.tether`` fails ITS movie on resume; the queue continues.

    The regression this locks (#203) only appears when the extract stage is *skipped*:
    the guard inside ``write_extraction`` never runs, so before the fix the correct
    stage happily opened the newer file ``r+`` and wrote to it.
    """
    jobs = _jobs(tmp_path, "future", "fine")
    first = _run(jobs)
    assert all(r.ok for r in first.results)
    _stamp_future_schema(tmp_path / "future.tether")

    # Every runner raises if invoked: "fine" must still skip all three stages, and
    # "future" must be refused before any runner is reached.
    summary = run_batch(
        jobs,
        _extract=_raising_extract,
        _correct=_raising_correct,
        _idealize=_raising_idealize,
    )
    future, fine = summary.results

    assert future.stages[STAGE_EXTRACT].status == STATUS_FAILED
    assert "newer than this app's" in (future.stages[STAGE_EXTRACT].error or "")
    assert "refusing to open" in (future.stages[STAGE_EXTRACT].error or "")
    # Downstream stages are blocked, not attempted.
    assert future.stages[STAGE_CORRECT].status == STATUS_BLOCKED
    assert future.stages[STAGE_IDEALIZE].status == STATUS_BLOCKED
    assert not future.ok

    # Isolation: the healthy movie is untouched by its neighbour's refusal.
    assert fine.ok
    assert fine.stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert summary.n_failed == 1 and summary.n_ok == 1


def test_future_schema_output_is_never_written_to(tmp_path: Path) -> None:
    """The refusal must PREVENT the write, not merely report it.

    ``/settings/batch`` provenance is stamped at the end of every job — including a
    failed one — so it is the last thing that would still open the newer file ``r+``.
    """
    jobs = _jobs(tmp_path, "future")
    _run(jobs)
    path = tmp_path / "future.tether"
    # Plant a sentinel INSIDE the group the stamp deletes and recreates. Comparing
    # `settings` key sets would not catch this: the stamp replaces `/settings/batch`,
    # leaving the names identical.
    with h5py.File(path, "r+") as f:
        f["settings/batch"].attrs["sentinel"] = "untouched"
    _stamp_future_schema(path)

    run_batch(jobs, _extract=_raising_extract, _correct=_raising_correct)

    with h5py.File(path, "r") as f:
        assert str(f["settings/batch"].attrs["sentinel"]) == "untouched"
        assert int(f.attrs["schema_version"]) == SCHEMA_VERSION + 1


def test_resume_still_reattempts_an_unreadable_or_incomplete_output(tmp_path: Path) -> None:
    """The guard must not turn a crashed run into a permanent failure.

    It refuses only a file that *declares* a newer schema. A half-written or corrupt
    store keeps the checkpoint probes' "(re-)attempt rather than falsely skip"
    behaviour, so a crashed run is still resumable.
    """
    jobs = _jobs(tmp_path, "corrupt")
    (tmp_path / "corrupt.tether").write_bytes(b"not an HDF5 file at all")

    summary = _run(jobs)  # the real extract stub re-creates the project

    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.ok


def test_foreign_hdf5_with_a_version_attr_is_not_treated_as_a_future_project(
    tmp_path: Path,
) -> None:
    """A stranger's HDF5 is not a future Tether project, whatever attrs it carries.

    Without the ``format``-marker check the guard would refuse any readable HDF5 whose
    ``schema_version`` happened to be large, breaking the documented ``--overwrite``
    re-extract path and blaming the wrong thing.
    """
    jobs = _jobs(tmp_path, "foreign")
    with h5py.File(tmp_path / "foreign.tether", "w") as f:
        f.attrs["schema_version"] = SCHEMA_VERSION + 50
        f.attrs["format"] = "somebody-elses-format"
        f.create_group("unrelated")

    summary = _run(jobs, overwrite=True)  # the real extract stub re-creates the project

    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.ok


def test_foreign_hdf5_with_array_valued_attrs_falls_through(tmp_path: Path) -> None:
    """An array-valued ``format`` attribute must not blow up the guard.

    h5py hands back a numpy array for an array-valued attribute, and ``array != str``
    is an elementwise array whose truthiness raises. That ``ValueError`` would escape
    the guard, be caught by ``_do_extract`` as an extract failure, and block the very
    re-attempt/overwrite path the format check exists to preserve.
    """
    jobs = _jobs(tmp_path, "arrayattrs")
    with h5py.File(tmp_path / "arrayattrs.tether", "w") as f:
        f.attrs["format"] = ["tether-project", "tether-project"]
        f.attrs["schema_version"] = np.array([SCHEMA_VERSION + 7, SCHEMA_VERSION + 8])
        f.create_group("unrelated")

    summary = _run(jobs, overwrite=True)

    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.ok


def test_correct_stage_refuses_a_future_schema_project(tmp_path: Path) -> None:
    """``run_correct_stage`` guards itself — each correction opens the store ``r+``."""
    path = tmp_path / "x.tether"
    create_project(path)
    _stamp_future_schema(path)
    with pytest.raises(ValueError, match="newer than this app's"):
        run_correct_stage(path)


# --- Over-gate policy --------------------------------------------------------


def test_policy_warn_keeps_low_confidence_movie(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    summary = _run(jobs, policy=POLICY_WARN, _extract=_extract_stub(low_conf=frozenset({"x"})))
    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert "low-confidence" in r.stages[STAGE_EXTRACT].detail
    assert r.stages[STAGE_CORRECT].status == STATUS_DONE
    assert r.ok


def test_policy_fail_fails_low_confidence_movie(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    summary = _run(jobs, policy=POLICY_FAIL, _extract=_extract_stub(low_conf=frozenset({"x"})))
    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_FAILED
    assert r.stages[STAGE_CORRECT].status == STATUS_BLOCKED
    assert r.stages[STAGE_IDEALIZE].status == STATUS_BLOCKED
    assert not r.ok


# --- idealize=False ----------------------------------------------------------


def test_idealize_not_requested(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    summary = _run(jobs, idealize=False, _idealize=_raising_idealize)
    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.stages[STAGE_CORRECT].status == STATUS_DONE
    assert r.stages[STAGE_IDEALIZE].status == STATUS_NOT_REQUESTED
    assert r.ok  # not-requested is not a failure


# --- Structured log ----------------------------------------------------------


def test_structured_log_records_and_jsonl(tmp_path: Path) -> None:
    import json

    log_path = tmp_path / "batch-log.jsonl"
    jobs = _jobs(tmp_path, "x")
    with BatchLog(path=log_path) as log:
        _run(jobs, log=log)
        records = list(log.records)

    triples = [(r["movie"], r["stage"], r["status"]) for r in records]
    assert ("x", STAGE_EXTRACT, STATUS_DONE) in triples  # label = output .tether stem
    assert ("x", STAGE_CORRECT, STATUS_DONE) in triples
    assert ("x", STAGE_IDEALIZE, STATUS_DONE) in triples

    on_disk = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert on_disk == records


def test_log_appends_across_runs_preserving_prior_records(tmp_path: Path) -> None:
    # A resumed batch reuses the same log path; prior records must survive (append,
    # not truncate) — the append-only audit trail (§7.11).
    import json

    log_path = tmp_path / "batch-log.jsonl"
    jobs = _jobs(tmp_path, "x")
    with BatchLog(path=log_path) as log:
        _run(jobs, log=log)
    n_first = len(log_path.read_text(encoding="utf-8").splitlines())
    # Resume: every stage is now checkpointed, so this pass records skips — but the
    # earlier records must remain in the file.
    with BatchLog(path=log_path) as log:
        run_batch(
            jobs,
            _extract=_raising_extract,
            _correct=_raising_correct,
            _idealize=_raising_idealize,
            log=log,
        )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > n_first  # prior records preserved + new ones appended
    assert all(json.loads(line) for line in lines)  # every line is valid JSON


def test_queue_survives_a_failing_log_sink(tmp_path: Path) -> None:
    # If the log sink itself raises (disk full, permission change) the queue must not
    # abort — every movie still gets a result (§7.11 "isolate each movie").
    class _ExplodingLog(BatchLog):
        def event(self, **kwargs):  # type: ignore[override]
            raise OSError("log sink dead")

    jobs = _jobs(tmp_path, "a", "b", "c")
    summary = _run(jobs, log=_ExplodingLog())
    assert summary.n_movies == 3  # loop completed despite every log write raising
    for r in summary.results:
        assert STAGE_EXTRACT in r.stages  # the stage result was still recorded


def test_log_captures_failure_error(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    with BatchLog() as log:
        _run(jobs, log=log, _correct=_correct_stub(fail=frozenset({"x"})))
        failed = [r for r in log.records if r["status"] == STATUS_FAILED]
    assert len(failed) == 1
    assert failed[0]["stage"] == STAGE_CORRECT
    assert "correct boom" in failed[0]["error"]


# --- End-of-run summary ------------------------------------------------------


def test_report_enumerates_movies_and_names_failures(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "movie_a", "movie_b", "movie_c")
    summary = _run(jobs, _correct=_correct_stub(fail=frozenset({"movie_b"})))
    report = summary.format_report()

    for stem in ("movie_a", "movie_b", "movie_c"):
        assert stem in report  # every movie enumerated (label = output stem)
    assert "3 movie(s), 2 ok, 1 failed" in report
    assert "correct boom: movie_b" in report  # the failure is named
    assert "policy=warn" in report


# --- /settings/batch provenance (additive; NFR-REPRO) ------------------------


def test_settings_batch_provenance_stamped(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    _run(jobs, policy=POLICY_WARN)
    with h5py.File(jobs[0].output_path, "r") as f:
        assert "/settings/batch" in f
        grp = f["/settings/batch"]
        assert grp.attrs["source"] == "batch-runner"
        assert grp.attrs["policy"] == "warn"
        assert bool(grp.attrs["idealize_requested"]) is True
        assert grp.attrs["extract_status"] == STATUS_DONE
        assert grp.attrs["correct_status"] == STATUS_DONE
        assert grp.attrs["idealize_status"] == STATUS_DONE
        assert "created_utc" in grp.attrs
        assert "app_version" in grp.attrs
        # Additive only: the frozen §5 skeleton groups are untouched.
        for frozen in ("molecules", "movies", "traces", "idealization", "settings"):
            assert frozen in f


def test_provenance_can_be_disabled(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    _run(jobs, stamp_provenance=False)
    with h5py.File(jobs[0].output_path, "r") as f:
        assert "/settings/batch" not in f


def test_settings_batch_records_idealize_false(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    _run(jobs, idealize=False, _idealize=_raising_idealize)
    with h5py.File(jobs[0].output_path, "r") as f:
        grp = f["/settings/batch"]
        assert bool(grp.attrs["idealize_requested"]) is False
        assert grp.attrs["idealize_status"] == STATUS_NOT_REQUESTED


def test_idealize_checkpoint_ignores_crashed_staging_group(tmp_path: Path) -> None:
    # idealize_molecules stages a model under `{model}.__writing__` before its atomic
    # swap; a crash mid-swap can leave only that staging group. It must NOT read as
    # "already idealized" — the stage must re-run on resume, not be falsely skipped.
    jobs = _jobs(tmp_path, "x")

    def staging_only(output_path, **kwargs):
        _add_group(Path(output_path), "idealization/vbconhmm.__writing__")
        raise RuntimeError("sidecar crashed before the atomic swap")

    first = _run(jobs, _idealize=staging_only)
    assert first.results[0].stages[STAGE_IDEALIZE].status == STATUS_FAILED

    # Resume: extract + correct are checkpointed; idealize must RE-RUN (a staging-only
    # /idealization is not a completed model), not skip.
    second = run_batch(
        jobs,
        _extract=_raising_extract,
        _correct=_raising_correct,
        _idealize=_idealize_stub(),
    )
    assert second.results[0].stages[STAGE_IDEALIZE].status == STATUS_DONE
    with h5py.File(jobs[0].output_path, "r") as f:
        keys = set(f["/idealization"].keys())
    assert "vbconhmm" in keys  # the real model now exists


# --- MovieJob path coercion --------------------------------------------------


def test_moviejob_coerces_paths_and_label() -> None:
    job = MovieJob(movie_path="a/b/movie_010.tif", output_path="out/movie_010.tether")
    assert isinstance(job.movie_path, Path)
    assert isinstance(job.output_path, Path)
    assert job.tmap is None
    assert job.label == "movie_010"  # per-job-unique: the output .tether stem


def test_label_disambiguates_same_named_movies_in_different_folders() -> None:
    a = MovieJob(movie_path="condA/movie.tif", output_path="out/condA_movie.tether")
    b = MovieJob(movie_path="condB/movie.tif", output_path="out/condB_movie.tether")
    assert a.label != b.label  # same movie basename, distinct output → distinct labels


# --- Integration: the REAL correct stage on a synthetic store -----------------
#
# Drives the default run_correct_stage (photobleach → leakage → corrected-E) against
# a real .tether whose few molecules make leakage withhold the dataset α, so γ is
# skipped and corrected-FRET falls to apparent E — the whole withheld-α path, no
# movie extraction and no sidecar needed.

from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import MoleculeTraces, MovieMetadata, write_extraction  # noqa: E402
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402

_WINDOW = 21


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _build_real_store(path: Path, *, n_mol: int = 3, n_frames: int = 200) -> None:
    """A real extracted .tether with a clear per-channel bleach in every molecule."""
    rng = np.random.default_rng(0)
    donor = np.empty((n_mol, n_frames), dtype="float64")
    acceptor = np.empty((n_mol, n_frames), dtype="float64")
    acceptor_pb, donor_pb = 100, 150
    for i in range(n_mol):
        d = rng.normal(1000.0, 4.0, n_frames)
        d[donor_pb:] = rng.normal(0.0, 4.0, n_frames - donor_pb)
        a = np.empty(n_frames, dtype="float64")
        a[:acceptor_pb] = rng.normal(600.0, 4.0, acceptor_pb)
        a[acceptor_pb:donor_pb] = 0.09 * d[acceptor_pb:donor_pb] + rng.normal(
            0.0, 4.0, donor_pb - acceptor_pb
        )
        a[donor_pb:] = rng.normal(0.0, 4.0, n_frames - donor_pb)
        donor[i], acceptor[i] = d, a

    coords = np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * i] for i in range(n_mol)], dtype="float64")
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n_mol, dtype=bool),
        donor_index=np.arange(n_mol, dtype=np.intp),
        acceptor_index=np.full(n_mol, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.zeros((n_mol, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n_mol, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=n_frames,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"),
        registration_map=_reg_map(),
    )


def test_integration_real_correct_stage_withheld_alpha(tmp_path: Path) -> None:
    out = tmp_path / "video_010.tether"

    def build_store_extract(movie_path, output_path, **kwargs):
        _build_real_store(Path(output_path))
        return SimpleNamespace(n_molecules=3, low_confidence_registration=False)

    jobs = [MovieJob(movie_path=tmp_path / "video_010.tif", output_path=out)]
    # _correct is left as the default (run_correct_stage — the real ordered pass).
    summary = run_batch(
        jobs,
        idealize=False,
        _extract=build_store_extract,
        _idealize=_raising_idealize,
    )

    r = summary.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
    assert r.stages[STAGE_CORRECT].status == STATUS_DONE
    # Fewer than min_qualifying_traces molecules → α withheld → apparent-E fallback.
    detail = r.stages[STAGE_CORRECT].detail
    assert "withheld" in detail
    assert "apparent-E" in detail

    # The real corrected-FRET pass wrote its provenance, and no NaN factor was stamped.
    with h5py.File(out, "r") as f:
        assert "/settings/correction" in f
        assert "/settings/batch" in f
        table = f["molecules/table"][:]
        assert np.all(np.isnan(table["alpha"]))  # withheld → NaN sentinel, never fabricated


# --- Sidecar supervision (PR7-B, ADR-0031) -----------------------------------
#
# All supervision is driven through injected seams (a fake startup `_probe` and idealize
# runners that raise SidecarError with a chosen `transient` flag) — no real sidecar env.


def _probe_available(supervision):
    return ProbeResult(available=True, detail="ready")


def _probe_unavailable(supervision):
    return ProbeResult(available=False, detail="no sidecar interpreter")


def _idealize_flaky(*, transient_fails: int = 0, deterministic: bool = False):
    """Idealize stub that raises before writing, then succeeds; counts calls per movie."""
    calls: dict[str, int] = {}

    def run(output_path, **kwargs):
        stem = Path(output_path).stem
        calls[stem] = calls.get(stem, 0) + 1
        if deterministic:
            raise SidecarError(f"bad fit: {stem}", transient=False)
        if calls[stem] <= transient_fails:
            raise SidecarError(f"sidecar crash {calls[stem]}: {stem}", transient=True)
        _add_group(Path(output_path), "idealization/vbconhmm")
        return SimpleNamespace(model_name="vbconhmm", nstates=2, molecule_keys=["m1"])

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_sidecar_unavailable_defers_idealization(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "a", "b")
    # A failed startup probe defers idealization: the idealize runner is never invoked
    # (the raising stub proves it), yet extract + correct complete and the run is clean.
    summary = _run(
        jobs,
        supervision=SidecarSupervision(),
        _probe=_probe_unavailable,
        _idealize=_raising_idealize,
    )
    for r in summary.results:
        assert r.stages[STAGE_EXTRACT].status == STATUS_DONE
        assert r.stages[STAGE_CORRECT].status == STATUS_DONE
        assert r.stages[STAGE_IDEALIZE].status == STATUS_DEFERRED
        assert r.ok  # deferred is not a movie failure
    assert summary.n_failed == 0
    report = summary.format_report()
    assert "idealize=deferred" in report
    assert "FAIL" not in report


def test_deferred_idealization_resumes_when_sidecar_returns(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    first = _run(
        jobs,
        supervision=SidecarSupervision(),
        _probe=_probe_unavailable,
        _idealize=_raising_idealize,
    )
    assert first.results[0].stages[STAGE_IDEALIZE].status == STATUS_DEFERRED

    # Resume with a live sidecar: extract + correct are skipped via checkpoint (the
    # raising stubs prove it), and only the deferred idealize stage runs.
    second = _run(
        jobs,
        supervision=SidecarSupervision(),
        _probe=_probe_available,
        _extract=_raising_extract,
        _correct=_raising_correct,
        _idealize=_idealize_stub(),
    )
    r = second.results[0]
    assert r.stages[STAGE_EXTRACT].status == STATUS_SKIPPED
    assert r.stages[STAGE_CORRECT].status == STATUS_SKIPPED
    assert r.stages[STAGE_IDEALIZE].status == STATUS_DONE


def test_transient_sidecar_failure_is_restarted(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    stub = _idealize_flaky(transient_fails=2)
    log = BatchLog()
    summary = _run(
        jobs, supervision=SidecarSupervision(), _probe=_probe_available, _idealize=stub, log=log
    )
    r = summary.results[0]
    assert r.stages[STAGE_IDEALIZE].status == STATUS_DONE
    assert stub.calls["x"] == 3  # 1 initial attempt + 2 restarts, then success
    warns = [
        rec
        for rec in log.records
        if rec["stage"] == STAGE_IDEALIZE and rec["status"] == STATUS_WARNING
    ]
    assert len(warns) == 2
    assert "restart 1/3" in warns[0]["detail"]
    assert "restart 2/3" in warns[1]["detail"]


def test_restart_exhaustion_fails_only_that_movie(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "bad", "good")

    def _idealize(output_path, **kwargs):
        stem = Path(output_path).stem
        if stem == "bad":
            raise SidecarError(f"sidecar crash: {stem}", transient=True)
        _add_group(Path(output_path), "idealization/vbconhmm")
        return SimpleNamespace(model_name="vbconhmm", nstates=2, molecule_keys=["m1"])

    summary = _run(
        jobs,
        supervision=SidecarSupervision(max_restarts=2),
        _probe=_probe_available,
        _idealize=_idealize,
    )
    by = {r.job.label: r for r in summary.results}
    assert by["bad"].stages[STAGE_IDEALIZE].status == STATUS_FAILED
    assert "after 2 restart(s)" in by["bad"].stages[STAGE_IDEALIZE].error
    # The failing movie is isolated: the other movie idealizes fine and the queue survives.
    assert by["good"].stages[STAGE_IDEALIZE].status == STATUS_DONE
    assert by["good"].ok
    assert summary.n_failed == 1


def test_deterministic_sidecar_error_not_restarted(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, "x")
    stub = _idealize_flaky(deterministic=True)
    summary = _run(
        jobs,
        supervision=SidecarSupervision(max_restarts=3),
        _probe=_probe_available,
        _idealize=stub,
    )
    r = summary.results[0]
    assert r.stages[STAGE_IDEALIZE].status == STATUS_FAILED
    assert stub.calls["x"] == 1  # a sidecar-reported fit error is not retried
    assert "bad fit" in r.stages[STAGE_IDEALIZE].error


def test_available_sidecar_runs_idealize_normally(tmp_path: Path) -> None:
    summary = _run(_jobs(tmp_path, "x"), supervision=SidecarSupervision(), _probe=_probe_available)
    assert summary.results[0].stages[STAGE_IDEALIZE].status == STATUS_DONE


def test_supervision_owns_timeout_and_sidecar_python(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def _idealize(output_path, **kwargs):
        seen.update(kwargs)
        _add_group(Path(output_path), "idealization/vbconhmm")
        return SimpleNamespace(model_name="vbconhmm", nstates=2, molecule_keys=["m1"])

    _run(
        _jobs(tmp_path, "x"),
        supervision=SidecarSupervision(timeout=42.0, sidecar_python="/x/py"),
        _probe=_probe_available,
        _idealize=_idealize,
        idealize_kwargs={"timeout": 999, "sidecar_python": "/other", "model_type": "vbconhmm"},
    )
    # Supervision wins for the two keys it owns; unrelated kwargs pass through untouched.
    assert seen["timeout"] == 42.0
    assert seen["sidecar_python"] == "/x/py"
    assert seen["model_type"] == "vbconhmm"


def test_no_defer_skips_probe_and_runs_idealize(tmp_path: Path) -> None:
    probed = {"called": False}

    def _probe(supervision):
        probed["called"] = True
        return ProbeResult(available=False, detail="unused")

    summary = _run(
        _jobs(tmp_path, "x"),
        supervision=SidecarSupervision(defer_if_unavailable=False),
        _probe=_probe,
        _idealize=_idealize_stub(),
    )
    assert probed["called"] is False  # no probe when deferral is disabled
    assert summary.results[0].stages[STAGE_IDEALIZE].status == STATUS_DONE
