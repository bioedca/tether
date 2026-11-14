# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The docstring standard and its two scripts (issue #611, ADR-0093).

* No Sphinx cross-reference role is left in ``src/tether``, and the migration script has nothing
  left to rewrite.
* ``scripts/migrate_sphinx_roles.py`` writes the cross-reference forms that
  ``docs/maintainers/docstring-style.md`` names, and never changes executable code.
* The docstring allowlist in ``pyproject.toml`` can only shrink. ``test_allowlist_never_grows``
  compares it with the copy on ``main``. The stale-entry half needs ruff, so the CI ``lint`` job
  runs ``scripts/check_docstring_allowlist.py`` for it; here its logic is tested on its own.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
BASE_ENV = "TETHER_DOCSTRING_BASE_REF"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their annotations through sys.modules
    spec.loader.exec_module(module)
    return module


migrate = _load("migrate_sphinx_roles")
allow = _load("check_docstring_allowlist")


# --------------------------------------------------------------------------- no role is left


def test_no_sphinx_role_is_left_in_the_package():
    """Acceptance criterion 3: ``grep`` finds no Sphinx cross-reference role in ``src/tether``."""
    role = re.compile(r":(?:py:)?(?:func|class|mod|attr|meth|data|exc|obj|const):`")
    hits = [
        f"{path.relative_to(_REPO).as_posix()}:{number}"
        for path in sorted((_REPO / "src" / "tether").rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if role.search(line)
    ]
    assert hits == []


def test_the_migration_has_nothing_left_to_rewrite(capsys):
    assert migrate.main(["--check"]) == 0
    assert capsys.readouterr().out.startswith("0 files, 0 links")


# --------------------------------------------------------------------------- the migration


def _index(tmp_path: Path, files: dict[str, str]):
    paths = []
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    index = migrate.Index()
    for path in paths:
        parts = list(path.relative_to(tmp_path).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        name = ".".join(parts[:-1] if is_package else parts)
        index.add_module(name, migrate.ast.parse(path.read_text(encoding="utf-8")), is_package)
    return index


_MOVIE = '''"""Movies."""


class Movie:
    """A movie."""

    def frame(self, index):
        """Return one frame."""

    def frames(self):
        """Return every frame; see :meth:`frame`."""
'''


def _rewrite(index, module: str, source: str) -> tuple[str, object]:
    report = migrate.Report()
    return migrate.rewrite_source(source, index, module, "x.py", report), report


def test_a_name_in_scope_gets_the_short_form_and_a_tilde_the_full_path(tmp_path):
    source = (
        '"""Read.\n\nUse :class:`Movie` or :class:`~tether.movie.Movie`, '
        'not :func:`len`."""\n\nfrom tether.movie import Movie\n'
    )
    index = _index(
        tmp_path,
        {
            "tether/__init__.py": '"""Pkg."""\n',
            "tether/movie.py": _MOVIE,
            "tether/reader.py": source,
        },
    )
    out, report = _rewrite(index, "tether.reader", source)
    assert "Use [`Movie`][] or [`Movie`][tether.movie.Movie], not [`len`][]." in out
    assert report.unresolved == []


def test_a_member_resolves_inside_its_class(tmp_path):
    index = _index(tmp_path, {"tether/__init__.py": '"""Pkg."""\n', "tether/movie.py": _MOVIE})
    out, _ = _rewrite(index, "tether.movie", _MOVIE)
    assert "see [`frame`][]." in out


def test_a_comment_role_becomes_a_code_span_and_an_unknown_target_too(tmp_path):
    index = _index(tmp_path, {"tether/__init__.py": '"""Pkg."""\n'})
    source = '"""See :func:`nowhere`."""\n\nX = 1  # like :func:`tether.thing`\n'
    index.add_module("tether.m", migrate.ast.parse(source), False)
    out, report = _rewrite(index, "tether.m", source)
    assert out == '"""See `nowhere`."""\n\nX = 1  # like `tether.thing`\n'
    assert report.unresolved == ["x.py:1: :func:`nowhere`"]


def test_a_private_target_is_a_code_span(tmp_path):
    source = '"""Use :func:`_helper`."""\n\n\ndef _helper():\n    """Help."""\n'
    index = _index(tmp_path, {"tether/__init__.py": '"""Pkg."""\n', "tether/m.py": source})
    out, _ = _rewrite(index, "tether.m", source)
    assert out.startswith('"""Use `_helper`."""')


def test_a_role_outside_a_docstring_or_comment_stops_the_script(tmp_path):
    index = _index(tmp_path, {"tether/__init__.py": '"""Pkg."""\n'})
    source = '"""Doc."""\n\nTEXT = "see :func:`len`"\n'
    index.add_module("tether.m", migrate.ast.parse(source), False)
    with pytest.raises(SystemExit, match="outside a docstring"):
        _rewrite(index, "tether.m", source)


def test_a_long_line_breaks_outside_links_and_a_summary_stays_one_line(tmp_path):
    long_name = "a_function_whose_long_name_pushes_the_line_over"
    module = (
        f'"""Summary that names :func:`~tether.m.{long_name}`."""\n\n\n'
        f"def {long_name}():\n"
        f'    """Do it.\n\n    The text before the link is long, and then :func:`{long_name}` '
        'ends it.\n    """\n'
    )
    index = _index(tmp_path, {"tether/__init__.py": '"""Pkg."""\n', "tether/m.py": module})
    out, report = _rewrite(index, "tether.m", module)
    assert all(len(line) <= migrate.LINE_LENGTH for line in out.splitlines())
    assert out.splitlines()[0] == f'"""Summary that names `{long_name}`."""'
    assert report.summary == 1
    assert f"[`{long_name}`][]" in out


# --------------------------------------------------------------------------- the allowlist

_BASE = """
[tool.ruff.lint.per-file-ignores]
"__init__.py" = ["F401"]
"!src/tether/**" = ["D"]
"src/tether/a.py" = ["D401", "D205"]
"""


def test_the_allowlist_is_the_package_entries_with_d_rules_only():
    assert allow.allowlist(_BASE) == {("src/tether/a.py", "D401"), ("src/tether/a.py", "D205")}


def test_an_added_entry_is_named_and_a_removed_one_is_not():
    grown = _BASE + '"src/tether/b.py" = ["D102"]\n'
    shrunk = _BASE.replace('"D401", ', "")
    assert allow.added_entries(_BASE, grown) == [("src/tether/b.py", "D102")]
    assert allow.added_entries(_BASE, shrunk) == []


def test_a_stale_entry_is_named():
    allowed = {("src/tether/a.py", "D401"), ("src/tether/a.py", "D205")}
    assert allow.stale_entries(allowed, {("src/tether/a.py", "D401")}) == [
        ("src/tether/a.py", "D205")
    ]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(_REPO), *args], capture_output=True, text=True)


