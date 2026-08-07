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

# The governance pages outside the dispatched-worker set that still carry commands an agent is
# expected to run. #382 removed a defect class from `CONTRACT_FILES`; nothing stopped it re-entering
# through one of these, and Codex found it doing exactly that on #386 — `docs/agents/adr.md` told a
# native-lane worker to run `python3`, on the page the Codex lane is *required* to read before
# adding an ADR. That instance was fixed there; this set is what stops the next one (#390).
SHARED_PAGES = [
    _REPO / "AGENTS.md",
    _REPO / "CLAUDE.md",
    _REPO / "CONTRIBUTING.md",
    # The PR template is prose an agent READS AND FOLLOWS while filling the lane state, so a command
    # in it is as binding as one on a docs page — and it carried `python3 .agents/bin/
    # greptile_usage.py`, the balance check a native-lane worker is told to run before spending a
    # metered credit. Left out of the first draft of this set because it is a template rather than a
    # page; Codex found the defect sitting in it (#390).
    _REPO / ".github" / "pull_request_template.md",
    *sorted((_REPO / "docs" / "agents").glob("*.md")),
]
GUARDED_FILES = [*CONTRACT_FILES, *SHARED_PAGES]

# `CONTRIBUTING.md` is guarded, but THREE of the rules below do not reach it, and this is the
# exception the issue asks be *stated* rather than silently skipped (#390, third criterion). Named
# individually, because an exception stated as a number is the kind that drifts — this comment said
# "two" while the set had grown to three, and CodeRabbit caught it:
#
#   1. `test_no_fence_asserts_a_shell_the_worker_may_not_be_in`, which reads `WORKER_FACING`.
#   2. `test_a_shell_specific_gate_is_given_for_both_lanes`, and
#   3. `test_a_page_both_lanes_read_resolves_the_interpreter_instead_of_naming_one`, both of which
#      read `BOTH_LANES` — and `BOTH_LANES` is derived from `WORKER_FACING`, so excluding a page
#      here excludes it from all three at once.
#
# The second of those is the one whose exclusion is load-bearing rather than merely harmless: the
# page carries `QT_QPA_PLATFORM=offscreen pytest ...` with no PowerShell counterpart, which the
# pairing rule would otherwise reject outright.
#
# The other pages instruct an agent that was dispatched into a shell it did not choose, running an
# interpreter it did not choose. `CONTRIBUTING.md` addresses a human setting up a development
# environment, who chooses both. Its ```bash blocks are therefore a *true* annotation rather than a
# false one — that POSIX inline env-var prefix is one PowerShell genuinely cannot parse, so the page
# names the shell it means. Relabelling those `sh` would make the file less accurate, and requiring
# `<py>` of a human reader would make it unusable. What the three rules exist to catch is an
# annotation or an interpreter that is wrong *for a reader who had no say*, and that reader is not
# this page's audience.
HUMAN_FACING = frozenset({_REPO / "CONTRIBUTING.md"})
WORKER_FACING = [path for path in GUARDED_FILES if path not in HUMAN_FACING]

# Which lanes read a page decides what it may name. A page both lanes read may not spell either
# lane's interpreter, because one of the two spellings is always wrong there; a lane's own
# adaptation may spell its own. `CLAUDE.md` is that adaptation — `AGENTS.md` is authoritative and it
# exists because Claude Code loads it instead — and `.claude/skills/` is its skill pointer.
LANE_SPECIFIC = frozenset({CLAUDE_SKILL, _REPO / "CLAUDE.md"})
BOTH_LANES = [path for path in WORKER_FACING if path not in LANE_SPECIFIC]

# `<py>` is only a convention if the page says what it stands for. Both existing definitions read
# "`<py>` is your lane's interpreter", wrapped across a line break in `AGENTS.md`, so the window is
# generous and the match is on the pairing rather than on either word alone.
_PY_DEFINED = re.compile(r"`<py>`[^`]{0,80}\binterpreter\b", re.DOTALL)

# `<SHA>` is the same shape of problem as `<py>` and gets the same shape of guard: a placeholder
# nothing substitutes, which is only a convention where the page says what to put there. Same
# generous non-backtick window, matched on the pairing rather than on either token alone.
#
# BOTH halves are required, and the width alone is not enough. A page saying only "supply a 40-hex
# SHA" satisfies a reader who then supplies the head re-read from the pull request while arming —
# which is 40 hex characters, and is the exact value that makes `--match-head-commit` compare the
# head against itself. The width is checkable; the SOURCE is what makes it a binding. So the window
# must also reach the review the value comes from.
_SHA_DEFINED = re.compile(r"`<SHA>`[^`]{0,80}40-hex[^`]{0,60}\breview\b", re.DOTALL)

#: The flag `<SHA>` is supplied to. It stands in for the merge queue this repository cannot have.
_MERGE_BINDING_FLAG = "--match-head-commit"

