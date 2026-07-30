# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Both vendor lanes reach the same contract, and neither entry point restates it.

``AGENTS.md`` governs every agent here. The two lanes read different files to get there — Codex
takes ``.agents/skills/`` and its ``agents/openai.yaml``, Claude Code takes ``CLAUDE.md`` and
``.claude/skills/`` — so "the contract is reachable" is a property of *both* routes, not of the
contract.

It was true of only one. ``CLAUDE.md`` was gitignored (#312), so a fresh clone gave a Claude Code
session no project instructions at all, and the copy that happened to exist on one machine predated
the swarm rebuild: it described a worktree-per-concern flow with no claim, no mutex and no
generation fence, while reading as though it were the contract.

Two failure modes, and this module guards both:

* **the route disappears** — an entry point stops being tracked, or points at a file that is not
  there;
* **the route becomes a second contract** — an entry point starts restating rules, which then drift
  from ``AGENTS.md`` invisibly. That is what #280 spent twelve sites undoing, and a duplicate is
  worse than an absence because it is believed.

Stdlib only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

CLAUDE_ENTRY = _REPO / "CLAUDE.md"
CLAUDE_SKILL = _REPO / ".claude" / "skills" / "tether-worker" / "SKILL.md"
CODEX_SKILL = _REPO / ".agents" / "skills" / "tether-worker" / "SKILL.md"
CONTRACT = _REPO / "AGENTS.md"


def _tracked(path: Path) -> bool:
    """Whether git tracks this path. The whole defect was a file that was not."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(_REPO).as_posix())],
        cwd=_REPO,
        capture_output=True,
        check=False,
    )
    return out.returncode == 0


def test_both_lanes_entry_points_are_tracked() -> None:
    """A fresh clone must carry both routes.

    This is the assertion that would have failed before #312: `CLAUDE.md` was listed in
    `.gitignore`, so `git ls-files` returned nothing for it and a clone had no Claude entry point.
    """
    missing = [
        str(p.relative_to(_REPO))
        for p in (CONTRACT, CLAUDE_ENTRY, CLAUDE_SKILL, CODEX_SKILL)
        if not _tracked(p)
    ]
    assert not missing, (
        f"these agent entry points are not tracked, so a fresh clone does not have them: {missing}"
    )


def test_the_claude_entry_point_routes_to_the_contract() -> None:
    """It must name `AGENTS.md` as governing, not merely mention it in passing.

    "Mentions AGENTS.md" is too weak a bar — a file can name it while still telling an agent to do
    something else. The bar here is that it says AGENTS.md *governs*, in the first few lines, where
    it is read before anything it might contradict.
    """
    text = CLAUDE_ENTRY.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:20])
    assert "AGENTS.md" in head, "the contract must be named up front, not buried"
    assert re.search(r"AGENTS\.md`?\*{0,2}\s+is the contract", head), (
        "the entry point must state that AGENTS.md governs, not just link it"
    )


def test_the_claude_entry_point_does_not_restate_the_contract() -> None:
    """A second contract drifts from the first, and the drift is invisible until it is followed.

    Checked as *shared long lines* rather than by topic: prose copied from `AGENTS.md` shows up as
    identical sentences, while a legitimate pointer names a file and says where to read it. The
    threshold is deliberately generous — this is meant to catch paragraphs, not phrases like
    "arm auto-merge".
    """

    def sentences(path: Path) -> set[str]:
        text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
        return {s.strip() for s in re.split(r"(?<=[.!?]) ", text) if len(s.strip()) >= 60}

    shared = sentences(CLAUDE_ENTRY) & sentences(CONTRACT)
    assert not shared, (
        "these sentences appear in BOTH CLAUDE.md and AGENTS.md — the entry point is becoming a "
        f"second contract; point at the rule instead of copying it: {sorted(shared)}"
    )


def test_the_claude_entry_point_stays_short() -> None:
    """It is resident context on every model call, so its length is a running cost.

    `AGENTS.md` is held to a similar bound for the same reason (ADR-0057, decision driver 3). A
    router that grows past this is almost certainly restating something.
    """
    lines = len(CLAUDE_ENTRY.read_text(encoding="utf-8").splitlines())
    assert lines <= 70, f"CLAUDE.md is {lines} lines; it routes, it does not explain"


def test_the_claude_skill_is_a_pointer_not_a_copy() -> None:
    """One skill, two lookup paths. Two copies could tell the lanes different things.

    The mutex is the one place an asymmetry between lanes is unaffordable: if the Claude copy and
    the Codex copy disagreed about claiming, two workers could believe different things about who
    owns an issue.
    """
    text = CLAUDE_SKILL.read_text(encoding="utf-8")
    assert ".agents/skills/tether-worker/SKILL.md" in text, "it must name its target"
    assert CODEX_SKILL.is_file(), "the pointer's target does not exist"

    # A pointer is short. The real skill is many times this; a copy would be too.
    pointer_lines = len(text.splitlines())
    real_lines = len(CODEX_SKILL.read_text(encoding="utf-8").splitlines())
    assert pointer_lines * 2 < real_lines, (
        f"the Claude skill is {pointer_lines} lines against the real one's {real_lines}; "
        "it looks like a copy rather than a pointer"
    )


def test_per_machine_notes_have_a_home_that_is_ignored() -> None:
    """The content displaced from the tracked file must still have somewhere to go.

    Without this, per-machine paths get written back into the tracked entry point — which is how it
    became unreviewable in the first place.
    """
    ignore = (_REPO / ".gitignore").read_text(encoding="utf-8")
    entries = {line.strip() for line in ignore.splitlines() if not line.lstrip().startswith("#")}
    assert "CLAUDE.local.md" in entries, "per-machine notes need an ignored home"
    assert "CLAUDE.md" not in entries, (
        "CLAUDE.md must not be ignored again — that is the defect #312 fixed"
    )
