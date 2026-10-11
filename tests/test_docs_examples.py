# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every ``tether`` import printed in a docs code fence names a real module and object.

The docs show readers ``from tether.analysis.histogram import population_apparent_e_histogram``
and the like; nothing else verifies those lines, and the set grows with every new page (#183).
This module walks ``docs/**/*.md`` (minus ``docs/PRD.md``, which ``mkdocs.yml`` excludes from
the site), extracts every ``from tether…`` / ``import tether…`` statement from the Python
fences with :mod:`ast`, and checks that

* each module resolves to a source file or package in this checkout's ``src/tether``, and
* each imported name is defined at the top level of that module's source, or is one of its
  submodules.

**Nothing is ever imported** — not the module, and not its parent packages. Torch lives only
in the isolated ``deep/`` lock stack, and ``tether.ml.deep.model`` is importable without it
only because it imports torch lazily inside its functions; an import-based check would tie a
required 3-OS job to that discipline holding forever. :func:`importlib.util.find_spec` on a
dotted name imports every parent package to read its ``__path__``, so resolution instead walks
the package tree one level at a time with :meth:`importlib.machinery.PathFinder.find_spec`,
which only searches the given directories. Anchoring the walk at this checkout's ``src/``
also means the docs are checked against the source beside them, never against whatever
``tether`` another environment happens to have installed.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SRC = ROOT / "src"
_EXCLUDED = {DOCS / "PRD.md"}

_FENCE_OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^\s`{]*)")
_PYTHON_LANGS = {"python", "py", "python3", "py3", "pycon"}
# Any line that reads as a tether import, whatever fence it sits in: the coverage guard.
_TETHER_IMPORT_LINE_RE = re.compile(r"^\s*(?:>>> |\.\.\. )?(?:from|import)\s+tether\b")


def _fences(path: Path) -> list[tuple[str, int, list[str]]]:
    """``(language, first_body_line_number, body_lines)`` for each closed fence in ``path``."""
    lines = path.read_text(encoding="utf-8").split("\n")
    out = []
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN_RE.match(lines[i])
        if m:
            indent, fence, lang = m.groups()
            for j in range(i + 1, len(lines)):
                stripped = lines[j].strip()
                if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                    body = [
                        ln[len(indent) :] if ln.startswith(indent) else ln.lstrip()
                        for ln in lines[i + 1 : j]
                    ]
                    out.append((lang.lower(), i + 2, body))
                    i = j
                    break
        i += 1
    return out


def _statements(body: list[str]) -> list[tuple[int, ast.stmt]]:
    """Top-level statements of a fence body, with 0-based line offsets into ``body``.

    A whole-fence parse is tried first. A fence that is not valid Python as a whole (an
    elided ``...`` argument list, a ``>>>`` session) falls back to parsing each statement
    that starts on its own line, extending it line by line until it parses, so a
    parenthesised multi-line import is still read as one statement.
    """
    body = [re.sub(r"^(?:>>> |\.\.\. )", "", ln) for ln in body]
    try:
        tree = ast.parse("\n".join(body))
        return [(node.lineno - 1, node) for node in tree.body]
    except SyntaxError:
        pass
    out = []
    for start, line in enumerate(body):
        if not re.match(r"(?:from|import)\s", line):
            continue
        for end in range(start + 1, len(body) + 1):
            try:
                tree = ast.parse("\n".join(body[start:end]))
            except SyntaxError:
                continue
            out.extend((start + node.lineno - 1, node) for node in tree.body)
            break
    return out


def _docs_imports() -> list[tuple[str, int, ast.Import | ast.ImportFrom]]:
    """Every tether import statement in a Python fence: ``(file, line, node)``."""
    found = []
    for path in sorted(p for p in DOCS.rglob("*.md") if p not in _EXCLUDED):
        rel = path.relative_to(ROOT).as_posix()
        for lang, first, body in _fences(path):
            if lang not in _PYTHON_LANGS:
                continue
            for offset, node in _statements(body):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.ImportFrom)
                        and sub.level == 0
                        and _is_tether(sub.module)
                        or isinstance(sub, ast.Import)
                        and any(_is_tether(a.name) for a in sub.names)
                    ):
                        found.append((rel, first + offset + sub.lineno - node.lineno, sub))
    return found


def _is_tether(name: str | None) -> bool:
    return name is not None and (name == "tether" or name.startswith("tether."))


@cache
def _spec(dotted: str) -> ModuleSpec | None:
    """Resolve ``dotted`` under ``src/`` one package at a time, without importing anything."""
    parent, _, _ = dotted.rpartition(".")
    if parent:
        parent_spec = _spec(parent)
        if parent_spec is None or not parent_spec.submodule_search_locations:
            return None
        search = list(parent_spec.submodule_search_locations)
    else:
        search = [str(SRC)]
    return PathFinder.find_spec(dotted, search)


def _is_type_checking(test: ast.expr) -> bool:
    """``TYPE_CHECKING`` or ``<anything>.TYPE_CHECKING`` — false at runtime."""
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _bound_names(stmts: list[ast.stmt]) -> set[str]:
    """Names a module binds at top level, descending into if/try/with but not defs.

    The body of ``if TYPE_CHECKING:`` is skipped (its ``else`` is not): those imports
    exist only for the type checker, so a reader's ``from module import name`` of one
    fails at runtime and must not pass here.
    """
    names: set[str] = set()
    for node in stmts:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            names |= _bound_names(node.orelse)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names if a.name != "*")
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for field in ("body", "orelse", "finalbody"):
                names |= _bound_names(getattr(node, field, []))
            for handler in getattr(node, "handlers", []):
                names |= _bound_names(handler.body)
    return names


@cache
def _module_names(dotted: str) -> frozenset[str]:
    spec = _spec(dotted)
    assert spec is not None and spec.origin
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    return frozenset(_bound_names(tree.body))


def _problems(rel: str, line: int, node: ast.Import | ast.ImportFrom) -> list[str]:
    where = f"{rel}:{line}"
    if isinstance(node, ast.Import):
        return [
            f"{where}: 'import {a.name}' — no module {a.name} under src/"
            for a in node.names
            if _is_tether(a.name) and _spec(a.name) is None
        ]
    module = node.module or ""
    if _spec(module) is None:
        return [f"{where}: 'from {module} import …' — no module {module} under src/"]
    out = []
    for alias in node.names:
        if alias.name == "*":
            continue
        if alias.name in _module_names(module) or _spec(f"{module}.{alias.name}") is not None:
            continue
        out.append(
            f"{where}: 'from {module} import {alias.name}' — {module} defines no {alias.name}"
        )
    return out


def test_docs_import_examples_resolve() -> None:
    """Every tether import shown in a docs Python fence names a real module and object."""
    imports = _docs_imports()
    assert len(imports) >= 10, f"expected the docs' tether imports; found only {len(imports)}"
    problems = [p for rel, line, node in imports for p in _problems(rel, line, node)]
    assert not problems, "\n".join(problems)


def test_every_tether_import_line_in_a_fence_is_checked() -> None:
    """Coverage guard: no tether import line in any docs fence escapes the check above.

    Catches a fence whose language tag is not Python (or is missing) and a statement the
    fallback parse could not read — either would otherwise drop out silently.
    """
    checked = {(rel, line) for rel, line, _ in _docs_imports()}
    missed = []
    for path in sorted(p for p in DOCS.rglob("*.md") if p not in _EXCLUDED):
        rel = path.relative_to(ROOT).as_posix()
        for _, first, body in _fences(path):
            for offset, text in enumerate(body):
                if _TETHER_IMPORT_LINE_RE.match(text) and (rel, first + offset) not in checked:
                    missed.append(f"{rel}:{first + offset}: {text.strip()}")
    assert not missed, (
        "these tether imports sit in a docs fence the example check does not read — tag the "
        "fence ```python, or make the statement parse:\n" + "\n".join(missed)
    )