_SLOTS = _REPO / ".agents" / "bin" / "swarm_slots.py"

#: `gh` spellings that do not work on every `gh` a lane resolves, mapped to what to use instead.
#:
#: A DENYLIST of things measured broken, not a capability table. The full matrix of subcommand ×
#: version is unmaintainable and would rot into a second source of truth about a tool nobody here
#: controls; this is the short list of what has actually bitten, and each entry earns its place by
#: having cost a real failure. `swarm_slots.LANE_GH` is a bare `gh`, and
#: `test_the_launcher_injects_a_bare_name_for_every_lane` forbids it carrying arguments — so a
#: version cannot be pinned at the call site and the page has to carry the rule instead.
#:
#: Measured 2026-08-07: WSL resolves the apt package at 2.45.0, native resolves 2.95.0, and the
#: scripts are documented to run under `python3` - the WSL interpreter - so they get the older one.
UNPORTABLE_GH = {
    "--slurp": (
        "`gh api --slurp` was added after gh 2.45.0, which the WSL lane resolves. `--paginate` "
        "already concatenates an array endpoint's pages, so drop the flag (#417)."
    ),
    "pr edit": (
        "`gh pr edit` queries the sunset `projectCards` field on gh 2.45.0 and fails for every "
        "flag. Use `gh api -X PATCH repos/{owner}/{repo}/pulls/<PR>` instead (#418)."
    ),
}

# Absolute-path spellings that pin a command to one machine or one shell. `/mnt/` is the WSL view of
# a Windows drive and is just as unportable as `C:\` — it is meaningless natively.
ABSOLUTE = ("C:\\", "c:\\", "/mnt/", "%APPDATA%", "Program Files", "$env:", "~/")

# A line inside a fence is a command; outside one, a backtick span is a command only if it looks
# like an invocation rather than a filename or a label. Naming the tools this repository actually
# tells a worker to run keeps prose like `agent/issue-<N>` out of the sample.
_TOOLS = ("python", "python3", "gh", "git", "pytest", "mkdocs", "reuse", "pre-commit", "ssh")

# A resolver standing in for the interpreter: `<py>` for a reader to substitute as it goes,
# `{{PYTHON}}`/`{{GH}}` for the launcher to render into a task template. Both are matched with a
# LOOKAHEAD rather than `\b`, because these tokens end in a non-word character — `>` and `}` — so
# `\b` never holds between them and the following space.
#
# That is not a nicety. It meant the resolver branch had never matched anything: `{{PYTHON}}`
# commands were reaching the sample only through the `.agents/bin/` branch below, and the moment
# `docs/agents/gates.md` was corrected to `<py> scripts/dump_schema.py --check` — a script outside
# `.agents/bin/` — that line dropped out of `_commands` entirely. Widening the guard would then have
# stopped checking a required gate's command, and every rule below would have passed it vacuously.
# Codex found it on #390.
_RESOLVERS = (r"<py>", r"\{\{PYTHON\}\}", r"\{\{GH\}\}")

# A command may set an environment variable before naming its tool, and that prefix is the one
# construct with **no** shell-neutral spelling: `VAR=value cmd` is POSIX-only and PowerShell needs
# `$env:VAR='value'; cmd`. Recognising it is what puts such a command inside the sample at all —
# without this the offscreen-Qt test matrix, a *required* local gate, was invisible to every rule
# below and could carry a typo indefinitely (Codex P2 on #390).
_ENV_PREFIX = re.compile(r"^(?:\$env:\w+\s*=\s*[^;\s]+\s*;\s*|\w+=[^\s=]*\s+)+")
_INVOCATION = re.compile(
    r"^(?:" + "|".join(_RESOLVERS) + r")(?=\s|$)"
    r"|^(?:" + "|".join(_TOOLS) + r")\b"
    r"|(?<![\w/])\.agents/bin/\S+\.py\b"
)


def _declares_its_shell(command: str) -> bool:
    """Whether this command opens with an env-var prefix, which commits it to one shell openly."""
    return bool(_ENV_PREFIX.match(command))


def _gate_body(command: str) -> str:
    """The command with its env prefix removed — what the two lane spellings must agree on.

    The rules below run on THIS rather than skipping an env-prefixed command, which an earlier
    revision did. Exempting let `QT_QPA_PLATFORM=offscreen python -m pytest` pass with a hard-coded
    interpreter on a shared page — the exact WSL/native failure `<py>` exists to prevent, waved
    through by the check meant to catch it (Codex P2 on #390). The prefix is the shell-specific
    part; everything after it still owes shell-neutrality.
    """
    return _ENV_PREFIX.sub("", command).strip()


