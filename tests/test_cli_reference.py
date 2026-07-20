# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The published CLI reference matches the parser it documents (PRD §9 M9 docs gate).

``docs/cli.md`` is the only user-facing description of the headless surface, and the
failure mode it guards against is silent: a flag added to :mod:`tether.cli` without a
docs edit leaves the page quietly incomplete, and a flag renamed or removed leaves the
page quietly *wrong*. Neither is visible to ``mkdocs build --strict`` — no link breaks
either way — and neither shows up in a diff that only touches ``src/``.

So the check runs in both directions, which is exactly what issue #166 asks for: every
option the parser defines is named on the page, and every ``--flag`` token on the page
is an option the parser actually defines. It reads the *live* parser rather than a
transcript, so it cannot drift the way a copied ``--help`` block can.

Base-matrix only: :func:`tether.cli.build_parser` imports nothing beyond ``argparse``
(the imaging/HDF5 stack is deliberately lazy so ``--version`` stays fast), and the page
is read with :mod:`pathlib`. No optional dependency, so this runs on all three OSes.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tether.cli import build_parser

PAGE = Path(__file__).resolve().parents[1] / "docs" / "cli.md"

# Counted in issue #166 from the `add_argument` calls, and asserted here so that adding
# an option without touching the page fails loudly rather than silently under-documenting
# it. Positionals (`movie`, `movies`) are checked separately below.
_EXPECTED_OPTION_COUNTS = {"extract": 14, "batch": 11}

# `-h/--help` is argparse boilerplate on every parser and is deliberately not documented.
_UNDOCUMENTED = frozenset({"-h", "--help"})

# A long-form flag as it appears in prose, a table cell or a fenced block.
_FLAG_RE = re.compile(r"--[a-zA-Z][a-zA-Z0-9-]*")


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
    """Direction 1 — nothing the parser accepts is missing from the page."""
    parser = build_parser()
    text = _page_text()

    missing: dict[str, list[str]] = {}
    for name, sub in sorted(_subparsers(parser).items()):
        absent = sorted(opt for opt in _option_strings(sub) if opt not in text)
        if absent:
            missing[name] = absent
    top_level_absent = sorted(opt for opt in _option_strings(parser) if opt not in text)
    if top_level_absent:
        missing["tether"] = top_level_absent

    assert not missing, f"these CLI options are not documented in docs/cli.md: {missing}"


def test_every_positional_is_documented() -> None:
    """Direction 1, continued — the positional arguments are named too."""
    parser = build_parser()
    missing = {
        name: sorted(dest for dest in _positionals(sub) if f"`{dest}`" not in _page_text())
        for name, sub in sorted(_subparsers(parser).items())
    }
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, f"these CLI positionals are not documented in docs/cli.md: {missing}"


def test_the_page_invents_no_flags() -> None:
    """Direction 2 — every ``--flag`` on the page is one the parser really defines.

    Catches the more damaging drift: a flag that was renamed or removed leaves behind
    prose telling users to type something that now fails.
    """
    parser = build_parser()
    real = _option_strings(parser) | {
        opt for sub in _subparsers(parser).values() for opt in _option_strings(sub)
    }
    real |= set(_UNDOCUMENTED)

    invented = sorted(set(_FLAG_RE.findall(_page_text())) - real)
    assert not invented, (
        "docs/cli.md documents these flags, but no tether parser defines them "
        f"(renamed or removed?): {invented}"
    )


def test_documented_exit_codes_match_the_implementation() -> None:
    """The page's exit-code table is the contract scripts depend on.

    ``tether batch`` reserves 2 for a refusal to start; ``tether extract`` never returns
    it. Assert the page still says so, so the table cannot rot into a plausible lie.
    """
    text = _page_text()
    assert "## Exit codes" in text, "docs/cli.md has no exit-code section"
    assert "*not used*" in text, (
        "the exit-code table no longer records that `tether extract` never returns 2"
    )
