# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The workstation runner itself, on the required matrix (ADR-0071, #698).

``scripts/workstation/run_private_checks.py`` runs the ``workstation`` tests on the maintainer's
workstation, where the private data is. These tests prove its three promises without that data:

* it refuses a commit that is not reachable from ``origin/main`` and fetches no other ref
  (a synthetic git origin with a ``main`` and a pull-request branch);
* its dry run writes a result file that the format check accepts (a tiny synthetic data folder);
* no name from the name-mapping file reaches the result file or the console, also when a check
  fails and pytest's own output does name the file (that output goes to the local log only).
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "workstation" / "run_private_checks.py"
_spec = importlib.util.spec_from_file_location("tether_run_private_checks_test", SCRIPT)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

#: Names that look like the real ones: distinctive, so a leak of any of them is unambiguous.
SECRET_FILES = {
    "lab-01": "SECRET-series-alpha/acquisition-ZETA-001.tif",
    "lab-02": {
        "movie": "SECRET-series-alpha/acquisition-ZETA-002.tif",
        "export": "deeplasi-OMEGA-export.mat",
    },
}


def _leaks(text: str) -> list[str]:
    names = []
    for value in SECRET_FILES.values():
        names += [value] if isinstance(value, str) else list(value.values())
    found = []
    for name in names:
        path = Path(name)
        found += [s for s in (name, name.replace("/", "\\"), path.name, path.stem) if s in text]
    return found


def _data_dir(root: Path, *, missing: str | None = None) -> Path:
    data = root / "data"
    for value in SECRET_FILES.values():
        for rel in [value] if isinstance(value, str) else value.values():
            if rel != missing:
                (data / rel).parent.mkdir(parents=True, exist_ok=True)
                (data / rel).write_bytes(b"synthetic")
    (data / runner.MAPPING_FILE).write_text(json.dumps(SECRET_FILES), encoding="utf-8")
    return data


def _dry_run(root: Path, data: Path) -> subprocess.CompletedProcess:
    """A dry run started in *root* with RELATIVE paths, which pytest must still find elsewhere."""
    cmd = [sys.executable, str(SCRIPT), "run", "--dry-run", "--state", "state"]
    cmd += ["--data", data.relative_to(root).as_posix()]
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module")
def passing_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("passing")
    proc = _dry_run(root, _data_dir(root))
    return root, proc


@pytest.fixture(scope="module")
def failing_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("failing")
    proc = _dry_run(root, _data_dir(root, missing=SECRET_FILES["lab-02"]["export"]))
    return root, proc


def _only_result(root: Path) -> tuple[str, dict]:
    files = list((root / "state" / "results").glob("*.json"))
    assert len(files) == 1, files
    text = files[0].read_text(encoding="utf-8")
    return text, json.loads(text)


# --- The format and the privacy of a result ----------------------------------------------------


def test_the_dry_run_writes_a_result_that_matches_the_format(passing_run) -> None:
    root, proc = passing_run
    assert proc.returncode == runner.EXIT_OK, proc.stdout + proc.stderr
    _, result = _only_result(root)
    assert runner.validate_result(result) == []
    assert (result["mode"], result["status"], result["on_main"]) == ("dry-run", "passed", False)
    (check,) = result["checks"]
    assert check["check"] == f"{runner.SELF_CHECK}::test_every_neutral_id_names_readable_files"
    assert check["outcome"] == "passed"
    assert check["datasets"] == {
        "lab-01": {"n_files": 1, "n_bytes": 9},
        "lab-02": {"n_files": 2, "n_bytes": 18},
    }
    assert result["tools"]["python"] and "pytest" in result["tools"]


def test_no_mapped_name_reaches_the_result_or_the_console(passing_run, failing_run) -> None:
    for root, proc in (passing_run, failing_run):
        text, _ = _only_result(root)
        assert _leaks(text) == []
        assert _leaks(proc.stdout + proc.stderr) == []
    root, proc = failing_run
    assert proc.returncode == runner.EXIT_FAILED, proc.stdout + proc.stderr
    _, result = _only_result(root)
    assert result["status"] == "failed" and result["checks"][0]["outcome"] == "failed"
    # Not vacuous: pytest's own report of the failure does name the file, in the local log.
    (log,) = (root / "state" / "logs").glob("*.log")
    assert _leaks(log.read_text(encoding="utf-8"))


def test_a_result_that_names_a_private_file_is_withheld(passing_run) -> None:
    root, _ = passing_run
    _, result = _only_result(root)
    tokens = runner.private_tokens(runner.load_mapping(root / "data"))
    result["checks"][0]["check"] = "tests/test_x.py::test_y[acquisition-ZETA-001]"
    sealed, text = runner.seal(result, tokens)
    assert sealed["status"] == "withheld" and sealed["checks"] == []
    assert _leaks(text) == [] and runner.validate_result(sealed) == []