def _first_token(command: str) -> str:
    """The executable a shell must resolve, with any environment prefix removed.

    Every rule that asks *what does this command run* goes through here, and that is the point.
    Normalisation used to be opt-in at each call site, so a rule could read `command.split()[0]` —
    which for `VAR=value cmd` is the **assignment** — and silently govern nothing. That defect was
    found three separate times on #390, in three different rules, because fixing the instance never
    fixed the class. One helper, and `test_every_first_token_rule_sees_past_an_env_prefix` asserts
    the rules actually use it.

    Empty when the command is nothing but a prefix: `_ENV_PREFIX`'s PowerShell branch ends `;\\s*`,
    so `$env:X=1;` reduces to `""`. Callers must treat that as *no executable named* rather than
    indexing it.
    """
    parts = _gate_body(command).split()
    return parts[0] if parts else ""


_PS_ASSIGNMENT = re.compile(r"\$env:(\w+)\s*=\s*([^;\s]+)")
_POSIX_ASSIGNMENT = re.compile(r"(\w+)=(\S*)")
_QUOTES = "\"'"


def _assignment(name: str, value: str) -> str:
    return f"{name}={value.strip(_QUOTES)}"


def _gate_env(command: str) -> frozenset[str]:
    """The environment a gate sets, normalised across the two spellings it has to be written in.

    Pairing on the command body alone was not enough. `$env:QT_QPA_PLATFRM='offscreen'; pytest …`
    and `QT_QPA_PLATFORM=offscreen pytest …` share a body, so the typo paired happily and one lane
    ran the gate **without the offscreen platform** — a required gate with different semantics per
    lane, which is the defect one level in from the one the pairing already catches (Codex P2 on
    #390). The variable is half the gate, so it is half the key.

    Quotes are stripped because only PowerShell needs them: `'offscreen'` and `offscreen` are the
    same value, and keying on the quoting would report every correctly paired gate as unpaired.
    """
    prefix = _ENV_PREFIX.match(command)
    if not prefix:
        return frozenset()
    text = prefix.group(0)
    pairs = {_assignment(name, value) for name, value in _PS_ASSIGNMENT.findall(text)}
    # Whatever is left once the PowerShell form is removed is the POSIX form. Matching POSIX first
    # would swallow `env:QT_QPA_PLATFORM='offscreen'` as a name/value pair of its own.
    remainder = _PS_ASSIGNMENT.sub("", text)
    pairs |= {_assignment(name, value) for name, value in _POSIX_ASSIGNMENT.findall(remainder)}
    return frozenset(pairs)


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
    # Inline spans are matched over the whole file rather than line by line, because a long command
    # WRAPS. `docs/agents/hpc.md`'s first-use SSH probe — the fail-closed check that gates every
    # cluster action — spans four lines, so a per-line scan never saw it and no rule below applied
    # to it (Codex P2 on #390). Fenced lines are blanked first so they are not counted twice, and
    # each span's internal whitespace is collapsed to give the single command a reader sees.
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    unfenced, inside = [], False
    for line in lines:
        if re.match(r"^\s*```", line):
            inside = not inside
            unfenced.append("")
        else:
            unfenced.append("" if inside else line)
    joined = "\n".join(unfenced)
    for match in re.finditer(r"`([^`]+)`", joined, re.DOTALL):
        number = joined.count("\n", 0, match.start()) + 1
        span = " ".join(match.group(1).split())
        if _INVOCATION.search(_ENV_PREFIX.sub("", span)):
            found.append((number, span))
    return found


def _at(path: Path, number: int, command: str) -> str:
    return f"{path.relative_to(_REPO).as_posix()}:{number}: {command}"


def test_the_sample_is_not_empty() -> None:
    """A parse that finds nothing would make every assertion below pass vacuously."""
    counts = {p.name: len(_commands(p)) for p in GUARDED_FILES}
    assert len(_commands(CODEX_SKILL)) >= 6, (
        f"the worker skill should carry the claim/check/release/reserve/arm/scope-hash "
        f"commands; found {counts}"
    )
    assert all(_commands(t) for t in TASKS), f"a task template names no command at all: {counts}"

    # Named individually rather than asserted over the whole set: `docs/agents/evidence.md` and
    # `docs/agents/tools.md` are routing pages that carry no commands at all, and requiring one of
    # every page would only invite a decorative command to satisfy the test.
    for page in (_REPO / "AGENTS.md", _REPO / "CONTRIBUTING.md", _REPO / "docs/agents/adr.md"):
        assert _commands(page), f"{page.name} carries no command; the widened guard reads nothing"

    # The cluster probe specifically, because it is the fail-closed check gating every HPC action
    # and it is written as a backtick span WRAPPED across four lines. A per-line scan could not see
    # it, so no rule applied to it at all (Codex P2 on #390). Named rather than left to the
    # page-is-not-empty assertion above, which `git archive <SHA>` satisfies on its own.
    hpc = [c for _n, c in _commands(_REPO / "docs/agents/hpc.md")]
    assert any(c.startswith("ssh ") and "BatchMode=yes" in c for c in hpc), (
        f"the first-use cluster probe must be inside the sample; hpc.md yielded {hpc}"
    )


