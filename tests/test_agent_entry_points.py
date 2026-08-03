# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Both vendor lanes carry the same contract, and the copies cannot drift apart silently.

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


def test_every_contract_section_is_adapted() -> None:
    """The binding that makes duplication survivable.

    A rule added to `AGENTS.md` under a new heading is a rule this lane would never see. Matching on
    headings rather than prose is deliberate: an *adaptation* is supposed to reword, so demanding
    identical text would force the two files to be one and defeat the point of adapting.
    """
    contract = _sections(CONTRACT)
    adapted = set(_sections(CLAUDE_ENTRY))
    assert contract, "AGENTS.md has no sections; the parse is wrong"

    unadapted = [s for s in contract if s not in adapted]
    assert not unadapted, (
        "AGENTS.md has sections CLAUDE.md does not adapt, so this lane never sees those rules: "
        f"{unadapted}"
    )


def test_the_adaptation_stays_close_to_the_contract_in_size() -> None:
    """Both are resident context on every call, so both are a running cost (ADR-0057, driver 3).

    A bound rather than a target. Much shorter means rules were dropped; much longer means it has
    stopped being an adaptation and started being its own document.
    """
    contract_lines = len(CONTRACT.read_text(encoding="utf-8").splitlines())
    adapted_lines = len(CLAUDE_ENTRY.read_text(encoding="utf-8").splitlines())
    assert contract_lines <= adapted_lines <= contract_lines * 2, (
        f"CLAUDE.md is {adapted_lines} lines against AGENTS.md's {contract_lines}; "
        "it should track the contract, not shrink below it or grow into a separate document"
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


# --------------------------------------------------------------------------------------------
# The contract is not merely reachable, it is runnable (#382)
#
# ``swarm_slots._inner_command`` dispatches ``claude`` into ``wsl -e bash -lc`` and ``codex`` into a
# native PowerShell shim — from ONE shared skill file. A command in it that names one shell is wrong
# in the other lane, and nothing read that file's body until this block existed.

TASKS = _REPO / ".agents" / "tasks"

#: Everything a dispatched worker is told to run commands out of. Both lanes read all three.
WORKER_COMMANDS = (CODEX_SKILL, TASKS / "build.md", TASKS / "amend.md")

_FENCE = "```"

#: Info strings that commit a block to one shell. Naming either is wrong in the other lane.
SHELL_FENCES = frozenset(
    {"bash", "sh", "zsh", "console", "powershell", "pwsh", "ps1", "bat", "cmd"}
)

#: Shapes measured unrunnable in the shell the launcher actually dispatches into, 2026-08-03.
NATIVE_ONLY = (
    ('& "', "PowerShell's call operator is not a command in bash"),
    ("C:\\", "a drive-lettered path does not exist inside WSL"),
    ("$env:", "PowerShell environment syntax is not bash"),
)


def _blocks(path: Path) -> list[tuple[str, list[str]]]:
    """Every fenced block as ``(info string, its non-blank lines)``.

    Fences are matched after stripping indentation: two of the skill's blocks sit inside a numbered
    list, so an unstripped ``startswith`` would skip exactly the two that take the mutex.
    """
    blocks: list[tuple[str, list[str]]] = []
    info: str | None = None
    body: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(_FENCE):
            if info is None:
                info, body = line[len(_FENCE) :].strip(), []
            else:
                blocks.append((info, body))
                info = None
        elif info is not None and line:
            body.append(line)
    return blocks


def _commands(path: Path) -> list[str]:
    """Every command an agent is told to *run*: fenced lines, plus inline spans with arguments.

    Both forms carry instructions — the skill fences its commands, the task templates put theirs
    inline in a numbered step — so reading only one would have missed half of #382.

    An inline span with no whitespace is a *name*, not an invocation: prose saying WSL has no
    ``python`` is describing the defect, not committing it. Requiring an argument separates the two
    without needing a prose allowlist.
    """
    fenced = [line for _, body in _blocks(path) for line in body]
    spans = (span.strip() for span in re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8")))
    return fenced + [span for span in spans if " " in span]


def test_every_worker_command_runs_in_both_dispatched_shells() -> None:
    """#382: six ```powershell fences, handed to a session started with ``wsl -e bash -lc``.

    The worst of them was the arming line — ``& "C:\\Program Files\\GitHub CLI\\gh.exe" pr merge …``
    — and it is the ONLY auto-merge arming instruction in this repository. A worker that could not
    run it could not arm, which is why #327 and #334 both sat MERGEABLE/CLEAN with green checks and
    a null ``autoMergeRequest`` until a human merged them by hand.

    Two assertions, one property: a block that annotates one shell is wrong in the other lane
    whichever it names, and a command carrying that shell's syntax is wrong even under a bare fence.
    """
    fenced = [
        (path.name, info)
        for path in WORKER_COMMANDS
        for info, _ in _blocks(path)
        if info.lower() in SHELL_FENCES
    ]
    assert not fenced, (
        "both lanes read these files, so a block may not annotate one shell; "
        f"docs/agents/adr.md's bare fence is the form to use: {fenced}"
    )

    syntax = [
        (path.name, line, why)
        for path in WORKER_COMMANDS
        for line in _commands(path)
        for needle, why in NATIVE_ONLY
        if needle in line
    ]
    assert not syntax, f"these cannot run in the shell they are dispatched into: {syntax}"


def test_no_worker_command_names_the_interpreter_wsl_does_not_have() -> None:
    """``wsl -e bash -lc 'python -V'`` is command-not-found; ``python3`` is 3.12.3 (2026-08-03).

    The skill is not templated, so it names ``python3``, which resolves in both shells. The task
    templates *are* templated and take the launcher's per-lane ``{{PYTHON}}`` instead. This asserts
    on the committed text, never on a rendered task file.
    """
    bad = [
        (path.name, line)
        for path in WORKER_COMMANDS
        for line in _commands(path)
        if re.match(r"^python\b", line)
    ]
    assert not bad, f"use python3, or the templated {{{{PYTHON}}}} token: {bad}"


def test_the_shared_skill_never_hard_codes_one_vendor_lane() -> None:
    """The skill said ``--vendor claude`` twice, in a file the Codex lane loads verbatim.

    The ``.claude/skills/`` pointer exists precisely so both lanes read ONE text. A hard-coded
    vendor in it tells a codex worker to mirror its claim under the wrong ``agent:*`` label, and
    that label is the only public record of which lane holds an issue.
    """
    bad = [
        (path.name, line)
        for path in WORKER_COMMANDS
        for line in _commands(path)
        if re.search(r"--vendor\s+(claude|codex|copilot)\b", line)
    ]
    assert not bad, f"a shared instruction must not name one lane: {bad}"


def test_the_arming_command_survives_and_resolves_gh_from_path() -> None:
    """Asserted present *before* asserted correct: deleting it would also make the greps green.

    ``gh`` is on PATH in both lanes — ``/usr/bin/gh`` 2.45.0 under WSL, which supports
    ``--match-head-commit``, and the native CLI on Windows — so the bare name is the portable form.
    """
    fenced = [line for _, body in _blocks(CODEX_SKILL) for line in body]
    arming = [line for line in fenced if "pr merge" in line]
    assert arming, "the skill must still carry the one arming instruction in this repository"
    assert all(line.startswith("gh pr merge") for line in arming), arming
    assert all("--match-head-commit" in line for line in arming), (
        "there is no merge queue on this repository; that guard is what replaces it"
    )
