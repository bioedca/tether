# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the atomic issue-claim helper.

Every GitHub call goes through a fake transport: CI must not depend on the network, and the
interesting cases (losing a 422 race, a reclaimed generation) cannot be produced on demand against
a live repository anyway.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "bin" / "claim.py"

_spec = importlib.util.spec_from_file_location("tether_claim", SCRIPT)
assert _spec is not None and _spec.loader is not None
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

DIGEST = "a" * 64
OTHER = "b" * 64
MARKER = '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + DIGEST + '"} -->'
HEAD = "c" * 40


# A claimable issue is a GROOMED one, and since #336 that includes an Execution-autonomy
# declaration the body carries itself. The default fixture therefore declares it; a test that wants
# an ungroomed body passes `body=` explicitly.
GROOMED_BODY = "Acceptance criteria\n\n## Execution autonomy\n\nagent-can-do-alone\n"


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "state": "open",
        "title": "feat(io): a thing",
        "body": GROOMED_BODY,
        "labels": [{"name": "status:ready"}],
        "assignees": [],
    }
    issue.update(overrides)
    return issue


class Fake:
    """Records every request and answers from a routing table keyed by (method, path-prefix)."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        for (route_method, prefix), response in self.routes.items():
            if method == route_method and path.startswith(prefix):
                return response
        return 200, None


def _install(monkeypatch: pytest.MonkeyPatch, fake: Fake) -> Fake:
    # Patch the module OBJECT, not a dotted string: a dotted patch of a non-package module
    # resolves differently under CI's import layout.
    monkeypatch.setattr(claim, "_request", fake)
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: DIGEST)
    return fake


Routes = dict[tuple[str, str], tuple[int, Any]]


def _routes(over: Routes | None = None) -> Routes:
    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/issues/7/comments"): (
            200,
            [{"user": {"login": "bioedca"}, "body": f"Approved.\n\n{MARKER}"}],
        ),
        ("GET", "/repos/bioedca/tether/issues/7"): (200, _issue()),
        ("GET", "/repos/bioedca/tether/git/ref/heads/main"): (200, {"object": {"sha": HEAD}}),
        ("POST", "/repos/bioedca/tether/git/refs"): (201, {}),
        ("GET", "/repos/bioedca/tether/activity"): (
            200,
            [{"id": 42, "activity_type": "branch_creation"}],
        ),
    }
    routes.update(over or {})
    return routes


# --------------------------------------------------------------------- eligibility


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        (_issue(state="closed"), "not open"),
        (_issue(labels=[{"name": "status:blocked"}]), "not status:ready"),
        (_issue(assignees=[{"login": "someone-else"}]), "assigned to someone else"),
        (_issue(pull_request={"url": "x"}), "pull request"),
    ],
    ids=["closed", "not-ready", "other-assignee", "is-a-pr"],
)
def test_claim_refuses_ineligible_work_before_creating_any_ref(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    issue: dict[str, Any],
    message: str,
) -> None:
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, issue)})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert message in capsys.readouterr().err
    # The mutex must never be taken for work that may not be worked.
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_claim_refuses_when_the_approval_no_longer_binds_the_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue was edited after approval: the marker's digest no longer matches the snapshot."""
    fake = _install(monkeypatch, Fake(_routes()))
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: OTHER)
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "edited after approval" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_claim_ignores_an_approval_from_a_non_maintainer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/issues/7/comments"): (
                200,
                [{"user": {"login": "a-stranger"}, "body": MARKER}],
            )
        }
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "no maintainer approval" in capsys.readouterr().err


# ------------------------------------------------------- what the issue says about itself (#336)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("## Execution autonomy\n\nexternal/human action required\n", "human action"),
        ("## Execution autonomy\n\nmaintainer decision required\n", "maintainer decision"),
        ("Acceptance criteria\n", "declares no Execution autonomy"),
        (
            "## Execution autonomy\n\n`needs-human-action` - a desktop installer\n",
            "needs-human-action",
        ),
        (
            "## Execution autonomy\n\n`agent-can-do-alone` for the drafting; the rest is a "
            "maintainer decision\n",
            "maintainer decision",
        ),
        # Greptile on #428: a separator must not decide a safety verdict. This opens with an
        # admitting prefix and names its restriction with a hyphen, so a literal-token match saw
        # nothing and ADMITTED it - a fail-open in the one gate whose purpose is failing closed.
        (
            "## Execution autonomy\n\nagent-can-do-alone; maintainer-decision required\n",
            "maintainer decision",
        ),
        (
            "## Execution autonomy\n\nagent-can-do-alone, needs_human_action for the upload\n",
            "human action",
        ),
        (
            "## Execution autonomy\n\n`agent-can-do-alone`, unless the sizing note above says "
            "grooming must decide first.\n",
            "is not a registered autonomy value",
        ),
        (
            "## Execution autonomy\n\n`agent-can-do-alone` for the drafting; the validation-suite "
            "membership needs maintainer confirmation.\n",
            "is not a registered autonomy value",
        ),
        (
            "## Execution autonomy\n\n`agent-can-do-alone`, unless the sizing note says "
            "otherwise.\n",
            "is not a registered autonomy value",
        ),
        (
            "<!-- tether-grooming-v1 -->\n\n- **Autonomy after unblock:** agent-can-do-alone\n",
            "after unblock",
        ),
        (
            "<!-- tether-grooming-v1 -->\n\n"
            "- **Status:** unblocked\n"
            "- **Autonomy after unblock:** agent-can-do-alone\n",
            "after unblock",
        ),
        # Codex on #462: the same condition, soft-wrapped onto the next line, was admitted.
        (
            "## Execution autonomy\n\nagent-can-do-alone\nunless the sizing note says otherwise\n",
            "is not a registered autonomy value",
        ),
        (
            "<!-- tether-grooming-v1 -->\n\n"
            "- **Status:** unblocked.\n"
            "- **Autonomy:** agent-can-do-alone\n"
            "  once the sizing note is settled.\n"
            "- **Open dependencies:** none.\n",
            "is not a registered autonomy value",
        ),
    ],
    ids=[
        "external-human",
        "maintainer-decision",
        "absent",
        "legacy-spelling",
        "split-declaration",
        "hyphenated-restriction",
        "underscored-restriction",
        "conditional-corpus-339",
        "conditional-corpus-379",
        "qualified",
        "after-unblock",
        "after-unblock-status-unblocked",
        "wrapped-heading-condition",
        "wrapped-grooming-bullet-condition",
    ],
)
def test_an_issue_whose_body_says_no_agent_can_do_it_is_not_claimable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body: str,
    expected: str,
) -> None:
    """The regression for #336, and it must fail against the pre-#336 `_check_eligible`.

    Every issue here is open, `status:ready`, unassigned and carries a marker that binds - so all
    four checks that existed before this passed, and the mutex was issued. #246 is why that matters:
    its body records that *"a wrong first upload cannot be replaced (PyPI forbids re-uploading a
    version)"*, so an agent reaching that step produces a permanent public artifact with wrong
    metadata. Most admission mistakes waste a worker; that one cannot be undone.
    """
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert expected in capsys.readouterr().err
    # Eligibility is a precondition of the claim: the ref must never have been created.
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


@pytest.mark.parametrize(
    "body",
    [
        "## Execution autonomy\n\nagent-can-do-alone\n",
        "### Execution autonomy\n\n`agent-can-do-alone`.\n",
        "## Autonomy\n\nagent-can-do-alone\n",
        "- **Autonomy:** agent-can-do-alone\n",
        "- **Autonomy:** agent can complete alone\n",
    ],
    ids=[
        "heading",
        "backticked",
        "short-heading",
        "bullet",
        "legacy-prose",
    ],
)
def test_every_spelling_of_agent_can_do_alone_in_the_live_corpus_admits(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """The five registered spellings in the live corpus still admit."""
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    claim._cmd_claim(_args(issue=7))
    assert [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


@pytest.mark.parametrize("token", claim.AUTONOMY_REFUSES)
def test_no_refusal_token_depends_on_the_separator_it_is_written_with(token: str) -> None:
    """Re-spelling an `AUTONOMY_REFUSES` entry must not change any verdict.

    The flattener's whole job is that a separator never decides a safety verdict, and the
    table is the one place a future edit can quietly undo that. `external/human` is the only
    entry carrying a `/`, and until this test existed the flattener widened `[\\s_-]+` but not
    `/`: writing that entry as `external - human` instead left it unable to match the body it
    was there to refuse, re-opening the exact fail-open Greptile found on #428 - silently, in a
    table edit that reads as a formatting change.

    So this asserts the property over **every** entry rather than the one that broke. The parameter
    set is frozen at collection time so a mutation that empties the module attribute still runs
    these assertions. Each restriction sits in **scan-only** prose - the remainder of a bare
    admitting heading's section - where the token scan is the only check that runs, and the
    refusal must name the entry it matched: token matching is its only possible reason to refuse.
    This used to sit in a table row's value cell, and once that cell became exact-checked the body
    refused for a second reason and the assertion held with the scan broken (Greptile on #462).
    """
    separators = (" ", "-", "_", "/")
    canonical = claim._flatten_autonomy(token)
    body = f"## Execution autonomy\n\nagent-can-do-alone\n\n{token} applies here\n"
    for separator in separators:
        respelled = canonical.replace(" ", separator)
        assert claim._flatten_autonomy(respelled) == canonical, (
            f"{token!r} re-spelled as {respelled!r} flattens differently"
        )
        patched = tuple(respelled if entry == token else entry for entry in claim.AUTONOMY_REFUSES)
        original = claim.AUTONOMY_REFUSES
        try:
            claim.AUTONOMY_REFUSES = patched
            refusal = claim._autonomy_refusal(body)
            assert refusal is not None, (
                f"{token!r} written as {respelled!r} stopped refusing - fail-open"
            )
            # The token verdict names the table entry it matched - the first in table order, which
            # for `needs human action` is the shorter `human action` - and the scan-only place.
            named = re.search(r"It names '([^']*)'", refusal)
            assert named is not None and named.group(1) in patched, refusal
            assert "heading remainder" in refusal, refusal
            # The scan is the only reason: with no entries to match, the same body admits.
            claim.AUTONOMY_REFUSES = ()
            assert claim._autonomy_refusal(body) is None
        finally:
            claim.AUTONOMY_REFUSES = original


def test_an_autonomy_table_row_can_refuse_but_not_admit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sizing-table restriction governs beside a bare, registered heading value."""
    table_only = claim._autonomy_refusal(
        "| Field | Value |\n| --- | --- |\n| **autonomy** | agent-can-do-alone |\n"
    )
    assert table_only is not None and "only in a shape that cannot admit" in table_only

    body = (
        "| Field | Value |\n| --- | --- |\n| **autonomy** | maintainer decision required |\n\n"
        "## Execution autonomy\n\nagent-can-do-alone\n"
    )
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "maintainer decision" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_an_autonomy_table_row_is_read_with_or_without_its_outer_pipes() -> None:
    """GitHub Markdown makes a row's leading and trailing pipes optional, so the scan must too.

    Codex on #462: `Autonomy | maintainer decision required` above a bare admitting heading was
    admitted, because the row pattern demanded a leading `|`. That is table typography deciding a
    safety verdict - the same class as the separator defect, in the one shape R4 added.
    """
    heading = "\n\n## Execution autonomy\n\nagent-can-do-alone\n"
    rows = {
        "no outer pipes": "Autonomy | maintainer decision required",
        "trailing pipe only": "**Autonomy** | maintainer decision required |",
        "leading pipe only": "| autonomy | maintainer decision required",
        "indented, no outer pipes": "  Execution autonomy | maintainer decision required",
        # The first cell's emphasis is typography as well, and so is a trailing colon.
        "code-formatted key": "| `autonomy` | maintainer decision required |",
        "underscore emphasis": "| __Autonomy__ | maintainer decision required |",
        "colon inside the emphasis": "| **Autonomy:** | maintainer decision required |",
        "colon outside the emphasis": "_Autonomy_: | maintainer decision required",
    }
    for where, row in rows.items():
        # The row is a table's header row: GitHub needs the delimiter line below it to render a
        # table at all, and the parser reads what GitHub renders (ADR-0066).
        refusal = claim._autonomy_refusal(row + "\n--- | ---" + heading)
        assert refusal is not None, f"{where}: a restrictive table row was not read - fail-open"
        assert "maintainer decision" in refusal, f"{where}: {refusal}"

    # Still unable to admit: dropping the pipe must not turn a row into a way to admit.
    bare = claim._autonomy_refusal("autonomy | agent-can-do-alone\n--- | ---\n")
    assert bare is not None and "only in a shape that cannot admit" in bare

    # Without the delimiter line there is no table: GitHub renders the pipes as text, and a
    # paragraph is not a declaration shape, so the line is read exactly as it is rendered.
    lone = claim._autonomy_refusal("| **autonomy** | maintainer decision required |" + heading)
    assert lone is None, "a lone piped line is a paragraph on GitHub, not a table row"


def test_a_recognised_table_value_is_exact_checked_but_never_admits() -> None:
    """Codex on #462 (read of `f7c11d7`): `Autonomy | human review required` beside an admitting
    heading was claimable. The row was scan-only, and `human review required` is not one of the
    finite `AUTONOMY_REFUSES` phrases, so a recognised autonomy declaration holding neither
    registered value was ignored. A row keyed `autonomy` is now a declaration like a `+` bullet:
    its value cell is exact-checked, so any unregistered value refuses, and it still cannot admit.
    """
    heading = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    table = "| Field | Value |\n| --- | --- |\n| Autonomy | {} |\n"
    unregistered = claim._autonomy_refusal(heading + table.format("human review required"))
    assert unregistered is not None, "an unregistered table value was ignored - fail-open"
    assert "human review required" in unregistered and "not a registered" in unregistered
    assert "table row" in unregistered, unregistered

    # A registered value in the row counts for nothing either way: the heading admits, and the
    # row alone is still absence.
    assert claim._autonomy_refusal(heading + table.format("agent-can-do-alone")) is None
    alone = claim._autonomy_refusal(table.format("agent-can-do-alone"))
    assert alone is not None and "only in a shape that cannot admit" in alone

    # Cells past the value are scanned for a token, one cell at a time.
    wide = "| Field | Value | Note |\n| --- | --- | --- |\n| Autonomy | agent-can-do-alone | {} |\n"
    assert claim._autonomy_refusal(heading + wide.format("after a maintainer decision")) is not None
    assert claim._autonomy_refusal(heading + wide.format("small and self-contained")) is None


def test_a_styled_autonomy_heading_is_read_like_a_styled_bullet() -> None:
    """Codex on #462 (read of `f7c11d7`): `## **Execution autonomy**` over `maintainer decision
    required` was not read as the autonomy heading, because `Heading.text` carries the inline
    `**`, so a plain admitting bullet elsewhere made the issue claimable. Typography must not
    decide a verdict in either direction - the bullet's `**` has been optional for the same
    reason - so the heading key tolerates the same dress the table cell does, and a styled
    heading at column zero admits exactly as a plain one would.
    """
    restricted = (
        "- **Autonomy:** agent-can-do-alone\n\n## **Execution autonomy**\n\n"
        "maintainer decision required\n"
    )
    refusal = claim._autonomy_refusal(restricted)
    assert refusal is not None, "a styled restrictive heading was skipped - fail-open"
    assert "maintainer decision" in refusal and "heading" in refusal, refusal

    for styled in ("## **Execution autonomy**", "## _Autonomy_:", "## `Execution autonomy`"):
        assert claim._autonomy_refusal(f"{styled}\n\nagent-can-do-alone\n") is None, styled
        indented = claim._autonomy_refusal(f"   {styled}\n\nagent-can-do-alone\n")
        assert indented is not None and "only in a shape that cannot admit" in indented, styled


def test_a_marker_inside_other_raw_html_cannot_start_a_grooming_block() -> None:
    """Codex on #462 (read of `f7c11d7`): `<div>`, marker, `</div>` is one raw HTML block to
    Markdown, and searching that block's text for the marker made the whole construct a grooming
    block - so a restriction above it was discarded and an admitting declaration below it
    governed, for a marker that was never on a line of its own. A marker block *begins* with the
    marker; a block that merely contains one is a misplaced marker and the body refuses outright.
    """
    marker = "<!-- tether-grooming-v1 -->"
    restrict = "## Execution autonomy\n\nmaintainer decision required\n\n"
    admit = "\n\n## Execution autonomy\n\nagent-can-do-alone\n"
    buried = claim._autonomy_refusal(restrict + f"<div>\n{marker}\n</div>" + admit)
    assert buried is not None, "a marker inside other raw HTML started a grooming block"
    assert "cannot start" in buried and "raw HTML" in buried, buried

    # On its own line the marker is its own block whatever raw HTML follows it, and the grooming
    # block it starts governs: the restriction above is superseded as the groomer intended.
    own_line = claim._autonomy_refusal(restrict + f"{marker}\n<div>note</div>" + admit)
    assert own_line is None, own_line


def test_visible_raw_html_is_read_and_comments_are_not() -> None:
    """Codex on #462 (read of `f7c11d7`): a `<details><summary>maintainer decision required
    </summary></details>` under an admitting heading is rendered by GitHub and was read by the
    regex scan, but the parser returns it as one raw HTML block and `_prose` made every HTML
    block empty, so the restriction vanished. What GitHub shows of raw HTML is prose; what it
    hides - a comment - is not; and a value written in raw HTML is read and exact-checked but is
    one more shape that cannot admit.
    """
    heading = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    details = "<details>\n<summary>maintainer decision required</summary>\n</details>\n"
    shown = claim._autonomy_refusal(heading + details)
    assert shown is not None, "a restriction rendered from raw HTML was dropped - fail-open"
    assert "maintainer decision" in shown and "heading remainder" in shown, shown

    hidden = claim._autonomy_refusal(heading + "<!-- maintainer decision required, once -->\n")
    assert hidden is None, hidden

    as_value = claim._autonomy_refusal("## Execution autonomy\n\n<p>agent-can-do-alone</p>\n")
    assert as_value is not None and "only in a shape that cannot admit" in as_value, as_value
    unregistered = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n\n## Execution autonomy\n\n"
        "<p>human review required</p>\n"
    )
    assert unregistered is not None and "not a registered" in unregistered, unregistered


def test_a_heading_admits_only_on_its_own_next_paragraph() -> None:
    """Codex on #462 (read of `c4ee870`): `## Execution autonomy` over `> agent-can-do-alone`
    admitted. The flattener opens block quotes and list items so a heading's section is read in
    document order, and that erased the one boundary this decision needs: the quoted paragraph
    became the heading's value and the registered shape it is not. A leaf now names its container,
    and a heading admits only when its value is a paragraph in the **same** container. The quoted
    or listed value is still read - exact-checked, able to refuse - and the message names the shape
    rather than claiming the declaration is absent.
    """
    for where, body in {
        "quoted": "## Execution autonomy\n\n> agent-can-do-alone\n",
        "listed": "## Execution autonomy\n\n- agent-can-do-alone\n",
        "quoted after a blank": "## Execution autonomy\n\n\n> agent-can-do-alone\n",
    }.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None, f"{where}: a value inside a container admitted - fail-open"
        assert "only in a shape that cannot admit" in refusal, f"{where}: {refusal}"
        assert "agent-can-do-alone" in refusal and "heading" in refusal, f"{where}: {refusal}"

    # Read, not ignored: a quoted restriction under the heading still governs.
    quoted = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n\n## Execution autonomy\n\n"
        "> maintainer decision required\n"
    )
    assert quoted is not None and "maintainer decision" in quoted, quoted

    # The registered shape is untouched, blank lines and all.
    assert claim._autonomy_refusal("## Execution autonomy\n\n\n\nagent-can-do-alone\n") is None


def test_inline_html_is_invisible_to_the_key_as_it_is_on_the_page() -> None:
    """Codex on #462 (read of `c4ee870`): `- **Auto<!-- note -->nomy:** maintainer decision
    required` in a grooming block renders as a plain restrictive bullet, but the parser keeps the
    inline comment in the paragraph's text, so the key never matched and the bullet was not read.
    Keys are now matched on the rendered text: a comment leaves nothing behind, as on the page,
    and a tag leaves a space - `<b>Autonomy:</b>` is the key, and `maintainer</li><li>decision`
    is still two words. Prose that merely looks like a tag is prose. A tag is read so that a
    restriction dressed in one is found; since the read of `434dfff` it is never the reason an
    issue is claimed, so the admitting shapes below are shapes that cannot admit.
    """
    marker = "<!-- tether-grooming-v1 -->"
    split = f"{marker}\n- **Auto<!-- note -->nomy:** maintainer decision required\n"
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    refusal = claim._autonomy_refusal(split + admitting)
    assert refusal is not None, "a key split by an inline comment was not read - fail-open"
    assert "maintainer decision" in refusal and "grooming block bullet" in refusal, refusal
    assert claim._autonomy_refusal("- **Auto<!-- note -->nomy:** agent-can-do-alone\n") is None
    # Codex on #462 (read of `5f41bae`): a processing instruction, a declaration and a CDATA
    # section are hidden as a comment is, so a key split by one is the key the page shows.
    for hidden in ("<?note?>", "<!DOCTYPE x>", "<![CDATA[y]]>"):
        split = claim._autonomy_refusal(
            f"- **Auto{hidden}nomy:** maintainer decision required\n" + admitting
        )
        assert split is not None, f"{hidden!r}: a key split by hidden HTML was not read"
        assert "maintainer decision" in split, f"{hidden!r}: {split}"
        assert claim._autonomy_refusal(f"- **Auto{hidden}nomy:** agent-can-do-alone\n") is None
    # On its own line each draws nothing and is read past, as a comment block is.
    past = claim._autonomy_refusal("## Execution autonomy\n\n<?xml?>\n\nagent-can-do-alone\n")
    assert past is None, past

    tagged = claim._autonomy_refusal(
        "- <b>Autonomy:</b> maintainer decision required\n" + admitting
    )
    assert tagged is not None and "maintainer decision" in tagged, tagged
    in_tag = claim._autonomy_refusal("- **Autonomy:** <b>agent-can-do-alone</b>\n")
    assert in_tag is not None and "only in a shape that cannot admit" in in_tag, in_tag

    # Greptile on #462 (read of `4b61fb0`): a phrasing tag *inside* the key was read as a space,
    # so `Auto<b>nomy</b>:` was two words, not the key, and the restriction beside it was dropped.
    # The page shows one word; so does the gate.
    split_by_tag = claim._autonomy_refusal(
        "- **Auto<b>nomy</b>:** human review required\n" + admitting
    )
    assert split_by_tag is not None, "a key split by a phrasing tag was not read - fail-open"
    assert "human review required" in split_by_tag and "bullet" in split_by_tag, split_by_tag
    key_in_tag = claim._autonomy_refusal("- **Auto<b>nomy</b>:** agent-can-do-alone\n")
    assert key_in_tag is not None and "HTML tag" in key_in_tag, key_in_tag

    # A comment in the value is hidden on the page and hidden here; a token the page shows across
    # a phrasing tag, or a `<br>`, is still a token; `<` in prose is not a tag.
    heading = "## Execution autonomy\n\nagent-can-do-alone"
    hidden = claim._autonomy_refusal(heading + " <!-- was: maintainer decision required -->\n")
    assert hidden is None, hidden
    for shown_whole in (
        "<p>needs <b>maintainer</b> <i>decision</i></p>",
        "<p>needs maintainer<br>decision</p>",
    ):
        across = claim._autonomy_refusal(heading + f"\n\n{shown_whole}\n")
        assert across is not None and "maintainer decision" in across, f"{shown_whole}: {across}"
    # Codex on #462 (read of `aa47973`): raw HTML is read one rendered block at a time, so two
    # `<li>` are two items on the page and here, as their Markdown spelling already was - a
    # token governs where it is written, and nobody wrote this one.
    for two_blocks in (
        "<ul><li>needs maintainer</li><li>decision</li></ul>",
        "- needs maintainer\n- decision",
    ):
        assert claim._autonomy_refusal(heading + f"\n\n{two_blocks}\n") is None, two_blocks
    angle = claim._autonomy_refusal(
        heading + "\n\nneeds a maintainer decision if n < 5 and m > 3\n"
    )
    assert angle is not None and "maintainer decision" in angle, angle

    # The marker is a comment too, and prose hides it - so the misplaced-marker scan reads the
    # HTML the parser found. Codex on #462 (read of `f9fc1f0`): not the source, since a code
    # span quoting the marker is text on the page and refused a correctly groomed issue.
    inline = claim._autonomy_refusal(f"text {marker} more\n\n" + heading + "\n")
    assert inline is not None and "cannot start" in inline, inline
    for quoting in (
        f"Use `{marker}` when re-grooming.\n",
        f"| step |\n|---|\n| post `{marker}` |\n",
        f"## Use `{marker}` to re-groom\n",
        f"\\{marker}\n",
    ):
        quoted = claim._autonomy_refusal(heading + "\n\n" + quoting)
        assert quoted is None, f"{quoting!r}: {quoted}"
        groomed = claim._autonomy_refusal(f"{marker}\n\n{heading}\n\n{quoting}")
        assert groomed is None, f"{quoting!r} in the grooming block: {groomed}"


def test_keys_and_values_are_matched_on_the_rendered_text_not_the_source() -> None:
    """Codex on #462 (read of `7f3a250`): `## [Execution autonomy](https://example.test)` over
    `maintainer decision required` beside a plain admitting bullet was claimable. The gate had
    stripped HTML from the source itself, and the link syntax hid the key from it while GitHub
    shows the heading *Execution autonomy*. Keys and values are now read from the parser's own
    rendering of the inline tokens (``plain``, ADR-0066) rather than from a pattern over the
    source, so a link is its text, an entity and an escape are decoded, and the same shape admits
    when it is the registered one.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    linked = "## [Execution autonomy](https://example.test)\n\nmaintainer decision required\n"
    refusal = claim._autonomy_refusal(admitting + linked)
    assert refusal is not None, "a linked restrictive heading was not read - fail-open"
    assert "maintainer decision" in refusal and "heading" in refusal, refusal
    assert (
        claim._autonomy_refusal("## [Execution autonomy](https://x)\n\nagent-can-do-alone\n")
        is None
    )

    # A link in the value is its text; an entity in the key is decoded. Neither is a shape the
    # forms write; the point is that no two spellings GitHub renders alike are read differently.
    assert (
        claim._autonomy_refusal("## Execution autonomy\n\n[agent-can-do-alone](https://x)\n")
        is None
    )
    entity = claim._autonomy_refusal(
        admitting + "## Execution&nbsp;autonomy\n\nhuman review required\n"
    )
    assert entity is not None and "human review required" in entity, entity


def test_a_one_column_autonomy_table_is_a_declaration_with_an_empty_value() -> None:
    """Codex on #462 (read of `7f3a250`): `| Autonomy |\\n| --- |` is a valid one-column table whose
    row is keyed `autonomy` and has no cell after the key, and the table branch recorded nothing
    for it - so beside an admitting heading the issue was claimable on a recognised autonomy row
    holding no value at all. A row with no value cell now records an empty value, which fails the
    same exact check every other table declaration faces.
    """
    heading = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    table = "| Autonomy |\n| --- |\n"
    refusal = claim._autonomy_refusal(heading + table)
    assert refusal is not None, "a one-column autonomy table was ignored - fail-open"
    assert "not a registered" in refusal and "table row" in refusal, refusal
    alone = claim._autonomy_refusal(table)
    assert alone is not None and "not a registered" in alone, alone

    # A body row under that header is a row of its own, keyed by its first cell: a registered
    # token there is a one-cell row keyed by the token, which is no autonomy row, and the header
    # row above it still refuses on its own.
    refusal = claim._autonomy_refusal(heading + table + "| agent-can-do-alone |\n")
    assert refusal is not None and "not a registered" in refusal, refusal


def test_only_the_latest_grooming_block_governs() -> None:
    """Codex on #462 (read of `7f3a250`): a body with two grooming markers was read as the union
    of both blocks, so a first block's `- **Autonomy:** agent-can-do-alone` admitted an issue whose
    latest pass declared only `- **Status:** ready`. A grooming block supersedes the body because
    it is the later statement; a second block is later again and alone governs. The latest pass
    saying nothing is silence, and silence refuses - the precedence the sibling tests establish
    for the body, applied between blocks too.
    """
    marker = "<!-- tether-grooming-v1 -->\n"
    first = marker + "- **Autonomy:** agent-can-do-alone\n\n"
    silent = claim._autonomy_refusal(first + marker + "- **Status:** ready\n")
    assert silent is not None, "an earlier grooming block's declaration governed - fail-open"
    assert "declares no Execution autonomy" in silent and "its grooming block" in silent, silent

    restricted = claim._autonomy_refusal(
        first + marker + "- **Autonomy:** maintainer decision required\n"
    )
    assert restricted is not None and "maintainer decision" in restricted, restricted

    # The other direction too: the latest pass admitting is what counts, not an earlier refusal.
    relaxed = claim._autonomy_refusal(
        marker + "- **Autonomy:** maintainer decision required\n\n" + first
    )
    assert relaxed is None, relaxed


def test_a_marker_that_shares_its_line_with_visible_text_is_not_on_its_own_line() -> None:
    """Codex on #462 (read of `f3a58e3`): a comment block runs to the end of the line that closes
    it, so `<!-- tether-grooming-v1 --> maintainer decision required` is one block that begins
    with the marker. Reading it as the marker discarded the restriction the page shows beside it,
    and an admitting bullet below became the only declaration. A marker block must show nothing;
    one that shows text is a marker where a grooming block cannot start, and refuses the body.
    Codex on #462 (read of `93ed23d`): nor may it *draw* anything - `<img>` beside the marker,
    or an empty `<details>` - which renders to no text and was taken for the marker, so a
    restriction above it was discarded for an admitting bullet below.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    beside = claim._autonomy_refusal(
        "<!-- tether-grooming-v1 --> maintainer decision required\n" + admitting
    )
    assert beside is not None, "text beside the marker was discarded - fail-open"
    assert "cannot start" in beside, beside
    # A marker followed only by another comment still shows nothing and is still the marker.
    nested = claim._autonomy_refusal(
        admitting + "\n<!-- tether-grooming-v1 --><!-- nothing to report -->\n"
    )
    assert nested is not None and "its grooming block" in nested, nested
    restrictive = "- **Autonomy:** maintainer decision required\n\n"
    for drawn in ('<img src="x">', "<details></details>", "<br>", "<b></b>"):
        beside = claim._autonomy_refusal(
            f"{restrictive}<!-- tether-grooming-v1 -->{drawn}\n\n{admitting}"
        )
        assert beside is not None, f"{drawn!r}: a marker beside something drawn was the marker"
        assert "cannot start" in beside, f"{drawn!r}: {beside}"


def test_a_quoted_attribute_value_does_not_end_a_tag_early() -> None:
    """Codex on #462 (read of `f3a58e3`): a `>` inside a quoted attribute value stopped the tag
    matcher, so `<b title="a>b">` was left in the rendered text, the key was not the key and the
    restriction under it was dropped. The tag grammar is CommonMark's, quoted values included.
    """
    refusal = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n"
        '- **Auto<b title="a>b">nomy</b>:** maintainer decision required\n'
    )
    assert refusal is not None, "a key wrapped in a quoted-attribute tag was not read - fail-open"
    assert "maintainer decision" in refusal, refusal


def test_a_recognised_heading_with_nothing_below_it_declares_an_empty_value() -> None:
    """Codex on #462 (read of `f3a58e3`): `## Execution autonomy` as the last line of a body, or
    straight above another heading, had an empty section and recorded nothing, so an admitting
    bullet elsewhere carried the issue past a field naming no registered value. It is now an
    empty value, which fails the exact check as the one-column table's does.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for where, body in {
        "terminal": admitting + "## Execution autonomy\n",
        "under another heading": admitting + "## Execution autonomy\n\n## Notes\n\nprose\n",
        "over code only": admitting + "## Execution autonomy\n\n```\nagent-can-do-alone\n```\n",
    }.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None, f"{where}: an empty heading recorded nothing - fail-open"
        assert "not a registered" in refusal and "heading" in refusal, f"{where}: {refusal}"
    alone = claim._autonomy_refusal("## Execution autonomy\n")
    assert alone is not None and "not a registered" in alone, alone


def test_a_task_list_item_is_read_past_its_checkbox_and_cannot_admit() -> None:
    """Codex on #462 (read of `f3a58e3`): `- [ ] **Autonomy:** maintainer decision required`
    renders as a checkbox before a restrictive declaration, but the preset keeps `[ ]` as text,
    the key never matched and the restriction was dropped beside an admitting bullet. The
    checkbox is read past; the shape is one more that can refuse and cannot admit.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    for box in ("[ ]", "[x]", "[X]"):
        refusal = claim._autonomy_refusal(
            admitting + f"- {box} **Autonomy:** maintainer decision required\n"
        )
        assert refusal is not None, f"{box}: a task-list restriction was dropped - fail-open"
        assert "maintainer decision" in refusal, f"{box}: {refusal}"
        alone = claim._autonomy_refusal(f"- {box} **Autonomy:** agent-can-do-alone\n")
        assert alone is not None and "only in a shape that cannot admit" in alone, alone
        assert "task-list" in alone, alone
    # `[ ]` with no space after it is not a checkbox on the page, and not one here: the item's
    # text then starts with `[ ]`, which is not the key.
    literal = claim._autonomy_refusal("- [ ]**Autonomy:** agent-can-do-alone\n")
    assert literal is not None and "declares no Execution autonomy" in literal, literal


def test_a_q_element_is_the_quotation_marks_the_page_draws() -> None:
    """Codex on #462 (read of `f3a58e3`): `<q>` was in the phrasing set and vanished, so
    `**Auto<q></q>nomy:**` was read as the key while the page shows `Auto“”nomy:`. It now leaves
    the quotation marks the page draws, so a key defaced by one is not the key and a value inside
    one is still the value.
    """
    defaced = claim._autonomy_refusal("- **Auto<q></q>nomy:** agent-can-do-alone\n")
    assert defaced is not None and "declares no Execution autonomy" in defaced, defaced
    quoted = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n- **Autonomy:** <q>maintainer decision required</q>\n"
    )
    assert quoted is not None and "maintainer decision" in quoted, quoted


def test_raw_html_is_read_as_the_page_draws_it_in_three_more_ways() -> None:
    """Codex on #462 (read of `6993d3c`): three shapes the rendering rule still read wrongly.
    `<wbr>` is a break *opportunity* that draws nothing, so `Auto<wbr>nomy:` is the key and a
    space there dropped the restriction beside it. A character reference inside a raw block -
    `maintainer&#32;decision` - is a space on the page and was left undecoded, hiding the token
    from the remainder scan. And `<del>`, `<s>`, `<strike>` strike a value out, which is the
    retraction `~~x~~` already is, yet they vanished and the struck-out token admitted.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    wbr = claim._autonomy_refusal(admitting + "- **Auto<wbr>nomy:** maintainer decision required\n")
    assert wbr is not None, "a key split by <wbr> was not read - fail-open"
    assert "maintainer decision" in wbr, wbr
    # Codex on #462 (read of `0be2c31`): an empty `<picture>` draws nothing either.
    picture = claim._autonomy_refusal(
        admitting + "- **Auto<picture></picture>nomy:** maintainer decision required\n"
    )
    assert picture is not None, "a key split by <picture> was not read - fail-open"
    assert "maintainer decision" in picture, picture

    entity = claim._autonomy_refusal(
        "## Execution autonomy\n\nagent-can-do-alone\n\n"
        "<div>maintainer&#32;decision required</div>\n"
    )
    assert entity is not None, "an undecoded character reference hid the token - fail-open"
    assert "maintainer decision" in entity and "heading remainder" in entity, entity

    for tag in ("del", "s", "strike"):
        struck = claim._autonomy_refusal(f"- **Autonomy:** <{tag}>agent-can-do-alone</{tag}>\n")
        assert struck is not None, f"<{tag}>: a struck-out value admitted - fail-open"
        assert "not a registered" in struck and "~~agent-can-do-alone~~" in struck, struck
        # A struck-out restriction is still read: the token is in the text either way.
        restrictive = claim._autonomy_refusal(
            admitting + f"- **Autonomy:** <{tag}>maintainer decision required</{tag}>\n"
        )
        assert restrictive is not None and "maintainer decision" in restrictive, restrictive


def test_a_markup_character_the_page_shows_is_part_of_the_value() -> None:
    """Codex on #462 (read of `434dfff`): `agent\\*-can-do-alone` renders as `agent*-can-do-alone`,
    a star the page shows, yet the exact check still stripped `*` as emphasis and admitted it.
    The parser removes the markup that is markup; nothing is stripped after rendering, so a
    character that survives rendering is compared as the reader sees it.
    """
    for value in ("agent\\*-can-do-alone", "`agent-can-do-alone`\\*", "agent-can-do-alone\\`"):
        refusal = claim._autonomy_refusal(f"- **Autonomy:** {value}\n")
        assert refusal is not None, f"{value!r}: a shown markup character was stripped - fail-open"
        assert "not a registered" in refusal, f"{value!r}: {refusal}"
    # Markup that is markup is still gone: these are the corpus's own spellings. An underscore
    # is a separator the flattener widens, not markup, so `agent_can_do_alone` admits by design.
    assert claim._autonomy_refusal("- **Autonomy:** `agent-can-do-alone`.\n") is None
    assert claim._autonomy_refusal("- **Autonomy:** _agent-can-do-alone_\n") is None
    assert claim._autonomy_refusal("- **Autonomy:** agent_can_do_alone\n") is None


def test_a_key_admits_one_colon_and_never_two() -> None:
    """Codex on #462 (read of `434dfff`): `## Execution autonomy::` matched a key pattern with two
    independent optional colons and admitted the heading under it. One colon is tolerated, since
    `## Autonomy:` is a heading the corpus writes; a second is not the key. Since the read of
    `d6c16fc` the heading line is then read as a bullet is - the second colon starts a value on
    the line, which is not registered - where before the body was silent; either way it refuses.
    """
    assert claim._autonomy_refusal("## Autonomy:\n\nagent-can-do-alone\n") is None
    for body in (
        "## Execution autonomy::\n\nagent-can-do-alone\n",
        "## Autonomy: :\n\nagent-can-do-alone\n",
    ):
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None, "a doubled colon was still the key - fail-open"
        assert "not a registered" in refusal and "heading line" in refusal, refusal
    # A bullet's second colon is the start of its value, which is then not registered.
    doubled = claim._autonomy_refusal("- **Autonomy::** agent-can-do-alone\n")
    assert doubled is not None and "not a registered" in doubled, doubled


def test_scan_only_table_cells_are_scanned_one_at_a_time() -> None:
    """Codex on #462 (read of `434dfff`): a row in the remainder of an admitting heading's section
    had its cells joined before the token scan, so `| human | action items |` read as
    `human action` where nobody wrote it and refused a registered declaration - the block-boundary
    defect again, one level down. A cell is scanned on its own; a token inside one still governs.
    """
    heading = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    split = claim._autonomy_refusal(heading + "| a | b |\n|---|---|\n| human | action items |\n")
    assert split is None, split
    whole = claim._autonomy_refusal(heading + "| a | b |\n|---|---|\n| x | human action items |\n")
    assert whole is not None and "human action" in whole, whole


def test_a_misplaced_marker_above_the_latest_grooming_block_is_superseded_with_the_rest() -> None:
    """Codex on #462 (read of `434dfff`): a stale paragraph quoting the marker inline, above a
    later marker on its own line, refused a correctly re-groomed issue, because the misplaced
    check ran over the whole document rather than the authoritative source. The latest block
    supersedes everything above it, that paragraph included; inside the latest block a misplaced
    marker still refuses.
    """
    marker = "<!-- tether-grooming-v1 -->"
    superseded = claim._autonomy_refusal(
        f"Example: {marker}\n\n{marker}\n- **Autonomy:** agent-can-do-alone\n"
    )
    assert superseded is None, superseded
    inside = claim._autonomy_refusal(
        f"{marker}\n- **Autonomy:** agent-can-do-alone\n\n> {marker}\n> - **Status:** ready\n"
    )
    assert inside is not None and "grooming block cannot start" in inside, inside
    # With no grooming block at all the whole body is the source, as before.
    bare = claim._autonomy_refusal(f"Example: {marker}\n\n- **Autonomy:** agent-can-do-alone\n")
    assert bare is not None and "grooming block cannot start" in bare, bare


def test_nothing_inside_a_details_block_can_admit() -> None:
    """Codex on #462 (read of `434dfff`): a bullet between `<details>` and `</details>` is a
    top-level Markdown list on the parse and collapsed on the page, and it admitted. The lines
    between a `<details>` and its `</details>` are read for restrictions and may not admit;
    an unclosed `<details>` collapses the rest of the body.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    collapsed = f"<details>\n<summary>more</summary>\n\n{admitting}\n</details>\n"
    hidden = claim._autonomy_refusal(collapsed)
    assert hidden is not None, "a declaration inside <details> admitted - fail-open"
    assert "only in a shape that cannot admit" in hidden and "details" in hidden, hidden
    unclosed = claim._autonomy_refusal(f"<details>\n\n{admitting}")
    assert unclosed is not None and "only in a shape that cannot admit" in unclosed, unclosed
    heading = claim._autonomy_refusal(
        "<details>\n\n## Execution autonomy\n\nagent-can-do-alone\n\n</details>\n"
    )
    assert heading is not None and "only in a shape that cannot admit" in heading, heading
    # Read, not ignored: a restriction inside still governs, and a declaration after the block
    # closes is on the page again.
    restrictive = claim._autonomy_refusal(
        admitting + "\n<details>\n\n- **Autonomy:** maintainer decision required\n\n</details>\n"
    )
    assert restrictive is not None and "maintainer decision" in restrictive, restrictive
    after = claim._autonomy_refusal("<details>\n<summary>x</summary>\n</details>\n\n" + admitting)
    assert after is None, after
    # Codex on #462 (read of `0be2c31`): a `</details>` written inside a comment closes nothing
    # on the page, and closed the span here because the raw text was scanned. Tags are read with
    # comments removed first.
    commented = claim._autonomy_refusal(
        f"<details>\n<summary>x</summary>\n<!-- </details> -->\n\n{admitting}"
    )
    assert commented is not None, "a commented-out </details> ended the span - fail-open"
    assert "only in a shape that cannot admit" in commented, commented


def test_a_details_block_opened_above_the_marker_still_collapses_the_grooming_block() -> None:
    """Codex on #462 (read of `78687b5`): the collapsed spans were read off the grooming block
    alone, so a `<details>` opened above the marker and closed below the declaration was never
    counted, and a bullet the page keeps collapsed admitted. The spans are the whole document's:
    the marker supersedes what the body says, not where the page draws it.
    """
    marker = "<!-- tether-grooming-v1 -->"
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    hidden = claim._autonomy_refusal(
        f"<details>\n<summary>groomed</summary>\n\n{marker}\n\n{admitting}\n</details>\n"
    )
    assert hidden is not None, "a grooming block inside <details> admitted - fail-open"
    assert "only in a shape that cannot admit" in hidden and "grooming block" in hidden, hidden
    # Unclosed above the marker, the block runs to the end of the body as it does on the page.
    unclosed = claim._autonomy_refusal(f"<details>\n\n{marker}\n\n{admitting}")
    assert unclosed is not None and "only in a shape that cannot admit" in unclosed, unclosed
    # A block closed above the marker hides nothing below it.
    closed = claim._autonomy_refusal(
        f"<details>\n<summary>old</summary>\n</details>\n\n{marker}\n\n{admitting}"
    )
    assert closed is None, closed


def test_a_declaration_drawn_as_an_image_can_refuse_and_never_admit() -> None:
    """Codex on #462 (read of `78687b5`): `![agent-can-do-alone](url)` under the heading rendered
    to its alternative text and admitted, while the page draws a picture there and shows that
    text only when the picture fails to load. A key or value carrying an image is the tag rule
    from the other side - a shape that can refuse and never admit - with the alternative text
    kept so a restriction written there is still found.
    """
    for shape in (
        "## Execution autonomy\n\n![agent-can-do-alone](https://example.test/value.png)\n",
        "- **Autonomy:** ![agent-can-do-alone](https://example.test/value.png)\n",
        "- ![Autonomy:](https://example.test/key.png) agent-can-do-alone\n",
        # Reference-style, its definition elsewhere in the body: still a picture on the page.
        "## Execution autonomy\n\n![agent-can-do-alone][v]\n\n[v]: https://example.test/v.png\n",
    ):
        refusal = claim._autonomy_refusal(shape)
        assert refusal is not None, f"{shape!r}: a pictured declaration admitted"
        assert "only in a shape that cannot admit" in refusal and "image" in refusal, refusal
    # Not an image on the page, not an image here: the escape makes it a `!` and a link, and
    # the value the page shows is `!agent-can-do-alone`, which is not a registered token.
    escaped = claim._autonomy_refusal("## Execution autonomy\n\n\\![agent-can-do-alone](x)\n")
    assert escaped is not None and "'!agent-can-do-alone'" in escaped, escaped
    # The alternative text is still read for a restriction.
    through = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n"
        "- **Autonomy:** ![maintainer decision required](https://example.test/x.png)\n"
    )
    assert through is not None and "maintainer decision" in through, through


def test_an_empty_latest_grooming_block_is_the_source_and_supersedes_a_stale_marker() -> None:
    """Codex on #462 (read of `78687b5`): the misplaced-marker check fell back to the whole body
    when the latest grooming block was empty - the marker as the last line - because an empty
    tuple read as no block, and a stale inline marker above it then refused the body for the
    wrong reason, naming a fix already applied. The empty block is the source: it says nothing,
    and silence refuses as silence.
    """
    marker = "<!-- tether-grooming-v1 -->"
    silent = claim._autonomy_refusal(
        f"see {marker} above\n\n- **Autonomy:** agent-can-do-alone\n\n{marker}\n"
    )
    assert silent is not None and "declares no Execution autonomy" in silent, silent
    assert "its grooming block" in silent and "cannot start" not in silent, silent
    # With no block after it, the stale marker is the only one and is misplaced as before.
    bare = claim._autonomy_refusal(f"see {marker} above\n\n- **Autonomy:** agent-can-do-alone\n")
    assert bare is not None and "grooming block cannot start" in bare, bare


def test_a_headings_value_is_the_first_thing_the_page_draws_below_it() -> None:
    """Codex on #462 (read of `b2efdf1`), two P1s with one cause: the section below a heading
    kept only leaves with prose, so `![](value.png)` - a picture with no alternative text - and
    a `<details>` opening tag were skipped, and the paragraph after them admitted as the
    heading's own next paragraph while the page draws a picture, or a collapsed widget, in
    between. The value is the first leaf the page draws anything for, and only a comment block
    draws nothing; the value must be on the page too, not only the heading.
    """
    heading = "## Execution autonomy\n\n"
    for between in (
        "![](https://example.test/value.png)",
        '<img src="https://example.test/value.png">',
        "<details>\n<summary>more</summary>",
        "```\nexample\n```",
        "---",
        "| a | b |\n|---|---|\n| c | d |",
        "<b></b>",
    ):
        refusal = claim._autonomy_refusal(f"{heading}{between}\n\nagent-can-do-alone\n")
        assert refusal is not None, f"{between!r}: the paragraph past it admitted - fail-open"
        assert "heading" in refusal, f"{between!r}: {refusal}"
    # A comment block draws nothing and is read past, as a form's prompt relies on.
    assert claim._autonomy_refusal(f"{heading}<!-- pick one -->\n\nagent-can-do-alone\n") is None
    # Codex on #462 (read of `aa47973`): a container holding only such a comment is still
    # drawn - the quote's bar, the list's bullet - and flattening it to its undrawn leaf let
    # the paragraph below it admit as the heading's own next paragraph.
    for container in ("> <!-- note -->", "- <!-- note -->", "> - <!-- note -->", "-"):
        refusal = claim._autonomy_refusal(f"{heading}{container}\n\nagent-can-do-alone\n")
        assert refusal is not None, f"{container!r}: the paragraph past it admitted - fail-open"
        assert "heading" in refusal, f"{container!r}: {refusal}"
    # The value inside a `<details>` the heading sits above is not on the page.
    collapsed = claim._autonomy_refusal(f"{heading}<details>\n\nagent-can-do-alone\n\n</details>\n")
    assert collapsed is not None, "a collapsed heading value admitted - fail-open"
    assert "heading" in collapsed, collapsed


def test_a_details_opened_in_running_text_collapses_what_follows_it() -> None:
    """The HTML parser closes a paragraph where an inline `<details>` opens, and the widget takes
    everything up to its `</details>`, so a declaration below such a line is collapsed on the
    page - while the span scan read only raw HTML blocks. The inline HTML the parser found
    counts too; a `<details>` in a code span is text and opens nothing. Codex on #462 (read of
    `a1408d7`): one opened inside a heading is popped by the heading's own end tag, so it takes
    nothing past the heading - and the same holds of a list item, a block quote and a table
    cell, each of whose end tags pops what was opened inside it.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    inline = claim._autonomy_refusal(f"Notes <details>\n\n{admitting}\n</details>\n")
    assert inline is not None, "a declaration under an inline <details> admitted - fail-open"
    assert "only in a shape that cannot admit" in inline, inline
    literal = claim._autonomy_refusal(f"Notes `<details>`\n\n{admitting}")
    assert literal is None, literal
    # Closed in running text as well, the declaration after it is on the page again.
    closed = claim._autonomy_refusal(f"<details>\n\nmore </details>\n\n{admitting}")
    assert closed is None, closed
    # Opened inside an element with its own end tag, it ends with that element.
    for opener in (
        "## Notes <details>",
        "| a |\n|---|\n| <details> |",
        "- note <details>",
        "- <details>\n  <summary>x</summary>",
        "> <details>",
    ):
        bounded = claim._autonomy_refusal(f"{opener}\n\n{admitting}")
        assert bounded is None, f"{opener!r}: {bounded}"
    # A `</details>` written in a table cell cannot close one opened outside the table.
    cell = claim._autonomy_refusal(f"<details>\n\n| a |\n|---|\n| </details> |\n\n{admitting}")
    assert cell is not None and "only in a shape that cannot admit" in cell, cell
    # Codex on #462 (read of `93ed23d`): raw HTML has scopes of its own. A `<details>` opened
    # inside a `<td>`, an `<li>` or a `<div>` is popped by that element's end tag, so it takes
    # nothing past it; one in a cell that never closes runs to the end, as the parser's does.
    for scoped in (
        "<table><tr><td><details>note</td></tr></table>",
        "<ul><li><details>note</li></ul>",
        "<div><details>note</div>",
        "<table><tr><td><details><summary>x</summary>note</table>",
    ):
        popped = claim._autonomy_refusal(f"{scoped}\n\n{admitting}")
        assert popped is None, f"{scoped!r}: {popped}"
    unclosed = claim._autonomy_refusal(f"<table><tr><td><details>note\n\n{admitting}")
    assert unclosed is not None and "only in a shape that cannot admit" in unclosed, unclosed
    # Codex on #462 (read of `9fc6782`), one shape over: a tag GitHub strips is no scope, since
    # its end tag is stripped too and closes nothing on the page - `<section><details>...
    # </section>` leaves the widget open to the end of the body, so what follows is hidden.
    # A kept element around the stripped one still pops it.
    for stripped in ("section", "main", "article", "nav", "center", "fieldset", "dialog"):
        opened = claim._autonomy_refusal(
            f"<{stripped}>\n<details><summary>s</summary>\n</{stripped}>\n\n{admitting}"
        )
        assert opened is not None and "only in a shape that cannot admit" in opened, (
            stripped,
            opened,
        )
    assert (
        claim._autonomy_refusal(
            f"<table><tr><td><nav><details><summary>s</summary></nav></td></tr></table>\n\n{admitting}"
        )
        is None
    )
    assert claim._HTML_SCOPES < claim._markdown.BLOCK_TAGS and "section" not in claim._HTML_SCOPES
    # And a `</details>` inside a raw cell cannot close one opened outside the table, while
    # one inside a raw `<li>` can, a list item being no scope boundary.
    through = claim._autonomy_refusal(
        f"<details>\n\n<table><tr><td></details></td></tr></table>\n\n{admitting}"
    )
    assert through is not None and "only in a shape that cannot admit" in through, through
    assert (
        claim._autonomy_refusal(f"<details>\n\n<ul><li></details></li></ul>\n\n{admitting}") is None
    )


