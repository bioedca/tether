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
   toolchain and `--no-cache-dir`, so pip cannot reuse an immutable-commit wheel produced
   by a different builder; setuptools 80.9.0 is only downloaded and staged afterward.
2. `scripts/setup_sidecar.py` installs only the git-pinned tMAVEN source first with
   `--no-build-isolation --no-deps`, so pip cannot resolve or mutate the locked sidecar
   dependency set. Because disabling build isolation makes the caller responsible for
   the build dependencies, the script permits that build only when the target
   interpreter's actual setuptools version, conda package SHA-256, and installed file
   digests match one platform artifact in the supplied unified sidecar lock, and the
   selected absolute lexical `setuptools` package path and `setuptools.build_meta`
   origin resolve to those verified files after all matching bytecode caches are removed.
   The probe runs with isolated/no-site startup before any target import, so version
   equality, `PYTHONPATH`, `.pth`, preloaded modules, symlinked package paths, or
   timestamp-valid cache bytes cannot satisfy the conda build-state gate, while valid
   platform-specific artifact hashes remain accepted.
   Before creating or inspecting an environment, the script also accepts only the
   repository's default pinned short tMAVEN spec or a custom `git+` spec ending in a
   full 40- or 64-hex commit; tags, branches, and abbreviated custom commits fail
   closed instead of being installed and rejected afterward. The VCS command uses
   `--force-reinstall --no-cache-dir`, so restoring the
   locked builder cannot let pip retain an already-installed same-version tMAVEN wheel
   or reuse an immutable-commit wheel cached under the runtime-only backend. The script
   re-inspects the installed PEP 610 and `WHEEL` records immediately after the command
   and refuses the runtime overlay unless both match. After the runtime-only overlay, a
   rerun may instead reuse tMAVEN only when PEP 610 `direct_url.json` proves the
   requested repository and exact resolved commit and the installed distribution's
   `WHEEL` metadata records the locked `Generator: setuptools (...)`, every Python row in
   the raw wheel `RECORD` resolves inside the sidecar prefix and still matches its
   SHA-256, absolute lexical `tmaven.__file__` and `tmaven.__spec__.origin` exactly equal
   the raw-`RECORD` initializer path, and the sole absolute lexical `tmaven.__path__`
   equals its parent. The actual `tmaven.maven` spec selected by the production runner
   must be a located non-package module at the exact raw-`RECORD` `tmaven/maven.py`
   path, with its resolved origin among the digest-verified files. Checking only a
   resolved `tmaven.__file__` is insufficient: a symlinked genuine initializer can
   retain a shadow package path and select an unverified submodule. Before importing
   tMAVEN, the probe safely discards every matching in-prefix legacy and `__pycache__`
   bytecode file named by the raw Python source rows. Cleanup failure or a symlinked cache
   disables reuse; isolated/no-site startup ignores external `PYTHONPYCACHEPREFIX`,
   `PYTHONPATH`, and site hooks, so timestamp-and-size-valid unrecorded `.pyc` bytes
   cannot override the verified `.py` sources. The probe parses
   raw `RECORD` CSV because Python 3.12 `importlib.metadata.Distribution.files` can omit
   a listed file that is missing on disk. Commit and generator metadata alone cannot
   prove which bytes are present or imported. Every other state fails closed and
   requires a genuinely fresh interpreter/new environment name or an explicit
   deterministic restore; reinstalling into the same named conda env is insufficient
   because pip overlays can leave stale files behind conda metadata. Optional
   live-suite tooling is a separate layer: pytest 9.1.1 and its four dependencies absent
   from the lock are pinned and wheel-hashed in `sidecar/pytest-requirements.txt`, then
   installed with `--no-deps`. The compatibility wheel is installed last with
   binary-only hash
   checking and `--force-reinstall`, so a rerun cannot trust an already-installed
   same-version distribution without verifying the locked artifact. Before liveness,
   setup first canonicalizes the installed raw `RECORD` by sorting the original wheel
   rows and excluding only pip-generated cache/installer rows, then requires its digest
   to match the source-controlled `RECORD-SHA256` derived from the hash-locked wheel.
   Only then does an isolated/no-site
   probe eagerly clear all in-prefix bytecode caches, verify every recorded
   `pkg_resources` and `setuptools/_vendor` Python source, import with the verified
   vendored path first, and require the actual initializer, spec, sole package path, and
   loaded `packaging`, `jaraco.text`, `platformdirs`, and transitive vendored origins to
   match those anchored files. Mutable installed metadata, preloaded modules, and
   `PYTHONPATH`/`.pth` shadows therefore fail closed before tMAVEN runs. This independent
   post-overlay check still runs with `--skip-install`; it never silently reinstalls.
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
under the lock's ordinary setuptools before the downgrade, constructor receives prebuilt
wheels, and a source-setup rerun either verifies both that exact Git commit and its
locked-setuptools `WHEEL` generator or refuses the build. If a future path uses this
interpreter to build an sdist, the exception no longer matches this decision and must
fail review. The scheduled dependency audit runs the installed base environment and this
compatibility requirement as separate advisory steps, so a nonzero base finding cannot
suppress the overlay report. The PR requires qualified security/release judgment.

## Consequences

- A tagged commit determines the exact compatibility artifact without relying on a later
  resolver decision. The signed-release pipeline also publishes the requirement as its
  fourth authoritative source-lock asset and covers it with the combined checksum manifest.
- Every OS job stages identical bytes and constructor receives exactly one verified
  universal wheel.
- The source, live-sidecar, advisory packaging, and signed-release paths share one
  requirement instead of copying a range.
- The optional live-sidecar test tooling cannot make pip resolve tMAVEN dependencies or
  overwrite the scientific stack. The sidecar lock continues to supply `packaging==26.2`;
  only the absent pytest tools are layered from their dedicated wheel-hash file.
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
- [pip build-system interface](https://pip.pypa.io/en/stable/reference/build-system/),
  consulted through Context7 for locked pip 26.1.2: default build isolation uses a
  temporary environment independent of the runtime environment; with
  `--no-build-isolation`, the caller must preinstall and manage all PEP 518 build
  dependencies. The target interpreter's setuptools is therefore the tMAVEN build
  toolchain on these source installs.
- [pip caching](https://pip.pypa.io/en/stable/topics/caching/), consulted through
  Context7 for locked pip 26.1.2: pip caches locally built wheels, including wheels from
  immutable VCS commit references, and may reuse them on later installs. The source
  rebuild therefore disables pip's cache and verifies installed build provenance before
  applying the runtime-only compatibility wheel.
- PyPI JSON for [pytest 9.1.1](https://pypi.org/pypi/pytest/9.1.1/json),
  [iniconfig 2.3.0](https://pypi.org/pypi/iniconfig/2.3.0/json),
  [pluggy 1.6.0](https://pypi.org/pypi/pluggy/1.6.0/json),
  [Pygments 2.20.0](https://pypi.org/pypi/Pygments/2.20.0/json), and
  [colorama 0.4.6](https://pypi.org/pypi/colorama/0.4.6/json), retrieved 2026-07-27:
  pytest's direct requirements plus the exact universal wheel SHA-256 values and
  non-yanked state committed in `sidecar/pytest-requirements.txt`.
- [Pinned tMAVEN `setup.py`](https://github.com/GonzalezBiophysicsLab/tmaven/blob/10f4230/setup.py)
  and [`maven.py`](https://github.com/GonzalezBiophysicsLab/tmaven/blob/10f4230/tmaven/maven.py),
  retrieved 2026-07-26: the build imports setuptools; `pkg_resources` is imported when
  `maven_class` configures runtime logging.
