# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rewrite the Sphinx cross-reference roles of ``src/tether`` (issue #611, ADR-0093).

A Markdown-based API generator prints a Sphinx role such as ``:func:`load_project``` as
literal text. This script rewrites every role in ``src/tether`` once, so that the generator of
#179 (mkdocstrings with its Python handler) can link it:

* **In a docstring**, a role becomes a Markdown reference-style link to the object's full
  canonical path, the cross-reference form of mkdocstrings::

      :func:`load_project`              ->  [`load_project`][tether.project.store.load_project]
      :class:`~tether.io.movie.Movie`   ->  [`Movie`][tether.io.movie.Movie]
      :exc:`ValueError`                 ->  [`ValueError`][ValueError]

  The text that the reader sees is the text that Sphinx shows: the target as written, or its
  last part after a ``~``, or the title of an explicit ``title <target>`` role. The target is
  the path where the object is *defined*, not where it is re-exported, because that is the path
  that the Python handler always gives an anchor.
* **In a comment**, a role becomes a code span (```load_project```). The generator does not read
  comments, so a link there has no reader.

The script resolves a target statically, with the standard library only: it reads every module of
``src/tether`` with :mod:`ast` and builds a table of the names that each module defines and
imports. It never imports ``tether``. A name resolves in this order, which follows Python's own
scoping: a member of the enclosing class, then a name of the module (its definition or its
import), then a full ``tether.`` path, then a top-level name defined in exactly one module of
``tether``, then a builtin. A target that none of these find stays a code span, and the script
names it on stderr.

The change is to docstrings and comments only. After the rewrite the script parses each changed
file again and compares its syntax tree, with every docstring blanked, to the tree of the file
before. A difference is an error and nothing is written.

Usage::

    python scripts/migrate_sphinx_roles.py           # rewrite src/tether in place
    python scripts/migrate_sphinx_roles.py --check   # exit 1 if a role is left (writes nothing)

Rerunning it on the parent of the migration commit reproduces that commit exactly.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import re
import sys
import tokenize
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
_PACKAGE = "tether"

#: One Sphinx role, optionally with the ``py:`` domain: ``:func:`x```, ``:py:class:`~a.B```.
ROLE = re.compile(
    r":(?:py:)?(?P<kind>func|class|mod|attr|meth|data|exc|obj|const):`(?P<body>[^`]+)`"
)
#: The explicit-title form of a role body: ``title <target>``.
_EXPLICIT = re.compile(r"^(?P<title>.*?)\s*<(?P<target>[^<>]+)>$", re.DOTALL)


#: Top-level packages outside ``tether`` whose objects keep the path they are named by. The
#: standard library comes from the interpreter; the rest are the base stack's packages that the
#: docstrings name. A fixed list, so the result does not depend on what is installed.
_OUTSIDE = frozenset(sys.stdlib_module_names) | {
    "h5py",
    "napari",
    "numba",
    "matplotlib",
    "numpy",
    "pyqtgraph",
    "PySide6",
    "pytest",
    "qtpy",
    "scipy",
    "skimage",
    "sklearn",
    "tifffile",
}


# --------------------------------------------------------------------------- the static index


