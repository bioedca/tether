# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the skip budget of the CI jobs (#726): ``scripts/check_skips.py`` and
``tests/skip_budget.toml``.

pytest exits 0 when a test skips, so a job stays green when a fixture, a tool or a package
goes absent and its tests skip. Each CI job that runs pytest therefore writes a JUnit report,
and ``scripts/check_skips.py`` compares it with the job's part of ``tests/skip_budget.toml``.

Three groups of tests:

* the script, on JUnit reports that a real pytest writes for a small suite in a temporary
  folder — one more skip fails and names the test and its reason, a deselected file fails the
  minimum count, more skips than the maximum fail;
* the committed budget, which must name the six jobs and give a reason for each skip;
* the workflows, where each of the six jobs must write the report and run the check on it,
  and ``tests/test_plot_export.py``, which must fail, not skip, without matplotlib.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_skips.py"
BUDGET = REPO / "tests" / "skip_budget.toml"
WORKFLOWS = REPO / ".github" / "workflows"

_spec = importlib.util.spec_from_file_location("check_skips", SCRIPT)
assert _spec is not None and _spec.loader is not None
check_skips = importlib.util.module_from_spec(_spec)
# A dataclass looks its module up in sys.modules, so register it before it runs.
sys.modules[_spec.name] = check_skips
_spec.loader.exec_module(check_skips)

# --- A small suite, run by a real pytest -----------------------------------------------------

_SUITE = {
    "pytest.ini": """\
        [pytest]
        markers =
            slow: the marker that the deselection test excludes
        """,
    "tests/test_alpha.py": """\
        import os

        import pytest


        def test_runs():
            pass


        def test_extra():
            if os.environ.get("SKIP_BUDGET_EXTRA_SKIP") == "1":
                pytest.skip("the fixture went absent")


        @pytest.mark.skipif(True, reason="not on this job")
        def test_marked():
            pass


        class TestGroup:
            @pytest.mark.parametrize("value", ["a.b", "c"])
            def test_param(self, value):
                if value == "c":
                    pytest.skip("param skip")
        """,
    "tests/test_beta.py": """\
        import pytest

        pytest.importorskip("tether_absent_726")


        def test_never():
            pass
        """,
    "tests/test_gamma.py": """\
        import pytest

        pytestmark = pytest.mark.slow


        def test_one():
            pass


        def test_two():
            pass
        """,
}

#: The skips of the suite when it runs whole, as the script must read them.
_BASE_SKIPS = {
    "tests/test_alpha.py::test_marked": "not on this job",
    "tests/test_alpha.py::TestGroup::test_param[c]": "param skip",
    "tests/test_beta.py": (
        "could not import 'tether_absent_726': No module named 'tether_absent_726'"
    ),
}

_BUDGET = """\
    [jobs."demo"]
    min_collected = 8
    max_skipped = 3

    [jobs."other"]
    min_collected = 0
    max_skipped = 0

    [[skip]]
    test = "tests/test_alpha.py::test_marked"
    reason = "not on this job"
    jobs = ["demo"]
    why = "the demo job has no such thing"

    [[skip]]
    test = "tests/test_alpha.py::TestGroup::test_param[c]"
    reason = "param skip"
    jobs = ["demo"]
    why = "an exact node id that holds brackets"

    [[skip]]
    test = "tests/test_beta.py"
    reason = "could not import 'tether_absent_726': No module named 'tether_absent_726'"
    jobs = ["demo"]
    why = "a module that skips at collection"
    """


