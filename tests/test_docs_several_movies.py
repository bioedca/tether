# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The user pages say what a project of several movies is, and how to make one (#601).

A project can take a second movie since #347 (ADR-0117). The how-to guides were written for the
state before that change, so they told the reader that a project holds one movie and asked for
each movie file in the project once. These tests pin the rewrite:

* no how-to guide says a project holds one movie, or asks for each movie file once (Tether
  refuses a repeated movie itself, so the requirement is gone);
* ``docs/how-to/extract-traces.md`` has the procedure that adds a second movie, with a script
  that calls the append and its expected output;
* the expected output, the refusal and the lock error the procedure quotes are what Tether
  prints, checked by running the procedure on two small movies;
* every ``tether.…`` name, every ``tether <command>`` and every ``name`` (``src/…``) pair the
  changed pages write in prose exists. The fenced code is already checked by
  ``tests/test_docs_examples.py`` and ``tests/test_docs_how_to.py``.

The name checks read source with :mod:`ast` and import nothing under ``tether`` except
:mod:`tether.cli`; only the behavioral test needs the extraction stack.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _docs_source import split_front_matter
from tether.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
HOW_TO = DOCS / "how-to"
SRC = ROOT / "src"
TUTORIAL_MOVIE = DOCS / "tutorial" / "data" / "tutorial_movie.tif"

#: The pages #601 changed. The prose checks run on these.
CHANGED = (
    "how-to/extract-traces.md",
    "how-to/curate-traces.md",
    "how-to/apply-corrections.md",
    "how-to/idealize.md",
    "how-to/analyze.md",
    "how-to/export-results.md",
    "glossary.md",
    "reference/tether-format.md",
)

_FENCE_RE = re.compile(
    r"^(?P<fence>`{3,})(?P<lang>[\w+-]*)[ ]*\n(?P<code>.*?)^(?P=fence)[ ]*$", re.M | re.S
)
_SECTION = "## Add another movie to a project"
_ONE_MOVIE_RE = re.compile(r"\bholds?\s+(?:only\s+)?one\s+movie\b", re.I)
_EACH_FILE_ONCE_RE = re.compile(r"each\s+movie\s+file\s+in\s+the\s+project\s+once", re.I)
_ADDED_RE = re.compile(
    r"^Added \d+ molecule\(s\) as mov-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
)
_DOTTED_RE = re.compile(r"`(tether(?:\.[A-Za-z_]\w*)+)`")
_COMMAND_RE = re.compile(r"`tether ([a-z][a-z-]*)\b[^`]*`")
_PAIR_RE = re.compile(r"`([A-Za-z_]\w*)` \(`(src/tether/[\w/]+\.py)`\)")


def _text(rel: str) -> str:
    return split_front_matter((DOCS / rel).read_text(encoding="utf-8"))[1]


def _prose(rel: str) -> str:
    """The page without its fenced code, which other modules check."""
    return _FENCE_RE.sub("", _text(rel))


def _section(rel: str, heading: str) -> str:
    text = _text(rel)
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end >= 0 else len(text)]


def _fences(text: str, lang: str) -> list[str]:
    return [m.group("code") for m in _FENCE_RE.finditer(text) if m.group("lang") == lang]


# --- What the guides say -------------------------------------------------------------------


@pytest.mark.parametrize("page", sorted(p.name for p in HOW_TO.glob("*.md")))
def test_no_guide_says_a_project_holds_one_movie(page: str) -> None:
    prose = " ".join(_prose(f"how-to/{page}").split())
    assert not _ONE_MOVIE_RE.search(prose), _ONE_MOVIE_RE.search(prose)
    assert not _EACH_FILE_ONCE_RE.search(prose)


def test_extract_traces_has_the_procedure_that_adds_a_movie() -> None:
    section = _section("how-to/extract-traces.md", _SECTION)
    (script,) = _fences(section, "python")
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(ast.parse(script))
        if isinstance(node, ast.Call)
    }
    assert {"add_movie_to_project", "publish_lock"} <= calls
    assert any(cmd.split()[:2] == ["tether", "info"] for cmd in _fences(section, "bash"))
    printed, info = _fences(section, "text")
    assert _ADDED_RE.match(printed.strip()), printed
    assert "movies: 2" in info
    assert "`movie_id`" in section


def test_the_batch_section_links_to_the_procedure() -> None:
    section = _section("how-to/extract-traces.md", "## Many movies at once")
    assert "(#add-another-movie-to-a-project)" in section


# --- What Tether prints --------------------------------------------------------------------