def test_closing_a_raw_details_pops_the_scopes_opened_inside_it() -> None:
    """Codex on #462 (read of `f488e46`): `<details><div>x</details><details>y</div>` over a
    registered bullet - the HTML parser pops the `div` with the first disclosure and ignores
    the unmatched `</div>`, so the second stays open over the bullet (GitHub's markdown
    endpoint, 2026-10-03). Here the `div` stayed on the scope stack, the `</div>` popped the
    second disclosure, and the hidden bullet was on the page. The end tag now pops every
    scope opened inside the disclosure it closes.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    for hidden in (
        f"<details><div>x</details><details>y</div>\n\n{admitting}",
        f"<details><div><span>x</details><details>y</span></div>\n\n{admitting}",
        f"<details>\n\n<div>x</details><details>y</div>\n\n{admitting}",
    ):
        read = claim._autonomy_refusal(hidden)
        assert read is not None and "only in a shape that cannot admit" in read, (hidden, read)
    # Controls: a scope opened before the disclosure survives its close, and a disclosure
    # closed inside an open `div` is closed.
    assert claim._autonomy_refusal(f"<div><details>x</details>y</div>\n\n{admitting}") is None
    assert (
        claim._autonomy_refusal(
            f"<div><details>x</details></div><details>y</details>\n\n{admitting}"
        )
        is None
    )


def test_raw_scopes_are_the_documents_and_outlive_the_block() -> None:
    """Codex on #462 (read of `22b148b`): `<details><table><tr><td>` ending at a blank line,
    then `</details>` in a later raw block - the cell is still open on the page, so the end
    tag is inside it and closes nothing, and a registered bullet below stays hidden (GitHub's
    markdown endpoint, 2026-10-03). The scope stack was dropped at the block's end, so the
    later block closed the disclosure and the bullet was on the page. The stack is the
    document's now, as the parser's is: raw or in running text, the end tag is inside the
    cell; closing the cell and the table first closes it; a `</div>` closes a disclosure
    opened inside the div and what follows is on the page; and a cell left open inside a
    list item swallows the item's own end tag, so a disclosure opened there runs on.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    for hidden in (
        f"<details><table><tr><td>\n\n</details>\n\n{admitting}",
        f"<details><table><tr><td>\n\nx </details>\n\n{admitting}",
        f"<details>\n\n<table><tr><td>\n\n</details>\n\n{admitting}",
        f"<details><table><tr><td>\n\n</td>\n\n</details>\n\n{admitting}",
        f"- <table><tr><td><details>note\n\n{admitting}",
        f"- <details><table><tr><td>note\n\n{admitting}",
        f"<div><details>\n\n{admitting}\n</div>\n",
    ):
        read = claim._autonomy_refusal(hidden)
        assert read is not None and "only in a shape that cannot admit" in read, (hidden, read)
    for shown in (
        f"<details><table><tr><td>\n\n</td></tr></table></details>\n\n{admitting}",
        f"<details><table><tr><td>\n\n</table></details>\n\n{admitting}",
        f"<div><details>\n\nx\n\n</div>\n\n{admitting}",
        f"<div>\n\n<details>\n\nx\n\n</div>\n\n{admitting}",
        f"- <table><tr><td><details>note</td></tr></table>\n\n{admitting}",
        f"<table><tr><td>\n\n</td></tr></table>\n\n<details>x</details>\n\n{admitting}",
    ):
        assert claim._autonomy_refusal(shown) is None, shown


def test_a_details_tag_in_a_footnote_acts_at_the_foot_and_not_in_the_body() -> None:
    """The mirror of Codex's read-34 finding on #462 (a continuation's raw HTML), found while
    fixing it: a footnote is drawn at the page's foot, after the whole body, so a tag in one
    acts there. `<details>` over `[^1]: x </details>` over a registered bullet hides the bullet
    on the page - the end tag closes the disclosure below the footnotes section - and the gate,
    reading the footnote in source order, closed it above the bullet and admitted (GitHub's
    markdown endpoint, 2026-10-03). A `<details>` opened in a footnote collapses the rest of
    the foot and nothing of the body. Neither is counted now.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    for hidden in (
        f"<details>\n\n[^1]: x </details>\n\n{admitting}\nSee[^1].\n",
        f"<details>\n\n- [^1]: x </details>\n\n{admitting}\nSee[^1].\n",
        f"<details>\n\n[^1]: x\n\n    </details>\n\n{admitting}\nSee[^1].\n",
        f"<details>\n\n[^1]: x\n\n    y </details>\n\n{admitting}\nSee[^1].\n",
    ):
        read = claim._autonomy_refusal(hidden)
        assert read is not None and "only in a shape that cannot admit" in read, (hidden, read)
    for shown in (
        f"See[^1].\n\n[^1]: note <details>\n\n{admitting}",
        f"See[^1].\n\n[^1]: note\n\n    <details>\n\n{admitting}",
        f"See[^1].\n\n[^1]: note\n\n    x <details>\n\n{admitting}",
    ):
        assert claim._autonomy_refusal(shown) is None, shown
    # Control: the same end tag in a body paragraph closes the disclosure where it is.
    assert claim._autonomy_refusal(f"<details>\n\nx </details>\n\n{admitting}") is None


def test_a_tag_github_strips_leaves_the_key_it_was_written_inside_whole() -> None:
    """Codex on #462 (read of `9fc6782`): GitHub sanitizes raw HTML before rendering, and an
    element it does not keep is removed with its text left in place, so `- **Auto<foo></foo>
    nomy:** maintainer decision required` shows an ordinary restrictive declaration - which the
    rendering read as `Auto nomy`, no key, and an admitting bullet beside it carried the issue.
    Rendered as the page renders it, the key is whole and the restriction governs; a tag in the
    key still bars admitting, as every tag does. A stripped block-ish tag - `<section>` - leaves
    a space on each side, as the sanitizer does, so the key it splits is split on the page too
    and the restriction beside it is still read as remainder text.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for split in (
        "- **Auto<foo></foo>nomy:** maintainer decision required\n",
        "- **Auto<center>nomy</center>:** maintainer decision required\n",
        "Auto<x/>nomy: maintainer decision required\n",
        "## Auto<foo></foo>nomy\n\nmaintainer decision required\n",
        "| Auto<foo></foo>nomy | maintainer decision required |\n|---|---|\n",
        "<p>Auto<foo></foo>nomy: maintainer decision required</p>\n",
    ):
        joined = claim._autonomy_refusal(admitting + split)
        assert joined is not None, f"{split!r}: a key a stripped tag splits was not read"
        assert "maintainer decision" in joined, f"{split!r}: {joined}"
    assert claim._autonomy_refusal("- **Auto<foo></foo>nomy:** agent-can-do-alone\n") is not None
    # The sanitizer's own spacing: text inside a stripped `<section>` is spaced, not joined.
    spaced = claim._autonomy_refusal(
        admitting + "- **Autonomy:** <section>agent-can-do-alone</section>maintainer decision\n"
    )
    assert spaced is not None and "maintainer decision" in spaced, spaced
    # And what the page joins is joined: `needsmaintainer` is one word there, naming nothing.
    assert claim._autonomy_refusal(admitting + "needs<foo></foo>maintainer review\n") is None


