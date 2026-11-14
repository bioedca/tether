# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source links on a release build of the docs site point at the release tag (#600).

Pages link Tether's source as ``https://github.com/bioedca/tether/blob/main/...``. The site is
versioned with ``mike``, so a reader of the ``1.0`` pages must reach the ``1.0`` code, not
today's file on ``main``. ``scripts/mkdocs_hooks.py`` rewrites those links to the tag that
``docs.yml`` passes in ``TETHER_DOCS_SOURCE_TAG``; with the variable unset the links stay on
``main``. These tests cover both cases, run the rewrite over every page of the site, and guard
the precondition that makes the rewrite complete: no page links a different branch or a commit.

Stdlib-only apart from the ``yaml`` the base lock already carries, so it runs on the 3-OS
``test`` matrix, which has no MkDocs.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
HOOKS = _REPO / "scripts" / "mkdocs_hooks.py"

_spec = importlib.util.spec_from_file_location("mkdocs_hooks", HOOKS)
assert _spec is not None and _spec.loader is not None
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)

ENV = hooks.SOURCE_TAG_ENV
BASE = "https://github.com/bioedca/tether"

#: A link into this repository's files: ``blob/<ref>`` or ``tree/<ref>``, ``<ref>`` being one
#: path segment. A ref that holds a ``/`` would be read as its first segment, which is not a
#: branch the guard accepts, so such a link still fails.
_SOURCE_LINK = re.compile(r"github\.com/bioedca/tether/(?:blob|tree)/([^/\s)#?\"'>\]]+)")


def _site_pages() -> list[Path]:
    """Every Markdown page MkDocs builds: ``docs/**/*.md`` minus the excluded ``PRD.md``."""
    cfg = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))
    excluded = {line.strip() for line in cfg.get("exclude_docs", "").splitlines() if line.strip()}
    assert excluded == {"PRD.md"}, f"update _site_pages for the new exclude_docs: {excluded}"
    return sorted(p for p in DOCS.rglob("*.md") if p.relative_to(DOCS).as_posix() != "PRD.md")


# --- the hook ------------------------------------------------------------------------------


def test_release_build_points_source_links_at_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "v9.9.9")
    page = (
        f"See [`batch.py`]({BASE}/blob/main/x.py) and the [tree]({BASE}/tree/main/src).\n"
        f"[ref]: {BASE}/blob/main/docs/x.md#anchor\n"
        f"The repository root: <{BASE}/tree/main>.\n"
        f"A bare link ends a sentence: {BASE}/tree/main.\n"
    )
    out = hooks.on_page_markdown(page, page=None, config=None, files=None)
    assert out == (
        f"See [`batch.py`]({BASE}/blob/v9.9.9/x.py) and the [tree]({BASE}/tree/v9.9.9/src).\n"
        f"[ref]: {BASE}/blob/v9.9.9/docs/x.md#anchor\n"
        f"The repository root: <{BASE}/tree/v9.9.9>.\n"
        f"A bare link ends a sentence: {BASE}/tree/v9.9.9.\n"
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_tag_keeps_source_links_on_main(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    page = f"[`batch.py`]({BASE}/blob/main/x.py) and [src]({BASE}/tree/main/src)\n"
    assert hooks.on_page_markdown(page, page=None, config=None, files=None) == page
    assert hooks.on_config({"site_name": "Tether"}) == {"site_name": "Tether"}


def test_only_this_repositorys_main_is_rewritten() -> None:
    untouched = (
        f"{BASE}/blob/main-old/x.py {BASE}/tree/main.bak/x "
        "https://github.com/jimporter/mike/blob/main/README.md "
        f"{BASE}/issues/600 {BASE}/releases/tag/v1.0.0 {BASE}/tree/v0.0.0"
    )
    assert hooks.rewrite_source_links(untouched, "v9.9.9") == untouched


@pytest.mark.parametrize("value", ["main", "1.0.0", "v1.0", "v1.0.0/../x", "v1.0.0 x", "dev"])
def test_a_value_that_is_not_a_release_tag_stops_the_build(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV, value)
    with pytest.raises(ValueError, match=ENV):
        hooks.on_config({})
    with pytest.raises(ValueError, match=ENV):
        hooks.on_page_markdown("", page=None, config=None, files=None)


@pytest.mark.parametrize("tag", ["v1.0.0", "v10.2.33", "v1.0.0-rc3"])
def test_release_tags_are_accepted(tag: str) -> None:
    assert hooks.source_tag({ENV: tag}) == tag


# --- the site ------------------------------------------------------------------------------


def test_a_release_build_leaves_no_source_link_on_main() -> None:
    pages = _site_pages()
    rewritten = 0
    for page in pages:
        text = page.read_text(encoding="utf-8")
        out = hooks.rewrite_source_links(text, "v9.9.9")
        left = re.findall(r"bioedca/tether/(?:blob|tree)/main(?![\w-]|\.\w)", out)
        assert not left, f"{page.relative_to(_REPO)} keeps {len(left)} link(s) on main"
        rewritten += out.count("/v9.9.9")
    # The site links its source (24 links on nav pages when #600 was filed). Zero here means
    # the walk or the pattern broke, not that the rewrite succeeded.
    assert rewritten >= 24


def test_no_page_links_a_branch_other_than_main_or_a_commit() -> None:
    """The hook rewrites only ``main``, so a link to any other ref would escape it.

    A version tag is allowed: it already names a release, as ``docs/release.md`` does for the
    first tag. A commit hash or a different branch is not.
    """
    bad: list[str] = []
    for page in _site_pages():
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            for ref in _SOURCE_LINK.findall(line):
                if ref != "main" and not hooks._TAG_RE.fullmatch(ref):
                    bad.append(f"{page.relative_to(_REPO)}:{n}: {ref}")
    assert not bad, "source links that are not on main or on a version tag:\n" + "\n".join(bad)


def test_the_guard_pattern_sees_a_commit_and_a_branch() -> None:
    line = f"[a]({BASE}/blob/0123abc/x.py) [b]({BASE}/tree/feature/x) [c]({BASE}/blob/main/x)"
    assert _SOURCE_LINK.findall(line) == ["0123abc", "feature", "main"]


# --- the wiring ----------------------------------------------------------------------------


def test_mkdocs_registers_the_hook() -> None:
    cfg = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))
    assert "scripts/mkdocs_hooks.py" in cfg.get("hooks", [])


def test_the_docs_deployment_passes_the_tag_it_builds_from() -> None:
    workflow = yaml.safe_load(
        (_REPO / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["deploy"]["steps"]
    (publish,) = [s for s in steps if "mike deploy" in s.get("run", "")]
    assert publish["env"][ENV] == "${{ github.ref_type == 'tag' && github.ref_name || '' }}"