@pytest.fixture(scope="module")
def suite(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("suite")
    for name, text in _SUITE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def _pytest_report(suite: Path, name: str, *args: str, extra_skip: bool = False) -> Path:
    """Run a real pytest on the suite and return the JUnit report it writes."""
    report = suite / f"{name}.xml"
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("SKIP_BUDGET_EXTRA_SKIP", None)
    if extra_skip:
        env["SKIP_BUDGET_EXTRA_SKIP"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        + [f"--junitxml={report}", *args],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return report


@pytest.fixture(scope="module")
def base_report(suite: Path) -> Path:
    return _pytest_report(suite, "base")


@pytest.fixture
def budget(tmp_path: Path) -> Path:
    path = tmp_path / "budget.toml"
    path.write_text(textwrap.dedent(_BUDGET), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _plain_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plain ``error:`` format, also when this suite itself runs on GitHub Actions.

    On Actions the script writes workflow commands; the tests below read the plain format,
    and the one test of the Actions format sets the variable itself.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _run(capsys, *argv: str) -> tuple[int, str]:
    status = check_skips.main(list(argv))
    return status, capsys.readouterr().out


# --- The script --------------------------------------------------------------------------------


def test_the_report_gives_node_ids_counts_and_reasons(base_report: Path) -> None:
    report = check_skips.read_report(base_report)
    assert len(report.tests) == 8
    assert {skip.test: skip.reason for skip in report.skips} == _BASE_SKIPS
    assert "tests/test_alpha.py::TestGroup::test_param[a.b]" in report.tests
    assert "tests/test_gamma.py::test_two" in report.tests


def test_a_run_inside_the_budget_passes(capsys, base_report: Path, budget: Path) -> None:
    status, out = _run(
        capsys, "--job", "demo", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 0, out
    assert "8 tests (min_collected 8), 3 skipped (max_skipped 3)" in out
    assert "error" not in out


def test_one_more_skip_fails_and_names_the_test_and_the_reason(
    capsys, suite: Path, budget: Path
) -> None:
    report = _pytest_report(suite, "extra", extra_skip=True)
    status, out = _run(capsys, "--job", "demo", "--report", str(report), "--budget", str(budget))
    assert status == 1
    errors = [line for line in out.splitlines() if line.startswith("error:")]
    assert any(
        "unexpected skip: tests/test_alpha.py::test_extra" in line
        and "'the fixture went absent'" in line
        for line in errors
    ), out
    assert any("4 tests skipped" in line and "max_skipped = 3" in line for line in errors), out


def test_a_file_deselected_by_a_marker_fails_the_minimum(capsys, suite: Path, budget: Path) -> None:
    report = _pytest_report(suite, "deselected", "-m", "not slow")
    status, out = _run(capsys, "--job", "demo", "--report", str(report), "--budget", str(budget))
    assert status == 1
    errors = [line for line in out.splitlines() if line.startswith("error:")]
    assert errors == [errors[0]], out
    assert "6 tests ran" in errors[0] and "min_collected = 8" in errors[0]


def test_more_skips_than_the_maximum_fail_even_when_each_is_listed(
    capsys, base_report: Path, budget: Path
) -> None:
    budget.write_text(budget.read_text().replace("max_skipped = 3", "max_skipped = 2", 1))
    status, out = _run(
        capsys, "--job", "demo", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 1
    assert "3 tests skipped; the budget of demo allows max_skipped = 2." in out
    assert "unexpected skip" not in out


_FIRST_ENTRY = (
    'test = "tests/test_alpha.py::test_marked"\nreason = "not on this job"\njobs = ["demo"]'
)


@pytest.mark.parametrize(
    "entry",
    [
        # the same reason on another test
        'test = "tests/test_alpha.py::test_runs"\nreason = "not on this job"\njobs = ["demo"]',
        # the same test with another reason
        'test = "tests/test_alpha.py::test_marked"\nreason = "not here any more"\njobs = ["demo"]',
        # the right test and reason, listed for another job
        'test = "tests/test_alpha.py::test_marked"\nreason = "not on this job"\njobs = ["other"]',
    ],
    ids=["other-test", "other-reason", "other-job"],
)
def test_an_entry_allows_only_its_own_test_reason_and_job(
    capsys, base_report: Path, budget: Path, entry: str
) -> None:
    text = budget.read_text()
    assert text.count(_FIRST_ENTRY) == 1
    budget.write_text(text.replace(_FIRST_ENTRY, entry))
    status, out = _run(
        capsys, "--job", "demo", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 1
    assert "unexpected skip: tests/test_alpha.py::test_marked -- reason: 'not on this job'" in out


def test_a_pattern_entry_matches_with_fnmatch(capsys, base_report: Path, budget: Path) -> None:
    text = budget.read_text().replace(
        'test = "tests/test_alpha.py::test_marked"', 'test = "tests/test_alpha.py::test_m*"'
    )
    budget.write_text(text)
    status, out = _run(
        capsys, "--job", "demo", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 0, out


def test_loose_limits_and_absent_skips_are_notices(capsys, base_report: Path, budget: Path) -> None:
    text = budget.read_text().replace("min_collected = 8", "min_collected = 7", 1)
    text = text.replace("max_skipped = 3", "max_skipped = 5", 1)
    text += textwrap.dedent(
        """
        [[skip]]
        test = "tests/test_alpha.py::test_gone"
        reason = "no longer skips"
        jobs = ["demo"]
        why = "an entry whose skip did not occur"
        """
    )
    budget.write_text(text)
    status, out = _run(
        capsys, "--job", "demo", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 0, out
    notices = [line for line in out.splitlines() if line.startswith("notice:")]
    assert len(notices) == 3, out
    assert "expected skip did not occur: tests/test_alpha.py::test_gone" in notices[0]
    assert "fewer than max_skipped = 5" in notices[1]
    assert "more than min_collected = 7" in notices[2]


def test_on_github_actions_each_message_is_one_annotation_line(
    capsys, monkeypatch, suite: Path, budget: Path
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    report = suite / "multiline.xml"
    report.write_text(
        '<testsuites><testsuite tests="1"><testcase classname="tests.test_alpha" name="test_x">'
        '<skipped type="pytest.skip" message="line one&#10;100% line two"/>'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    status, out = _run(capsys, "--job", "other", "--report", str(report), "--budget", str(budget))
    assert status == 1
    lines = [line for line in out.splitlines() if "unexpected skip" in line]
    assert len(lines) == 1, out
    assert lines[0].startswith("::error title=skip budget::unexpected skip: ")
    assert "'line one\\n100%25 line two'" in lines[0]
    assert "  skipped: tests/test_alpha.py::test_x -- 'line one\\n100% line two'" in out


def test_an_xfail_is_not_a_skip(tmp_path: Path) -> None:
    report = tmp_path / "xfail.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_alpha" name="test_x">'
        '<skipped type="pytest.xfail" message="known bug"/></testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    result = check_skips.read_report(report)
    assert result.tests == ("tests/test_alpha.py::test_x",)
    assert result.skips == ()


@pytest.mark.parametrize(
    ("classname", "name", "expected"),
    [
        ("tests.test_alpha", "test_runs", "tests/test_alpha.py::test_runs"),
        (
            "tests.test_alpha.TestGroup",
            "test_p[1.5]",
            "tests/test_alpha.py::TestGroup::test_p[1.5]",
        ),
        ("tests.sub.test_x.TestA.TestB", "test_y", "tests/sub/test_x.py::TestA::TestB::test_y"),
        ("", "tests.test_beta", "tests/test_beta.py"),
    ],
)
def test_node_id_from_a_junit_testcase(classname: str, name: str, expected: str) -> None:
    assert check_skips.node_id(classname, name) == expected


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.replace('jobs = ["demo"]', 'jobs = ["dmeo"]', 1), "names no [jobs] table"),
        (lambda t: t.replace('why = "the demo job has no such thing"', 'why = " "'), "`why`"),
        (lambda t: t.replace("max_skipped = 3", "max_skipped = -1", 1), "`max_skipped`"),
        (lambda t: t.replace("min_collected = 8", 'min_collected = "8"', 1), "`min_collected`"),
        (lambda t: t + "\n[[skip]\n", "is not valid TOML"),
    ],
)
def test_a_bad_budget_cannot_give_a_verdict(
    capsys, base_report: Path, budget: Path, change, message: str
) -> None:
    budget.write_text(change(budget.read_text()))
    status, out = _run(
        capsys, "--job", "demo", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 2
    assert message in out


def test_an_absent_report_or_an_unknown_job_cannot_give_a_verdict(
    capsys, tmp_path: Path, base_report: Path, budget: Path
) -> None:
    status, out = _run(
        capsys, "--job", "demo", "--report", str(tmp_path / "none.xml"), "--budget", str(budget)
    )
    assert status == 2
    assert "no JUnit report at" in out
    status, out = _run(
        capsys, "--job", "nope", "--report", str(base_report), "--budget", str(budget)
    )
    assert status == 2
    assert 'has no [jobs."nope"] table' in out
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites>", encoding="utf-8")
    status, out = _run(capsys, "--job", "demo", "--report", str(broken), "--budget", str(budget))
    assert status == 2
    assert "is not a JUnit XML report" in out


# --- The committed budget and the workflows ----------------------------------------------------

#: The six jobs of #726 (each check name, keyed by workflow file and job id).
GUARDED = {
    ("ci.yml", "test"): ("test (ubuntu-latest)", "test (macos-latest)", "test (windows-latest)"),
    ("sidecar.yml", "parity"): ("sidecar / parity",),
    ("deep.yml", "smoke"): ("deep / smoke",),
    ("large-fixtures.yml", "large"): ("large-fixtures",),
}
REPORT = '"$RUNNER_TEMP/pytest-junit.xml"'
CHECK_COMMAND = f'python scripts/check_skips.py --job "$SKIP_BUDGET_JOB" --report {REPORT}'


def _pytest_lines(script: str) -> list[str]:
    """The non-comment lines of a ``run:`` script that start pytest as a command."""
    lines = []
    for line in script.splitlines():
        words = line.split()
        if words and not words[0].startswith("#") and "pytest" in words:
            lines.append(line.strip())
    return lines


def test_the_committed_budget_names_the_six_jobs_and_gives_each_skip_a_reason() -> None:
    import tomllib

    data = tomllib.loads(BUDGET.read_text(encoding="utf-8"))
    names = {name for names in GUARDED.values() for name in names}
    assert set(data["jobs"]) == names
    for name in sorted(names):
        budget = check_skips.load_budget(BUDGET, name)  # validates every entry
        assert budget.max_skipped >= 0 and budget.min_collected > 0
    for entry in data["skip"]:
        assert (
            entry["why"].strip() and entry["reason"].strip() and entry["test"].startswith("tests/")
        )


@pytest.mark.parametrize(("workflow", "job_id"), sorted(GUARDED))
def test_each_job_writes_the_report_and_compares_it_with_the_budget(
    workflow: str, job_id: str
) -> None:
    job = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][job_id]
    steps = [step for step in job["steps"] if isinstance(step, dict)]
    runners = [i for i, step in enumerate(steps) if _pytest_lines(step.get("run") or "")]
    assert len(runners) == 1, f"{workflow}:{job_id} must run pytest in exactly one step"
    run = steps[runners[0]]
    commands = _pytest_lines(run["run"])
    assert all(f"--junitxml={REPORT}" in command for command in commands), commands
    assert run.get("id"), f"the pytest step of {workflow}:{job_id} needs an id"
    assert not run.get("continue-on-error")

    checks = [i for i, step in enumerate(steps) if (step.get("run") or "").strip() == CHECK_COMMAND]
    assert len(checks) == 1, f"{workflow}:{job_id} must run `{CHECK_COMMAND}` in one step"
    assert checks[0] > runners[0], "the check must come after the pytest step"
    check = steps[checks[0]]
    assert not check.get("continue-on-error")
    condition = str(check.get("if", ""))
    # Live after a red pytest step, skipped exactly when pytest did not run.
    assert "!cancelled()" in condition
    assert f"steps.{run['id']}.outcome == 'success'" in condition
    assert f"steps.{run['id']}.outcome == 'failure'" in condition

    value = check["env"]["SKIP_BUDGET_JOB"]
    if "${{ matrix.os }}" in value:
        names = tuple(
            value.replace("${{ matrix.os }}", os_) for os_ in job["strategy"]["matrix"]["os"]
        )
    else:
        names = (value,)
    assert names == GUARDED[(workflow, job_id)]


def test_the_plot_export_tests_fail_without_matplotlib(tmp_path: Path) -> None:
    """The base stack requires matplotlib, so its absence fails the run instead of a skip."""
    source = (REPO / "tests" / "test_plot_export.py").read_text(encoding="utf-8")
    assert 'importorskip("matplotlib")' not in source
    shim = tmp_path / "shim" / "matplotlib"
    shim.mkdir(parents=True)
    (shim / "__init__.py").write_text(
        'raise ModuleNotFoundError("No module named \'matplotlib\'", name="matplotlib")\n',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(shim.parent), os.environ.get("PYTHONPATH", "")]),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        + ["tests/test_plot_export.py"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.INTERRUPTED, out
    assert "ModuleNotFoundError" in out and "matplotlib" in out