def test_a_footnote_is_read_behind_its_label_and_never_admits() -> None:
    """Codex on #462 (read of `9fc6782`): GitHub renders footnotes and the parser does not, so
    `[^1]: Autonomy: maintainer decision required` reached `_keyed` with the label in front of
    the key and was no declaration, while the page draws the restriction at its foot. The
    parser now marks the paragraph a footnote and renders the text behind the label, so it is
    read as any paragraph is; a definition whose text is one word is swallowed by CommonMark's
    reference rule and handed back the same way. And Codex on #462 (read of `f5c37f0`): the
    page draws a footnote at its foot, not where the definition sits, so it is in no heading's
    section and leads no item - `## Execution autonomy` over `[^1]: note` over
    `agent-can-do-alone` shows the registered value first, where taking the definition as the
    value refused the issue - and so a footnote never admits.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for footnote in (
        "See note[^1].\n\n[^1]: Autonomy: maintainer decision required\n",
        "[^1]: **Autonomy:** maintainer decision required\n",
        "[^note]: Autonomy:maintainer-decision-required\n",
        '[^1]: Autonomy: "maintainer decision required"\n',
        "[^1]: Autonomy:\n",
        "[^1]: Autonomy: human review required\n",
        "- [^1]: Autonomy: human review required\n",
        "> [^1]: Autonomy: human review required\n",
        "[^1]: Execution autonomy\n\nmaintainer decision required\n",
    ):
        footed = claim._autonomy_refusal(admitting + footnote)
        assert footed is not None, f"{footnote!r}: a footnote's declaration was not read"
    # A footnote can refuse and cannot admit, wherever it sits.
    for shape in (
        "[^1]: Autonomy: agent-can-do-alone\n",
        "- [^1]: Autonomy: agent-can-do-alone\n",
        "- [^1]: **Autonomy:** agent-can-do-alone\n",
        "## Execution autonomy\n\n[^1]: agent-can-do-alone\n",
        "## Execution autonomy\n\n[^1]: agent-can-do-alone extra\n",
        "**Execution autonomy**\n\n[^1]: agent-can-do-alone\n",
    ):
        unregistered = claim._autonomy_refusal(shape)
        assert unregistered is not None, f"{shape!r}: a footnote admitted"
        assert "only in a shape that cannot admit" in unregistered or "not a registered" in (
            unregistered
        ), f"{shape!r}: {unregistered}"
    # A footnote under the field's heading is not its value: the page draws it at the foot,
    # and the paragraph after it is the first thing under the heading. The definition is still
    # read for what it declares, wherever it sits, and a line opening one cuts the paragraph
    # above it as cmark-gfm does.
    for moved in (
        "## Execution autonomy\n\n[^1]: note\n\nagent-can-do-alone\n\nSee[^1].\n",
        "## Execution autonomy\n\n[^1]: Autonomy is discussed above.\n\nagent-can-do-alone\n",
        "**Execution autonomy**\n\n[^1]: note\n\nagent-can-do-alone\n" + admitting,
        "- [^1]: note\n\n" + admitting,
    ):
        assert claim._autonomy_refusal(moved) is None, moved
    for still in (
        "## Execution autonomy\n\n[^1]: Autonomy: maintainer decision required\n\n"
        "agent-can-do-alone\n",
        "## Execution autonomy\n\n[^1]: Autonomy: human review required\n\nagent-can-do-alone\n",
        admitting + "prose\n[^1]: Autonomy: human review required\n",
    ):
        read = claim._autonomy_refusal(still)
        assert read is not None and "footnote" in read, (still, read)
    # Codex on #462 (read of `270e6ae`): a reference whose label opens with `^` but is no
    # footnote label is a link definition, draws nothing, and is not an error.
    for link in ("[^release note]: /url\n", "[^]: /url\n"):
        assert claim._autonomy_refusal(admitting + link) is None, link
    # An escaped label is text on the page, and the key is then not the paragraph's start.
    assert claim._autonomy_refusal(admitting + "\\[^1]: Autonomy: human review required\n") is None
    # A footnote that is only prose is prose, and a label with nothing after it draws nothing.
    for prose in ("[^1]: Autonomy is discussed above.\n", "[^1]:\n", "See note[^1].\n"):
        assert claim._autonomy_refusal(admitting + prose) is None, prose
    # The checkbox is still read past, and a bullet carrying it still cannot admit.
    assert claim._autonomy_refusal("- [ ] **Autonomy:** agent-can-do-alone\n") is not None
    assert claim._autonomy_refusal(admitting + "- [x] Autonomy: human review required\n")


def test_a_stripped_elements_text_is_on_the_page_and_an_empty_strike_draws_nothing() -> None:
    """Codex on #462 (read of `f5c37f0`) held, from Selma's source, that the sanitizer removes
    `<svg>`, `<math>` and `<noscript>` with their text, and read 29 encoded it. GitHub's
    markdown endpoint (2026-10-03) renders `<svg>maintainer decision required</svg>` as that
    paragraph, so the three are stripped tags like any other, text kept - and the rule hid a
    restriction the page shows: `## Execution autonomy` over `agent-can-do-alone` over that
    element admitted on `270e6ae`, as did the element in a bullet's remainder. Reversed at
    read 31, where Codex found the pattern mis-reading a quoted `>` as well. An empty `<del>`
    draws nothing, so `Auto<del></del>nomy:` is the key - where `~~~~` was drawn for it - and
    so is `Auto<del title=">"></del>nomy:`, the opening tag read by the shared attribute
    grammar (Codex on #462, read of `c02ab15`). A `</details>` inside an `<svg>` closes the
    widget, since the page keeps it. `<script>` and its kin are literal text on GitHub, text
    and all, so a key they split is split there too; a `<del>` with text still strikes it
    out; and an empty `<q>` still defaces the key.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    section = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    # The text inside a stripped element is on the page, so a restriction there governs.
    for shown in (
        section + "<svg>maintainer decision required</svg>\n",
        section + "note <svg>maintainer decision required</svg>\n",
        section + "<svg>maintainer decision required\n",
        admitting + "  <math>maintainer decision required</math>\n",
        admitting + "  <noscript>maintainer decision required</noscript>\n",
    ):
        kept = claim._autonomy_refusal(shown)
        assert kept is not None and "maintainer decision" in kept, (shown, kept)
    # A key or value a stripped element splits is not plain Markdown, and cannot admit.
    for split in (
        "- **Auto<svg>x</svg>nomy:** agent-can-do-alone\n",
        "- **Autonomy:** <svg>agent-can-do-alone</svg>\n",
        "- **Autonomy:** agent-<svg>can</svg>-do-alone\n",
    ):
        assert claim._autonomy_refusal(split) is not None, split
    # An empty strike element draws nothing and a self-closing stripped tag holds nothing, so
    # the key is whole and the restriction behind it governs.
    for whole in (
        "- **Auto<del></del>nomy:** maintainer decision required\n",
        "Auto<s></s>nomy: maintainer decision required\n",
        "## Auto<strike></strike>nomy\n\nmaintainer decision required\n",
        '- **Auto<del title=">"></del>nomy:** maintainer decision required\n',
        "- **Auto<svg/>nomy:** maintainer decision required\n",
        'Auto<svg viewBox="0 0 1 1" />nomy: maintainer decision required\n',
        '- **Auto<svg title=">"/>nomy:** maintainer decision required\n',
        "<p>Auto<math/>nomy: maintainer decision required</p>\n",
    ):
        read = claim._autonomy_refusal(admitting + whole)
        assert read is not None and "maintainer decision" in read, (whole, read)
    # A `</details>` inside an `<svg>` closes the widget on the page, so the bullet after it
    # is drawn and registered - where one inside a comment closes nothing.
    assert claim._autonomy_refusal(f"<details>\n\n<svg></details></svg>\n\n{admitting}") is None
    assert (
        claim._autonomy_refusal(f"Notes <details>\n\nmore <svg></details></svg>\n\n{admitting}")
        is None
    )
    hidden = claim._autonomy_refusal(f"<details>\n\n<!-- </details> -->\n\n{admitting}")
    assert hidden is not None and "only in a shape that cannot admit" in hidden, hidden
    # Controls: struck text is struck, an empty `<q>` defaces, a literal `<script>` splits.
    struck = claim._autonomy_refusal(
        "- **Autonomy:** <del>maintainer decision</del> agent-can-do-alone\n"
    )
    assert struck is not None and "maintainer decision" in struck, struck
    assert (
        claim._autonomy_refusal(admitting + "Auto<q></q>nomy: maintainer decision required\n")
        is None
    )
    assert (
        claim._autonomy_refusal(
            admitting + "Auto<script>x</script>nomy: maintainer decision required\n"
        )
        is None
    )


def test_a_footnote_above_the_marker_is_read_with_the_grooming_block() -> None:
    """Codex on #462 (read of `c02ab15`): the page draws a footnote at its foot wherever the
    definition sits, so `[^1]: Autonomy: maintainer decision required` above the marker and
    `agent-can-do-alone[^1]` inside the block shows the restriction - while the block, being
    the blocks after the marker, left the definition with the superseded text and the issue
    admitted. Every footnote of the body is read with the block. One the block never
    references is read too: a footnote only refuses, so that costs a refusal on a body the
    groomer re-grooms, never a claim, and it needs no reader of references on the source.
    """
    marker = "<!-- tether-grooming-v1 -->\n\n"
    plain = "- **Autonomy:** agent-can-do-alone\n"
    groomed = plain + "\nSee[^1].\n"
    for above in (
        "[^1]: Autonomy: maintainer decision required\n\n",
        "[^1]: **Execution autonomy:** maintainer decision required\n\n",
        "- note\n\n  [^1]: Autonomy: maintainer decision required\n\n",
        "[^1]: Autonomy: needs-maintainer-input\n\n",
    ):
        read = claim._autonomy_refusal(above + marker + groomed)
        assert read is not None and ("maintainer decision" in read or "maintainer input" in read)
    # Referenced only from the superseded text, or from nowhere: read all the same.
    for unreferenced in (
        "[^1]: Autonomy: maintainer decision required\n\nSee[^1].\n\n",
        "[^1]: Autonomy: maintainer decision required\n\n",
    ):
        read = claim._autonomy_refusal(unreferenced + marker + plain)
        assert read is not None and "maintainer decision" in read, (unreferenced, read)
    # An admitting footnote above the marker admits nothing: the block says nothing.
    assert claim._autonomy_refusal("[^1]: Autonomy: agent-can-do-alone\n\n" + marker) is not None
    assert claim._autonomy_refusal(marker + plain) is None


def test_a_foot_marker_above_the_block_is_misplaced() -> None:
    """Codex on #462 (read of `f53c44a`): `[^1]: <!-- tether-grooming-v1 -->` above the latest
    top-level marker is a marker drawn at the foot, where a grooming block cannot start, and
    the declarations read the body's footnotes with the block while the misplaced-marker
    check read the block alone - so the foot marker was never seen and an admitting bullet
    in the block carried the issue. One source feeds both readers (`_source`).
    """
    marker = "<!-- tether-grooming-v1 -->\n\n"
    plain = "- **Autonomy:** agent-can-do-alone\n"
    for above in (
        "[^1]: <!-- tether-grooming-v1 -->\n\n",
        "[^1]: note <!-- tether-grooming-v1 --> here\n\n",
        "[^1]: <div><!-- tether-grooming-v1 --></div>\n\n",
        "[^1]: a\n\n    <!-- tether-grooming-v1 -->\n\n",
        "- note\n\n  [^1]: <!-- tether-grooming-v1 -->\n\n",
        "> [^1]: <!-- tether-grooming-v1 -->\n\n",
    ):
        for groomed in (plain, plain + "\nSee[^1].\n"):
            read = claim._autonomy_refusal(above + marker + groomed)
            assert read is not None and "marker inside" in read, (above, groomed, read)
    # Controls: a footnote above the block with no marker in it is read for what it says, and
    # the body alone with a foot marker is refused as before.
    assert claim._autonomy_refusal("[^1]: see also\n\n" + marker + plain) is None
    body = claim._autonomy_refusal(plain + "\n[^1]: <!-- tether-grooming-v1 -->\n")
    assert body is not None and "marker inside" in body, body


def test_a_marker_inside_a_tag_attribute_is_no_marker() -> None:
    """Codex on #462 (read of `c3181fb`): `<img alt="<!-- tether-grooming-v1 -->">` is one
    run of inline HTML to the parser, and searching the run for the marker found it inside
    the attribute - a picture's alternative text on the page, no comment - and refused the
    body for a marker it does not carry. The run is searched with its tags gone; a comment
    beside a tag, even one whose attribute holds `-->`, is still the marker it is.
    """
    plain = "- **Autonomy:** agent-can-do-alone\n\n"
    for quoted in (
        '<img alt="<!-- tether-grooming-v1 -->" src="x">\n',
        'see <img alt="<!-- tether-grooming-v1 -->" src="x"> here\n',
        '<div title="<!-- tether-grooming-v1 -->">x</div>\n',
        '| a | b |\n|---|---|\n| <img alt="<!-- tether-grooming-v1 -->"> | c |\n',
        '- <span title="<!-- tether-grooming-v1 -->">note</span>\n',
    ):
        assert claim._autonomy_refusal(plain + quoted) is None, quoted
    for marker in (
        '<img alt="-->" src="x"><!-- tether-grooming-v1 -->\n',
        "<p><!-- tether-grooming-v1 --></p>\n",
        "see <!-- tether-grooming-v1 --> here\n",
        '<img alt="<!-- tether-grooming-v1 -->"><!-- tether-grooming-v1 -->\n',
        '<!-- <img alt=" --> x"><!-- tether-grooming-v1 -->\n',
    ):
        read = claim._autonomy_refusal(plain + marker)
        assert read is not None and "marker inside" in read, (marker, read)
    # A tag inside a comment is the comment's text, and a comment the marker's text does not
    # fill whole is no marker - as the block form of it is none.
    for comment in (
        "<!-- tether-grooming-v1 <b> -->\n",
        "see <!-- tether-grooming-v1 <b> --> here\n",
        "<!-- tether-<b></b>grooming-v1 -->\n",
    ):
        assert claim._autonomy_refusal(plain + comment) is None, comment
    # Codex on #462 (read of `eb049c0`): the marker's bytes inside a processing instruction, a
    # CDATA section or a declaration - a bogus comment to the page, ending at its first `>` -
    # are no comment and no marker; a comment after that `>` is the comment it is.
    for hidden in (
        "<?note <!-- tether-grooming-v1 --> ?>\n",
        "see <?note <!-- tether-grooming-v1 --> ?> here\n",
        "<![CDATA[<!-- tether-grooming-v1 -->]]>\n",
        "see <![CDATA[<!-- tether-grooming-v1 -->]]> here\n",
        "<!DOCTYPE <!-- tether-grooming-v1 --> >\n",
        "see <!DOCTYPE <!-- tether-grooming-v1 --> > here\n",
    ):
        assert claim._autonomy_refusal(plain + hidden) is None, hidden
    for marker in (
        "<?x ?><!-- tether-grooming-v1 -->\n",
        "<?x > <!-- tether-grooming-v1 --> ?>\n",
        "<![CDATA[x]]><!-- tether-grooming-v1 -->\n",
        "<!--><!-- tether-grooming-v1 -->\n",
        "<!-- x --!><!-- tether-grooming-v1 --> -->\n",
    ):
        read = claim._autonomy_refusal(plain + marker)
        assert read is not None and "marker inside" in read, (marker, read)
    # Found beside it: `<!-->` is an empty comment to the page, so a key after one heads its
    # line and is read, and an unclosed comment block hides the rest of the body, so a body
    # that is one declares nothing.
    read = claim._autonomy_refusal("- <!-->Autonomy: maintainer decision required\n" + plain)
    assert read is not None and "maintainer decision" in read, read
    read = claim._autonomy_refusal("<!-- x\n\n" + plain)
    assert read is not None and "maintainer decision" not in read and "declar" in read, read
    # And one a `<div>` block opens hides the rest of the document the same way, the bullet
    # after it with it; closed, it hides nothing.
    read = claim._autonomy_refusal("<div>\n<!-- x\n</div>\n\n" + plain)
    assert read is not None and "declar" in read, read
    assert claim._autonomy_refusal("<div>\n<!-- x -->\n</div>\n\n" + plain) is None
    # Codex on #462 (read of `97662e4`): an unclosed opener inside a quoted attribute value is
    # the tag's, and the restriction after the tag is drawn and read.
    for attributed in (
        '<span title="<!--">Autonomy: maintainer decision required</span>\n',
        '- <span title="<!--">Autonomy: maintainer decision required</span>\n',
        '<div title="<!--">Autonomy: maintainer decision required</div>\n',
        '<p title="<!--">Autonomy: maintainer decision required</p>\n',
    ):
        read = claim._autonomy_refusal(plain + attributed)
        assert read is not None and "maintainer decision" in read, (attributed, read)


def test_a_continuation_reads_a_reference_link_defined_in_the_body() -> None:
    """Codex on #462 (read of `97662e4`): `[Autonomy: human review required][ref]` indented
    under a footnote definition, with `[ref]` defined elsewhere in the body, draws the key on
    the page, where reading the continuation in an environment of its own left the brackets
    literal, the key unread, and an admitting bullet beside it carried the issue.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for body in (
        "[ref]: /u\n\n[^1]: note\n\n    [Autonomy: human review required][ref]\n",
        "[^1]: note\n\n    [Autonomy: human review required][ref]\n\n[ref]: /u\n",
        "[ref]: /u\n\n[^1]: /url\n\n    - [**Autonomy:** maintainer decision required][ref]\n",
    ):
        read = claim._autonomy_refusal(admitting + body)
        assert read is not None and "cannot admit" not in read, (body, read)
        assert "review required" in read or "maintainer decision" in read, (body, read)
    # Undefined, the brackets are literal on the page too, and nothing is read.
    assert claim._autonomy_refusal(admitting + "[^1]: note\n\n    [Autonomy: x][none]\n") is None


def test_a_comment_a_continuation_opens_hides_nothing_of_the_body() -> None:
    """Codex on #462 (read of `1cf54ed`): a raw block of a footnote's continuation is drawn at
    the page's foot, after every body block, so a comment it opens and never closes hides the
    rest of the foot and nothing of the body - and a restriction after the definition in the
    source is drawn, where the cut in source order dropped it and the admitting bullet above
    the definition carried the issue.
    """
    body = (
        "- **Autonomy:** agent-can-do-alone\n\n[^1]: note\n\n    <div><!-- x</div>\n\n"
        "- **Autonomy:** maintainer decision required\n"
    )
    read = claim._autonomy_refusal(body)
    assert read is not None and "maintainer decision" in read, read


def test_an_end_tag_that_closes_nothing_leaves_the_key_whole() -> None:
    """Codex on #462 (read of `1cf54ed`): `Auto</q>nomy: human review required` shows the key
    whole on the page, the stray end tag ignored by its tree builder, where a quotation mark
    drawn for it split the key and the restriction went unread beside an admitting bullet;
    the same for every end tag that draws something, inline and in a raw block.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for split in (
        "Auto</q>nomy: human review required\n",
        "- Auto</section>nomy: maintainer decision required\n",
        "- **Auto</div>nomy:** maintainer decision required\n",
        "<p>Auto</div>nomy: maintainer decision required</p>\n",
        "<div>Auto</li>nomy: maintainer decision required</div>\n",
    ):
        read = claim._autonomy_refusal(admitting + split)
        assert read is not None and "cannot admit" not in read, (split, read)
        assert "review required" in read or "maintainer decision" in read, (split, read)
    # An end tag that closes something open draws what it draws, and a `</p>` always does.
    assert (
        claim._autonomy_refusal(admitting + "<q>Auto</q>nomy: maintainer decision required\n")
        is None
    )
    assert (
        claim._autonomy_refusal(admitting + "Auto</p>nomy: maintainer decision required\n") is None
    )
    comments = claim._markdown.comments
    assert list(comments('a <img alt="<!-- x -->"> <!-- y --> b')) == ["<!-- y -->"]
    assert list(comments("<!-- a <b> --> <b>c</b>")) == ["<!-- a <b> -->"]
    assert list(comments('<!-- <img alt=" --> x">')) == ['<!-- <img alt=" -->']
    assert list(comments("<?a <!-- x --> ?> <!-- y -->")) == ["<!-- y -->"]
    assert list(comments("<![CDATA[<!-- x -->]]><!DOCTYPE <!-- x --> ><!-- y -->")) == [
        "<!-- y -->"
    ]
    assert list(comments("<?a <!-- x --> y")) == ["<!-- x -->"]


def test_a_marker_inside_another_comment_is_that_comments_text() -> None:
    """Codex on #462 (read of `56e3e65`): `<!-- note <!-- tether-grooming-v1 -->` is one
    comment to the page's tokenizer, the inner opener its data, so the page carries no
    marker and the body governs - where a search of the comment's text found the marker's
    bytes in it and refused an eligible issue for a misplaced marker. A comment is the marker
    when it is the marker whole; one beside other text, or inline, is misplaced as before.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for nested in (
        "<!-- note <!-- tether-grooming-v1 -->\n",
        "a <!-- note <!-- tether-grooming-v1 --> b\n",
        "<!-- <!-- tether-grooming-v1 --> -->\n",
        "<!-- tether-grooming-v1 <b> -->\n",
    ):
        assert claim._autonomy_refusal(admitting + nested) is None, nested
    for misplaced in (
        "<p><!-- tether-grooming-v1 --></p>\n",
        "a <!-- tether-grooming-v1 --> b\n",
        "<!-- x --><!-- tether-grooming-v1 -->\n",
    ):
        read = claim._autonomy_refusal(admitting + misplaced)
        assert read is not None and "marker inside" in read, (misplaced, read)


def test_a_bare_keys_section_is_read_once() -> None:
    """Codex on #462 (read of `56e3e65`): a body alternating thousands of bare `Autonomy`
    paragraphs with their values flattened its blocks and read its remainder once per key,
    quadratic, and four thousand blocks took tens of seconds near GitHub's limit - now a
    section walk stops at a leaf read already as an earlier key's remainder, the rest of it
    having been read then, and each container is flattened once. A raw key's section the
    same. The verdict is the first key's.
    """
    bodies = (
        "Autonomy\n\nmaintainer decision required\n\n" * 2000,
        "<p>Autonomy</p>\n\nmaintainer decision required\n\n" * 1000,
        "- Autonomy\n\n  maintainer decision required\n\n" * 1000,
    )
    for body in bodies:
        start = time.perf_counter()
        read = claim._autonomy_refusal(body)
        elapsed = time.perf_counter() - start
        assert read is not None and "maintainer decision" in read, read
        assert elapsed < 2, f"{elapsed:.1f}s for {len(body)} bytes"
    # The stop is at a leaf read already, never at the value: the second key's value is read.
    read = claim._autonomy_refusal("Autonomy\n\nx\n\nAutonomy\n\nmaintainer decision required\n")
    assert read is not None and "maintainer decision" in read, read
    read = claim._autonomy_refusal(
        "Autonomy\n\nx\n\n- Autonomy\n\n  maintainer decision required\n"
    )
    assert read is not None and "maintainer decision" in read, read


def test_an_end_tag_is_stopped_by_the_scope_the_page_gives_it() -> None:
    """Codex on #462 (read of `56e3e65`): `<ul><li><details><ul></li></ul>` leaves the widget
    open over everything after it, since the page's tree builder stops a `</li>` at a list
    opened inside the item (HTML5 "in list item scope"), where the walk past the inner list
    closed the item, popped the widget and put a hidden bullet on the page. Every end tag
    is scoped as the builder scopes it now, in the collapse walker and in the layout of a
    raw run, and a Markdown item's own end tag the same: `- <details><ul>` hides every
    bullet after it (GitHub's markdown endpoint, 2026-10-03).
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for hidden in (
        "<ul><li><details><ul></li></ul>",
        "<ol><li><details><ol></li></ol>",
        "- <details><ul>",
        "1. <details><ol>",
        "- a\n  <details><ul>\n- b",
    ):
        read = claim._autonomy_refusal(f"{hidden}\n\n{admitting}")
        assert read is not None and "only in a shape that cannot admit" in read, (hidden, read)
    # Closed where the builder acts on the end tag: no list in the way, a `</ul>` that closes
    # the outer list and everything in it, a `</dd>` or `</div>` a list does not stop, a
    # `</h3>` closing an open `<h2>`, and a Markdown item, quote or heading whose end the raw
    # element inside does not swallow.
    for shown in (
        "<ul><li><details><ol></li></ul>",
        "<ul><li><details><div></li></ul>",
        "<ul><li><details></li></ul>",
        "<dl><dd><details><ul></dd></dl>",
        "<div><details><ul></div></ul>",
        "<h2><details>x</h3>",
        "<ul><li><details><table><tr><td></li></td></tr></table></ul>",
        "- <details><div>",
        "> <details><ul>",
        "## x <details><ul>",
    ):
        assert claim._autonomy_refusal(f"{shown}\n\n{admitting}") is None, shown
    # In the layout of a run the same scoping leaves a key whole where an end tag the builder
    # ignores cut it, and the restriction after it is read: a `</li>` stopped by a list, a
    # `</div>`, `</blockquote>` or `</li>` stopped by a cell, a `</q>` or `</del>` stopped by
    # any block - and `</q>` closing nothing after the box closed the paragraph it opened in.
    for whole in (
        "<ul><li><ul>Auto</li>nomy: maintainer decision required</ul></ul>\n",
        "<ol><li><ol>Auto</li>nomy: maintainer decision required</ol></ol>\n",
        "<div><table><tr><td>Auto</div>nomy: maintainer decision required</td></tr></table>\n",
        "<blockquote><table><tr><td>Auto</blockquote>nomy: maintainer decision required"
        "</td></tr></table>\n",
        "<ul><li><table><tr><td>Auto</li>nomy: maintainer decision required"
        "</td></tr></table></ul>\n",
        "<q><div>Auto</q>nomy: maintainer decision required</div>\n",
        "<del><div>Auto</del>nomy: maintainer decision required</div>\n",
        "<q>a<div>b</div></q>Autonomy: maintainer decision required\n",
    ):
        read = claim._autonomy_refusal(admitting + whole)
        assert read is not None and "maintainer decision" in read, (whole, read)
    # And split where the builder acts on it.
    for split in (
        "<ul><li><div>Auto</li>nomy: maintainer decision required</div></ul>\n",
        "<h2><div>Auto</h3>nomy: maintainer decision required</div></h2>\n",
        "<dl><dd><ul>Auto</dd>nomy: maintainer decision required</ul></dl>\n",
    ):
        assert claim._autonomy_refusal(admitting + split) is None, split


def test_a_block_tag_in_a_paragraph_opens_a_block_the_page_reads() -> None:
    """Found beside Codex's read of `56e3e65` on #462: the page's tree builder closes a
    paragraph at a block tag in its running text and lays what follows out as a block of
    its own, so `a <div>Autonomy: maintainer decision required</div>` is the paragraph `a`
    and then a box the restriction heads - where reading the text as the one paragraph it
    is to Markdown put the key mid-line, and the restriction went unread beside an
    admitting bullet (GitHub's markdown endpoint, 2026-10-03). The pieces after the tag are
    read as a raw block's are, in a paragraph, a bullet's lead and a footnote; a tag that
    opens no block cuts nothing, and nothing after a tag admits.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for after in (
        "a <div>Autonomy: maintainer decision required</div>\n",
        "a <p>Autonomy: maintainer decision required\n",
        "a <h2>Autonomy: maintainer decision required</h2>\n",
        "a <li>Autonomy: maintainer decision required\n",
        "a <dd>Autonomy: maintainer decision required\n",
        "a <blockquote>Autonomy: maintainer decision required</blockquote>\n",
        "a <table><tr><td>Autonomy: maintainer decision required</td></tr></table>\n",
        "- a <div>Autonomy: maintainer decision required</div>\n",
        "**a** <div>**Autonomy:** maintainer decision required</div>\n",
        "- a\n\n  b <div>Autonomy: maintainer decision required</div>\n",
        "> a <div>Autonomy: maintainer decision required</div>\n",
        "[^1]: a <div>Autonomy: maintainer decision required</div>\n",
        "a <div><h2>Execution autonomy</h2></div>\n\nmaintainer decision required\n",
    ):
        read = claim._autonomy_refusal(admitting + after)
        assert read is not None and "maintainer decision" in read, (after, read)
    for whole in (
        "a <br>Autonomy: maintainer decision required\n",
        "a <b>Autonomy: maintainer decision required</b>\n",
        "a <td>Autonomy: maintainer decision required\n",
        "a `<div>`Autonomy: maintainer decision required\n",
    ):
        assert claim._autonomy_refusal(admitting + whole) is None, whole
    for cannot in (
        "a <div>Autonomy: agent-can-do-alone</div>\n",
        "- **Autonomy:** <div>agent-can-do-alone</div>\n",
        "- a <div>**Autonomy:** agent-can-do-alone</div>\n",
    ):
        read = claim._autonomy_refusal(cannot)
        assert read is not None and "cannot admit" in read, (cannot, read)


def test_a_table_part_outside_a_table_is_no_boundary() -> None:
    """Found beside the end-tag rule on #462: the page's tree builder ignores a table-part tag
    with no table open and a void element's end tag, so `<td>Auto</td>nomy: maintainer
    decision required` and `<hr>Auto</hr>nomy: ...` show the key whole and the restriction
    with it, where cutting at the tag split the key and an admitting bullet beside it was
    admitted; and `<td><details>note</td>` leaves the widget open to the end of the body,
    the bullet below it hidden, where the `</td>` popped it (GitHub's markdown endpoint,
    2026-10-03). Inside a table the parts are the cells the page draws, and a cell's end tag
    pops what was opened in it.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for whole in (
        "<td>Auto</td>nomy: maintainer decision required\n",
        "<th>Auto</th>nomy: maintainer decision required\n",
        "<tr>Auto</tr>nomy: maintainer decision required\n",
        "<thead>Auto</thead>nomy: maintainer decision required\n",
        "<tbody>Auto</tbody>nomy: maintainer decision required\n",
        "<tfoot>Auto</tfoot>nomy: maintainer decision required\n",
        "<caption>Auto</caption>nomy: maintainer decision required\n",
        "<td>Auto<td>nomy: maintainer decision required\n",
        "<div>Auto<td>nomy: maintainer decision required</div>\n",
        "<table></table><td>Auto</td>nomy: maintainer decision required\n",
        "<table><tr><td>a</td></tr></table>\n\n<td>Auto</td>nomy: maintainer decision required\n",
        "<hr>Auto</hr>nomy: maintainer decision required\n",
        "- Auto<td>nomy: maintainer decision required\n",
    ):
        read = claim._autonomy_refusal(admitting + whole)
        assert read is not None and "maintainer decision" in read, (whole, read)
    # Inside a table the parts are cells, drawn apart, and a key split across two is none.
    cells = "<table><tr><td>Auto</td><td>nomy: maintainer decision required</td></tr></table>\n"
    assert claim._autonomy_refusal(admitting + cells) is None
    # A `<details>` opened after an ignored `<td>` is popped by no `</td>`, and a `<td>`
    # ignored inside one is in the way of no `</details>`; a cell of a table pops as before.
    hidden = claim._autonomy_refusal("<td><details>note</td>\n\n" + admitting)
    assert hidden is not None and "only in a shape that cannot admit" in hidden, hidden
    assert claim._autonomy_refusal("<details>x<td>y</details>\n\n" + admitting) is None
    popped = claim._autonomy_refusal("<table><td><details>note</td></table>\n\n" + admitting)
    assert popped is None, popped


def test_a_code_block_after_a_definition_in_another_container_is_code() -> None:
    """Codex on #462 (read of `c3181fb`): `[^1]: /url` over `-     Autonomy: human review
    required` is the footnote and then an item holding a code block, which the page shows
    literal - but the continuation lookup went by adjacent source lines alone, took the
    item's code for the footnote's continuation, read it as Markdown, and refused the issue
    for a declaration the page shows as code. cmark-gfm continues a definition only inside
    the item both are in, and a definition in a block quote absorbs nothing (GitHub's
    markdown endpoint, 2026-10-03).
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for code in (
        "[^1]: /url\n-     Autonomy: maintainer decision required\n",
        "[^1]: /url\n\n-     Autonomy: maintainer decision required\n",
        "[^1]: /url\n>     Autonomy: maintainer decision required\n",
        "> [^1]: /url\n>\n>     Autonomy: maintainer decision required\n",
        "> [^1]: note\n>\n>     Autonomy: maintainer decision required\n",
        "> [^1]: note\n>\n>       Autonomy: maintainer decision required\n",
        "- [^1]: /url\n-     Autonomy: maintainer decision required\n",
        "- > [^1]: /url\n  >\n  >     Autonomy: maintainer decision required\n",
        "> [^1]: /url\n\n    Autonomy: maintainer decision required\n",
        "> [^1]: note\n\n    Autonomy: maintainer decision required\n",
        "- > [^1]: /url\n\n      Autonomy: maintainer decision required\n",
    ):
        assert claim._autonomy_refusal(admitting + code) is None, code
    # A continuation inside the definition's own container is still the footnote's text.
    for continued in (
        "[^1]: /url\n\n    Autonomy: maintainer decision required\n",
        "[^1]: /url\n    Autonomy: maintainer decision required\n",
        "[^1]: note\n\n    Autonomy: maintainer decision required\n",
        "- [^1]: /url\n\n      Autonomy: maintainer decision required\n",
        "- [^1]: note\n\n      Autonomy: maintainer decision required\n",
        "- a\n  - [^1]: /url\n\n        Autonomy: maintainer decision required\n",
    ):
        read = claim._autonomy_refusal(admitting + continued)
        assert read is not None and "maintainer decision" in read, (continued, read)
    # Four spaces after a quoted definition in an item, or after an item's own, is the
    # item's next paragraph on the page, and refuses as the paragraph it is.
    for paragraph in (
        "- > [^1]: /url\n\n    Autonomy: maintainer decision required\n",
        "- [^1]: /url\n\n    Autonomy: maintainer decision required\n",
    ):
        read = claim._autonomy_refusal(admitting + paragraph)
        assert read is not None and "maintainer decision" in read, (paragraph, read)


def test_a_struck_out_restriction_still_refuses_and_a_struck_out_admission_never_admits() -> None:
    """Codex on #462 (read of `c02ab15`) read `_plain`'s note - a struck-out value fails a
    token match - as a rule for both directions and asked that the refusal scan skip struck
    text. The marks cut one way: the page still shows the words, crossed out, and the gate
    reads a retraction only as not admitting. `~~maintainer decision required~~` in a heading's
    section or a bullet's remainder refuses, as a `<del>` drawn the same way does, and
    `~~agent-can-do-alone~~` as a value admits nothing. Admitting past the marks would be a
    capability the agent layer does not take from a review finding (ADR-0064); a retracted
    restriction is deleted by re-grooming.
    """
    section = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    for struck in (
        section + "~~maintainer decision required~~\n",
        section + "<del>maintainer decision required</del>\n",
        "- **Autonomy:** agent-can-do-alone\n\n  ~~needs maintainer input~~\n",
        "- **Autonomy:** agent-can-do-alone ~~(maintainer decision required)~~\n",
        "- **Autonomy:** ~~maintainer decision required~~ agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(struck)
        assert read is not None and ("maintainer decision" in read or "maintainer input" in read)
    for retracted in (
        "- **Autonomy:** ~~agent-can-do-alone~~\n",
        "- **Autonomy:** <del>agent-can-do-alone</del>\n",
        "## Execution autonomy\n\n~~agent-can-do-alone~~\n",
    ):
        assert claim._autonomy_refusal(retracted) is not None, retracted
    assert claim._autonomy_refusal("- **Autonomy:** agent-can-do-alone\n") is None


def test_a_struck_out_key_is_read_and_never_admits() -> None:
    """Codex on #462 (read of `35710e3`): the parser renders a strike-through between `~~`
    marks, so a key struck out whole - `- ~~Autonomy: maintainer decision required~~` - matched
    no key and the item was not read at all, while an admitting bullet beside it carried the
    issue. The marks cut one way: the key is read through them, the value stays as the page
    shows it, and a shape carrying any strike never admits.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for struck in (
        "- ~~Autonomy: maintainer decision required~~\n",
        "- ~~**Autonomy:** maintainer decision required~~\n",
        "- <del>Autonomy: maintainer decision required</del>\n",
        "- ~~Autonomy:~~ maintainer decision required\n",
        "- ~~Autonomy~~: maintainer decision required\n",
        "- [ ] ~~Autonomy: maintainer decision required~~\n",
        "## ~~Execution autonomy~~\n\nmaintainer decision required\n",
        "~~Autonomy~~\n\nmaintainer decision required\n",
        "| ~~Autonomy~~ | maintainer decision required |\n|---|---|\n",
        "<p><del>Autonomy: maintainer decision required</del></p>\n",
        "<p><del>Autonomy</del></p><p>maintainer decision required</p>\n",
    ):
        read = claim._autonomy_refusal(admitting + struck)
        assert read is not None and "maintainer decision" in read, (struck, read)
    # A retracted admission is read as not admitting, in every shape that could have admitted.
    for retracted in (
        "- ~~**Autonomy:** agent-can-do-alone~~\n",
        "- ~~Autonomy:~~ agent-can-do-alone\n",
        "- <del>Autonomy: agent-can-do-alone</del>\n",
        "## ~~Execution autonomy~~\n\nagent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(retracted)
        assert read is not None and "cannot admit" in read, (retracted, read)
    # And the admitting bullet beside a retracted admission still admits: a retraction only
    # withdraws what it strikes.
    assert claim._autonomy_refusal(admitting + "- ~~**Autonomy:** agent-can-do-alone~~\n") is None
    texts = claim._key_texts
    assert list(texts("~~Autonomy:~~ ~~x~~")) == [
        "~~Autonomy:~~ ~~x~~",
        "Autonomy: ~~x~~",
        "Autonomy: x",
    ]
    assert list(texts("~~Autonomy: x~~")) == ["~~Autonomy: x~~", "Autonomy: x"]
    assert list(texts("~~Autonomy: a~~ ~~b~~")) == [
        "~~Autonomy: a~~ ~~b~~",
        "Autonomy: a ~~b~~",
        "Autonomy: a b",
    ]
    assert list(texts("~~")) == ["~~"] and list(texts("~~Autonomy: x")) == ["~~Autonomy: x"]
    # The first reading the key matches is the one taken, so marks inside the value stay.
    assert claim._keyed("~~Autonomy:~~ ~~x~~") == ("", "~~x~~", False, True)


def test_a_key_struck_in_part_is_read_through_every_pair() -> None:
    """Codex on #462 (read of `6c060e7`): `- Auto<del>nomy</del>: maintainer decision required`
    renders `Auto~~nomy~~: ...`, and reading only a text that *opens* with a mark through it
    left the key unmatched, so the item was not read and an admitting bullet beside it
    carried the issue. Every balanced pair is read through, from the left, until the key
    matches; and GitHub strikes between one tilde as between two (its markdown endpoint,
    2026-10-03), while `~~x~` and `~~ x ~~` are literal and `~~~` opens a fence.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for struck in (
        "- Auto<del>nomy</del>: maintainer decision required\n",
        "- Auto~~nomy~~: maintainer decision required\n",
        "- **Auto~~nomy~~:** maintainer decision required\n",
        "- ~~Auto~~no~~my~~: maintainer decision required\n",
        "- ~Autonomy~: maintainer decision required\n",
        "- ~Autonomy: maintainer decision required~\n",
        "- <s>Auto</s>nomy: maintainer decision required\n",
        "## Auto~~nomy~~\n\nmaintainer decision required\n",
        "Auto~nomy~\n\nmaintainer decision required\n",
        "| Auto~~nomy~~ | maintainer decision required |\n|---|---|\n",
        "<p>Auto<del>nomy</del>: maintainer decision required</p>\n",
    ):
        read = claim._autonomy_refusal(admitting + struck)
        assert read is not None and "maintainer decision" in read, (struck, read)
    # Read, and never admitting, in every shape that could have admitted.
    for retracted in (
        "- Auto~~nomy~~: agent-can-do-alone\n",
        "- ~Autonomy:~ agent-can-do-alone\n",
        "## ~Execution autonomy~\n\nagent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(retracted)
        assert read is not None and "cannot admit" in read, (retracted, read)
    # A value between one tilde is shown struck, and is not the registered value.
    struck_value = claim._autonomy_refusal("- Autonomy: ~agent-can-do-alone~\n")
    assert struck_value is not None and "not a registered" in struck_value, struck_value
    # Literal on the page, and so no key: an admitting bullet beside one still admits.
    for literal in ("- ~~Autonomy~: agent-can-do-alone\n", "- ~~ Autonomy ~~: x\n"):
        assert claim._autonomy_refusal(admitting + literal) is None, literal
    assert list(claim._key_texts("~~x~")) == ["~~x~"]
    assert list(claim._key_texts("~a~ ~~b~~")) == ["~a~ ~~b~~", "a ~~b~~", "a b"]


def test_abutting_struck_spans_are_one_struck_key() -> None:
    """Found beside Codex's read of `6c060e7` on #462: the page draws two struck spans that
    abut, nest or overlap as one struck run, so `<del>Auto</del><del>nomy</del>`,
    `<del>Auto</del>~~nomy~~`, `<del><del>Autonomy</del></del>` and `~~<del>Auto</del>nomy~~`
    each show the struck key (GitHub's markdown endpoint, 2026-10-03) - while drawing each
    tag's own marks rendered `~~Auto~~~~nomy~~`, a four-tilde run no reader of pairs can
    read, and the restriction beside it went unread beside an admitting bullet. The marks are
    laid out for the struck runs. The *literal* `~~Auto~~~~nomy~~` is one pair holding a
    literal `~~~~` on the page, and a raw block's tildes are literal, so those stay as they
    are and are not the key.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    restriction = ": maintainer decision required\n"
    for joined in (
        "- <del>Auto</del><del>nomy</del>" + restriction,
        "- <del>Auto</del><s>nomy</s>" + restriction,
        "- <del>Au</del><del>to</del><strike>nomy</strike>" + restriction,
        "- <del>Auto</del>~~nomy~~" + restriction,
        "- ~~Auto~~<del>nomy</del>" + restriction,
        "- <del>Auto</del>~nomy~" + restriction,
        "- <del><del>Autonomy</del></del>" + restriction,
        "- <del>~~Autonomy~~</del>" + restriction,
        "- ~~<del>Autonomy</del>~~" + restriction,
        "- <del>~~Auto</del>nomy~~" + restriction,
        "- <del>Auto<s>nomy</s></del>" + restriction,
        "- </del><del>Autonomy</del>" + restriction,
        "## <del>Auto</del><del>nomy</del>\n\nmaintainer decision required\n",
        "| <del>Auto</del><del>nomy</del> | maintainer decision required |\n|---|---|\n",
        "<p><del>Auto</del><del>nomy</del>: maintainer decision required</p>\n",
        "<h2><del>Auto</del><del>nomy</del></h2><p>maintainer decision required</p>\n",
    ):
        read = claim._autonomy_refusal(admitting + joined)
        assert read is not None and "maintainer decision" in read, (joined, read)
    retracted = claim._autonomy_refusal("- <del>Auto</del><del>nomy</del>: agent-can-do-alone\n")
    assert retracted is not None and "cannot admit" in retracted, retracted
    # Literal on the page, and so no key: an admitting bullet beside one still admits.
    for literal in (
        "- ~~Auto~~~~nomy~~" + restriction,
        "- ~~Auto~~~nomy~" + restriction,
        "- <del>Auto</del>~~nomy" + restriction,
        "<p><del>Auto</del>~~nomy~~: maintainer decision required</p>\n",
    ):
        assert claim._autonomy_refusal(admitting + literal) is None, literal
    assert claim._keyed("~~Autonomy~~: ~~x~~") == ("", "~~x~~", False, True)


def test_a_key_in_other_than_the_registered_spelling_is_read_and_never_admits() -> None:
    """Codex on #462 (read of `f53c44a`): under Unicode case folding `- **Executıon
    autonomy:** agent-can-do-alone` - a dotless i - matched the key and admitted, while the
    page shows a field of another name. The folding is kept, since `Executıon autonomy:
    maintainer decision required` is the restriction a reader of the page sees and an ASCII
    fold would leave it unread beside an admitting bullet; what it shows is read, and a key
    in other than the registered spelling never admits (`_defaced`). The same for a format
    character the page keeps and draws nothing for - a zero-width space, a joiner, a soft
    hyphen (GitHub's markdown endpoint, 2026-10-03) - and for a compatibility character the
    page draws as a variant of the letter it stands for - fullwidth, circled, mathematical,
    the long s, a ligature, the fullwidth colon - which NFKC reads as that letter: read
    through, never admitting. A letter of another script that only looks like the key's is
    not read. U+E000 the page drops outright, so it is dropped and the key is the key.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for spelled in (
        "- **Executıon autonomy:** maintainer decision required\n",
        "- **Executİon autonomy:** maintainer decision required\n",
        "- Auto\u200bnomy: maintainer decision required\n",
        "- Auto\u200dnomy: maintainer decision required\n",
        "- Auto\u00adnomy: maintainer decision required\n",
        "- \ufeffAutonomy: maintainer decision required\n",
        "- Auto\ue000nomy: maintainer decision required\n",
        "- Ａｕｔｏｎｏｍｙ: maintainer decision required\n",
        "- Ⓐutonomy： maintainer decision required\n",
        "- 𝐀𝐮𝐭𝐨𝐧𝐨𝐦𝐲: maintainer decision required\n",
        "- Execution autonomy\ufb01eld: maintainer decision required\n",
        "## Executıon autonomy\n\nmaintainer decision required\n",
        "## Ａｕｔｏｎｏｍｙ\n\nmaintainer decision required\n",
        "Auto\u200bnomy\n\nmaintainer decision required\n",
        "| Auto\u200bnomy | maintainer decision required |\n|---|---|\n",
        "<p>Auto\u200bnomy: maintainer decision required</p>\n",
    ):
        read = claim._autonomy_refusal(admitting + spelled)
        assert read is not None and "maintainer decision" in read, (spelled, read)
    for unregistered in (
        "- **Executıon autonomy:** agent-can-do-alone\n",
        "- **Executİon autonomy:** agent-can-do-alone\n",
        "- Auto\u200bnomy: agent-can-do-alone\n",
        "- Autonomy:\u200b agent-can-do-alone\n",
        "## Executıon autonomy\n\nagent-can-do-alone\n",
        "## Auto\u200bnomy\n\nagent-can-do-alone\n",
        "- Ａｕｔｏｎｏｍｙ: agent-can-do-alone\n",
        "- Autonomy： agent-can-do-alone\n",
        "## 𝐀𝐮𝐭𝐨𝐧𝐨𝐦𝐲\n\nagent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(unregistered)
        assert read is not None and "cannot admit" in read, (unregistered, read)
    # A Cyrillic a is another letter: not the key, read as prose.
    assert claim._keyed("Аutonomy: maintainer decision required") is None
    # Capitalization of the registered spelling is the registered spelling.
    for registered in (
        "- **EXECUTION AUTONOMY:** agent-can-do-alone\n",
        "- **execution autonomy:** AGENT-CAN-DO-ALONE\n",
        "- Auto\ue000nomy: agent-can-do-alone\n",
        "## AUTONOMY\n\nagent-can-do-alone\n",
    ):
        assert claim._autonomy_refusal(registered) is None, registered
    assert claim._shown("Auto\u200bnomy\u00ad: x") == "Autonomy: x"
    assert claim._shown("Ⓐｕｔｏｎｏｍｙ：\u200b x") == "Autonomy: x"
    assert claim._defaced("Executıon autonomy: x") and claim._defaced("Auto\u200bnomy")
    assert claim._defaced("~~Autonomy~~") and not claim._defaced("Execution autonomy: x")


def test_a_strike_heavy_paragraph_is_matched_once() -> None:
    """Codex on #462 (read of `f53c44a`): reading a key through its strike marks rescanned
    and copied the whole text once per pair, so a paragraph of thousands of struck spans
    that was no key was matched once per span against a fresh copy - quadratic, and a claim
    or a doctor report stalled on it. The pairs are found in one pass, the reading with every
    mark gone is tried first as the test of whether any can match, and the readings are built
    only up to the first that matches. The readings themselves are unchanged: the leftmost-
    first pairing, checked here against the reading that gave it.
    """

    class Counting:
        def __init__(self, pattern: re.Pattern[str]) -> None:
            self.pattern, self.calls = pattern, 0

        def fullmatch(self, text: str) -> re.Match[str] | None:
            self.calls += 1
            return self.pattern.fullmatch(text)

    spans = " ".join(f"~~w{n}~~" for n in range(3000))
    counting = Counting(claim._AUTONOMY_BULLET)
    assert claim._match_key(counting, spans + ": v") is None and counting.calls == 1
    counting = Counting(claim._AUTONOMY_BULLET)
    keyed = claim._match_key(counting, "~~Autonomy~~: " + spans)
    assert keyed is not None and keyed.group("value") == spans and counting.calls == 3
    counting = Counting(claim._BARE_KEY)
    assert claim._match_key(counting, "~~Auto~~~nomy~~ " + spans) is None and counting.calls == 1
    # The readings are those the leftmost-first pairing gives: the first mark with a partner,
    # paired with the nearest, repeated.
    leftmost = re.compile(r"(?<!~)(~~?)(?!~)(.+?)(?<!~)\1(?!~)")

    def rescanned(text: str) -> list[str]:
        readings = [text]
        while (pair := leftmost.search(text)) is not None:
            text = text[: pair.start()] + pair.group(2) + text[pair.end() :]
            readings.append(text)
        return readings

    draw = random.Random(462)
    for _ in range(3000):
        text = "".join(draw.choice("~~~ab ") for _ in range(draw.randint(1, 12)))
        assert list(claim._key_texts(text)) == rescanned(text), text
    for text in ("~a ~~b~~ c~", "~~a~~~~b~~", "~~a ~b~ c~~", "~ ~~x~~ ~", "~~a~~~b~"):
        assert list(claim._key_texts(text)) == rescanned(text), text


def test_an_empty_heading_is_drawn() -> None:
    """Codex on #462 (read of `35710e3`): `- ## <!-- note -->` over `Autonomy:
    agent-can-do-alone` in the same item is an empty `<h2>` over the paragraph on the page
    (GitHub's markdown endpoint, 2026-10-03), a block with height of its own, so the paragraph
    is not the item's lead and the item is not the registered bullet. The heading read as
    undrawn - rendering to nothing, carrying no tag - and the paragraph admitted as the lead.
    A heading is drawn whatever it holds.
    """
    for item in (
        "- ## <!-- note -->\n\n  Autonomy: agent-can-do-alone\n",
        "- ##\n\n  Autonomy: agent-can-do-alone\n",
        "- ###### <!-- note -->\n\n  Autonomy: agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(item)
        assert read is not None and "cannot admit" in read, (item, read)
    restricted = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n\n"
        "- ## <!-- note -->\n\n  Autonomy: maintainer decision required\n"
    )
    assert restricted is not None and "maintainer decision" in restricted, restricted
    # Controls: an empty heading above a registered bullet is a heading, and a key heading's
    # empty value under it still refuses.
    assert (
        claim._autonomy_refusal("## <!-- note -->\n\n- **Autonomy:** agent-can-do-alone\n") is None
    )
    empty = claim._autonomy_refusal(
        "## Execution autonomy\n\n### <!-- note -->\n\nagent-can-do-alone\n"
    )
    assert empty is not None and "declares autonomy ''" in empty, empty
    assert claim._markdown.draws(claim._markdown.Heading(2, "## <!-- note -->", "", 0, 0, "##"))


def test_a_paragraph_is_drawn_whatever_it_holds() -> None:
    """Found beside Codex's read-36 findings on #462 by probing the empty heading's neighbours:
    `- &nbsp;` over `Autonomy: agent-can-do-alone` in the same item is `<p>&nbsp;</p>` over
    the paragraph on the page (GitHub's markdown endpoint, 2026-10-03), a line of its own
    height, and `- [](x)` or `- &#32;` a `<p>` with its margin; each read as undrawn - its text
    rendering to nothing, no picture, no tag - and the paragraph admitted as the item's lead.
    The page lays out a paragraph for a paragraph however little it shows; a comment alone on
    its lines is the one block it does not.
    """
    for item in (
        "- &nbsp;\n\n  Autonomy: agent-can-do-alone\n",
        "- &#32;\n\n  Autonomy: agent-can-do-alone\n",
        "- &ensp;\n\n  Autonomy: agent-can-do-alone\n",
        "- [](x)\n\n  Autonomy: agent-can-do-alone\n",
        "- &nbsp;<!-- c -->\n\n  Autonomy: agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(item)
        assert read is not None and "cannot admit" in read, (item, read)
    restricted = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n\n"
        "- &nbsp;\n\n  Autonomy: maintainer decision required\n"
    )
    assert restricted is not None and "maintainer decision" in restricted, restricted
    # A heading's value is its own next paragraph, and an empty one is an empty value.
    empty = claim._autonomy_refusal("## Execution autonomy\n\n&nbsp;\n\nagent-can-do-alone\n")
    assert empty is not None and "declares autonomy ''" in empty, empty
    # Controls. A comment alone above the key is still read past; a non-breaking space before
    # the key, or a hard break inside the key's own paragraph, is that paragraph and not a
    # block before it - the page shows the key indented by a space, or on the paragraph's
    # second line; and an empty bullet under the heading is still its empty value.
    assert claim._autonomy_refusal("- <!-- c -->\n\n  **Autonomy:** agent-can-do-alone\n") is None
    assert claim._autonomy_refusal("- &nbsp;**Autonomy:** agent-can-do-alone\n") is None
    assert claim._autonomy_refusal("- \\\n  **Autonomy:** agent-can-do-alone\n") is None
    bullet = claim._autonomy_refusal("## Execution autonomy\n\n- &nbsp;\n- agent-can-do-alone\n")
    assert bullet is not None and "declares autonomy ''" in bullet, bullet


def test_a_bare_key_in_an_item_heads_that_item_alone() -> None:
    """Codex on #462 (read of `6c060e7`): `- **Autonomy**` over `- agent-can-do-alone` is an
    item holding the key alone and a sibling holding a value, and the key's section ran into
    the sibling, so the empty declaration the page shows in the first item never failed the
    exact check and an admitting bullet beside them carried the issue. A bare key heads what
    follows it in its own container.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for split in (
        "- **Autonomy**\n- agent-can-do-alone\n",
        "- **Autonomy**\n\n- agent-can-do-alone\n",
        "- **Autonomy**\n\nagent-can-do-alone\n",
        "> **Autonomy**\n\nagent-can-do-alone\n",
        "- - **Autonomy**\n  - agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(split + admitting)
        assert read is not None and "declares autonomy ''" in read, (split, read)
    # The key's own paragraph, or a list nested in its item, is what it heads - read, and
    # unable to admit, as a bare key always is.
    for own in (
        "- **Autonomy**\n\n  human review required\n",
        "- **Autonomy**\n  - human review required\n",
        "> **Autonomy**\n>\n> human review required\n",
    ):
        read = claim._autonomy_refusal(admitting + own)
        assert read is not None and "human review required" in read, (own, read)
    assert claim._autonomy_refusal(admitting + "- **Autonomy**\n\n  agent-can-do-alone\n") is None
    # A heading's section is not confined: the page draws it the same wherever it is nested.
    nested = claim._autonomy_refusal(
        "- x\n  ## Execution autonomy\n\nmaintainer decision required\n"
    )
    assert nested is not None and "maintainer decision" in nested, nested


def test_a_footnote_continuation_is_read_by_the_bodys_rules() -> None:
    """Codex on #462 (read of `6c060e7`), twice, and the mirrors found beside them: the foot
    came back flattened into footnote paragraphs, so a `## Execution autonomy after unblock`
    in a continuation was a paragraph the heading pass never saw, and `<!-- tether-grooming-v1
    -->` there was dropped with the raw block's text, while a raw `<h2>`, a table's cells and
    an item's boundary were lost the same way - each a reading the body has and the foot had
    not. GitHub draws a continuation's headings, lists, tables and raw HTML inside the footnote
    (its markdown endpoint, 2026-10-03). The parser hands the continuation over as the blocks
    it holds, each carrying the note, and every reader reads them by the body's rule.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\nSee[^1].\n\n"
    for continued, expect in (
        (
            "[^1]: a\n\n    ## Execution autonomy after unblock\n\n    human review required\n",
            "after unblock",
        ),
        (
            "[^1]: a\n\n    ## Execution autonomy\n\n    maintainer decision required\n",
            "footnote heading",
        ),
        (
            "[^1]: a\n\n    <h2>Execution autonomy after unblock</h2>\n\n"
            "    human review required\n",
            "after unblock",
        ),
        (
            "[^1]: a\n\n    | Autonomy | human review required |\n    |---|---|\n",
            "footnote table row",
        ),
        ("[^1]: a\n\n    - **Autonomy**\n    - agent-can-do-alone\n", "declares autonomy ''"),
        ("[^1]: a\n\n    - **Autonomy:** maintainer decision required\n", "footnote bullet"),
        ("[^1]: a\n\n    <!-- tether-grooming-v1 -->\n", "marker inside"),
        ("[^1]: a\n\n    <div>\n    <!-- tether-grooming-v1 -->\n    </div>\n", "marker inside"),
    ):
        read = claim._autonomy_refusal(admitting + continued)
        assert read is not None and expect in read, (continued, read)
    # Nothing at the foot admits: not a column-zero bullet, not a heading over its paragraph.
    for foot in (
        "[^1]: a\n\n    - **Autonomy:** agent-can-do-alone\n",
        "[^1]: a\n\n    ## Execution autonomy\n\n    agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal("See[^1].\n\n" + foot)
        assert read is not None and "cannot admit" in read, (foot, read)
    # A heading at the foot ends no body section, and a body heading's section skips the foot.
    assert (
        claim._autonomy_refusal(
            "## Execution autonomy\n\n[^1]: a\n\n    ## Notes\n\n    human review required\n\n"
            "agent-can-do-alone\n"
        )
        is None
    )
    # A marker at the foot starts no grooming block: the body is refused for the misplaced
    # marker, never re-groomed by it.
    stale = claim._autonomy_refusal(
        "- **Autonomy:** maintainer decision required\n\nSee[^1].\n\n"
        "[^1]: a\n\n    <!-- tether-grooming-v1 -->\n\n    - **Autonomy:** agent-can-do-alone\n"
    )
    assert stale is not None and "marker inside" in stale, stale
    md = claim._markdown
    doc = md.parse("[^1]: a\n\n    ## H\n\n    - x\n\n    | c |\n    |---|\n    <hr>\n")
    assert all(md.at_foot(block) for block in doc)
    assert all(md.at_foot(item) for item in doc[2].items)
    assert md.at_foot(doc[3].rows[0]) and not md.at_foot(md.parse("x\n")[0])


def test_a_void_raw_block_is_a_piece_before_the_text_after_it() -> None:
    """Codex on #462 (read of `6c060e7`): in `<h2>Execution autonomy</h2><hr>agent-can-do-alone`
    the text after the `<hr>` was the piece the rule opened, so the raw heading's first drawn
    block was the text and an admitting bullet beside it carried the issue, where the page
    draws the rule between them. A void block is a piece of its own at once, and what follows
    it is text no tag opened.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for raw in (
        "<h2>Execution autonomy</h2><hr>agent-can-do-alone\n",
        "<h2>Execution autonomy</h2><hr/>agent-can-do-alone\n",
        "<h2>Execution autonomy</h2>\n<hr>\nagent-can-do-alone\n",
        "<p>Autonomy</p><hr>agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(admitting + raw)
        assert read is not None and "declares autonomy ''" in read, (raw, read)
    md = claim._markdown
    assert [p.tag for p in md.parse("<h2>x</h2><hr>y\n")[0].shown] == ["h2", "hr", ""]
    assert [p.tag for p in md.parse("<div><hr>y</div>\n")[0].shown] == ["div", "hr", ""]


def test_a_period_in_the_qualifier_is_a_qualifier() -> None:
    """Codex on #462 (read of `35710e3`): `- **Autonomy.:** agent-can-do-alone` - the page
    shows `Autonomy.:`, not the bare field - read as the bare key, because the qualifier was
    normalized as a value is, trailing period dropped, and a column-zero bullet carrying it
    admitted. A qualifier keeps every character the page shows; only its whitespace goes.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for shape in (
        "- **Autonomy.:** agent-can-do-alone\n",
        "- **Autonomy..:** agent-can-do-alone\n",
        "## Autonomy.\n\nagent-can-do-alone\n",
        "## Execution autonomy.\n\nagent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(shape)
        dotted = any(f"with qualifier '{dots}'" in (read or "") for dots in (".", ".."))
        assert read is not None and dotted, (shape, read)
    for shape in (
        "| Autonomy. | agent-can-do-alone |\n|---|---|\n",
        "<p>Autonomy.: agent-can-do-alone</p>\n",
        "<table><tr><td>Autonomy.</td><td>agent-can-do-alone</td></tr></table>\n",
    ):
        read = claim._autonomy_refusal(admitting + shape)
        assert read is not None and "qualifier" in read, (shape, read)
    # Controls: a period ends a value, and whitespace before the colon is no qualifier.
    assert claim._autonomy_refusal("- **Autonomy:** agent-can-do-alone.\n") is None
    assert claim._autonomy_refusal("- **Autonomy :** agent-can-do-alone\n") is None
    assert claim._normalize_qualifier(" (after  unblock) ") == "(after unblock)"


def test_a_footnote_continuation_is_read_at_the_foot() -> None:
    """Codex on #462 (read of `e259a99`): a footnote definition's continuation - its lines
    indented four spaces - is drawn inside the footnote at the page's foot, and the parser
    handed it over as a code block nobody read, so `[^1]: first line` over `    Autonomy:
    maintainer decision required` beside an admitting bullet admitted. The parser now hands
    the continuation over as more footnote paragraphs, and the foot is read whole: a footnote
    that is not keyed is scan-only, as an item's remainder is, and a bare key in one heads the
    footnote paragraphs after it and nothing of the body.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\nSee[^1].\n\n"
    for continued in (
        "[^1]: first line\n\n    Autonomy: maintainer decision required\n",
        "[^1]: first line\n\n    - **Autonomy:** maintainer decision required\n",
        "[^1]: first line\n    lazy\n\n    ## Execution autonomy\n\n    human review required\n",
        "[^1]: /url\n\n    Autonomy: maintainer decision required\n",
        "[^1]:\n\n    **Execution autonomy**\n\n    human review required\n",
        "[^1]: needs maintainer input first\n",
        "[^1]: first line\n\n    then needs-maintainer-input\n",
    ):
        read = claim._autonomy_refusal(admitting + continued)
        assert read is not None and "footnote" in read, (continued, read)
        assert "maintainer decision" in read or "human review" in read or "maintainer input" in read
    # The foot never admits, whatever a bare key there heads.
    foot = claim._autonomy_refusal(
        "[^1]: **Execution autonomy**\n\n    agent-can-do-alone\n\nSee[^1].\n"
    )
    assert foot is not None and "only in a shape that cannot admit" in foot, foot
    # Two spaces continue nothing: that line is a keyed paragraph of the body. And an indented
    # block after anything but a footnote is a code block, literal.
    body = claim._autonomy_refusal(
        admitting + "[^1]: first line\n\n  Autonomy: human review required\n"
    )
    assert body is not None and "body paragraph" in body, body
    assert (
        claim._autonomy_refusal(admitting + "text\n\n    Autonomy: maintainer decision required\n")
        is None
    )


def test_a_bare_key_in_a_footnote_heads_that_footnote_alone() -> None:
    """Codex on #462 (read of `f488e46`): `[^1]: **Autonomy**` over `[^2]: agent-can-do-alone`
    read the second footnote's paragraph as the first's value - every footnote paragraph
    looking alike - so the empty declaration the page shows in the first never failed the
    exact check, and an admitting bullet beside them carried the issue. A footnote paragraph
    now names the definition it belongs to, and a bare key heads only the rest of its own.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\nSee[^1][^2].\n\n"
    for split in (
        "[^1]: **Autonomy**\n\n[^2]: agent-can-do-alone\n",
        "[^1]: **Autonomy**\n[^2]: agent-can-do-alone\n",
        "[^1]: note\n\n    **Execution autonomy**\n\n[^2]: agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(admitting + split)
        assert read is not None and "declares autonomy ''" in read, (split, read)
    # Within one footnote the bare key still heads its continuation.
    own = claim._autonomy_refusal(admitting + "[^1]: **Autonomy**\n\n    human review required\n")
    assert own is not None and "human review required" in own, own
    # Codex on #462 (read of `f488e46`), the same read: raw HTML in a continuation is read a
    # block at a time, so the keyed second `<p>` is the declaration the page shows.
    raw = claim._autonomy_refusal(
        admitting
        + "[^1]: note\n\n    <div><p>Notes</p><p>Autonomy: human review required</p></div>\n"
    )
    assert raw is not None and "human review required" in raw, raw


def test_a_block_the_foot_draws_with_no_text_keeps_its_place() -> None:
    """Codex on #462 (read of `22b148b`): `[^1]: Autonomy` over an indented `<hr>` over
    `agent-can-do-alone` is, at the foot, a bare key heading a rule - the page draws the rule
    first (GitHub's markdown endpoint, 2026-10-03), and in the body a rule or a code block
    under a bare key is its empty value. The continuation dropped what drew no text, so the
    key headed the registered value and an admitting bullet beside it carried the issue.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\nSee[^1].\n\n"
    for empty in ("<hr>", "---", "    code", "<div></div>", "-", "> <!-- c -->", "- <!-- c -->"):
        read = claim._autonomy_refusal(
            f"{admitting}[^1]: Autonomy\n\n    {empty}\n\n    agent-can-do-alone\n"
        )
        assert read is not None and "declares autonomy ''" in read, (empty, read)
    # The mirror of the list's bullet per item, at the foot (the endpoint draws `<li></li>`
    # over `<li>agent-can-do-alone</li>` inside the footnote).
    read = claim._autonomy_refusal(
        f"{admitting}[^1]: Autonomy\n\n    - <!-- c -->\n    - agent-can-do-alone\n"
    )
    assert read is not None and "declares autonomy ''" in read, read
    # Control: with nothing between, the key heads the value, and the foot cannot admit.
    assert claim._autonomy_refusal(f"{admitting}[^1]: Autonomy\n\n    agent-can-do-alone\n") is None
    alone = claim._autonomy_refusal("See[^1].\n\n[^1]: Autonomy\n\n    agent-can-do-alone\n")
    assert alone is not None and "cannot admit" in alone, alone


def test_a_list_draws_a_bullet_per_item_whatever_the_item_holds() -> None:
    """Codex on #462 (read of `22b148b`): `## Execution autonomy` over `- <!-- c -->` over
    `- agent-can-do-alone` draws an empty bullet over the value (GitHub's markdown endpoint,
    2026-10-03), so the heading's value is empty and refuses. The stand-in for an undrawn
    container was given per list, only when no item drew, so the empty first item vanished
    and the second item's value was the heading's own; it is given per item now.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for shape in (
        "## Execution autonomy\n\n- <!-- c -->\n- agent-can-do-alone\n",
        "## Execution autonomy\n\n* <!-- c -->\n* agent-can-do-alone\n",
        "## Execution autonomy\n\n1. <!-- c -->\n2. agent-can-do-alone\n",
        "Autonomy\n\n- <!-- c -->\n- agent-can-do-alone\n",
        "## Execution autonomy\n\n> - <!-- c -->\n> - agent-can-do-alone\n",
    ):
        read = claim._autonomy_refusal(admitting + shape)
        assert read is not None and "declares autonomy ''" in read, (shape, read)
    # Controls: a drawn first item is the value, wherever an undrawn item sits after it.
    for shape in (
        "## Execution autonomy\n\n- agent-can-do-alone\n- <!-- c -->\n",
        "## Execution autonomy\n\n- agent-can-do-alone\n",
    ):
        assert claim._autonomy_refusal(admitting + shape) is None, shape


def test_an_images_alternative_text_is_read_as_the_page_shows_it() -> None:
    """Codex on #462 (read of `e259a99`), twice: `- ![**Autonomy:** human review required](x)`
    rendered its label raw, so no key matched and the unregistered declaration the page shows
    as the image's alternative text sat beside an admitting bullet unread; and a raw `<img
    alt="Autonomy: human review required">` was a space, its `alt` - which the sanitizer keeps
    - never read. Both render the alternative text now, and neither shape can admit: the
    Markdown image is `pictured`, the raw one carries a tag.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for pictured in (
        "- ![**Autonomy:** human review required](x)\n",
        "![**Execution autonomy:** human review required](x)\n",
        "## Execution autonomy\n\n![human review required](x)\n",
        '<img src="/missing" alt="Autonomy: human review required"> after\n',
        '<p><img src="/m" alt="Autonomy: human review required"></p>\n',
        "- <img alt='Autonomy: maintainer decision required'>\n",
        # Codex on #462 (read of `be3164c`): the real `alt`, not the one quoted in `title`.
        '<img title=" alt=\'note\'" alt="Autonomy: human review required">\n',
    ):
        read = claim._autonomy_refusal(admitting + pictured)
        assert read is not None and ("human review" in read or "maintainer decision" in read)
    for alone in (
        "- ![**Autonomy:** agent-can-do-alone](x)\n",
        '<p><img alt="Autonomy: agent-can-do-alone"></p>\n',
        "- <img alt='Autonomy: agent-can-do-alone'>\n",
    ):
        shape = claim._autonomy_refusal(alone)
        assert shape is not None and "only in a shape that cannot admit" in shape, (alone, shape)


def test_every_cell_of_a_row_is_read_as_a_raw_cell_is() -> None:
    """Codex on #462 (read of `9fc6782`): `| Autonomy | agent-can-do-alone | Autonomy: human
    review required |` was accepted beside a valid bullet: the third cell is a second, visible
    declaration, but every cell past the value was scan-only, and `human review required`
    names no token, so it was never exact-checked. A row is read one cell at a time, as a raw
    `<tr>` is read one piece at a time: a cell that starts with the key heads the cell after
    it, a keyed cell is a declaration, and the rest is remainder. Its raw mirror is read the
    same way, in the one place it was not: a `<td>` that starts with a qualified key -
    `Autonomy after unblock` - was no key at all, while the Markdown row refused.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    keyed_third = "| Autonomy | agent-can-do-alone | Autonomy: human review required |"
    for row in (
        f"{keyed_third}\n|---|---|---|\n",
        f"| a | b | c |\n|---|---|---|\n{keyed_third}\n",
        "| note | Autonomy | human review required |\n|---|---|---|\n",
        "| note | Autonomy after unblock | maintainer decision required |\n|---|---|---|\n",
        "| note | Autonomy: human review required |\n|---|---|\n",
        "| Autonomy | agent-can-do-alone | Autonomy |\n|---|---|---|\n",
        "<table><tr><td>Autonomy after unblock</td><td>maintainer decision required</td>"
        "</tr></table>\n",
        "<table><tr><td>note</td><td>Autonomy</td><td>human review required</td></tr></table>\n",
        "<table><tr><td>Autonomy</td><td>agent-can-do-alone</td>"
        "<td>Autonomy: human review required</td></tr></table>\n",
        "<table><tr><td>Autonomy</td></tr></table>\n\nagent-can-do-alone\n",
        "<table><tr><th>Autonomy</th><th>Owner</th></tr></table>\n",
    ):
        unread = claim._autonomy_refusal(admitting + row)
        assert unread is not None, f"{row!r}: a cell's declaration was not exact-checked"
        assert "not a registered" in unread or "qualifier" in unread or "maintainer" in unread, (
            f"{row!r}: {unread}"
        )
    # A dash-led key in a cell makes its row one about the field, scanned and declaring
    # nothing, in Markdown and raw HTML alike; a row of registered values declares nothing
    # that refuses; and a cell's value is the cell after it, not the paragraph after the table.
    for about in (
        "| Autonomy - notes | agent-can-do-alone |\n|---|---|\n",
        "<table><tr><td>Autonomy - notes</td><td>see above</td></tr></table>\n",
        "| Field | Value |\n|---|---|\n| Autonomy | agent-can-do-alone |\n",
        "<table><tr><td>Autonomy</td><td>agent-can-do-alone</td></tr></table>\n\n"
        "See the maintainer.\n",
    ):
        assert claim._autonomy_refusal(admitting + about) is None, about
    scanned = claim._autonomy_refusal(
        admitting + "| Autonomy - notes | needs maintainer decision |\n|---|---|\n"
    )
    assert scanned is not None and "maintainer decision" in scanned, scanned


def test_an_empty_bullet_value_is_a_declaration_that_fails_the_exact_check() -> None:
    """Codex on #462 (read of `a1408d7`): `- **Autonomy:**` with nothing after it matched no
    bullet, because the value group required a character, so the field vanished and an admitting
    bullet beside it carried the issue. An empty field is a visible declaration with an empty
    value, and it fails the exact check as the empty heading and the one-column row do.
    """
    empty = claim._autonomy_refusal("- **Autonomy:**\n- **Autonomy:** agent-can-do-alone\n")
    assert empty is not None, "an empty bullet value vanished beside an admitting one - fail-open"
    assert "not a registered" in empty and "bullet" in empty, empty
    alone = claim._autonomy_refusal("- **Autonomy:** \n")
    assert alone is not None and "not a registered" in alone, alone


def test_a_list_item_is_read_past_a_leading_block_that_draws_nothing() -> None:
    """Codex on #462 (read of `a1408d7`): an item whose first block is a comment on its own line
    - `- <!-- groomed -->` over `**Autonomy:** maintainer decision required` - declared nothing,
    because the declaration had to be the item's literal first block, and an admitting bullet
    beside it carried the issue while the page shows the restriction in the list. The item's
    declaration is its first block the page draws anything for; a leading block that draws
    something - a picture, a `<details>` - is still the item's first block, and the item
    declares nothing.
    """
    restrictive = "- <!-- groomed -->\n  **Autonomy:** maintainer decision required\n"
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    hidden = claim._autonomy_refusal(admitting + restrictive)
    assert hidden is not None, "a restriction behind a leading comment was not read - fail-open"
    assert "maintainer decision" in hidden, hidden
    # A comment the page does not draw leaves the registered shape as it is.
    assert (
        claim._autonomy_refusal("- <!-- groomed -->\n  **Autonomy:** agent-can-do-alone\n") is None
    )
    # A drawn first block is the item's lead, so the key below it is not the bullet's; it is
    # read as a keyed paragraph, which can refuse and cannot admit. Codex on #462 (read of
    # `ef646ee`): a nested list or a block quote the item opens with is drawn too, and skipping
    # it let the paragraph under it admit as the item's own text.
    for lead in ("![](x.png)", "- child", "> quoted"):
        led = claim._autonomy_refusal(f"- {lead}\n\n  **Autonomy:** agent-can-do-alone\n")
        assert led is not None, f"{lead!r}: the paragraph under a drawn lead admitted"
        assert "only in a shape that cannot admit" in led and "paragraph" in led, led
    empty_marker = claim._autonomy_refusal("-\n  - child\n\n  **Autonomy:** agent-can-do-alone\n")
    assert empty_marker is not None and "only in a shape that cannot admit" in empty_marker


def test_keyed_text_anywhere_on_the_page_is_a_declaration_that_cannot_admit() -> None:
    """A paragraph keyed `**Autonomy:**` that is not a list item's lead - on its own at the top
    level, second in an item, inside a block quote - is a field the page shows, and it was not
    read at all, so a restriction written that way beside an admitting bullet was never seen:
    the class of the comment-led item (Codex on #462, read of `a1408d7`) one shape over. Codex
    on #462 (read of `d6c16fc`): the same key in a raw HTML block, `<p><strong>Autonomy:</strong>
    maintainer decision required</p>`, shows on the page and was not read either; nor was one
    on a heading line with its value, or in a table cell. Each is a declaration of the `+`
    bullet's kind - exact-checked, able to refuse, never able to admit. The live corpus has
    three such paragraphs and no such block, heading or cell; each paragraph already refuses.
    Codex on #462 (read of `aa47973`): a raw HTML block is read one rendered block at a time,
    because a `<div>` of two `<p>` is one block here and two paragraphs on the page, and the key
    matched against the run joined read past the restriction in the second; what follows a
    keyed block in the run is its remainder, scan-only, as a row's further cells are.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    for shape in (
        "**Autonomy:** maintainer decision required\n",
        "- note\n\n  **Autonomy:** maintainer decision required\n",
        "> **Autonomy:** maintainer decision required\n",
        "Execution autonomy: maintainer-decision required\n",
        "<p><strong>Autonomy:</strong> maintainer decision required</p>\n",
        "<details><summary>Autonomy: maintainer decision required</summary></details>\n",
        "<div>\n<p>Notes</p>\n<p><strong>Autonomy:</strong> maintainer decision required</p>\n"
        "</div>\n",
        "<table><tr><td>Autonomy:</td><td>maintainer decision required</td></tr></table>\n",
        "<p><strong>Autonomy:</strong><br>maintainer decision required</p>\n",
        "<div><p>Autonomy: agent-can-do-alone</p><p>Autonomy: maintainer decision required</p>"
        "</div>\n",
        "## Autonomy: maintainer decision required\n",
        "| field | note |\n|---|---|\n| scope | Autonomy: maintainer decision required |\n",
    ):
        refusal = claim._autonomy_refusal(admitting + "\n" + shape)
        assert refusal is not None, f"{shape!r}: keyed text was not read - fail-open"
        assert "maintainer decision" in refusal, f"{shape!r}: {refusal}"
    for shape, named in (
        ("**Autonomy:** agent-can-do-alone\n", "paragraph"),
        ("<p><strong>Autonomy:</strong> agent-can-do-alone</p>\n", "raw HTML"),
        ("## Autonomy: agent-can-do-alone\n", "heading line"),
        ("| a |\n|---|\n| Autonomy: agent-can-do-alone |\n", "table cell"),
    ):
        alone = claim._autonomy_refusal(shape)
        assert alone is not None, f"{shape!r}: keyed text admitted"
        assert "only in a shape that cannot admit" in alone and named in alone, alone
    # A bullet's lead is read once, as the bullet, and text that is not keyed is prose.
    assert claim._autonomy_refusal(admitting + "\nAutonomy is discussed above.\n") is None
    # A second keyed block in one raw HTML run is a declaration of its own, exact-checked, and
    # not merely scanned: `human review required` names no token and still refuses.
    second = claim._autonomy_refusal(
        admitting + "\n<div><p>Autonomy: agent-can-do-alone</p><p>Autonomy: human review "
        "required</p></div>\n"
    )
    assert second is not None and "not a registered" in second, second


def test_a_qualified_heading_or_row_key_is_a_declaration_that_cannot_admit() -> None:
    """Codex on #462 (read of `a1408d7`): `## Execution autonomy after unblock` over `maintainer
    decision required` was no heading key at all, so an admitting bullet beside it carried the
    issue while the page shows the restriction under a heading naming the field. A qualified
    heading is the qualified bullet's counterpart - a declaration that refuses on its qualifier
    - and so is a table row keyed the same way. Codex on #462 (read of `ef646ee`): bounding the
    qualifier to a character class left `once #123's merged` unread the same way, so every
    heading that starts with the key is read now, and the qualifier's shape decides how. A
    dash-led one is a heading *about* the field - the live corpus has one, #442's
    `### Execution autonomy — declared in the grooming block` over prose, `status:ready`, which
    reading as the field refused - and its section is scan-only: a restriction there refuses,
    nothing there admits, nothing is exact-checked. Codex on #462 (read of `aa47973`): the dash
    was searched for rather than matched at the front, so `after unblock - notes` - a condition
    with a dash later in it - was prose about the field and the condition was never
    exact-checked. The dash must open the qualifier.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    heading = claim._autonomy_refusal(
        admitting + "## Execution autonomy after unblock\n\nmaintainer decision required\n"
    )
    assert heading is not None, "a qualified heading declared nothing - fail-open"
    assert "maintainer decision" in heading, heading
    qualified = claim._autonomy_refusal("## Autonomy after unblock\n\nagent-can-do-alone\n")
    assert qualified is not None and "qualifier 'after unblock'" in qualified, qualified
    row = claim._autonomy_refusal(
        admitting + "| Autonomy after unblock | agent-can-do-alone |\n|---|---|\n"
    )
    assert row is not None and "qualifier 'after unblock'" in row, row
    for condition in (
        "## Execution autonomy (once #220 lands)\n\nagent-can-do-alone\n",
        "## Execution autonomy once #123's merged\n\nagent-can-do-alone\n",
        "## Execution autonomy, after unblock\n\nagent-can-do-alone\n",
    ):
        qualified = claim._autonomy_refusal(admitting + condition)
        assert qualified is not None and "qualifier" in qualified, f"{condition!r}: {qualified}"
    restricted = claim._autonomy_refusal(
        admitting + "## Execution autonomy once #123's merged\n\nmaintainer decision required\n"
    )
    assert restricted is not None and "maintainer decision" in restricted, restricted
    # A dash-led heading is about the field: its section refuses on a restriction and never
    # admits or exact-checks, so the corpus's prose shape keeps admitting beside its bullet.
    for about in (
        "### Execution autonomy — declared in the grooming block\n\nStated once, above.\n",
        "## Execution autonomy – in brief\n\nSee the grooming block.\n",
        "## Execution autonomy - notes\n\nSee the grooming block.\n",
    ):
        prose = claim._autonomy_refusal(admitting + about)
        assert prose is None, f"{about!r}: a heading about the field was read as the field"
    through = claim._autonomy_refusal(
        admitting + "### Execution autonomy — see below\n\nmaintainer decision required\n"
    )
    assert through is not None and "maintainer decision" in through, through
    unread = claim._autonomy_refusal("### Execution autonomy — see below\n\nagent-can-do-alone\n")
    assert unread is not None and "declares no Execution autonomy" in unread, unread
    # Codex on #462 (read of `93ed23d`): the dash-led qualifier is read for a restriction too,
    # since `## Execution autonomy — maintainer decision required` carries it in the heading.
    for carrying in (
        "## Execution autonomy — maintainer decision required\n\nSee above.\n",
        "| Execution autonomy — maintainer decision required | see above |\n|---|---|\n",
        "<h2>Execution autonomy — maintainer decision required</h2><p>See above.</p>\n",
    ):
        heading_itself = claim._autonomy_refusal(admitting + carrying)
        assert heading_itself is not None, f"{carrying!r}: the qualifier was not read"
        assert "maintainer decision" in heading_itself, f"{carrying!r}: {heading_itself}"
    # A dash later in the qualifier does not make the heading one about the field: the
    # condition before it is the qualifier, and it refuses on it, row and heading alike.
    for later in (
        "## Execution autonomy after unblock - notes\n\nSome prose.\n",
        "## Execution autonomy after unblock — notes\n\nagent-can-do-alone\n",
        "## Execution autonomy -notes\n\nagent-can-do-alone\n",
        "| Execution autonomy after unblock - notes | agent-can-do-alone |\n|---|---|\n",
    ):
        qualified = claim._autonomy_refusal(admitting + later)
        assert qualified is not None, f"{later!r}: a condition with a dash in it admitted"
        assert "qualifier" in qualified, f"{later!r}: {qualified}"


def test_a_bare_key_heads_the_block_the_page_draws_next() -> None:
    """Codex on #462 (read of `93ed23d`): `<div><h2>Execution autonomy</h2><p>maintainer decision
    required</p></div>` renders a heading over a restriction, and the raw HTML reader applied
    only the bullet's grammar, which needs a colon in the same block, so both were unread and
    an admitting bullet beside them carried the issue. A piece raw HTML draws as a heading is
    read as a Markdown heading is - a key with any qualifier, the next piece its value; any
    other piece that is the bare key - `<p>Autonomy</p>`, `<td>Autonomy</td>` - heads the piece
    after it. The Markdown mirror: `**Execution autonomy**` as a paragraph of its own, or as an
    item's lead over the item's next paragraph, heads what the page draws next. An item whose
    lead is raw HTML was skipped as a lead and is read like any other block. Nothing in any of
    these shapes admits, and prose that merely starts with the word stays prose.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for shape in (
        "<div><h2>Execution autonomy</h2><p>maintainer decision required</p></div>\n",
        "<h2>Autonomy:</h2><p>maintainer decision required</p>\n",
        "<p>Autonomy</p><p>maintainer decision required</p>\n",
        "<table><tr><td>Autonomy</td><td>maintainer decision required</td></tr></table>\n",
        "<h2>Execution autonomy after unblock</h2><p>maintainer decision required</p>\n",
        "**Execution autonomy**\n\nmaintainer decision required\n",
        "Autonomy\n\nmaintainer decision required\n",
        "- **Execution autonomy**\n\n  maintainer decision required\n",
        "- <p><strong>Autonomy:</strong> maintainer decision required</p>\n",
    ):
        refusal = claim._autonomy_refusal(admitting + shape)
        assert refusal is not None, f"{shape!r}: a bare key over a restriction was not read"
        assert "maintainer decision" in refusal, f"{shape!r}: {refusal}"
    for shape, named in (
        ("<h2>Execution autonomy</h2><p>agent-can-do-alone</p>\n", "raw HTML"),
        ("<p>Autonomy</p><p>agent-can-do-alone</p>\n", "raw HTML"),
        ("**Execution autonomy**\n\nagent-can-do-alone\n", "bare key"),
        ("- **Execution autonomy**\n\n  agent-can-do-alone\n", "bare key"),
    ):
        alone = claim._autonomy_refusal(shape)
        assert alone is not None, f"{shape!r}: a bare key's value admitted"
        assert "only in a shape that cannot admit" in alone and named in alone, alone
    # Codex on #462 (read of `5376120`): a raw heading's section is the page's, not the
    # block's - Markdown ends the raw block at a blank line, and the paragraphs after it sit
    # under the heading on the page - so it continues through the leaves that follow, the next
    # drawn one its value if one is still owed, up to the next heading, raw or Markdown.
    for continued in (
        "<h2>Execution autonomy \u2014 notes</h2>\n\nThe upload is a maintainer decision.\n",
        "<h2>Execution autonomy</h2>\n\nmaintainer decision required\n",
        "<h2>Execution autonomy</h2>\n\nSee below.\n\n- needs maintainer decision\n",
        "<p>Autonomy</p>\n\nmaintainer decision required\n",
        "<div><h2>Execution autonomy</h2></div>\n\n| a | b |\n|---|---|\n"
        "| maintainer decision | c |\n",
    ):
        past = claim._autonomy_refusal(admitting + continued)
        assert past is not None, f"{continued!r}: the section past the raw block was not read"
        assert "maintainer decision" in past, f"{continued!r}: {past}"
    owed = claim._autonomy_refusal("<h2>Execution autonomy</h2>\n\nagent-can-do-alone\n")
    assert owed is not None and "only in a shape that cannot admit" in owed, owed
    # Codex on #462 (read of `f5c37f0`): a checkbox before the bare key is read past as
    # `_keyed` reads past one - `- [ ] **Autonomy**` over `human review required` is the key
    # over its block - and what the key heads still cannot admit.
    for checked in (
        "- [ ] **Autonomy**\n\n  human review required\n",
        "- [x] **Execution autonomy**\n\n  maintainer decision required\n",
        "- [ ] Autonomy\n\n  - human review required\n",
    ):
        boxed = claim._autonomy_refusal(admitting + checked)
        assert boxed is not None, f"{checked!r}: a bare key behind a checkbox was not read"
        assert "bare key" in boxed, f"{checked!r}: {boxed}"
    assert claim._autonomy_refusal("- [ ] **Autonomy**\n\n  agent-can-do-alone\n") is not None
    for stopped in (
        "<h2>Execution autonomy \u2014 notes</h2>\n\nStated above.\n\n## Human action items\n",
        "<h2>Execution autonomy \u2014 notes</h2>\n\nStated above.\n\n"
        "<h2>Human action items</h2>\n",
        "## Execution autonomy \u2014 notes\n\nStated above.\n\n<p>fine</p>"
        "<h2>Human action items</h2>\n",
        "**Execution autonomy**\n\nagent-can-do-alone\n\n<div><h3>Human action items</h3></div>\n",
    ):
        bounded_section = claim._autonomy_refusal(admitting + stopped)
        assert bounded_section is None, f"{stopped!r}: {bounded_section}"
    # Codex on #462 (read of `f9fc1f0`): a heading that is not the key opens a section of its
    # own, so the field's remainder stops there, as `_section` stops at a Markdown heading.
    sectioned = claim._autonomy_refusal(
        admitting + "<div><h2>Execution autonomy — notes</h2><p>Stated above.</p>"
        "<h2>Human action items</h2><p>none</p></div>\n"
    )
    assert sectioned is None, sectioned
    bounded = claim._autonomy_refusal(
        admitting + "<h2>Execution autonomy</h2><p>agent-can-do-alone</p>"
        "<h2>Notes</h2><p>a human action item</p>\n"
    )
    assert bounded is None, bounded
    keyed_heading = claim._autonomy_refusal(
        admitting + "<h2>Notes</h2><h3>Autonomy: maintainer decision required</h3>\n"
    )
    assert keyed_heading is not None and "maintainer decision" in keyed_heading, keyed_heading
    # A raw HTML heading's qualifier refuses as a Markdown heading's does; the value of a
    # heading followed by another heading is empty; a bare key at the end heads nothing.
    qualified = claim._autonomy_refusal(
        admitting + "<h2>Execution autonomy after unblock</h2><p>agent-can-do-alone</p>\n"
    )
    assert qualified is not None and "qualifier 'after unblock'" in qualified, qualified
    # Codex on #462 (read of `5f41bae`): a block a tag opens that shows no text - an `<hr>`, a
    # `<div>` - is what the page draws next, as a rule or a quote is under a Markdown heading.
    for empty in (
        "<h2>Execution autonomy</h2><h3>Next</h3><p>agent-can-do-alone</p>\n",
        "<h2>Execution autonomy</h2><hr><p>agent-can-do-alone</p>\n",
        "<h2>Execution autonomy</h2><div><p>agent-can-do-alone</p></div>\n",
        "<p>Autonomy</p><hr><p>agent-can-do-alone</p>\n",
        "<p>Execution autonomy</p>\n",
        "**Execution autonomy**\n",
    ):
        headless = claim._autonomy_refusal(admitting + empty)
        assert headless is not None and "not a registered" in headless, f"{empty!r}: {headless}"
    # The bare key is the key alone: no qualifier, no colon. Prose starting with the word is
    # prose, and `Autonomy:` alone is the bullet's grammar with an empty value.
    assert claim._autonomy_refusal(admitting + "Autonomy is discussed above.\n") is None
    assert claim._autonomy_refusal(admitting + "<p>Autonomy is discussed above.</p>\n") is None
    colon = claim._autonomy_refusal(admitting + "Autonomy:\n\nmaintainer decision required\n")
    assert colon is not None and "not a registered" in colon and "paragraph" in colon, colon


def test_a_declaration_carrying_an_html_tag_can_refuse_and_never_admit() -> None:
    """Six reads in a row found a tag the rendering drew differently from the page - `<b>` as a
    space, `<q>` as nothing, `<del>` as nothing, `<wbr>` as a space - and each time the error ran
    towards admitting a defaced key or a retracted value. The rendering stays, to find a
    restriction however it is dressed; a key or value whose source carries a tag is no longer a
    shape that can admit, so the next tag the approximation draws wrongly can only over-refuse.
    """
    for shape in (
        "- **Autonomy:** <b>agent-can-do-alone</b>\n",
        "- **Auto<wbr>nomy:** agent-can-do-alone\n",
        "- <span>**Autonomy:**</span> agent-can-do-alone\n",
        "## Execution <i>autonomy</i>\n\nagent-can-do-alone\n",
        "## Execution autonomy\n\n<kbd>agent-can-do-alone</kbd>\n",
    ):
        refusal = claim._autonomy_refusal(shape)
        assert refusal is not None, f"{shape!r}: a tagged declaration admitted"
        assert "HTML tag" in refusal, f"{shape!r}: {refusal}"
    # A comment is not a tag, and the plain shapes are untouched.
    assert claim._autonomy_refusal("- **Autonomy:** agent-can-do-alone <!-- ok -->\n") is None
    assert claim._autonomy_refusal("## Execution autonomy\n\nagent-can-do-alone\n") is None
    # The restriction is still found through the tag, as every earlier case pins.
    through = claim._autonomy_refusal(
        "- **Autonomy:** agent-can-do-alone\n- <b>Autonomy:</b> maintainer decision required\n"
    )
    assert through is not None and "maintainer decision" in through, through


def test_autonomy_refusals_distinguish_absent_restricted_and_unregistered_values() -> None:
    """Each repairable failure says which of the three issue-body edits is needed."""
    absent = claim._autonomy_refusal("Acceptance criteria\n")
    restricted = claim._autonomy_refusal(
        "## Execution autonomy\n\nagent-can-do-alone; maintainer-decision required\n"
    )
    unregistered = claim._autonomy_refusal(
        "## Execution autonomy\n\nagent-can-do-alone for the documentation change\n"
    )

    assert absent is not None and "declares no Execution autonomy" in absent
    assert restricted is not None and "maintainer decision" in restricted
    assert unregistered is not None and "is not a registered autonomy value" in unregistered
    assert "is not a registered autonomy value" not in absent
    assert "is not a registered autonomy value" not in restricted


def test_a_restrictive_declaration_governs_wherever_it_sits_in_the_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two declarations in one source: the restrictive one governs, whichever is found first.

    `_declared_autonomy` used to return the **first** match and stop, and it looked for bullets
    before headings. So a body whose `## Execution autonomy` section said `maintainer decision
    required` was admitted outright if any bullet anywhere else in it read `agent-can-do-alone` -
    the heading was never read. That is a fail-open in the gate whose entire purpose is to fail
    closed, reachable by an ordinary issue body that declares the same thing twice.

    The precedence that *is* deliberate is between sources: a grooming block supersedes the body
    above it, which the neighbouring test covers. Within one source there is no such argument -
    two disagreeing declarations mean the issue is not clearly groomed, and the restrictive half
    governs exactly as it already does when one value names both.

    Asserted in both orders, because the defect was an ordering artefact and a fix that only
    reversed the search order would pass one and fail the other.
    """
    bodies = {
        "heading first": (
            "## Execution autonomy\n\nmaintainer decision required\n\n"
            "- **Autonomy:** agent-can-do-alone\n"
        ),
        "bullet first": (
            "- **Autonomy:** agent-can-do-alone\n\n"
            "## Execution autonomy\n\nmaintainer decision required\n"
        ),
        # Two headings, which the first fix still got wrong: it collected every bullet but took
        # only `_AUTONOMY_HEADING.search()`, so a second `## Execution autonomy` restricting the
        # issue was invisible behind an admitting first one. Same defect, one level down.
        "two headings, restrictive second": (
            "## Execution autonomy\n\nagent-can-do-alone\n\n"
            "## Execution autonomy\n\nmaintainer decision required\n"
        ),
        "two headings, restrictive first": (
            "## Execution autonomy\n\nmaintainer decision required\n\n"
            "## Execution autonomy\n\nagent-can-do-alone\n"
        ),
        # And two bullets, for the same reason in the other direction.
        "two bullets, restrictive second": (
            "- **Autonomy:** agent-can-do-alone\n- **Autonomy:** maintainer decision required\n"
        ),
        # A heading section is more than its first line. Taking only `lines[0]` dropped the rest,
        # so a restriction written as the sentence *under* the declaration was never read.
        "heading section, restrictive second line": (
            "## Execution autonomy\n\nagent-can-do-alone\n"
            "The upload step is a maintainer decision.\n"
        ),
        # `_GROOMING_BLOCK` stopped capturing at the next `<!--`, so any nested comment truncated
        # the authoritative source and hid everything after it.
        "grooming block split by a nested comment": (
            "<!-- tether-grooming-v1 -->\n\n"
            "- **Autonomy:** agent-can-do-alone\n"
            "<!-- a note from the groomer -->\n"
            "- **Autonomy:** maintainer decision required\n"
        ),
        # The bullet pattern required `**`, so a plainly written bullet was invisible and an
        # emphasized one below it governed. Markdown does not require the emphasis and neither
        # should a safety verdict.
        "plain bullet restrictive, emphasized bullet admitting": (
            "- Autonomy: maintainer decision required\n- **Autonomy:** agent-can-do-alone\n"
        ),
    }
    for where, body in bodies.items():
        assert claim._autonomy_refusal(body) is not None, (
            f"{where}: a restrictive declaration was ignored - fail-open"
        )
        routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
        fake = _install(monkeypatch, Fake(routes))
        with pytest.raises(SystemExit) as exit_info:
            claim._cmd_claim(_args(issue=7))
        assert exit_info.value.code == 3, where
        assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]], (
            f"{where}: a claim ref was created for an issue a maintainer must decide"
        )
        assert "maintainer" in capsys.readouterr().err


def test_a_grooming_block_supersedes_a_stale_body_declaration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The grooming block wins, and it is the restrictive one here.

    Those blocks exist to restate readiness after the body above them went stale, so reading the
    body first would admit on a value a grooming pass had already replaced.
    """
    body = (
        "## Execution autonomy\n\nagent-can-do-alone\n\n"
        "<!-- tether-grooming-v1 -->\n\n"
        "- **Status:** blocked.\n"
        "- **Autonomy:** needs maintainer input (the dataset decision)\n"
    )
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "maintainer input" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_ordinary_prose_under_the_declaration_does_not_refuse_a_ready_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading the whole heading section must not turn every sentence into a verdict.

    The sibling above makes a restrictive line *under* an admitting one govern, which it must. The
    obvious way to get that — treat each line as its own declaration — over-refuses badly: an
    issue that declares `agent-can-do-alone` and then explains itself in a sentence would be
    refused because the sentence does not *admit*, and that is a false negative on exactly the
    well-groomed issues this gate is meant to let through.

    So the first paragraph is exact-matched as the declaration and the remaining prose is scan-only.
    A refusal token there still governs; prose that names none changes nothing. This asserts the
    permissive half, because a fail-closed fix that quietly stopped admitting anything would pass
    every other test here.
    """
    body = (
        "## Execution autonomy\n\nagent-can-do-alone\n\n"
        "The tests are already written and the scope is small, so this is self-contained.\n"
    )
    assert claim._autonomy_refusal(body) is None, "ordinary prose was read as a restriction"
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    claim._cmd_claim(_args(issue=7))
    assert [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]], (
        "a ready issue was refused because it explained itself"
    )


@pytest.mark.parametrize(
    ("first", "rest", "expected"),
    [
        (
            "## Execution autonomy\n\nagent-can-do-alone",
            "unless the sizing note says otherwise",
            "is not a registered autonomy value",
        ),
        (
            "- **Autonomy:** agent-can-do-alone",
            "unless the sizing note says otherwise",
            "is not a registered autonomy value",
        ),
        (
            "## Execution autonomy\n\nagent-can-do-alone",
            "after a maintainer decision on the dataset",
            "maintainer decision",
        ),
        (
            "- **Autonomy:** agent-can-do-alone",
            "after a maintainer decision on the dataset",
            "maintainer decision",
        ),
    ],
    ids=["heading-condition", "bullet-condition", "heading-token", "bullet-token"],
)
def test_a_line_break_inside_a_declaration_never_decides_the_verdict(
    first: str, rest: str, expected: str
) -> None:
    """A soft-wrapped declaration is one declaration, however it is wrapped.

    Codex on #462: `agent-can-do-alone` with `unless the sizing note says otherwise` on the *next*
    line was admitted, while the same words on one line were refused as unregistered. Markdown
    renders both as one paragraph, so a line break was deciding a safety verdict - the separator
    defect `_flatten_autonomy` closes, one level up. The bullet path had the same hole and it was
    the worse one: `_AUTONOMY_BULLET` captures a single physical line, so a wrapped continuation
    was never read at all, not even for a refusal token, in the source a grooming block makes
    authoritative.

    Asserted as an invariance rather than as one body per shape, because the defect *is* the
    variance: every way of wrapping the same words must reach the verdict the unwrapped line does.
    """
    for joiner in (" ", "\n", "\n  ", "\n\t"):
        refusal = claim._autonomy_refusal(f"{first}{joiner}{rest}\n")
        assert refusal is not None, f"joined by {joiner!r}: a wrapped condition was admitted"
        assert expected in refusal, f"joined by {joiner!r}: {refusal}"


def test_a_line_that_cannot_interrupt_a_paragraph_stays_in_the_declaration() -> None:
    """Looking like a list item is not enough to end the value; Markdown must end it there too.

    Codex on #462, after ordered lists were first recognised: every `N.` marker ended the value,
    but GitHub Markdown lets an ordered list interrupt a paragraph only when it starts at `1`. So
    `agent-can-do-alone` followed by `2. unless the sizing note says otherwise` renders as one
    conditional paragraph, and treating the second line as a new block moved the condition out of
    the exact match and admitted it. The same holds for an empty marker and for a line indented
    four or more columns past its container: each renders as continuation text.
    """
    condition = "unless the sizing note says otherwise"
    bodies = {
        "heading, ordered marker not starting at 1": (
            f"## Execution autonomy\n\nagent-can-do-alone\n2. {condition}\n"
        ),
        "heading, ten is not one": (
            f"## Execution autonomy\n\nagent-can-do-alone\n10. {condition}\n"
        ),
        "heading, marker indented four columns": (
            f"## Execution autonomy\n\nagent-can-do-alone\n    - {condition}\n"
        ),
        "bullet, marker indented four columns past its content": (
            f"- **Autonomy:** agent-can-do-alone\n      - {condition}\n"
        ),
        "heading, empty marker": f"## Execution autonomy\n\nagent-can-do-alone\n+\n{condition}\n",
        # Codex on #462: a tab after the marker reaches column 4, not column 5, so a marker
        # indented eight columns is four past the content edge and cannot interrupt.
        "bullet, tab padding then an eight-column marker": (
            f"-\t**Autonomy:** agent-can-do-alone\n        - {condition}\n"
        ),
    }
    for where, body in bodies.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None, f"{where}: a condition Markdown keeps in the value was admitted"
        assert "is not a registered autonomy value" in refusal, f"{where}: {refusal}"


def test_a_block_markdown_starts_after_a_declaration_is_outside_it() -> None:
    """The declaration ends exactly where the rendered block ends, and not a line sooner or later.

    Two shapes the regex reader got wrong in the *closed* direction, both settled by the parser
    (ADR-0066). A `2.` line after a bullet item is outside that item, where any list may begin -
    the rule that `2.` cannot interrupt a paragraph protects the paragraph's own lines, and this
    one is not one of them - so GitHub renders a new numbered list, not a wrapped bullet. And a
    marker padded five or more columns does open a list item, one whose content is indented code:
    a non-empty item interrupts a paragraph whatever its first block is. Either way the condition
    is rendered as its own block below the value and never joined into the exact match. Under a
    heading that block is still in the section and scan-only; after a bullet it is a sibling item,
    outside the declaration exactly as a sibling bullet always was. Code in it is literal besides.
    """
    condition = "unless the sizing note says otherwise"
    outside = {
        "bullet, then an ordered item": f"- **Autonomy:** agent-can-do-alone\n2. {condition}\n",
        "heading, marker padded five spaces": (
            f"## Execution autonomy\n\nagent-can-do-alone\n-     {condition}\n"
        ),
        "bullet, then an item holding indented code": (
            f"- **Autonomy:** agent-can-do-alone\n1.     {condition}\n"
        ),
        "heading, marker padded two tabs": (
            f"## Execution autonomy\n\nagent-can-do-alone\n-\t\t{condition}\n"
        ),
    }
    for where, body in outside.items():
        assert claim._autonomy_refusal(body) is None, f"{where}: a separate block joined the value"
    # The block below a heading's value is still read: a refusal token there governs.
    below = claim._autonomy_refusal(
        "## Execution autonomy\n\nagent-can-do-alone\n-     this needs maintainer input\n"
    )
    assert below is None, "indented code inside the item is literal"
    below = claim._autonomy_refusal(
        "## Execution autonomy\n\nagent-can-do-alone\n- this needs maintainer input\n"
    )
    assert below is not None and "maintainer input" in below


def test_a_restrictive_bullet_governs_under_any_marker_markdown_accepts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`+` and an indent of up to three spaces make a bullet too, so they can carry a restriction.

    Codex on #462: `+ Autonomy: maintainer decision required` or `  - Autonomy: ...` beside a
    column-zero admitting bullet was never read, so the admitting one governed alone. Those
    shapes are now read the way R4 reads a table row: **scan-only**. They can refuse and can
    never admit, so the one registered declaration shape - a column-zero `-` or `*` bullet with a
    bare enum value - stays the only way through, and this widens what the gate can see without
    widening what it will accept.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    restrictive = {
        "plus marker": ("+ Autonomy: maintainer decision required\n", "maintainer decision"),
        "indented two": ("  - Autonomy: maintainer decision required\n", "maintainer decision"),
        "indented three, star": (
            "   * **Autonomy:** maintainer decision required\n",
            "maintainer decision",
        ),
        "plus marker, wrapped": (
            "+ **Autonomy:** agent-can-do-alone\n  pending a maintainer decision\n",
            "maintainer decision",
        ),
        # Codex on #462, second pass: a value naming no token still is not a registered value, and
        # a scan would have let it through. Every bullet is exact-checked; only one shape admits.
        "plus marker, unregistered": (
            "+ Autonomy: human review required\n",
            "is not a registered autonomy value",
        ),
        "indented, unregistered": (
            "  - **Autonomy:** agent-can-do-alone for the docs\n",
            "is not a registered autonomy value",
        ),
    }
    for where, (line, expected) in restrictive.items():
        for body in (line + admitting, admitting + line):
            refusal = claim._autonomy_refusal(body)
            assert refusal is not None, f"{where}: a restrictive bullet was ignored - fail-open"
            assert expected in refusal, f"{where}: {refusal}"

    # The scan-only half: none of these shapes may become a way to admit.
    for where, body in {
        "plus marker alone": "+ **Autonomy:** agent-can-do-alone\n",
        "indented alone": "  - **Autonomy:** agent-can-do-alone\n",
    }.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None and "only in a shape that cannot admit" in refusal, (
            f"{where}: a non-registered bullet shape admitted - fail-open"
        )

    body = "+ Autonomy: maintainer decision required\n" + admitting
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "maintainer decision" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_scan_only_prose_is_scanned_one_block_at_a_time() -> None:
    """A refusal token must be stated, not assembled from the ends of two blocks.

    Codex on #462: the heading remainder was joined into one string with its blank lines dropped,
    so `The reviewer is a human` followed by a blank line and `Action items are listed below`
    read as `human action` and refused a registered declaration. Each paragraph, and each list
    item, is now scanned on its own; a token still governs wherever it is actually written.
    """
    bodies = {
        "token halves across paragraphs": (
            "## Execution autonomy\n\nagent-can-do-alone\n\n"
            "The reviewer is a human\n\nAction items are listed below.\n"
        ),
        "token halves across list items": (
            "## Execution autonomy\n\nagent-can-do-alone\n\n- needs a human\n- action pending\n"
        ),
    }
    for where, body in bodies.items():
        assert claim._autonomy_refusal(body) is None, f"{where}: a token was fabricated"

    # Written in one paragraph, the same words are a statement and still govern.
    stated = "## Execution autonomy\n\nagent-can-do-alone\n\nThe upload needs a human\naction.\n"
    refusal = claim._autonomy_refusal(stated)
    assert refusal is not None and "human action" in refusal


def test_the_rest_of_an_autonomy_bullets_list_item_is_scanned_like_heading_prose() -> None:
    """What a bullet says about itself below its first paragraph is read for refusal tokens.

    The heading path scans the prose under its declaration; the bullet path read nothing past
    the declaration paragraph, so `- **Autonomy:** agent-can-do-alone` followed by an indented
    paragraph or nested bullet saying the upload is a maintainer decision was admitted. Same
    rule on both paths now: the item's remaining blocks are scan-only, a token governs, prose
    that names none changes nothing, and the next sibling bullet is outside the item.
    """
    refusing = {
        "indented paragraph after a blank line": (
            "- **Autonomy:** agent-can-do-alone\n\n  The upload is a maintainer decision.\n"
        ),
        "nested bullet": (
            "- **Autonomy:** agent-can-do-alone\n  - except the upload: maintainer decision\n"
        ),
    }
    for where, body in refusing.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None, f"{where}: a restriction inside the item was not read"
        assert "maintainer decision" in refusal, f"{where}: {refusal}"

    admitting = {
        "nested bullet naming no token": (
            "- **Autonomy:** agent-can-do-alone\n  - the tests are already written\n"
        ),
        "sibling bullet is outside the item": (
            "- **Autonomy:** agent-can-do-alone\n- **Blocker:** a maintainer decision on #1\n"
        ),
    }
    for where, body in admitting.items():
        assert claim._autonomy_refusal(body) is None, f"{where}: read as a restriction"


def test_a_heading_markdown_still_renders_is_read_wherever_it_is_indented() -> None:
    """Up to three spaces before `#` and closing hashes are still an ATX heading.

    A restrictive `## Execution autonomy` section indented one space was invisible to the heading
    pattern, so an admitting bullet beside it governed alone. Read now, and like a `+` bullet it
    cannot admit on its own: the registered shapes stay column-zero.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n"
    for where, section in {
        "indented one": " ## Execution autonomy\n\nmaintainer decision required\n",
        "indented three, closing hashes": (
            "   ## Execution autonomy ##\n\nmaintainer decision required\n"
        ),
    }.items():
        for body in (section + "\n" + admitting, admitting + "\n" + section):
            refusal = claim._autonomy_refusal(body)
            assert refusal is not None, f"{where}: a restrictive heading was ignored - fail-open"
            assert "maintainer decision" in refusal, f"{where}: {refusal}"

    alone = claim._autonomy_refusal(" ## Execution autonomy\n\nagent-can-do-alone\n")
    assert alone is not None and "only in a shape that cannot admit" in alone

    # Codex on #462: closing hashes need a space before them; `autonomy##` is the heading's text.
    # Since the read of `ef646ee` a heading carrying more than the field is a qualified key, so
    # it refuses on its qualifier rather than going unread - the glued hashes are that qualifier.
    glued = claim._autonomy_refusal("## Execution autonomy##\n\nagent-can-do-alone\n")
    assert glued is not None and "qualifier '##'" in glued, glued


def test_a_child_list_under_a_bullet_is_scanned_one_item_at_a_time() -> None:
    """A child list under an autonomy bullet is nested relative to *that bullet's* content
    column, so two child items are two blocks and a token must not be assembled across them
    (Codex on #462). The parser draws the item boundaries; this asserts the scan respects them.
    """
    children = (
        "- **Autonomy:** agent-can-do-alone\n"
        "    - The reviewer is a human\n"
        "    - Action items are listed below\n"
    )
    assert claim._autonomy_refusal(children) is None, "a token was assembled across child items"


def test_a_refusing_token_begins_a_word_and_may_end_inside_one() -> None:
    """Codex on #462 (read of `5f41bae`): the token test was a bare substring, so `nonhuman
    actions` in a bullet's remainder named `human action` and refused a registered
    declaration. The token must begin a word; it may end mid-word, so the plural the corpus
    writes still governs, and a separator before it is a boundary, so `non-human action`
    flattens to one that names the token - the fail-closed reading of a negation.
    """
    admitting = "- **Autonomy:** agent-can-do-alone\n\n"
    for prose in (
        "  Covers nonhuman action classification.\n",
        "  See the subhuman-action note.\n",
        "  The chairmaintainer decision is logged.\n",
    ):
        inside = claim._autonomy_refusal(admitting + prose)
        assert inside is None, f"{prose!r}: a token inside a word refused"
    for prose in (
        "  Needs human actions first.\n",
        "  Pending (human action).\n",
        "  Non-human action only.\n",
        "  Maintainer decisions are tracked.\n",
    ):
        names = claim._autonomy_refusal(admitting + prose)
        assert names is not None, f"{prose!r}: a token at a word's start was not read"
        assert "restrictive statement governs" in names, f"{prose!r}: {names}"


def test_fenced_code_is_literal_and_declares_nothing() -> None:
    """A fenced example can neither restrict nor admit, and cannot stand in for a grooming block.

    Codex on #462: a fenced snippet containing `Autonomy | maintainer decision required` was
    scanned as a real table row and refused an admitting issue. The parser returns code as a
    literal block (ADR-0066), so no declaration pattern ever sees it - and the grooming marker is
    looked for among HTML blocks, so an example block quoting the marker is not the authoritative
    source either.
    """
    admitting = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    assert (
        claim._autonomy_refusal(admitting + "```\nAutonomy | maintainer decision required\n```\n")
        is None
    ), "a fenced table row was read as a restriction"
    tilde_fence = admitting + "~~~markdown\n- **Autonomy:** maintainer decision\n~~~\n"
    assert claim._autonomy_refusal(tilde_fence) is None, "a fenced bullet was read as a restriction"

    fenced_only = claim._autonomy_refusal("```\n- **Autonomy:** agent-can-do-alone\n```\n")
    assert fenced_only is not None and "declares no Execution autonomy" in fenced_only

    quoted_marker = (
        "```markdown\n<!-- tether-grooming-v1 -->\n- **Autonomy:** agent-can-do-alone\n```\n\n"
        "- **Autonomy:** maintainer decision required\n"
    )
    refusal = claim._autonomy_refusal(quoted_marker)
    assert refusal is not None and "maintainer decision" in refusal
    # Codex on #462 (read of `f9fc1f0`): a `<pre>` is the raw HTML spelling of a fence and is
    # literal in the same way - as a keyed piece, in a heading's section, as a heading's value.
    for pre in (
        "<pre><code>Autonomy: maintainer decision required</code></pre>\n",
        "<pre>Autonomy: maintainer decision required</pre>\n",
        "<pre>- **Autonomy:** maintainer decision required</pre>\n",
        "<div><pre>maintainer decision required</pre></div>\n",
    ):
        assert claim._autonomy_refusal(admitting + pre) is None, pre
    as_value = claim._autonomy_refusal(
        "<h2>Execution autonomy</h2><pre>agent-can-do-alone</pre>\n\n"
        "- **Autonomy:** agent-can-do-alone\n"
    )
    assert as_value is not None and "not a registered" in as_value, as_value

    # An unclosed fence runs to the end of the body, as Markdown renders it.
    unclosed = claim._autonomy_refusal(admitting + "```\n| autonomy | maintainer decision |\n")
    assert unclosed is None


def test_the_structure_the_regex_reader_misread_is_read_as_github_renders_it() -> None:
    """The four shapes Codex's eighth read of #462 found still wrong, now settled by the parser.

    Each was a fail-open: a structure the expressions did not see was a restriction the gate did
    not read. ADR-0066 replaced them with a CommonMark parser, and these pin that the gate now
    reads each shape where a reader of the rendered issue sees it.
    """
    restriction = "maintainer decision required"
    # A tab before `##` inside a list item is four columns of indent, so the heading belongs to
    # the item; the regex reader ended the surrounding section there and lost the paragraph.
    tab_heading = (
        f"- Notes\n\t## Execution autonomy\n\n{restriction}\n\n- **Autonomy:** agent-can-do-alone\n"
    )
    refusal = claim._autonomy_refusal(tab_heading)
    assert refusal is not None and "maintainer decision" in refusal
    # A backtick fence is not closed by tildes, so the restriction after the `~~~` is still code.
    mixed_closer = (
        "- **Autonomy:** agent-can-do-alone\n\n```\nexample\n~~~\n"
        f"- **Autonomy:** {restriction}\n```\n"
    )
    assert claim._autonomy_refusal(mixed_closer) is None, "a mixed-character closer ended a fence"
    # A lazy continuation inside a nested item is part of that item's paragraph.
    lazy = "- **Autonomy:** agent-can-do-alone\n  - the sizing question is a\nmaintainer decision\n"
    refusal = claim._autonomy_refusal(lazy)
    assert refusal is not None and "maintainer decision" in refusal
    # Four spaces after a blank line open indented code, which is literal however much it looks
    # like a table row. Inside a list item the same four spaces are two past the content column
    # and open a paragraph instead, which is prose and is read.
    heading = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    indented_code = f"{heading}    Autonomy | {restriction}\n"
    assert claim._autonomy_refusal(indented_code) is None, "indented code was read as a row"
    in_item = f"- **Autonomy:** agent-can-do-alone\n\n    Autonomy | {restriction}\n"
    refusal = claim._autonomy_refusal(in_item)
    assert refusal is not None and "maintainer decision" in refusal

    # The four bodies exactly as the eighth read wrote them.
    exact = {
        "a tab before ## continues the paragraph, so the condition stays in the value": (
            "## Execution autonomy\n\nagent-can-do-alone\n\t## note\n"
            "unless a maintainer decision is made\n",
            "maintainer decision",
        ),
        "a mixed-character closer leaves the fence open, so the bullet is literal": (
            "```\n```~~~\n- **Autonomy:** agent-can-do-alone\n",
            "declares no Execution autonomy",
        ),
        "a lazy continuation completes the nested item's restriction": (
            "- **Autonomy:** agent-can-do-alone\n  - This needs a human\naction before upload\n",
            "human action",
        ),
    }
    for why, (body, expected) in exact.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None and expected in refusal, f"{why}: {refusal}"
    literal_row = (
        "    Autonomy | maintainer decision required\n\n- **Autonomy:** agent-can-do-alone\n"
    )
    assert claim._autonomy_refusal(literal_row) is None, "indented code governed a claim"


def test_text_outside_the_declaration_paragraph_does_not_join_it() -> None:
    """The permissive half of the test above: only the declaration's own paragraph is its value.

    Joining too much is the over-refusal `test_ordinary_prose_under_the_declaration...` exists to
    prevent, moved to the bullet path. Every well-groomed block follows its `Autonomy:` bullet with
    another bullet, and that bullet's own wrapped lines belong to it and not to the declaration.
    """
    bodies = {
        "next bullet, and that bullet's own continuation": (
            "- **Autonomy:** agent-can-do-alone.\n"
            "- **Why it is agent-doable:** two files, no schema.\n"
            "  The whole diff is a parser and its tests.\n"
        ),
        "nested bullet": "- **Autonomy:** agent-can-do-alone\n  - the tests are already written\n",
        "blank line, then prose": (
            "- **Autonomy:** agent-can-do-alone\n\nThe tests are already written.\n"
        ),
        "heading straight after the bullet": (
            "- **Autonomy:** agent-can-do-alone\n## Related work\n\nNothing overlaps.\n"
        ),
        # Codex on #462: an ordered list is a list too. Joining `1. Reproduce the issue` into the
        # value refused a registered declaration for being followed by ordinary Markdown.
        "ordered list straight after the bullet": (
            "- **Autonomy:** agent-can-do-alone\n1. Reproduce the issue\n2. Fix it\n"
        ),
        "ordered list with a parenthesis straight after the bullet": (
            "- **Autonomy:** agent-can-do-alone\n1) Reproduce the issue\n"
        ),
        "ordered list straight after a heading value": (
            "## Execution autonomy\n\nagent-can-do-alone\n1. Reproduce the issue\n"
        ),
        # Codex on #462: Markdown reads `01.` as a list starting at 1, so it interrupts too.
        "zero-padded ordered list straight after the bullet": (
            "- **Autonomy:** agent-can-do-alone\n01. Reproduce the issue\n"
        ),
        "zero-padded ordered list straight after a heading value": (
            "## Execution autonomy\n\nagent-can-do-alone\n000000001) Reproduce the issue\n"
        ),
    }
    for where, body in bodies.items():
        assert claim._autonomy_refusal(body) is None, f"{where}: read as part of the declaration"


def test_a_grooming_block_that_declares_no_autonomy_does_not_fall_back_to_the_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A present grooming block is authoritative even when it declares nothing.

    The sibling above establishes that a grooming block supersedes a stale body. That rule had a
    hole: the source loop skipped a source that yielded no declarations, so a grooming block
    carrying a `Status:` and no `Autonomy:` fell through to the body — and the body's stale
    `agent-can-do-alone` admitted the claim. That is precisely the value the grooming pass dropped,
    governing because it was dropped, which inverts the rule it is supposed to serve.

    Refusing here is the fail-closed reading and it costs little: an issue whose latest grooming
    pass did not state an autonomy has not stated one, and `_autonomy_refusal` already treats
    silence as refusal rather than consent. A re-groom corrects it.
    """
    stale = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    bodies = {
        "block with content but no autonomy": (
            stale + "<!-- tether-grooming-v1 -->\n\n- **Status:** blocked.\n- **Owner:** bioedca\n"
        ),
        # The capture is *exactly* empty here - the marker ends the body with no trailing newline -
        # so keying the source choice on the captured text rather than on the marker sent it
        # straight back to the stale heading above. An empty groom is still a groom.
        "marker at end of body, empty capture": stale + "<!-- tether-grooming-v1 -->",
        "block holding only a nested comment": (
            stale + "<!-- tether-grooming-v1 --><!-- nothing to report -->"
        ),
    }
    for where, body in bodies.items():
        routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
        fake = _install(monkeypatch, Fake(routes))
        with pytest.raises(SystemExit) as exit_info:
            claim._cmd_claim(_args(issue=7))
        assert exit_info.value.code == claim.EXIT_INELIGIBLE, where
        assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]], (
            f"{where}: the stale declaration the grooming pass dropped created a claim ref"
        )
        assert "autonomy" in capsys.readouterr().err.lower(), where


def test_a_grooming_marker_anywhere_but_its_own_top_level_line_refuses_the_body() -> None:
    """A marker that cannot start a grooming block is not read around; the body is refused.

    A grooming block is a marker on its own line at the top level and the top-level blocks after
    it (ADR-0066). Written inside a list item or block quote, or inline in a paragraph, the marker
    still announces that the text above it is superseded, and a reader that cannot say where the
    superseded text ends must not pick a side. Refusing costs one re-groom; guessing could let a
    stale admitting line govern. Inside a fence it is literal and is neither, as the fence test
    pins.
    """
    stale = "## Execution autonomy\n\nagent-can-do-alone\n\n"
    misplaced = {
        "inside a list item": stale
        + "- Groomed:\n  <!-- tether-grooming-v1 -->\n  - **Status:** blocked\n",
        "inside a block quote": stale
        + "> <!-- tether-grooming-v1 -->\n> - **Autonomy:** agent-can-do-alone\n",
        "inline in a paragraph": stale + "Groomed <!-- tether-grooming-v1 --> on 2026-08-13.\n",
        "in a table cell": stale
        + "| Note | Value |\n| --- | --- |\n| <!-- tether-grooming-v1 --> | x |\n",
    }
    for where, body in misplaced.items():
        refusal = claim._autonomy_refusal(body)
        assert refusal is not None and "grooming block cannot start" in refusal, (
            f"{where}: {refusal}"
        )

    # On its own top-level line, with nothing shown beside it, it is the authoritative source.
    for marker in ("<!-- tether-grooming-v1 -->", "<!--tether-grooming-v1--><!-- by hand -->"):
        refusal = claim._autonomy_refusal(
            stale + marker + "\n- **Autonomy:** maintainer decision\n"
        )
        assert refusal is not None and "maintainer decision" in refusal, marker
    # Text on the marker's line is part of the same raw HTML block, shown literally on the page
    # and once discarded with the marker (Codex on #462); that line is not the marker on its own.
    dressed = claim._autonomy_refusal(
        stale + "<!--tether-grooming-v1--> **groomed**\n- **Autonomy:** maintainer decision\n"
    )
    assert dressed is not None and "grooming block cannot start" in dressed, dressed


def test_the_refusal_names_the_declared_value_so_a_worker_knows_not_to_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "## Execution autonomy\n\nexternal/human action required\n"
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit):
        claim._cmd_claim(_args(issue=7))
    err = capsys.readouterr().err
    assert "external/human action required" in err
    assert "agent-can-do-alone" in err


def test_autonomy_is_read_before_the_comment_page_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body that bars agent work is decided from the issue alone - no comment pagination.

    Not a micro-optimisation: `_paginate` walks up to twenty pages, and an issue no agent may work
    should cost one GET to refuse.
    """
    body = "## Execution autonomy\n\nexternal/human action required\n"
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(body=body))})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit):
        claim._cmd_claim(_args(issue=7))
    assert not [c for c in fake.calls if "comments" in c[1]]


