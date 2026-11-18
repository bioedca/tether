# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Progress lines while ``tether batch`` runs (#648, FR-BATCH §7.11).

The runner tells a :class:`~tether.project.batch.BatchProgress` when each movie starts
and ends, and the :class:`~tether.project.batch.BatchLog` hands each record to an
``on_event`` callback, so the progress text comes from the same events as the structured
log. These tests pin the three levels (``--quiet``, the default, ``--verbose``), that
stdout stays the summary alone, that the setup line comes before any movie is read, and
that neither a failing display nor a failing callback can cost a movie its stage.

The library tests drive the injected stage runners of ``test_project_batch``; the CLI
test runs the real extract and correct stages on one synthetic movie and one unreadable
file. Headless; runs in the base CI matrix.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

from test_project_batch import (  # noqa: E402
    _correct_stub,
    _extract_stub,
    _idealize_stub,
    _jobs,
)
from tether.cli import main  # noqa: E402
from tether.idealize.supervisor import ProbeResult, SidecarSupervision  # noqa: E402
from tether.project.batch import (  # noqa: E402
    BatchLog,
    BatchProgress,
    TextProgress,
    _format_elapsed,
    run_batch,
)


def _clock(*ticks: float):
    """A fake monotonic clock that returns ``ticks`` in order."""
    values = iter(ticks)
    return lambda: next(values)


def _probe_unavailable(supervision):
    return ProbeResult(available=False, detail="no sidecar interpreter")


def _run_with_progress(tmp_path: Path, *, verbose: bool, **kw):
    stream = io.StringIO()
    progress = TextProgress(stream, verbose=verbose, clock=_clock(0.0, 3.25, 10.0, 75.0))
    jobs = _jobs(tmp_path, "a", "b")
    progress.run_started(n_movies=len(jobs), out_dir="out", policy="warn", idealize=True)
    with BatchLog(on_event=progress.event) as log:
        summary = run_batch(
            jobs,
            log=log,
            progress=progress,
            _extract=kw.pop("_extract", _extract_stub()),
            _correct=_correct_stub(),
            _idealize=_idealize_stub(),
            **kw,
        )
    return stream.getvalue().splitlines(), summary, log


def test_default_level_prints_setup_then_a_start_and_an_end_line_per_movie(tmp_path) -> None:
    lines, _, _ = _run_with_progress(
        tmp_path,
        verbose=False,
        _extract=_extract_stub(low_conf=frozenset({"a"}), fail=frozenset({"b"})),
        supervision=SidecarSupervision(),
        _probe=_probe_unavailable,
    )

    assert lines == [
        "tether batch: 2 movie(s) into out (policy=warn, idealize=on)",
        "tether batch: idealize deferred - sidecar unavailable — idealization deferred: "
        "no sidecar interpreter",
        "[1/2] a: started",
        "[1/2] a: ok in 3.2s (extract=done correct=done idealize=deferred; flagged: extract)",
        "[2/2] b: started",
        "[2/2] b: FAIL in 1m05s (extract=failed(extract boom: b) correct=blocked idealize=blocked)",
    ]


def test_verbose_level_adds_every_stage_event_with_its_position(tmp_path) -> None:
    lines, _, log = _run_with_progress(tmp_path, verbose=True)

    assert lines == [
        "tether batch: 2 movie(s) into out (policy=warn, idealize=on)",
        "[1/2] a: started",
        "[1/2] a extract: done - 3 molecule(s)",
        "[1/2] a correct: done - α withheld; apparent-E fallback (3 mol)",
        "[1/2] a idealize: done - vbconhmm n=2 2 mol",
        "[1/2] a: ok in 3.2s (extract=done correct=done idealize=done)",
        "[2/2] b: started",
        "[2/2] b extract: done - 3 molecule(s)",
        "[2/2] b correct: done - α withheld; apparent-E fallback (3 mol)",
        "[2/2] b idealize: done - vbconhmm n=2 2 mol",
        "[2/2] b: ok in 1m05s (extract=done correct=done idealize=done)",
    ]
    # One verbose line per recorded stage event: no second source of truth.
    assert len([line for line in lines if " a " in line or " b " in line]) == len(log.records)


