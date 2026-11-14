<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0093 — NumPy docstrings, with cross-references in the form of mkdocstrings

- **Status:** accepted
- **Date:** 2026-11-14
- **Deciders:** bioedca (the docstring style and the scripted migration, on issue #611 on
  2026-11-10; the order of #611 before #179, on 2026-11-14)
- **PRD anchor:** §12.4 (definition of done), §12.6 (the `lint` gate), §12.9 (pre-commit)
- **Milestone:** M10
- **Issue:** [#611](https://github.com/bioedca/tether/issues/611)

## Context and problem statement

[#179](https://github.com/bioedca/tether/issues/179) generates the API reference from the
docstrings with a Markdown-based generator. On `main` at `952b411`, `src/tether` holds 1,888
Sphinx cross-reference roles in docstrings and 74 in comments, for example ``:func:`…` `` and
``:class:`…` ``. A Markdown generator prints each one as literal text. No written standard says
which docstring style the code uses, although the code is consistent: it has NumPy sections
(`Parameters`, `Returns`, `Raises` with an underline) and no Google sections and no Sphinx field
lists.

Which docstring style does Tether write, which generator reads it, and how do the roles become
links that the generator can resolve?

## Decision drivers

- The description and the code change in one commit: the reference is generated, never written
  by hand.
- One style, written down, so that a tool can check it and a contributor does not choose.
- No content change in the migration: it must be reviewable as the output of a script.
- The required `docs-build` gate stays free of dependencies other than `requirements-docs.txt`
  (#179).

## Considered options

For the style:

- **NumPy style.** The code already uses it.
- Google style. Every docstring would change.

For the roles:

- **Rewrite them once with a committed script** to the cross-reference form of the generator.
- Rewrite them at build time, in a generator hook. The source would keep a form that the
  generator does not read, and every reader of the source would meet two forms.
- Leave them as literal text.

## Decision outcome

Chosen: **NumPy style**, read by **mkdocstrings with its Python handler** (griffe), and **one
scripted migration** of the roles. The maintainer decided the style and the migration on
2026-11-10. This record does not reopen them. It records the form, the versions and the rules.

### The cross-reference form

The form is the Markdown reference link of mkdocstrings, confirmed against the installed source
of the versions below (`mkdocs_autorefs/_internal/references.py`, which strips the backticks of
``[`Foo`][]`` to make the identifier, and `mkdocstrings_handlers/python/_internal/rendering.py`,
whose `AutorefsHook.expand_identifier` implements `scoped_crossrefs`):

| Sphinx role | Becomes |
|---|---|
| ``:func:`load_project` `` (a name that Python scoping finds) | ``[`load_project`][]`` |
| ``:class:`~tether.io.movie.Movie` `` | ``[`Movie`][tether.io.movie.Movie]`` |
| ``:func:`tether.fret.apparent_fret` `` | ``[`tether.fret.apparent_fret`][]`` |
| ``:exc:`ValueError` `` | ``[`ValueError`][]`` |
| a role in a comment | a code span, such as `` `load_project` `` |

The short form ``[`name`][]`` is used only where the name, as written, resolves by Python's own
scoping from the docstring's object (a member of an enclosing class, or a name that the module
defines or imports), or is a full path. Everything else gets the explicit form, with the path
where the object is defined. The text that the reader sees is the text that Sphinx showed.

The script turns a role into a code span instead of a link in four cases: a comment (the
generator does not read comments); a private target (the generator leaves private names out, so
a link has no target); a target that the script cannot find (10 in all: a name that no longer
exists, a class that is built inside a function, or a Qt or napari method that the module does
not import); and a link that would make a summary
line longer than 100 characters (27). A summary must stay one line, and ruff's line limit applies
to docstrings.

### The versions

`requirements-docs.txt` pins no generator yet; #179 adds the pins. The form was confirmed on
**mkdocstrings 1.0.8, mkdocstrings-python 2.0.11, mkdocs-autorefs 1.4.5 and griffelib 2.4.1**,
with mkdocs 1.6.1 and mkdocs-material 9.8.1, the pins that the file already holds. A trial build
of every public module of `src/tether` on these versions resolved every cross-reference to a
Tether object. It needs two handler options, which #179 sets:

- `scoped_crossrefs: true`, so that a short ``[`name`][]`` resolves from its scope;
- `show_if_no_docstring: true`, so that a dataclass field or attribute without its own docstring
  has an anchor to link to. Without it, 315 more cross-reference warnings appear, one for each
link to such a member.

The remaining warnings of the trial build are links to the standard library and to NumPy,
SciPy, Qt and the other dependencies. They resolve when #179 adds the projects' `objects.inv`
inventories to the handler, or #179 can remove them. The others (35 warnings) come from 16
reStructuredText citations in the docstring text, such as `[Torres2007]_`. They are not roles,
so this migration does not touch them. #179 meets them.

### How the standard is held

Ruff runs its `D` rules with the NumPy convention on `src/tether` only (`"!src/tether/**" =
["D"]`). Ruff cannot limit the shape rules to public names, so a private docstring that exists
is checked too. The missing-docstring rules (`D1`) apply to public names only. On `main` at
`952b411`, 101 file and rule pairs in 65 files broke the convention. They are the allowlist in
`[tool.ruff.lint.per-file-ignores]`, and it can only become shorter:
`tests/test_docstring_standard.py` fails on an entry that `main` does not have, and
`scripts/check_docstring_allowlist.py`, in the `lint` job, fails on an entry that hides no
violation.

### Consequences

- Good: the reference that #179 generates shows links, not role text, and one script rerun
  reproduces the migration commit.
- Good: a new docstring is checked for its shape the day it is written.
- Trade-off: where a link made a line too long, the script broke the line at a space. Some
  paragraphs are now uneven. Markdown reads them as the same paragraph.
- Trade-off: the short form depends on `scoped_crossrefs`. If #179 or a later change turns it
  off, the short links do not resolve and `mkdocs build --strict` fails, which is the signal.
- Follow-up: rewriting the content of the docstrings to the standard (units, examples) is a
  later sweep, package by package. The allowlist shrinks with it.

## More information

- The standard: [Writing docstrings](../maintainers/docstring-style.md).
- The script: `scripts/migrate_sphinx_roles.py`. It reads `src/tether` with `ast`, never imports
  it, and refuses to write a file whose syntax tree, with its docstrings blanked, would change.
- #179 (the generator wiring), #582 (the prose style guide), #589 (the terms), #185 (prose
  linting).
