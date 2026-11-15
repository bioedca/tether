# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The monthly mutation run of the science modules (issue #725). It reports; it never gates.

Coverage shows which lines the tests run, not whether a test fails when a line is wrong. This
script changes one token at a time in the modules that ``.github/mutation.toml`` names (``<`` to
``<=``, ``+`` to ``-``, ``and`` to ``or``, a number to a different number, ``not x`` to ``x``)
and runs, for each change (a *mutant*), only the tests that ran its line. A mutant that no test
finds *survives*. A line at module or class level runs on import, before any test, so its mutants
also run the tests of each test file that imports the module (#883).

1. ``contexts``: one run of the default tier under ``coverage`` with this file loaded as a pytest
   plugin, which names the coverage context of each line after the test that ran it (the pytest
   node id) and records each test's result and duration.
2. ``mutants``: every mutant of every target file, from the syntax tree, so a ``*`` in ``*args``
   or a number in a type annotation is never one.
3. ``run``: each mutant is written into a private copy of ``src/`` that ``PYTHONPATH`` puts
   first, and pytest runs the covering tests that passed in step 1, stopping at the first
   failure. A failure or an error kills the mutant; a run past its time limit counts as killed.
4. ``report``: ``mutation-report.json`` and ``mutation-report.md`` with the score of each module
   (killed / (killed + survived)) and each survivor with its file, line, change and covering
   tests. A mutant that matches an entry of the committed equivalent list is reported once, in
   its own section, and is not counted.
5. ``compare``: with ``--previous``, exit 1 when a module's score fell by more than
   ``DROP_POINTS`` from the last report, so the workflow's report job (#697) opens or updates
   one issue. Survivors alone never fail the run, and no pull request ever runs it.

``--budget-minutes`` stops starting mutants when the time is spent; the rest are ``not_run`` and a
module that did not finish is marked incomplete and never compared, so the job ends inside the
GitHub job limit with a report.

Standard library and ``coverage`` only. ``coverage`` is pip-pinned in the workflow as in the
required matrix (#724, ADR-0072) and is in no lock file.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / ".github" / "mutation.toml"
#: The tiers the required matrix leaves out (``tests/test_marker_contract.py`` pins that list).
MARKERS = "not large and not sidecar and not deep and not workstation and not reference_download"
#: A module whose score falls by more than this many points from the last report fails the run.
DROP_POINTS = 5.0
#: A mutant may run this many times as long as its tests took in the contexts run, plus a floor.
TIME_FACTOR, TIME_FLOOR = 5.0, 60.0
#: Where the plugin half of this file writes each test's result (step 1).
TESTS_ENV = "TETHER_MUTATION_TESTS_JSON"
#: One thread per numeric library, in the contexts run and in every worker, so parallel workers
#: do not oversubscribe the runner and every run sums in the same order.
THREADS = dict.fromkeys(
    ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"), "1"
)

_COMPARE = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}
_COMPARE_SWAP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
_MEMBER = {ast.Is: ("is", "is not"), ast.IsNot: ("is not", "is")}
_MEMBER |= {ast.In: ("in", "not in"), ast.NotIn: ("not in", "in")}
_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//"}
_BINARY |= {ast.Mod: "%", ast.Pow: "**"}
_BINARY_SWAP = {"+": "-", "-": "+", "*": "/", "/": "*", "//": "/", "%": "//", "**": "*"}
_BOOL = {ast.And: ("and", "or"), ast.Or: ("or", "and")}


# --- pytest plugin (step 1): loaded with ``-p run_mutation`` ------------------------------------
_results: dict[str, dict] = {}
_current = [""]  # the context in force, so a fixture can put it back
#: The context of a fixture that serves more than one test (module, class, package or session
#: scope): ``fixture|<node id of its scope>|<fixture name>``. Its lines ran once, for the first
#: test that asked for it, and every later test that asks for it uses what they made.
FIXTURE_CONTEXT = "fixture|"

try:
    import pytest as _pytest
except ImportError:  # the runner's own process does not need pytest
    _pytest = None


def _switch_context(name: str) -> None:
    import coverage

    _current[0] = name
    current = coverage.Coverage.current()
    if current is not None:
        current.switch_context(name)


def _entry(nodeid: str) -> dict:
    return _results.setdefault(nodeid, {"outcome": "passed", "duration": 0.0, "fixtures": []})


def pytest_runtest_setup(item) -> None:  # noqa: ANN001 - pytest hook
    _entry(item.nodeid)["fixtures"] = sorted(getattr(item, "fixturenames", ()))
    _switch_context(item.nodeid)


def pytest_runtest_logfinish(nodeid: str, location: object) -> None:
    _switch_context("")


if _pytest is not None:

    @_pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(fixturedef, request):  # noqa: ANN001, ANN201 - pytest hook
        if fixturedef.scope == "function":
            return (yield)
        before = _current[0]
        _switch_context(f"{FIXTURE_CONTEXT}{request.node.nodeid}|{fixturedef.argname}")
        try:
            return (yield)
        finally:
            _switch_context(before)


def pytest_runtest_logreport(report) -> None:  # noqa: ANN001 - pytest hook
    entry = _entry(report.nodeid)
    entry["duration"] += float(report.duration)
    if report.outcome != "passed" and entry["outcome"] == "passed":
        entry["outcome"] = report.outcome


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    if os.environ.get(TESTS_ENV):
        Path(os.environ[TESTS_ENV]).write_text(json.dumps(_results, indent=1), encoding="utf-8")


# --- mutants (step 2) ---------------------------------------------------------------------------
@dataclass
class Mutant:
    path: str  # repository-relative, POSIX
    line: int
    col: int  # character columns, not the bytes ``ast`` counts
    end_line: int
    end_col: int
    old: str
    new: str
    function: str  # qualified name of the enclosing def, or "<module>"
    line_text: str
    #: The lines whose tests run this mutant; empty means "every test of the file, and every
    #: test that imports the module" (a module- or class-level statement, which runs on import,
    #: before any test context).
    test_lines: list[int] = field(default_factory=list)
    occurrence: int = 0  # how many identical changes precede it on the same line

    @property
    def change(self) -> str:
        return f"{self.old} -> {self.new or '(removed)'}"

    @property
    def key(self) -> tuple[str, str, str, str, int]:
        """The identity that survives line moves: the equivalent list matches on it."""
        return (self.path, self.function, self.line_text, self.change, self.occurrence)


def _char_col(lines: list[str], lineno: int, byte_col: int) -> int:
    return len(lines[lineno - 1].encode("utf-8")[:byte_col].decode("utf-8"))


def _number_mutant(value: object) -> str | None:
    if isinstance(value, bool):
        return repr(not value)
    if isinstance(value, int):
        return "1" if value == 0 else repr(value + 1)
    if isinstance(value, float):
        return "1.0" if value == 0.0 else repr(value * 2)
    return None


def mutants_of(source: str, path: str) -> list[Mutant]:
    """Every mutant of one file, in source order. Lines that say ``pragma: no mutate`` are kept."""
    tree = ast.parse(source)
    lines = source.splitlines()
    tokens = list(tokenize.generate_tokens(iter(source.splitlines(keepends=True)).__next__))
    parents: dict[ast.AST, tuple[ast.AST, str]] = {}
    for node in ast.walk(tree):
        for name, value in ast.iter_fields(node):
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    parents[child] = (node, name)

    def context(node: ast.AST) -> tuple[str, list[int]] | None:
        """(qualified name, lines whose tests run it), or None for a node never to mutate."""
        names: list[str] = []
        test_lines: list[int] | None = None
        stmt: ast.stmt | None = None
        child = node
        while child in parents:
            if stmt is None and isinstance(child, ast.stmt):
                stmt = child
            parent, name = parents[child]
            if name in ("annotation", "returns") or isinstance(parent, ast.JoinedStr):
                return None
            if isinstance(parent, ast.If) and _is_type_checking(parent.test):
                return None
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(parent.name)
                if test_lines is None and not isinstance(parent, ast.ClassDef):
                    if name == "body" and stmt is not None:
                        test_lines = [stmt.lineno, node.lineno]
                    elif name == "args":  # a default value: the tests that call the function
                        test_lines = [_first_body_line(parent)]
            child = parent
        return ".".join(reversed(names)) or "<module>", sorted(set(test_lines or []))

    found: list[tuple[int, int, int, int, str, str, ast.AST]] = []

    by_line: dict[int, list[int]] = {}
    for i, tok in enumerate(tokens):
        by_line.setdefault(tok.start[0], []).append(i)

    def token_between(start: tuple[int, int], stop: tuple[int, int], text: str) -> tuple | None:
        """The span of *text* (one token, or two words such as ``not in``) inside the gap."""
        words = text.split()
        for row in range(start[0], stop[0] + 1):
            for i in by_line.get(row, ()):
                seq = tokens[i : i + len(words)]
                if seq[0].start < start or seq[-1].end > stop:
                    continue
                if [t.string for t in seq] == words:
                    return (*seq[0].start, *seq[-1].end)
        return None

    def next_token_start(pos: tuple[int, int]) -> tuple[int, int] | None:
        for i in by_line.get(pos[0], ()):
            if tokens[i].start == pos and i + 1 < len(tokens):
                return tokens[i + 1].start
        return None

    def end_of(n: ast.AST) -> tuple[int, int]:
        return (n.end_lineno, _char_col(lines, n.end_lineno, n.end_col_offset))

    def start_of(n: ast.AST) -> tuple[int, int]:
        return (n.lineno, _char_col(lines, n.lineno, n.col_offset))

    def add(span: tuple | None, old: str, new: str, node: ast.AST) -> None:
        if span is not None:
            found.append((*span, old, new, node))

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for op, left, right in zip(node.ops, operands, operands[1:], strict=False):
                if type(op) in _COMPARE:
                    old = _COMPARE[type(op)]
                    new = _COMPARE_SWAP[old]
                elif type(op) in _MEMBER:
                    old, new = _MEMBER[type(op)]
                else:
                    continue
                add(token_between(end_of(left), start_of(right), old), old, new, node)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            old = _BINARY[type(node.op)]
            span = token_between(end_of(node.left), start_of(node.right), old)
            add(span, old, _BINARY_SWAP[old], node)
        elif isinstance(node, ast.AugAssign) and type(node.op) in _BINARY:
            old = _BINARY[type(node.op)] + "="
            span = token_between(end_of(node.target), start_of(node.value), old)
            add(span, old, _BINARY_SWAP[old[:-1]] + "=", node)
        elif isinstance(node, ast.BoolOp):
            old, new = _BOOL[type(node.op)]
            for left, right in zip(node.values, node.values[1:], strict=False):
                add(token_between(end_of(left), start_of(right), old), old, new, node)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
            old = "not" if isinstance(node.op, ast.Not) else "-"
            # The operator token and the space after it go, and nothing else: ``not (a < b)``
            # becomes ``(a < b)``, ``-x`` becomes ``x``.
            after = next_token_start(start_of(node))
            add(None if after is None else (*start_of(node), *after), old, "", node)
        elif isinstance(node, ast.Constant) and (new := _number_mutant(node.value)) is not None:
            add((*start_of(node), *end_of(node)), repr(node.value), new, node)

    result: list[Mutant] = []
    seen: dict[tuple, int] = {}
    for line, col, end_line, end_col, old, new, node in sorted(found, key=lambda f: f[:4]):
        text = lines[line - 1]
        if "pragma: no mutate" in text:
            continue
        where = context(node)
        if where is None:
            continue
        mutant = Mutant(path, line, col, end_line, end_col, old, new, where[0], text.strip())
        mutant.test_lines = where[1]
        mutant.occurrence = seen.get((line, old, new), 0)
        seen[(line, old, new)] = mutant.occurrence + 1
        try:
            compile(apply(source, mutant), path, "exec")
        except SyntaxError:
            continue
        result.append(mutant)
    return result


def _is_type_checking(test: ast.AST) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _first_body_line(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    body = func.body
    if len(body) > 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return body[1].lineno  # past the docstring
    return body[0].lineno


def apply(source: str, mutant: Mutant) -> str:
    """The source with one mutant applied."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for text in lines:
        starts.append(starts[-1] + len(text))
    begin = starts[mutant.line - 1] + mutant.col
    end = starts[mutant.end_line - 1] + mutant.end_col
    return source[:begin] + mutant.new + source[end:]


# --- configuration ------------------------------------------------------------------------------
def load_config(text: str) -> dict:
    """``.github/mutation.toml``: targets, exclusions with reasons, and the equivalent list."""
    data = tomllib.loads(text)
    targets = data.get("targets", {})
    paths = targets.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("[targets] needs a non-empty `paths` list")
    exclude = targets.get("exclude", {})
    bad = sorted(path for path, why in exclude.items() if not str(why).strip())
    if bad:
        raise ValueError(f"every exclusion needs a reason: {bad}")
    equivalents = data.get("equivalent", [])
    for entry in equivalents:
        missing = {"path", "function", "line", "change", "reason"} - set(entry)
        if missing or not str(entry.get("reason", "")).strip():
            raise ValueError(
                f"an equivalent entry needs path, function, line, change and a reason: {entry}"
            )
    return {"paths": paths, "exclude": dict(exclude), "equivalent": equivalents}


def equivalent_key(entry: Mapping) -> tuple[str, str, str, str, int]:
    return (
        entry["path"],
        entry["function"],
        entry["line"].strip(),
        entry["change"],
        int(entry.get("occurrence", 0)),
    )


def target_files(root: Path, config: Mapping) -> list[str]:
    files: set[str] = set()
    for base in config["paths"]:
        files |= {p.relative_to(root).as_posix() for p in (root / base).rglob("*.py")}
    return sorted(files - set(config["exclude"]))


# --- running (steps 1 and 3) --------------------------------------------------------------------
def run_contexts(
    work: Path, extra: Iterable[str] = ()
) -> tuple[dict, dict[str, dict[int, set]], int]:
    """Run the default tier once under coverage: test results, line -> contexts, exit status."""
    from coverage import CoverageData

    data_file, tests_json = work / ".coverage", work / "tests.json"
    env = dict(
        os.environ,
        **THREADS,
        COVERAGE_FILE=str(data_file),
        **{TESTS_ENV: str(tests_json)},
        PYTHONPATH=os.pathsep.join([str(REPO / "scripts"), str(REPO / "src")]),
    )
    cmd = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "-m",
        "pytest",
        "-q",
        "-p",
        "run_mutation",
        "-p",
        "no:cacheprovider",
        "-m",
        MARKERS,
        *extra,
    ]
    status = subprocess.run(cmd, cwd=REPO, env=env, check=False).returncode
    if not tests_json.is_file() or not data_file.is_file():
        return {}, {}, status
    tests = json.loads(tests_json.read_text(encoding="utf-8"))
    cov = CoverageData(basename=str(data_file))
    cov.read()
    lines: dict[str, dict[int, set]] = {}
    for measured in cov.measured_files():
        rel = Path(measured).resolve()
        try:
            key = rel.relative_to(REPO).as_posix()
        except ValueError:
            continue
        by_line = cov.contexts_by_lineno(measured)
        lines[key] = {ln: {c for c in ctx if c} for ln, ctx in by_line.items()}
    return tests, lines, status


def module_name(path: str) -> str:
    """``src/tether/a/b.py`` -> ``tether.a.b``; a package's ``__init__.py`` is the package."""
    parts = list(Path(path).with_suffix("").parts)
    if parts[:1] == ["src"]:
        parts = parts[1:]
    if parts[-1:] == ["__init__"]:
        parts = parts[:-1]
    return ".".join(parts)


def _module_file(src: Path, name: str) -> Path | None:
    base = src.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _absolute(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    """The absolute module an ``ImportFrom`` in *module* names (relative ones resolved)."""
    if not node.level:
        return node.module or ""
    parts = module.split(".") if is_package else module.split(".")[:-1]
    parts = parts[: len(parts) - (node.level - 1)]
    return ".".join([*parts, *([node.module] if node.module else [])])


def _module_scope(tree: ast.Module) -> Iterable[ast.AST]:
    """The nodes that bind module names: ``if``/``try``/``with`` blocks are entered, a ``def``
    or ``class`` body is not, so a local import never shadows what the module passes on."""
    todo: list[ast.AST] = list(tree.body)
    while todo:
        node = todo.pop(0)
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = (ast.stmt, ast.excepthandler)
            todo[:0] = [c for c in ast.iter_child_nodes(node) if isinstance(c, inner)]


class _Imports:
    """Which modules of ``src/`` a test file imports, re-exports followed.

    ``import tether.a.b`` and ``from tether.a.b import x`` import ``tether.a.b``;
    ``from tether.a import b`` imports ``tether.a`` and, when ``b`` is a submodule, ``tether.a.b``.
    When ``tether.a`` only re-exports ``b`` (``from tether.a.c import b`` in its ``__init__``),
    the test reads a value that ``tether.a.c`` made, so it imports ``tether.a.c`` too, and so on
    down the chain of re-exports (every branch of a conditional one). Every module found also
    imports its parent packages (``tether`` and ``tether.a`` for ``tether.a.b``), as Python
    runs their ``__init__`` first."""

    def __init__(self, src: Path) -> None:
        self.src = src
        self._reexports: dict[str, dict[str, list[tuple[str, str]]]] = {}

    def reexports(self, module: str) -> dict[str, list[tuple[str, str]]]:
        """name -> every (module it may come from, its name there), for the names *module*
        imports; an ``if``/``else`` or ``try``/``except`` pair gives a name two origins."""
        if module not in self._reexports:
            self._reexports[module] = {}
            path = _module_file(self.src, module)
            if path is not None:
                is_package = path.name == "__init__.py"
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError):
                    tree = ast.Module(body=[], type_ignores=[])
                for node in _module_scope(tree):
                    if isinstance(node, ast.ImportFrom):
                        origin = _absolute(module, is_package, node)
                        for alias in node.names:
                            if alias.name != "*":
                                self._reexports[module].setdefault(
                                    alias.asname or alias.name, []
                                ).append((origin, alias.name))
        return self._reexports[module]

    def _from(self, module: str, name: str, seen: set[tuple[str, str]]) -> set[str]:
        """The modules that ``from module import name`` loads and that made the value."""
        if (module, name) in seen:
            return set()
        seen.add((module, name))
        found = {module}
        if _module_file(self.src, f"{module}.{name}") is not None:
            found.add(f"{module}.{name}")
        for origin, original in self.reexports(module).get(name, ()):
            found |= self._from(origin, original, seen)
        return found

    def of(self, source: str) -> set[str]:
        """Every module a test file's source imports, at any depth of the file."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                for alias in node.names:
                    star = alias.name == "*"
                    found |= {node.module} if star else self._from(node.module, alias.name, set())
        parents = {name.rsplit(".", i)[0] for name in found for i in range(1, name.count(".") + 1)}
        return found | parents


def imports_of_tests(root: Path, test_files: Iterable[str]) -> dict[str, set[str]]:
    """test file (repository-relative) -> the modules of ``src/`` it imports (see ``_Imports``)."""
    imports = _Imports(root / "src")
    out: dict[str, set[str]] = {}
    for test_file in sorted(set(test_files)):
        path = root / test_file
        if path.is_file():
            out[test_file] = imports.of(path.read_text(encoding="utf-8"))
    return out


def covering_tests(
    mutant: Mutant,
    lines: Mapping[str, Mapping[int, set]],
    passed: set,
    fixtures: Mapping[str, Iterable[str]] | None = None,
    imports: Mapping[str, set[str]] | None = None,
) -> list:
    """The passing tests that ran the mutant's lines, and every test that used a shared fixture
    whose setup ran them (*fixtures*: node id -> the fixture names the test asked for).

    A mutant at module or class level runs on import, before any test context, so it also runs
    every passing test of each test file that imports its module (*imports*: test file -> the
    modules it imports, from ``imports_of_tests``): such a test may only read a value the import
    made and run no line of the file."""
    by_line = lines.get(mutant.path, {})
    wanted = mutant.test_lines or list(by_line)
    contexts = {c for ln in wanted for c in by_line.get(ln, ())}
    found = contexts & passed
    if not mutant.test_lines and imports:
        name = module_name(mutant.path)
        found |= {t for t in passed if name in imports.get(t.split("::", 1)[0], ())}
    for context in contexts:
        if not context.startswith(FIXTURE_CONTEXT):
            continue
        scope, _, name = context[len(FIXTURE_CONTEXT) :].rpartition("|")
        found |= {
            test
            for test in passed
            if name in (fixtures or {}).get(test, ())
            and (not scope or test.startswith((scope + "::", scope + "/")))
        }
    return sorted(found)


def run_one(
    mutant: Mutant,
    source: str,
    worker: Path,
    tests: list[str],
    limit: float,
    exclude: Iterable[str] = (),
) -> str:
    """Run *tests* against *mutant* in one worker's copy of ``src/``; return the outcome.

    *exclude* holds the tests that did not pass in the coverage run; the file fallback deselects
    them, by function when parametrized, so a test that fails without the mutant never counts as
    a kill, even when the mutant renamed it."""
    target = worker / "src" / Path(mutant.path).relative_to("src")
    target.write_text(apply(source, mutant), encoding="utf-8")
    # No bytecode: a mutant of the same size written in the same second as the last one would
    # otherwise load the last one's cached .pyc.
    env = dict(os.environ, **THREADS, PYTHONPATH=str(worker / "src"), PYTHONDONTWRITEBYTECODE="1")
    base = [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"]
    base += [f"--basetemp={worker / 'tmp'}", "-m", MARKERS]
    # A mutant can rename a test: a module constant in a ``parametrize`` id (``[1e-06]``). Then
    # a test id is not found (exit 4) or nothing is collected (exit 5), and the files run,
    # less the tests of those files that did not pass in the coverage run. The mutant can rename
    # those too, and ``--deselect`` matches a prefix of the id it collects, so a parametrized one
    # is deselected by its function (``test_x[``), every parameter set of it: a mutant never
    # changes a function or class name, only the ``[...]`` part.
    files = sorted({test.split("::", 1)[0] for test in tests})
    stems = {"".join(t.partition("[")[:2]) for t in exclude if t.split("::", 1)[0] in files}
    skip = [f"--deselect={stem}" for stem in sorted(stems)]
    try:
        for selection in (tests, [*skip, *files]):
            done = subprocess.run(
                [*base, *selection], cwd=REPO, env=env, capture_output=True, timeout=limit
            )
            if done.returncode not in (4, 5):
                break
    except subprocess.TimeoutExpired:
        return "timeout"
    finally:
        target.write_text(source, encoding="utf-8")
    return {0: "survived", 1: "killed", 2: "killed", 3: "killed"}.get(done.returncode, "error")


# --- report and compare (steps 4 and 5) ---------------------------------------------------------
KILLED = {"killed", "timeout"}


def summarize(records: list[dict]) -> dict[str, dict]:
    modules: dict[str, dict] = {}
    for rec in records:
        row = modules.setdefault(
            rec["path"],
            {
                "mutants": 0,
                "killed": 0,
                "timeout": 0,
                "survived": 0,
                "uncovered": 0,
                "equivalent": 0,
                "error": 0,
                "not_run": 0,
            },
        )
        row["mutants"] += 1
        row[rec["status"]] += 1
    for row in modules.values():
        found = row["killed"] + row["timeout"]
        scored = found + row["survived"]
        row["score"] = round(100.0 * found / scored, 1) if scored else None
        row["complete"] = row["not_run"] == 0 and row["error"] == 0
    return modules


def drops(previous: Mapping, current: Mapping, points: float = DROP_POINTS) -> list[dict]:
    """Modules whose score fell by more than *points*; incomplete modules never count."""
    out = []
    for path, now in sorted(current.get("modules", {}).items()):
        before = previous.get("modules", {}).get(path)
        if not before or not before.get("complete") or not now.get("complete"):
            continue
        if before.get("score") is None or now.get("score") is None:
            continue
        if before["score"] - now["score"] > points:
            out.append({"path": path, "before": before["score"], "now": now["score"]})
    return out


def render_markdown(report: Mapping) -> str:
    out = [
        f"# Mutation report ({report.get('commit', '?')[:12]})",
        "",
        "Score = killed / (killed + survived). Uncovered, equivalent, error and not-run "
        "mutants are not counted. A timeout counts as killed.",
        "",
        "| Module | Score | Mutants | Killed | Timeout | Survived | Uncovered | Equivalent "
        "| Error | Not run |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for path, row in sorted(report["modules"].items()):
        score = "-" if row["score"] is None else f"{row['score']:.1f}"
        score += "" if row["complete"] else " (incomplete)"
        out.append(
            f"| `{path}` | {score} | {row['mutants']} | {row['killed']} | "
            f"{row['timeout']} | {row['survived']} | {row['uncovered']} | "
            f"{row['equivalent']} | {row['error']} | {row['not_run']} |"
        )
    for title, items in (("Score drops", report.get("drops", [])),):
        if items:
            out += ["", f"## {title}", ""]
            out += [f"- `{d['path']}`: {d['before']} -> {d['now']}" for d in items]
    out += ["", f"## Surviving mutants ({len(report['survivors'])})", ""]
    for rec in report["survivors"]:
        tests = ", ".join(f"`{t}`" for t in rec["tests"][:5])
        more = f" and {len(rec['tests']) - 5} more" if len(rec["tests"]) > 5 else ""
        out.append(
            f"- `{rec['path']}:{rec['line']}` in `{rec['function']}`: `{rec['change']}` "
            f"on `{rec['line_text']}`; covered by {tests}{more}"
        )
    out += ["", f"## Known equivalent mutants ({len(report['equivalent'])})", ""]
    out += [
        f"- `{r['path']}:{r['line']}` `{r['change']}`: {r['reason']}" for r in report["equivalent"]
    ]
    if report.get("stale_equivalents"):
        out += ["", "## Equivalent entries that match no mutant", ""]
        out += [
            f"- `{e['path']}` `{e['function']}` `{e['change']}` on `{e['line']}`"
            for e in report["stale_equivalents"]
        ]
    return "\n".join(out) + "\n"


def build_report(records: list[dict], config: Mapping, commit: str) -> dict:
    used = {tuple(r["key"]) for r in records if r["status"] == "equivalent"}
    stale = [e for e in config["equivalent"] if equivalent_key(e) not in used]
    return {
        "version": 1,
        "commit": commit,
        "drop_points": DROP_POINTS,
        "modules": summarize(records),
        "survivors": [r for r in records if r["status"] == "survived"],
        "equivalent": [r for r in records if r["status"] == "equivalent"],
        "stale_equivalents": stale,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("mutation-report"))
    parser.add_argument("--previous", type=Path, help="the last report's JSON, if any")
    parser.add_argument("--budget-minutes", type=float, default=300.0)
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="mutants run at once, each in its own copy of src/",
    )
    parser.add_argument("--only", nargs="*", default=[], help="limit to paths starting with these")
    args = parser.parse_args(argv)
    start = time.monotonic()
    config = load_config(CONFIG.read_text(encoding="utf-8"))
    files = [f for f in target_files(REPO, config) if f.startswith(tuple(args.only or [""]))]
    equivalents = {equivalent_key(e): e for e in config["equivalent"]}
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tether-mutation-") as tmp:
        work = Path(tmp)
        tests, lines, status = run_contexts(work)
        passed = {t for t, r in tests.items() if r["outcome"] == "passed"}
        # Exit 1 is a failed test, which is left out below. Anything else (a collection error,
        # an internal error) or no passing test at all would make every mutant "uncovered" and
        # the report a false base for the next comparison, so the run fails with no report.
        if status not in (0, 1) or not passed:
            print(
                f"::error::the coverage run of the tests ended with exit {status} and "
                f"{len(passed)} passing tests; no report is written",
                flush=True,
            )
            return 2
        failed = sorted(t for t, r in tests.items() if r["outcome"] == "failed")
        if failed:
            print(
                f"::warning::{len(failed)} tests failed in the coverage run and run no mutant: "
                + ", ".join(failed[:10]),
                flush=True,
            )
        not_passed = sorted(set(tests) - passed)
        fixtures = {t: r.get("fixtures", []) for t, r in tests.items()}
        imports = imports_of_tests(REPO, (t.split("::", 1)[0] for t in passed))
        records: list[dict] = []
        todo: list[tuple[dict, Mutant, str, list[str]]] = []
        for path in files:
            source = (REPO / path).read_text(encoding="utf-8")
            for mutant in mutants_of(source, path):
                selected = covering_tests(mutant, lines, passed, fixtures, imports)
                rec = asdict(mutant) | {
                    "change": mutant.change,
                    "key": list(mutant.key),
                    "tests": selected,
                }
                records.append(rec)
                if mutant.key in equivalents:
                    rec["status"] = "equivalent"
                    rec["reason"] = equivalents[mutant.key]["reason"]
                elif not selected:
                    rec["status"] = "uncovered"
                else:
                    todo.append((rec, mutant, source, selected))
        workers: queue.Queue[Path] = queue.Queue()
        for i in range(max(1, args.jobs)):
            shutil.copytree(
                REPO / "src", work / f"w{i}" / "src", ignore=shutil.ignore_patterns("__pycache__")
            )
            workers.put(work / f"w{i}")

        def one(item: tuple[dict, Mutant, str, list[str]]) -> None:
            rec, mutant, source, selected = item
            if time.monotonic() - start > 60 * args.budget_minutes:
                rec["status"] = "not_run"
                return
            worker = workers.get()
            try:
                took = sum(tests[t]["duration"] for t in selected)
                limit = TIME_FLOOR + TIME_FACTOR * took
                rec["status"] = run_one(mutant, source, worker, selected, limit, not_passed)
            finally:
                workers.put(worker)
            print(f"{rec['status']:>10} {mutant.path}:{mutant.line} {mutant.change}", flush=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            list(pool.map(one, todo))
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.strip()
    report = build_report(records, config, commit)
    if args.previous and args.previous.is_file():
        report["drops"] = drops(json.loads(args.previous.read_text(encoding="utf-8")), report)
    else:
        report["drops"] = []
    (args.out / "mutation-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    markdown = render_markdown(report)
    (args.out / "mutation-report.md").write_text(markdown, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(markdown[:900_000])
    for drop in report["drops"]:
        print(
            f"::error::mutation score of {drop['path']} fell from {drop['before']} to "
            f"{drop['now']} (more than {DROP_POINTS} points)"
        )
    return 1 if report["drops"] else 0


if __name__ == "__main__":
    sys.exit(main())