def test_a_warning_event_is_named_on_the_end_line(tmp_path) -> None:
    progress = TextProgress(io.StringIO(), clock=_clock(0.0, 1.0))
    (job,) = _jobs(tmp_path, "a")
    progress.movie_started(1, 1, job)
    progress.event({"movie": "a", "stage": "donor-side-check", "status": "warning"})
    progress.event({"movie": "a", "stage": "provenance", "status": "warning", "error": "x"})
    progress.event({"movie": "a", "stage": "provenance", "status": "warning", "error": "y"})
    summary = run_batch([job], idealize=False, _extract=_extract_stub(), _correct=_correct_stub())
    progress.movie_finished(1, 1, summary.results[0])

    assert progress._stream.getvalue().splitlines()[-1] == (
        "[1/1] a: ok in 1.0s (extract=done correct=done idealize=not-requested; "
        "flagged: donor-side-check, provenance)"
    )


def test_progress_does_not_change_the_records_or_the_summary(tmp_path) -> None:
    jobs_plain = _jobs(tmp_path / "plain", "a", "b")
    (tmp_path / "plain").mkdir()
    with BatchLog(path=tmp_path / "plain.jsonl") as log:
        plain = run_batch(
            jobs_plain,
            log=log,
            _extract=_extract_stub(fail=frozenset({"b"})),
            _correct=_correct_stub(),
            _idealize=_idealize_stub(),
        )
    lines, watched, _ = _run_with_progress(
        tmp_path, verbose=True, _extract=_extract_stub(fail=frozenset({"b"}))
    )

    assert lines  # the display did run
    assert watched.format_report() == plain.format_report()
    plain_records = [json.loads(x) for x in (tmp_path / "plain.jsonl").read_text().splitlines()]
    assert [(r["movie"], r["stage"], r["status"]) for r in plain_records] == [
        ("a", "extract", "done"),
        ("a", "correct", "done"),
        ("a", "idealize", "done"),
        ("b", "extract", "failed"),
        ("b", "correct", "blocked"),
        ("b", "idealize", "blocked"),
    ]


def test_a_failing_display_never_costs_a_movie_its_stage(tmp_path) -> None:
    class Broken(BatchProgress):
        def movie_started(self, index, total, job):
            raise OSError("stderr closed")

        def movie_finished(self, index, total, result):
            raise OSError("stderr closed")

    def broken_callback(record):
        raise OSError("stderr closed")

    with BatchLog(on_event=broken_callback) as log:
        summary = run_batch(
            _jobs(tmp_path, "a", "b"),
            log=log,
            progress=Broken(),
            _extract=_extract_stub(),
            _correct=_correct_stub(),
            _idealize=_idealize_stub(),
        )

    assert summary.n_ok == 2
    assert len(log.records) == 6


def test_an_observer_that_edits_its_result_cannot_change_the_summary(tmp_path) -> None:
    class Meddler(BatchProgress):
        def movie_finished(self, index, total, result):
            result.stages.clear()
            raise RuntimeError("display bug")

    summary = run_batch(
        _jobs(tmp_path, "a", "b"),
        progress=Meddler(),
        _extract=_extract_stub(fail=frozenset({"b"})),
        _correct=_correct_stub(),
        _idealize=_idealize_stub(),
    )

    assert summary.n_failed == 1
    assert [len(r.stages) for r in summary.results] == [3, 3]


