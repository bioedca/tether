# 0049 — M9 packaging: constructor installer architecture (offline base env + isolated sidecar)

- **Status:** accepted
- **Date:** 2026-07-13
- **Deciders:** bioedca
- **PRD anchor:** §4.1 (technology stack / installers), §9 M9 (packaging & docs), §12.7 (release pipeline)
- **Milestone:** M9

## Context and problem statement

M9 ships Tether as **signed, self-contained desktop installers** for Windows, macOS and Linux:
"installers install clean on Windows + Mac + Linux" (§9 M9), built with **`constructor`** (PRD §4.1
names it). The installer must bundle two of the repo's three isolated conda stacks and honour every
load-bearing invariant:

1. **Two conda-lock stacks stay isolated** (PLAN §1.3, [[ADR-0004]]): the **base** GUI/compute stack
   (`conda-lock.yml` — PySide6/napari on current numpy) and the **trimmed tMAVEN sidecar**
   (`sidecar/conda-lock.yml` — `numpy<2`/PyQt5, `biasd` omitted, numba upper-bounded). They must land
   as **two separate environments inside one installer**, never merged into one interpreter.
2. **Pin-and-hold** (PRD §4.1, [[ADR-0004]]): the installer must bundle the **exact** locked packages,
   never re-solve fresh at build time.
3. **No install-time git/network** (§9 M9): everything the two envs need is bundled at *build* time;
   the installer resolves fully offline.
4. Two components are **not** conda packages and cannot come from a channel:
   - **`tether` itself** is a hatchling/`hatch-vcs` **wheel**, not a conda package.
   - **tMAVEN** is a `pip`/git project (not on conda-forge), the source of the sidecar's `vbFRET` /
     consensus VB-HMM / ebFRET drivers, with `biasd` deliberately omitted (sidecar env header).
