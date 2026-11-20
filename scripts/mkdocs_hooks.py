# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MkDocs build hooks for the documentation site (the ``hooks:`` setting of ``mkdocs.yml``).

Two hooks live here.

**Source links follow the release** (#600). Pages link Tether's source
as ``https://github.com/bioedca/tether/blob/main/...`` (or ``tree/main/...``), and authors keep
writing them that way. When the build is a release deployment, ``docs.yml`` sets
``TETHER_DOCS_SOURCE_TAG`` to the tag it checked out and builds from, and this hook rewrites every
such link to that tag. A reader of the ``1.0`` docs who follows "this is the code that computes the
value" then reaches the code that shipped in ``1.0``, not today's file on ``main`` (FAIR R1.2).

With the variable unset or empty -- a local ``mkdocs build --strict``, the required ``docs-build``
gate, and the ``dev`` deployment dispatched from ``main`` -- the links stay on ``main``.

**The project schema is served** (#612). ``schema/schema_frozen.json``, the machine-checked manifest
that ``scripts/dump_schema.py --check`` guards, lives outside ``docs/``. :func:`on_files` adds it to
the site as ``reference/tether-schema.json``, so each versioned build serves the schema of the
version it documents, at a stable address beside the column dictionary and the provenance schema
(which are committed under ``docs/reference/`` and need no hook). The file is copied byte for byte.

The module is stdlib-only at import time -- :func:`on_files` imports MkDocs only when MkDocs calls
it -- so ``tests/test_docs_source_links.py`` and ``tests/test_docs_published_data.py`` run it in the
base test matrix, which has no MkDocs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent

#: Files outside ``docs/`` that the site serves, as ``{site path: repository file}`` (#612).
PUBLISHED_FILES: dict[str, Path] = {
    "reference/tether-schema.json": _REPO / "schema" / "schema_frozen.json",
}

#: The environment variable ``docs.yml`` sets to the release tag it builds from.
SOURCE_TAG_ENV = "TETHER_DOCS_SOURCE_TAG"

# A release tag exactly as release.yml's `verify` job accepts one (its TAG_ERE): vMAJOR.MINOR.PATCH
# with no leading zeros, and an optional pre-release part of letters, digits, dots and hyphens.
# The value comes from the workflow's ref, so it is checked rather than trusted before it is
# written into every source link of the site. `[0-9]`, not `\d`, which also matches other digits.
_TAG_RE = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?"
)

# `blob/main` or `tree/main` on this repository, where `main` is the whole ref name: what follows
# it cannot continue a branch name. A branch called `main-x` or `main.y` is left alone, while a
# sentence's full stop after a bare `.../tree/main` is not taken for part of the name.
_MAIN_LINK_RE = re.compile(r"(https://github\.com/bioedca/tether/(?:blob|tree))/main(?![\w-]|\.\w)")


def source_tag(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the release tag that source links point at, or ``None`` to keep them on ``main``.

    Reads :data:`SOURCE_TAG_ENV` from ``environ`` (default ``os.environ``). An unset or empty
    value means "not a release build". Any other value must look like a release tag; anything
    else raises :class:`ValueError`, which stops the build rather than publishing broken links.
    """
    env = os.environ if environ is None else environ
    tag = env.get(SOURCE_TAG_ENV, "").strip()
    if not tag:
        return None
    if not _TAG_RE.fullmatch(tag):
        raise ValueError(
            f"{SOURCE_TAG_ENV}={tag!r} is not a release tag (expected vMAJOR.MINOR.PATCH, "
            "optionally with a pre-release part); unset it to keep source links on main"
        )
    return tag


def rewrite_source_links(markdown: str, tag: str | None) -> str:
    """Point every ``blob/main`` and ``tree/main`` link to this repository at ``tag``.

    ``tag`` of ``None`` returns ``markdown`` unchanged.
    """
    if tag is None:
        return markdown
    return _MAIN_LINK_RE.sub(lambda m: f"{m.group(1)}/{tag}", markdown)


def on_config(config: Any, **kwargs: Any) -> Any:
    """Validate :data:`SOURCE_TAG_ENV` once, before any page is read."""
    source_tag()
    return config


def on_page_markdown(markdown: str, **kwargs: Any) -> str:
    """Rewrite the page's source links to the release tag of a release build."""
    return rewrite_source_links(markdown, source_tag())


def on_files(files: Any, config: Any, **kwargs: Any) -> Any:
    """Add every :data:`PUBLISHED_FILES` entry to the site, copied unchanged.

    A missing source file raises :class:`FileNotFoundError`, which stops the build rather than
    publishing a page that links to nothing.
    """
    from mkdocs.structure.files import File  # MkDocs is present whenever MkDocs calls this

    for site_path, source in PUBLISHED_FILES.items():
        if not source.is_file():
            raise FileNotFoundError(f"{source} is missing; the site cannot serve {site_path}")
        files.append(File.generated(config, site_path, abs_src_path=str(source)))
    return files
