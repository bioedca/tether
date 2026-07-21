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
and never mentioned where a reader of ``tether extract`` would look.

Base-matrix only: :func:`tether.cli.build_parser` imports nothing beyond ``argparse``
(the imaging/HDF5 stack is deliberately lazy so ``--version`` stays fast), and the page is
read with :mod:`pathlib`. The two tests that reach into :mod:`tether.project` do pull that
stack in transitively (the package ``__init__`` imports :mod:`tether.io`, and so h5py and
numpy), but those are locked base-environment dependencies rather than optional extras —
nothing here needs Qt, torch or the sidecar, so this runs on all three OSes.
"""

from __future__ import annotations

import argparse
import inspect
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
# documented `-d`. Nothing scopes the match to a `tether` command line, and it cannot be: the
# option tables spell `-o`/`-d` in rows that never name `tether`, which direction 1 has to
# see. So the assumption is about the page as it stands — every short flag on it today is a
# tether option — not an invariant the page enforces. A non-tether example carrying its own
# short flag (`python -m tether`, `ls -l`) would be reported by `test_the_page_invents_no_flags`
# as an invented tether flag; if the page ever grows one, scope *that* test's extraction to
# lines mentioning `tether` rather than loosening the regex.
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
    """
    section = _exit_code_section()
    for code in ("`0`", "`1`", "`2`"):
        assert code in section, f"the exit-code table no longer documents {code}"


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


def test_the_policy_fail_resume_caveat_matches_the_runner() -> None:
    """``--policy fail`` gates only the first run, and the page must still say so.

    ``_do_extract`` calls the extract runner — which has already written the ``.tether``
    by the time it returns — and applies the over-gate policy to its summary afterwards;
    on the next run the ``_is_extracted`` checkpoint is consulted *before* that policy
    branch, so the rejected movie resumes as ``skipped`` and correction/idealization
    complete. Reading the runner's source order is what keeps the paragraph honest in
    both directions: if the ordering is ever changed so the policy wins over the
    checkpoint, this fails and the caveat must come off the page.

    Source-order only — no movie, no project file, no sidecar. Importing
    :mod:`tether.project.batch` does cost the base scientific stack even so: the module
    itself is standard-library-only at module scope, but the package ``__init__`` it runs
    through imports :mod:`tether.io`, and so h5py and numpy.
    """
    from tether.project import batch  # noqa: PLC0415 - keep the module import test-local

    source = inspect.getsource(batch._do_extract)  # noqa: SLF001 - the ordering is the point
    checkpoint = source.index("_is_extracted(")
    gate = source.index("POLICY_FAIL")
    assert checkpoint < gate, (
        "`_do_extract` no longer skips an already-extracted movie before applying the "
        "over-gate policy; docs/cli.md's `--policy fail` resume caveat is now wrong"
    )

    section = _subcommand_section("batch")
    assert "does not survive the re-run" in section, (
        "docs/cli.md no longer warns that a `--policy fail` rejection is undone by a "
        "resume (the project is written before the gate, and the checkpoint skips it)"
    )
