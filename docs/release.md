# Releasing Tether (signed installers)

Tether ships **signed, self-contained installers** for Windows, macOS and Linux,
built and published by [`.github/workflows/release.yml`](https://github.com/bioedca/tether/blob/main/.github/workflows/release.yml)
(see [ADR-0050](adr/0050-release-pipeline-and-code-signing.md)). The pipeline runs on a
signed `v*` tag: it **verifies** the tag, **builds** the installers (the
[constructor recipe](packaging.md)), **code-signs** them, and **publishes** a GitHub
Release with checksums, a CycloneDX SBOM, the frozen base, sidecar, and deep conda locks,
a Conventional-Commits changelog and a build-provenance attestation. The deep lock is a
standalone reproducibility asset; the deep environment is not bundled into the desktop installers.

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

## Publish the documentation

The documentation site is versioned with [`mike`](https://github.com/jimporter/mike) and
served from the `gh-pages` branch; each build lives under `/tether/<MAJOR.MINOR>/`, with
the `latest` alias and the site default pointing at the current stable tree.

**A stable release publishes the site automatically.** The last step of `release.yml`'s
`release` job dispatches [`.github/workflows/docs.yml`](https://github.com/bioedca/tether/blob/main/.github/workflows/docs.yml)
with the release tag, right after the GitHub Release is created.

That explicit dispatch exists because the obvious mechanism does not work. `docs.yml`
does trigger on `release: [published]`, but `release.yml` creates the Release with the
default `GITHUB_TOKEN`, and GitHub deliberately does **not** start new workflow runs from
events raised by that token. Without the dispatch, `docs.yml` simply never fires on a
release — so the site would stay frozen on whatever was last published by hand.

**Pre-releases do not publish.** A hyphenated tag (`v1.0.0-rc1`) collapses to the same
`1.0` documentation label as the stable tag, so publishing it would repoint `latest` and
the site default at release-candidate docs. `release.yml` skips the dispatch for those
tags and logs a `::notice::` saying so. This means a release candidate produces **no**
`docs.yml` run at all — release-triggered documentation publishing is therefore first
proven by the stable tag, not by the rehearsal.

The site is built **from the release tag**, not from whatever `main` happens to hold. The
dispatch passes `--ref "$TAG"`; without it `gh workflow run` targets the default branch,
and a tag cut a few commits back — or `main` advancing during the ~15-minute build matrix
— would publish unreleased docs under the released version.

Each version's canonical URLs are handled by `mike`, not by `mkdocs.yml`. `mike deploy`
injects its own plugin and rewrites `site_url` to `<site_url>/<version>` at build time, so
the published `1.0` tree carries canonicals and a sitemap under `/tether/1.0/`. That is why
`mkdocs.yml`'s `site_url` stays at the Pages root: pointing it at `/tether/latest/` would
produce `/tether/latest/1.0/…`, which does not exist.

### Manual fallback

If the dispatch fails, or you need to republish, run `docs.yml` yourself:

```bash
gh workflow run docs.yml --ref v1.0.0 -f version=1.0
```

**Pass `--ref` and pass the tag**, exactly as the automatic dispatch does. Without it `gh`
targets the default branch, so you would publish whatever `main` holds right now under a
released version's label — the mistake the manual path exists to recover from. The same
applies to *Actions → docs → Run workflow*: set **Use workflow from** to the release tag,
not `main`.

The `version` input accepts `MAJOR.MINOR`, `MAJOR.MINOR.PATCH` (a leading `v` is stripped,
and the patch component is dropped to give the doc tree) or the literal `dev`. Anything
else — including a four-component `1.0.0.0` or a pre-release `1.0.0-rc1` — is **rejected**
at the version-resolution step rather than silently truncated to a plausible label.

Whatever you publish takes over the `latest` alias and the site default: `docs.yml` always
runs `mike deploy --update-aliases <label> latest` followed by `mike set-default --push
latest`. There is no way to publish a version *without* promoting it, so do not dispatch an
older branch to "just refresh" an old tree.

### Verify it published

```bash
gh api "repos/bioedca/tether/contents/versions.json?ref=gh-pages" \
  --jq '.content' | base64 -d
```

> Keep the quotes. `?` is a glob character in zsh — the default shell on macOS — and an
> unquoted URL fails with `no matches found` before `gh` ever runs.

The new version must appear in the list with `latest` among its `aliases`, e.g.
`[{"version": "1.0", "title": "1.0", "aliases": ["latest"]}]`. Then load
<https://bioedca.github.io/tether/latest/> and confirm the version selector offers the
new version.

### After 1.0 is live: retire the `dev` tree

The site currently carries a placeholder `dev` tree, created by hand before any release
existed. Once `1.0` is published **and** verified as above, it can be removed:

```bash
mike delete --push dev
```

> **Do not run this before `1.0` is live.** `latest` presently aliases `dev`; deleting it
> first takes the published site down until the stable tree replaces it. After deleting,
> re-check that `latest` and the site default both still resolve to `1.0`.

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
