# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The monthly mutation run (issue #725): the runner offline, and the workflow's shape.

The first half drives ``scripts/run_mutation.py`` on small in-memory sources and reports: which
tokens become mutants and which never do, which tests a mutant runs, how the score and a drop are
computed, and how the committed equivalent list is read. The second half holds the committed
files: the workflow runs only on a schedule or a manual dispatch (so it can never gate a pull
request), reports a drop through the #697 report job, installs its tooling outside every lock,
and the equivalent list gives a reason for each entry.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency)

_REPO = Path(__file__).resolve().parents[1]
SCRIPT = _REPO / "scripts" / "run_mutation.py"
WORKFLOW = _REPO / ".github" / "workflows" / "mutation.yml"
CONFIG = _REPO / ".github" / "mutation.toml"
LOCKS = ["conda-lock.yml", "sidecar/conda-lock.yml", "deep/conda-lock.yml"]

_spec = importlib.util.spec_from_file_location("run_mutation_under_test", SCRIPT)
rm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rm
_spec.loader.exec_module(rm)

SOURCE = '''"""Module doc."""
from typing import TYPE_CHECKING

LIMIT = 3
if TYPE_CHECKING:
    HIDDEN = 1


def f(a: int = 2, *args: int, b: float = 0.5) -> int:
    """Doc."""
    if a < LIMIT and not args:
        return a + 1
    x = -a
    x += 2 ** a
    return x if a is not None else int(a in [1])


class C:
    k = 4

    def g(self, n: int) -> str:
        return f"{n + 1}" * (n // 2)
'''


def _changes(source: str = SOURCE) -> list[tuple[int, str, str]]:
    return [(m.line, m.function, m.change) for m in rm.mutants_of(source, "src/x.py")]


def test_each_operator_family_gives_one_mutant_per_token() -> None:
    changes = _changes()
    assert (11, "f", "< -> <=") in changes
    assert (11, "f", "and -> or") in changes
    assert (11, "f", "not -> (removed)") in changes
    assert (12, "f", "+ -> -") in changes
    assert (13, "f", "- -> (removed)") in changes
    assert (14, "f", "+= -> -=") in changes
    assert (14, "f", "** -> *") in changes
    assert (15, "f", "is not -> is") in changes
    assert (15, "f", "in -> not in") in changes
    assert (22, "C.g", "// -> /") in changes


def test_numbers_change_to_a_different_number_and_defaults_are_mutated() -> None:
    changes = _changes()
    assert (4, "<module>", "3 -> 4") in changes
    assert (9, "f", "2 -> 3") in changes
    assert (9, "f", "0.5 -> 1.0") in changes
    assert (19, "C", "4 -> 5") in changes
    assert rm._number_mutant(0) == "1" and rm._number_mutant(0.0) == "1.0"
    assert rm._number_mutant(True) == "False" and rm._number_mutant("text") is None


def test_annotations_fstrings_type_checking_and_star_args_are_never_mutants() -> None:
    changes = _changes()
    assert not [c for c in changes if c[0] == 6], "a TYPE_CHECKING block never runs"
    # Only the two defaults of `def f(a: int = 2, *args: int, b: float = 0.5) -> int:`.
    assert [c for ln, _, c in changes if ln == 9] == ["2 -> 3", "0.5 -> 1.0"]
    # The f-string's `n + 1` is not a mutant; the operators outside it are.
    assert [c for ln, _, c in changes if ln == 22] == ["* -> /", "// -> /", "2 -> 3"]


def test_a_line_marked_no_mutate_gives_no_mutant() -> None:
    source = "def f(a):\n    return a + 1  # pragma: no mutate\n"
    assert _changes(source) == []


def test_apply_changes_exactly_one_span_and_keeps_non_ascii_columns() -> None:
    source = 'def f(a):\n    s = "µ"; return a < 1\n'
    (cmp,) = [m for m in rm.mutants_of(source, "src/x.py") if m.old == "<"]
    assert rm.apply(source, cmp) == 'def f(a):\n    s = "µ"; return a <= 1\n'
    (neg,) = rm.mutants_of("def f(a):\n    return -a\n", "src/x.py")
    assert rm.apply("def f(a):\n    return -a\n", neg) == "def f(a):\n    return a\n"


