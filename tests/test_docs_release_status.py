# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``docs/stability.md`` describes the release state without counting it.

The deprecation policy's "Where is it announced?" row carries a note on the repository's
current release state. That note said the repository had **nine tags and no published
Releases** — true when written, false by the time #240 was filed: ten tags exist and
``v1.0.0-rc1`` has been a published, non-draft prerelease since 2026-07-21.

The failure mode is the *shape* of the claim, not the numbers in it. An inventory count
goes stale on the next tag, silently, and nothing in ``mkdocs build --strict`` can see it —
a wrong number renders exactly as well as a right one. So the guard forbids the shape.

#323 went one step further: the row no longer describes the release state at all. Its
replacement note ("the only Release published so far is ``v1.0.0-rc1``"; "the stable
``v1.0.0`` Release has not been published") was a *state* rather than a count, but it went
false on the next publication just the same. The row now answers "Where is it announced?"
with a link and lets the Releases page be the source of truth about itself, which retires
#240's second criterion and the test that pinned it.

Dependency-free and stdlib-only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
STABILITY = _REPO / "docs" / "stability.md"
RELEASE = _REPO / "docs" / "release.md"

RC1_URL = "https://github.com/bioedca/tether/releases/tag/v1.0.0-rc1"

#: A counted inventory of git tags -- "nine tags", "10 tags". The count is the defect: it is
#: correct only until the next tag, and the page has no way to notice when it stops being so.
_TAG_COUNT = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+tags\b",
    re.IGNORECASE,
)

#: A claim that the Releases page is empty. It is not, and has not been since 2026-07-21.
_NO_RELEASES = re.compile(r"\bno\s+(?:published\s+)?releases\b", re.IGNORECASE)


def _volatile_claims(text: str) -> list[str]:
    """Lines asserting a countable release inventory, which cannot stay true on its own."""
    return [
        line.strip()
        for line in text.splitlines()
        if _TAG_COUNT.search(line) or _NO_RELEASES.search(line)
    ]


def test_the_stability_page_states_no_countable_release_inventory() -> None:
    """#240's first criterion, made durable rather than a one-off correction.

    Scoped to this page deliberately: ``docs/reference/tether-format.md`` says "two tags"
    about the per-molecule ``tags`` column, which is a different noun entirely, so a
    repository-wide sweep on the same pattern would report a false offender.
    """
    offenders = _volatile_claims(STABILITY.read_text(encoding="utf-8"))
    assert not offenders, (
        "docs/stability.md states a release inventory that goes stale on the next tag; "
        f"describe the state instead of counting it: {offenders}"
    )


def test_the_guard_reports_the_wording_it_replaced() -> None:
    """An absence-assertion is worthless unless presence is shown, so both forms are pinned."""
    assert _volatile_claims("the repository has nine tags and **no published Releases**")
    assert _volatile_claims("the repository has 10 tags")
    assert _volatile_claims("there are no Releases on the Releases page")
    # The wording that replaced it names the state without counting it.
    assert not _volatile_claims(
        f"the only Release published so far is the retained [`v1.0.0-rc1` prerelease]({RC1_URL})"
    )


#: The announcement row of the deprecation-policy table.
_ANNOUNCEMENT_ROW = re.compile(r"^\| Where is it announced\? \|.*$", re.MULTILINE)

#: The row's exact allowed content, pinned. It describes *where* deprecations are announced and
#: links the Releases page and the release pipeline page; it says nothing about what is
#: currently on the Releases page. A denylist of state-describing phrasings could not be made
#: complete -- each version of one let ordinary wording through -- so the row is pinned whole.
EXPECTED_ANNOUNCEMENT_ROW = (
    "| Where is it announced? | **The GitHub release notes** for the release that introduces "
    "the deprecation, at [github.com/bioedca/tether/releases]"
    "(https://github.com/bioedca/tether/releases). There is no `CHANGELOG.md` in this "
    "repository, so do not go looking for one. The Releases page is the record of every "
    "published Release; the [release pipeline](release.md) describes how a Release is cut. |"
)


def _announcement_row(text: str) -> str:
    match = _ANNOUNCEMENT_ROW.search(text)
    assert match, "docs/stability.md must keep its 'Where is it announced?' row"
    return match.group(0)


def test_the_announcement_row_is_pinned_to_describe_the_page_not_its_contents() -> None:
    """#323: the row carries no statement a future Release publication silently falsifies.

    This replaces ``test_the_stability_page_separates_the_prerelease_from_the_stable_release``,
    deleted deliberately: it pinned "the stable ``v1.0.0`` Release has not been published",
    which made the page's staleness loud by turning CI red at the moment of a release cut.
    Under the maintainer's decision on #323 the row says nothing about the current state.
    Pinning the whole row keeps it that way: any edit fails here and has to be made
    deliberately, together with this constant.
    """
    row = _announcement_row(STABILITY.read_text(encoding="utf-8"))
    assert row == EXPECTED_ANNOUNCEMENT_ROW, (
        "the 'Where is it announced?' row changed. Describe the Releases page, not its "
        "contents: say nothing about what is or is not currently published (#323). If the "
        "change is deliberate and does that, update EXPECTED_ANNOUNCEMENT_ROW with it.\n"
        f"expected: {EXPECTED_ANNOUNCEMENT_ROW}\nfound:    {row}"
    )


def test_the_pin_reports_a_row_that_describes_the_page_contents() -> None:
    """Presence before absence: the removed note, or any rewording of it, fails the pin."""
    page = STABILITY.read_text(encoding="utf-8")
    for note in (
        f"the only Release published so far is the retained [`v1.0.0-rc1` prerelease]({RC1_URL}).",
        "The prerelease is currently the only available Release.",
    ):
        reworded = page.replace(
            EXPECTED_ANNOUNCEMENT_ROW,
            EXPECTED_ANNOUNCEMENT_ROW.removesuffix(" |") + f" {note} |",
        )
        assert reworded != page
        assert _announcement_row(reworded) != EXPECTED_ANNOUNCEMENT_ROW


def test_the_two_release_pages_agree_about_the_retained_prerelease() -> None:
    """#240's third criterion: the announcement guidance stays consistent with `release.md`.

    ``docs/release.md`` is the page that owns the prerelease-retention decision and the
    release process; since #323 the stability row no longer names the retained prerelease
    and only links that page and the Releases page, which are what it must keep.
    """
    release = RELEASE.read_text(encoding="utf-8")
    assert RC1_URL in release, "docs/release.md must still document the retained prerelease"
    assert "retain" in release.lower(), (
        "docs/release.md owns the decision to retain the prerelease and must still state it"
    )

    stability = STABILITY.read_text(encoding="utf-8")
    assert "(release.md)" in stability, (
        "the announcement row must keep pointing at the release pipeline page"
    )
    assert "github.com/bioedca/tether/releases" in stability, (
        "the announcement row must keep pointing readers at the GitHub release notes"
    )
