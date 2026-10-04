# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The pull-request template exposes every review-evidence field, and the launcher grants no merge.

#260 retired ``tests/test_review_policy.py`` because it pinned the *wording* of the review policy
and so blocked that policy's own correction. Two of its tests were not prose matchers, and #261
re-covers them structurally:

* the template must carry each review-evidence field the lane records — once each, as a fillable
  ``- <field>:`` bullet under ``## Linked tracking``. A field that goes missing is evidence nobody
  is asked for; a field that appears twice is two places to record one fact, and they drift.
* the worker launcher config must still invoke the worker skill and its claim tool, and must carry
  no merge command: merge authority is per-PR and never conferred by a launcher.

Only labels and shape are asserted. No sentence of ``AGENTS.md``, ``CONTRIBUTING.md``,
``docs/PRD.md`` or ``docs/adr/**`` is encoded here, and a field's guidance text after its label may
change freely — that is the property #260 needed and #261 requires.

Stdlib only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

TEMPLATE = _REPO / ".github" / "pull_request_template.md"
LAUNCHER = _REPO / ".agents" / "skills" / "tether-worker" / "agents" / "openai.yaml"

# The review-evidence fields a pull request records, by label. Each names a leg or an outcome of
# the review lane: the head the evidence binds, each provider's result, the provider that produced
# none, the disposition of findings, and the human sign-off.
REVIEW_EVIDENCE_FIELDS = (
    "Final head SHA",
    "Codex",
    "Codex closing review",
    "Greptile",
    "CodeRabbit",
    "Provider that did not review",
    "Findings",
    "Human sign-off",
)

# A field label ends at its first ":" or at a spaced em dash, whichever comes first — the two
# separators the template uses between a label and its guidance.
_LABEL_END = re.compile(r":| — ")


def _section(text: str, heading: str) -> list[str]:
    """The lines under ``## <heading>``, up to the next ``## `` heading."""
    lines = text.splitlines()
    start = lines.index(f"## {heading}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def _field(line: str) -> tuple[str, str] | None:
    """Split a top-level ``- <label>: <value>`` bullet into its label and the rest, or ``None``."""
    if not line.startswith("- ") or line.startswith("- ["):
        return None
    bare = line[2:].replace("**", "")
    match = _LABEL_END.search(bare)
    if match is None:
        return None
    return bare[: match.start()].strip(), bare[match.end() :].strip()


def _fields() -> list[tuple[str, str]]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return [f for f in map(_field, _section(text, "Linked tracking")) if f is not None]


def test_each_review_evidence_field_appears_exactly_once() -> None:
    """Every field is present, and none is duplicated."""
    counts = Counter(label for label, _ in _fields())
    wrong = {name: counts[name] for name in REVIEW_EVIDENCE_FIELDS if counts[name] != 1}
    assert not wrong, (
        f"review-evidence fields must each appear exactly once under '## Linked tracking'; "
        f"found {wrong} (0 = missing, >1 = duplicated)"
    )


def test_review_evidence_fields_are_fillable_bullets() -> None:
    """A field is a label the author fills in, so a value slot follows it on the same line.

    ``Final head SHA:`` is the one field whose slot is deliberately empty; the rest offer the
    states to choose from. What is pinned is that the line *is* a field — label, separator, slot —
    not what the guidance says.
    """
    fields = dict(_fields())
    for name in REVIEW_EVIDENCE_FIELDS:
        assert name in fields, f"'{name}' is not a '- {name}: …' bullet under '## Linked tracking'"
    unfilled = [n for n in REVIEW_EVIDENCE_FIELDS if n != "Final head SHA" and not fields[n]]
    assert not unfilled, f"these fields offer no value slot to fill: {unfilled}"


def _launcher_values() -> dict[str, str]:
    """The ``interface`` keys of the launcher config. Its shape is flat, so stdlib parsing holds."""
    values: dict[str, str] = {}
    for line in LAUNCHER.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s+(\w+):\s*\"(.*)\"\s*", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def test_launcher_invokes_the_worker_skill_and_its_claim_tool() -> None:
    """The launcher's job is to start one worker on the claim mutex — through the skill."""
    values = _launcher_values()
    for key in ("display_name", "short_description", "default_prompt"):
        assert values.get(key), f"{LAUNCHER.name} has no non-empty interface.{key}"
    prompt = values["default_prompt"]
    assert "$tether-worker" in prompt, "the launcher must invoke the tether-worker skill"
    assert ".agents/bin/claim.py" in prompt, "the launcher must route the claim through claim.py"
    assert (_REPO / ".agents" / "bin" / "claim.py").is_file(), "the claim tool it names is missing"


def test_launcher_carries_no_merge_command() -> None:
    """Merge authority is per pull request and explicit; a launcher must never embed it."""
    prompt = _launcher_values()["default_prompt"]
    assert "gh pr merge" not in prompt, (
        "the launcher prompt carries a merge command — merge authority is never a launcher default"
    )