def test_removing_a_unary_operator_keeps_the_parentheses_of_its_operand() -> None:
    source = "def f(x):\n    if not (0 < x < 1):\n        return -(x)\n"
    removed = [rm.apply(source, m) for m in rm.mutants_of(source, "x") if m.new == ""]
    assert removed == [
        "def f(x):\n    if (0 < x < 1):\n        return -(x)\n",
        "def f(x):\n    if not (0 < x < 1):\n        return (x)\n",
    ]


def test_a_line_run_by_a_shared_fixture_runs_every_test_that_uses_the_fixture() -> None:
    (mutant,) = [
        m for m in rm.mutants_of("def f(a):\n    return a + 1\n", "src/x.py") if m.old == "+"
    ]
    # A module-scoped fixture ran the line once, while the first test set it up.
    lines = {"src/x.py": {2: {"tests/t.py::a", "fixture|tests/t.py|parsed"}}}
    passed = {"tests/t.py::a", "tests/t.py::b", "tests/t.py::c", "tests/u.py::d"}
    fixtures = {
        "tests/t.py::a": ["parsed"],
        "tests/t.py::b": ["parsed", "tmp_path"],
        "tests/t.py::c": ["tmp_path"],  # does not use it
        "tests/u.py::d": ["parsed"],  # another module's fixture of the same name
    }
    assert rm.covering_tests(mutant, lines, passed) == ["tests/t.py::a"]
    assert rm.covering_tests(mutant, lines, passed, fixtures) == ["tests/t.py::a", "tests/t.py::b"]
    # A session fixture (empty scope id) serves every test that asks for it.
    lines = {"src/x.py": {2: {"fixture||parsed"}}}
    every = ["tests/t.py::a", "tests/t.py::b", "tests/u.py::d"]
    assert rm.covering_tests(mutant, lines, passed, fixtures) == every


@pytest.mark.parametrize(("status", "tests"), [(2, {"t::a": {"outcome": "passed"}}), (0, {})])
def test_a_coverage_run_that_did_not_test_writes_no_report_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, tests: dict
) -> None:
    monkeypatch.setattr(rm, "run_contexts", lambda work: (tests, {}, status))
    out = tmp_path / "report"
    assert rm.main(["--out", str(out), "--jobs", "1"]) == 2
    assert not (out / "mutation-report.json").exists(), "a failed run must never become a base"


def test_a_mutant_runs_the_tests_of_its_statement_and_a_default_runs_the_callers() -> None:
    mutants = {(m.line, m.change): m for m in rm.mutants_of(SOURCE, "src/x.py")}
    lines = {"src/x.py": {4: {"t::import"}, 10: {"t::doc"}, 11: {"t::a", "t::b"}, 12: {"t::a"}}}
    passed = {"t::a", "t::b", "t::import", "t::doc"}
    assert rm.covering_tests(mutants[(11, "< -> <=")], lines, passed) == ["t::a", "t::b"]
    # A default value runs the tests that run the function body (past the docstring).
    assert rm.covering_tests(mutants[(9, "2 -> 3")], lines, passed) == ["t::a", "t::b"]
    # A module-level statement runs on import, so every test of the file runs it.
    every = ["t::a", "t::b", "t::doc", "t::import"]
    assert rm.covering_tests(mutants[(4, "3 -> 4")], lines, passed) == every
    # A test that failed in the contexts run would kill every mutant, so it never runs one.
    assert rm.covering_tests(mutants[(11, "< -> <=")], lines, {"t::b"}) == ["t::b"]


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_a_module_level_mutant_runs_the_tests_that_only_read_the_value(tmp_path: Path) -> None:
    # #883: ``test_defaults`` reads DEFAULT_STATE_NUMBER_LOW through the package that passes it
    # on, runs no line of state_number.py, and must still run the mutant of that constant.
    path = "src/tether/analysis/state_number.py"
    source = (_REPO / path).read_text(encoding="utf-8")
    _tree(
        tmp_path,
        {
            path: source,
            "src/tether/__init__.py": "",
            "src/tether/analysis/__init__.py": (
                "from tether.analysis.state_number import (\n"
                "    DEFAULT_STATE_NUMBER_LOW,\n    state_number_counts,\n)\n"
            ),
            "tests/test_analysis_state_number.py": (
                "from tether.analysis import DEFAULT_STATE_NUMBER_LOW\n\n\n"
                "def test_defaults():\n    assert DEFAULT_STATE_NUMBER_LOW == 1\n"
            ),
            "tests/test_other.py": "import tether\n\n\ndef test_x():\n    pass\n",
        },
    )
    (mutant,) = [
        m
        for m in rm.mutants_of(source, path)
        if m.line_text == "DEFAULT_STATE_NUMBER_LOW = 1" and m.change == "1 -> 2"
    ]
    assert mutant.function == "<module>" and not mutant.test_lines
    body = next(m for m in rm.mutants_of(source, path) if m.test_lines)
    defaults, other = (
        "tests/test_analysis_state_number.py::test_defaults",
        "tests/test_other.py::test_x",
    )
    counts = "tests/test_analysis_state_number.py::test_counts"
    lines = {path: {body.test_lines[0]: {counts}}}
    passed = {defaults, other, counts}
    imports = rm.imports_of_tests(tmp_path, [t.split("::")[0] for t in passed])
    assert rm.covering_tests(mutant, lines, passed) == [counts], "the gap #883 closes"
    assert rm.covering_tests(mutant, lines, passed, None, imports) == [counts, defaults]
    # A mutant inside a function runs the tests of its lines only, importers or not.
    assert defaults not in rm.covering_tests(body, lines, passed, None, imports)