@dataclass
class _Module:
    name: str
    is_package: bool
    #: Name bound at module level -> ("def", full path) or ("import", imported dotted path).
    names: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class Index:
    """What ``src/tether`` defines, read with :mod:`ast` and never imported."""

    modules: dict[str, _Module] = field(default_factory=dict)
    #: Full path of a class -> the names of its members.
    members: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: Every defined object: modules, module-level names, classes and their members.
    defined: set[str] = field(default_factory=set)
    #: Top-level short name -> the full paths that define it.
    short: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # .. building ...........................................................................

    def add_module(self, name: str, tree: ast.Module, is_package: bool) -> None:
        mod = _Module(name, is_package)
        self.modules[name] = mod
        self.defined.add(name)
        for node in _module_level(tree.body):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._define(mod, node.name)
                if isinstance(node, ast.ClassDef):
                    self._add_class(f"{name}.{node.name}", node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for target in _targets(node):
                    self._define(mod, target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        mod.names[alias.asname] = ("import", alias.name)
                    else:
                        top = alias.name.split(".")[0]
                        mod.names.setdefault(top, ("import", top))
            elif isinstance(node, ast.ImportFrom):
                base = _absolute(name, is_package, node.module, node.level)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    mod.names[alias.asname or alias.name] = ("import", f"{base}.{alias.name}")

    def _define(self, mod: _Module, short: str) -> None:
        full = f"{mod.name}.{short}"
        mod.names[short] = ("def", full)
        self.defined.add(full)
        self.short[short].add(full)

    def _add_class(self, path: str, node: ast.ClassDef) -> None:
        self.defined.add(path)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._member(path, item.name)
                if isinstance(item, ast.ClassDef):
                    self._add_class(f"{path}.{item.name}", item)
                else:
                    # Instance attributes: ``self.x = ...`` anywhere in a method.
                    for sub in ast.walk(item):
                        if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                            for tgt in sub.targets if isinstance(sub, ast.Assign) else [sub.target]:
                                if (
                                    isinstance(tgt, ast.Attribute)
                                    and isinstance(tgt.value, ast.Name)
                                    and tgt.value.id == "self"
                                ):
                                    self._member(path, tgt.attr)
            elif isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for target in _targets(item):
                    self._member(path, target)

    def _member(self, cls: str, name: str) -> None:
        self.members[cls].add(name)
        self.defined.add(f"{cls}.{name}")

    # .. resolving ..........................................................................

    def canonical(self, path: str, _depth: int = 0) -> str | None:
        """Follow re-exports from *path* to the path that defines the object, or ``None``."""
        if _depth > 20:
            return None
        if path.split(".")[0] != _PACKAGE:
            return path  # an outside object keeps the path it was named by
        if path in self.modules:
            return path
        parts = path.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:cut])
            mod = self.modules.get(prefix)
            if mod is None:
                continue
            head, rest = parts[cut], parts[cut + 1 :]
            bound = mod.names.get(head)
            if bound is None:
                # A submodule that the package does not import is still reachable by its path.
                sub = f"{prefix}.{head}"
                if sub in self.modules:
                    return self.canonical(".".join([sub, *rest]), _depth + 1)
                return None
            kind, target = bound
            if kind == "import":
                return self.canonical(".".join([target, *rest]), _depth + 1)
            return self._walk_members(target, rest)
        return None

    def _walk_members(self, base: str, rest: list[str]) -> str | None:
        path = base
        for part in rest:
            if part not in self.members.get(path, ()):
                return None
            path = f"{path}.{part}"
        return path

    def resolve(
        self, target: str, module: str, classes: list[str]
    ) -> tuple[str, bool | None] | None:
        """Resolve a role *target* written in *module*, inside the classes *classes*.

        Return the full path and whether Python's own scoping finds the target as written
        (``True``), finds it only by its path (``False``) or not at all, the script having found
        a unique owner by its name (``None``); or return ``None`` when nothing resolves. The
        strategies run in order and the first that names a known object wins.
        """
        for candidate, scoped in self._candidates(target, module, classes):
            if candidate is None:
                continue
            if candidate.split(".")[0] != _PACKAGE or candidate in self.defined:
                return candidate, scoped
        return None

    def _candidates(self, target: str, module: str, classes: list[str]):
        if target.startswith("."):
            hits = sorted(p for p in self.defined if p.endswith(target))  # Sphinx's ".name"
            yield (hits[0] if len(hits) == 1 else None), False
            return
        head, _, tail = target.partition(".")
        rest = tail.split(".") if tail else []
        # 1. A member of an enclosing class, innermost first, as Sphinx searches.
        for cls in reversed(classes):
            if head in self.members.get(cls, ()):
                yield self._walk_members(f"{cls}.{head}", rest), True
        # 2. A name that the module defines or imports.
        bound = self.modules[module].names.get(head)
        if bound is not None:
            yield self.canonical(".".join([bound[1], *rest])), True
        # 3. A full path into the package.
        if head == _PACKAGE:
            yield self.canonical(target), True
        # 4. A path written relative to an enclosing package, such as ``idealize.reidealize``
        #    under a function-local ``from tether.project import idealize``.
        parts = module.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            yield self.canonical(".".join([*parts[:cut], target])), False
        # 5. A top-level name that exactly one module of the package defines.
        owners = self.short.get(head, set())
        if len(owners) == 1:
            yield self.canonical(".".join([next(iter(owners)), *rest])), None
        # 6. A builtin, or an object of the standard library or the base stack.
        if hasattr(builtins, head) or head in _OUTSIDE:
            yield target, True
        # 7. A bare member name written outside its class: the one class that has it, first in
        #    this module, then in the package.
        if not rest:
            for scope in (f"{module}.", f"{_PACKAGE}."):
                owners_of = sorted(
                    c for c, names in self.members.items() if head in names and c.startswith(scope)
                )
                if len(owners_of) == 1:
                    yield f"{owners_of[0]}.{head}", None
                    return