def _unmodelled(body: str) -> Any:
    raise claim._markdown.MarkdownStructureError("unmodelled block token 'tfoot_open'")


def test_a_body_the_parser_cannot_model_is_an_error_not_a_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The structure reader raises on a block token it does not model and when the pinned parser
    itself fails (ADR-0066). Neither is a reading of the issue, so neither may reach exit 3: a
    compliant agent believes `ineligible` and stops, and a false verdict costs approved work
    (#315). Before this the error surfaced through `main`'s `ValueError` arm as *input exceeds
    safe processing limits* - the right exit code under a message naming the wrong cause.
    """
    monkeypatch.setattr(claim._markdown, "parse", _unmodelled)
    with pytest.raises(claim.ClaimError) as excinfo:
        claim._autonomy_refusal(GROOMED_BODY)
    assert not isinstance(excinfo.value, claim.IneligibleError)
    assert "tfoot_open" in str(excinfo.value)

    _install(monkeypatch, Fake(_routes()))
    monkeypatch.setattr(
        claim.sys, "argv", ["claim.py", "claim", "--issue", "7", "--vendor", "claude"]
    )
    assert claim.main() == 2
    err = capsys.readouterr().err
    assert err.startswith("error: #7 body could not be read as Markdown"), err
    assert "tfoot_open" in err
    assert "ineligible" not in err
    assert "safe processing limits" not in err


# --------------------------------------------------------------------- the mutex


def test_claim_wins_and_reports_the_server_assigned_generation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, Fake(_routes()))
    claim._cmd_claim(_args(issue=7))
    record = json.loads(capsys.readouterr().out)
    assert record["branch"] == "agent/issue-7"
    assert record["generation"] == 42
    assert record["base_sha"] == HEAD


def test_losing_the_race_is_an_ordinary_exit_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {("POST", "/repos/bioedca/tether/git/refs"): (422, {"message": "Reference already exists"})}
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_LOST
    captured = capsys.readouterr().err
    assert "already exists" in captured
    assert "Traceback" not in captured
    assert str(ROOT) not in captured


def test_a_failed_label_write_does_not_void_the_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The label is a mirror, never the lock: the next agent still gets 422 either way."""
    routes = _routes({("POST", "/repos/bioedca/tether/issues/7/labels"): (403, None)})
    _install(monkeypatch, Fake(routes))
    claim._cmd_claim(_args(issue=7))
    record = json.loads(capsys.readouterr().out)
    assert record["generation"] == 42
    assert record["label_mirror"] is False


# --------------------------------------------------------------------- fencing


def test_check_passes_only_for_the_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, Fake(_routes()))
    claim._cmd_check(_args(issue=7, generation=42))


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"id": 99, "activity_type": "branch_creation"}], "was reclaimed"),
        ([], "no claim ref"),
        ([{"id": 99, "activity_type": "push"}], "no claim ref"),
        # The activity API keeps the creation entry after the ref is deleted, so reading only
        # creations would tell a reaped worker it still holds the claim. Verified live.
        (
            [
                {"id": 42, "activity_type": "branch_creation"},
                {"id": 77, "activity_type": "branch_deletion"},
            ],
            "no claim ref",
        ),
    ],
    ids=["reclaimed", "no-ref", "pushes-are-not-creations", "reaped-then-stale-creation-remains"],
)
def test_a_superseded_worker_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[dict[str, Any]],
    message: str,
) -> None:
    routes = _routes({("GET", "/repos/bioedca/tether/activity"): (200, entries)})
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_check(_args(issue=7, generation=42))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert message in capsys.readouterr().err