def test_a_resolver_led_command_is_visible_to_the_parser() -> None:
    """A rule that cannot see the command it governs is worse than no rule.

    Codex's P2 on this PR. `_INVOCATION` accepted only a known tool name at the start or
    `.agents/bin/*.py` anywhere, so `<py> scripts/dump_schema.py --check` — the corrected form of a
    **required local gate** — matched neither and left `_commands` entirely. Converting a page to
    `<py>` would then have *reduced* what the widened guard checked on that line.

    Asserted on the pattern as well as on the page, so it holds for the next resolver-led command
    too, wherever its script lives.
    """
    missed = [
        command
        for command in (
            "<py> scripts/dump_schema.py --check",
            "<py> .agents/bin/claim.py reserve-adr",
            "{{PYTHON}} .agents/bin/claim.py check --issue 7 --generation 5",
            "{{GH}} pr merge 7 --auto --squash",
        )
        if not _INVOCATION.search(command)
    ]
    assert not missed, f"no rule below applies to a command the parser cannot see: {missed}"

    gates = _commands(_REPO / "docs" / "agents" / "gates.md")
    assert any("dump_schema.py" in command for _number, command in gates), (
        "the schema gate's command is a required local gate and must be inside the sample"
    )


def test_a_shell_specific_gate_is_given_for_both_lanes() -> None:
    """The one command with no neutral spelling owes each lane its own, on a page both must read.

    `VAR=value cmd` is POSIX-only and PowerShell needs `$env:VAR='value'; cmd`, so the offscreen-Qt
    test matrix cannot be written once — which is precisely why it must be written twice. Giving
    only the PowerShell form left the `claude` lane, dispatched into WSL bash, with no runnable
    version of a **required** local gate, on the page whose whole subject is the required gates.

    The exemption in `_declares_its_shell` is what makes this assertion the substitute for the
    first-token rule rather than a hole beside it.
    """
    for path in BOTH_LANES:
        # Paired by the command BODY **and the environment it sets**, not counted per page. Counting
        # proved only that the page held one of each somewhere, so keeping the bash pytest matrix
        # while changing the PowerShell line to something unrelated still passed. Keying on the body
        # alone then left the variable free: a typo in one lane's `QT_QPA_PLATFORM` paired against
        # the other's correct spelling and ran a required gate with different semantics. Both were
        # Codex P2s on #390, one round apart.
        gates: dict[tuple[frozenset[str], str], set[str]] = {}
        for _number, command in _commands(path):
            if _declares_its_shell(command):
                shell = "powershell" if command.startswith("$env:") else "posix"
                gates.setdefault((_gate_env(command), _gate_body(command)), set()).add(shell)
        unpaired = {
            f"{' '.join(sorted(env))} {body}".strip(): sorted(shells)
            for (env, body), shells in gates.items()
            if len(shells) < 2
        }
        assert not unpaired, (
            f"{path.relative_to(_REPO).as_posix()} gives these environment-prefixed gates for only "
            f"one shell, so the other lane cannot run them: {unpaired}"
        )


def test_the_two_lane_spellings_of_a_gate_are_paired_by_their_variable_too() -> None:
    """What `_gate_env` is for, asserted on the pairing key rather than on today's pages.

    The rule above can only be as strong as its key. Keyed on the body alone, a gate whose variable
    was mistyped in one lane paired against the other's correct spelling: same command, same page,
    both shells present, test green — and one lane running the Qt matrix **without** the offscreen
    platform. That is a required gate silently meaning two different things, which is worse than the
    missing-lane case the pairing already catches, because nothing about it looks wrong.

    Stated here rather than left to the pages: no page currently carries the typo, so a
    corpus-driven assertion would pass against a key that ignored the variable entirely.
    """
    posix = "QT_QPA_PLATFORM=offscreen pytest -m 'not large'"
    powershell = "$env:QT_QPA_PLATFORM='offscreen'; pytest -m 'not large'"
    typo = "$env:QT_QPA_PLATFRM='offscreen'; pytest -m 'not large'"
    missing = "pytest -m 'not large'"

    assert _gate_env(posix) == _gate_env(powershell), (
        "the two spellings of one gate must give one key, or every correct pair reads as unpaired"
    )
    assert _gate_body(posix) == _gate_body(powershell) == _gate_body(missing)
    assert _gate_env(typo) != _gate_env(posix), "a mistyped variable must not pair"
    assert _gate_env(missing) == frozenset(), "and no prefix at all is not a gate of this kind"