def _module_level(body: list[ast.stmt]):
    """Yield the module-level statements, looking inside ``if``/``try``/``with`` blocks."""
    for node in body:
        if isinstance(node, ast.If):
            yield from _module_level(node.body)
            yield from _module_level(node.orelse)
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody):
                yield from _module_level(block)
            for handler in node.handlers:
                yield from _module_level(handler.body)
        elif isinstance(node, ast.With):
            yield from _module_level(node.body)
        else:
            yield node


def _targets(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[str]:
    raw = node.targets if isinstance(node, ast.Assign) else [node.target]
    out: list[str] = []
    for tgt in raw:
        for sub in ast.walk(tgt):
            if isinstance(sub, ast.Name):
                out.append(sub.id)
    return out


def _absolute(module: str, is_package: bool, name: str | None, level: int) -> str:
    if level == 0:
        return name or ""
    parts = module.split(".")
    base = parts if is_package else parts[:-1]
    base = base[: len(base) - (level - 1)] if level > 1 else base
    return ".".join([*base, *([name] if name else [])])


def module_name(path: Path) -> tuple[str, bool]:
    """Return the dotted module name of *path* under ``src/`` and whether it is a package."""
    rel = path.relative_to(_SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


def build_index(files: list[Path]) -> Index:
    index = Index()
    for path in files:
        name, is_package = module_name(path)
        index.add_module(name, ast.parse(path.read_text(encoding="utf-8")), is_package)
    return index


# --------------------------------------------------------------------------- the rewrite


@dataclass
class Report:
    links: int = 0
    short: int = 0
    summary: int = 0
    spans: int = 0
    unresolved: list[str] = field(default_factory=list)
    long: list[str] = field(default_factory=list)
    guessed: list[str] = field(default_factory=list)


def _is_private(path: str) -> bool:
    """Whether a part of *path* is private (``_name``); a dunder such as ``__call__`` is not."""
    return any(p.startswith("_") and not p.endswith("__") for p in path.split("."))


def _split(body: str) -> tuple[str, str, bool]:
    """Return ``(shown text, target, link)`` for a role body, as Sphinx reads it."""
    explicit = _EXPLICIT.match(body)
    if explicit:
        target = re.sub(r"\s+", "", explicit["target"])
        return explicit["title"].strip(), target.lstrip("~!"), not target.startswith("!")
    link = not body.startswith("!")
    # A role that wraps over two lines of a docstring: a path never holds whitespace.
    body = re.sub(r"\s+", "", body.lstrip("!"))
    if body.startswith("~"):
        target = body[1:]
        return target.split(".")[-1], target, link
    return body.lstrip("."), body, link


def _rewrite_docstring(
    text: str, index: Index, module: str, classes: list[str], where: str, report: Report
) -> str:
    def sub(match: re.Match[str]) -> str:
        shown, target, link = _split(match["body"])
        if not link:
            report.spans += 1
            return f"`{shown}`"
        found = index.resolve(target, module, classes)
        if found is None:
            report.spans += 1
            report.unresolved.append(f"{where}: {match.group(0)}")
            return f"`{shown}`"
        path, scoped = found
        if scoped is None:
            report.guessed.append(f"{where}: {match.group(0)} -> {path}")
        if _is_private(path):
            # The generator leaves private names out of the reference, so a link has no target.
            report.spans += 1
            return f"`{shown}`"
        report.links += 1
        if scoped and shown == target:
            # The handler's scoped cross-references find this name as Python does: short form.
            report.short += 1
            return f"[`{shown}`][]"
        return f"[`{shown}`][{path}]"

    return ROLE.sub(sub, text)


#: The line length that ruff holds the code to (``[tool.ruff] line-length``).
LINE_LENGTH = 100
_LIST_ITEM = re.compile(r"^(?P<indent>\s*)(?P<marker>[-*+] |\d+\. )")
_NUMPY_PARAMETER = re.compile(r"^\s*\*{0,2}\w+(?:\s*,\s*\*{0,2}\w+)*\s:\s")


def _wrap(old: str, new: str, column: int, where: str, report: Report) -> str:
    """Break each rewritten docstring line that is now too long, at a space outside a link.

    Only a line that the rewrite changed is touched. The text after the break moves to a new
    line with the same indentation (under the text of a list item), which Markdown reads as the
    same paragraph. A NumPy ``name : type`` line is never broken; it is reported instead.
    """
    old_lines = set(old.split("\n"))
    out: list[str] = []
    for number, line in enumerate(new.split("\n")):
        first = column if number == 0 else 0
        if line in old_lines or first + len(line) <= LINE_LENGTH:
            out.append(line)
            continue
        if _NUMPY_PARAMETER.match(line):
            report.long.append(f"{where}: a parameter line is longer than {LINE_LENGTH}")
            out.append(line)
            continue
        if number == 0:
            # The summary must stay one line (the docstring standard, ruff D205). Turn its new
            # links into code spans, the longest first, until it fits; never break it.
            line = _shorten_summary(line, LINE_LENGTH - first, report)
            if first + len(line) > LINE_LENGTH:
                report.long.append(f"{where}: a summary line is longer than {LINE_LENGTH}")
            out.append(line)
            continue
        item = _LIST_ITEM.match(line)
        if item:
            indent = item["indent"] + " " * len(item["marker"])
        else:
            indent = line[: len(line) - len(line.lstrip())]
        while first + len(line) > LINE_LENGTH and " " in line.strip():
            cut = _break_at(line, LINE_LENGTH - first)
            if cut is None:
                report.long.append(f"{where}: no space to break a line at")
                break
            out.append(line[:cut].rstrip())
            line = indent + line[cut:].lstrip()
            first = 0
        out.append(line)
    return "\n".join(out)


_LINK = re.compile(r"\[`(?P<shown>[^`]+)`\]\[(?P<target>[^\]]*)\]")


def _shorten_summary(line: str, room: int, report: Report) -> str:
    links = sorted(_LINK.finditer(line), key=lambda m: (-len(m.group(0)), m.start()))
    for match in links:
        if len(line) <= room:
            break
        line = line.replace(match.group(0), f"`{match['shown']}`", 1)
        report.links -= 1
        report.short -= not match["target"]
        report.spans += 1
        report.summary += 1
    return line


def _break_at(line: str, limit: int) -> int | None:
    """Where to break *line*: the last space at or before *limit* outside a code span and a
    link, else the first such space after it, else ``None``."""
    best = None
    ticks = 0
    depth = 0
    start = len(line) - len(line.lstrip())
    for i, char in enumerate(line[: limit + 1]):
        if char == "`":
            ticks ^= 1
        elif char == "[" and not ticks:
            depth += 1
        elif char == "]" and not ticks:
            depth = max(depth - 1, 0)
        elif char == " " and not ticks and not depth and i > start:
            best = i
    if best is None:
        # One link is longer than the room: break after it instead. Ruff does not count a line
        # of a single word as too long, so the link may stand on a line of its own.
        for i in range(limit + 1, len(line)):
            char = line[i]
            if char == "`":
                ticks ^= 1
            elif char == "[" and not ticks:
                depth += 1
            elif char == "]" and not ticks:
                depth = max(depth - 1, 0)
            elif char == " " and not ticks and not depth:
                return i
    return best


def _rewrite_comment(text: str, report: Report) -> str:
    def sub(match: re.Match[str]) -> str:
        report.spans += 1
        return f"`{_split(match['body'])[0]}`"

    return ROLE.sub(sub, text)


def _docstring_scopes(tree: ast.Module, module: str) -> dict[int, list[str]]:
    """Map the start line of each docstring to the full paths of the classes around its owner."""
    scopes: dict[int, list[str]] = {}

    def visit(node: ast.AST, classes: list[str]) -> None:
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            outer = classes[-1] if classes else module
            own = [*classes, f"{outer}.{node.name}"] if isinstance(node, ast.ClassDef) else classes
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                scopes[body[0].value.lineno] = own
            classes = own
        for child in ast.iter_child_nodes(node):
            visit(child, classes)

    visit(tree, [])
    return scopes


def _blank_docstrings(tree: ast.AST) -> str:
    """Dump *tree* with every docstring emptied, so only executable code is compared."""
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body[0].value.value = ""
    return ast.dump(tree, include_attributes=False)


def rewrite_source(source: str, index: Index, module: str, label: str, report: Report) -> str:
    """Return *source* with every role rewritten; raise if executable code would change."""
    tree = ast.parse(source)
    scopes = _docstring_scopes(tree, module)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits: list[tuple[int, int, str]] = []
    for tok in tokens:
        if not ROLE.search(tok.string):
            continue
        start = offsets[tok.start[0] - 1] + tok.start[1]
        end = offsets[tok.end[0] - 1] + tok.end[1]
        where = f"{label}:{tok.start[0]}"
        if tok.type == tokenize.COMMENT:
            new = _rewrite_comment(tok.string, report)
        elif tok.type == tokenize.STRING and tok.start[0] in scopes:
            new = _rewrite_docstring(tok.string, index, module, scopes[tok.start[0]], where, report)
            new = _wrap(tok.string, new, tok.start[1], where, report)
        else:
            raise SystemExit(f"{where}: a role outside a docstring or comment; not rewritten")
        edits.append((start, end, new))
    out = source
    for start, end, new in reversed(edits):
        out = out[:start] + new + out[end:]
    if out != source and _blank_docstrings(ast.parse(out)) != _blank_docstrings(tree):
        raise SystemExit(f"{label}: the rewrite would change executable code; nothing written")
    return out


def package_files() -> list[Path]:
    return sorted((_SRC / _PACKAGE).rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report only; exit 1 on any role")
    parser.add_argument(
        "--explain", action="store_true", help="also name each target found by its name alone"
    )
    args = parser.parse_args(argv)
    files = package_files()
    index = build_index(files)
    report = Report()
    changed: dict[Path, str] = {}
    for path in files:
        source = path.read_text(encoding="utf-8")
        label = path.relative_to(_REPO).as_posix()
        new = rewrite_source(source, index, module_name(path)[0], label, report)
        if new != source:
            changed[path] = new
    for line in report.unresolved:
        print(f"unresolved, left as a code span: {line}", file=sys.stderr)
    if args.explain:
        for line in report.guessed:
            print(f"found by its name alone: {line}", file=sys.stderr)
    for line in report.long:
        print(f"left too long: {line}", file=sys.stderr)
    print(
        f"{len(changed)} files, {report.links} links ({report.short} short), "
        f"{report.spans} code spans ({report.summary} to keep a summary on one line), "
        f"{len(report.unresolved)} unresolved"
    )
    if args.check:
        return 1 if changed else 0
    for path, new in changed.items():
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
