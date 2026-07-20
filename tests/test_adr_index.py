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

# An index table row: `| [NNNN](NNNN-title.md) | Title | Status | PRD anchor |`.
_ROW_RE = re.compile(r"^\|\s*\[(\d{4})\]\(")

# The Title cell should be the record's H1. The longest current heading is 161 chars
# (ADR-0019), so 200 leaves headroom for a new record without re-admitting the
# multi-hundred-character Decision dumps this bound was introduced to stop.
_MAX_TITLE_CHARS = 200


def _numbered_adrs() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md") if p.name != _TEMPLATE)


def _index_lines() -> list[str]:
    return INDEX.read_text(encoding="utf-8").split("\n")


def test_at_least_one_adr_present() -> None:
    """Sanity: the ADR set is non-empty (guards a broken ADR_DIR path)."""
    adrs = _numbered_adrs()
    assert len(adrs) >= 50, f"expected the accumulated ADR set; found only {len(adrs)}"


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


def test_index_rows_render_as_table_rows() -> None:
    """No blank line splits a table block, so every ADR row renders as a row.

    A single blank line legally terminates a Markdown table, so everything after it
    renders as one run-on paragraph of literal ``|`` characters. ``mkdocs build
    --strict`` cannot see this — nothing becomes an unresolvable link — and the
    link-target assertions above still pass, because ``](NNNN-title.md)`` is present
    whether the row is a table cell or prose. That combination is how ADRs 0039-0050
    reached the published site as a paragraph.

    Assert structurally, with no Markdown renderer: every ADR row must be immediately
    preceded by another table line (a row, or the ``|---|`` delimiter). A blank line
    *between* the two index sections stays legal, because the row after it is preceded
    by that section's own delimiter row. Stdlib only — ``python-markdown`` is not in
    the base conda-lock, so it cannot be imported on the 3-OS ``test`` matrix.
    """
    lines = _index_lines()
    orphaned = [
        (i + 1, m.group(1))
        for i, ln in enumerate(lines)
        if (m := _ROW_RE.match(ln)) and not (i > 0 and lines[i - 1].startswith("|"))
    ]
    assert not orphaned, (
        "these ADR rows are not preceded by a table line, so a blank line has split the "
        f"table and they render as literal '|' prose — (line, ADR): {orphaned}"
    )


def test_index_titles_are_concise() -> None:
    """Each Title cell is the record's own H1, not its full Decision text.

    The index exists to route a reader to the right record; cells running to hundreds
    of characters stop being scannable and duplicate text that the record already
    carries. Keep each row's Title tracking that record's ``# NNNN — ...`` heading.
    """
    over: list[tuple[str, int]] = []
    for ln in _index_lines():
        m = _ROW_RE.match(ln)
        if not m:
            continue
        cells = ln.split("|")
        title = "|".join(cells[2:-3]).strip()
        if len(title) > _MAX_TITLE_CHARS:
            over.append((m.group(1), len(title)))
    assert not over, (
        f"these ADR index Title cells exceed {_MAX_TITLE_CHARS} characters — use the "
        f"record's H1 and leave the detail in the record — (ADR, length): {over}"
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
    repo. Parse mkdocs.yml as YAML and inspect the resolved structure (so a stray
    mention in a comment cannot satisfy the check): the nav maps "Architecture
    decisions" -> adr/README.md, and ``not_in_nav`` keeps the individual records in
    the build (so ``--strict`` still validates their links) rather than excluding them.
    """
    import yaml  # provided by the base conda-lock (a mkdocs dependency)

    cfg = yaml.safe_load((ADR_DIR.parents[1] / "mkdocs.yml").read_text(encoding="utf-8"))

    nav_targets = [
        value
        for entry in cfg.get("nav", [])
        if isinstance(entry, dict)
        for key, value in entry.items()
        if key == "Architecture decisions"
    ]
    assert nav_targets == ["adr/README.md"], (
        f"mkdocs.yml nav must map 'Architecture decisions' -> adr/README.md; got {nav_targets}"
    )

    not_in_nav = (cfg.get("not_in_nav") or "").split()
    assert "adr/0*.md" in not_in_nav, (
        f"mkdocs.yml not_in_nav must keep the ADR records in the build; got {not_in_nav}"
    )

    # And the records must NOT be excluded from the build entirely.
    exclude_docs = (cfg.get("exclude_docs") or "").split()
    assert not any(pat.startswith("adr/") for pat in exclude_docs), (
        f"mkdocs.yml exclude_docs must not drop the ADR records; got {exclude_docs}"
    )
