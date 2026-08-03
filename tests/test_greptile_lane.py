# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Greptile is metered, so the thing that keeps it from firing is load-bearing config.

Greptile Pro includes 50 credits per seat per month, one credit per completed review, charged to
the PR author - and this account's single seat is shared across three active repositories. Left on
automatic it spends a credit the moment a PR opens: on 2026-08-03 it spent two in one day, across
two repositories, neither requested.

`.greptile/config.json` is what stops that, and it is silent when wrong. A misspelled key, a
`greptile.json` shadowing it, or a merge that drops the file all fail the same way - no error, just
credits draining. So the settings are asserted rather than trusted.

Stdlib only, so it runs on the base 3-OS `test` matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

CONFIG_DIR = _REPO / ".greptile"
CONFIG = CONFIG_DIR / "config.json"
LEGACY = _REPO / "greptile.json"
COUNTER = _REPO / ".agents" / "bin" / "greptile_usage.py"
GATE = _REPO / "docs" / "agents" / "review.md"


def test_greptile_never_reviews_a_pull_request_unasked() -> None:
    """`skipReview: "AUTOMATIC"` is the one setting the budget rests on.

    It suppresses the automatic review while leaving `@greptileai` working, which is exactly the
    draft-first lane: iterate with the unmetered provider, then spend a credit deliberately.
    """
    assert CONFIG.is_file(), "the config that gates a metered provider is missing"
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config.get("skipReview") == "AUTOMATIC", (
        "without skipReview=AUTOMATIC Greptile reviews every PR on open, at one credit each"
    )
    assert config.get("triggerOnDrafts") is False, "the draft phase is where the free work happens"
    assert config.get("triggerOnUpdates") is False, "a review per commit would multiply the spend"


def test_no_legacy_greptile_json_shadows_the_directory_config() -> None:
    """`.greptile/` takes precedence and `greptile.json` is then ignored - silently.

    Two files would mean the one a reader edits is not necessarily the one that binds, and the
    failure mode is a credit spent rather than an error.
    """
    assert not LEGACY.exists(), (
        "greptile.json is the legacy form and is ignored when .greptile/ exists; keep one"
    )


def test_the_config_is_valid_json_with_no_unknown_top_level_keys() -> None:
    """A misspelled key is not rejected by Greptile; it is ignored, and the default applies.

    The default for `skipReview` is to review, so a typo here is indistinguishable from having no
    config at all - which is the state this whole file exists to prevent.
    """
    known = {
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
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    unknown = sorted(set(config) - known)
    assert not unknown, f"these keys are not in Greptile's reference and will be ignored: {unknown}"


def test_the_counter_spans_the_whole_seat_not_one_repository() -> None:
    """Credits are billed per seat, so a per-repository number cannot say what is left.

    Greptile publishes no usage API, so this counter is the only programmatic reading available -
    and it is only honest if it looks everywhere the seat opens pull requests.
    """
    assert COUNTER.is_file(), "the only readable budget signal is missing"
    source = COUNTER.read_text(encoding="utf-8")
    assert "INCLUDED_CREDITS = 50" in source, "Greptile Pro includes 50 credits per seat per month"
    for repo in ("bioedca/tether", "bioedca/Yeliztli", "bioedca/tbox-finder"):
        assert repo in source, f"{repo} draws on the same seat and must be counted"


def test_the_review_gate_states_the_lane_and_its_order() -> None:
    """The config stops the spend; only the gate says what to spend it ON, and when.

    Asserted structurally rather than by prose equality: the ordering claim is the part a future
    edit could quietly invert, and inverting it is what makes the budget unaffordable again.
    """
    gate = GATE.read_text(encoding="utf-8")
    assert "@greptileai review this draft" in gate, "the manual trigger must be written down"
    assert ".agents/bin/greptile_usage.py" in gate, "the gate must name how to read the balance"
    draft = gate.index("as many rounds as it takes")
    greptile = gate.index("Optionally spend one Greptile credit")
    coderabbit = gate.index("last gate before merge")
    assert draft < greptile < coderabbit, (
        "the lane is ordered cheapest provider first; that order IS the budget control"
    )