def test_a_command_that_is_only_an_env_assignment_does_not_crash_the_guard() -> None:
    """CodeRabbit on #390: the guard must report, not raise, on the shape that empties out.

    `_ENV_PREFIX`'s PowerShell branch ends `;\\s*`, so it can consume a whole command. No page
    carries one today, and `SHARED_PAGES` globs `docs/agents/*.md`, so a new page can add one — at
    which point the first-token rule would die with `IndexError` rather than assert. A guard that
    crashes is worse than one that passes vacuously: the vacuous one at least reports green
    honestly, while this one reports a failure that says nothing about the contract.

    Asserted on the helpers rather than on a fixture page, since the point is that no page needs to
    carry the shape for the rule to be safe against it.
    """
    only_assignment = "$env:TETHER_ALLOW_NONSTRICT_X509=1;"
    assert _gate_body(only_assignment) == "", "the prefix really does consume the whole command"
    assert _gate_body(only_assignment).split() == [], "so indexing [0] would raise"

    # The rule's own comprehension, run over the pathological input.
    assert not [
        command
        for command in (only_assignment,)
        if (token := _first_token(command)) and re.search(r"[/\\:%$]", token)
    ]


def test_every_first_token_rule_sees_past_an_env_prefix() -> None:
    """The class, not the instance — found three times on #390 before it was fixed as one.

    Each rule below asks *what does this command run*, and each one independently read
    `command.split()[0]`, which for `VAR=value cmd` is the **assignment**. Fixing them one at a time
    kept working and kept missing siblings: the interpreter rule, then the `<py>`-definition rule,
    then the call-operator and lane-specific rules a round later. The shared cause is that
    normalisation was opt-in per call site.

    So it is now `_first_token`, and this asserts the rules actually reach through a prefix rather
    than that some particular line calls the helper. A rule that regresses to raw `.split()[0]`
    fails here even if it is a rule written after this test.
    """
    prefixed = "QT_QPA_PLATFORM=offscreen python -m pytest"
    powershell = "$env:QT_QPA_PLATFORM='offscreen'; & gh.exe pr merge"
    absolute = "QT_QPA_PLATFORM=offscreen /mnt/c/Python/python.exe -m pytest"
    prefixed_gh = "GH_TOKEN=redacted gh pr edit 1 --body-file body.md"

    assert _first_token(prefixed) == "python", "the executable, not the assignment"
    assert _first_token(powershell) == "&", "the call operator is the first token once stripped"
    assert _first_token("$env:TETHER_ALLOW_NONSTRICT_X509=1;") == "", "prefix-only names nothing"

    # Each rule's own predicate, run over a command that hides its defect behind a prefix.
    assert _gate_body(powershell).startswith("&"), "the call-operator rule must fire"
    assert _first_token(prefixed) in ("python", "python3"), "the interpreter rule must fire"
    assert any(marker in _gate_body(absolute) for marker in ABSOLUTE), "the path rule must fire"
    assert re.search(r"[/\\:%$]", _first_token(absolute)), "the bare-name rule must fire"

    # The `gh`-portability rule (#418), added after this test was written — which is the case its
    # docstring promises to cover and did not, because the list above is enumerated by hand.
    # CodeRabbit read the rule as broken on a prefixed command (`Major` on #422); it is not, because
    # it goes through `_first_token` like the others. What was missing is this line, so that a
    # regression to raw `.split()[0]` fails here rather than silently governing nothing.
    assert _first_token(prefixed_gh) == "gh", "the gh-portability rule must fire"
    assert any(spelling in _gate_body(prefixed_gh) for spelling in UNPORTABLE_GH), (
        "and must still see the unportable spelling once the prefix is stripped"
    )


def test_no_command_names_an_absolute_path() -> None:
    """The defect that could not arm auto-merge.

    `C:\\Program Files\\GitHub CLI\\gh.exe` is not a thing inside WSL, and `/mnt/c/...` is not a
    thing natively. Either spelling picks one lane and breaks the other.
    """
    bad = [
        _at(path, number, command)
        for path in GUARDED_FILES
        for number, command in _commands(path)
        if any(marker in _gate_body(command) for marker in ABSOLUTE)
    ]
    assert not bad, (
        "these commands hard-code a path that does not exist in the other lane's shell; "
        f"resolve the tool from PATH instead: {bad}"
    )


def test_no_command_begins_with_the_powershell_call_operator() -> None:
    """`&` is how PowerShell invokes a quoted path. bash reads it as a syntax error."""
    bad = [
        _at(path, number, command)
        for path in GUARDED_FILES
        for number, command in _commands(path)
        if _gate_body(command).startswith("&")
    ]
    assert not bad, f"these commands are PowerShell-only; drop the call operator: {bad}"


