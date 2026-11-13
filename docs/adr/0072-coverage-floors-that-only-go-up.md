<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0072 — Coverage floors that only go up, measured on the Linux leg

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** bioedca
- **PRD anchor:** §12.6 (CI merge gate); NFR-REPRO
- **Milestone:** M19

## Context and problem statement

CI did not measure test coverage, so new code with no test merged without a signal (#724, finding
DCS-F4 of the #695 audit). A measurement on `main` at `4094720` found 92.8 % of the lines run by
the required matrix, with packages from 84.1 % (`ml`) to 98.5 % (`fret`). How does CI measure
coverage and stop it from falling, without a third-party service and without a change to what
the installers ship?

## Decision drivers

- The maintainer decision on #724: one floor for the total and one per package, start values at
  the measured value rounded down, and floors that can only go up.
- Coverage is not sufficient alone: most of the 167 audit findings are lines that the tests run
  and that can be wrong while all tests pass (#695). It is a floor, not a target; #725 adds the
  monthly mutation run.
- `conda-lock.yml` is the runtime stack and the constructor installers are rendered from it
  (ADR-0049). Test tooling (`pytest`, `pytest-qt`) is pip-pinned in the `test` job on top of the
  restored lock, never locked (ADR-0004).
- The Linux leg may grow by less than 25 %; the macOS and Windows legs do not change.

## Considered options

- **A. `coverage` pip-pinned on the Linux leg; floors in a committed file; a test ratchet.**
- **B. Add `coverage` (or `pytest-cov`) to `environment.yml` and re-lock the base stack.**
- **C. A coverage service** (Codecov, Coveralls) that keeps the history and posts the delta.

## Decision outcome

Chosen option: **A**.

- The Linux leg runs `coverage run -m pytest` with branch measurement (`[tool.coverage.run]` in
  `pyproject.toml`) and uploads `coverage.json` and `coverage.txt` as the `coverage-linux` artifact.
- `.github/coverage-floors.toml` holds the floors of **line** coverage: `total` and one per package
  of `src/tether`. `scripts/check_coverage_floors.py` writes the line and branch table to the job
  summary, fails the job below a floor, prints a notice when a value is more than one point above
  its floor, and fails when a package has no floor or a floor names no package.
- The ratchet is `tests/test_coverage_floors.py::test_floors_never_go_down`: it compares the file
  with the copy on `main`, which the Linux leg fetches. A lowered or removed floor fails it. There,
  `TETHER_COVERAGE_BASE_REF` is set, so a missing `main` fails instead of skipping.
- Two modules are excluded **by name** because they never run in the base environment:
  `idealize/_sidecar_runner.py` (the tMAVEN sidecar interpreter, PRD §4.3) and
  `ml/deep/_torch_model.py` (imports torch at module top, ADR-0047). Measuring them in their own
  environments is not in this record.

B was rejected because the lock is what the installers bundle: a test-only tool would ship to
every user, and a base re-lock is a deliberate, maintainer-approved event (ADR-0004) for no runtime
gain. The `test` extra of `pyproject.toml` names `coverage` in place of the unused `pytest-cov`. C
was rejected by the issue (no third-party service) and needs a token.

### Consequences

- Good: a pull request that deletes tests or adds untested code crosses a floor and fails the
  required `test (ubuntu-latest)` context; the table is in every Linux run's summary.
- Good: raising a floor is a one-line edit that the notice prompts; lowering one needs a change to
  this record and the maintainer's decision, not a quiet edit.
- Trade-off: the Linux leg is slower (the pull request records the measured times). Coverage of
  code that the tests run in a subprocess is not counted, so a value can read lower than reality.
- Trade-off: macOS-only and Windows-only branches are not measured.
- Follow-up: #725 (mutation run), #702 and the bad-test issues raise the values.

## More information

- Issue #724; audit #695 (DCS-F4). `coverage` 7.17.2: line and branch summaries from
  `coverage json` (`files.<path>.summary.covered_lines`, `num_statements`, `covered_branches`,
  `num_branches`), `omit` and `relative_files` in `[tool.coverage.run]` (Context7,
  `/coveragepy/coveragepy/7.17.2`, queried 2026-11-11).
- Related: [ADR-0004](0004-pin-and-hold-dual-lock-isolation.md) (pin-and-hold locks),
  [ADR-0047](0047-deep-model-optional-stack-and-dataset.md) (the deep stack),
  [ADR-0049](0049-m9-packaging-constructor-architecture.md) (installers rendered from the lock).
