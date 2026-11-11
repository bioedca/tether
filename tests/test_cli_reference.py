# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The published CLI reference matches the parser it documents (PRD §9 M9 docs gate).

``docs/cli.md`` is the only user-facing description of the headless surface, and the
failure mode it guards against is silent: a flag added to :mod:`tether.cli` without a
docs edit leaves the page quietly incomplete, and a flag renamed or removed leaves the
page quietly *wrong*. Neither is visible to ``mkdocs build --strict`` — no link breaks
either way — and neither shows up in a diff that only touches ``src/``.

So the check runs in both directions, which is exactly what issue #166 asks for: every
option the parser defines is named on the page, and every flag token on the page —
``--out-dir`` and its ``-d`` alias alike — is an option the parser actually defines. It
reads the *live* parser rather than a transcript, so it cannot drift the way a copied
``--help`` block can.

Direction 1 is scoped to each subcommand's own section rather than to the whole page.
``--tmap``, ``--tdat`` and ``--overwrite`` exist on *both* parsers, so a page-wide check
would happily accept an ``extract``-only flag that had been documented under ``batch``
and never mentioned where a reader of ``tether extract`` would look. Direction 2 runs
page-wide *and* per section: the usage block and the option table of each subcommand may
name only that subcommand's own options, so a flag removed from one parser is found even
while the other parser still has it (#744).

The option tables also print a **Default** column, and every default argparse holds is
compared with its cell, so a changed argparse literal cannot leave the page behind (#744).

Base-matrix only: :func:`tether.cli.build_parser` imports nothing beyond ``argparse``
(the imaging/HDF5 stack is deliberately lazy so ``--version`` stays fast), and the page is
read with :mod:`pathlib`. The resume regression that reaches into
:mod:`tether.project` does pull that stack in transitively (the package ``__init__``
imports :mod:`tether.io`, and so h5py and numpy), but that stack is a locked
base-environment dependency rather than an optional extra — nothing here needs Qt,
torch or the sidecar, so this runs on all three OSes.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from tether.cli import build_parser, main

PAGE = Path(__file__).resolve().parents[1] / "docs" / "cli.md"

# Counted in issue #166 from the `add_argument` calls, and asserted here so that adding
# an option without touching the page fails loudly rather than silently under-documenting
# it. Positionals (`movie`, `movies`) are checked separately below.
_EXPECTED_OPTION_COUNTS = {"extract": 14, "batch": 11}

# `-h/--help` is argparse boilerplate on every parser and is deliberately not documented.
_UNDOCUMENTED = frozenset({"-h", "--help"})

# A long-form flag as it appears in prose, a table cell or a fenced block.
_FLAG_RE = re.compile(r"--[a-zA-Z][a-zA-Z0-9-]*")

# A short alias (`-o`, `-d`) in the same places. The lookarounds keep it off the tail of a
# hyphenated word and out of the middle of a long flag, so `--donor-side` does not read as a
# documented `-d`. Nothing scopes the match to a `tether` command line, so the assumption is
# about the page as it stands — every short flag on it today is a tether option, on a
# `tether ...` usage or example line or in the option-table row documenting it — not an
# invariant the page enforces. A non-tether short flag (`ls -l`, or the `-m` in
# `python -m tether`) would be reported by `test_the_page_invents_no_flags` as an invented
# tether flag; if the page ever grows one, scope *that* test's extraction rather than loosening
# the regex. The discriminator is the line's command word, not whether it invokes tether:
# `python -m tether extract ...` does invoke tether (that is what `tether.__main__` is for, and
# ADR-0049 recommends it on Windows), yet its `-m` belongs to `python`, not to a tether parser.
_SHORT_FLAG_RE = re.compile(r"(?<![\w-])-[a-zA-Z](?![\w-])")


def _documented_flags(text: str) -> set[str]:
    """Every option token — long or short — spelled out in ``text``.

    Matching whole tokens rather than substrings is what makes the short aliases count:
    ``"-d" in "--donor-side"`` is true, so a plain ``in`` test can never tell a documented
    ``-d`` from an incidental one.

    The flip side of tokenising is that only the spellings tether actually uses round-trip.
    ``_FLAG_RE`` wants a letter after the ``--`` and then letters, digits and hyphens only,
    so a hypothetical ``--out_dir`` tokenises as ``--out`` (reported missing *and* invented
    from a correctly written page) and ``--2color`` tokenises as nothing at all. No such
    option exists; adding one means widening ``_FLAG_RE`` in the same commit.
    """
    return set(_FLAG_RE.findall(text)) | set(_SHORT_FLAG_RE.findall(text))


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """The registered subcommands, by name.

    argparse exposes no public accessor for this, so reach through the
    ``_SubParsersAction`` the way the stdlib's own tests do.
    """
    for action in parser._actions:  # noqa: SLF001 - no public API for subparser lookup
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    raise AssertionError("tether's top-level parser registers no subcommands")


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    """Every option string on ``parser``, ``-h``/``--help`` excluded."""
    return {
        opt
        for action in parser._actions  # noqa: SLF001 - mirrors _subparsers above
        for opt in action.option_strings
    } - set(_UNDOCUMENTED)


def _positionals(parser: argparse.ArgumentParser) -> set[str]:
    return {
        action.dest
        for action in parser._actions  # noqa: SLF001
        if not action.option_strings
    }


def _page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


def _subcommand_section(name: str) -> str:
    """The slice of the page under the ``## `tether <name>`` `` heading.

    Membership checks have to be scoped to the owning subcommand, not run against the
    whole page: ``--tmap``, ``--tdat`` and ``--overwrite`` exist on *both* parsers, so a
    page-wide check would accept an ``extract``-only flag that had been documented under
    ``batch`` and never mentioned in its own section.

    The slice runs to the next level-2 heading, so the ``###`` subsections (the
    ``--donor-side`` callout, the worked examples) count as part of their subcommand.
    """
    heading = f"## `tether {name}`"
    text = _page_text()
    start = text.find(heading)
    assert start != -1, f"docs/cli.md has no '{heading}' section"
    rest = text[start + len(heading) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _option_table(section: str) -> list[tuple[str, str, str]]:
    """The body rows of the section's ``| Argument | Default | Meaning |`` table.

    Each row is returned as its three stripped cells. A row that does not split into
    exactly three cells fails here, because a ``|`` inside a cell would shift the
    Default column and the checks below would then read the wrong cell.
    """
    lines = section.splitlines()
    header = next(
        (i for i, line in enumerate(lines) if line.startswith("| Argument | Default |")), None
    )
    assert header is not None, "the section has no '| Argument | Default | Meaning |' table"
    rows: list[tuple[str, str, str]] = []
    for line in lines[header + 2 :]:  # skip the header and the |---| separator
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 3, f"an option-table row does not have three cells: {line!r}"
        rows.append((cells[0], cells[1], cells[2]))
    assert rows, "the option table has no rows"
    return rows


def _usage_block(name: str, section: str) -> str:
    """The section's usage synopsis: its first fenced block, ``tether <name> [-h] ...``."""
    match = re.search(r"^```text\n(.*?)^```", section, re.DOTALL | re.MULTILINE)
    assert match, f"the `tether {name}` section has no fenced usage block"
    usage = match.group(1)
    assert usage.startswith(f"tether {name} "), (
        f"the first fenced block of the `tether {name}` section is not its usage synopsis"
    )
    return usage


def _same_default(literal: str, default: object) -> bool:
    """Does the page's backticked ``literal`` spell argparse's ``default``?

    Numbers compare by value, so the page may print ``2`` for the float ``2.0`` as
    argparse's own ``(default: 2)`` help text does.
    """
    if isinstance(default, int | float) and not isinstance(default, bool):
        try:
            return float(literal) == float(default)
        except ValueError:
            return False
    return literal == default


def test_the_reference_page_exists() -> None:
    """Sanity: guards a renamed page silently disabling every assertion below."""
    assert PAGE.is_file(), f"the CLI reference is missing from {PAGE}"


def test_both_subcommands_are_documented() -> None:
    """`extract` and `batch` each get their own section.

    They are the entire headless product surface; a third subcommand added later must
    either be documented or this fails.
    """
    text = _page_text()
    names = set(_subparsers(build_parser()))
    assert names == {"extract", "batch"}, f"unexpected subcommand set: {sorted(names)}"
    for name in sorted(names):
        assert f"`tether {name}`" in text, (
            f"docs/cli.md has no `tether {name}` section for the {name!r} subcommand"
        )


def test_option_counts_are_unchanged() -> None:
    """The per-subcommand option counts the page was written against still hold.

    This is the tripwire: it fires on *any* added or removed flag, including one whose
    name happens to already appear somewhere on the page, which the membership checks
    below would not catch.
    """
    subs = _subparsers(build_parser())
    actual = {
        name: len({o for o in _option_strings(p) if o.startswith("--")}) for name, p in subs.items()
    }
    assert actual == _EXPECTED_OPTION_COUNTS, (
        "the CLI option set changed; update docs/cli.md and this expectation together — "
        f"expected {_EXPECTED_OPTION_COUNTS}, found {actual}"
    )


def test_every_parser_option_is_documented() -> None:
    """Direction 1 — nothing the parser accepts is missing from its own section."""
    parser = build_parser()

    missing: dict[str, list[str]] = {}
    for name, sub in sorted(_subparsers(parser).items()):
        documented = _documented_flags(_subcommand_section(name))
        absent = sorted(opt for opt in _option_strings(sub) if opt not in documented)
        if absent:
            missing[name] = absent
    # The top-level parser's own options belong to no subcommand section, so they are
    # the one thing still checked page-wide.
    documented_page_wide = _documented_flags(_page_text())
    top_level_absent = sorted(
        opt for opt in _option_strings(parser) if opt not in documented_page_wide
    )
    if top_level_absent:
        missing["tether"] = top_level_absent

    assert not missing, (
        f"these CLI options are not documented in their own docs/cli.md section: {missing}"
    )


def test_every_positional_is_documented() -> None:
    """Direction 1, continued — the positional arguments are named in their section."""
    parser = build_parser()
    missing = {
        name: sorted(
            dest for dest in _positionals(sub) if f"`{dest}`" not in _subcommand_section(name)
        )
        for name, sub in sorted(_subparsers(parser).items())
    }
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, (
        f"these CLI positionals are not documented in their own docs/cli.md section: {missing}"
    )


def test_the_page_invents_no_flags() -> None:
    """Direction 2 — every flag on the page, short aliases included, is one the parser defines.

    Catches the more damaging drift: a flag that was renamed or removed leaves behind
    prose telling users to type something that now fails.

    Short aliases have to be in scope here: the option-count tripwire above only counts
    ``--`` options, so dropping ``-d`` while keeping ``--out-dir`` moves no count and leaves
    the page telling readers to type a spelling argparse now rejects.
    """
    parser = build_parser()
    real = _option_strings(parser) | {
        opt for sub in _subparsers(parser).values() for opt in _option_strings(sub)
    }
    real |= set(_UNDOCUMENTED)

    invented = sorted(_documented_flags(_page_text()) - real)
    assert not invented, (
        "docs/cli.md documents these flags, but no tether parser defines them "
        f"(renamed or removed?): {invented}"
    )


def test_each_section_usage_and_table_name_only_its_own_flags() -> None:
    """Direction 2, per section — the usage block and option table use only own flags.

    The page-wide check above unions the options of all parsers, and ``--tmap``,
    ``--tdat`` and ``--overwrite`` are on both. So a flag removed from ``extract`` but
    still on ``batch`` left ``tether extract [--tmap PATH]`` on the page, and only the
    option-count tripwire failed; remove one flag and add another whose name the
    section's prose already contains, and nothing failed (#744, GUI-F20). The prose of a
    section may name the other subcommand's flags (``--policy`` on ``tether batch`` in the
    extract section, ``--donor-side`` in the batch section), so the scope is the usage
    block and the option table, which describe the one parser only.
    """
    invented: dict[str, list[str]] = {}
    for name, sub in sorted(_subparsers(build_parser()).items()):
        section = _subcommand_section(name)
        table = "\n".join(" | ".join(row) for row in _option_table(section))
        documented = _documented_flags(_usage_block(name, section)) | _documented_flags(table)
        foreign = sorted(documented - _option_strings(sub) - set(_UNDOCUMENTED))
        if foreign:
            invented[name] = foreign
    assert not invented, (
        "the usage block or option table of these docs/cli.md sections names flags "
        f"that the subcommand's own parser does not define: {invented}"
    )


def test_each_option_table_default_is_the_default_argparse_holds() -> None:
    """The Default column states the value the parser applies when the flag is absent.

    The page says a quoted default "is the string argparse prints", but only option
    names were compared; every default in ``tether.cli`` is a literal of its own and
    could change with the page left behind (#744, GUI-F3). Each option whose argparse
    default is a value must have a row whose Default cell is that value, and each
    ``store_true`` switch a row that reads ``off``. Options that default to ``None``
    resolve their effective default elsewhere (the detection mode, the sidecar
    supervision constants, the log path), and their cells are not read here.
    """
    for name, sub in sorted(_subparsers(build_parser()).items()):
        by_flag = {
            opt: action
            for action in sub._actions  # noqa: SLF001 - mirrors _subparsers above
            for opt in action.option_strings
        }
        checked: set[str] = set()
        for argument, default_cell, _meaning in _option_table(_subcommand_section(name)):
            flags = [flag for flag in _FLAG_RE.findall(argument) if flag in by_flag]
            if not flags:
                continue  # a positional argument
            action = by_flag[flags[0]]
            row = f"`tether {name}` row {argument!r}"
            if isinstance(action, argparse._StoreTrueAction):  # noqa: SLF001
                assert default_cell == "off", f"{row}: a switch's Default cell must read 'off'"
            elif action.default is not None:
                literals = re.findall(r"`([^`]+)`", default_cell)
                assert len(literals) == 1 and _same_default(literals[0], action.default), (
                    f"{row}: docs/cli.md prints the default {default_cell!r}, but the parser "
                    f"holds {action.default!r}; change the page and tether.cli together"
                )
            else:
                continue
            checked.add(action.dest)
        defaulted = {
            action.dest
            for action in sub._actions  # noqa: SLF001
            if action.option_strings
            and action.dest != "help"
            and (isinstance(action, argparse._StoreTrueAction) or action.default is not None)  # noqa: SLF001
        }
        assert checked == defaulted, (
            f"these `tether {name}` options hold a default that no option-table row states: "
            f"{sorted(defaulted - checked)}"
        )


def test_the_batch_policy_default_is_the_run_batch_default() -> None:
    """``tether batch --policy`` defaults to the policy ``run_batch`` applies by itself.

    The argparse default is a literal, and ``_run_batch`` always passes it on, so the
    library default would never be used from the command line. A changed literal
    turns every unattended batch from "keep with a flag" into "fail" (#744, GUI-F3).
    """
    import inspect  # noqa: PLC0415 - test-local stdlib import

    from tether.project.batch import POLICY_WARN, run_batch  # noqa: PLC0415

    batch = _subparsers(build_parser())["batch"]
    policy = next(a for a in batch._actions if a.dest == "policy")  # noqa: SLF001
    assert policy.default == inspect.signature(run_batch).parameters["policy"].default
    assert policy.default == POLICY_WARN


def _exit_code_section() -> str:
    text = _page_text()
    start = text.find("## Exit codes")
    assert start != -1, "docs/cli.md has no exit-code section"
    return text[start:]


def test_argparse_rejects_bad_arguments_with_code_2() -> None:
    """Both subcommands really do exit 2 on a usage error.

    This is exercised rather than asserted about, because the page originally claimed
    ``tether extract`` never returns 2 — argparse's own ``parser.error()`` exits 2 long
    before ``main()`` runs, which made that row simply false.
    """
    parser = build_parser()
    for argv in (["extract", "movie.tif"], ["batch", "movie.tif"]):
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(argv)
        assert excinfo.value.code == 2, (
            f"expected argparse to exit 2 for {argv!r}, got {excinfo.value.code!r}"
        )


def test_batch_refuses_a_basename_collision_with_code_2(tmp_path: Path) -> None:
    """The documented ``batch`` startup refusal returns 2 — and still creates ``--out-dir``.

    Two movies sharing a basename map to one output project; the checkpoint would treat
    the second as already done using the first's data. No movie is read on this path, so it
    is cheap to exercise directly — but ``out_dir.mkdir()`` runs *before* the collision
    loop, so "exit 2 means nothing was written" is not quite true here. The page carries
    that caveat, and both halves are pinned together: move the ``mkdir`` after the check
    and this fails, telling whoever did it that the caveat now has to come off the page.
    """
    out_dir = tmp_path / "nested" / "out"
    code = main(["batch", "a/movie_010.tif", "b/movie_010.tif", "-d", str(out_dir)])
    assert code == 2, f"expected a basename collision to return 2, got {code}"
    assert out_dir.is_dir(), (
        "`tether batch` no longer creates --out-dir before refusing a basename collision; "
        "docs/cli.md's empty-output-directory caveat is now wrong"
    )
    assert not list(out_dir.iterdir()), (
        f"the collision refusal wrote something into {out_dir}: {sorted(out_dir.iterdir())}"
    )
    assert "empty output directory" in _exit_code_section(), (
        "docs/cli.md no longer warns that the basename-collision refusal can leave an "
        "empty --out-dir behind"
    )


def test_the_exit_code_table_documents_every_code() -> None:
    """The table names 0, 1 and 2 for both subcommands.

    Presence of the row is the weak half; the exercised tests above are what pin the
    behaviour. What this adds is that the page cannot quietly drop a code.

    The rows themselves are parsed: the prose after the table also spells `` `0` ``,
    `` `1` `` and `` `2` ``, so a search of the section text passed with the whole table
    deleted (#744, GUI-F19). Each code needs exactly one ``| `N` | … | … |`` row with a
    non-empty cell for ``tether extract`` and one for ``tether batch``.
    """
    rows: dict[str, list[list[str]]] = {}
    for line in _exit_code_section().splitlines():
        match = re.match(r"^\| `(\d+)` \|(.*)\|\s*$", line)
        if match:
            cells = [cell.strip() for cell in match.group(2).split("|")]
            rows.setdefault(match.group(1), []).append(cells)
    assert sorted(rows) == ["0", "1", "2"], (
        f"the exit-code table documents the codes {sorted(rows)}, not 0, 1 and 2"
    )
    for code, found in sorted(rows.items()):
        assert len(found) == 1, f"the exit-code table has {len(found)} rows for `{code}`"
        assert len(found[0]) == 2 and all(found[0]), (
            f"the `{code}` row must state the code's meaning for both `tether extract` "
            f"and `tether batch`, found {found[0]}"
        )


def test_the_page_does_not_claim_extract_never_exits_2() -> None:
    """Regression guard for the specific error this page shipped with.

    The first draft's table read ``| 2 | *not used* | …``, which a script author would
    reasonably read as "an ``extract`` invocation can only return 0 or 1". It cannot be
    reintroduced without failing here.
    """
    assert "*not used*" not in _exit_code_section(), (
        "the exit-code table again claims a code is unused; `tether extract` exits 2 on "
        "an argparse usage error (see test_argparse_rejects_bad_arguments_with_code_2)"
    )


def test_the_deferred_idealization_caveat_survives() -> None:
    """Exit 0 from ``batch`` does not imply idealization ran, and the page must say so.

    ``run_batch`` records an unavailable-at-startup sidecar as ``deferred``, which is not
    ``failed``, so ``n_failed`` stays 0 and the run exits 0 having done extraction and
    correction only. A script consuming those projects as fully idealized is the failure
    this paragraph exists to prevent.
    """
    section = _exit_code_section()
    assert "defer" in section.lower(), (
        "the exit-code section no longer warns that `tether batch` can exit 0 with "
        "idealization deferred"
    )


def test_batch_overwrite_help_describes_policy_fail_checkpoint() -> None:
    """The live CLI help names the policy-fail recovery path and its boundary."""
    help_text = " ".join(_subparsers(build_parser())["batch"].format_help().split())
    assert "completed extraction rejected by the current fail policy" in help_text
    assert "accepted completed checkpoints still skip" in help_text


def test_batch_help_defaults_state_the_live_supervision_constants() -> None:
    """The `default 3` / `(default 1800)` printed by `tether batch --help` are literals.

    Both flags are ``default=None`` in ``tether.cli``; the effective defaults live in
    ``tether.idealize.supervisor``, and nothing interpolates them into the help strings —
    deliberately, because ``build_parser()`` runs on the ``tether --version`` path and
    importing either constant there would put NumPy on it. This guard is what makes that
    arrangement safe, and it is the test ``docs/stability.md``'s sidecar carve-out leans
    on: retune a constant (or edit a help string) without the other side and this names
    the stale flag. The default is attributed per option through the parser's own actions
    — never a whole-text scan, where ``--log``'s ``(default: <out-dir>/batch-log.jsonl)``
    sits immediately above ``--max-restarts`` and a stray match could bind the wrong
    option — and the comparison is numeric, so the help may keep printing ``1800`` for
    the float ``1800.0``.
    """
    from tether.idealize.supervisor import (  # noqa: PLC0415 - the path docs/stability.md names
        DEFAULT_MAX_RESTARTS,
        DEFAULT_SIDECAR_TIMEOUT,
    )

    batch = _subparsers(build_parser())["batch"]
    rendered = " ".join(batch.format_help().split())
    by_flag = {
        opt: action
        for action in batch._actions  # noqa: SLF001 - mirrors _subparsers above
        for opt in action.option_strings
    }
    expected = {
        "--max-restarts": DEFAULT_MAX_RESTARTS,
        "--sidecar-timeout": DEFAULT_SIDECAR_TIMEOUT,
    }
    for flag, constant in sorted(expected.items()):
        own_help = " ".join((by_flag[flag].help or "").split())
        assert own_help and own_help in rendered, (
            f"{flag}'s help text is absent from the rendered `tether batch --help`"
        )
        stated = re.search(r"default:? (\d+(?:\.\d+)?)", own_help)
        assert stated, (
            f"{flag}'s --help states no numeric default (its help reads: {own_help!r}); "
            "the printed default is this guard's whole subject and may not silently vanish"
        )
        assert float(stated.group(1)) == constant, (
            f"{flag}'s --help states 'default {stated.group(1)}' but the live constant "
            f"is {constant!r} — the help string is a literal, so whichever side changed, "
            "change the other in the same commit"
        )


def test_policy_fail_rejection_survives_cli_resume(tmp_path, monkeypatch, capsys) -> None:
    """The real CLI + checkpoint path keeps an over-gate movie failed on every run."""
    import json  # noqa: PLC0415 - test-local base dependency
    from types import SimpleNamespace  # noqa: PLC0415

    import h5py  # noqa: PLC0415 - test-local base dependency

    from tether.io.schema import create_project  # noqa: PLC0415
    from tether.project import batch  # noqa: PLC0415

    extract_calls = 0

    def extract(movie_path, output_path, *, options=None, tmap=None, tdat=None, overwrite=False):
        nonlocal extract_calls
        extract_calls += 1
        create_project(output_path, overwrite=True)
        with h5py.File(output_path, "r+") as store:
            profile = store["settings"].create_group("extraction")
            profile.attrs["profile_json"] = json.dumps(
                {"registration_rms_px": 0.75, "rms_gate": 0.5}
            )
        return SimpleNamespace(n_molecules=3, low_confidence_registration=True)

    def forbidden_stage(*args, **kwargs):
        raise AssertionError("a downstream stage ran for a policy-rejected movie")

    real_run_batch = batch.run_batch

    def run_with_stub_stages(jobs, **kwargs):
        return real_run_batch(
            jobs,
            _extract=extract,
            _correct=forbidden_stage,
            _idealize=forbidden_stage,
            **kwargs,
        )

    monkeypatch.setattr(batch, "run_batch", run_with_stub_stages)
    movie = tmp_path / "movie_001.tif"
    movie.write_bytes(b"stub movie")
    argv = [
        "batch",
        str(movie),
        "--out-dir",
        str(tmp_path / "out"),
        "--policy",
        "fail",
        "--no-defer",
    ]

    assert main(argv) == 1
    first = capsys.readouterr().out
    assert "extract=failed" in first
    assert "correct=blocked" in first
    assert "idealize=blocked" in first

    assert main(argv) == 1
    second = capsys.readouterr().out
    assert "extract=failed" in second
    assert "correct=blocked" in second
    assert "idealize=blocked" in second
    assert extract_calls == 1

    section = _subcommand_section("batch")
    assert "does not survive the re-run" not in section
