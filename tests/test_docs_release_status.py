# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``docs/stability.md`` describes the release state without counting it.

The deprecation policy's "Where is it announced?" row carries a note on the repository's
current release state. That note said the repository had **nine tags and no published
Releases** — true when written, false by the time #240 was filed: ten tags exist and
``v1.0.0-rc1`` has been a published, non-draft prerelease since 2026-07-21.

The failure mode is the *shape* of the claim, not the numbers in it. An inventory count
goes stale on the next tag, silently, and nothing in ``mkdocs build --strict`` can see it —
a wrong number renders exactly as well as a right one. So the guard forbids the shape and
pins the two facts the page actually needs: that the retained prerelease is published, and
that the stable Release is not.

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


def test_the_stability_page_separates_the_prerelease_from_the_stable_release() -> None:
    """#240's second criterion. The two are distinct facts and the page must not merge them.

    A reader who is told only "there is a Release" will look for `1.0.0` deprecation notes
    that do not exist; a reader told only "1.0.0 is unpublished" will assume the Releases
    page is empty and stop looking. Both halves are load-bearing.
    """
    text = STABILITY.read_text(encoding="utf-8")
    assert RC1_URL in text, "the page must link the retained prerelease it describes"
    assert "prerelease" in text, "the retained Release must be named as a prerelease"
    assert re.search(r"stable `v1\.0\.0` Release has not been published", text), (
        "the page must say the stable v1.0.0 Release is not published, so a reader does "
        "not read the retained prerelease as the 1.0 release notes"
    )


def test_the_two_release_pages_agree_about_the_retained_prerelease() -> None:
    """#240's third criterion: the announcement guidance stays consistent with `release.md`.

    ``docs/release.md`` is the page that owns the prerelease-retention decision; the
    stability page only cites it. If that page ever stops describing a retained
    prerelease, the citation here is the thing that goes wrong, so bind them.
    """
    release = RELEASE.read_text(encoding="utf-8")
    assert RC1_URL in release, "docs/release.md must still document the retained prerelease"
    assert "retain" in release.lower(), (
        "docs/stability.md calls the prerelease 'retained' on this page's authority"
    )

    stability = STABILITY.read_text(encoding="utf-8")
    assert "(release.md)" in stability, (
        "the announcement row must keep pointing at the release pipeline page"
    )
    assert "github.com/bioedca/tether/releases" in stability, (
        "the announcement row must keep pointing readers at the GitHub release notes"
    )
