# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Both vendor lanes reach the same contract, and neither route may quietly stop working.

``AGENTS.md`` governs every agent here, but the two lanes load different files to reach it: Codex
takes ``.agents/skills/`` and its ``agents/openai.yaml``; Claude Code loads ``CLAUDE.md`` and
``.claude/skills/``. "The contract is reachable" is therefore a property of *both* routes.

It was true of only one. ``CLAUDE.md`` was gitignored (#312), so a fresh clone gave a Claude Code
session no project instructions at all — and the copy that happened to exist on one machine predated
the swarm rebuild, describing a worktree-per-concern flow with no claim, no mutex and no generation
fence while reading as though it were the contract.

``CLAUDE.md`` is now tracked and is an **adaptation** of ``AGENTS.md``: the same rules, in the file
this lane actually loads, so an agent has them resident rather than one indirection away.
Duplication is the deliberate trade and its cost is drift — so the copies are bound here.
``AGENTS.md`` is authoritative; a section it grows that ``CLAUDE.md`` has not adapted fails this
module.

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
        ["git", "ls-files", "--error-unmatch", path.relative_to(_REPO).as_posix()],
        cwd=_REPO,
        capture_output=True,
        check=False,
    )
    return out.returncode == 0


def _sections(path: Path) -> list[str]:
    """The ``##`` headings of a contract file, in order."""
    return re.findall(r"^##\s+(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)


def _description(path: Path) -> str:
    """A skill's frontmatter ``description`` — the field that decides when it activates."""
    match = re.search(
        r"^description:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert match, f"{path.name} has no frontmatter description"
    return match.group(1)


def test_both_lanes_entry_points_are_tracked() -> None:
    """A fresh clone must carry both routes.

    This is the assertion that would have failed before #312: `CLAUDE.md` was in `.gitignore`, so
    `git ls-files` returned nothing for it and a clone had no Claude entry point at all.
    """
    missing = [
        p.relative_to(_REPO).as_posix()
        for p in (CONTRACT, CLAUDE_ENTRY, CLAUDE_SKILL, CODEX_SKILL)
        if not _tracked(p)
    ]
    assert not missing, (
        f"these agent entry points are not tracked, so a clone lacks them: {missing}"
    )


def test_the_adaptation_names_the_contract_as_authoritative() -> None:
    """Duplication needs a tie-breaker, or a drift has no defined resolution.

    Stated up front, where it is read before anything it might contradict: `AGENTS.md` wins and
    `CLAUDE.md` is the bug. Without that sentence an agent finding a discrepancy has to guess, and
    the guess is the whole risk of keeping two copies.
    """
    head = "\n".join(CLAUDE_ENTRY.read_text(encoding="utf-8").splitlines()[:20])
    assert "AGENTS.md" in head, "the contract must be named up front, not buried"
    assert re.search(r"`?AGENTS\.md`?\*{0,2}\s+is authoritative", head), (
        "the adaptation must say AGENTS.md wins on conflict"
    )


def test_the_claude_entry_point_is_a_pointer_not_a_second_contract() -> None:
    """The replacement for the two drift guards this file used to carry (ADR-0064).

    Those guards bound a hand-written *adaptation*: every `##` section of `AGENTS.md` had to appear
    in `CLAUDE.md`, and the two had to stay within a factor of two in size. They worked, and the
    thing they were protecting was the problem. Two files said the same eight things in different
    words — only 49 of ~150 lines byte-identical — so every contract edit cost two files and a test
    run, and ADR-0057's third driver (contract text is resident context, so its size is a running
    cost) was being paid twice for one contract.

    A pointer cannot drift, so the property worth binding changes: not *does it adapt every section*
    but *is it still a pointer*. `.claude/skills/tether-worker/SKILL.md` is bound the same way by
    `test_the_claude_skill_is_a_pointer_with_identical_routing_metadata` below; this is that rule
    applied one level up.
    """
    text = CLAUDE_ENTRY.read_text(encoding="utf-8")
    assert "AGENTS.md" in text, "it must name its target"
    assert CONTRACT.is_file(), "the pointer's target does not exist"

    contract_lines = len(CONTRACT.read_text(encoding="utf-8").splitlines())
    entry_lines = len(text.splitlines())
    assert entry_lines * 2 < contract_lines, (
        f"CLAUDE.md is {entry_lines} lines against AGENTS.md's {contract_lines}; it looks like a "
        "copy of the contract rather than a pointer to it"
    )
    assert not _sections(CLAUDE_ENTRY), (
        "CLAUDE.md carries `##` sections of its own, so it has started restating the contract "
        "instead of pointing at it"
    )


def test_the_claude_skill_is_a_pointer_with_identical_routing_metadata() -> None:
    """One skill, two lookup paths — and the frontmatter decides when it activates.

    Codex's finding on #313: the pointer had copied the canonical `description` and the two had
    *already* drifted ("Use when asked" against "Use when an agent is asked"). A pointer whose
    activation metadata differs is not a pointer; it is a second skill firing under different
    conditions. Byte equality, asserted — the one field that must never diverge.
    """
    assert _description(CLAUDE_SKILL) == _description(CODEX_SKILL), (
        "the pointer's activation metadata has drifted from the canonical skill"
    )
    text = CLAUDE_SKILL.read_text(encoding="utf-8")
    assert ".agents/skills/tether-worker/SKILL.md" in text, "it must name its target"
    assert CODEX_SKILL.is_file(), "the pointer's target does not exist"
    assert len(text.splitlines()) * 2 < len(CODEX_SKILL.read_text(encoding="utf-8").splitlines()), (
        "the Claude skill looks like a copy of the real one rather than a pointer to it"
    )


def test_claude_md_is_not_ignored_again() -> None:
    """The defect was an ignore rule, so the ignore rule is what this guards.

    Kept separate from the tracked check because they fail for different reasons, and a reader
    debugging one should not have to consider the other.
    """
    ignore = (_REPO / ".gitignore").read_text(encoding="utf-8")
    entries = {line.strip() for line in ignore.splitlines() if not line.lstrip().startswith("#")}
    assert "CLAUDE.md" not in entries, "CLAUDE.md must stay tracked — that is the defect #312 fixed"
