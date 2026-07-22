<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0050 — Release pipeline + code-signing (tag-driven, SignPath for Windows, gated Apple)

- **Status:** accepted
- **Date:** 2026-07-14
- **Deciders:** bioedca
- **PRD anchor:** §9 M9 (packaging & docs), §12.7 (release pipeline), §4.1 (installers)
- **Milestone:** M9

## Context and problem statement

M9 must publish Tether as **signed, self-contained desktop installers** driven by a
signed `v*` tag, with provenance and a bill-of-materials (PRD §9 M9, §12.7). The
constructor recipe ([ADR-0049](0049-m9-packaging-constructor-architecture.md)) already
builds the 3-OS installers **unsigned**; this decision homes the release *pipeline* and,
specifically, **how the installers get code-signed** without the maintainer holding a
traditional certificate. How do we sign a GPL open-source project's installers, keep the
merge gate green before any signing secret exists, and never let the heavy release job
become a required PR check?

## Decision drivers

- **OSS-appropriate, low-cost signing.** Trusted OV/EV Authenticode certs now require a
  hardware/cloud HSM; a raw `.pfx` in a secret is no longer issuable for a trusted cert.
- **Green-before-secrets.** The pipeline (and the PR that lands it) must pass CI before
  any signing credential is provisioned — signing is enrolled by the maintainer later.
- **Never a required/gating check.** Building + signing a full napari/PySide6 + sidecar
  installer is heavy and per-OS; it must not sit in the required PR matrix (the
  `deep-gpu.yml` / `packaging.yml` posture, [ADR-0047](0047-deep-model-optional-stack-and-dataset.md) / ADR-0049).
- **Provenance travels with the artifact** — the release-suite counterpart of the
  data-model provenance invariant ([ADR-0001](0001-provenance-first-data-model.md)).

## Considered options

- **A — SignPath (free for OSS) for Windows; gated Apple Developer ID for macOS.**
- **B — Azure Trusted Signing for Windows.** Modern cloud HSM, but needs a paid Azure
  subscription + verified identity — heavier onboarding for an academic lab.
- **C — Traditional `.pfx`/token secret + `signtool`.** No longer issuable as a *trusted*
  OV/EV cert (HSM requirement); only a self-signed/internal cert would fit — no real
  SmartScreen benefit.
- **D — Ship everything unsigned.** Fails the §9 M9 "signed installers" requirement.

## Decision outcome

Chosen: **Option A.** A tag-driven `release.yml` (`verify` → `build`+`sign` → `release`)
with signing **gated on non-secret repository variables** so the pipeline is inert-but-wired
until the maintainer enrolls:

- **Windows — SignPath (`signpath/github-action-submit-signing-request`).** After
  `constructor` builds the `.exe`, it is uploaded as a workflow artifact and submitted to
  SignPath, which returns the signed `.exe`. The step runs **only when
  `vars.SIGNPATH_ORGANIZATION_ID` is set** (a repository *variable* — `secrets` cannot be
  read in an `if:`), with the API token as `secrets.SIGNPATH_API_TOKEN`. Unset ⇒ the `.exe`
  ships unsigned with a build warning.
- **macOS — Developer ID `productsign` + `notarytool` staple**, gated on
  `vars.APPLE_SIGNING_ENABLED == 'true'`. Until an Apple Developer account + a **Developer
  ID Installer** cert + an App Store Connect notary key exist as secrets, the `.pkg` ships
  unsigned (Gatekeeper warns) — the maintainer's explicit "wire it, enable later" choice.
- **Linux — no OS-level signing**; the SHA-256 checksums + the build-provenance
  attestation are the integrity anchor.
- **Tag verification without shipping keys to the runner.** `verify` requires an
  **annotated** tag whose signature GitHub reports **verified** (via the REST API — GitHub
  holds bioedca's registered SSH signing key), and whose commit is an ancestor of
  `origin/main`. A `workflow_dispatch` **dry-run** builds + signs + checksums but does not
  publish, satisfying the §9 M9 "dry-run on a pre-release tag first" clause.
- **Release assets** — the signed installers, per-platform + combined `SHA256SUMS`, a CycloneDX
  SBOM, and the frozen Tether GUI/runtime (`conda-lock.yml`), sidecar
  (`sidecar-conda-lock.yml`), and deep (`deep-conda-lock.yml`) source-lock assets (the authoritative
  reproducibility bill of materials), a Conventional-Commits changelog, and an
  `actions/attest-build-provenance` attestation over the installers. Constructor consumes rendered
  forms of the GUI/runtime and sidecar locks as its `tether` and `sidecar` extra environments; its
  own `base` is the live-solved Python + conda bootstrap and is excluded from that bill of materials.
  The deep lock recreates ADR-0047's optional environment and does not place that environment in the
  installers.

### Consequences

- Good: the full pipeline lands + passes CI **before** any secret exists; the maintainer
  turns signing on by enrolling in SignPath and adding the vars/secret (documented in
  `docs/release.md`) — no code change. Third-party actions are SHA-pinned (supply-chain).
- Bad / trade-off: SignPath requires a one-time OSS-project enrollment; the macOS path is
  authored-but-inert until an Apple Developer account is funded. The heavy build cannot be
  end-to-end-validated in the PR (no secrets, no signed tag) — real validation is the
  maintainer's `workflow_dispatch` dry-run, then the `v1.0.0` cut (M9 PR-6).
- Follow-up: `tests/test_marker_contract.py` locks the release shape (no
  `pull_request`/`merge_group` trigger → never a required check; tag-filtered `push`; no
  bare `exit` under `bash -el`). The GUI menu shortcut is a later slice. **macOS enablement
  is a real follow-up, not a flag flip:** the current step `productsign`s only the outer
  `.pkg`; notarization also requires the bundled conda payload's Mach-O binaries to be
  *Developer ID Application*-signed with a hardened runtime (else `notarytool` → *Invalid*),
  so a payload deep-sign (or constructor's native `signing_identity_name`/
  `notarization_identity_name`) must land before `APPLE_SIGNING_ENABLED` is turned on.

## More information

- Recipe: [ADR-0049](0049-m9-packaging-constructor-architecture.md) (the installers this
  pipeline signs); `packaging/construct.yaml`; `.github/workflows/packaging.yml` (the
  advisory build leg this pipeline's `build` job mirrors).
- SignPath GitHub Action: `docs.signpath.io/trusted-build-systems/github` (upload artifact
  → submit `github-artifact-id` → download signed artifact).
- Maintainer setup (variables/secrets + SignPath enrollment): `docs/release.md`.