def test_each_import_form_names_the_modules_the_test_file_loads(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "from .sub import ALIAS as NAME\nfrom pkg import loop\n",
            "src/pkg/sub.py": "from pkg.deep.leaf import VALUE as ALIAS\n",
            "src/pkg/deep/__init__.py": "",
            "src/pkg/deep/leaf.py": "VALUE = 1\n",
            "src/pkg/mod.py": "X = 1\n",
            "src/pkg/loop.py": "from pkg import loop\n",
            "src/pkg/shadow.py": (
                "try:\n    from pkg.mod import X\nexcept ImportError:\n    pass\n\n\n"
                "def f():\n    from pkg.deep.leaf import VALUE as X\n"
            ),
            "src/pkg/cond.py": (
                "import os\n\nif os.environ.get('A'):\n    from pkg.mod import X\n"
                "else:\n    from pkg.deep.leaf import VALUE as X\n"
            ),
        },
    )
    of = rm._Imports(tmp_path / "src").of
    # A dotted import also runs each parent package's ``__init__``.
    assert of("import pkg.mod as m\n") == {"pkg", "pkg.mod"}
    assert of("from pkg.mod import X\n") == {"pkg", "pkg.mod"}
    assert of("from pkg import mod\n") == {"pkg", "pkg.mod"}
    # A name passed on through two modules, relative and renamed on the way.
    assert of("from pkg import NAME\n") == {"pkg", "pkg.sub", "pkg.deep", "pkg.deep.leaf"}
    assert of("def t():\n    from pkg.deep import leaf\n") == {"pkg", "pkg.deep", "pkg.deep.leaf"}
    assert of("from pkg import *\n") == {"pkg"}
    assert of("from pkg import loop\n") == {"pkg", "pkg.loop"}, "a cycle ends"
    # A module-scope binding (here inside ``try``) counts; a function's local import does not.
    assert of("from pkg.shadow import X\n") == {"pkg", "pkg.shadow", "pkg.mod"}
    # Both branches of a conditional re-export are followed, not only the last one read.
    assert of("from pkg.cond import X\n") == {
        "pkg",
        "pkg.cond",
        "pkg.mod",
        "pkg.deep",
        "pkg.deep.leaf",
    }
    assert of("from . import x\nimport numpy\n") == {"numpy"}
    assert rm.module_name("src/pkg/deep/__init__.py") == "pkg.deep"
    assert rm.module_name("src/pkg/deep/leaf.py") == "pkg.deep.leaf"


def test_identical_changes_on_one_line_have_different_occurrences() -> None:
    mutants = [m for m in rm.mutants_of("def f(a):\n    return a + a + a\n", "x") if m.old == "+"]
    assert [m.occurrence for m in mutants] == [0, 1]
    assert len({m.key for m in mutants}) == 2


