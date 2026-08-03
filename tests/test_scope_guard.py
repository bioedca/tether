# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contract tests for the advisory scope guard.

Two things carry most of the weight here, because they are choices rather than derivations and a
future reader will otherwise assume they were arbitrary:

* the budget counts **added** lines, so a large deletion is not charged as scope, and
* the proportional-test rule's operands are **diff additions**, not resulting file sizes.

Both readings of the second were already in use before this landed, so the tests pin the decision
and not just the arithmetic.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "bin" / "scope_guard.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scope-guard.yml"

_spec = importlib.util.spec_from_file_location("tether_scope_guard", SCRIPT)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


class Fake:
    """Answers by (method, path-prefix), longest prefix first."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        best: tuple[int, tuple[int, Any]] | None = None
        for (m, prefix), response in self.routes.items():
            if m == method and path.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), response)
        return best[1] if best is not None else (200, None)


def _file(
    name: str, added: int, deleted: int = 0, status: str = "modified", patch: str = ""
) -> dict:
    return {
        "filename": name,
        "additions": added,
        "deletions": deleted,
        "status": status,
        "patch": patch,
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    files: list[dict[str, Any]],
    labels: list[str],
    body: str = "Closes: #7\n",
    reviews: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    draft: bool = False,
) -> Fake:
    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/repos/bioedca/tether/pulls/99/files"): (200, files),
        ("GET", "/repos/bioedca/tether/pulls/99/reviews"): (200, reviews or []),
        ("GET", "/repos/bioedca/tether/pulls/99/comments"): (200, []),
        ("GET", "/repos/bioedca/tether/issues/99/timeline"): (200, timeline or []),
        ("GET", "/repos/bioedca/tether/pulls/99"): (
            200,
            {
                "number": 99,
                "body": body,
                "labels": [],
                "draft": draft,
                "base": {"sha": "a" * 40},
                "head": {"sha": "b" * 40},
            },
        ),
        ("GET", "/repos/bioedca/tether/issues/7"): (
            200,
            {"number": 7, "labels": [{"name": n} for n in labels]},
        ),
    }
    fake = Fake(routes)
    monkeypatch.setattr(guard.claim, "_request", fake)

    def paginate(path: str, what: str) -> list[Any]:
        status, payload = fake("GET", path)
        if status != 200 or not isinstance(payload, list):
            raise guard.claim.ClaimError(f"{what} could not be read")
        return payload

    monkeypatch.setattr(guard.claim, "_paginate", paginate)
    return fake


# ------------------------------------------------------------------------- the budget


def test_the_budget_counts_added_lines_not_the_whole_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#280's shape, and the reason the definition is what it is.

    385 added against 834 deleted was the largest net simplification in the rebuild. On added lines
    it passes `size:M` comfortably; counting deletions scores it 3.0x over the very same budget. A
    budget that penalises removal work would have argued against every deletion this rebuild
    depended on.

    """
    files = [_file("AGENTS.md", 385, 834)]
    _install(monkeypatch, files=files, labels=["size:M"])
    report = guard.measure(99)
    assert report["added"] == 385
    assert report["added_with_deletions"] == 1219
    assert report["ratio"] == 0.96
    assert report["findings"] == []


def test_being_over_the_budget_is_reported_with_its_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, files=[_file("src/tether/x.py", 900)], labels=["size:M"])
    report = guard.measure(99)
    assert report["ratio"] == 2.25
    assert any("2.25x over" in f for f in report["findings"])


def test_exceeding_the_largest_rung_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """`size:L` is the top of the ladder; there is no `size:XL`.

    Four PRs in this rebuild landed above it, so "there is no bucket for this" is information a
    reviewer needs rather than a rounding detail.
    """
    _install(monkeypatch, files=[_file("src/tether/x.py", 1200)], labels=["size:L"])
    report = guard.measure(99)
    assert any("largest rung that exists" in f for f in report["findings"])