def test_release_refuses_to_delete_a_successors_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/activity"): (
                200,
                [{"id": 99, "activity_type": "branch_creation"}],
            )
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert "releasing would delete a successor" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]


def test_a_recreated_ref_supersedes_the_deleted_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclaim is delete-then-recreate: the successor's creation must win over the deletion."""
    _install(
        monkeypatch,
        Fake(
            _routes(
                {
                    ("GET", "/repos/bioedca/tether/activity"): (
                        200,
                        [
                            {"id": 42, "activity_type": "branch_creation"},
                            {"id": 77, "activity_type": "branch_deletion"},
                            {"id": 91, "activity_type": "branch_creation"},
                        ],
                    ),
                }
            )
        ),
    )
    assert claim._generation(7) == 91


def test_release_refuses_when_the_ref_exists_but_its_generation_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The destructive path must not be the permissive one.

    `check` reads an unreadable generation as fail-closed. `release` used to read the same fact as
    authorization and delete, so a stale worker could destroy a live successor's mutex ref and
    requeue an issue someone was mid-way through. The activity feed can lag the ref - `claim`
    itself handles "201 but no activity record yet" - so this is reachable in the reclaim window.
    """
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (200, {"object": {}}),
            ("GET", "/repos/bioedca/tether/activity"): (200, []),
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert exit_info.value.code == claim.EXIT_SUPERSEDED
    assert "unreadable" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]


def test_release_of_an_absent_ref_cleans_labels_without_deleting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A legitimately reaped worker must still be able to reset the labels."""
    routes = _routes(
        {
            ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (404, None),
            ("GET", "/repos/bioedca/tether/activity"): (200, []),
        }
    )
    fake = _install(monkeypatch, Fake(routes))
    claim._cmd_release(_args(issue=7, generation=42, vendor="claude"))
    assert json.loads(capsys.readouterr().out)["ref"] == "absent"
    assert not [c for c in fake.calls if c[0] == "DELETE" and "git/refs" in c[1]]
    assert [c for c in fake.calls if c[0] == "POST" and "labels" in c[1]]