@pytest.mark.parametrize(
    ("codes", "outcome"),
    [
        ((0,), "survived"),
        ((1,), "killed"),
        ((4, 1), "killed"),
        ((5, 0), "survived"),
        ((4, 4), "error"),
    ],
)
def test_a_renamed_test_reruns_by_file_and_the_source_is_always_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codes: tuple[int, ...], outcome: str
) -> None:
    source = "def f(a):\n    return a + 1\n"
    target = tmp_path / "src" / "x.py"
    target.parent.mkdir()
    target.write_text(source, encoding="utf-8")
    (mutant,) = [m for m in rm.mutants_of(source, "src/x.py") if m.old == "+"]
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        assert target.read_text(encoding="utf-8") == rm.apply(source, mutant)
        return type("Done", (), {"returncode": codes[len(calls) - 1]})()

    monkeypatch.setattr(rm.subprocess, "run", fake_run)
    tests = ["tests/test_a.py::test_one[1e-06]", "tests/test_a.py::test_two"]
    assert rm.run_one(mutant, source, tmp_path, tests, 60.0) == outcome
    assert len(calls) == len(codes)
    if len(codes) == 2:  # exit 4 (not found) or 5 (none collected): the files run instead
        assert calls[1][-1:] == ["tests/test_a.py"] and calls[0][-2:] == tests
    assert target.read_text(encoding="utf-8") == source


def test_the_file_fallback_deselects_the_tests_that_failed_without_the_mutant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "def f(a):\n    return a + 1\n"
    target = tmp_path / "src" / "x.py"
    target.parent.mkdir()
    target.write_text(source, encoding="utf-8")
    (mutant,) = [m for m in rm.mutants_of(source, "src/x.py") if m.old == "+"]
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("Done", (), {"returncode": (4, 0)[len(calls) - 1]})()

    monkeypatch.setattr(rm.subprocess, "run", fake_run)
    tests = ["tests/test_a.py::test_one[1e-06]"]
    broken = ["tests/test_a.py::test_broken", "tests/test_other.py::test_broken"]
    assert rm.run_one(mutant, source, tmp_path, tests, 60.0, broken) == "survived"
    assert calls[1][-2:] == ["--deselect=tests/test_a.py::test_broken", "tests/test_a.py"]
    assert not any("test_other" in arg for arg in calls[1])


def test_the_fallback_deselects_a_failing_test_that_the_mutant_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mutant changes the parametrize id of both the selected test and the one that failed in
    # the coverage run ([1] -> [2]); real pytest runs the fallback's own arguments.
    source = "def f(a):\n    return a + 1\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text(source, encoding="utf-8")
    (mutant,) = [m for m in rm.mutants_of(source, "src/x.py") if m.old == "+"]
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("Done", (), {"returncode": (4, 0)[len(calls) - 1]})()

    monkeypatch.setattr(rm.subprocess, "run", fake_run)
    tests = ["tests/test_a.py::test_one[1]"]
    rm.run_one(mutant, source, tmp_path, tests, 60.0, ["tests/test_a.py::test_broken[1]"])
    fallback = calls[1][len(calls[0]) - len(tests) :]
    assert fallback == ["--deselect=tests/test_a.py::test_broken[", "tests/test_a.py"]

    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_a.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('x', [2])\n"
        "def test_one(x):\n    assert x\n\n"
        "@pytest.mark.parametrize('x', [2])\n"
        "def test_broken(x):\n    assert False\n",
        encoding="utf-8",
    )
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    monkeypatch.undo()
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *fallback]
    result = rm.subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout
    assert "1 passed, 1 deselected" in result.stdout


def _record(path: str, status: str) -> dict:
    return {"path": path, "status": status, "key": [path, "f", "x", "a -> b", 0]}


def test_the_score_counts_timeouts_as_found_and_leaves_out_the_rest() -> None:
    statuses = ["killed", "killed", "timeout", "survived", "uncovered", "equivalent"]
    row = rm.summarize([_record("a.py", s) for s in statuses])["a.py"]
    assert row["score"] == 75.0 and row["complete"] is True
    row = rm.summarize([_record("b.py", "killed"), _record("b.py", "not_run")])["b.py"]
    assert row["complete"] is False
    assert rm.summarize([_record("c.py", "uncovered")])["c.py"]["score"] is None


def _report(**modules: tuple[float | None, bool]) -> dict:
    return {"modules": {k: {"score": s, "complete": c} for k, (s, c) in modules.items()}}


def test_a_drop_of_more_than_five_points_is_reported_and_five_is_not() -> None:
    before = _report(a=(80.0, True), b=(80.0, True), c=(80.0, True), d=(80.0, False))
    now = _report(a=(74.9, True), b=(75.0, True), c=(60.0, False), d=(10.0, True), e=(0.0, True))
    assert rm.drops(before, now) == [{"path": "a", "before": 80.0, "now": 74.9}]
    assert rm.drops({}, now) == []