def test_lockfiles_are_excluded_from_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quoting the label descriptions: "excl. lockfiles/generated".

    A re-solved lock is thousands of lines of machine output and says nothing about how much a human
    has to review - and a base re-lock drifts every pin, so it is the largest diff in the
    repository.

    """
    files = [_file("conda-lock.yml", 5000), _file("src/tether/x.py", 40)]
    _install(monkeypatch, files=files, labels=["size:XS"])
    report = guard.measure(99)
    assert report["added"] == 40
    assert report["findings"] == []


def test_no_size_label_is_reported_rather_than_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`size:*` is applied at grooming, so its absence is a grooming gap, not a default."""
    _install(monkeypatch, files=[_file("src/tether/x.py", 5000)], labels=[])
    report = guard.measure(99)
    assert report["budget"] is None
    assert report["ratio"] is None
    assert any("no single size:* label" in f for f in report["findings"])


def test_two_size_labels_are_also_reported_rather_than_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picking the larger forgives a mis-grooming; picking the smaller invents a breach."""
    _install(monkeypatch, files=[_file("x.py", 10)], labels=["size:XS", "size:L"])
    assert guard.measure(99)["budget"] is None


def test_the_size_label_is_read_from_the_pr_as_well_as_the_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Either placement works: grooming labels the issue, reviewers look at the PR."""
    fake = _install(monkeypatch, files=[_file("x.py", 10)], labels=[])
    fake.routes[("GET", "/repos/bioedca/tether/pulls/99")] = (
        200,
        {
            "number": 99,
            "body": "Closes: #7\n",
            "labels": [{"name": "size:XS"}],
            "base": {"sha": "a" * 40},
            "head": {"sha": "b" * 40},
        },
    )
    assert guard.measure(99)["budget"] == 50


# --------------------------------------------------------------- the proportional rule


def test_the_proportional_rule_uses_diff_additions_not_file_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ambiguity C11b names, settled.

    #269 is "303 against a cap of 516" read as resulting file sizes and "188 vs 362" read as diff
    additions. Both readings were in use, so the guard has to pick one - additions, for consistency
    with the budget - and a test has to pin which, or two people compute different answers.
    """
    files = [_file("src/tether/x.py", 100), _file("tests/test_x.py", 250)]
    _install(monkeypatch, files=files, labels=["size:L"])
    report = guard.measure(99)
    assert (report["source_added"], report["test_added"]) == (100, 250)
    assert report["proportional_cap"] == 200
    assert any("cap of 200" in f for f in report["findings"])


def test_the_proportional_rule_has_a_floor_so_small_changes_can_be_tested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-line fix with a 60-line regression test is good practice, not a scope breach.

    `size:S` rather than `size:XS`, so the BUDGET is not what fires: 61 added lines is over XS's 50,
    and the point of this test is that the *proportional* rule stays quiet at 2 x 1 source line.

    """
    files = [_file("src/tether/x.py", 1), _file("tests/test_x.py", 60)]
    _install(monkeypatch, files=files, labels=["size:S"])
    report = guard.measure(99)
    assert report["proportional_cap"] == guard.PROPORTIONAL_FLOOR
    assert report["findings"] == []


def test_a_new_oversized_test_file_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    files = [_file("tests/test_big.py", 600, status="added")]
    _install(monkeypatch, files=files, labels=["size:L"])
    assert any("over the 400-line cap" in f for f in guard.measure(99)["findings"])


def test_growing_an_existing_test_file_is_not_the_new_file_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is on NEW files. Adding 600 lines to an existing suite is the budget's business."""
    files = [_file("tests/test_big.py", 600, status="modified")]
    _install(monkeypatch, files=files, labels=["size:L"])
    assert not any("400-line cap" in f for f in guard.measure(99)["findings"])


# ------------------------------------------------------------------- the linked issue


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Closes: #7\n", [7]),
        ("closes #7\n", [7]),
        ("Closes: #7\nCloses: #8\n", [7, 8]),
        ("Refs: #7\n", []),
    ],
    ids=["footer", "bare", "two", "refs-only"],
)
def test_linked_issues_come_from_closes_footers_only(
    monkeypatch: pytest.MonkeyPatch, body: str, expected: list[int]
) -> None:
    """`Refs:` is not counted: pointing at related work is normal, not scope creep."""
    _install(monkeypatch, files=[_file("x.py", 1)], labels=["size:XS"], body=body)
    assert guard.measure(99)["linked_issues"] == expected


