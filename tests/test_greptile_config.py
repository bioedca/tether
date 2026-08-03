# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Greptile is metered, so the config that keeps it from firing is load-bearing.

Greptile Pro includes 50 credits per seat per month, one credit per completed review, charged to
the PR author - and this account's single seat is shared across three active repositories. Left on
automatic it spends a credit the moment a PR opens: on 2026-08-03 it spent two in one day, across
two repositories, neither requested.

`.greptile/config.json` is what stops that, and every way it can break is silent. A misspelled key
is ignored and the default applies; a `greptile.json` alongside it is ignored wholesale; a bad merge
that drops the file leaves no trace. None of those raise anything - they just spend credits. So the
settings are asserted rather than trusted.

Stdlib only, so it runs on the base 3-OS `test` matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

CONFIG = _REPO / ".greptile" / "config.json"
LEGACY = _REPO / "greptile.json"

#: Greptile's documented `greptile.json` / `.greptile/config.json` reference, as of 2026-08-03.
KNOWN_KEYS = frozenset(
    {
        "$schema",
        "skipReview",
        "triggerOnDrafts",
        "triggerOnUpdates",
        "strictness",
        "commentTypes",
        "instructions",
        "labels",
        "disabledLabels",
        "includeBranches",
        "excludeBranches",
        "includeAuthors",
        "excludeAuthors",
        "includeKeywords",
        "ignoreKeywords",
        "fileChangeLimit",
        "ignorePatterns",
        "context",
        "customContext",
        "patternRepositories",
        "shouldUpdateDescription",
        "updateSummaryOnly",
        "fixWithAI",
        "hideFooter",
        "includeIssuesTable",
        "includeConfidenceScore",
        "includeSequenceDiagram",
        "statusCheck",
        "statusCommentsEnabled",
        "summarySection",
        "issuesTableSection",
        "confidenceScoreSection",
        "sequenceDiagramSection",
    }
)


def _config() -> dict[str, object]:
    assert CONFIG.is_file(), "the config that gates a metered provider is missing"
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_greptile_never_reviews_a_pull_request_unasked() -> None:
    """`skipReview: "AUTOMATIC"` is the single setting the credit budget rests on.

    It suppresses the automatic review while leaving `@greptileai` working, so a credit is spent
    deliberately rather than on every PR that happens to open.
    """
    config = _config()
    assert config.get("skipReview") == "AUTOMATIC", (
        "without skipReview=AUTOMATIC Greptile reviews every PR on open, at one credit each"
    )
    assert config.get("triggerOnDrafts") is False, "a draft must stay free to iterate in"
    assert config.get("triggerOnUpdates") is False, "a review per commit would multiply the spend"


def test_no_legacy_greptile_json_shadows_the_directory_config() -> None:
    """`.greptile/` takes precedence, and `greptile.json` is then ignored - silently.

    Two files would mean the one a reader edits is not necessarily the one that binds, and the
    failure mode is a credit spent rather than an error raised.
    """
    assert not LEGACY.exists(), (
        "greptile.json is the legacy form and is ignored when .greptile/ exists; keep one"
    )


def test_every_key_is_one_greptile_actually_reads() -> None:
    """A misspelled key is not rejected; it is ignored, and the default applies.

    The default for `skipReview` is to review, so a typo here is indistinguishable from having no
    config at all - which is the state this file exists to prevent.
    """
    unknown = sorted(set(_config()) - KNOWN_KEYS)
    assert not unknown, f"these keys are not in Greptile's reference and will be ignored: {unknown}"