def test_the_fence_filters_by_activity_type_so_pushes_cannot_evict_the_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unfiltered feed is newest-first and mixes in pushes, which can hide the creation."""
    fake = _install(monkeypatch, Fake(_routes()))
    claim._generation(7)
    activity = [p for _, p in fake.calls if "/activity" in p]
    assert activity, "no activity call made"
    assert all(
        "activity_type=branch_creation" in p or "activity_type=branch_deletion" in p
        for p in activity
    )


def test_approval_discovery_pages_past_the_first_hundred_comments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A re-approval past comment 100 must be found, not reported as an edited-after-approval."""
    page1 = [{"user": {"login": "bioedca"}, "body": "chatter"} for _ in range(100)]
    page2 = [{"user": {"login": "bioedca"}, "body": f"Re-approved.\n\n{MARKER}"}]

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if "/issues/7/comments" in path:
            return 200, page2 if "page=2" in path else page1
        if path.endswith("/issues/7"):
            return 200, _issue()
        if "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            return 201, {}
        if "/activity" in path:
            return 200, [{"id": 42, "activity_type": "branch_creation"}]
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    monkeypatch.setattr(claim, "_scope_hash", lambda title, body: DIGEST)
    claim._cmd_claim(_args(issue=7))
    assert json.loads(capsys.readouterr().out)["generation"] == 42