def test_every_interpreter_and_cli_reference_is_a_bare_resolvable_name() -> None:
    """The positive form of the rule the two tests above state negatively.

    The first token of a command is the thing the shell has to resolve. A bare name resolves from
    `PATH` in bash and PowerShell alike; anything carrying a separator, a drive letter or an
    environment-variable expansion has already chosen a lane.
    """
    # `_gate_body`, and the emptiness guard is not defensive padding. `_ENV_PREFIX`'s PowerShell
    # branch ends `;\s*`, so it can consume a whole command: a fenced line reading
    # `$env:TETHER_ALLOW_NONSTRICT_X509=1;` strips to `""`, and `"".split()[0]` is an `IndexError`.
    # The fence branch of `_commands` takes any non-empty non-`#` line, so nothing filters that
    # shape out, and `SHARED_PAGES` globs `docs/agents/*.md` — a new page can introduce one. The
    # guard would then die with an opaque traceback instead of reporting what it found, which is a
    # worse failure than the one it exists to catch (CodeRabbit on #390).
    bad = [
        _at(path, number, command)
        for path in GUARDED_FILES
        for number, command in _commands(path)
        if (token := _first_token(command)) and re.search(r"[/\\:%$]", token)
    ]
    assert not bad, f"these commands lead with something other than a bare executable name: {bad}"


def test_no_fence_asserts_a_shell_the_worker_may_not_be_in() -> None:
    """The annotation is a claim about the reader's shell, and it was the wrong claim.

    A ```` ```powershell ```` block dispatched into bash is the defect #382 exists to remove — and
    it is the part a reader believes before running anything.

    `WORKER_FACING`, not every guarded page: see `HUMAN_FACING` for why `CONTRIBUTING.md` is the one
    place a shell-specific annotation is the honest one.
    """
    bad = [
        f"{path.relative_to(_REPO).as_posix()}:{number}: ```{language}"
        for path in WORKER_FACING
        for number, language, _body in _fences(path)
        if language in _SHELL_SPECIFIC
    ]
    assert not bad, (
        "these fences name a shell the dispatched worker may not have; the commands are "
        f"shell-neutral, so annotate them `sh`: {bad}"
    )


def test_a_page_both_lanes_read_resolves_the_interpreter_instead_of_naming_one() -> None:
    """The rule #386 established, enforced everywhere it applies rather than where it was found.

    `python3` and `python` are each wrong in exactly one of the two shells — Ubuntu ships no
    `python`, and on Windows `python3` may be an unconfigured Store stub — so on a page **both**
    lanes read, either spelling is a command one lane cannot run. `docs/agents/adr.md` was that page
    (#386): it told a native-lane worker to run `python3` to reserve an ADR number, on a page the
    Codex lane is required to read before adding a record. Fixing that instance left the class free
    to re-enter anywhere the old `CONTRACT_FILES` guard did not read (#390).

    A resolver is required instead: `<py>` for a page a human or agent substitutes as it reads, or
    `{{PYTHON}}` for a task template the launcher renders. Neither names a lane.

    The rule is stated over the *first token* rather than over `.agents/bin/*.py` alone, because the
    defect is the interpreter, not the script that follows it: `python scripts/dump_schema.py` on a
    shared page strands the same lane for the same reason.

    A bare `` `python3` `` with nothing after it is a **mention**, not a command — it is how a page
    says what `<py>` stands for, and how §Shell's table names the lanes. Requiring an argument is
    what separates the two; without it this rule would forbid the definitions it depends on.
    """
    bad: list[str] = []
    for path in BOTH_LANES:
        for number, command in _commands(path):
            # `_gate_body`, not `command`: the first token of an env-prefixed command is the
            # ASSIGNMENT, so reading `parts[0]` here let `QT_QPA_PLATFORM=offscreen python -m
            # pytest ...` name a lane's interpreter on a shared page and pass — the very failure
            # `<py>` exists to prevent, waved through by the rule that governs it (Codex P2 on
            # #390). The prefix is the shell-specific part and is paired for both lanes above;
            # what follows it still owes lane-neutrality.
            parts = _gate_body(command).split()
            if _first_token(command) in ("python", "python3") and len(parts) > 1:
                bad.append(_at(path, number, command))
    assert not bad, (
        "these commands name one lane's interpreter on a page both lanes read; one of the two "
        f"shells cannot run them. Use `<py>` (or `{{{{PYTHON}}}}` in a rendered template): {bad}"
    )

    # `_gate_body` here too. Reading the raw command repeats the exact blindness the loop above was
    # just fixed for: the first token of `QT_QPA_PLATFORM=offscreen <py> scripts/dump_schema.py` is
    # the assignment, so the page would use the convention without ever being asked to define it —
    # which turns `<py>` back into a typo (CodeRabbit on #390).
    undefined = [
        path.relative_to(_REPO).as_posix()
        for path in BOTH_LANES
        if any(_gate_body(command).startswith("<py>") for _number, command in _commands(path))
        and not _PY_DEFINED.search(path.read_text(encoding="utf-8"))
    ]
    assert not undefined, (
        "these pages use `<py>` without saying what it stands for, which makes it a typo rather "
        f"than a convention; state that it is the lane's interpreter: {undefined}"
    )