5. The optional **`deep/` GPU stack** ([[ADR-0047]]) is a **terminal optional add-on** ("CPU base app
   unaffected", §9 M8) — a heavy torch/CUDA world that must **not** bloat the base installer.
6. The final installers must be **code-signed** (Authenticode on Windows, `productsign` + notarization
   on macOS) — which requires signing **secrets/certificates** that only the maintainer holds.

Constructor's runtime model makes (2)–(4) tractable: it fetches every package at **build** time and
embeds them, so the produced installer is offline by construction; a secondary env is declared with
`extra_envs`; and non-conda payloads ride along via `extra_files` + a `post_install` script.

## Decision drivers

- Honour **pin-and-hold**: bundle the frozen locks byte-for-byte, no re-solve ([[ADR-0004]]).
- Keep the **two stacks isolated**: the sidecar is its own environment, never in the base interpreter.
- **Offline by construction**: no git/network at install time (§9 M9).
- Keep the **base app / base lock untouched** and the required 3-OS matrix green — packaging is
  heavy, network-bound and cannot run in the required `test` matrix (mirrors [[ADR-0047]]'s reasoning
  for the deep leg).
- Ship the **GPL** license texts beside/inside the installer (REUSE/GPL compliance).
- **Never fabricate**: bundle real, locked, buildable artifacts — no stub env, no placeholder wheel.

## Considered options

- **A. `constructor` with rendered explicit locks + offline wheels + an `extra_envs` sidecar.**
  Base env from the **rendered per-platform explicit lock** (`conda-lock render` → `@EXPLICIT`), so
  constructor installs the exact pinned URLs and never re-solves _(see the **base-env restructure**
  correction below: constructor requires `conda` in `base` for `extra_envs`, so the GUI stack moved to
  an `extra_envs` and `base` became a thin python+conda bootstrap — the pinned locks are unchanged)_;
  the **`tether` wheel** bundled via
  `extra_files` and `pip install --no-index --no-deps` in a `post_install` script; the **trimmed
  sidecar** declared as a constructor `extra_envs` from the rendered `sidecar/conda-lock.yml`, with
  the **tMAVEN wheel** bundled and offline-installed into it and `TETHER_SIDECAR_PYTHON` wired to the
  bundled sidecar interpreter. The `deep/` stack is **not** bundled. Signing is layered on later.
- **B. PyInstaller / Briefcase one-file bundle.** Rejected: napari/PySide6 + Qt/OpenGL plus a *second*
  `numpy<2`/PyQt5 world is precisely what conda environments isolate cleanly; a freezer fights the
  dual-numpy isolation and the GL stack, and constructor is the PRD-named tool.
- **C. `conda-pack` of pre-built envs.** Rejected: produces a tarball, not a user installer — no
  guided install, menu/uninstall, or per-OS packaging; doesn't meet "installers install clean".
- **D. Also bundle the `deep/` GPU stack.** Rejected: bloats every install with a heavy DL framework
  and breaks "optional / CPU base unaffected" (§9 M8); torch-CUDA stays the documented separate
  install ([[ADR-0047]]).

## Decision outcome

**Chosen: Option A.** It is the only option that honours *both* pin-and-hold (rendered explicit
locks, no re-solve) *and* the two-isolated-stacks invariant (the sidecar as a constructor
`extra_envs`), while producing an offline, GPL-compliant, per-OS installer.

**This PR (PR-1a) is the recipe + advisory build leg only — UNSIGNED.** Concretely it lands:

- `packaging/construct.yaml` — the recipe: base env from the per-platform rendered explicit lock
  (platform-selected), `extra_envs.sidecar` from the rendered sidecar lock, `extra_files` for the two
  wheels + the GPL `license_file`, `post_install` scripts, `installer_type` per OS (`exe`/`pkg`/`sh`),
  and the signing keys **present but documented-and-unset** (constructor leaves installers unsigned by
  default) so PR-2 turns them on with secrets.
- `packaging/scripts/post_install.{sh,bat}` — offline `pip --no-index --no-deps` of the `tether`
  wheel into the base env and the tMAVEN wheel into `envs/sidecar`, then persist
  `TETHER_SIDECAR_PYTHON` pointing at the bundled sidecar interpreter.
- `.github/workflows/packaging.yml` — a **non-required, `workflow_dispatch`-only** 3-OS leg
  (render locks → build the two wheels → `constructor` → **networking-blocked install-smoke**:
  `tether --version`, then the bundled sidecar interpreter imports `tmaven`/PyQt5 offline). It is
  advisory **by construction** (no `pull_request`/`push`/`merge_group` trigger), so it can never
  become a required merge check — the same posture as `deep-gpu.yml` ([[ADR-0047]]).
- `tests/test_marker_contract.py` — a new clause locking `packaging.yml`'s advisory shape
  (dispatch-only; runs the offline install-smoke), so the leg cannot silently become gating.

**Signing / notarization is deferred to PR-2 (`release.yml`, §12.7)** where the Authenticode
certificate and Apple notarization credentials are wired as repository secrets — constructor's
`windows_signing_tool` / `signing_identity_name` / `notarization_identity_name` are simply unset in
this slice. This is the standard constructor pattern (build unsigned; sign in the release pipeline),
not a stub: the recipe and the artifacts it bundles are real, locked and buildable on the advisory leg.

### Consequences

- **Good:** pin-and-hold is honoured (rendered `@EXPLICIT` locks — no re-solve); the two stacks stay
  isolated (base interpreter + `envs/sidecar`); the installer is offline by construction; the base
  app, the three locks and the required 3-OS matrix are **untouched** (schema-guard + conda-lock-verify
  green — this PR adds only packaging config, a docs page and a workflow); the advisory leg exercises
  the real 3-OS build without ever gating a merge; the contract test keeps the leg honestly advisory.
- **Bad / trade-off:** the full 3-OS *build* is validated only on the non-required leg, not the
  required matrix (identical posture to `deep-gpu.yml`); the tMAVEN wheel must be built from its
  pinned source at build time and offline-installed (a `post_install` step, not a channel package);
  the GUI **desktop menu shortcut** is deferred — there is currently no GUI console-script entry point
  (only the `tether` CLI), so the install-smoke targets the **headless** `tether --version` + the
  sidecar-resolves-offline clause (the roadmap's actual PR-1 acceptance), and a `menuinst` shortcut
  for the PySide6 shell is a follow-up; **signing** is deferred to PR-2.
- **Follow-up:** **PR-2** (`release.yml`) adds Authenticode + notarization signing, SBOM (CycloneDX/Syft
  over both stacks), checksums, provenance/SBOM attestations and the changelog/publish pipeline, driven
  by a signed `v*` tag; a later slice adds the GUI menu shortcut; **PR-6** cuts `v1.0.0`.

## More information

- **No new §11.2 tunable** — this is packaging infrastructure, not a scientific parameter.
- **New files:** `packaging/construct.yaml`, `packaging/scripts/post_install.sh`,
  `packaging/scripts/post_install.bat`, `packaging/README.md`, `.github/workflows/packaging.yml`,
  `docs/packaging.md`; contract clause in `tests/test_marker_contract.py`.
- **Locks consumed (unchanged):** root `conda-lock.yml` (the frozen Tether GUI/runtime installed as
  `extra_envs.tether`) and `sidecar/conda-lock.yml` (installed as `extra_envs.sidecar`), rendered per
  platform at build time. Constructor's own `base` is a live-solved Python + conda bootstrap and is
  not part of the reproducibility bill of materials. `deep/conda-lock.yml` is intentionally **not**
  consumed or bundled by constructor; `release.yml` publishes it only as the standalone
  `deep-conda-lock.yml` reproducibility asset for the optional deep environment, so no deep packages
  enter the desktop installers.
- Related: [ADR-0004](0004-pin-and-hold-dual-lock-isolation.md) (pin-and-hold + dual-lock isolation),
  [ADR-0047](0047-deep-model-optional-stack-and-dataset.md) (optional stack + the non-required advisory
  CI-leg precedent), [ADR-0010](0010-defer-cross-os-gui-handoff.md) (the standalone-tMAVEN GUI
  hand-off deferred to M9 — the sidecar's PyQt5/GUI role the bundled sidecar env preserves).

## Correction — base-env restructure (PR-1b, 2026-07-13)

The first real 3-OS `constructor` build (the advisory `packaging.yml` leg, dispatched for the first
time in PR-1b) surfaced a hard `constructor` constraint that PR-1a's `--render`/schema validation
could not catch, because it is enforced only during the solve, not at parse time:

> `RuntimeError: conda needs to be present in 'base' environment for 'extra_envs' to work`
> (`constructor/fcp.py`: raised unconditionally when `extra_envs` are declared and `conda` is not among
> the base env's package records — the installer uses the **base env's `conda`** to create each extra
> env offline at install time).

PR-1a's recipe made the **PySide6/napari GUI stack the constructor `base`** (from its explicit lock)
and the sidecar the only `extra_envs`. That base has no `conda`, so the build failed. The only ways to
add `conda` to that base would be to put `conda` into the **frozen GUI `conda-lock.yml`** — forcing a
re-solve of the pinned runtime stack and bloating every install with the conda machinery — which
violates pin-and-hold and this ADR's own "base lock untouched" consequence. Rejected.

**Corrected architecture (implemented in PR-1b):** `base` is a **thin `python` + `conda` bootstrap**,
solved fresh from conda-forge at build time (installer plumbing, version-inert — it holds no Tether
runtime code). **Both** pinned runtime stacks are now `extra_envs`, consumed byte-for-byte from their
committed explicit locks: the GUI/compute stack at **`envs/tether`** and the isolated tMAVEN sidecar at
**`envs/sidecar`**. Consequences of the correction:

- **Pin-and-hold is strengthened, not weakened:** both frozen locks are consumed verbatim as `extra_envs`
  (no re-solve); only the throwaway bootstrap `base` is solved live, and it carries nothing that affects
  the app.
- **Isolation is unchanged:** `envs/tether` (PySide6 / current numpy) and `envs/sidecar` (PyQt5 / numpy<2)
  are still two separate interpreters that never share a process ([[ADR-0004]]).
- The **`tether` wheel** is now offline-installed into `envs/tether` (was: `base`); the app launches from
  `envs/tether` (`envs/tether/bin/tether`, or `python -m tether` on Windows). `TETHER_SIDECAR_PYTHON` is
  wired via `envs/tether`'s `activate.d`, resolving the sibling `envs/sidecar` interpreter.
- `initialize_conda: false` is set so the bootstrap `conda` never offers to modify the user's shell — this
  stays a self-contained GUI app, not a conda distribution.