def test_an_observer_that_edits_the_donor_side_record_cannot_flag_a_movie(
    tmp_path, monkeypatch
) -> None:
    import tether.project.batch as batch_mod  # noqa: PLC0415

    monkeypatch.setattr(
        batch_mod, "_stored_donor_side_check", lambda _path: {"verdict": "consistent"}
    )

    class Meddler(BatchProgress):
        def movie_finished(self, index, total, result):
            result.donor_side["verdict"] = "swapped"

    summary = run_batch(
        _jobs(tmp_path, "a"),
        progress=Meddler(),
        _extract=_extract_stub(),
        _correct=_correct_stub(),
        _idealize=_idealize_stub(),
    )

    assert summary.results[0].donor_side == {"verdict": "consistent"}
    assert summary.donor_side_flagged == []


def test_a_broken_stream_never_stops_the_run(tmp_path) -> None:
    class Closed(io.StringIO):
        def write(self, text):
            raise BrokenPipeError("progress reader went away")

    progress = TextProgress(Closed(), verbose=True)
    # Called by the CLI before run_batch, so it must isolate its own failure (#648).
    progress.run_started(n_movies=2, out_dir="out", policy="warn", idealize=True)
    with BatchLog(on_event=progress.event) as log:
        summary = run_batch(
            _jobs(tmp_path, "a", "b"),
            log=log,
            progress=progress,
            _extract=_extract_stub(),
            _correct=_correct_stub(),
            _idealize=_idealize_stub(),
        )

    assert summary.n_ok == 2
    assert len(log.records) == 6


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(-1.0, "0.0s"), (3.25, "3.2s"), (59.9, "59.9s"), (65.0, "1m05s"), (7380.0, "2h03m")],
)
def test_elapsed_time_stays_short(seconds, text) -> None:
    assert _format_elapsed(seconds) == text


# --- The CLI: three levels, stdout unchanged --------------------------------


def _cli_batch(tmp_path: Path, capsys, level: list[str], movies: list[Path], name: str):
    out = tmp_path / name
    rc = main(["batch", *map(str, movies), "--out-dir", str(out), "--no-idealize", *level])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err.splitlines()


def test_cli_two_movie_batch_at_each_level(tmp_path, capsys) -> None:
    for module in ("scipy", "skimage", "tifffile"):
        pytest.importorskip(module)
    import test_extract_cli as helpers  # noqa: PLC0415

    good = helpers._make_movie(tmp_path, name="good_001.tif")
    bad = tmp_path / "bad_002.tif"
    bad.write_bytes(b"not a tiff at all")
    movies = [good, bad]

    rc_q, out_q, err_q = _cli_batch(tmp_path, capsys, ["--quiet"], movies, "quiet")
    rc_d, out_d, err_d = _cli_batch(tmp_path, capsys, [], movies, "default")
    rc_v, out_v, err_v = _cli_batch(tmp_path, capsys, ["--verbose"], movies, "verbose")

    assert rc_q == rc_d == rc_v == 1
    # stdout is the end-of-run summary alone, byte for byte, whatever the level.
    assert out_q == out_d == out_v
    assert out_q.startswith("Batch run: 2 movie(s), 1 ok, 1 failed")

    def progress(err: list[str]) -> list[str]:
        return [line for line in err if line.startswith(("tether batch:", "["))]

    # --quiet: no progress line; only the structured-log pointer stays.
    assert progress(err_q) == []
    assert err_q[-1].startswith("  structured log: ")

    default = progress(err_d)
    assert default[0] == (
        f"tether batch: 2 movie(s) into {tmp_path / 'default'} (policy=warn, idealize=off)"
    )
    assert default[1] == "[1/2] good_001: started"
    assert re.fullmatch(
        r"\[1/2\] good_001: ok in \d+\.\ds \(extract=done correct=done "
        r"idealize=not-requested\)",
        default[2],
    )
    assert default[3] == "[2/2] bad_002: started"
    assert re.fullmatch(
        r"\[2/2\] bad_002: FAIL in \d+\.\ds \(extract=failed\(.+\) .+\)", default[4]
    )
    assert len(default) == 5

    verbose = progress(err_v)
    assert "[1/2] good_001 extract: done - " in "\n".join(verbose)
    assert "[2/2] bad_002 correct: blocked - extract did not complete" in verbose

    # Verbose is the default plus the stage events: drop those and the rest is the same.
    def same(lines: list[str]) -> list[str]:
        return [re.sub(r" in \S+ \(", " in T (", line) for line in lines]

    events = [line for line in verbose if re.match(r"\[\d/2\] \S+ [\w-]+: ", line)]
    assert len(events) == 6  # three stages for each of the two movies
    rest = [line for line in verbose if line not in events]
    assert same(rest[1:]) == same(default[1:])
    assert rest[0] == default[0].replace("default", "verbose")

    # Plain text: no carriage return and no escape sequence, terminal or not.
    for line in err_d + err_v:
        assert "\r" not in line
        assert "\x1b" not in line