def test_a_folder_name_of_the_mapping_is_private_too(passing_run) -> None:
    root, _ = passing_run
    _, result = _only_result(root)
    tokens = runner.private_tokens(runner.load_mapping(root / "data"))
    assert "SECRET-series-alpha" in tokens
    result["checks"][0]["check"] = "tests/test_x.py::test_y[SECRET-series-alpha]"
    sealed, text = runner.seal(result, tokens)
    assert sealed["status"] == "withheld" and sealed["checks"] == []
    assert "SECRET-series-alpha" not in text
    assert runner.redact("in SECRET-series-alpha", tokens) == f"in {runner.PRIVATE}"


@pytest.mark.parametrize("name", ['acquisition-"ZETA".tif', "acquisition-éclair.tif"])
def test_a_name_that_json_escapes_is_still_found(passing_run, tmp_path, name) -> None:
    """JSON text escapes a quote or a non-ASCII letter, so the search reads decoded strings."""
    (tmp_path / runner.MAPPING_FILE).write_text(json.dumps({"lab-01": name}), encoding="utf-8")
    tokens = runner.private_tokens(runner.load_mapping(tmp_path))
    root, _ = passing_run
    _, result = _only_result(root)
    result["checks"][0]["check"] = f"tests/test_x.py::test_y[{name}]"
    sealed, text = runner.seal(result, tokens)
    assert sealed["status"] == "withheld" and sealed["checks"] == []
    assert name not in json.loads(text)["checks"] and Path(name).stem not in text


def test_the_environment_folders_come_first_on_path_as_the_launchers_put_them() -> None:
    """The scheduled task runs the env's python unactivated; MKL needs its folders first (#553)."""
    bat = (_REPO / "packaging" / "scripts" / "post_install.bat").read_text(encoding="utf-8")
    (launcher,) = [ln for ln in bat.splitlines() if ln.startswith('set "ENV_PATH=')]
    expected = [d.removeprefix("%ENV_D%").lstrip("\\") for d in launcher[14:-1].split(";")]
    assert list(runner.WINDOWS_ACTIVATION_DIRS) == expected
    env = runner.activated_env(Path("C:/envs/tether"), windows=True)
    first = env["PATH"].split(";")[: len(expected)]
    assert first[0] == str(runner.PureWindowsPath("C:/envs/tether"))
    assert first[3].endswith("Library\\bin")
    assert runner.activated_env(None) == dict(runner.os.environ)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["checks"][0]["datasets"].update({"acquisition-ZETA-001.tif": {}}),
        lambda r: r["checks"][0]["datasets"]["lab-01"].update({"n_bytes": "9"}),
        lambda r: r.update({"on_main": True}),
        lambda r: r.update({"traces": [0.1, 0.2]}),
    ],
    ids=["file-name-as-dataset", "text-as-number", "dry-run-on-main", "extra-key"],
)
def test_the_format_check_rejects_what_a_result_may_not_hold(passing_run, change) -> None:
    root, _ = passing_run
    _, result = _only_result(root)
    change(result)
    assert runner.validate_result(result)


@pytest.mark.parametrize(
    "mapping",
    [
        {"acquisition-ZETA-001.tif": "acquisition-ZETA-001.tif"},
        {"lab-01": "/data/acquisition-ZETA-001.tif"},
        {"lab-01": "C:\\data\\acquisition-ZETA-001.tif"},
        {"lab-01": "../acquisition-ZETA-001.tif"},
        {"lab-01": {"Acquisition-ZETA-001": "x.tif"}},
        {},
    ],
)
def test_a_bad_mapping_is_refused_without_quoting_it(tmp_path, mapping) -> None:
    (tmp_path / runner.MAPPING_FILE).write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(runner.MappingError) as exc:
        runner.load_mapping(tmp_path)
    assert "ZETA" not in str(exc.value)


def test_a_run_inside_the_interval_does_nothing(passing_run, capsys) -> None:
    root, _ = passing_run
    args = ["run", "--dry-run", "--state", str(root / "state"), "--data", str(root / "data")]
    assert runner.main([*args, "--min-interval-hours", "24"]) == runner.EXIT_OK
    assert capsys.readouterr().out.startswith("skipped:")
    assert len(list((root / "state" / "results").glob("*.json"))) == 1