@pytest.mark.parametrize("status", [403, 404, 500, 502], ids=["forbidden", "missing", "500", "502"])
def test_reserve_adr_refuses_to_guess_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    """Dropping a failed read meant "no ADRs exist" -> 0001, which collides with a real ADR.

    The reservation namespace is legitimately empty today, so the contents read is the only source
    of used numbers: one 403 was enough, and the CAS cannot catch it because no *ref* holds 0001.
    """
    posted: list[str] = []

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/git/matching-refs/adr-reservations"):
            return 200, []
        if method == "GET" and path.endswith("/contents/docs/adr"):
            return status, None
        if method == "GET" and "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            posted.append(body["ref"])
            return 201, {}
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    assert claim.main.__module__  # module import sanity
    with pytest.raises(claim.ClaimError, match="refusing to guess"):
        claim._cmd_reserve_adr(_args(attempts=8))
    assert posted == [], "no reservation may be created when the number is a guess"


def test_generation_never_comes_from_commit_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """committedDate is client-settable, so the generation must come from the activity API alone."""
    fake = _install(monkeypatch, Fake(_routes()))
    claim._generation(7)
    paths = [path for _, path in fake.calls]
    assert any("/activity?ref=refs/heads/agent/issue-7" in p for p in paths)
    assert not [p for p in paths if "/commits" in p or "graphql" in p]


# --------------------------------------------------------------------- ADR reservation


def test_reserve_adr_skips_taken_numbers_and_never_uses_a_tag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-version tag breaks hatch-vcs version derivation and turns main red."""
    posted: list[str] = []

    def transport(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and path.endswith("/git/matching-refs/adr-reservations"):
            return 200, [{"ref": "refs/adr-reservations/0058"}]
        if method == "GET" and path.endswith("/contents/docs/adr"):
            return 200, [{"name": "0057-a.md"}, {"name": "0052-b.md"}]
        if method == "GET" and "git/ref/heads/main" in path:
            return 200, {"object": {"sha": HEAD}}
        if method == "POST" and path.endswith("/git/refs"):
            posted.append(body["ref"])
            return (422, None) if body["ref"].endswith("0059") else (201, {})
        return 200, None

    monkeypatch.setattr(claim, "_request", transport)
    claim._cmd_reserve_adr(_args(attempts=8))
    assert json.loads(capsys.readouterr().out)["adr"] == "0060"
    assert posted == ["refs/adr-reservations/0059", "refs/adr-reservations/0060"]
    assert not [ref for ref in posted if ref.startswith("refs/tags/")]


# ------------------------------------------------------------ the frozen approval digest

# A frozen snapshot and the digest it must always produce. This normalization is a **published
# contract**: the markers on #188, #189, #216 and #218 were computed with it, and all four were
# re-verified against this re-homed implementation and reproduced their published digests
# byte-for-byte on 2026-07-30. Those checks need the GitHub API, so this pinned pair is what CI can
# enforce offline. It moved here with the function it pins, from the withdrawn lease helper.
PIN_TITLE = "build(packaging): pin the wheel"
PIN_BODY = "Acceptance criteria\n\n- [ ] one source of truth\n"
PIN_DIGEST = "9906a25c28495a649934b2e809e2b70c136b724b25b025db6907f1797100e0dc"
NON_ASCII_DIGEST = "329909edc2df9090ff2861ea36485b53039d0bd28f79d91eeac2bb2a7b9cb8c8"


def test_the_scope_digest_is_pinned_for_a_known_snapshot() -> None:
    assert claim._scope_hash(PIN_TITLE, PIN_BODY) == PIN_DIGEST


@pytest.mark.parametrize(
    "body",
    [
        "Acceptance criteria\r\n\r\n- [ ] one source of truth\r\n",
        "Acceptance criteria\r\r- [ ] one source of truth\r",
        "Acceptance criteria\n\n- [ ] one source of truth\n\n\n\n",
    ],
    ids=["crlf", "cr", "extra-trailing-newlines"],
)
def test_the_scope_digest_normalizes_line_endings_and_trailing_newlines(body: str) -> None:
    assert claim._scope_hash(PIN_TITLE, body) == PIN_DIGEST


def test_the_scope_digest_is_pinned_for_a_non_ascii_snapshot() -> None:
    """Pins ensure_ascii=False: \\uXXXX-escaping before hashing changes every non-ASCII issue."""
    body = "Rationale: the γ correction factor — see Hellenkamp 2018.\n"
    assert claim._scope_hash("fix(fret): γ factor", body) == NON_ASCII_DIGEST


def test_the_scope_digest_separates_title_from_body() -> None:
    """Moving text across the title/body boundary must change the digest."""
    assert claim._scope_hash("ab", "c") != claim._scope_hash("a", "bc")


def test_scope_hash_reads_the_snapshot_from_the_api_and_renders_a_binding_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One snapshot source for both sides of the approval.

    The maintainer renders a marker and an agent later recomputes the digest; if those read the
    snapshot differently the approval silently stops binding. Reading a body from a file the caller
    prepared is how a prepended BOM could change the digest of an unchanged issue - there is no file
    in this path at all - so the two cannot diverge that way.
    """
    routes = _routes(
        {("GET", "/repos/bioedca/tether/issues/7"): (200, _issue(title=PIN_TITLE, body=PIN_BODY))}
    )
    monkeypatch.setattr(claim, "_request", Fake(routes))
    claim._cmd_scope_hash(_args(issue=7))
    printed = json.loads(capsys.readouterr().out)
    assert printed["criteria_sha256"] == PIN_DIGEST
    marker = '<!-- tether-agent-ready {"version":1,"criteria_sha256":"' + PIN_DIGEST + '"} -->'
    assert printed["marker"] == marker
    # Deliberately NOT via _install: the real _scope_hash must run on both sides, so this asserts
    # the rendered marker is the exact form _approval_binds accepts, not merely a similar one.
    assert claim._approval_binds(
        {"title": PIN_TITLE, "body": PIN_BODY},
        [{"user": {"login": "bioedca"}, "body": f"Approved as written.\n\n{marker}\n"}],
        "bioedca",
    )


def test_scope_hash_refuses_a_pull_request_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PR's title and body are not an approvable scope, so digesting one is a wrong answer.

    `/issues/{n}` answers for pull requests too, so the refusal has to be explicit. It lives in
    `_issue`, shared with the eligibility path, rather than being repeated in each caller.
    """
    routes = {("GET", "/repos/bioedca/tether/issues/7"): (200, {"pull_request": {"url": "x"}})}
    monkeypatch.setattr(claim, "_request", Fake(routes))
    with pytest.raises(claim.ClaimError, match="not an issue"):
        claim._cmd_scope_hash(_args(issue=7))


def _args(**values: Any) -> Any:
    defaults = {"vendor": "claude", "owner": "bioedca", "base": None, "attempts": 16}
    defaults.update(values)
    return type("Args", (), defaults)()


# ------------------------------------------------- the activity index's read-after-write lag


class LaggingFake(Fake):
    """Answers the activity endpoint with nothing until the ``n``-th read.

    The shape measured live on this repository's first claim: `POST /git/refs` returned 201 and the
    `branch_creation` entry was not yet readable. It appeared moments later.
    """

    def __init__(self, routes: Routes, *, appears_on: int) -> None:
        super().__init__(routes)
        self.appears_on = appears_on
        self.activity_reads = 0

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method == "GET" and "/activity" in path:
            self.activity_reads += 1
            if self.activity_reads < self.appears_on:
                self.calls.append((method, path))
                return 200, []
        return super().__call__(method, path, body)


def test_a_late_activity_record_is_waited_for_not_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect the pilot's very first live claim hit, and it failed in the worst available way.

    The ref existed, so the mutex was taken and every other agent got 422 - while the caller raised
    before the label mirror, leaving the issue reading `status:ready` with no `agent:*` label. The
    board said the work was free and the mutex said it was taken, and the caller was told to stop.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    fake = _install(monkeypatch, LaggingFake(_routes(), appears_on=3))
    claim._cmd_claim(_args(issue=7))

    assert fake.activity_reads >= 3, "it must actually re-read rather than sleep once and give up"
    assert json.loads(capsys.readouterr().out)["generation"] == 42
    # The mirror runs exactly once on the success path, so the board and the mutex agree.
    adds = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/labels")]
    assert len(adds) == 2, f"agent:<vendor> and status:in-progress, once each: {adds}"


def test_a_record_that_never_appears_leaves_the_ref_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It must NOT delete, and the reason is a TOCTOU both reviewers refused independently.

    An earlier version deleted the ref after checking its tip still equalled the SHA this call
    created it at. `GET`-compare-`DELETE` is not atomic, `DELETE /git/refs` takes no expected-SHA
    precondition, and the base SHA is **not a claim identity**: a successor claiming the same issue
    while the default branch has not moved creates the ref at exactly the same SHA, so the guard
    passes on a ref that is no longer ours.

    Leaking a claim costs one reaper cycle. Deleting a successor's claim puts two workers on one
    issue - the single failure the mutex exists to prevent. This asserts the trade by asserting on
    what does not happen.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    fake = _install(monkeypatch, LaggingFake(_routes(), appears_on=10_000))

    with pytest.raises(claim.ClaimError, match="NOT deleted"):
        claim._cmd_claim(_args(issue=7))

    assert not [c for c in fake.calls if c[0] == "DELETE"], (
        f"no delete may be issued on this path at all: {fake.calls}"
    )


def test_the_same_base_sha_interleaving_cannot_reach_a_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit's exact interleaving, pinned so a future 'safe' guard cannot reintroduce it.

    The reaper deletes the ref and a successor recreates it at the SAME unchanged default-branch
    SHA. Every tip comparison a claimant could make still passes, because the SHA is not an
    identity. The only defence is not having a delete on this path.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    routes = _routes(
        {
            # A successor's ref, indistinguishable from ours: identical SHA.
            ("GET", "/repos/bioedca/tether/git/ref/heads/agent/issue-7"): (
                200,
                {"object": {"sha": HEAD}},
            )
        }
    )
    fake = _install(monkeypatch, LaggingFake(routes, appears_on=10_000))

    with pytest.raises(claim.ClaimError, match="NOT deleted"):
        claim._cmd_claim(_args(issue=7))

    assert not [c for c in fake.calls if c[0] == "DELETE"]


def test_the_unfenced_message_tells_the_caller_not_to_reclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller's next move differs from every other failure, so the message must say it.

    Exit 4 means *lost, stand down*. This is *held but unusable*, and re-claiming would get a 422
    and read as lost - so the message says do not re-claim, names the reaper as the resolver, and
    points at #303 for the case where the record never lands at all.
    """
    monkeypatch.setattr(claim.time, "sleep", lambda _seconds: None)
    _install(monkeypatch, LaggingFake(_routes(), appears_on=10_000))
    with pytest.raises(claim.ClaimError) as info:
        claim._cmd_claim(_args(issue=7))
    message = str(info.value)
    assert "Do not re-claim" in message
    assert "reaper" in message
    assert "#303" in message, "the residual is a filed issue, not a docstring note"


def test_the_wait_is_bounded_and_does_not_poll_indefinitely() -> None:
    """A read-after-write wait, not the coordination polling ADR-0057 retired.

    That was 977 `wait_*` calls waiting on other agents. This waits on one server's own index for a
    write it has already acknowledged, and it is bounded by a constant rather than by an outcome.
    """
    assert claim.GENERATION_ATTEMPTS[0] == 0.0, "the first read happens immediately"
    assert sum(claim.GENERATION_ATTEMPTS) <= 20.0, "a claim must not hang on a lagging index"


# ----------------------------------------- transport is not a verdict, and TLS is not the network


def _cert_error(message: str) -> Any:
    """An ``SSLCertVerificationError`` shaped like the one OpenSSL actually raises.

    ``verify_message`` is the path-free field the fix reads, and it is not settable through the
    constructor.
    """
    error = claim.ssl.SSLCertVerificationError(1, f"[SSL: CERTIFICATE_VERIFY_FAILED] {message}")
    error.verify_message = message
    return error


def _raises(exc: BaseException) -> Any:
    def call(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return call


def _transport(monkeypatch: pytest.MonkeyPatch, reason: BaseException) -> None:
    """Make every real HTTP call fail at the socket, the way a proxy or an outage does."""
    monkeypatch.setattr(claim, "_token", lambda: "t")
    monkeypatch.setattr(
        claim.urllib.request, "urlopen", _raises(claim.urllib.error.URLError(reason))
    )


class _Answer:
    """The little of a ``urlopen`` result ``_request`` actually touches, and no more."""

    def __init__(self, payload: bytes | BaseException, status: int = 200) -> None:
        """Carry either the bytes to hand back or the exception to raise instead of them."""
        self._payload = payload
        self.status = status

    def __enter__(self) -> _Answer:
        """``urlopen`` is used as a context manager, so this stands in for one."""
        return self

    def __exit__(self, *_exc: object) -> bool:
        """Never suppress: a test that swallowed its own failure would pass for nothing."""
        return False

    def read(self) -> bytes:
        """Hand back the body, or fail the way a real response fails part-way through one."""
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("payload", "named"),
    [
        pytest.param(b"<html>502 Bad Gateway</html>", "JSONDecodeError", id="not-json"),
        pytest.param(claim.http.client.IncompleteRead(b"{"), "IncompleteRead", id="cut-short"),
        pytest.param(TimeoutError("timed out"), "TimeoutError", id="stalled-mid-read"),
    ],
)
def test_an_answer_that_cannot_be_read_leaves_request_as_a_claim_error(
    monkeypatch: pytest.MonkeyPatch, payload: bytes | BaseException, named: str
) -> None:
    """``ClaimError`` is the promise every caller holds; only the transport half of it was kept.

    ``_request`` converted a socket failure and returned an HTTP status, so both of those arrive as
    something a caller can handle. The success path did not: ``response.read()`` and ``json.loads``
    ran *inside* the ``try`` but past every ``except``, so a proxy's HTML error page or a connection
    dropped mid-body came out as a raw ``ValueError`` or ``IncompleteRead``.

    That is not a tidiness point. ``triage._verdict_at_head`` documents itself as failing **soft** —
    an unreadable comment list means *no verdict seen*, withholding an authority rather than
    granting one — and implements it as ``except claim.ClaimError``. For anything but a transport
    error the documented soft failure was a hard crash of the whole triage run (CodeRabbit on #407).
    """
    monkeypatch.setattr(claim, "_token", lambda: "t")
    monkeypatch.setattr(claim.urllib.request, "urlopen", lambda *_a, **_k: _Answer(payload))
    with pytest.raises(claim.ClaimError) as caught:
        claim._request("GET", "/repos/o/r/issues/1/comments")
    assert named in str(caught.value)
    assert "could not be read" in str(caught.value)


class _UnreadableBody:
    """An ``HTTPError`` file object that fails where ``error.read()`` reads it."""

    def read(self, *_a: object) -> bytes:
        """The failure itself: the body ends before the length the headers promised."""
        raise claim.http.client.IncompleteRead(b"{")

    def close(self) -> None:
        """``HTTPError`` closes its file object on the way out, and that must not fail too."""
        return None


def test_a_status_survives_a_body_that_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same defect one branch over, and Python's scoping is why (CodeRabbit's `Major` on #407).

    ``error.read()`` runs INSIDE the ``HTTPError`` handler, and an exception raised inside a handler
    is not offered to its siblings — so the guard added for the success path could never have caught
    this one.

    Degraded rather than raised, deliberately. The status line arrived intact, so the answer is
    known even though the body is not, and it is the same loss the unparseable-body branch beside it
    already accepts. ``_request`` promises HTTP errors are *returned* — 422 is an answer — and
    raising here would falsify that for a 404 whose body happened to be truncated.
    """
    monkeypatch.setattr(claim, "_token", lambda: "t")
    monkeypatch.setattr(
        claim.urllib.request,
        "urlopen",
        _raises(
            claim.urllib.error.HTTPError("https://api", 404, "Not Found", {}, _UnreadableBody())  # type: ignore[arg-type]
        ),
    )
    assert claim._request("GET", "/repos/o/r/issues/1") == (404, None)


def test_a_transport_failure_on_the_eligibility_read_is_an_error_not_a_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect: an approved issue reported to an agent as unapproved.

    Exit 3 is ``EXIT_INELIGIBLE``, which ``AGENTS.md`` defines as *do not work it*, and a compliant
    agent obeys it. So the blanket ``except ClaimError`` around ``_check_eligible`` turned every
    network or TLS failure into a scope verdict about work nobody managed to read.
    """
    monkeypatch.setattr(claim, "_check_eligible", _raises(claim.TransportError("no answer")))
    with pytest.raises(claim.TransportError):
        claim._cmd_claim(_args(issue=7))
    assert "ineligible" not in capsys.readouterr().err


def test_that_transport_failure_reaches_the_shell_as_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through ``main``, which is what a worker's shell actually sees."""
    monkeypatch.setattr(claim, "_check_eligible", _raises(claim.TransportError("no answer")))
    monkeypatch.setattr(
        claim.sys, "argv", ["claim.py", "claim", "--issue", "7", "--vendor", "claude"]
    )
    assert claim.main() == 2
    err = capsys.readouterr().err
    assert err.startswith("error:"), err
    assert "ineligible" not in err


def test_a_genuine_ineligibility_still_exits_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two must not collapse into one code again, in either direction.

    An unapproved issue is a *decided answer* and stays exit 3; the case above is the absence of an
    answer and is exit 2.
    """
    routes = _routes({("GET", "/repos/bioedca/tether/issues/7/comments"): (200, [])})
    fake = _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit) as exit_info:
        claim._cmd_claim(_args(issue=7))
    assert exit_info.value.code == claim.EXIT_INELIGIBLE
    assert "ineligible" in capsys.readouterr().err
    assert not [c for c in fake.calls if c[0] == "POST" and "git/refs" in c[1]]


def test_a_certificate_failure_names_the_certificate_and_the_one_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Unreachable" was the wrong cause, and it sent readers hunting a nonexistent outage."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error("Basic Constraints of CA cert not marked critical"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "certificate failed verification" in message
    assert "Basic Constraints of CA cert not marked critical" in message
    if not claim._strict_is_the_default():
        # Below 3.13 the flag is off, so strict conformance genuinely is not the cause and the
        # remedy must not be offered. Saying otherwise is the confidently-wrong message again.
        assert "not enabled on this interpreter" in message
        assert claim.STRICT_OPT_OUT not in message
        return
    assert "VERIFY_X509_STRICT" in message, "the message must name the cause it observed"
    assert claim.STRICT_OPT_OUT in message, "and the one supported remedy"
    assert "host is reachable" in message, "it must not read as a network outage"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (claim.ClaimError("no GitHub token: set GH_TOKEN or run gh auth login"), "no GitHub token"),
        (claim.ClaimError("#7 comments could not be read"), "could not be read"),
        (claim.TransportError("the GitHub API could not be reached (gaierror)"), "reached"),
    ],
    ids=["no-token", "http-error", "transport"],
)
def test_only_a_decided_answer_reaches_exit_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected: str,
) -> None:
    """Codex P1 on #388: subtyping the *failures* left the guarantee false.

    `_token` raises a plain `ClaimError` when there is no GitHub token, and `_issue`/`_paginate`
    raise one on any 401, 403 or 5xx. None is a verdict about the issue, and all of them slipped
    past an `except TransportError` arm into the blanket `except ClaimError` below it. Enumerating
    what *is* a verdict is the fix, and this is the test that would have caught the first attempt.
    """
    monkeypatch.setattr(claim, "_check_eligible", _raises(failure))
    with pytest.raises(claim.ClaimError) as info:
        claim._cmd_claim(_args(issue=7))
    assert not isinstance(info.value, claim.IneligibleError)
    assert expected in str(info.value)
    assert "ineligible" not in capsys.readouterr().err


def test_the_decided_answers_are_enumerated_and_stay_enumerated() -> None:
    """Bind the enumeration itself, since its whole value is that it is closed.

    Writing this test found the fifth: `_issue` refuses a pull-request number, which *is* a verdict
    — the server told us what the number is — while the `status != 200` beside it is not. A later
    edit that reaches for `IneligibleError` somewhere new shows up here rather than as a worker
    silently skipping approved work.

    The sixth arrived with #336: what the issue *body* declares about the autonomy the work needs.
    It belongs here on the same reasoning as the other five — it is a decided answer about the
    issue, read from the issue, and not a failure to ask.
    """
    source = (ROOT / ".agents" / "bin" / "claim.py").read_text(encoding="utf-8")
    eligible = source.partition("def _check_eligible")[2].partition("\ndef ")[0]
    fetch = source.partition("def _issue")[2].partition("\ndef ")[0]
    assert source.count("raise IneligibleError") == 6, "the verdicts are exactly six"
    assert eligible.count("raise IneligibleError") == 5
    assert fetch.count("raise IneligibleError") == 1
    assert "could not be read" in fetch, "a failed read sits beside it and must NOT be a verdict"
    assert fetch.count("raise ClaimError") == 1


def test_a_certificate_failure_with_the_opt_out_already_set_does_not_suggest_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remedy branch must not fire when the remedy is already applied.

    If the opt-out is on and the certificate *still* fails, the strict-conformance story is no
    longer the explanation - the chain itself is untrusted. Telling the reader to set a variable
    they have already set would send them in a circle, and worse, imply another notch of loosening
    exists. There is not one.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", True)
    _transport(monkeypatch, _cert_error("some OpenSSL wording nobody here has seen"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "already set" in message
    assert f"{claim.STRICT_OPT_OUT}=1" not in message, "do not re-suggest what is already applied"
    assert "Do not relax verification further" in message


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        ("certificate has expired", "invalid however X.509 conformance is configured"),
        ("Hostname mismatch, certificate is not valid for 'x'", "however X.509 conformance"),
        ("unable to get local issuer certificate", "SSL_CERT_FILE"),
    ],
)
def test_the_opt_out_being_set_does_not_relabel_an_unrelated_failure(
    monkeypatch: pytest.MonkeyPatch, cause: str, expected: str
) -> None:
    """Codex P1 on #388: the opt-out branch used to short-circuit ahead of classification.

    It announced "the chain itself is not trusted" for whatever came through, which is wrong for an
    expired certificate or a hostname mismatch — neither is a chain-trust failure, and both survive
    the relaxation precisely because it leaves chain and hostname verification on. What was observed
    is classified first now; the opt-out's state qualifies only the branches about conformance.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", True)
    _transport(monkeypatch, _cert_error(cause))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert cause in message
    assert expected in message
    assert "chain itself is not trusted" not in message, "that is not what happened"


