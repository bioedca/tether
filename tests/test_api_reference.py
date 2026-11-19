# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The generated API reference covers every public module and builds offline (#179, ADR-0111).

``mkdocs build --strict`` checks that each rendered cross-reference resolves, but it cannot see
a module that no page renders: such a module is simply absent from the site, with the gate green.
These tests close that gap and pin the wiring that keeps the gate free of the conda stack:

* each public subpackage of ``src/tether`` has its own page;
* each public module of ``src/tether`` (no part of its dotted path starts with ``_``) has its
  own ``::: <module>`` block on the page of its subpackage, or on the overview page for the
  package itself and its top-level modules, and no block names a module that does not exist;
* each page under ``docs/api/`` is in the ``API reference`` section of the nav;
* ``mkdocs.yml`` lists ``search`` before ``mkdocstrings`` (MkDocs turns ``search`` on by itself
  only while no ``plugins:`` key exists), and the Python handler reads ``src`` statically, with
  the two options ADR-0093 requires;
* ``requirements-docs.txt`` pins each generator package exactly;
* ``src/tether/py.typed`` exists and is empty (PEP 561).

Stdlib plus PyYAML (a mkdocs dependency in the base lock); it reads the files as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
_API = _REPO / "docs" / "api"
_BLOCK_RE = re.compile(r"^::: (\S+)\s*$", re.MULTILINE)
#: Text the generator does not run: an HTML comment, or a fenced block at the root.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)
_SUBPACKAGES = (
    "analysis",
    "fret",
    "gui",
    "idealize",
    "imaging",
    "io",
    "ml",
    "project",
    "services",
)


def _config() -> dict:
    return yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))


def _public_modules(subpackage: str) -> list[str]:
    found = []
    for path in sorted((_SRC / "tether" / subpackage).rglob("*.py")):
        parts = list(path.relative_to(_SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if any(part.startswith("_") for part in parts):
            continue
        found.append(".".join(parts))
    return found


def _blocks(page: Path) -> list[str]:
    """The ``:::`` blocks the generator renders: one in a comment or a fence does not count."""
    text = page.read_text(encoding="utf-8")
    return _BLOCK_RE.findall(_FENCE_RE.sub("", _COMMENT_RE.sub("", text)))


def _module_exists(dotted: str) -> bool:
    base = _SRC.joinpath(*dotted.split("."))
    return (base / "__init__.py").is_file() or base.with_suffix(".py").is_file()


def _nav_pages(entries: object) -> list[str]:
    if isinstance(entries, list):
        return [page for entry in entries for page in _nav_pages(entry)]
    if isinstance(entries, dict):
        return [page for value in entries.values() for page in _nav_pages(value)]
    return [entries] if isinstance(entries, str) else []


def _api_section() -> list[str]:
    def find(entries: object) -> object:
        if isinstance(entries, list):
            for entry in entries:
                if (hit := find(entry)) is not None:
                    return hit
        elif isinstance(entries, dict):
            for title, value in entries.items():
                if title == "API reference":
                    return value
                if (hit := find(value)) is not None:
                    return hit
        return None

    section = find(_config()["nav"])
    assert section is not None, "mkdocs.yml nav has no 'API reference' section"
    return _nav_pages(section)


def test_every_public_module_has_a_block_on_its_subpackage_page() -> None:
    problems = []
    for sub in _SUBPACKAGES:
        page = _API / f"{sub}.md"
        assert page.is_file(), f"docs/api/{sub}.md is missing"
        blocks = _blocks(page)
        for module in _public_modules(sub):
            if blocks.count(module) != 1:
                problems.append(
                    f"docs/api/{sub}.md: '::: {module}' appears {blocks.count(module)} times"
                )
    assert not problems, "\n".join(problems)


def test_every_public_subpackage_has_its_own_page() -> None:
    """A new subpackage needs a page: the test above walks only the listed ones."""
    found = sorted(
        path.parent.name
        for path in (_SRC / "tether").glob("*/__init__.py")
        if not path.parent.name.startswith("_")
    )
    assert found == sorted(_SUBPACKAGES), f"subpackages under src/tether: {found}"


def test_every_top_level_module_has_a_block_on_the_overview_page() -> None:
    top = ["tether"] + [
        f"tether.{path.stem}"
        for path in sorted((_SRC / "tether").glob("*.py"))
        if not path.stem.startswith("_")
    ]
    blocks = _blocks(_API / "index.md")
    missing = [module for module in top if blocks.count(module) != 1]
    assert not missing, f"docs/api/index.md needs exactly one '::: <module>' for {missing}"


def test_every_block_names_a_module_that_exists() -> None:
    problems = [
        f"{page.relative_to(_REPO)}: '::: {block}' names no module under src/"
        for page in sorted(_API.glob("*.md"))
        for block in _blocks(page)
        if not _module_exists(block)
    ]
    assert not problems, "\n".join(problems)


def test_the_module_walk_sees_the_package() -> None:
    """Sanity: the walk finds modules, so an empty result cannot pass the test above."""
    assert "tether.analysis.histogram" in _public_modules("analysis")
    assert "tether.ml.deep" in _public_modules("ml")
    assert not any("._" in module for sub in _SUBPACKAGES for module in _public_modules(sub))


def test_a_block_in_a_comment_or_a_fence_does_not_count(tmp_path: Path) -> None:
    """A block the generator skips must not satisfy the coverage tests above."""
    page = tmp_path / "page.md"
    page.write_text(
        "::: tether.io.movie\n\n"
        "<!--\n::: tether.services.maps\n-->\n\n"
        "```text\n::: tether.analysis.histogram\n```\n\n"
        "~~~\n::: tether.ml.deep\n~~~\n",
        encoding="utf-8",
    )
    assert _blocks(page) == ["tether.io.movie"]


def test_every_api_page_is_in_the_api_reference_section() -> None:
    pages = sorted(f"api/{page.name}" for page in _API.glob("*.md"))
    assert sorted(_api_section()) == pages


def test_search_is_listed_before_mkdocstrings() -> None:
    names = [next(iter(p)) if isinstance(p, dict) else p for p in _config()["plugins"]]
    assert names[:2] == ["search", "mkdocstrings"], names


def test_the_handler_reads_the_source_without_importing_it() -> None:
    plugin = next(
        p["mkdocstrings"]
        for p in _config()["plugins"]
        if isinstance(p, dict) and "mkdocstrings" in p
    )
    python = plugin["handlers"]["python"]
    assert python["paths"] == ["src"]
    options = python["options"]
    assert options["allow_inspection"] is False
    assert options["docstring_style"] == "numpy"
    assert options["scoped_crossrefs"] is True
    assert options["show_if_no_docstring"] is True
    # Only the names in `__all__` (or, with no `__all__`, the public ones), as the overview says.
    assert options["filters"] == "public"
    assert "inventories" not in python, "ADR-0111: the build fetches nothing from the network"


def test_the_generator_packages_are_pinned_exactly() -> None:
    pins = {}
    for line in (_REPO / "requirements-docs.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, version = line.partition("==")
            pins[name] = version
    for name in ("mkdocstrings[python]", "mkdocstrings-python", "mkdocs-autorefs", "griffelib"):
        assert re.fullmatch(r"\d+(\.\d+)+", pins.get(name, "")), f"{name} is not pinned: {pins}"


def test_the_package_carries_an_empty_py_typed_marker() -> None:
    marker = _SRC / "tether" / "py.typed"
    assert marker.is_file()
    assert marker.read_bytes() == b""
