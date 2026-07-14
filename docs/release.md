# Releasing Tether (signed installers)

Tether ships **signed, self-contained installers** for Windows, macOS and Linux,
built and published by [`.github/workflows/release.yml`](https://github.com/bioedca/tether/blob/main/.github/workflows/release.yml)
(see [ADR-0050](adr/0050-release-pipeline-and-code-signing.md)). The pipeline runs on a
signed `v*` tag: it **verifies** the tag, **builds** the installers (the
[constructor recipe](packaging.md)), **code-signs** them, and **publishes** a GitHub
Release with checksums, a CycloneDX SBOM, the frozen conda locks, a Conventional-Commits
changelog and a build-provenance attestation.

Code-signing is **gated on repository variables**, so the pipeline is green before any
signing credential exists. Until you complete the setup below, the installers ship
**unsigned** (a build warning says so) — everything else still works.

## Cutting a release

1. Ensure `main` is green and releasable.
2. Create a **signed, annotated** tag on the release commit and push it:

    ```bash
    git tag -s v1.0.0 -m "Tether v1.0.0"
    git push origin v1.0.0
    ```

    The tag must be **annotated** and its signature **verified by GitHub** (your SSH
    signing key registered as a *Signing Key* on the account), and its commit must be on
    `main` — `release.yml`'s `verify` job enforces all three.
3. To rehearse without publishing, run the **`release`** workflow via *Actions → release
   → Run workflow* with `ref: v1.0.0-rc1` and `dry_run: true` — it builds, signs (where
   configured), checksums and SBOMs, but publishes no Release.

## Windows signing — SignPath (free for open source)

SignPath's Foundation program signs open-source releases at no cost.

1. Enroll `bioedca/tether` at [signpath.io](https://signpath.io/open-source) and create a
   **project** and a **signing policy** (e.g. `release-signing`).
2. In the repo, add these **variables** (Settings → Secrets and variables → Actions →
   *Variables*) and one **secret** (*Secrets*):

    | Kind | Name | Value |
    | --- | --- | --- |
    | Variable | `SIGNPATH_ORGANIZATION_ID` | your SignPath organization id |
    | Variable | `SIGNPATH_PROJECT_SLUG` | the project slug (e.g. `tether`) |
    | Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | the policy slug (e.g. `release-signing`) |
    | Secret | `SIGNPATH_API_TOKEN` | a SignPath API token |

    The Windows signing step activates the moment `SIGNPATH_ORGANIZATION_ID` is set
    (variables — not secrets — because GitHub forbids reading a secret in an `if:`).

## macOS signing — Apple Developer ID (optional, deferred)

Wired but **disabled by default**. To enable, you need an Apple Developer Program
membership, a **Developer ID Installer** certificate (`.pkg` installers are signed with
*Installer*, not *Application*), and an App Store Connect API key for notarization. Then:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `APPLE_SIGNING_ENABLED` | `true` |
| Variable | `APPLE_SIGNING_IDENTITY` | the Developer ID Installer identity name |
| Secret | `APPLE_CERTIFICATE_P12_BASE64` | base64 of the `.p12` cert |
| Secret | `APPLE_CERTIFICATE_PASSWORD` | the `.p12` password |
| Secret | `APPLE_NOTARY_KEY_ID` | App Store Connect key id |
| Secret | `APPLE_NOTARY_ISSUER_ID` | App Store Connect issuer id |
| Secret | `APPLE_NOTARY_KEY_P8_BASE64` | base64 of the `.p8` notary key |

> **Prerequisite before enabling.** The workflow currently `productsign`s only the outer
> `.pkg`. Apple **notarization** additionally requires every Mach-O binary in the bundled
> conda payload to be `codesign`ed with a *Developer ID Application* identity **and a
> hardened runtime** — otherwise `notarytool` returns *Invalid* and `stapler` fails. Add a
> recursive payload-signing pass (or use constructor's native `signing_identity_name` /
> `notarization_identity_name`) before flipping `APPLE_SIGNING_ENABLED` to `true`.

## Linux

No OS-level signing; the per-file and combined **`SHA256SUMS`** plus the build-provenance
attestation are the integrity anchor.