def test_cli_setup_line_comes_before_any_movie_is_read(tmp_path, monkeypatch, capsys) -> None:
    import tether.project.batch as batch_mod  # noqa: PLC0415

    seen: dict[str, str] = {}

    def _fake(jobs, **kwargs):
        seen["err"] = capsys.readouterr().err
        return batch_mod.BatchSummary(results=[], policy="fail", idealize_requested=False)

    monkeypatch.setattr(batch_mod, "run_batch", _fake)
    movie = tmp_path / "m_001.tif"
    movie.write_bytes(b"x")

    main(["batch", str(movie), "-d", str(tmp_path / "o"), "--policy", "fail", "--no-idealize"])

    assert seen["err"] == (
        f"tether batch: 1 movie(s) into {tmp_path / 'o'} (policy=fail, idealize=off)\n"
    )


class _Recorder(BatchProgress):
    """Records what the service tells it; ``run_started`` raises when asked to."""

    def __init__(self, *, raise_on_start: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self._raise = raise_on_start

    def run_started(self, **setup) -> None:
        self.calls.append(("run_started", setup))
        if self._raise:
            raise RuntimeError("display broke")

    def event(self, record) -> None:
        self.calls.append(("event", record["stage"]))


@pytest.mark.parametrize("raise_on_start", [False, True])
def test_the_batch_service_hands_its_progress_to_the_log_and_the_runner(
    tmp_path, monkeypatch, raise_on_start
) -> None:
    import tether.project.batch as batch_mod  # noqa: PLC0415
    from tether.services import BatchRequest  # noqa: PLC0415
    from tether.services import run as run_service  # noqa: PLC0415

    recorder = _Recorder(raise_on_start=raise_on_start)
    seen: dict[str, object] = {}

    def _fake(jobs, **kwargs):
        seen["progress"] = kwargs["progress"]
        kwargs["log"].event(movie="m_001", stage="extract", status="ok")
        return batch_mod.BatchSummary(results=[], policy="fail", idealize_requested=False)

    monkeypatch.setattr(batch_mod, "run_batch", _fake)
    out = tmp_path / "o"
    request = BatchRequest(movies=("m_001.tif",), out_dir=str(out), policy="fail", idealize=False)

    run_service(request, progress=recorder)

    assert seen["progress"] is recorder
    assert recorder.calls == [
        ("run_started", {"n_movies": 1, "out_dir": out, "policy": "fail", "idealize": False}),
        ("event", "extract"),
    ]


def test_a_refused_batch_states_no_setup(tmp_path) -> None:
    from tether.services import BatchRequest, RequestRefused  # noqa: PLC0415
    from tether.services import run as run_service  # noqa: PLC0415

    recorder = _Recorder()
    request = BatchRequest(movies=("a/m.tif", "b/m.tif"), out_dir=str(tmp_path / "o"))

    with pytest.raises(RequestRefused):
        run_service(request, progress=recorder)
    assert recorder.calls == []


def test_cli_quiet_and_verbose_are_exclusive(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["batch", "m.tif", "-d", str(tmp_path), "--quiet", "--verbose"])
    assert exc.value.code == 2
