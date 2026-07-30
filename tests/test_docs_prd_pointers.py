# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""No published page points a reader at ``docs/PRD.md``, which the site does not serve.

``mkdocs.yml`` lists ``PRD.md`` under ``exclude_docs``, so **every in-site link to it is dead by
construction** — and ``mkdocs build --strict`` cannot see the failure, because a bare-text mention
is not a link and an absolute URL always "resolves". Four published pages carried such pointers when
#188 was filed, including an ADR record, which builds under ``not_in_nav`` and is therefore live on
the site.

Dependency-free and stdlib-only: it reads Markdown as text and parses ``mkdocs.yml`` with the
``yaml`` already required by ``tests/test_issue_forms.py``, so it runs on the base 3-OS ``test``
matrix without the scientific stack.

**What counts as a pointer.** A reference that invites the reader to go and read the document. A
path named as the *scope of a rule* is not one — ``docs/agents/review.md`` enumerates
``docs/PRD.md`` in the list of paths whose edits are material, alongside ``AGENTS.md`` and
``.agents/**``, and rewriting that as a hyperlink would misrepresent a glob as a citation.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"

BLOB_URL = "https://github.com/bioedca/tether/blob/main/docs/PRD.md"

#: A mention of the excluded document, anywhere in a page.
_MENTION = re.compile(r"docs/PRD\.md")

#: A bare backticked token -- a path or glob and nothing else.
_BACKTICKED = re.compile(r"^`[^`]+`$")

#: How many backticked companions a scope enumeration must have. Two is the smallest number
#: that cannot be reached by ordinary prose accidentally: one neighbour is a sentence like
#: "``docs/PRD.md``, and the schema" and the real lists name four or more paths.
_MIN_COMPANIONS = 2


def _is_scope_enumeration(line: str) -> bool:
    """True when the mention is an element of a list of backticked paths, not a citation.

    Recognised by the WHOLE construct, not by the punctuation next to it. An earlier version
    allowed any mention followed by ``,`` or ``)``, which Codex pointed out is satisfied by
    ordinary prose -- ``Read `docs/PRD.md`, which documents the requirements.`` -- so a real
    dead pointer sitting before a comma stayed invisible and the sweep stayed green.

    Two conditions, and both are needed. The comma-separated element containing the mention
    must be *exactly* a backticked token, which rejects ``Read `docs/PRD.md``; and it must
    keep company with at least two more bare backticked tokens, which rejects a two-clause
    sentence whose first clause happens to be nothing but the path.
    """
    elements = [part.strip() for part in line.split(",")]
    holding = [part for part in elements if _MENTION.search(part)]
    if not holding or not _BACKTICKED.match(holding[0]):
        return False
    companions = [
        part
        for part in elements
        if part is not holding[0] and _BACKTICKED.match(part.rstrip(").").strip())
    ]
    return len(companions) >= _MIN_COMPANIONS


def _pages() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p.name != "PRD.md")


def _unresolved_mentions(text: str) -> list[str]:
    """Lines mentioning the excluded document in neither allowed form."""
    out = []
    for line in text.splitlines():
        if not _MENTION.search(line):
            continue
        if BLOB_URL in line or _is_scope_enumeration(line):
            continue
        out.append(line.strip())
    return out


def test_the_prd_is_excluded_from_the_built_site() -> None:
    """The premise. If the PRD were ever served, this whole guard would be pointless.

    Asserted from the parsed config rather than a text search, so a mention in a comment cannot
    satisfy it.
    """
    cfg = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))
    assert "PRD.md" in (cfg.get("exclude_docs") or "").split()


def test_no_published_page_points_at_the_unserved_prd() -> None:
    """#188's acceptance criterion, made durable instead of a grep run once.

    Every mention must be the absolute blob URL a reader can follow, or a path named as the scope
    of a rule. A bare in-site pointer sends the reader nowhere and `--strict` never notices.
    """
    offenders = {
        str(page.relative_to(_REPO)): lines
        for page in _pages()
        if (lines := _unresolved_mentions(page.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        "these published pages point at docs/PRD.md, which mkdocs excludes from the site, in a "
        f"form the reader cannot follow; use {BLOB_URL}: {offenders}"
    )


def test_the_guard_reports_a_bare_pointer() -> None:
    """An absence-assertion is worthless unless presence is shown, so both forms are pinned."""
    assert _unresolved_mentions("See `docs/PRD.md` §12.5 for the lifecycle.")
    assert _unresolved_mentions("See [the spec](../PRD.md) and docs/PRD.md §7.")
    assert not _unresolved_mentions(f"See the [product spec]({BLOB_URL}) §12.5.")
    # The scope-enumeration form, which is a rule's operand rather than a citation.
    assert not _unresolved_mentions(
        "governance text (`AGENTS.md`, `CONTRIBUTING.md`, `docs/PRD.md`, `docs/adr/**`)"
    )


def test_prose_before_a_comma_is_not_a_scope_enumeration() -> None:
    """Codex's counterexample on #305, and the reason the check is on the whole construct.

    The first version allowed any mention followed by `,` or `)`, so a dead pointer in a
    perfectly ordinary sentence read as a scope list and the sweep stayed green -- the guard
    reporting nothing while the criterion it enforces had regressed.
    """
    assert _unresolved_mentions("Read `docs/PRD.md`, which documents the requirements.")
    assert _unresolved_mentions("The spec (`docs/PRD.md`) has the details.")
    # One companion is not a list; it is a two-clause sentence.
    assert _unresolved_mentions("`docs/PRD.md`, `CONTRIBUTING.md` and a long prose clause.")
    # Three companions is a list, wherever the mention sits in it.
    assert not _unresolved_mentions(
        "material: `AGENTS.md`, `docs/PRD.md`, `docs/adr/**`, `.agents/**`"
    )
    assert not _unresolved_mentions(
        "material: `AGENTS.md`, `CONTRIBUTING.md`, `docs/adr/**`, `docs/PRD.md`"
    )


def test_the_roadmap_answers_what_it_exists_to_answer() -> None:
    """The page replaced those pointers, so it has to carry what a reader went looking for.

    Structural, not a wording check: the three sections #188 specifies, and the non-goals bounded
    below rather than counted exactly, since the count is editorial and the floor is the promise.
    """
    text = (DOCS / "roadmap.md").read_text(encoding="utf-8")
    for heading in ("## Current release", "## Next planned work", "## Explicit non-goals"):
        assert heading in text, f"the roadmap must state {heading!r}"

    non_goals = text.split("## Explicit non-goals", 1)[1]
    bullets = [ln for ln in non_goals.splitlines() if ln.startswith("- **")]
    assert len(bullets) >= 3, f"at least three explicit non-goals; found {len(bullets)}"


def test_the_roadmap_is_in_the_nav() -> None:
    """`--strict` fails on `omitted_files`, so this is also what keeps the build green."""
    cfg = yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))

    def targets(entries: object) -> list[str]:
        found: list[str] = []
        if isinstance(entries, list):
            for entry in entries:
                found += targets(entry)
        elif isinstance(entries, dict):
            for value in entries.values():
                found += targets(value)
        elif isinstance(entries, str):
            found.append(entries)
        return found

    assert "roadmap.md" in targets(cfg.get("nav"))