def test_the_report_lists_survivors_with_their_tests_and_stale_equivalents() -> None:
    survivor = _record("a.py", "survived") | {
        "line": 3,
        "function": "f",
        "change": "< -> <=",
        "line_text": "if a < b:",
        "tests": ["tests/test_a.py::test_one"],
    }
    config = {
        "equivalent": [
            {
                "path": "a.py",
                "function": "f",
                "line": "x = 1",
                "change": "1 -> 2",
                "reason": "unused",
            }
        ]
    }
    report = rm.build_report([survivor], config, "0" * 40) | {"drops": []}
    assert report["survivors"] == [survivor]
    assert report["stale_equivalents"] == config["equivalent"]
    text = rm.render_markdown(report)
    assert "`a.py:3` in `f`: `< -> <=`" in text and "tests/test_a.py::test_one" in text
    assert "match no mutant" in text


def test_the_config_needs_targets_and_a_reason_for_each_exclusion_and_equivalent() -> None:
    with pytest.raises(ValueError, match="paths"):
        rm.load_config("[targets]\npaths = []\n")
    with pytest.raises(ValueError, match="reason"):
        rm.load_config('[targets]\npaths = ["src"]\n[targets.exclude]\n"src/a.py" = " "\n')
    entry = '[[equivalent]]\npath = "a"\nfunction = "f"\nline = "x"\nchange = "a -> b"\n'
    with pytest.raises(ValueError, match="reason"):
        rm.load_config('[targets]\npaths = ["src"]\n' + entry + 'reason = ""\n')


# --- the committed files ------------------------------------------------------------------------


def test_the_committed_config_loads_and_every_equivalent_matches_a_target() -> None:
    config = rm.load_config(CONFIG.read_text(encoding="utf-8"))
    targets = set(rm.target_files(_REPO, config))
    assert targets, "the mutation run must have target files"
    assert not any(path.startswith(("src/tether/gui", ".agents")) for path in targets)
    for path in config["exclude"]:
        assert (_REPO / path).is_file(), f"an exclusion names no file: {path}"
    for entry in config["equivalent"]:
        assert entry["path"] in targets, f"an equivalent entry names no target: {entry}"
        source = (_REPO / entry["path"]).read_text(encoding="utf-8")
        keys = {m.key for m in rm.mutants_of(source, entry["path"])}
        assert rm.equivalent_key(entry) in keys, f"an equivalent entry matches no mutant: {entry}"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_runs_only_on_a_schedule_or_by_hand() -> None:
    on = _workflow().get("on", _workflow().get(True))
    assert set(on) == {"schedule", "workflow_dispatch"}, on
    (cron,) = [entry["cron"] for entry in on["schedule"]]
    assert cron.split()[2:] == ["3", "*", "*"], f"the run is monthly: {cron}"


def test_the_workflow_reports_a_drop_through_the_scheduled_failure_job() -> None:
    jobs = _workflow()["jobs"]
    uses = "./.github/workflows/scheduled-failure.yml"
    (report,) = [job for job in jobs.values() if job.get("uses") == uses]
    assert report["needs"] == "mutation" and report["if"] == "${{ !cancelled() }}"
    mutation = jobs["mutation"]
    assert mutation["timeout-minutes"] <= 360, "the GitHub job limit is 6 hours"
    run = next(s["run"] for s in mutation["steps"] if "run_mutation.py" in str(s.get("run")))
    budget = float(run.split("--budget-minutes")[1].split()[0])
    assert budget + 30 <= mutation["timeout-minutes"], "the budget leaves time for the report"
    upload = next(s for s in mutation["steps"] if "upload-artifact" in str(s.get("uses")))
    assert upload["if"] == "${{ !cancelled() }}" and upload["with"]["name"] == "mutation-report"


def test_the_tooling_is_pip_pinned_in_the_job_and_in_no_lock() -> None:
    steps = _workflow()["jobs"]["mutation"]["steps"]
    install = " ".join(str(s.get("run")) for s in steps if "pip install" in str(s.get("run")))
    assert '"coverage==7.17.2"' in install, install
    for lock in LOCKS:
        text = (_REPO / lock).read_text(encoding="utf-8")
        for name in ("mutmut", "cosmic-ray", "cosmic_ray"):
            assert f"name: {name}\n" not in text, f"{name} must not enter {lock}"
