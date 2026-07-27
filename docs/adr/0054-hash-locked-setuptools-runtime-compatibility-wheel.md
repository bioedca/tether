<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0054 — Hash-lock the temporary setuptools runtime compatibility wheel

- **Status:** accepted
- **Date:** 2026-07-26
- **Deciders:** bioedca
- **PRD anchor:** §4.1 (pin-and-hold), §4.3 (isolated sidecar), §9 M9 (packaging)
- **Milestone:** M9

## Context and problem statement

The pinned tMAVEN revision imports `pkg_resources` when `maven_class` starts. setuptools
82.0.0 removed that API, while `sidecar/conda-lock.yml` resolves 82.0.1. Issue #212
therefore added a separately installed `setuptools<81` compatibility layer.

That range floated independently in `packaging.yml`, `release.yml`, and
`scripts/setup_sidecar.py`. Different OS jobs could download different releases, a later
rebuild of the same tag could change, and the commit did not identify the wheel bytes that
shipped. The range also appeared in a manual measurement workflow and in the packaging
build environment even though tMAVEN's `setup.py` does not import `pkg_resources`.

How should Tether retain the necessary legacy runtime API without making a supply-chain
exception invisible or allowing it to spread into build tooling?

## Decision

Use `packaging/setuptools-compatibility.txt` as the sole version and hash source:

- `setuptools==80.9.0`
- wheel `setuptools-80.9.0-py3-none-any.whl`
- SHA-256 `062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922`

PyPI's release record reported that exact universal wheel, digest, and non-yanked state on
2026-07-26. Both constructor workflows use `pip download --require-hashes
--only-binary=:all: --no-deps -r ...`. `scripts/verify_setuptools_wheel.py` then derives
the permitted filename from the committed exact version, rejects missing or extra
setuptools wheels, and independently hashes the staged bytes before exporting the
constructor path.

The older wheel is a **runtime compatibility layer, not a build backend**:

1. The packaging and release jobs build the pinned tMAVEN wheel with their current build
   toolchain; setuptools 80.9.0 is only downloaded and staged afterward.
2. `scripts/setup_sidecar.py` installs the git-pinned tMAVEN source first, then installs
   the compatibility wheel separately with binary-only hash checking and
   `--force-reinstall`, so a rerun cannot trust an already-installed same-version
   distribution without verifying the locked artifact.
3. Constructor installs the already-built compatibility and tMAVEN wheels offline into
   the isolated sidecar. The base Tether environment never receives the older package.

The exception must be removed when the pinned tMAVEN revision no longer imports
`pkg_resources`. A tMAVEN change that merely suppresses the warning does not satisfy the
trigger; the runtime import itself must be gone.

## Security review

This pin is intentionally older and dependency findings remain actionable evidence. At
the time of adoption, upstream advisory
[GHSA-h35f-9h28-mq5c](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c)
(`CVE-2026-59890`, moderate) listed setuptools through 82.0.1 as affected by a Unicode
normalization mismatch in `MANIFEST.in` exclusions while building source distributions
on normalization-preserving macOS filesystems. On 2026-07-26 the GitHub advisory still
listed no patched release, while PyPI's OSV records and a local `pip-audit` of the
compatibility requirement identified `PYSEC-2026-3447` and 83.0.0 as the fixed release.
Neither answer provides a compatible upgrade: setuptools removed `pkg_resources` in
82.0.0, so the fixed 83.0.0 line cannot satisfy the pinned tMAVEN runtime import.

Tether does not waive or hide that finding. Its exposure is bounded because the
compatibility version never builds a source distribution in these paths: tMAVEN is built
before the downgrade, constructor receives prebuilt wheels, and source setup applies the
older wheel only after the git-sourced tMAVEN install. If a future path uses this
interpreter to build an sdist, the exception no longer matches this decision and must
fail review. Dependency-audit output remains visible in CI and the PR requires qualified
security/release judgment.

## Consequences

- A tagged commit determines the exact compatibility artifact without relying on a later
  resolver decision.
- Every OS job stages identical bytes and constructor receives exactly one verified
  universal wheel.
- The source, live-sidecar, advisory packaging, and signed-release paths share one
  requirement instead of copying a range.
- The sidecar conda metadata still records 82.0.1 while pip overlays 80.9.0. This
  deliberate exception remains documented and probe-tested.
- Updating the compatibility artifact requires one reviewed requirements-file change,
  updated provenance evidence, dependency/security review, and the contract tests.

## Evidence

- [PyPI JSON for setuptools 80.9.0](https://pypi.org/pypi/setuptools/80.9.0/json),
  retrieved 2026-07-26: exact wheel filename, SHA-256, non-yanked state, and OSV records
  identifying 83.0.0 as the fix for `PYSEC-2026-3447` / `CVE-2026-59890`.
- `python -m pip_audit -r packaging/setuptools-compatibility.txt --progress-spinner
  off`, run 2026-07-26: reported the known `PYSEC-2026-3447` finding and fix version
  83.0.0; the proposed exception remains subject to qualified security/release review.
- [pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/), consulted
  through Context7 for the locked pip 26.1.2: hash-checking mode requires requirements
  to be pinned and hashed.
- [pip download](https://pip.pypa.io/en/stable/cli/pip_download/), consulted through
  Context7 for pip 26.1.2: `-r`, `--require-hashes`, `--only-binary`, `--no-deps`, and
  `--dest` are supported together.
- [Pinned tMAVEN `setup.py`](https://github.com/GonzalezBiophysicsLab/tmaven/blob/10f4230/setup.py)
  and [`maven.py`](https://github.com/GonzalezBiophysicsLab/tmaven/blob/10f4230/tmaven/maven.py),
  retrieved 2026-07-26: the build imports setuptools; `pkg_resources` is imported when
  `maven_class` configures runtime logging.