def test_resolution_never_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deep-stack example resolves without torch, because nothing is imported."""
    import sys

    _spec.cache_clear()
    _module_names.cache_clear()
    monkeypatch.setitem(sys.modules, "torch", None)  # any `import torch` now raises
    before = {m for m in sys.modules if m == "tether" or m.startswith("tether.")}
    assert "train_classifier" in _module_names("tether.ml.deep.model")
    after = {m for m in sys.modules if m == "tether" or m.startswith("tether.")}
    assert after == before


def test_flags_a_renamed_or_missing_object() -> None:
    """A wrong name or module is reported with file and line; a submodule import is fine."""
    node = ast.parse("from tether.analysis.histogram import no_such_function").body[0]
    assert _problems("docs/x.md", 7, node) == [
        "docs/x.md:7: 'from tether.analysis.histogram import no_such_function' — "
        "tether.analysis.histogram defines no no_such_function"
    ]
    node = ast.parse("from tether.analysis.no_such_module import x").body[0]
    assert _problems("docs/x.md", 8, node)[0].startswith("docs/x.md:8: ")
    node = ast.parse("import tether.no_such_package.sub").body[0]
    assert _problems("docs/x.md", 9, node)
    node = ast.parse("from tether.analysis import histogram").body[0]
    assert _problems("docs/x.md", 10, node) == []
    # A name bound only under `if TYPE_CHECKING:` does not exist at runtime.
    node = ast.parse("from tether.ml.active import Sequence").body[0]
    assert _problems("docs/x.md", 11, node) == [
        "docs/x.md:11: 'from tether.ml.active import Sequence' — "
        "tether.ml.active defines no Sequence"
    ]


def test_type_checking_bodies_are_skipped_but_their_else_is_not() -> None:
    tree = ast.parse(
        "import typing\n"
        "if TYPE_CHECKING:\n    from a import A\n"
        "if typing.TYPE_CHECKING:\n    from b import B\nelse:\n    B2 = 1\n"
        "if sys.version_info >= (3, 11):\n    C = 1\n"
    )
    assert _bound_names(tree.body) == {"typing", "B2", "C"}
