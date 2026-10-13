# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The six task guides under ``docs/how-to/`` keep the shape and the commands #177 set.

The how-to layer is one page per user goal, so a reader who knows what they want lands on
instructions rather than reference. These tests pin what makes those pages usable and true:

* ``docs/how-to/`` holds exactly the six pages, each in the nav under ``Using Tether``;
* each opens with a task H1, then ``## Who this page is for``, then ``## Before you start``;
* none carries a Markdown table, so no flag enumeration creeps in (reference pages own those);
* every ``tether …`` command in a shell fence parses with the real argparse parser, and no
  page invents a subcommand;
* every call a Python fence makes to an imported ``tether`` function or class method passes
  only arguments that the source signature accepts, and every argument it requires.

Imports are already checked by ``tests/test_docs_examples.py``; this module checks the calls.
Nothing under ``tether`` is imported except :mod:`tether.cli`, whose ``build_parser`` needs only
``argparse``. Signatures are read from source with :mod:`ast`, so the default matrix runs this
without SciPy, h5py or Matplotlib.
"""

from __future__ import annotations

import argparse
import ast
import html
import re
import shlex
from pathlib import Path

import pytest
import yaml

from tether.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
HOW_TO = DOCS / "how-to"
SRC = ROOT / "src"
PAGES = (
    "extract-traces.md",
    "curate-traces.md",
    "apply-corrections.md",
    "idealize.md",
    "analyze.md",
    "export-results.md",
)

# Python-Markdown's fenced_code matches fences at column zero only, so this is what renders.
_FENCE_RE = re.compile(
    r"^(?P<fence>`{3,})(?P<lang>[\w+-]*)[ ]*\n(?P<code>.*?)^(?P=fence)[ ]*$", re.M | re.S
)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_SHELL_LANGS = {"bash", "sh", "console", "powershell"}
_PYTHON_LANGS = {"python", "py", "pycon"}


def _text(name: str) -> str:
    return (HOW_TO / name).read_text(encoding="utf-8")


def _fences(name: str, langs: set[str]) -> list[str]:
    return [m.group("code") for m in _FENCE_RE.finditer(_text(name)) if m.group("lang") in langs]


def _nav_pages(entries: object) -> list[str]:
    if isinstance(entries, list):
        return [page for entry in entries for page in _nav_pages(entry)]
    if isinstance(entries, dict):
        return [page for value in entries.values() for page in _nav_pages(value)]
    return [entries] if isinstance(entries, str) else []


def test_the_folder_holds_exactly_the_six_pages() -> None:
    assert sorted(p.name for p in HOW_TO.iterdir()) == sorted(PAGES)


def test_every_page_is_in_the_nav_under_using_tether() -> None:
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    groups = {}
    for entry in config["nav"]:
        ((title, children),) = entry.items()
        groups[html.unescape(title)] = _nav_pages(children)
    for name in PAGES:
        assert f"how-to/{name}" in groups["Using Tether"], name


@pytest.mark.parametrize("name", PAGES)
def test_page_opens_with_task_title_reader_and_prerequisites(name: str) -> None:
    lines = _text(name).splitlines()
    assert lines[0].startswith("# "), name
    headings = [line for line in lines if line.startswith("## ")]
    assert headings[:2] == ["## Who this page is for", "## Before you start"], name


@pytest.mark.parametrize("name", PAGES)
def test_page_has_no_table(name: str) -> None:
    assert not _TABLE_ROW_RE.search(_text(name)), name


def _tether_commands() -> list[tuple[str, list[str]]]:
    found = []
    for name in PAGES:
        for code in _fences(name, _SHELL_LANGS):
            for line in code.splitlines():
                if re.match(r"tether(-gui)?\s", line):
                    found.append((name, shlex.split(line)))
    return found


def test_the_pages_show_tether_commands() -> None:
    assert {argv[1] for _, argv in _tether_commands() if argv[0] == "tether"} == {
        "extract",
        "batch",
    }


@pytest.mark.parametrize(("name", "argv"), _tether_commands())
def test_every_tether_command_parses(name: str, argv: list[str]) -> None:
    if argv[0] == "tether-gui":
        assert len(argv) == 2 and argv[1].endswith(".tether"), (name, argv)
        return
    assert argv[1] in {"extract", "batch"}, (name, argv)
    parser = build_parser()
    (sub,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    options = set(sub.choices[argv[1]]._option_string_actions)
    # Exact spellings only: argparse would also accept an abbreviation a reader cannot grep.
    for token in argv[2:]:
        if token.startswith("-"):
            assert token.split("=", 1)[0] in options, (name, token)
    try:
        parser.parse_args(argv[1:])
    except SystemExit as exc:  # argparse exits on an unknown flag or a bad value
        pytest.fail(f"{name}: {shlex.join(argv)} does not parse (exit {exc.code})")


def _module_tree(dotted: str) -> ast.Module:
    path = SRC / Path(*dotted.split("."))
    path = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
    return ast.parse(path.read_text(encoding="utf-8"))


def _top_level(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
    return None


def _check_call(call: ast.Call, func: ast.FunctionDef, *, bound: bool) -> list[str]:
    args = func.args
    positional = [a.arg for a in args.posonlyargs + args.args][1 if bound else 0 :]
    keyword_ok = set(positional[len(args.posonlyargs) :]) | {a.arg for a in args.kwonlyargs}
    problems = []
    if args.vararg is None and len(call.args) > len(positional):
        problems.append(f"{len(call.args)} positional arguments, at most {len(positional)}")
    given = set(positional[: len(call.args)])
    for kw in call.keywords:
        if kw.arg is None:
            continue
        if kw.arg not in keyword_ok and args.kwarg is None:
            problems.append(f"no parameter {kw.arg!r}")
        given.add(kw.arg)
    n_pos_defaults = len(args.defaults)
    required = positional[: len(positional) - n_pos_defaults] + [
        a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None
    ]
    problems += [f"missing {r!r}" for r in required if r not in given]
    return problems


def _python_calls() -> list[tuple[str, str, ast.Call, ast.FunctionDef, bool]]:
    found = []
    for name in PAGES:
        for code in _fences(name, _PYTHON_LANGS):
            tree = ast.parse(re.sub(r"^(>>> |\.\.\. )", "", code, flags=re.M))
            imported = {
                alias.asname or alias.name: node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tether")
                for alias in node.names
            }
            for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
                target, bound, label = None, False, None
                if isinstance(call.func, ast.Name) and call.func.id in imported:
                    target = _top_level(_module_tree(imported[call.func.id]), call.func.id)
                    label = call.func.id
                elif (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in imported
                ):
                    owner = _top_level(
                        _module_tree(imported[call.func.value.id]), call.func.value.id
                    )
                    assert isinstance(owner, ast.ClassDef), (name, call.func.value.id)
                    target = _top_level(
                        ast.Module(body=owner.body, type_ignores=[]), call.func.attr
                    )
                    bound, label = True, f"{call.func.value.id}.{call.func.attr}"
                if label is None:
                    continue
                assert isinstance(target, ast.FunctionDef), (name, label)
                found.append((name, label, call, target, bound))
    return found


def test_the_pages_call_tether() -> None:
    assert {label for _, label, *_ in _python_calls()} >= {
        "compute_photobleach",
        "compute_leakage_alpha",
        "compute_gamma",
        "compute_corrected_fret",
        "idealize_molecules",
        "reidealize",
        "population_apparent_e_histogram",
        "population_transition_density",
        "population_dwell_times",
        "export_figure",
        "export_molecule_table_csv",
        "export_deeplasi_txt",
        "export_subset_tether",
        "Project.open",
    }


@pytest.mark.parametrize(
    ("name", "label", "call", "func", "bound"),
    _python_calls(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_tether_call_matches_its_signature(name, label, call, func, bound) -> None:
    assert _check_call(call, func, bound=bound) == [], (name, label)
