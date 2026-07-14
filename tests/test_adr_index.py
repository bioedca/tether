# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ADR index is complete and its cross-links resolve (PRD §12.7 / §9 M9 gate).

PRD §12.7 makes the resolved-decision ADRs a concrete deliverable, and the M9 docs
PR "fails unless the ADR index is complete (no placeholder gaps)". This module is the
durable guard for that gate: every ``docs/adr/NNNN-*.md`` record is listed in the
index (``docs/adr/README.md``), every ``.md`` link in the index resolves to a file
that exists, and no ADR cross-link points at a missing record.

It runs on the base 3-OS ``test`` matrix, so it also catches a broken relative link
(the class of bug that made wiring ``adr/`` into the site break ``mkdocs build
--strict``) on Windows/macOS, not only on the Linux-only ``docs-build`` job.
"""

from __future__ import annotations

import re
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parents[1] / "docs" / "adr"
INDEX = ADR_DIR / "README.md"

# Numbered decision records are NNNN-kebab-title.md; 0000 is the blank template, not a
# decision, so it is excluded from the "must be indexed" set.
_TEMPLATE = "0000-template.md"

# A local Markdown link target ending in .md, with an optional #anchor: `](path.md)`
# or `](path.md#frag)`. External (http/https) links never match (they carry a scheme
# before `.md` only via a full URL, which the `[^)]` path capture still resolves
# against disk and would flag — none exist in the ADR set).
_MD_LINK_RE = re.compile(r"\]\((?!https?://)([^)#]+\.md)(?:#[^)]*)?\)")


def _numbered_adrs() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md") if p.name != _TEMPLATE)


def test_at_least_one_adr_present() -> None:
    """Sanity: the ADR set is non-empty (guards a broken ADR_DIR path)."""
    adrs = _numbered_adrs()
    assert len(adrs) >= 49, f"expected the accumulated ADR set; found only {len(adrs)}"


def test_every_adr_is_indexed() -> None:
    """Every numbered ADR file is linked from the index — no placeholder gaps.

    The index links each record as ``[NNNN](NNNN-title.md)``; assert the exact link
    target ``](<filename>)`` appears for every record on disk. A new ADR added without
    an index row (the §0.4 DoD "home it in the same PR" rule) fails here.
    """
    index_text = INDEX.read_text(encoding="utf-8")
    missing = [p.name for p in _numbered_adrs() if f"]({p.name})" not in index_text]
    assert not missing, (
        "these ADR records exist but are not linked in docs/adr/README.md "
        f"(the M9 'index complete, no gaps' gate): {missing}"
    )


def test_index_links_resolve() -> None:
    """Every ``.md`` link in the index points at a file that exists (no dead links)."""
    index_text = INDEX.read_text(encoding="utf-8")
    dead = sorted({t for t in _MD_LINK_RE.findall(index_text) if not (ADR_DIR / t).is_file()})
    assert not dead, f"docs/adr/README.md links to non-existent ADR files: {dead}"


def test_all_adr_cross_links_resolve() -> None:
    """Every relative ``.md`` link inside any ADR (and the template) resolves.

    This is the base-matrix complement to the Linux-only ``mkdocs build --strict``
    link check: a cross-ADR link to a renamed/missing record (e.g. the historical
    ``0024-one-click-idealization-store.md`` typo) fails here on all three OSes.
    """
    broken: dict[str, list[str]] = {}
    for adr in sorted(ADR_DIR.glob("*.md")):
        text = adr.read_text(encoding="utf-8")
        dead = sorted({t for t in _MD_LINK_RE.findall(text) if not (adr.parent / t).is_file()})
        if dead:
            broken[adr.name] = dead
    assert not broken, f"ADR records contain dead relative .md links: {broken}"


def test_index_wired_into_rendered_site() -> None:
    """mkdocs wires the ADR index into the built site (not excluded).

    The M9 gate needs the index *rendered and navigable*, not merely present in the
    repo. Assert the index page is in the nav and the individual records are kept in
    the build via ``not_in_nav`` (so ``--strict`` still validates their links).
    """
    mkdocs = (ADR_DIR.parents[1] / "mkdocs.yml").read_text(encoding="utf-8")
    assert "adr/README.md" in mkdocs, "mkdocs.yml nav must include the ADR index page"
    assert "not_in_nav" in mkdocs and "adr/0*.md" in mkdocs, (
        "mkdocs.yml must keep the individual ADR records in the build via not_in_nav"
    )