def test_allowlist_never_grows():
    """Acceptance criterion 6: an entry that ``main`` does not have fails here."""
    ref = os.environ.get(BASE_ENV) or "origin/main"
    if _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        if os.environ.get(BASE_ENV):
            pytest.fail(f"{BASE_ENV}={ref} names no commit; the CI step that fetches main failed")
        pytest.skip(f"no {ref} in this checkout (CI's Linux leg fetches it and sets {BASE_ENV})")
    shown = _git("show", f"{ref}:pyproject.toml")
    if shown.returncode != 0:
        pytest.fail(f"could not read pyproject.toml at {ref}: {shown.stderr}")
    head = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    if not allow.allowlist(shown.stdout) and "pydocstyle" not in shown.stdout:
        return  # main has no docstring check yet (the pull request that adds it)
    assert allow.added_entries(shown.stdout, head) == []


def test_an_attribute_assignment_does_not_hide_an_import(tmp_path):
    source = (
        '"""Use :class:`np.ndarray`."""\n\nimport numpy as np\n\nnp.some_setting = 1\n'
        "first, *rest = (1, 2)\n"
    )
    index = _index(tmp_path, {"tether/__init__.py": '"""Pkg."""\n', "tether/m.py": source})
    assert index.modules["tether.m"].names["np"] == ("import", "numpy")
    assert {"first", "rest"} <= set(index.modules["tether.m"].names)
    out, report = _rewrite(index, "tether.m", source)
    assert out.startswith('"""Use [`np.ndarray`][]."""')