def test_a_missing_issuer_gets_the_remedy_that_actually_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 on #388: the strict story was asserted for every certificate failure.

    For a genuinely missing issuer, `SSL_CERT_FILE` *is* the remedy — the first version told the
    reader the opposite, confidently. That is the failure mode #315 exists to remove, reintroduced
    one branch over.
    """
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error("unable to get local issuer certificate"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "SSL_CERT_FILE" in message
    assert "point SSL_CERT_FILE at that CA bundle" in message
    assert f"{claim.STRICT_OPT_OUT} cannot address it" in message
    assert "was found" not in message, "it was not found; that is the whole point"


def test_an_unrelated_certificate_defect_is_not_blamed_on_conformance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired certificate is not a Basic Constraints quibble, and must not read as one."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error("certificate has expired"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "certificate has expired" in message
    assert "Basic Constraints" not in message
    assert "the certificate was found" not in message
    # On *either* interpreter, and keyed on the arming form: naming the variable in order to say it
    # cannot help is a refusal, not advice. Codex's P1 at `0f14fa1` was that the 3.13+ fallback
    # offered the opt-out to anything without a conformance signature, so the gate the ADR promised
    # was not the gate the code applied.
    assert f"{claim.STRICT_OPT_OUT}=1" not in message, "no remedy for a certificate that is expired"
    # Interpreter-independent, unlike the unknown-signature case: expiry is checked the same way
    # under strict and non-strict verification, so the answer does not depend on which is running.
    assert "should not be accepted" in message
    assert "not a conformance defect" in message


@pytest.mark.parametrize(
    ("cause", "certainty"),
    [
        ("Basic Constraints of CA cert not marked critical", "conformance"),
        ("invalid CA certificate", "conformance"),
        ("Missing Authority Key Identifier", "conformance"),
        ("CA cert does not include key usage extension", "conformance"),
        ("certificate has expired", "not-conformance"),
        ("certificate is not yet valid", "not-conformance"),
        ("Hostname mismatch, certificate is not valid for 'api.github.com'", "not-conformance"),
        ("unable to get local issuer certificate", "missing-issuer"),
        ("self-signed certificate in certificate chain", "missing-issuer"),
        ("some OpenSSL wording nobody here has seen", "unknown"),
    ],
)
def test_the_certificate_message_claims_only_what_it_can_know(
    monkeypatch: pytest.MonkeyPatch, cause: str, certainty: str
) -> None:
    """Three certainty classes, because two of them were review findings pointing opposite ways.

    Codex first showed that *offering* `TETHER_ALLOW_NONSTRICT_X509=1` for anything unrecognized
    pointed expired certificates at a TLS switch that cannot help them. Then it showed that
    *denying* the remedy for anything unrecognized is equally unfounded — `_STRICT_MARKERS` cannot
    be exhaustive, since OpenSSL gates a family of checks behind `X509_V_FLAG_X509_STRICT` and words
    them per build, so a message that misses the list is genuinely **unknown**.

    Both are the same defect: asserting something the tool does not know. So known-conformance gets
    the remedy, known-not-conformance gets a definite refusal, and unknown gets neither — it names
    both possibilities and the experiment that separates them.
    """
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    _transport(monkeypatch, _cert_error(cause))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert cause in message, "the observed reason travels with every verdict"

    if not claim._strict_is_the_default() and certainty in ("conformance", "unknown"):
        # Below 3.13 the flag is off, so conformance cannot be the cause whatever the wording.
        assert "not enabled on this interpreter" in message
        assert f"{claim.STRICT_OPT_OUT}=1" not in message
        return

    if certainty == "conformance":
        assert f"{claim.STRICT_OPT_OUT}=1" in message, "the remedy applies and must be offered"
        assert "cannot tell" not in message
    elif certainty == "not-conformance":
        assert f"{claim.STRICT_OPT_OUT}=1" not in message, "the remedy cannot help; do not offer it"
        assert "should not be accepted" in message
    elif certainty == "missing-issuer":
        assert "SSL_CERT_FILE" in message
        assert f"{claim.STRICT_OPT_OUT}=1" not in message
    else:
        assert "cannot tell" in message, "an unknown signature must not be asserted either way"
        assert "must not be forced" in message
        # The experiment has to isolate the flag. "Re-run under an older interpreter" was the first
        # suggestion and does not: a different interpreter brings a different OpenSSL build, CA path
        # and - on this machine - a different environment, so success there proves nothing about
        # encoding. Toggling one variable in one process does.
        assert "same interpreter" in message
        assert "older than 3.13" not in message


def test_an_unreachable_host_is_still_reported_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the distinction: a genuine outage must not mention certificates."""
    _transport(monkeypatch, OSError(11001, "getaddrinfo failed"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    message = str(info.value)
    assert "could not be reached" in message
    assert "getaddrinfo failed" in message
    assert "certificate" not in message


def test_a_transport_message_never_carries_a_filesystem_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ClaimError`` promises its message carries no path, and ``OSError`` renders its filename.

    A misdirected ``SSL_CERT_FILE`` is exactly how a private path would otherwise reach a log.
    """
    _transport(monkeypatch, FileNotFoundError(2, "No such file or directory", "/home/me/ca.pem"))
    with pytest.raises(claim.TransportError) as info:
        claim._request("GET", "/repos/bioedca/tether/issues/7")
    assert "/home/me/ca.pem" not in str(info.value)


def test_the_opt_out_relaxes_conformance_only_and_never_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole security argument of ADR-0061, asserted rather than described.

    Clearing ``VERIFY_X509_STRICT`` restores pre-3.13 *conformance* checking. It must not touch
    ``verify_mode`` or ``check_hostname``: this tool sends a GitHub token, and a context that
    skipped either would hand it to whoever answered.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    relaxed = claim._ssl_context()
    stock = claim.ssl.create_default_context()
    # Exact, and independent of the interpreter: whatever the default context sets, the opt-out
    # differs from it by that single flag and nothing else. Asserting `not flags & STRICT` alone
    # would pass vacuously below 3.13, where the flag is off to begin with - which is the whole
    # reason this defect is version-dependent.
    assert relaxed.verify_flags == stock.verify_flags & ~claim.ssl.VERIFY_X509_STRICT
    assert relaxed.verify_mode == claim.ssl.CERT_REQUIRED
    assert relaxed.check_hostname is True


def test_only_the_literal_one_arms_the_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "An interlock that fires on anything truthy is not an interlock" (#315, maintainer).

    `true`, `yes` and `TRUE` are the spellings a shell profile picks up by habit, and each would
    silently relax a TLS check on a path that carries a GitHub token. Only `1` counts.

    Whitespace is covered by `test_only_the_exact_string_one_arms_it`, which rejects it. An earlier
    version of this file said whitespace was tolerated; that was true of an earlier implementation
    and stopped being true when the comparison became exact.
    """
    stock = claim.ssl.create_default_context()
    for value in ("", "0", "false", "no", "true", "yes", "TRUE", "on", "2", "-1", None):
        monkeypatch.setattr(claim, "_ANNOUNCED", False)
        if value is None:
            monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
        else:
            monkeypatch.setenv(claim.STRICT_OPT_OUT, value)
        assert not claim._nonstrict_x509_allowed(), f"{value!r} was read as an opt-in"
        context = claim._ssl_context()
        assert context.verify_flags == stock.verify_flags
        assert context.verify_mode == claim.ssl.CERT_REQUIRED
        assert context.check_hostname is True


def test_only_the_exact_string_one_arms_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Literal" means literal — no `strip`, no truthiness.

    An earlier version stripped whitespace, arguing that `"1 "` from a `.env` line is unambiguous
    intent and that refusing it would make a set variable a silent no-op. Both reviewers flagged it,
    and the argument does not survive: the contract says *literal*, and the no-op is not silent —
    a value that does not arm produces the ordinary strict failure, which prints the cause and this
    variable as the remedy. A malformed setting failing loudly is the safer direction to err.
    """
    stock = claim.ssl.create_default_context()
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    # Asserted on the predicate too, not only on the flags: below 3.13 the flag is already clear, so
    # a flags-only check would pass for every value and prove nothing.
    assert claim._nonstrict_x509_allowed()
    assert claim._ssl_context().verify_flags == stock.verify_flags & ~claim.ssl.VERIFY_X509_STRICT

    for value in (" 1", "1 ", " 1 ", "1\n", "01", "1.0"):
        monkeypatch.setenv(claim.STRICT_OPT_OUT, value)
        assert not claim._nonstrict_x509_allowed(), f"{value!r} armed the opt-out"
        assert claim._ssl_context().verify_flags == stock.verify_flags


def test_the_relaxation_announces_itself_once_per_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A process that quietly stopped enforcing a check reads like one that never needed to.

    Once per process rather than per request: `_paginate` can make twenty calls, and a warning
    repeated twenty times is one nobody reads.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    for _ in range(3):
        claim._ssl_context()
    err = capsys.readouterr().err
    assert err.count("notice:") == 1, err
    assert claim.STRICT_OPT_OUT in err
    assert "hostname verification remain enabled" in err


def test_a_failed_announcement_does_not_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex P2 on #388: latching before the write can relax a context in total silence.

    If stderr is closed or its reader has exited, the print raises and no notice arrived. Latching
    first would let a caller that catches that error and retries get a relaxed context with nothing
    on the record — which is precisely what the interlock exists to prevent.
    """
    monkeypatch.setenv(claim.STRICT_OPT_OUT, "1")
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    monkeypatch.setattr(claim, "print", _raises(BrokenPipeError("stderr is gone")), raising=False)
    with pytest.raises(BrokenPipeError):
        claim._ssl_context()
    assert claim._ANNOUNCED is False, "a notice that never arrived must not count as delivered"


def test_the_default_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The announcement marks the exception, so it must not fire on the ordinary path."""
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    monkeypatch.setattr(claim, "_ANNOUNCED", False)
    claim._ssl_context()
    assert capsys.readouterr().err == ""


@pytest.mark.skipif(sys.version_info < (3, 13), reason="VERIFY_X509_STRICT is off before 3.13")
def test_the_default_really_is_strict_on_the_interpreters_that_have_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: `untouched` only means `strict` where CPython makes it so.

    3.13 is where `create_default_context()` turned the flag on, and 3.13/3.14 are supported
    interpreters here - so on those, the shipped default must be the strict one.

    The `delenv` is load-bearing rather than tidy: the contract tells operators on the affected
    machines to set `TETHER_ALLOW_NONSTRICT_X509=1`, so in the very shell this fix exists to serve,
    reading the ambient environment would fail this test for an environment reason.
    """
    monkeypatch.delenv(claim.STRICT_OPT_OUT, raising=False)
    assert claim._ssl_context().verify_flags & claim.ssl.VERIFY_X509_STRICT


def test_the_claim_tool_never_reaches_for_a_blunter_instrument() -> None:
    """#315's non-goal, bound to the source rather than left as a promise in a docstring.

    Docstrings are blanked before the check, because this file's own prose names these mechanisms
    in order to rule them out - a plain substring search over the source flags that as a violation.
    `ast` drops comments outright, so unparsing covers those too.
    """
    tree = ast.parse((ROOT / ".agents" / "bin" / "claim.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            first.value.value = ""
    code = ast.unparse(tree)
    for forbidden in ("_create_unverified_context", "PYTHONHTTPSVERIFY", "CERT_NONE"):
        assert forbidden not in code, f"claim.py must never use {forbidden}"
    assert "check_hostname" not in code, "the default (on) must never be assigned away"


def test_no_other_subcommand_turns_a_transport_failure_into_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit #315 asks for: ``check``, ``release`` and ``reserve-adr`` must not share it.

    They never caught ``ClaimError`` at all, so each already reached ``main``'s exit 2 - but "shown
    not to have it" is worth binding, since the tempting fix for any of them is the same blanket
    ``except`` that caused this.
    """
    boom = _raises(claim.TransportError("no answer"))
    monkeypatch.setattr(claim, "_generation", boom)
    monkeypatch.setattr(claim, "_ref_exists", boom)
    monkeypatch.setattr(claim, "_default_sha", boom)
    for call in (
        lambda: claim._cmd_check(_args(issue=7, generation=42)),
        lambda: claim._cmd_release(_args(issue=7, generation=42, vendor="claude")),
        lambda: claim._cmd_reserve_adr(_args(attempts=8)),
    ):
        with pytest.raises(claim.TransportError):
            call()


# ------------------------------------------------------------------ doctor (#326)


DOCTOR_ROUTES: Routes = {
    ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:ready"): (
        200,
        [
            _issue(number=1, title="binds"),
            _issue(number=2, title="no marker"),
            _issue(number=3, title="edited since approval"),
            _issue(number=4, title="malformed marker"),
        ],
    ),
    ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:blocked"): (
        200,
        [{"number": 9, "body": "Depends on #1 and #2.", "title": "blocked"}],
    ),
    # Mode B queries both holding labels; #326's table is three-sevenths `status:backlog`.
    ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:backlog"): (200, []),
    ("GET", "/repos/bioedca/tether/pulls?state=open"): (200, []),
    # `_paginate` raises on a non-list body, so every comment page a walk reaches needs a route.
    # `Fake` returns the FIRST matching prefix in insertion order, not the longest, so a test that
    # overrides one of these must place its entry ahead of the general one rather than rely on
    # specificity.
    ("GET", "/repos/bioedca/tether/issues/1/comments"): (200, []),
    ("GET", "/repos/bioedca/tether/issues/2/comments"): (200, []),
    ("GET", "/repos/bioedca/tether/issues/3/comments"): (200, []),
    ("GET", "/repos/bioedca/tether/issues/4/comments"): (200, []),
    ("GET", "/repos/bioedca/tether/issues/9/comments"): (200, []),
    ("GET", "/repos/bioedca/tether/issues/1"): (200, {"number": 1, "state": "closed"}),
    ("GET", "/repos/bioedca/tether/issues/2"): (200, {"number": 2, "state": "open"}),
}


def test_doctor_calls_a_pull_request_merged_only_when_it_merged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pull_request` on the issues API means "is a PR", never "landed" (Greptile P1 on #432).

    It is present on every pull request the endpoint returns — open, closed-unmerged and merged
    alike — so keying on its presence reported *every* referenced pull request as `merged`. That is
    the one direction Mode B must never err in: it hands a maintainer raw dependency state, and a
    blocker reported as merged when it is still open is worse than not reporting it at all. Only
    `merged_at` distinguishes them.
    """
    routes = dict(DOCTOR_ROUTES)
    routes[("GET", "/repos/bioedca/tether/issues?state=open&labels=status:blocked")] = (
        200,
        [{"number": 9, "body": "Blocked by #30, #31 and #32.", "title": "blocked"}],
    )
    for ref, payload in (
        (30, {"number": 30, "state": "open", "pull_request": {"merged_at": None}}),
        (31, {"number": 31, "state": "closed", "pull_request": {"merged_at": None}}),
        (
            32,
            {
                "number": 32,
                "state": "closed",
                "pull_request": {"merged_at": "2026-08-01T00:00:00Z"},
            },
        ),
    ):
        routes[("GET", f"/repos/bioedca/tether/issues/{ref}")] = (200, payload)
        routes[("GET", f"/repos/bioedca/tether/issues/{ref}/comments")] = (200, [])
    _install(monkeypatch, Fake(routes))

    claim._cmd_doctor(_args(owner="bioedca"))
    mentions = json.loads(capsys.readouterr().out)["blocked"][0]["mentions"]
    assert mentions == {"30": "open", "31": "closed", "32": "merged"}


def _doctor(monkeypatch: pytest.MonkeyPatch, over: Routes | None = None) -> dict[str, Any]:
    routes = dict(DOCTOR_ROUTES)
    routes.update(over or {})
    _install(monkeypatch, Fake(routes))
    return routes


def test_doctor_tells_an_absent_marker_from_one_that_no_longer_binds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The distinction #326 calls "the valuable part", because the remedies differ.

    Absent means post a marker; stale means re-approve after an edit; malformed means the marker is
    there and unreadable. One exit-3 message covers all three, and a worker burns a session finding
    out which.
    """
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/issues/1/comments"): (
                200,
                [{"user": {"login": "bioedca"}, "body": MARKER}],
            ),
            ("GET", "/repos/bioedca/tether/issues/2/comments"): (200, []),
            ("GET", "/repos/bioedca/tether/issues/3/comments"): (
                200,
                [
                    {
                        "user": {"login": "bioedca"},
                        "body": '<!-- tether-agent-ready {"version":1,"criteria_sha256":"'
                        + OTHER
                        + '"} -->',
                    }
                ],
            ),
            ("GET", "/repos/bioedca/tether/issues/4/comments"): (
                200,
                [
                    {
                        "user": {"login": "bioedca"},
                        "body": "<!-- tether-agent-ready "
                        '{"version":1,"criteria_sha256":"nope"} -->',
                    }
                ],
            ),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    report = json.loads(capsys.readouterr().out)
    assert {r["issue"]: r["marker"] for r in report["ready"]} == {
        1: "binds",
        2: "absent",
        3: "stale",
        4: "malformed",
    }


def test_doctor_never_says_unblocked_and_only_reports_what_it_can_see(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mode B is raw data on purpose.

    #326 concedes the Dependencies parse is heuristic — the section is prose, its wording varies,
    and #261 names no issue number at all — and says "a false 'unblocked' here is worse than a
    miss". So the report prints what a body mentions and what those items are, and adjudicates
    nothing. A report that must not be trusted is one nobody reads.
    """
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/issues/1"): (200, {"number": 1, "state": "closed"}),
            ("GET", "/repos/bioedca/tether/issues/2"): (200, {"number": 2, "state": "open"}),
            ("GET", "/repos/bioedca/tether/issues/9/comments"): (200, []),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    out = capsys.readouterr().out
    assert "unblocked" not in out.lower(), "Mode B must never adjudicate"
    blocked = json.loads(out)["blocked"]
    assert blocked == [{"issue": 9, "mentions": {"1": "closed", "2": "open"}}]


def test_doctor_reports_an_unreadable_collection_instead_of_printing_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three collection reads fail the same way one issue does, and must report the same way.

    Catching the per-issue comments walk closed this hole one level down and left it open one level
    up: a failed `status:ready` listing, label query or pull-request listing still propagated out of
    `_cmd_doctor` and printed nothing. Each of the three is now caught where it is made, so a
    failure costs its own section and nothing else.

    Asserted one collection at a time, because a single test that broke all three would pass
    against a version that only caught the first.
    """
    collections = {
        "ready": "/repos/bioedca/tether/issues?state=open&labels=status:ready",
        "blocked": "/repos/bioedca/tether/issues?state=open&labels=status:blocked",
        "unarmed": "/repos/bioedca/tether/pulls?state=open",
    }
    for section, route in collections.items():
        _doctor(monkeypatch, {("GET", route): (500, {"message": "server error"})})
        claim._cmd_doctor(_args(owner="bioedca"))
        report = json.loads(capsys.readouterr().out)
        assert set(report) >= {"ready", "blocked", "unarmed"}, (
            f"{section}: one unreadable collection suppressed the whole report"
        )
        assert any("unreadable" in r for r in report[section]), (
            f"{section}: the failure was not reported"
        )
        others = [k for k in ("ready", "blocked", "unarmed") if k != section]
        for other in others:
            assert not any(
                r.get("collection", "").startswith(("status:", "open pull")) and "unreadable" in r
                for r in report[other]
            ), f"{section} failing marked {other} unreadable too"


def test_doctor_says_a_dependency_section_was_unparseable_rather_than_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`"mentions": {}` is ambiguous, and the ambiguity points the wrong way.

    A body naming no `#N` produced an empty set, which an operator reads as *every dependency is
    resolved* — the false-clean direction #326 names as worse than a miss. It cites #261 as an
    issue whose Dependencies section is prose with no issue number in it at all, so this is the
    corpus's own case rather than a hypothetical.

    The report now says `unparseable` in as many words. It still adjudicates nothing: it reports
    that it could not find a reference, not that there is no dependency.
    """
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:blocked"): (
                200,
                [{"number": 12, "body": "Blocked by the review-gate rewrite.", "title": "prose"}],
            ),
            ("GET", "/repos/bioedca/tether/issues/12/comments"): (200, []),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    blocked = {b["issue"]: b for b in json.loads(capsys.readouterr().out)["blocked"]}
    assert 12 in blocked, "the issue was dropped"
    assert blocked[12]["mentions"] == {}, "nothing was parseable, so nothing should be reported"
    assert "unparseable" in blocked[12], (
        "an unparseable dependency section rendered as an empty set, which reads as all-clear"
    )


def test_doctor_survives_a_ready_issue_whose_comments_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One unreadable page must cost one issue, not the whole report.

    `_paginate` raises on any 401, 403 or 5xx, and Mode A walked every `status:ready` issue's
    comments without catching it — so a single transient failure propagated out of `_cmd_doctor`
    and printed nothing at all. That silences Mode B and Mode C too: three diagnostics lost to one
    issue nobody was asking about, and the operator sees a traceback rather than the report.

    Same rule as the unreadable blocker and the unreadable pull request, which this PR already
    applies twice: name what could not be read, keep going, and never let an absence read as an
    answer.
    """
    _doctor(
        monkeypatch,
        {("GET", "/repos/bioedca/tether/issues/2/comments"): (500, {"message": "server error"})},
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    report = json.loads(capsys.readouterr().out)
    ready = {r["issue"]: r for r in report["ready"]}
    assert set(ready) == {1, 2, 3, 4}, "an unreadable issue was dropped instead of reported"
    assert "unreadable" in ready[2], "#2's failure was not reported as unreadable"
    assert "marker" not in ready[2], "an unreadable issue must not be given a marker verdict"
    assert all("marker" in ready[n] for n in (1, 3, 4)), "the other issues stopped being assessed"
    assert "blocked" in report and "unarmed" in report, (
        "one unreadable ready issue took the other two modes down with it"
    )


def test_doctor_reports_a_body_the_parser_cannot_model_as_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same failure through the reporter: one issue, not the report, and never a verdict.

    `doctor` mirrors the gate, and the gate reports a body the structure reader cannot model as an
    error rather than an exit-3 verdict (ADR-0066). So the `autonomy` field may not be written
    either way for that issue - `true` admits work nobody read, `false` is the verdict in another
    coat - and the unreadable shape that already covers a comment page is the honest one. Before
    this, the raise escaped `_doctor_ready` and took every section of the report with it.
    """
    real = claim._markdown.parse

    def parse(body: str) -> Any:
        if "unmodelled" in body:
            raise claim._markdown.MarkdownStructureError("unmodelled block token 'tfoot_open'")
        return real(body)

    monkeypatch.setattr(claim._markdown, "parse", parse)
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:ready"): (
                200,
                [
                    _issue(number=1, title="binds"),
                    _issue(number=2, title="unmodelled", body="unmodelled"),
                    _issue(number=3, title="edited since approval"),
                    _issue(number=4, title="malformed marker"),
                ],
            )
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    report = json.loads(capsys.readouterr().out)
    ready = {r["issue"]: r for r in report["ready"]}
    assert set(ready) == {1, 2, 3, 4}, "the unreadable issue was dropped instead of reported"
    assert "tfoot_open" in ready[2].get("unreadable", ""), ready[2]
    assert "autonomy" not in ready[2] and "marker" not in ready[2], (
        "an unreadable body must not be given a verdict in either field"
    )
    assert all(ready[n]["autonomy"] is True for n in (1, 3, 4)), "the other issues changed"
    assert "blocked" in report and "unarmed" in report, (
        "one unreadable body took the other two modes down with it"
    )


def test_doctor_reports_backlog_issues_too_and_lists_a_dual_labelled_one_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mode B holds on two labels, because #326's own evidence does.

    The first implementation queried `status:blocked` alone. #326's table is the specification and
    three of its seven rows are `status:backlog` — #298, #257 and #258, each naming a dependency
    that has since merged — so a single-label query answered a narrower question than the issue
    asked and dropped nearly half the evidence it was raised on. That is a silent miss: the report
    looks complete because nothing says which labels it covered.

    The dedupe is asserted as well. Nothing stops an issue carrying both labels, and a union that
    listed it twice would read as two stranded issues where there is one.
    """
    dual = {"number": 11, "body": "Blocked by #1.", "title": "both labels"}
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:blocked"): (
                200,
                [{"number": 9, "body": "Depends on #1.", "title": "blocked"}, dual],
            ),
            ("GET", "/repos/bioedca/tether/issues?state=open&labels=status:backlog"): (
                200,
                [{"number": 10, "body": "Start after #2 merges.", "title": "backlog"}, dual],
            ),
            ("GET", "/repos/bioedca/tether/issues/9/comments"): (200, []),
            ("GET", "/repos/bioedca/tether/issues/10/comments"): (200, []),
            ("GET", "/repos/bioedca/tether/issues/11/comments"): (200, []),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    blocked = json.loads(capsys.readouterr().out)["blocked"]
    numbers = [b["issue"] for b in blocked]
    assert sorted(numbers) == [9, 10, 11], "a status:backlog issue was dropped from Mode B"
    assert numbers.count(11) == 1, "an issue carrying both labels was reported twice"
    by_issue = {b["issue"]: b["mentions"] for b in blocked}
    assert by_issue[10] == {"2": "open"}, "the backlog issue's mentions were not looked up"


def test_doctor_rereads_draft_and_state_from_the_detail_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The list page is a snapshot; the detail response is the current answer.

    Mode C filtered `draft` from the list entry and never re-read it, and never read `state` at
    all. On a busy repository the list can be minutes stale, so a pull request closed or returned
    to draft in between would be reported as finished-and-stranded — advice to arm something that
    must not be armed.
    """
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    pr = {
        "number": 70,
        "draft": False,
        "state": "open",
        "auto_merge": None,
        "mergeable_state": "clean",
        "updated_at": stale,
        "head": {"sha": HEAD},
    }
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/pulls?state=open"): (
                200,
                [pr, {**pr, "number": 71}, {**pr, "number": 72}],
            ),
            ("GET", "/repos/bioedca/tether/pulls/70"): (200, pr),
            # closed since the list page was built
            ("GET", "/repos/bioedca/tether/pulls/71"): (200, {**pr, "state": "closed"}),
            # returned to draft since the list page was built
            ("GET", "/repos/bioedca/tether/pulls/72"): (200, {**pr, "draft": True}),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    unarmed = json.loads(capsys.readouterr().out)["unarmed"]
    assert [u["pr"] for u in unarmed] == [70], (
        "a pull request closed or re-drafted since the list page was built was reported as stranded"
    )


def test_doctor_diagnoses_a_pull_request_marked_ready_since_the_list_was_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Staleness cuts both ways, and the first fix only closed one direction.

    The sibling above stops a pull request that became draft or closed from being reported. This is
    the mirror: the loop still skipped on the **list's** `draft` before fetching anything, so one
    marked ready since the list was built was dropped before its current state was ever read.

    That is the worse direction of the two. A pull request that just went ready, is green and is
    unarmed is exactly the finished-and-stranded case Mode C exists to find — so the filter was
    most likely to discard the very thing it was looking for. Nothing now filters on the summary;
    the detail response decides.
    """
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    ready = {
        "number": 80,
        "draft": False,
        "state": "open",
        "auto_merge": None,
        "mergeable_state": "clean",
        "updated_at": stale,
        "head": {"sha": HEAD},
    }
    _doctor(
        monkeypatch,
        {
            # the LIST still says draft; the detail response says it is ready
            ("GET", "/repos/bioedca/tether/pulls?state=open"): (200, [{**ready, "draft": True}]),
            ("GET", "/repos/bioedca/tether/pulls/80"): (200, ready),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    unarmed = json.loads(capsys.readouterr().out)["unarmed"]
    assert [u["pr"] for u in unarmed] == [80], (
        "a pull request marked ready since the list page was built was never diagnosed"
    )


def test_doctor_says_it_could_not_read_a_reference_rather_than_omitting_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lookup that failed is not a reference that does not exist.

    The loop recorded a reference only on `200`, with no other branch, so a `404`, a `403` or a
    `500` dropped it. That is the silent truncation the comment above the loop says this report
    must never produce, arriving through a different door than the cap it does report: with every
    lookup failing, an issue naming two blockers rendered as `{"mentions": {}}` — byte-identical
    to an issue that names none, and read by a maintainer as *nothing is blocking this*.

    Fails against the pre-fix loop, which emits no `unreadable` key at all.
    """
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/issues/1"): (404, None),
            ("GET", "/repos/bioedca/tether/issues/2"): (500, None),
            ("GET", "/repos/bioedca/tether/issues/9/comments"): (200, []),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    blocked = json.loads(capsys.readouterr().out)["blocked"]
    assert blocked == [{"issue": 9, "mentions": {}, "unreadable": [1, 2]}], (
        "an unreadable reference must be reported, not dropped into an empty `mentions`"
    )


def test_doctor_reports_a_finished_pr_that_nothing_will_ever_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mode C: every gate the merge asks for is satisfied and the last step did not happen."""
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    pr = {
        "number": 50,
        "draft": False,
        "state": "open",
        "auto_merge": None,
        "mergeable_state": "clean",
        "updated_at": stale,
        "head": {"sha": HEAD},
    }
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/pulls?state=open"): (
                200,
                [pr, {**pr, "number": 51}, {**pr, "number": 52}],
            ),
            ("GET", "/repos/bioedca/tether/pulls/50"): (200, pr),
            # armed already, so not stranded
            ("GET", "/repos/bioedca/tether/pulls/51"): (200, {**pr, "auto_merge": {"x": 1}}),
            # pushed a minute ago: mid-flight, not stranded
            ("GET", "/repos/bioedca/tether/pulls/52"): (200, {**pr, "updated_at": fresh}),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    unarmed = json.loads(capsys.readouterr().out)["unarmed"]
    assert [u["pr"] for u in unarmed] == [50]


def test_doctor_reports_a_pull_request_it_could_not_read_rather_than_dropping_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable pull request must not print the same as an armed one.

    Mode C skipped any pull request whose detail call failed, so a transport failure and a
    correctly armed pull request produced identical output: absence. That is the defect
    `test_doctor_says_it_could_not_read_a_reference_rather_than_omitting_it` fixes for Mode B, in
    the mode that reports what nothing will ever merge — and it is the direction #326 calls out as
    the costly one, because *nothing is wrong here* is what a maintainer acts on by looking away.

    The remedies differ as well: a failed read is retried, a genuinely unarmed pull request is
    armed. Reporting them alike sends the reader to the wrong one.
    """
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    pr = {
        "number": 60,
        "draft": False,
        "state": "open",
        "auto_merge": None,
        "mergeable_state": "clean",
        "updated_at": stale,
        "head": {"sha": HEAD},
    }
    _doctor(
        monkeypatch,
        {
            ("GET", "/repos/bioedca/tether/pulls?state=open"): (
                200,
                [pr, {**pr, "number": 61}, {**pr, "number": 62}],
            ),
            ("GET", "/repos/bioedca/tether/pulls/60"): (200, pr),
            ("GET", "/repos/bioedca/tether/pulls/61"): (404, {}),
            ("GET", "/repos/bioedca/tether/pulls/62"): (500, {}),
        },
    )
    claim._cmd_doctor(_args(owner="bioedca"))
    unarmed = json.loads(capsys.readouterr().out)["unarmed"]
    assert sorted(u["pr"] for u in unarmed) == [60, 61, 62]
    assert {u["pr"]: u.get("unreadable") for u in unarmed} == {60: None, 61: 404, 62: 500}


def test_doctor_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "Report, never act" is the whole safety property, and #326 reserves every remedy.

    Posting an approval marker, promoting a label and arming someone else's merge are maintainer
    authority. Asserted over every write verb rather than trusted to the implementation.
    """
    fake = _install(monkeypatch, Fake(dict(DOCTOR_ROUTES)))
    claim._cmd_doctor(_args(owner="bioedca"))
    capsys.readouterr()
    assert not [c for c in fake.calls if c[0] in {"POST", "PATCH", "PUT", "DELETE"}]


def test_the_exit_three_message_points_at_doctor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One message covers three situations; it should say where to find out which.

    Named in the message rather than in `AGENTS.md` deliberately — resident contract is a running
    cost on every model call, and this is read exactly when it is needed.
    """
    routes = _routes(
        {("GET", "/repos/bioedca/tether/issues/7/comments"): (200, [])},
    )
    _install(monkeypatch, Fake(routes))
    with pytest.raises(SystemExit):
        claim._cmd_claim(_args(issue=7))
    assert "doctor" in capsys.readouterr().err