def test_a_lane_specific_page_may_name_its_own_interpreter() -> None:
    """The distinction the rule above rests on, asserted rather than assumed (#390).

    `CLAUDE.md` is the Claude adaptation — `AGENTS.md` is authoritative, and this copy exists only
    because Claude Code loads `CLAUDE.md` and not `AGENTS.md`. Its reader is always the WSL lane, so
    `python3` there is *correct*, and banning the spelling repository-wide would force a resolver on
    the one file that has nothing to resolve. Pinned so a later tightening of the rule above cannot
    quietly swallow the exception.
    """
    claude_md = _REPO / "CLAUDE.md"
    assert claude_md in LANE_SPECIFIC and claude_md not in BOTH_LANES
    named = [
        command
        for _number, command in _commands(claude_md)
        if _first_token(command) in ("python", "python3")
    ]
    assert named, (
        "CLAUDE.md no longer names its own lane's interpreter. That is allowed, but the exception "
        "is now untested — either drop it from LANE_SPECIFIC or restore a command that uses it"
    )
    lane_python = _load_slots().LANE_PYTHON["claude"]
    wrong = [command for command in named if _first_token(command) != lane_python]
    assert not wrong, (
        f"CLAUDE.md may name its own lane's interpreter, which is {lane_python!r} per LANE_PYTHON; "
        f"these name a different one: {wrong}"
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
                token = _first_token(command)
                if any(m in _gate_body(command) for m in ABSOLUTE) or (
                    token and re.search(r"[/\\:%$]", token)
                ):
                    bad.append(f"{task.name} [{vendor}]: {command}")
    assert not bad, f"a rendered task hands its worker an unrunnable command: {bad}"


#: The provider triggers. `docs/agents/review.md` states the rule these fire under: a mention fires
#: the bot **even inside backticks**, because a code span is not an escape.
PROVIDER_HANDLES = ("@coderabbitai", "@greptileai", "@codex", "@copilot")


def test_no_page_both_lanes_read_names_a_gh_spelling_one_of_them_cannot_run() -> None:
    """#418: `gh` resolves in both shells — to different builds, and two commands break on 2.45.

    This is the interpreter rule's other half. `<py>` exists because the two lanes need *different
    names*; `gh` needs the same name everywhere, so no token was ever added — and that was read as
    "`gh` needs no rule", which `.agents/skills/tether-worker/SKILL.md` said in as many words. The
    name resolving is not the same property as the command working. WSL resolves the apt package
    (2.45.0 here), native resolves 2.95.0, and every `.agents/bin/*.py` is documented to run under
    `python3`, so the scripts get the older one.

    Asserted as a **denylist of measured breakages**, not a version matrix. A capability table for a
    tool this repository does not control would rot into a second source of truth, and the honest
    unit is "this spelling cost us a real failure". Both entries did: `--slurp` made the Greptile
    balance unreadable, so the lane's own precondition for spending a credit could not be met, and
    `pr edit` broke the PR-body handoff that `.agents/tasks/build.md` calls the only thing carrying
    the lane forward.

    Scoped to `BOTH_LANES`, so `CLAUDE.md` — which is allowed to name one lane's tooling — is exempt
    for the same reason it may name `python3`.
    """
    bad: list[str] = []
    scanned = 0
    for path in BOTH_LANES:
        for number, command in _commands(path):
            body = _gate_body(command)
            if _first_token(command) != "gh" and "{{GH}}" not in body:
                continue
            scanned += 1
            bad += [
                f"{_at(path, number, command)} — {why}"
                for spelling, why in UNPORTABLE_GH.items()
                if spelling in body
            ]
    # A denylist over an empty sample is a green light that means nothing, and #421 is an open
    # finding about exactly that shape elsewhere in this file. The contract carries the merge
    # command, the triage dispatch and the PR-body write on these pages, so this floor is well
    # under the real count and only trips if the extractor stops seeing `gh` at all.
    assert scanned >= 5, f"only {scanned} gh commands were scanned; this guard is passing vacuously"
    assert not bad, (
        "these pages hand a worker a `gh` spelling one of its two lanes cannot run: "
        + "; ".join(bad)
    )


#: The issue forms GitHub posts. **`.md` as well as `.yml`** — GitHub supports and publishes both,
#: and none of the Markdown kind exists here today, so globbing only `*.yml` was latent rather than
#: broken (#421).
#:
#: `config.yml` is excluded **by name**: it is the issue-chooser configuration —
#: `blank_issues_enabled`, `contact_links` — and its contents never become the body of anything. It
#: carries no provider handle either, so including it changed no result; it was simply wrong about
#: what this set means, and the set is named for that meaning.
ISSUE_FORMS = sorted(
    path
    for suffix in ("*.yml", "*.yaml", "*.md")
    for path in (_REPO / ".github" / "ISSUE_TEMPLATE").glob(suffix)
    if path.name not in {"config.yml", "config.yaml"}
)

#: The templates GitHub **posts** rather than the pages an agent reads. Both become the body of a
#: real comment the moment someone opens a pull request or an issue from them.
POSTED_TEMPLATES = [_REPO / ".github" / "pull_request_template.md", *ISSUE_FORMS]


def test_no_posted_template_carries_a_provider_handle() -> None:
    """A handle in a template GitHub posts is a review request on every item opened from it.

    Every other guarded page is prose an agent *reads*. These are prose GitHub **publishes**:
    whatever they contain becomes a real comment, and a mention there fires the bot on something
    that is not ready for one — spending a fair-use review and throttling the pull request that was
    (`docs/agents/review.md`, measured 2026-08-03).

    **The rule is scoped to what is posted, and that scoping is the point.** `review.md` says its
    own page is where the trigger strings belong, but `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`
    and `.agents/tasks/build.md` all name a handle too — and none of them is a defect, because none
    of them is published as a comment. A guard over every page would fail on four files that are
    doing nothing wrong, and the fix would be to delete the exact command a worker needs. So the
    line is drawn where the harm is: a file whose contents GitHub turns into a comment.

    The templates have always been clean, and nothing said so. #400 added the CodeRabbit gate's
    evidence requirement to the pull-request template, which is exactly the edit that invites naming
    the command; it names it in prose — *"the full-review command"* — as `review.md` prescribes. The
    issue forms are here for the same reason and were never covered.

    Globbed, not listed: a hand-written tuple silently stops at the forms that existed when it was
    written, which is the failure `.agents/tasks/*.md` already had once (#387).

    **The emptiness guard counts the issue forms specifically (#421).** It used to read
    `len(POSTED_TEMPLATES) > 1` over the combined list, which `pull_request_template.md` plus the
    chooser `config.yml` satisfied on their own — so if every issue form were renamed, moved or
    converted to Markdown, the glob would have matched nothing real and this test would still have
    passed. That is the same vacuity the glob was written to prevent, reintroduced one level up.
    """
    assert len(ISSUE_FORMS) > 1, (
        "the issue-form glob matched nothing real; this guard is passing on the pull-request "
        "template alone, which is the vacuity the glob exists to prevent"
    )
    assert len(POSTED_TEMPLATES) == len(ISSUE_FORMS) + 1, "and the PR template is still in the set"
    bad: list[str] = []
    for path in POSTED_TEMPLATES:
        body = path.read_text(encoding="utf-8")
        bad += [
            f"{path.relative_to(_REPO).as_posix()}: {handle}"
            for handle in PROVIDER_HANDLES
            if handle in body
        ]
    assert not bad, (
        f"{bad} — a provider handle here fires a real review on everything opened from the "
        "template; name the command in prose instead"
    )


def test_a_page_that_arms_the_merge_says_what_sha_to_supply() -> None:
    """#400: a page that carries the merge-binding flag must say what `<SHA>` to supply.

    `<SHA>` is a placeholder nothing substitutes, so a page that omits the rule hands a worker a
    literal. `swarm_slots._render` consumes `{{...}}` tokens and refuses to dispatch when one
    survives. It has no opinion about angle brackets, so `<SHA>` reaches the worker exactly as
    written — on the one flag that stands in for the merge queue this repository cannot have. The
    failure is not that the merge breaks loudly: it is that a worker guesses, and the obvious guess
    is to re-read the head from the pull request while arming, which compares the head against
    itself, binds nothing, and still reads as protection.

    So this is the `<py>` rule applied to the other unsubstituted placeholder, and deliberately the
    same shape: **a page that carries the flag must also carry the rule.** Where `<py>` names a
    resolution table, this names a source — the `commit_id` on the clean review — and the guard
    requires **both** halves in one window: the 40-hex width, which a reader can check, and the
    review it comes from, which is what makes it a binding rather than a format. Requiring only the
    width would accept a page whose reader then supplies the head re-read from the pull request,
    since that value is also 40 hex characters and is precisely the one that binds nothing.

    Asserted over `GUARDED_FILES` rather than `BOTH_LANES`, because `CONTRIBUTING.md` carries the
    command too and a human following it can bind the wrong head just as easily. The pages
    themselves are allowed to point at `docs/agents/review.md` §Merge instead of restating it, and
    all of them do — the rule sentence is what is required here, not the reasoning behind it.
    """
    arming = [
        path
        for path in GUARDED_FILES
        if any(_MERGE_BINDING_FLAG in command for _number, command in _commands(path))
    ]
    assert len(arming) >= 5, (
        f"only {len(arming)} pages appear to arm the merge; the command extractor has stopped "
        "seeing them and this guard would pass vacuously"
    )
    undefined = [
        path.relative_to(_REPO).as_posix()
        for path in arming
        if not _SHA_DEFINED.search(path.read_text(encoding="utf-8"))
    ]
    assert not undefined, (
        "these pages hand a worker `<SHA>` without saying what to put there, so the placeholder "
        "reaches the merge command as a literal and the binding guard is a guess: "
        f"{undefined}. State that it is the 40-hex head the clean review read."
    )
