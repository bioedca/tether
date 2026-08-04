# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The commands the contract hands a worker must run in the shell it was dispatched into.

The launcher puts the lanes in **different shells** — `claude` inside WSL bash, `codex` and
`copilot` in native PowerShell (`swarm_slots._inner_command`) — but `SKILL.md` is not templated, so
both lanes read the same lines. Every command in it therefore has to be valid in either shell.

It was valid in neither reliably (#382). Six ```` ```powershell ```` blocks told a bash session to
run `python`, which Ubuntu does not provide under that name, and to arm auto-merge through
`& "C:\\Program Files\\GitHub CLI\\gh.exe"` — a call operator bash has no notion of, naming a path
that does not exist inside WSL. That arming line is the *only* one in the repository, so a worker
that could not run it left its pull request green, mergeable and unarmed: **#327** and **#334** both
had to be merged by hand.

Nothing read these files' bodies before. `test_agent_entry_points.py` checks that both lanes can
*reach* the contract; this module checks that what they reach is *runnable*. The property asserted
is deliberately shell-neutrality rather than "works on this machine": a bare executable name plus
arguments resolves from `PATH` in bash and PowerShell alike, while an absolute path or a `&` prefix
commits to exactly one of them.

Stdlib only, so it runs on the base 3-OS `test` matrix.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

CODEX_SKILL = _REPO / ".agents" / "skills" / "tether-worker" / "SKILL.md"
CLAUDE_SKILL = _REPO / ".claude" / "skills" / "tether-worker" / "SKILL.md"
TASKS = sorted((_REPO / ".agents" / "tasks").glob("*.md"))
CONTRACT_FILES = [CODEX_SKILL, CLAUDE_SKILL, *TASKS]

_SLOTS = _REPO / ".agents" / "bin" / "swarm_slots.py"

# Absolute-path spellings that pin a command to one machine or one shell. `/mnt/` is the WSL view of
# a Windows drive and is just as unportable as `C:\` — it is meaningless natively.
ABSOLUTE = ("C:\\", "c:\\", "/mnt/", "%APPDATA%", "Program Files", "$env:", "~/")

# A line inside a fence is a command; outside one, a backtick span is a command only if it looks
# like an invocation rather than a filename or a label. Naming the tools this repository actually
# tells a worker to run keeps prose like `agent/issue-<N>` out of the sample.
_TOOLS = ("python", "python3", "gh", "git", "pytest", "mkdocs", "reuse", "pre-commit")
_INVOCATION = re.compile(
    r"^(?:\{\{(?:PYTHON|GH)\}\}|" + "|".join(_TOOLS) + r")\b|(?<![\w/])\.agents/bin/\S+\.py\b"
)

# Fence languages that assert a shell the dispatched worker may not be in. `sh` is the honest
# annotation for a command written to be shell-neutral, and is deliberately absent.
#
# The POSIX names are here for the same reason the Windows ones are, which is the symmetry review
# caught: a ```bash fence is exactly as wrong for the Codex lane as ```powershell was for Claude's.
# Guarding only the spelling that happened to cause #382 would leave the mirrored defect free to
# land tomorrow.
_SHELL_SPECIFIC = frozenset(
    {
        "powershell",
        "pwsh",
        "ps1",
        "ps",
        "bat",
        "cmd",
        "batch",
        "bash",
        "zsh",
        "fish",
        "csh",
        "ksh",
        "shell-session",
        "console",
    }
)


def _load_slots():  # noqa: ANN202 - a module object; the launcher is not importable by name
    spec = importlib.util.spec_from_file_location("tether_slots_runnable", _SLOTS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fences(path: Path) -> list[tuple[int, str, list[str]]]:
    """Every fenced block as ``(line number of the opening fence, language, body lines)``."""
    blocks: list[tuple[int, str, list[str]]] = []
    opened: tuple[int, str] | None = None
    body: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fence = re.match(r"^\s*```(\S*)\s*$", line)
        if fence and opened is None:
            opened, body = (number, fence.group(1).lower()), []
        elif fence:
            blocks.append((opened[0], opened[1], body))
            opened = None
        elif opened is not None:
            body.append(line)
    return blocks


def _commands(path: Path) -> list[tuple[int, str]]:
    """Every command this file tells a worker to run, as ``(line number, command)``.

    Two sources, because the contract uses both: fenced blocks in `SKILL.md`, and inline backtick
    spans in the task templates, which are kept prose-shaped on purpose (they are resident context
    on every model call of the session they start).
    """
    found: list[tuple[int, str]] = []
    for start, _language, body in _fences(path):
        found += [
            (start + offset, line.strip())
            for offset, line in enumerate(body, start=1)
            if line.strip() and not line.lstrip().startswith("#")
        ]
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if re.match(r"^\s*```", line):
            continue
        found += [
            (number, span.strip())
            for span in re.findall(r"`([^`]+)`", line)
            if _INVOCATION.search(span.strip())
        ]
    return found


def _at(path: Path, number: int, command: str) -> str:
    return f"{path.relative_to(_REPO).as_posix()}:{number}: {command}"


def test_the_sample_is_not_empty() -> None:
    """A parse that finds nothing would make every assertion below pass vacuously."""
    counts = {p.name: len(_commands(p)) for p in CONTRACT_FILES}
    assert len(_commands(CODEX_SKILL)) >= 6, (
        f"the worker skill should carry the claim/check/release/reserve/arm/scope-hash "
        f"commands; found {counts}"
    )
    assert all(_commands(t) for t in TASKS), f"a task template names no command at all: {counts}"


def test_no_command_names_an_absolute_path() -> None:
    """The defect that could not arm auto-merge.

    `C:\\Program Files\\GitHub CLI\\gh.exe` is not a thing inside WSL, and `/mnt/c/...` is not a
    thing natively. Either spelling picks one lane and breaks the other.
    """
    bad = [
        _at(path, number, command)
        for path in CONTRACT_FILES
        for number, command in _commands(path)
        if any(marker in command for marker in ABSOLUTE)
    ]
    assert not bad, (
        "these commands hard-code a path that does not exist in the other lane's shell; "
        f"resolve the tool from PATH instead: {bad}"
    )


def test_no_command_begins_with_the_powershell_call_operator() -> None:
    """`&` is how PowerShell invokes a quoted path. bash reads it as a syntax error."""
    bad = [
        _at(path, number, command)
        for path in CONTRACT_FILES
        for number, command in _commands(path)
        if command.startswith("&")
    ]
    assert not bad, f"these commands are PowerShell-only; drop the call operator: {bad}"


def test_every_interpreter_and_cli_reference_is_a_bare_resolvable_name() -> None:
    """The positive form of the rule the two tests above state negatively.

    The first token of a command is the thing the shell has to resolve. A bare name resolves from
    `PATH` in bash and PowerShell alike; anything carrying a separator, a drive letter or an
    environment-variable expansion has already chosen a lane.
    """
    bad = [
        _at(path, number, command)
        for path in CONTRACT_FILES
        for number, command in _commands(path)
        if re.search(r"[/\\:%$]", command.split()[0])
    ]
    assert not bad, f"these commands lead with something other than a bare executable name: {bad}"


def test_no_fence_asserts_a_shell_the_worker_may_not_be_in() -> None:
    """The annotation is a claim about the reader's shell, and it was the wrong claim.

    A ```` ```powershell ```` block dispatched into bash is the defect #382 exists to remove — and
    it is the part a reader believes before running anything.
    """
    bad = [
        f"{path.relative_to(_REPO).as_posix()}:{number}: ```{language}"
        for path in CONTRACT_FILES
        for number, language, _body in _fences(path)
        if language in _SHELL_SPECIFIC
    ]
    assert not bad, (
        "these fences name a shell the dispatched worker may not have; the commands are "
        f"shell-neutral, so annotate them `sh`: {bad}"
    )


def test_the_skill_resolves_the_interpreter_instead_of_naming_one() -> None:
    """A bare `python3` in an untemplated file is the same defect mirrored, not a fix.

    Codex's P1 on #386: `LANE_PYTHON` selects `python` for the native lanes, so a skill that says
    only `python3` contradicts this very patch and strands a hand-driven claim, release, reserve or
    scope-hash on any machine where the native `python3` alias is absent. What the shared file owes
    the reader is a *rule*, so §Shell names both interpreters and which lane takes which.

    Asserted as **pairings**, not membership. Codex's follow-up P2: checking only that every lane
    name and every interpreter name appear *somewhere* in §Shell passes just as happily when the
    mappings are swapped, or when every lane is mapped to `python` — the section already mentions
    all four strings. The table and the launcher could then drift in exactly the way this test
    claims to prevent. So each row is parsed and matched against `LANE_PYTHON` directly.
    """
    shell = CODEX_SKILL.read_text(encoding="utf-8").partition("## Shell")[2].partition("\n## ")[0]
    assert shell.strip(), "the skill must carry a §Shell section"

    documented = {}
    for row in re.findall(r"^\|(.+)\|\s*$", shell, flags=re.MULTILINE):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) != 3 or not cells[0].startswith("`"):
            continue  # the header and its separator
        documented[cells[0].strip("`")] = cells[2].strip("`")

    slots = _load_slots()
    assert documented == slots.LANE_PYTHON, (
        f"§Shell's table says {documented}, the launcher renders {{PYTHON}} from "
        f"{slots.LANE_PYTHON}; a worker and its task text would disagree about the interpreter"
    )


def test_the_launcher_injects_a_bare_name_for_every_lane() -> None:
    """The templates delegate the interpreter to the launcher, so the launcher owes a bare name.

    `SKILL.md` cannot be templated — both lanes read it — but the task text *is*, and the launcher
    is the only party that knows which shell it is dispatching into. That makes `{{PYTHON}}` the
    resolver the issue asked for rather than a second hard-coded name; this asserts what it
    resolves to.
    """
    slots = _load_slots()
    names = [*slots.LANE_PYTHON.values(), slots.LANE_GH]
    bad = [name for name in names if re.search(r"[/\\:%$\s]", name)]
    assert not bad, f"the launcher injects something that is not a bare executable name: {bad}"


def test_the_interpreter_table_covers_every_lane_and_has_no_fallback() -> None:
    """A lane the table does not name has no interpreter, and must not inherit another lane's.

    `LANE_PYTHON` is indexed once per rendered task, so a missing vendor decides which interpreter a
    worker is told to run. Both available fallbacks are wrong for one of the two shells — `python3`
    is an unconfigured Store stub on Windows, `python` does not exist in Ubuntu — so a default here
    reinstates #382 for the lane it was added to protect. The launcher refuses instead (#387), and
    this asserts both halves: every vendor is present, and nothing absorbs the ones that are not.
    """
    slots = _load_slots()
    assert set(slots.LANE_PYTHON) == set(slots.claim.VENDORS), (
        f"LANE_PYTHON covers {sorted(slots.LANE_PYTHON)} but the vendors are "
        f"{sorted(slots.claim.VENDORS)}; a lane with no interpreter cannot be dispatched"
    )
    assert not hasattr(slots, "DEFAULT_PYTHON"), (
        "a default interpreter makes an unregistered lane render a task whose commands do not run, "
        "and it does so silently; `_lane_python` refuses instead"
    )


def test_a_rendered_task_is_runnable_in_the_lane_it_is_rendered_for() -> None:
    """End to end: what a worker actually receives, for every lane, must pass the same rules.

    The lanes are read from `claim.VENDORS` rather than listed here, so a fourth lane is covered the
    moment it is added. A hand-written tuple silently stops at three (#387).
    """
    slots = _load_slots()
    record = {"issue": 7, "branch": "agent/issue-7", "generation": 5, "base_sha": "a" * 40}
    bad: list[str] = []
    for task in TASKS:
        for vendor in slots.claim.VENDORS:
            item = {"vendor": vendor, "round": 1, "remaining": 1, "reason": "because"}
            for span in re.findall(r"`([^`]+)`", slots._render(task, record, item)):
                command = span.strip()
                if not _INVOCATION.search(command):
                    continue
                if any(m in command for m in ABSOLUTE) or re.search(
                    r"[/\\:%$]", command.split()[0]
                ):
                    bad.append(f"{task.name} [{vendor}]: {command}")
    assert not bad, f"a rendered task hands its worker an unrunnable command: {bad}"
