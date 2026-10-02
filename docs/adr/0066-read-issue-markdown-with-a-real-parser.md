<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0066 — Read issue Markdown with a real parser, as a dev-only dependency

- **Status:** accepted
- **Date:** 2026-10-02
- **Deciders:** bioedca
- **PRD anchor:** §4.1 (pin-and-hold), §12.5–§12.6
- **Milestone:** M11 — Agent-swarm infrastructure

## Context and problem statement

`.agents/bin/claim.py` is the mutex gate every agent passes through before working an issue. Part
of its precondition is the issue's own `Execution autonomy`, which it reads from the issue body;
`AGENTS.md` §Concurrent GitHub Flow states the rule — only a registered value may be claimed, one
restrictive declaration governs however many admitting ones sit beside it, *wherever it sits*.

Reading *what* a declaration says is a small, bounded problem: two registered values, a short list
of refusal tokens, exact comparison. Reading *where* it sits is not. The gate has to know which
lines belong to a heading's section, where a list item's paragraph ends and its remainder begins,
whether a line is a table row, and whether any of it is inside a fenced example and therefore
literal. That is the block grammar of CommonMark, and until this record the gate read it with
hand-written regular expressions.

[#454](https://github.com/bioedca/tether/issues/454) set out to close three ways that reading
failed open. Its pull request, #462, was reviewed by Codex eight times. Each read found a
structural misreading the previous fix had not reached — a value read from its first physical line
rather than its paragraph; a `+` bullet and an indented bullet invisible to a pattern written for
`-`; a tab after a marker measured as one column rather than as a stop at four; an ordered marker
that cannot interrupt a paragraph treated as though it could; a fence closed by the wrong character
or by a shorter run; prose under a heading scanned as one string so that two blocks could spell a
refusal token across a blank line. After seven fixing commits the eighth read still carried four:
a tab before `##` ending a section early, a mixed-character fence closer accepted, a lazy
continuation inside a nested item dropped, and indented code scanned as a table row.

Every one of those failed in the same direction. A structure the expressions did not see is a
restriction the gate did not read, and a restriction not read is a claim admitted. The sequence
was not converging, and the reason is not subtle: CommonMark's block grammar is defined by a
multi-pass algorithm over container blocks, and no finite set of line-oriented patterns reproduces
it. Each fix was correct for the shape named in the finding and wrong for the next shape over.

## Decision

**Block structure is read with `markdown-it-py`, and the gate's rules are applied to the tree it
returns.** The dependency is dev-only, and it is declared where the agent scripts run rather than
added to the locked environments.

- **The parser.** `markdown-it-py` is a pure-Python port of markdown-it, CommonMark-compliant
  against the specification's test suite, with a `table` rule for the GitHub extension the gate
  must read, and every block token carrying its source line range. It is **already pinned in the
  base `conda-lock.yml` at 4.2.0** as a transitive dependency of `rich`, which napari requires.
  Choosing it adds no package to any restored environment and changes no lock.
- **The boundary.** `.agents/bin/markdown_structure.py` is the only module that imports the
  parser. It returns a tree of plain records — headings with their level, text and column; list
  items with their marker, number, column and child blocks; paragraphs with soft breaks preserved;
  tables as rows of cells; HTML blocks; block quotes; and fenced or indented code marked literal —
  each with its 0-based source line. It knows nothing about autonomy. The gate's rules — which
  values admit, which shapes may admit, what is scanned for refusal tokens, which source governs —
  stay in `claim.py` and are applied to the tree. The parser decides what a heading *is*; the gate
  decides what a heading *means*.
- **Unknown blocks stop the gate.** The parser is configured once, as the `commonmark` preset with
  `table` enabled, and the token kinds that configuration can emit are a closed set the module
  names in full. A kind outside that set means the configuration changed, and the module raises
  rather than dropping the block — a mutex gate that silently reads past structure is the defect
  this record exists to end.
- **Dev-only, declared where it runs.** The package is never imported by `src/tether`, so it does
  not belong in `environment.yml` and triggers no re-lock (which would re-solve every pin — a
  deliberate change in its own right, not a side effect of this one). It is declared in the `dev`
  extra of `pyproject.toml`, unpinned like the other extras; on the CI `test` job's explicit
  tooling line, pinned to the locked `4.2.0`, where it is a no-op today and survives a future
  re-lock that drops the transitive path; and on `agent-reaper.yml`, whose bare `setup-python`
  runner restores no lock and runs `reaper.py`, which loads `claim.py`. `CONTRIBUTING.md` names it
  on the same line as `pytest`. A worker whose interpreter lacks it gets an import error that
  names the install line.
- **The consumer lands separately.** This record and its pull request land the dependency, the
  module and its tests. #454's pull request is reworked on top of them to replace its structure
  expressions with calls into the module, so that the dependency decision and the gate's semantics
  are reviewed as two changes rather than one.

## Consequences

- The gate reads the issue GitHub renders. A restriction written as a wrapped bullet, a `+` bullet,
  a nested item, a blockquoted item, a tab-indented heading, a row of a table without outer pipes,
  or prose under a heading is where a reader of the rendered issue would see it, and a fenced
  example is literal. None of those needed a pattern of its own.
- The agent layer carries one third-party import. ADR-0064 declared the layer feature-complete
  and this record is a maintainer-opened decision, not a capability grown from a review finding;
  the import is confined to one module so that the rest of the layer stays stdlib and the
  dependency can be read, tested and replaced in one place.
- Two workflows and one page name the version: the two pip lines and `CONTRIBUTING.md` pin
  `4.2.0` to match the lock, and a lock bump that moves `markdown-it-py` must move them in the
  same change. `conda-lock.yml` remains the source of truth; the pins elsewhere restate it.
- `reaper.py` and every test that loads `claim.py` by path now need the parser importable. On the
  3-OS `test` matrix it is, from the lock; on the reaper's runner it is installed explicitly.

## Alternatives considered

- **Keep widening the regular expressions.** Rejected on the evidence above: eight reads, eighteen
  findings, four still open, each fix correct for the shape named and wrong for the next.
- **Vendor a minimal CommonMark block parser.** Rejected: the block grammar's difficulty is the
  whole reason for this record, and a vendored copy would be the same code with a different
  author and no specification test suite behind it.
- **`mistune` or Python-Markdown.** Rejected: neither is in any committed lock, so either would add
  a package to a restored environment or require a pip line that adds something new; neither
  tracks the CommonMark specification as closely; and neither carries source line ranges on block
  tokens, which the gate needs to report the line a groomer must rewrite.
- **Add it to `environment.yml`.** Rejected: a base re-lock re-solves every pin and needs a
  GUI-stack retest, and the package is not a runtime dependency of the application. The lock
  already carries it; the extra and the pip lines are the honest statement of who needs it.
- **Keep the gate stdlib-only by moving the structural rules into a maintainer-run grooming
  check.** Rejected: the claim is the mutex, and a precondition checked anywhere but at the claim
  is not a precondition of the claim.