@pytest.mark.parametrize("body", ["Refs: #7\n", "Closes: #7\nCloses: #8\n"], ids=["none", "two"])
def test_anything_other_than_one_linked_issue_is_reported(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _install(monkeypatch, files=[_file("x.py", 1)], labels=["size:XS"], body=body)
    assert any("exactly one `Closes:`" in f for f in guard.measure(99)["findings"])


# ----------------------------------------------------------------- the prose-drift guard


def test_a_test_that_pins_governance_prose_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retired prose-drift guard returning under a new name.

    `tests/test_review_policy.py` was deleted in #260 because pinning `AGENTS.md`'s wording blocked
    reordering the contract. A new test that reads the same file and asserts on its text is the same
    mistake with a different name.
    """
    patch = '+    assert "two rounds" in Path("AGENTS.md").read_text()'
    files = [_file("tests/test_new_policy.py", 10, status="added", patch=patch)]
    _install(monkeypatch, files=files, labels=["size:XS"])
    assert any("pin governance prose" in f for f in guard.measure(99)["findings"])


def test_the_adr_index_test_is_allowlisted_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """C22: the heuristic would flag a pre-existing test that is a real PRD §12.7 gate.

    `tests/test_adr_index.py` reads `docs/adr/*.md`, but it asserts on STRUCTURE - every record
    indexed, every cross-link resolving - never on wording. Without the allowlist this guard lands
    red on `main`.

    """
    patch = '+    assert INDEX.read_text().count("docs/adr/0057-x.md")'
    files = [_file("tests/test_adr_index.py", 10, status="modified", patch=patch)]
    _install(monkeypatch, files=files, labels=["size:XS"])
    assert guard.measure(99)["findings"] == []
    assert "tests/test_adr_index.py" in guard.PROSE_GUARD_ALLOWLIST


def test_this_suite_is_allowlisted_because_its_fixtures_are_the_detected_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found by running the guard against its own PR: its first live finding was against itself.

    The fixture strings above *are* the shape the heuristic detects - that is what makes them
    fixtures - so a suite that tests the detector cannot avoid tripping it. Flagging them would
    accuse the tests of the defect they exist to catch, so the file is allowlisted here rather than
    the rule being complicated into telling a quoted diff line from a real one.
    """
    patch = '+    patch = \'+ assert "x" in Path("AGENTS.md").read_text()\''
    files = [_file("tests/test_scope_guard.py", 10, status="modified", patch=patch)]
    _install(monkeypatch, files=files, labels=["size:XS"])
    assert guard.measure(99)["findings"] == []
    assert "tests/test_scope_guard.py" in guard.PROSE_GUARD_ALLOWLIST


def test_a_docstring_that_merely_cites_the_contract_is_not_prose_pinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heuristic's own false positive, found by replaying it over #287 and now pinned.

    The first version matched any quoted `.md` name, including backticked ones, so it flagged
    `tests/test_swarm_slots.py` — whose docstrings cite `AGENTS.md` to explain why each test exists.
    Citing the contract is exactly what a good test docstring does, so flagging it would have taught
    the opposite of the intended lesson.

    The fix is two conditions instead of one: a Python string literal (never a backtick) **and** a
    file read on the same added line, which is the shape the retired guard actually had.
    """
    patch = "\n".join(
        [
            '+    """`AGENTS.md` requires this refusal be proven by a test, not by prose."""',
            "+    # See AGENTS.md §Review gate and docs/PRD.md §12.4.",
            '+    assert result["mode"] == "refuse"',
        ]
    )
    files = [_file("tests/test_swarm_slots.py", 30, status="added", patch=patch)]
    _install(monkeypatch, files=files, labels=["size:XS"])
    assert guard.measure(99)["findings"] == []


def test_asserting_on_a_workflow_or_script_is_not_prose_pinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executable and configuration files are code that happens not to be Python.

    A parsed `permissions:` mapping or the absence of a `Remove-Item` in a reclaim path is an
    assertion about behaviour. `tests/test_triage.py` and `tests/test_swarm_slots.py` both do
    exactly this, so a heuristic that flagged them would fire on most of the machinery in this
    rebuild.

    """
    patch = (
        '+    assert doc["permissions"] == {"contents": "read"}'
        "  # .github/workflows/agent-triage.yml"
    )
    files = [_file("tests/test_triage.py", 10, status="added", patch=patch)]
    _install(monkeypatch, files=files, labels=["size:XS"])
    assert guard.measure(99)["findings"] == []


# -------------------------------------------------------------------- materiality


def test_reformatting_and_rewording_a_docstring_is_not_material() -> None:
    """`AGENTS.md`: formatting and comment/docstring edits are explicitly non-material.

    Python goes through `ast.dump` with docstrings stripped, so a rewrap cannot re-arm a review
    round. Half the churn in this rebuild was rewrapping prose to the 100-column house limit.

    """
    before = {"a.py": '"""One line."""\n\n\ndef f(x):\n    return x + 1\n'}
    after = {
        "a.py": (
            '"""One line,\nnow two and reworded.\n"""\n\n\n'
            "def f(x):\n    # a new comment\n    return x + 1\n"
        )
    }
    verdict = guard.materiality(before, after)
    assert verdict["material"] is False
    assert verdict["material_paths"] == []


def test_a_changed_expression_is_material() -> None:
    before = {"a.py": "def f(x):\n    return x + 1\n"}
    after = {"a.py": "def f(x):\n    return x + 2\n"}
    assert guard.materiality(before, after)["material"] is True


def test_an_adr_renumber_is_not_material() -> None:
    """Explicitly non-material in `AGENTS.md`, and it is a RENAME: the body never changes.

    This is #237's shape - a collision forced a renumber, which previously invalidated reviews
    across three PRs at once. Canonicalising the number out of the path is what makes the pair match
    up.

    """
    body = "# 0054 - a decision\n\nText that does not change.\n"
    before = {"docs/adr/0054-a-decision.md": body}
    after = {"docs/adr/0058-a-decision.md": body}
    verdict = guard.materiality(before, after)
    assert verdict["material"] is False
    assert verdict["before_digest"] == verdict["after_digest"]


def test_an_adr_whose_text_changed_is_material_even_if_renumbered() -> None:
    """The path canonicalisation must not swallow a real edit that rides along with a renumber."""
    before = {"docs/adr/0054-a.md": "# 0054\n\nAccepted.\n"}
    after = {"docs/adr/0058-a.md": "# 0058\n\nSuperseded.\n"}
    assert guard.materiality(before, after)["material"] is True


def test_reindenting_yaml_is_not_material_but_changing_a_value_is() -> None:
    before = {"w.yml": "permissions:\n  contents: read\n"}
    assert (
        guard.materiality(before, {"w.yml": "permissions:\n    contents: read\n"})["material"]
        is False
    )
    assert (
        guard.materiality(before, {"w.yml": "permissions:\n  contents: write\n"})["material"]
        is True
    )


def test_moving_a_yaml_key_out_of_its_parent_is_material() -> None:
    """In YAML the indentation IS the nesting, so normalising whitespace is wrong, not conservative.

    Codex's case on this PR, and the reason `_structured` parses instead of substituting. Under a
    `re.sub(r"\\s+", " ", text)` canonical form both documents below collapse to
    `root: child: 1 peer: 2` and receive the same digest - so promoting `peer` from a child of
    `root` to a top-level key, which is a real change to what a workflow does, would be classified
    as non-material and would preserve review evidence it should have invalidated.
    """
    nested = {"w.yml": "root:\n  child: 1\n  peer: 2\n"}
    promoted = {"w.yml": "root:\n  child: 1\npeer: 2\n"}
    assert guard.materiality(nested, promoted)["material"] is True


def test_whitespace_inside_a_quoted_string_is_material() -> None:
    """The same substitution rewrote string *contents*, where whitespace is data.

    A workflow step's `run:` is the case that matters here: collapsing the spaces in a command line
    makes two different commands digest identically.
    """
    before = {"w.yml": 'jobs:\n  a:\n    steps:\n      - run: "pytest  -k  slow"\n'}
    after = {"w.yml": 'jobs:\n  a:\n    steps:\n      - run: "pytest -k slow"\n'}
    assert guard.materiality(before, after)["material"] is True


def test_reordering_yaml_sequence_entries_is_material_but_mapping_keys_are_not() -> None:
    """Mapping order is not semantic in any of these formats; sequence order is.

    A workflow's `steps:` run in the order written, so reordering them changes behaviour. Sorting
    everything - the obvious implementation - would call that non-material.
    """
    steps = {"w.yml": "steps:\n  - run: build\n  - run: test\n"}
    swapped = {"w.yml": "steps:\n  - run: test\n  - run: build\n"}
    assert guard.materiality(steps, swapped)["material"] is True

    keys = {"w.yml": "permissions:\n  contents: read\n  issues: read\n"}
    reordered = {"w.yml": "permissions:\n  issues: read\n  contents: read\n"}
    assert guard.materiality(keys, reordered)["material"] is False


def test_actions_scalars_are_not_coerced_to_booleans() -> None:
    """PyYAML is YAML 1.1, where `on`/`off`/`yes`/`no` are booleans. These files are workflows.

    Codex's round-2 finding, and it is the *same* collapse the whitespace substitution had, reached
    by a different route: an unquoted `with:` input edited from `on` to `yes` sends a different
    string to the action while both resolve to JSON `true`, so the push digests as unchanged and
    stale review evidence survives a real change.

    `tests/test_triage.py` reaching a workflow's trigger block through the key `True` is the same
    coercion, visible in the repository already.
    """
    step = "jobs:\n  a:\n    steps:\n      - with:\n          flag: {}\n"
    assert guard.materiality({"w.yml": step.format("on")}, {"w.yml": step.format("yes")})[
        "material"
    ], "`on` and `yes` are different strings to the action"

    # The trigger key itself must stay the string "on", not become True.
    assert '"on"' in guard._canonical("w.yml", "on:\n  push:\n    branches: [main]\n")

    # And real 1.2 booleans still parse as booleans, so `true` and `True` remain one value.
    assert guard.materiality({"w.yml": "x: true\n"}, {"w.yml": "x: True\n"})["material"] is False


def test_unparseable_structured_config_falls_back_to_verbatim() -> None:
    """Fail closed. A file that will not parse is unknown, and unknown must not read as unchanged.

    Deliberately a fallback rather than the `GuardError` Python gets: a Python file that does not
    parse cannot have been committed by a green PR, while a YAML *fragment* or a format this parser
    does not know can legitimately appear in a fixture.
    """
    before = {"w.yml": "a: [1, 2\n"}
    assert guard.materiality(before, {"w.yml": "a: [1, 2\n"})["material"] is False
    assert guard.materiality(before, {"w.yml": "a: [1, 3\n"})["material"] is True


def test_json_and_toml_go_through_their_own_parsers() -> None:
    assert (
        guard.materiality({"a.json": '{"x": 1, "y": 2}'}, {"a.json": '{\n  "y": 2,\n  "x": 1\n}'})[
            "material"
        ]
        is False
    )
    assert guard.materiality({"a.json": '{"x": 1}'}, {"a.json": '{"x": 2}'})["material"] is True
    assert (
        guard.materiality({"p.toml": "[t]\nx = 1\n"}, {"p.toml": "[t]\n\nx   =   1\n"})["material"]
        is False
    )
    assert (
        guard.materiality({"p.toml": "[t]\nx = 1\n"}, {"p.toml": "[t]\nx = 2\n"})["material"]
        is True
    )


def test_prose_is_compared_verbatim() -> None:
    """No canonical form is guessed for prose.

    Normalising whitespace in Markdown would call a real rewording non-material as soon as it
    changed the line breaks, and governance prose IS material under `AGENTS.md`.

    """
    before = {"AGENTS.md": "- One rule.\n"}
    after = {"AGENTS.md": "- One\n  rule.\n"}
    assert guard.materiality(before, after)["material"] is True


def test_unparseable_python_is_an_error_not_a_silent_non_material() -> None:
    """A file that will not parse is unknown, and unknown must never read as "nothing changed"."""
    with pytest.raises(guard.GuardError, match="materiality digest"):
        guard.materiality({"a.py": "def f(:\n"}, {"a.py": "def f():\n    pass\n"})


def _contents(sha: str, path: str, text: str | None) -> tuple[str, tuple[int, Any]]:
    route = f"/repos/bioedca/tether/contents/{path}?ref={sha}"
    if text is None:
        return (route, (404, None))
    payload = {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
    return (route, (200, payload))


def test_the_digest_is_wired_to_a_real_pair_of_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The classification has to be *computed on a push*, not merely computable.

    Codex's finding on this PR: `materiality()` existed with tests and nothing called it, so the
    contract's material-change rule still had no automated answer. `--since` is the wiring, and
    `github.event.before` is what the workflow passes into it.
    """
    fake = _install(monkeypatch, files=[_file("w.yml", 1)], labels=["size:XS"])
    old, new = "1" * 40, "b" * 40
    fake.routes[("GET", f"/repos/bioedca/tether/compare/{old}...")] = (
        200,
        {"files": [_file("w.yml", 1)]},
    )
    pair = ((old, "root:\n  child: 1\n  peer: 2\n"), (new, "root:\n  child: 1\npeer: 2\n"))
    for sha, text in pair:
        route, response = _contents(sha, "w.yml", text)
        fake.routes[("GET", route)] = response

    verdict = guard.measure(99, since=old)["materiality"]
    assert verdict["material"] is True
    assert verdict["material_paths"] == ["w.yml"]


def test_no_since_means_no_verdict_rather_than_a_false_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """`opened` has no previous head, and "nothing changed" is not the right answer to that."""
    _install(monkeypatch, files=[_file("w.yml", 1)], labels=["size:XS"])
    assert guard.measure(99)["materiality"] is None


def test_a_file_added_by_the_push_is_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent-before is a real state, not an empty edit.

    `_blob` answers None there, so the pair differs and the addition is seen.
    """
    fake = _install(monkeypatch, files=[_file("new.py", 1)], labels=["size:XS"])
    old, new = "1" * 40, "b" * 40
    fake.routes[("GET", f"/repos/bioedca/tether/compare/{old}...")] = (
        200,
        {"files": [_file("new.py", 1, status="added")]},
    )
    for sha, text in ((old, None), (new, "x = 1\n")):
        route, response = _contents(sha, "new.py", text)
        fake.routes[("GET", route)] = response

    assert guard.measure(99, since=old)["materiality"]["material"] is True


def test_a_file_too_large_to_read_is_material_not_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over ~1 MB the contents API answers with an empty body and `encoding: none`.

    A constant placeholder would make both sides equal and call every large file unchanged, which is
    the fail-open direction; the sentinel carries the SHA so the pair always differs.
    """
    fake = _install(monkeypatch, files=[_file("big.bin", 1)], labels=["size:XS"])
    old, new = "1" * 40, "b" * 40
    fake.routes[("GET", f"/repos/bioedca/tether/compare/{old}...")] = (
        200,
        {"files": [_file("big.bin", 1)]},
    )
    for sha in (old, new):
        fake.routes[("GET", f"/repos/bioedca/tether/contents/big.bin?ref={sha}")] = (
            200,
            {"encoding": "none", "content": ""},
        )

    assert guard.measure(99, since=old)["materiality"]["material"] is True


# --------------------------------------------------------------------------- reporting


def test_review_rounds_counts_distinct_heads_like_triage_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two providers at one head are ONE round, matching `triage.py`.

    Reported, never acted on here - it shares the undercount that counter documents, and this is a
    number for a human audit rather than an input to a decision.
    """
    reviews = [
        {"user": {"login": "chatgpt-codex-connector[bot]"}, "commit_id": "c" * 40},
        {"user": {"login": "coderabbitai[bot]"}, "commit_id": "c" * 40},
        {"user": {"login": "bioedca"}, "commit_id": "d" * 40},
    ]
    _install(monkeypatch, files=[_file("x.py", 1)], labels=["size:XS"], reviews=reviews)
    assert guard.measure(99)["review_rounds"] == 1


def test_a_pr_opened_ready_keeps_the_rounds_it_spent_before_a_draft_excursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror has to mirror *every* branch, not just the common one. Codex raised this on #385.

    A PR opened ready emits no `ready_for_review`, so a later draft excursion and return makes that
    return look like the first entry into the counted phase. Taking `min(ready)` therefore discarded
    every round spent before it, and the guard reported one fewer than `triage` - which reads as
    *the labels are ahead of the evidence*, the exact corruption this guard exists to detect. A
    `convert_to_draft` earlier than any `ready_for_review` is the signal that the clock started at
    creation.
    """
    timeline = [
        {"event": "convert_to_draft", "created_at": "2026-08-02T00:00:00Z"},
        {"event": "ready_for_review", "created_at": "2026-08-03T00:00:00Z"},
    ]
    reviews = [
        # Before the draft excursion, so a `min(ready)` cutoff would drop it.
        {
            "user": {"login": "coderabbitai[bot]"},
            "commit_id": "c" * 40,
            "submitted_at": "2026-08-01T00:00:00Z",
        },
        {
            "user": {"login": "coderabbitai[bot]"},
            "commit_id": "e" * 40,
            "submitted_at": "2026-08-03T12:00:00Z",
        },
    ]
    _install(
        monkeypatch,
        files=[_file("x.py", 1)],
        labels=["size:XS"],
        reviews=reviews,
        timeline=timeline,
    )
    assert guard.measure(99)["review_rounds"] == 2, "a draft excursion must refund no round"


def test_a_review_taken_during_the_prescribed_draft_phase_is_not_a_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction, and the reason the mirror needs the timeline at all.

    A PR that has never been ready is in the free phase, so a metered review spent there costs no
    round. Counting it would report the PR as spent while `triage` reports it as not.
    """
    reviews = [
        {
            "user": {"login": "coderabbitai[bot]"},
            "commit_id": "c" * 40,
            "submitted_at": "2026-08-01T00:00:00Z",
        }
    ]
    _install(
        monkeypatch,
        files=[_file("x.py", 1)],
        labels=["size:XS"],
        reviews=reviews,
        timeline=[],
        draft=True,
    )
    assert guard.measure(99)["review_rounds"] == 0


def test_the_report_always_declares_itself_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    """It will be quoted in arguments about other changes, so it must not read as a verdict."""
    _install(monkeypatch, files=[_file("x.py", 5000)], labels=["size:XS"])
    report = guard.measure(99)
    assert report["advisory"] is True
    assert "Advisory only" in guard._render(report)


def test_a_base_override_changes_the_measurement_not_only_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#216 resumes from `fe74ae4`, whose checkpoint is already +259/-20 against an XS budget of 50.

    Measured from `main` the PR's own file list carries those 259 lines, so the claimant is charged
    for work they inherited and an XS item reads as 5.6x over. Recording the base in the report
    without re-measuring would leave that verdict exactly as wrong, only now with a footnote - so
    the override is pinned as a *measurement*, and the assertion is that the inherited lines are
    gone from `added`.
    """
    fake = _install(monkeypatch, files=[_file("x.py", 259 + 12)], labels=["size:XS"])
    fake.routes[("GET", "/repos/bioedca/tether/compare/fe74ae4...")] = (
        200,
        {"files": [_file("x.py", 12)]},
    )
    report = guard.measure(99, base_override="fe74ae4")
    assert report["base"] == "fe74ae4"
    assert report["added"] == 12, "the checkpoint's own lines are not the claimant's"
    assert report["findings"] == []


def test_a_truncated_compare_is_an_error_not_a_smaller_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compare endpoint caps its file list at 300 and does not paginate it.

    Under-reporting is the fail-open direction for a measurement whose entire purpose is to notice
    something being too large, so the cap is refused rather than measured.
    """
    fake = _install(monkeypatch, files=[_file("x.py", 1)], labels=["size:XS"])
    fake.routes[("GET", "/repos/bioedca/tether/compare/fe74ae4...")] = (
        200,
        {"files": [_file(f"f{i}.py", 1) for i in range(guard.MAX_COMPARE_FILES)]},
    )
    with pytest.raises(guard.GuardError, match="truncated"):
        guard.measure(99, base_override="fe74ae4")


def test_the_cli_reports_json_and_a_named_over_budget_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "Over budget" and "the measurement broke" must be distinguishable without parsing prose."""
    _install(monkeypatch, files=[_file("x.py", 5000)], labels=["size:XS"])
    monkeypatch.setattr("sys.argv", ["scope_guard.py", "--pr", "99"])
    assert guard.main() == guard.EXIT_OVER_BUDGET
    assert json.loads(capsys.readouterr().out)["advisory"] is True


def test_a_clean_pr_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, files=[_file("x.py", 10)], labels=["size:XS"])
    monkeypatch.setattr("sys.argv", ["scope_guard.py", "--pr", "99"])
    assert guard.main() == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []


def test_an_unreadable_file_list_is_an_error_not_an_empty_diff(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty diff scores zero against every budget, so a failed read must not look like one."""
    fake = _install(monkeypatch, files=[], labels=["size:XS"])
    fake.routes[("GET", "/repos/bioedca/tether/pulls/99/files")] = (502, None)
    monkeypatch.setattr("sys.argv", ["scope_guard.py", "--pr", "99"])
    assert guard.main() == 2
    assert "error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------- workflow


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_is_not_and_must_not_become_required() -> None:
    """D5, and the reason is in the file rather than only in a plan.

    The thresholds are known-miscalibrated, so a required check here would make the ladder
    impossible to fix without a red `main`.

    """
    header = WORKFLOW.read_text(encoding="utf-8")
    assert "NOT REQUIRED" in header
    assert "main-baseline" in header, "name the ruleset it must stay out of"


def test_the_workflow_grants_no_write_scope() -> None:
    assert _workflow()["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "read",
    }


def test_the_workflow_checks_out_the_default_branch() -> None:
    """A PR must not be able to rewrite its own budget.

    `pull_request` gives the PR's merge ref, so the default checkout would run the measurement code
    from the branch being measured. The same defect #285 had to fix.
    """
    steps = _workflow()["jobs"]["measure"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"


def _exit_classifier() -> str:
    """The workflow's `case` over the guard's exit code, extracted from the parsed run script."""
    run = _workflow()["jobs"]["measure"]["steps"][-1]["run"]
    start = run.index('case "${code}" in')
    return run[start : run.index("esac", start)]


def test_over_budget_does_not_fail_the_job() -> None:
    """Exit 3 is a RESULT. A measurement that did not complete is not."""
    body = WORKFLOW.read_text(encoding="utf-8")
    classifier = _exit_classifier()
    assert "3)" in classifier and "::warning::" in classifier, "over budget warns, never fails"
    statements = [
        line.split("#", 1)[0] for line in body.splitlines() if not line.strip().startswith("#")
    ]
    assert not [s for s in statements if s.strip() == "exit 0"], (
        "an explicit zero exit reports failure"
    )


def test_the_advisory_exit_survives_the_inherited_errexit() -> None:
    """`shell: bash -el {0}` means errexit is ALREADY ON, and `set -uo pipefail` does not clear it.

    Found by CodeRabbit on this PR, and it had never fired: on the pull request that introduces the
    workflow the script is not on the default branch yet, so every run so far took the bootstrap
    branch and exited 0. The first genuinely over-budget PR after merge would have been the first
    execution of this line - and it would have failed the job, turning the advisory check into a
    gate at precisely the moment it had something to report.

    The invocation must therefore handle its own failure. Inside a `||` list errexit does not fire;
    a bare command followed by `code=$?` aborts before `code` is ever assigned.
    """
    step = _workflow()["jobs"]["measure"]["steps"][-1]
    assert step["shell"] == "bash -el {0}", "the premise of this test - errexit comes from `-e`"

    run = step["run"]
    invocation = run[run.index("python .agents/bin/scope_guard.py") :]
    invocation = invocation[: invocation.index("\n", invocation.index("scope.md"))]
    assert "|| code=$?" in invocation, (
        "under errexit the guard's advisory exit must be handled by the invocation itself; "
        f"got: {invocation!r}"
    )


def test_every_unexpected_exit_code_fails_the_job() -> None:
    """A job reporting success without a valid measurement is worse than no job.

    The classification used to name exit 2 and let everything else fall through to the terminal
    `echo`, so an import-time traceback exiting 1, or a signal, reported a clean budget check that
    never ran. Only 0 and the advisory 3 are success; the default arm takes the rest.
    """
    classifier = _exit_classifier()
    assert "*)" in classifier, "a default arm must exist"
    default = classifier[classifier.index("*)") :]
    assert "exit 1" in default, "an unexpected exit must fail the job"
    assert "::error::" in default
    assert "2)" not in classifier, (
        "exit 2 must be covered by the default arm, not named - naming it is what left every "
        "other code falling through"
    )


def test_the_guard_counts_the_head_a_provider_actually_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#307 in the audit half. A guard that disagrees with the counter it mirrors is worse than one
    that does not mirror it at all — the two would report different round counts for one PR and
    there would be no way to tell which was right.
    """
    # A METERED provider: since ADR-0062 the guard mirrors `triage.METERED_PROVIDERS`, and Codex —
    # the unmetered lane — consumes no round at all. The #307 property under test is the head
    # attribution, not which provider it belongs to.
    reviews = [
        {"user": {"login": "coderabbitai[bot]"}, "commit_id": "c" * 40},
        {
            "user": {"login": "coderabbitai[bot]"},
            "original_commit_id": "c" * 40,
            "commit_id": "d" * 40,
        },
    ]
    _install(monkeypatch, files=[_file("x.py", 1)], labels=["size:XS"], reviews=reviews)
    assert guard.measure(99)["review_rounds"] == 1
