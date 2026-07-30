<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0059 — Ship v1.0.0 unsigned, with SHA-256 manifests + build provenance as the integrity anchor

- **Status:** accepted (supersedes the code-signing half of
  [ADR-0050](0050-release-pipeline-and-code-signing.md))
- **Date:** 2026-07-30
- **PRD anchor:** §9 M9 (packaging & docs), §12.7 (release pipeline), §4.1 (installers), NFR-PKG
- **Deciders:** bioedca
- **Milestone:** M9

## Context and problem statement

[ADR-0050](0050-release-pipeline-and-code-signing.md) planned to code-sign the installers through
the **SignPath Foundation** programme on Windows and a gated Developer ID path on macOS, and wrote
both legs "green-before-secrets" so the pipeline could merge before any credential existed.

The credentials never arrived. **SignPath Foundation declined enrollment for `bioedca/tether` on
2026-07-26**: the programme requires public-visibility signals — stars, forks, contributors,
external references, institutional backing, sustained engagement — that a pre-1.0 project by one
maintainer does not yet have. A paid subscription was offered instead. The Apple leg is separately
out of budget, and ADR-0050 had already flagged that enabling it needs work that does not exist:
notarization requires every Mach-O binary in the bundled conda payload to be
Developer-ID-Application-signed with a hardened runtime, and the step only ever signed the outer
`.pkg`.

So both legs are **provably inert**, and inert is worse than absent. `v1.0.0-rc1` (published
2026-07-21, run `29791767414`, all six jobs green) built four installers, the per-platform and
combined `SHA256SUMS`, a CycloneDX SBOM, a changelog and the three frozen locks; the
`build+sign / win-64` leg's *only* annotation was `SignPath not configured
(vars.SIGNPATH_ORGANIZATION_ID unset) — the Windows installer ships UNSIGNED`. A green run is
exactly what dormant CI produces, so nothing in the pipeline's own output distinguishes "signing
works" from "signing has never once executed". The job was even *named* `build+sign`.

Does v1.0.0 wait for signing, ship the dormant legs, or ship unsigned and say so?

## Decision drivers

- **Green-before-secrets has an expiry.** The discipline that let ADR-0050 merge is sound; leaving
  the result in the tree after the secrets are known not to be coming converts it into a false
  promise that CI cannot flag.
- **A user's actual question is "is this file the one Tether built?"** — an authenticity question,
  not an OS-trust-prompt question. Code-signing answers it *and* suppresses the prompt; provenance
  answers it alone.
- **What already works on a real artifact beats what is one setting away.** The attestation is not
  aspirational: `gh api repos/bioedca/tether/attestations/sha256:dc49e0b2766b…` returns one in-toto
  attestation with a certificate for `Tether-1.0.0-rc1-Windows-x86_64.exe`.
- **Cost and automation, on a hosted runner, by one unpaid maintainer.**
- **The documentation must be able to state the truth simply.** "Unsigned; here is how to verify"
  is a sentence a user can act on. "Signed where configured" is not.

## Considered options

Priced during this decision (July 2026), recorded so they are not re-researched:

| Option | Cost | Automates on a hosted runner? |
| --- | --- | --- |
| **Ship unsigned; manifests + provenance are the anchor** | $0 | Yes — already running |
| Azure Artifact Signing (ex-Trusted Signing) | ~$9.99/mo Basic | Yes — first-class GH Action; individual identity validation GA in US/Canada |
| Certum Open Source Code Signing | ~$50–70/yr | **No** — SimplySign needs a phone-authenticated session per boot, so it needs a dedicated self-hosted signing agent. Max validity also drops to 460 days from 2026-03-01 |
| SignPath paid subscription | not published | Yes, but sales-gated |
| Apple Developer ID | $99/yr | Only after the payload deep-sign work lands |
| Keep the legs, ship them dormant | $0 | They do not run at all |

## Decision outcome

Chosen option: **"ship unsigned; manifests + provenance are the anchor"**, and **remove** both
signing legs rather than ship them dormant.

1. `v1.0.0` installers are **not** OS-code-signed on any platform. Windows SmartScreen and macOS
   Gatekeeper will warn, and that is the permanent, documented 1.0 state.
2. The integrity anchor is the **SHA-256 manifests** published with the Release and the
   **`actions/attest-build-provenance`** in-toto attestation over every installer, verifiable by a
   user with `gh attestation verify --repo bioedca/tether <file>`.
3. The SignPath action, the Apple keychain/`productsign`/`notarytool`/`stapler` step, every
   `vars.SIGNPATH_*` and `vars.APPLE_SIGNING_*` reference, and both "ships UNSIGNED" warning steps
   are **deleted** from `.github/workflows/release.yml`. The job is renamed `build`, because it
   builds.
4. **The tag signature is a different mechanism and stays.** The `verify` job still requires an
   annotated, GitHub-verified signature on the release tag. It authenticates *who cut the release*,
   not the binaries; the two are routinely confused and this record separates them on purpose.

Removing rather than disabling is the whole point. A disabled leg is indistinguishable in a green
run from a working one, which is the failure mode ADR-0050's own rc1 evidence demonstrates. Two
guards in `tests/test_marker_contract.py` hold the shape: one asserts no signing token returns to
`release.yml`, the other asserts the replacement anchor is still wired — because a guard on the
absence alone would stay green if the attestation were deleted too, which is the worse outcome.

### Consequences

- **Good.** The pipeline's output now means what it says; there is no dormant code claiming a
  capability the project does not have. The user-facing pages can make one true, actionable claim.
  Verification works today, on published artifacts, at no cost.
- **Bad / trade-off.** Users get an OS warning on first run and must click through it, which costs
  trust at exactly the moment a new user is deciding whether to continue — the single largest
  usability cost in the 1.0 install path. `gh attestation verify` also requires the `gh` CLI, so the
  verification story is weaker for a user who has not got it, and provenance is strictly an
  *authenticity* answer: it does not suppress the prompt and does not assert the software is safe.
- **Reversible, deliberately.** Nothing here is a permanent position on code-signing. Re-applying
  when the visibility criteria are met is tracked in
  [#244](https://github.com/bioedca/tether/issues/244); Azure Artifact Signing is the cheapest
  automatable path if a small recurring cost becomes acceptable. Reversing this is a superseding
  ADR that updates the guards with it, not an edit.
- **Follow-up.** The user-facing pages (`README.md`, `docs/packaging.md`, `docs/troubleshooting.md`,
  `docs/compatibility.md`) are swept by [#242](https://github.com/bioedca/tether/issues/242), which
  cites this record; `docs/privacy.md` by
  [#243](https://github.com/bioedca/tether/issues/243). Both were deliberately sequenced behind this
  ADR so they cite an accepted decision rather than one in flight.

## More information

- [ADR-0050](0050-release-pipeline-and-code-signing.md) — the superseded signing plan, kept intact
  as the record of what was decided in July 2026.
- [ADR-0049](0049-m9-packaging-constructor-architecture.md) — the constructor recipe, which has
  always built the installers unsigned. Its "signing is deferred to PR-2" text is a true record of
  what was decided then and is not rewritten.
- `.github/workflows/release.yml` — the pipeline this record governs.
- `docs/release.md` — the maintainer-facing verification story.
- [GitHub artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds)
  — what `attest-build-provenance` produces and how `gh attestation verify` consumes it.
