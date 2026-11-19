<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0111 — The API reference is built offline and links only Tether objects

- **Status:** accepted; amends [ADR-0093](0093-numpy-docstrings-and-mkdocstrings-cross-references.md)
  on the links to builtin and third-party names, whose other decisions govern unchanged
- **Date:** 2026-11-15
- **Deciders:** bioedca (the generator and the wiring, on issue #179; the split from #611, on
  2026-11-14)
- **PRD anchor:** §12.4 (definition of done), §12.6 (the `docs-build` gate), §4.1 (pin-and-hold)
- **Milestone:** M10
- **Issue:** [#179](https://github.com/bioedca/tether/issues/179)

## Context and problem statement

[#179](https://github.com/bioedca/tether/issues/179) wires mkdocstrings with its Python handler
into the site, as ADR-0093 chose. The required `docs-build` gate installs only
`requirements-docs.txt` on stock CPython and runs `mkdocs build --strict`, so every
cross-reference in a rendered docstring must resolve, or the build fails.

ADR-0093 confirmed that every link to a Tether object resolves. It left two groups of warnings
for #179:

- 88 links to names of Python itself or of another project, such as ``[`ValueError`][]``,
  ``[`numpy.histogram`][]`` and ``[`h5py.File`][]``. ADR-0093's table maps ``:exc:`ValueError` ``
  to ``[`ValueError`][]``, and Rule 6 of the standard said the same.
- 34 places where two citation keys stand side by side, such as `[McCann2010][Hellenkamp2018]`.
  Markdown reads that as one link with the text `McCann2010` and the target `Hellenkamp2018`.
  The reader loses the second key and the build fails.

The ADR named two remedies for the first group: give the handler the `objects.inv` inventory of
each project, or remove the links. How does the reference resolve them?

## Decision drivers

- The local gate is the same command as the required one. `mkdocs build --strict` must pass on
  a machine with no network access, as `AGENTS.md` requires of every local gate before review.
- Pin-and-hold (PRD §4.1): the output of the build depends only on pinned files.
- The gate and the deploy (`docs.yml`) install one file and build the same way.
- The published site loads nothing from another host (`docs/privacy.md`).

## Considered options

- **Remove the links: a builtin or third-party name is a code span.**
- Fetch the inventories of Python, NumPy, SciPy, h5py, matplotlib, napari, scikit-learn,
  pytest and torch at build time.
- Commit copies of those inventories and read them from the repository.
- Leave the links, and turn the warnings off.

## Decision outcome

Chosen: **remove the links**. A docstring links only a Tether object. A name from Python itself
or from another project is a code span, such as `` `ValueError` `` or `` `numpy.histogram` ``.
Two citation keys are separated by a comma, as in `[McCann2010], [Hellenkamp2018]`.

A one-time edit made the change in 52 files: 88 links became code spans and 34 citation pairs got
a comma. One link to a dataclass field got the full path of its target, because the alias path
did not resolve. The visible text of each link is the text of its code span, so no word changed. Rule 6
of [Writing docstrings](../maintainers/docstring-style.md) now says this, and the strict build is
the check: a new link to a name that the site does not document fails it.

Fetching the inventories fails the first driver. The cloud containers of this project cannot
reach `docs.python.org` or the other hosts, and a gate that depends on nine other sites fails
when one of them is down. The content of a fetched inventory is also not pinned. Committed copies
fix both, but add nine files of other projects, with their licenses, that someone must refresh at
each version bump, for a convenience link. Turning the warnings off would also hide a broken
link to a Tether object, which is the error that the strict build exists to catch.

### The wiring

- `requirements-docs.txt` pins `mkdocstrings[python]==1.0.8`, `mkdocstrings-python==2.0.11`,
  `mkdocs-autorefs==1.4.5` and `griffelib==2.4.1`, the versions on which ADR-0093 confirmed the
  link form. It stays the one source of the docs toolchain. `pyproject.toml` gets no `docs`
  extra, because #183 removed the extra as a second, wrong copy of this file.
- `mkdocs.yml` lists `search` first, then `mkdocstrings`. MkDocs turns on `search` by itself only
  while the config has no `plugins:` key.
- The handler reads `src` with `allow_inspection: false`. Griffe reads the source and never
  imports it, so the gate never needs PySide6, napari or torch, and the package is not installed.
- `scoped_crossrefs` and `show_if_no_docstring` are on, as ADR-0093 requires.
- One page for each subpackage shows the package docstring, then each public module (a module
  whose dotted path has no part that starts with `_`), with the names in its `__all__`. A module
  with no `__all__` shows its public names. `tests/test_api_reference.py` fails when a public
  module is missing from its page.
- `src/tether/py.typed` marks the package as typed (PEP 561). Hatchling puts it in the wheel.

### Consequences

- Good: the site, the gate and the deploy build the same reference with no network access.
- Good: the strict build checks every Tether link of every rendered docstring.
- Trade-off: a reader cannot click from `numpy.histogram` to the NumPy page. The name is still
  in the text, as a code span.
- Trade-off: a later change that wants those links must add pinned, committed inventories, with
  their licenses, and amend this record.

## More information

- [ADR-0093](0093-numpy-docstrings-and-mkdocstrings-cross-references.md): the docstring standard
  and the link form.
- [Writing docstrings](../maintainers/docstring-style.md), Rule 6.
- #183 (the `docs` extra removed), #611 (the role migration).