def test_the_procedure_prints_what_the_page_shows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run the page's script on two small movies, then ``tether info``, then the refusals."""
    np = pytest.importorskip("numpy")
    tifffile = pytest.importorskip("tifffile")
    pytest.importorskip("h5py")
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    from tether.cli import main  # noqa: PLC0415
    from tether.project.extract import (  # noqa: PLC0415
        ExtractionError,
        add_movie_to_project,
        extract_movie,
    )
    from tether.project.lock import LockedError, publish_lock  # noqa: PLC0415

    first = tmp_path / "movie.tif"
    first.write_bytes(TUTORIAL_MOVIE.read_bytes())
    stack = tifffile.imread(first)
    second = tmp_path / "movie_2.tif"
    tifffile.imwrite(second, np.ascontiguousarray(stack[::-1]), byteorder=">")
    project = tmp_path / "movie.tether"
    extract_movie(first, project)

    section = _section("how-to/extract-traces.md", _SECTION)
    (script,) = _fences(section, "python")
    _, info = _fences(section, "text")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    line = result.stdout.strip()
    assert _ADDED_RE.match(line), line

    capsys.readouterr()
    assert main(["info", str(project)]) == 0
    out = capsys.readouterr().out
    assert "  movies: 2\n" in out
    shape = re.compile(
        r"^    \S+: \d+ frames, (?:\S+ s/frame|frame time not recorded), \d+ molecules$"
    )
    for doc_line in info.splitlines():
        if doc_line.startswith("    ") and not doc_line.startswith("      "):
            assert shape.match(doc_line), doc_line
    assert sum(1 for out_line in out.splitlines() if shape.match(out_line)) == 2

    with (
        pytest.raises(ExtractionError, match="a movie is appended once"),
        publish_lock(project) as guard,
    ):
        add_movie_to_project(project, second, publish_guard=guard)
    assert "`a movie is appended once`" in section

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from tether.project.lock import publish_lock\n"
            f"with publish_lock({str(project)!r}):\n"
            "    print('held', flush=True)\n"
            "    sys.stdin.readline()\n",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        with pytest.raises(LockedError, match="is locked"), publish_lock(project):
            pass
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=30)
    assert "`is locked`" in section


# --- Every name the changed prose writes exists --------------------------------------------


def _module_path(dotted: str) -> Path | None:
    path = SRC / Path(*dotted.split("."))
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    return None


def _bound(body: list[ast.stmt], name: str) -> ast.AST | None:
    """The top-level statement of ``body`` that binds ``name``, if there is one."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return node
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                return node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((a.asname or a.name.split(".")[0]) == name for a in node.names):
                return node
        elif isinstance(node, (ast.If, ast.Try)):
            inner = [*node.body, *getattr(node, "orelse", []), *getattr(node, "finalbody", [])]
            found = _bound(inner, name)
            if found is not None:
                return found
    return None


def _resolves(dotted: str) -> bool:
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        path = _module_path(".".join(parts[:cut]))
        if path is None:
            continue
        body: list[ast.stmt] = ast.parse(path.read_text(encoding="utf-8")).body
        for name in parts[cut:]:
            node = _bound(body, name)
            if node is None:
                return False
            body = node.body if isinstance(node, ast.ClassDef) else []
        return True
    return False


def _names(pattern: re.Pattern[str]) -> list[tuple[str, tuple[str, ...]]]:
    found = []
    for rel in CHANGED:
        prose = " ".join(_prose(rel).split())
        found += [(rel, m.groups()) for m in pattern.finditer(prose)]
    return found


def test_the_changed_pages_name_tether_objects() -> None:
    names = {groups[0] for _, groups in _names(_DOTTED_RE)}
    assert "tether.project.extract" in names
    assert _names(_PAIR_RE)


@pytest.mark.parametrize(("rel", "groups"), _names(_DOTTED_RE))
def test_every_dotted_name_resolves(rel: str, groups: tuple[str, ...]) -> None:
    assert _resolves(groups[0]), f"{rel}: `{groups[0]}` names nothing under src/"


@pytest.mark.parametrize(("rel", "groups"), _names(_PAIR_RE))
def test_every_name_beside_a_source_file_is_in_it(rel: str, groups: tuple[str, ...]) -> None:
    name, source = groups
    text = (ROOT / source).read_text(encoding="utf-8")
    assert re.search(rf"\b{re.escape(name)}\b", text), f"{rel}: `{name}` is not in {source}"


def test_every_tether_command_in_prose_exists() -> None:
    parser = build_parser()
    (sub,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    commands = set(sub.choices)
    named = {groups[0] for _, groups in _names(_COMMAND_RE)}
    assert {"extract", "batch"} <= named
    assert named <= commands, named - commands
