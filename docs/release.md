# Releasing Tether (tag-driven pipeline)

**Who this page is for.** Tether maintainers with write access to `bioedca/tether`; users do
not need this page. To install a published release, follow [Install Tether](install.md).

Tether ships **self-contained installers** for Windows, macOS and Linux,
built and published by [`.github/workflows/release.yml`](https://github.com/bioedca/tether/blob/main/.github/workflows/release.yml)
(see [ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)). The pipeline
runs on a signed `v*` tag: it **verifies** the tag, **builds** the installers (the
[constructor recipe](packaging.md)), **install-smokes** each one on its build runner —
the same offline `packaging/scripts/install_smoke.sh` probe sequence the advisory
[packaging workflow](maintainers/building-installers.md#building-from-source) runs, executed on the exact
bytes about to be uploaded, so a broken installer fails the build instead of shipping —
and **publishes** a GitHub
Release with checksums, a CycloneDX SBOM, the frozen Tether GUI/runtime (`conda-lock.yml`), sidecar
(`sidecar-conda-lock.yml`), and deep (`deep-conda-lock.yml`) source-lock assets, a
Conventional-Commits changelog, and a build-provenance attestation. Constructor consumes the first
two as its `tether` and `sidecar` extra environments; its own `base` is a live-solved Python + conda
bootstrap excluded from that reproducibility bill of materials. The deep lock is standalone; the
deep environment is not bundled into the desktop installers.

The installers are **not OS-code-signed** on any platform, and that is the settled 1.0 position
rather than a pending task — SignPath Foundation declined enrollment on 2026-07-26 and Apple
Developer ID is out of budget, so [ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)
removed the signing legs instead of shipping them dormant.
[Verification](#verify-a-downloaded-installer) is by SHA-256 manifest and build-provenance
attestation, which answers the question a downloader actually has — *is this the file Tether
built?* — without an OS trust prompt. Re-applying when the project's visibility criteria are met is
tracked in [#244](https://github.com/bioedca/tether/issues/244).

**Two different signatures, easily confused.** The release *tag* is SSH-signed and `release.yml`
refuses to build without a GitHub-verified signature on it. That authenticates **who cut the
release**, not the binaries, and it is unaffected by any of the above.

## Cutting a release

1. Ensure `main` is green and releasable. This is enforced, not merely good practice:
   `release.yml` refuses to publish unless every required context has **completed** on the
   tagged commit with a conclusion the guard accepts (`success`, or `skipped` for
   `commitlint`, whose job only runs on pull requests). The decision is made twice — in
   `verify` before the build matrix, and again immediately before the Release is created —
   because check state is not frozen while the ~30–60 minute build runs: the nightly
   sidecar cron can land a fresh `sidecar / parity` run on the very commit being released,
   and a verdict fetched an hour earlier would not see it turn red. Two operational
   consequences follow. A tag cut seconds after a merge is refused until the post-merge run
   finishes on that commit. And because the producing workflows cancel an in-flight run
   when the next merge lands (`cancel-in-progress` keyed on the ref), a commit whose checks
   were cancelled by a following merge can never be released — release from a later commit
   whose checks ran to completion instead.
2. Create a **signed, annotated** tag on the release commit and push it, with the two
   commands below the list:

    **Before pushing the tag, confirm the Zenodo integration is on** for `bioedca/tether`
    (see [Archiving (Zenodo)](#archiving-zenodo)). A Release published while it is off is
    never archived and gets no DOI.

    Tag only commits that already contain the post-merge gate: the pipeline that runs is
    the `release.yml` **in the tagged tree**, so a tag on an older commit executes that
    tree's gate-less definition. **No repository-level control enforces this — the risk is
    accepted** ([#464](https://github.com/bioedca/tether/issues/464), 2026-10-04): a `v*`
    tag ruleset was considered and declined, so it rests on whoever cuts the tag. Before
    tagging, `git merge-base --is-ancestor 6df78e4 <commit>` must exit `0`, where
    `<commit>` is the commit the tag will refer to; `6df78e4` (#461) is the commit that
    added the gate. The check prints nothing whether or not `<commit>` descends from
    `6df78e4`, so read its exit status, not its output. It shows only that descent: it does
    not show that the gate is still present and enabled at `<commit>`. The tag command
    below names no commit, so it tags `HEAD`: run the check with `HEAD` as `<commit>`
    before running either command.

    The tag must have the form `vMAJOR.MINOR.PATCH` (PRD §12.7): three numeric components
    with no leading zeros, optionally followed by a `-<prerelease>` component such as
    `-rc1`, and nothing else — no build metadata (`+…`), and no fourth component, so
    `v1.0.0.0` is rejected. A tag with a `-<prerelease>` component publishes as a GitHub
    **prerelease** and does not repoint the documentation site; that verdict is read from
    the parsed tag's prerelease component, not from a search for a hyphen. The tag must also
    be **annotated** and its signature **verified by GitHub** (your SSH signing key
    registered as a *Signing Key* on the account), and its commit must be on `main`.

    `release.yml`'s `verify` job checks those requirements at two different times. The
    **tag form** is checked on every run, a `dry_run` dispatch included, in the ungated
    `Resolve target tag + publish mode` step. The annotated / GitHub-verified signature /
    commit-on-`main` trio is checked only on a real publish, in the step gated
    `if: steps.resolve.outputs.publish == 'true'`. The post-merge check-state guard from
    step 1 is not behind that gate either, so a dry run evaluates it too.

3. To rehearse without publishing, run the **`release`** workflow via *Actions → release
   → Run workflow* with `dry_run: true` and, as `ref`, a fresh `v*` rc tag cut from the
   `main` you are about to release — it builds, install-smokes, checksums and SBOMs, but
   publishes no Release. The dry run also evaluates the post-merge check guard and
   reports its verdict without blocking. Rehearse a **current** tag only: the dispatch runs `main`'s
   workflow definition against the `ref` tag's tree, so a historical tag — one predating
   the pipeline's checked-in inputs, `packaging/setuptools-compatibility.txt` and
   `packaging/scripts/install_smoke.sh` — fails early on the first absent file. To re-run
   such a tag self-consistently, set **Use workflow from** to the tag itself, the same
   rule the docs dispatch below already states.

The two commands for step 2, run only after the gate check it describes:

```bash
git tag -s v1.0.0 -m "Tether v1.0.0"
git push origin v1.0.0
```

### What a published Release contains: verified `v1.0.0-rc1`

The retained [`v1.0.0-rc1` prerelease](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc1)
is the durable rehearsal record for the first successful end-to-end release run. It was
published on 2026-07-21 with exactly the following 13 project-uploaded assets; the byte
counts are the values reported by GitHub for the published files. The expanded Release
asset list shows 15 downloads because GitHub also adds
[on-demand source archives](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
for the tag as `Source code (zip)` and `Source code (tar.gz)`. Those two generated
archives are not project-uploaded assets and are not part of the inventory below.

| Asset | Recorded size (bytes) |
| --- | ---: |
| `CHANGELOG-v1.0.0-rc1.md` | 1,719 |
| `conda-lock.yml` | 519,875 |
| `SHA256SUMS-linux-64.txt` | 99 |
| `SHA256SUMS-osx-64.txt` | 101 |
| `SHA256SUMS-osx-arm64.txt` | 100 |
| `SHA256SUMS-win-64.txt` | 102 |
| `SHA256SUMS.txt` | 1,112 |
| `sidecar-conda-lock.yml` | 258,926 |
| `Tether-1.0.0-rc1-Linux-x86_64.sh` | 802,018,820 |
| `Tether-1.0.0-rc1-MacOSX-arm64.pkg` | 624,027,165 |
| `Tether-1.0.0-rc1-MacOSX-x86_64.pkg` | 654,541,025 |
| `Tether-1.0.0-rc1-Windows-x86_64.exe` | 786,182,158 |
| `tether-sbom.cyclonedx.json` | 54,219 |

`SHA256SUMS.txt` lists the other 12 project-uploaded assets (it cannot list itself).
It does not cover the two GitHub-generated source archives.

This historical RC predates the current pipeline's addition of
`deep-conda-lock.yml` to release staging, so that file is not part of the 13-asset
record above. Neither the root `LICENSE` nor the root `NOTICE` is a standalone RC1
Release upload. Constructor presents and bundles Tether's root GPL license via its
`license_file`, while the bundled sidecar carries its own GPL text. RC1 did not stage
or package Tether's root `NOTICE`; it remains available in the source repository and
the GitHub-generated source archives.

#### Verify a downloaded installer

Download an installer and its matching platform manifest into the same directory,
then verify it before running it.

##### Linux

```bash
sha256sum -c SHA256SUMS-linux-64.txt
```

##### macOS (Apple silicon)

```bash
shasum -a 256 -c SHA256SUMS-osx-arm64.txt
```

##### macOS (Intel)

```bash
shasum -a 256 -c SHA256SUMS-osx-64.txt
```

##### Windows PowerShell

```powershell
$expected = ((Get-Content .\SHA256SUMS-win-64.txt) -split '\s+')[0]
$actual = (Get-FileHash -Algorithm SHA256 `
  .\Tether-1.0.0-rc1-Windows-x86_64.exe).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
```

GitHub's build-provenance attestation supplies a second, independent check for each
installer. To verify the complete four-installer set after downloading it, run:

```bash
verify_rc1() {
  gh attestation verify "$1" \
    --repo bioedca/tether \
    --signer-workflow bioedca/tether/.github/workflows/release.yml \
    --source-ref refs/tags/v1.0.0-rc1 \
    --source-digest 1ba112683a0f2a5ba842e39893fd757bff2d18b3
}

verify_rc1 Tether-1.0.0-rc1-Linux-x86_64.sh
verify_rc1 Tether-1.0.0-rc1-MacOSX-arm64.pkg
verify_rc1 Tether-1.0.0-rc1-MacOSX-x86_64.pkg
verify_rc1 Tether-1.0.0-rc1-Windows-x86_64.exe
```

The published attestation names all four installers as subjects. Each invocation
independently hashes its local installer and enforces the GitHub repository, signer
workflow, source tag, and source commit shown above; verifying one local file does not
checksum the other three.

#### Documentation status

Every RC installer is unsigned, on all three platforms, and so is every 1.0 installer that
follows it ([ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)).
The SHA-256 manifests and the GitHub build-provenance attestation verified above are the
integrity anchor.

The prerelease is intentionally retained as this documented evidence set. Pre-releases
also intentionally do **not** publish the documentation site: the RC release job logged
its pre-release skip notice and created no `docs.yml` run. Release-triggered
documentation publishing remains unproven until the stable `v1.0.0` tag.

## Archiving (Zenodo)

Every GitHub Release published while the Zenodo–GitHub integration is on, prereleases
included, is archived on Zenodo, and Zenodo mints a DOI for it. A Release published while
the integration was off is not archived until it is published again (see the recovery
below), so not every historical release has a DOI. Tether therefore has two kinds of
DOI, and they go in different places:

| DOI | Resolves to | Where it is written |
|---|---|---|
| **Concept DOI** `10.5281/zenodo.23306397` | the newest archived version, whichever that is | `CITATION.cff` `doi:`, `README.md`, [Citing Tether](citing.md). It never changes. |
| **Version DOI** | one release | only its own Zenodo record (linked from the concept record). Never hand-copied into the repository. |

The ordering below is load-bearing and cannot be repaired by editing files afterwards. Follow
it for every release:

1. **The integration must be on before the tag is pushed.** Zenodo archives a Release only
   if its webhook is active at the moment the Release is *published*. Check
   <https://zenodo.org/account/settings/github/> (the `bioedca/tether` toggle is on) and the
   repository's *Settings → Webhooks* (a `zenodo.org` hook is listed) before step 2 of
   [Cutting a release](#cutting-a-release).
2. **No DOI can be reserved in advance.** Through the GitHub integration Zenodo mints a
   version's DOI when its Release is published, after the tagged commit already exists, so
   no tagged commit can contain its own version DOI. That is why `CITATION.cff` carries the
   concept DOI instead.
3. **In the release commit**, set `CITATION.cff` `version:` to the tag without its leading
   `v` and `date-released:` to the release date (ISO 8601). Leave `doi:` alone.
4. **After the Release is published**, open the concept DOI and check that the new version
   appears in the record's version list. Zenodo reads the archive's metadata from
   `CITATION.cff` in the tagged tree.

**If the newest Release was published while the integration was off**, it can still be
archived without a new tag: switch the Release to a draft and publish it again. Publishing
fires the event Zenodo listens for, and `release.yml` does not run, because it is
triggered by tag pushes, not by Release events. For a stable Release, `docs.yml` also runs
on that event and redeploys the same documentation it already published; for a prerelease
it skips.

Use this **only for the newest Release**. Re-publishing an older one has side effects that
editing files cannot undo: Zenodo orders versions by when their records were created, so
the backfilled release would become the version the concept DOI resolves to; GitHub may
mark it as the repository's *Latest* release; and `docs.yml` would point the `latest`
documentation at it. An older Release published while the integration was off stays
unarchived, as `v1.0.0-rc1` does.

```bash
gh release edit <tag> -R bioedca/tether --draft=true
gh release edit <tag> -R bioedca/tether --draft=false   # add --prerelease for an rc tag
```

This is how the archive was started: the integration was switched on on 2026-10-11, after
`v1.0.0-rc2` had been published, and re-publishing `v1.0.0-rc2` created the Zenodo record
and the concept DOI. `v1.0.0-rc1` was never archived.

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

## OS code-signing: why there is none

Windows, macOS and Linux are all the same case, so this section replaced the three that
used to differ. **No installer is OS-code-signed**, the per-file and combined
**`SHA256SUMS`** plus the build-provenance attestation are the integrity anchor everywhere,
and users get a SmartScreen or Gatekeeper warning on Windows and macOS.

[ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) records
the decision and the priced alternatives so they are not re-researched. In short:

- **SignPath Foundation declined** `bioedca/tether` on 2026-07-26 — the programme wants
  public-visibility signals (stars, forks, contributors, external references, institutional
  backing) a pre-1.0 project by one maintainer does not have. A paid subscription was offered.
- **Apple Developer ID** is out of budget at $99/yr, and enabling it was never one setting
  away: notarization needs every Mach-O binary in the bundled conda payload
  `codesign`ed with a *Developer ID Application* identity and a hardened runtime, and the
  removed step signed only the outer `.pkg`.

The legs were **removed rather than disabled**. Both were written green-before-secrets and
neither ever executed once — `v1.0.0-rc1`'s only annotation was the "SignPath not
configured" warning — and a dormant leg is indistinguishable from a working one in a green
run. `tests/test_marker_contract.py` holds the shape: one guard fails if a signing token
returns to `release.yml`, another fails if the manifests or attestation that replaced it are
dropped.

Re-applying once the visibility criteria are met is tracked in
[#244](https://github.com/bioedca/tether/issues/244). Reversing this is a superseding ADR
that updates those guards with it, not an edit.