# --- The refusal ---------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    proc = subprocess.run(["git", *cfg, *args], cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def origin(tmp_path) -> dict:
    """A repository with two commits on ``main`` and a pull-request branch off the first."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    shas = []
    for name in ("one", "two"):
        (repo / f"{name}.txt").write_text(name, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "--quiet", "-m", name)
        shas.append(_git(repo, "rev-parse", "HEAD"))
    _git(repo, "checkout", "--quiet", "-b", "pr", shas[0])
    (repo / "pr.txt").write_text("unreviewed", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "pr")
    pr = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "main")
    return {"url": str(repo), "first": shas[0], "tip": shas[1], "pr": pr}


def test_the_clone_gets_the_tip_of_main_and_no_other_ref(tmp_path, origin) -> None:
    clone = tmp_path / "clone"
    assert runner.prepare_clone(clone, origin["url"]) == origin["tip"]
    assert _git(clone, "for-each-ref", "--format=%(refname)") == "refs/remotes/origin/main"
    assert runner.prepare_clone(clone, origin["url"], origin["first"][:12]) == origin["first"]


def test_the_runner_refuses_a_commit_that_is_not_on_main(tmp_path, origin) -> None:
    clone = tmp_path / "clone"
    with pytest.raises(runner.Refused, match="not in the history of origin/main"):
        runner.prepare_clone(clone, origin["url"], origin["pr"])
    # Also when someone fetched the pull request into the clone by hand.
    _git(clone, "fetch", "--quiet", "origin", "refs/heads/pr:refs/heads/pr")
    with pytest.raises(runner.Refused, match="not reachable from origin/main"):
        runner.prepare_clone(clone, origin["url"], origin["pr"])
    assert not (clone / "pr.txt").exists()


def test_the_runner_refuses_a_dirty_clone_or_another_origin(tmp_path, origin) -> None:
    clone = tmp_path / "clone"
    runner.prepare_clone(clone, origin["url"])
    (clone / "one.txt").write_text("edited", encoding="utf-8")
    with pytest.raises(runner.Refused, match="local changes"):
        runner.prepare_clone(clone, origin["url"])
    with pytest.raises(runner.Refused, match="not the canonical repository"):
        runner.prepare_clone(clone, str(tmp_path / "elsewhere"))


def test_a_scheduled_run_refuses_before_it_builds_or_runs_anything(
    tmp_path, origin, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(runner, "CANONICAL_REMOTE", origin["url"])
    monkeypatch.setattr(runner, "ensure_env", lambda *a: pytest.fail("built an environment"))
    data = _data_dir(tmp_path)
    args = ["run", "--state", str(tmp_path / "state"), "--data", str(data)]
    assert runner.main([*args, "--commit", origin["pr"]]) == runner.EXIT_REFUSED
    assert "refused: commit" in capsys.readouterr().out
    _, result = _only_result(tmp_path)  # the refusal is recorded, so `latest` shows it at once
    assert (result["status"], result["on_main"], result["checks"]) == ("refused", False, [])
    assert runner.validate_result(result) == []
    titles = [i["title"] for i in runner.latest(tmp_path / "state" / "results")["open_or_update"]]
    assert titles == [runner.RUN_ISSUE_TITLE]


def test_a_fetch_that_does_not_finish_is_a_run_error_with_a_result(
    tmp_path, origin, monkeypatch, capsys
) -> None:
    """A stalled fetch must not keep the run (and its lock) alive with no result."""
    monkeypatch.setattr(runner, "CANONICAL_REMOTE", origin["url"])
    monkeypatch.setattr(runner, "ensure_env", lambda *a: pytest.fail("built an environment"))
    real_run = subprocess.run

    def fetch_stalls(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "fetch"]:
            assert kwargs.get("timeout") == runner.NETWORK_TIMEOUT_S
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", fetch_stalls)
    data = _data_dir(tmp_path)
    args = ["run", "--state", str(tmp_path / "state"), "--data", str(data)]
    assert runner.main(args) == runner.EXIT_ERROR
    assert "error: git fetch did not finish" in capsys.readouterr().out
    _, result = _only_result(tmp_path)
    assert (result["status"], result["checks"]) == ("error", [])


def test_a_grandchild_that_keeps_the_output_open_does_not_extend_the_timeout(tmp_path) -> None:
    """git starts helpers (git-remote-https, git-lfs) that can live on after git is killed."""
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "                     stdout=sys.stdout, stderr=sys.stderr)\n"
        "open(sys.argv[1], 'w').write(str(g.pid))\n"
        "time.sleep(120)\n"
    )
    start = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="did not finish in 5 s"):
            runner._run_bounded([sys.executable, "-c", code, str(pid_file)], tmp_path, timeout=5)
        assert time.monotonic() - start < 60  # not the 120 s of the grandchild
        assert pid_file.read_text()  # the grandchild was started, so the case was exercised
    finally:
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(pid_file.read_text()), signal.SIGTERM)


def test_a_live_run_keeps_its_lock_however_long_it_takes(tmp_path) -> None:
    with runner._run_lock(tmp_path):
        old = time.time() - 48 * 3600  # a run older than a day is still a live run
        os.utime(tmp_path / "run.lock", (old, old))
        with (
            pytest.raises(RuntimeError, match="another run holds the lock"),
            runner._run_lock(tmp_path),
        ):
            pytest.fail("two runs at once")
    with runner._run_lock(tmp_path):  # released at the end, and the lock file left over
        pass  # from a run that ended (or died) never blocks the next one


def test_the_lock_of_a_run_that_was_killed_is_free(tmp_path) -> None:
    code = (
        "import importlib.util, os, sys, pathlib\n"
        f"spec = importlib.util.spec_from_file_location('r', {str(SCRIPT)!r})\n"
        "r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)\n"
        "with r._run_lock(pathlib.Path(sys.argv[1])):\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.readline()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "held"
        with (
            pytest.raises(RuntimeError, match="another run holds the lock"),
            runner._run_lock(tmp_path),
        ):
            pass
    finally:
        proc.kill()
        proc.wait(timeout=60)
    deadline = time.monotonic() + 30  # Windows releases a dead process's lock soon, not at once
    while True:
        try:
            with runner._run_lock(tmp_path):
                break
        except RuntimeError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def test_the_fixture_skips_unless_the_runner_started_pytest(tmp_path) -> None:
    """$TETHER_PRIVATE_DATA alone (a bare pytest) must not run a check whose output is not kept
    in the runner's local log."""
    env = {k: v for k, v in os.environ.items() if k != runner.RECORD_ENV}
    env[runner.DATA_ENV] = str(_data_dir(tmp_path))
    cmd = [sys.executable, "-m", "pytest", "-m", "workstation", "-p", "no:cacheprovider", "-rs"]
    proc = subprocess.run(
        [*cmd, runner.SELF_CHECK], cwd=_REPO, env=env, capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout and runner.RECORD_ENV in proc.stdout
    assert _leaks(proc.stdout + proc.stderr) == []


def test_a_cut_off_check_record_is_a_run_error_with_a_result(tmp_path, monkeypatch, capsys) -> None:
    """pytest killed while it writes a record line: the run still leaves a result to read."""

    def pytest_dies_mid_line(cmd, log, *, check=True, **kwargs):
        record = Path(kwargs["env"][runner.RECORD_ENV])
        record.write_text('{"nodeid": "tests/test_x.py::test_y", "kind": "rep', encoding="utf-8")
        return 1

    monkeypatch.setattr(runner, "_run_logged", pytest_dies_mid_line)
    data = _data_dir(tmp_path)
    args = ["run", "--dry-run", "--state", str(tmp_path / "state"), "--data", str(data)]
    assert runner.main(args) == runner.EXIT_ERROR
    assert "error: the check record" in capsys.readouterr().out
    _, result = _only_result(tmp_path)
    assert (result["status"], result["checks"]) == ("error", [])
    assert runner.validate_result(result) == []


# --- What an agent reads, and the agreements with the rest of the repository ---------------------


def test_latest_names_one_issue_for_each_check_that_did_not_pass(passing_run, tmp_path) -> None:
    root, _ = passing_run
    _, result = _only_result(root)
    ok = result["checks"][0]
    bad = {**ok, "check": "tests/test_x.py::test_y", "outcome": "failed"}
    result.update(status="failed", checks=[ok, bad])
    (tmp_path / "20261111T000000Z-0123456789ab.json").write_text(json.dumps(result), "utf-8")
    report = runner.latest(tmp_path)
    assert [i["title"] for i in report["open_or_update"]] == [
        "test(workstation): tests/test_x.py::test_y failed on main"
    ]
    assert report["passed"] == [runner.issue_title(ok["check"])]


def test_the_runner_and_ci_install_the_same_test_tooling() -> None:
    ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert all(f'"{pin}"' in ci for pin in runner.TEST_TOOLS), runner.TEST_TOOLS


def test_the_conftest_hook_reads_the_runner_s_record_variable() -> None:
    conftest = (_REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert f'os.environ.get("{runner.RECORD_ENV}")' in conftest


def test_a_workstation_test_with_another_tier_marker_stops_collection(tmp_path) -> None:
    """``-m large`` would select such a test in ``large-fixtures.yml``; collection refuses it."""
    (tmp_path / "conftest.py").write_bytes((_REPO / "tests" / "conftest.py").read_bytes())
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    workstation: x\n    large: x\n    sidecar: x\n    deep: x\n",
        encoding="utf-8",
    )
    (tmp_path / "test_both.py").write_text(
        "import pytest\n\n\n@pytest.mark.workstation\n@pytest.mark.large\n"
        "def test_both():\n    pass\n",
        encoding="utf-8",
    )
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-m", "not workstation"]
    proc = subprocess.run([*cmd, str(tmp_path)], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == pytest.ExitCode.USAGE_ERROR, proc.stdout + proc.stderr
    assert "test_both.py::test_both" in proc.stdout + proc.stderr
