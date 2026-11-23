---
description: "For maintainers: how a version tag builds and publishes the Tether installers and this site, and how Zenodo archives each release."
---

# Releasing Tether (tag-driven pipeline)

**Who this page is for.** Tether maintainers with write access to `bioedca/tether`, who cut a
release. It assumes that you can sign a git tag and run `gh`. Users do not need this page. To
install a published release, follow [Install Tether](install.md).

<!-- markdownlint-configure-file { "MD046": { "style": "fenced" } } -->

Tether ships **self-contained installers** for Windows, macOS and Linux.
[`.github/workflows/release.yml`](https://github.com/bioedca/tether/blob/main/.github/workflows/release.yml)
builds and publishes them
([ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)). The pipeline
runs on a signed `v*` tag, and it does four things:

1. It **verifies** the tag.
2. It **builds** the installers with the [constructor recipe](packaging.md).
3. It **install-smokes** each installer on its build runner. This is the same offline
   `packaging/scripts/install_smoke.sh` probe sequence that the advisory
   [packaging workflow](maintainers/building-installers.md#building-from-source) runs. Here it
   runs on the exact bytes that are about to be uploaded, so a broken installer fails the build
   instead of shipping.
4. It **publishes** a GitHub Release.

The Release carries these assets:

- the checksums,
- a CycloneDX SBOM,
- the frozen source-lock assets of the Tether GUI and runtime (`conda-lock.yml`), the sidecar
  (`sidecar-conda-lock.yml`) and deep (`deep-conda-lock.yml`),
- a Conventional-Commits changelog, which is the [change record](#change-record),
- a build-provenance attestation.

Constructor consumes the first two locks as its `tether` and `sidecar` extra environments. Its
own `base` is a live-solved Python and conda bootstrap. That bootstrap is outside the
reproducibility bill of materials. The deep lock is standalone, and the desktop installers do not
bundle the deep environment.

The installers are **not OS-code-signed** on any platform. That is the settled 1.0 position, not
a pending task. SignPath Foundation declined enrollment on 2026-07-26, and Apple Developer ID is
out of budget. So [ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)
removed the signing legs instead of shipping them dormant.

[Verification](#verify-a-downloaded-installer) uses the SHA-256 manifest and the build-provenance
attestation. They answer the question that a downloader actually has, without an OS trust
prompt: *is this the file Tether built?*
Issue [#244](https://github.com/bioedca/tether/issues/244) tracks a new application when the
visibility criteria of the project are met.

**Two different signatures, easily confused.** The release *tag* is SSH-signed. `release.yml`
refuses to build without a signature on it that GitHub verified. That signature authenticates
**who cut the release**, not the binaries. None of the above changes it.

## Cutting a release

1. Confirm that `main` is green and releasable.

    Each required check of the commit that you will tag shows a completed run, with no red one.

    **Why.** This is enforced, not merely good practice. `release.yml` refuses to publish unless
    every required context has **completed** on the tagged commit. The conclusion must be one that
    the guard accepts: `success`, or `skipped` for `commitlint`, whose job runs only on pull
    requests.

    The guard decides twice: in `verify` before the build matrix, and again immediately before
    it creates the Release. The reason is that the check state is not frozen during the build of
    about 30 to 60 minutes. The nightly sidecar cron can land a fresh `sidecar / parity` run on
    the commit being released. A verdict fetched an hour earlier would not see that run turn red.

    Two consequences follow. First, the guard refuses a tag cut seconds after a merge until the
    post-merge run finishes on that commit. Second, the producing workflows cancel an in-flight
    run when the next merge lands (`cancel-in-progress` keyed on the ref). So a commit whose
    checks a following merge canceled can never be released. Release from a later commit whose
    checks ran to completion instead.

2. **Read every user page before the tag, and record the read.** A named person does this
   step. An AI tool cannot do this step. AI tools draft the user pages, and the review lane of a
   pull request has only AI reviewers. This read is part of the release sign-off that the agent
   contract gives to a person. Do this step for each release, and for the first time for
   `v1.0.0`.

    1. **The reader.** One named person reads all the pages. Write that person's name in the
       record.
    2. **The pages.** Read each page in the `Using Tether` group of the `nav:` list in
       `mkdocs.yml`, and `README.md`. Read the pages at the commit that you will tag.
    3. **What to check.** On each page, check these three items:
        - Each instruction is true of the application at that commit.
        - Each warning comes before the step that it protects.
        - The text obeys the [writing style for documentation](maintainers/writing-style.md).

        Fix each problem through a pull request. Then read the changed pages again, as item 5
        tells you.

    4. **The record.** Write one row in the
       [record of user-page reads](#record-of-user-page-reads). The row gives the release,
       the date, the reader, the commit that the reader read, and the pages read. The table is
       the only place for the record.

    5. **A page that changes after the read.** Before you tag, find the files that changed
       after the commit that was read. Run
       `git diff --name-only <read-commit> HEAD -- README.md docs/ mkdocs.yml` for this.

        Read each changed user page again. If `mkdocs.yml` changed, compare its `Using Tether`
        group with the group at the commit that was read. Read each page that is new in the
        group. Add a row for this second read. The row names the pages that you read again. The
        commit that writes a row changes no user page, so it does not start a new read.

3. Confirm that the Zenodo integration is on for `bioedca/tether`.

    [Archiving (Zenodo)](#archiving-zenodo) tells you where to look. The `bioedca/tether` toggle
    is on, and a `zenodo.org` webhook is listed.

    **Why.** Zenodo does not archive a Release that is published while the integration is off,
    and that Release gets no DOI. Afterwards you can recover only the newest Release. Switch it
    to a draft, and publish it again once the integration is on.

4. Confirm that `HEAD`, the commit that you will tag, contains the post-merge gate.

    <!-- markdownlint-disable MD046 -->

        git merge-base --is-ancestor 6df78e4 HEAD && echo "gate present"

    The command prints `gate present`. If it prints nothing, do not tag this commit.

    **Why.** The pipeline that runs is the `release.yml` **in the tagged tree**. So a tag on an
    older commit runs the definition of that tree, which has no gate. `6df78e4` (#461) is the
    commit that added the gate.

    The check passes only when its exit status is `0`. The check
    itself prints nothing either way, so read the exit status, not the output. The `echo` above
    reads it for you.

    The check shows only that `HEAD` descends from `6df78e4`. It does not show that the gate is
    still present and enabled at `HEAD`. The tag command in step 6 names no commit, so it tags
    `HEAD`. That is why the check uses `HEAD`.

    **No repository-level control enforces this. The risk is accepted**
    ([#464](https://github.com/bioedca/tether/issues/464), 2026-10-04). A `v*` tag ruleset was
    considered and declined, so the check rests on whoever cuts the tag.

5. Choose a tag name of the form `vMAJOR.MINOR.PATCH` (PRD §12.7).

    The name obeys each of these rules:

    - It has three numeric components with no leading zeros.
    - An optional `-<prerelease>` component, such as `-rc1`, can follow them.
    - Nothing else follows. There is no build metadata (`+…`) and no fourth component, so
      `v1.0.0.0` is rejected.
    - The `-<prerelease>` component follows SemVer 2.0.0 §9. It is one or more identifiers
      separated by dots, and none of them is empty.
    - Each identifier is a number with no leading zero. Or it is a run of letters, digits and
      hyphens that holds at least one letter or hyphen.

    So `-rc1`, `-rc.1` and `-alpha-1` are accepted. `-01`, `-rc.01`, `-rc..1`, `-.rc1` and
    `-rc1.` are rejected.

    Hatch-vcs reads the tag with PEP 440 to set the package version. The release check rejects a
    tag that PEP 440 cannot parse, and a tag that PEP 440 reads as a **post-release**. PEP 440
    cannot parse `v1.0.0-foo`. It reads `v1.0.0-0` as `1.0.0.post0`, which sorts *after*
    `1.0.0`. Use a prerelease form such as `-rc1`, `-a1`, `-b2` or `-dev`.

    A tag with a `-<prerelease>` component publishes as a GitHub **prerelease**. It does not
    repoint the documentation site. The pipeline reads that verdict from the prerelease
    component of the parsed tag, not from a search for a hyphen.

6. Create a **signed, annotated** tag on the release commit.

        git tag -s v1.0.0 -m "Tether v1.0.0"

    `git cat-file -t v1.0.0` then prints `tag`, which shows that the tag is annotated.

    The tag must also meet three requirements. It is **annotated**. Its signature is **verified
    by GitHub**, so your SSH signing key must be registered as a *Signing Key* on the account.
    And its commit is on `main`.

7. Push the tag only after steps 1 to 6 pass. The push starts the pipeline that publishes the
   Release, and Zenodo archives a published Release with a DOI. Push the tag:

        git push origin v1.0.0

    <!-- markdownlint-enable MD046 -->

    A `release` workflow run starts for the tag, in the Actions tab of the repository.

    **What `verify` checks.** The `verify` job of `release.yml` checks the requirements of steps 5 and 6 at two
    different times. It checks the **tag form** on every run, a `dry_run` dispatch included. That
    check is in the ungated `Resolve target tag + publish mode` step.

    It checks the annotated tag,
    the signature that GitHub verified and the commit on `main` only on a real publish. Those
    checks are in the step gated `if: steps.resolve.outputs.publish == 'true'`. The check-state
    guard of step 1 is not behind that gate either, so a dry run evaluates it too.

8. After the Release is published, move [Install Tether](install.md) to the new release.

    Move every file name, release link, tag ref and `tether --version` line, and the
    `--source-digest`.

9. Set `INSTALL_RELEASE` and `INSTALL_RELEASE_COMMIT` in `tests/test_docs_install_release.py` to
   the same release.

    The test passes again. It fails when the install page names two releases at once.

### Rehearse a release without publishing

Rehearse on a fresh `v*` rc tag cut from the `main` that you are about to release. The rehearsal
builds, install-smokes, checksums and SBOMs, but it publishes no Release. It also evaluates the
post-merge check guard, and it reports the verdict of that guard without blocking.

1. Open *Actions → release → Run workflow*.
2. Set `dry_run` to `true`.
3. Set `ref` to the fresh rc tag.
4. Start the run.

Rehearse a **current** tag only. The dispatch runs the workflow definition of `main` against the
tree of the `ref` tag. A historical tag predates the checked-in inputs of the pipeline,
`packaging/setuptools-compatibility.txt` and `packaging/scripts/install_smoke.sh`. So it fails
early on the first absent file. To re-run such a tag self-consistently, set **Use workflow from**
to the tag itself. The docs dispatch below states the same rule.

### Record of user-page reads

Step 2 of [Cutting a release](#cutting-a-release) writes a row in this table. Each row records
one read of the user pages before a release tag. The first read fills the `v1.0.0` row. Each
later read adds a row.

The install-page update above comes after the tag. The read for the next release includes it.

| Release | Date | Reader | Commit read | Pages read |
|---|---|---|---|---|
| `v1.0.0` | | | | Not read yet |

### What a published Release contains: verified `v1.0.0-rc1`

The retained [`v1.0.0-rc1` prerelease](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc1)
is the durable rehearsal record for the first successful end-to-end release run. It was
published on 2026-07-21 with exactly the 13 project-uploaded assets below. The byte counts are
the values that GitHub reports for the published files.

The expanded Release asset list shows 15 downloads. GitHub also adds
[on-demand source archives](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
for the tag as `Source code (zip)` and `Source code (tar.gz)`. Those two generated archives are
not project-uploaded assets, and they are not part of the inventory below.

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

`SHA256SUMS.txt` lists the other 12 project-uploaded assets, because it cannot list itself. It
does not cover the two source archives that GitHub generates.

This historical RC predates the addition of `deep-conda-lock.yml` to the release staging of the
current pipeline. So that file is not part of the 13-asset record above. Neither the root
`LICENSE` nor the root `NOTICE` is a standalone RC1 Release upload.

Constructor presents and bundles the root GPL license of Tether through its `license_file`. The
bundled sidecar carries its own GPL text. RC1 did not stage or package the root `NOTICE` of
Tether. That file stays available in the source repository and in the source archives that GitHub
generates.

#### Verify a downloaded installer

Download an installer and its matching platform manifest into the same directory. Then verify
the installer before you run it.

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

The build-provenance attestation of GitHub gives a second, independent check for each
installer. To verify the complete set of four installers after you download it, run:

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

The published attestation names all four installers as subjects. Each invocation hashes its
local installer independently. It enforces the GitHub repository, the signer workflow, the source
tag and the source commit shown above. Verifying one local file does not checksum the other
three.

##### Offline, the way Tether's updater verifies

The commands above let `gh` fetch the attestation and the trusted root, so they need a
signed-in `gh`. Tether's updater (`tether.gui.update_verify`, #333) fetches both itself, with
no token. Then it runs `gh` with no network at all. To do the same by hand, set `$digest` to
the installer's SHA-256 from the manifest:

```bash
curl -fsS -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/bioedca/tether/attestations/sha256:$digest" \
  | python3 -c 'import json, sys
for a in json.load(sys.stdin)["attestations"]:
    print(json.dumps(a["bundle"]))' > bundle.jsonl
gh attestation trusted-root > trusted_root.jsonl
gh attestation verify Tether-1.0.0-rc1-Linux-x86_64.sh \
  --bundle bundle.jsonl --custom-trusted-root trusted_root.jsonl \
  --repo bioedca/tether \
  --signer-workflow bioedca/tether/.github/workflows/release.yml \
  --deny-self-hosted-runners
```

The last command needs no network and no sign-in. The updater also passes `--source-ref
refs/tags/vX.Y.Z` for the version it offers. It installs only a release whose certificate
names a `vMAJOR.MINOR.PATCH` tag, so it refuses every `rc` above. The attestation must also
name the downloaded file as this platform's installer.

#### Documentation status

Every RC installer is unsigned, on all three platforms. So is every 1.0 installer that follows
it ([ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)). The SHA-256
manifests and the GitHub build-provenance attestation verified above are the integrity anchor.

The project intentionally retains the prerelease as this documented evidence set. Pre-releases
also intentionally do **not** publish the documentation site. The RC release job logged its
pre-release skip notice and created no `docs.yml` run. So publishing the documentation from a
release stays unproven until the stable `v1.0.0` tag.

## Change record

**The GitHub Releases are the change record.** The notes of each published Release say what
changed in it, at [github.com/bioedca/tether/releases](https://github.com/bioedca/tether/releases).
This repository has no `CHANGELOG.md`. The project will not generate or commit one
([ADR-0067](adr/0067-github-releases-are-the-only-change-record.md)).

### How the notes are produced

`release.yml` writes the notes from the Conventional-Commit subjects in the range of the release.
It uses them as the Release body, and it attaches the same text to the Release as
`CHANGELOG-<tag>.md`. Nobody edits the notes by hand, so the notes of any Release can be
regenerated from `git log`.

The kind of release decides which tag the release is compared with:

- A **stable** release (`v1.0.0`, `v1.1.0`) is compared with the nearest earlier **stable**
  release. So the notes of `v1.0.0` cover everything since `v0.8.0`, release candidates
  included. They do not cover only the commits since its last release candidate.
- A **prerelease** (`v1.0.0-rc2`) is compared with the nearest earlier release of any kind. So
  each release candidate lists what changed since the one before it.

An earlier tag counts as a release only if it meets three conditions. It is annotated, it has the
`vMAJOR.MINOR.PATCH` form above, and it has a published (non-draft) GitHub Release. The nine
pre-1.0 tags listed under [Before 1.0](#before-10-git-history-only) are the only exception to the
last condition, because they never had Releases.

The notes skip a rehearsal tag from a dry run and a tag whose run failed. They also skip any
other tag left behind without a Release. So a tag left in place does not shorten the notes of the
next release.

The notes have up to nine sections, in this order. A section with no commits is left out.

1. **⚠ Breaking Changes**: any commit whose subject is `<type>!:` or `<type>(scope)!:`, or whose
   message has a line that begins `BREAKING CHANGE:` (or `BREAKING-CHANGE:`). That line is meant
   to be the footer, but any line that starts that way counts. So do not begin a body paragraph
   with it unless you mean it. A breaking commit is listed here and not again under its type.
2. **Features** (`feat`), **Bug fixes** (`fix`), **Performance** (`perf`), **Documentation**
   (`docs`), **Refactoring** (`refactor`), **Build & packaging** (`build`) and **CI** (`ci`).
3. **Internal**: `test` and `chore`.

The notes skip merge commits, and a commit whose type matches none of these sections.

### Comparing any two versions

Open the Releases page, or the compare view of GitHub for the two tags:
`https://github.com/bioedca/tether/compare/vX...vY`. For example,
[`v0.8.0...v1.0.0-rc1`](https://github.com/bioedca/tether/compare/v0.8.0...v1.0.0-rc1) lists
every commit that `v1.0.0-rc1` has and `v0.8.0` does not.

To find which tags contain a particular fix, run `git tag --contains <commit>` in a clone. The
command lists every tag, so check each one against the Releases page. A tag left behind by a dry
run or a failed run is not a release.

### Before 1.0: git history only

**The pre-1.0 versions have no release notes, and none will be written.** The tags `v0.0.0`
through `v0.8.0` were milestone markers, cut before the release pipeline existed. They have no
GitHub Release, and their record is the commit history alone.

You may need to know whether a result produced with a pre-1.0 version predates a fix. Then open
the comparison for that version below. Or search the commit history for the fix, and check which
tags contain it.

| Version | What changed |
| --- | --- |
| `v0.0.0` | The first tag: [its source tree](https://github.com/bioedca/tether/tree/v0.0.0) |
| `v0.1.0` | [`v0.0.0...v0.1.0`](https://github.com/bioedca/tether/compare/v0.0.0...v0.1.0) |
| `v0.2.0` | [`v0.1.0...v0.2.0`](https://github.com/bioedca/tether/compare/v0.1.0...v0.2.0) |
| `v0.3.0` | [`v0.2.0...v0.3.0`](https://github.com/bioedca/tether/compare/v0.2.0...v0.3.0) |
| `v0.4.0` | [`v0.3.0...v0.4.0`](https://github.com/bioedca/tether/compare/v0.3.0...v0.4.0) |
| `v0.5.0` | [`v0.4.0...v0.5.0`](https://github.com/bioedca/tether/compare/v0.4.0...v0.5.0) |
| `v0.6.0` | [`v0.5.0...v0.6.0`](https://github.com/bioedca/tether/compare/v0.5.0...v0.6.0) |
| `v0.7.0` | [`v0.6.0...v0.7.0`](https://github.com/bioedca/tether/compare/v0.6.0...v0.7.0) |
| `v0.8.0` | [`v0.7.0...v0.8.0`](https://github.com/bioedca/tether/compare/v0.7.0...v0.8.0) |

From `v1.0.0-rc1` on, every published Release carries its own notes.

## Archiving (Zenodo)

Zenodo archives every GitHub Release that is published while the Zenodo–GitHub integration is
on, prereleases included. Zenodo mints a DOI for each one. A Release published while the
integration was off stays unarchived until it is published again (see the recovery below). So
not every historical release has a DOI. Tether therefore has two kinds of DOI, and they go in
different places:

| DOI | Resolves to | Where it is written |
|---|---|---|
| **Concept DOI** `10.5281/zenodo.23306397` | the newest archived version, whichever that is | `CITATION.cff` `doi:`, `codemeta.json` `identifier`, the `DOI` entry of `pyproject.toml` `[project.urls]`, `README.md`, [Citing Tether](citing.md), the [home page](index.md#cite-tether), and the head of each docs page (copied from `CITATION.cff` at build time). It never changes. |
| **Version DOI** | one release | only its own Zenodo record (linked from the concept record). Never hand-copied into the repository. |

The order below is load-bearing. Editing files afterwards cannot repair it. Follow it for every
release:

1. Switch the integration on before you push the tag. Zenodo archives a Release only if its
   webhook is active at the moment the Release is *published*.

    Check <https://zenodo.org/account/settings/github/>: the `bioedca/tether` toggle is on. Then
    check the *Settings → Webhooks* page of the repository: a `zenodo.org` hook is listed. Do
    both before step 6 of [Cutting a release](#cutting-a-release).

2. Do not try to reserve a DOI in advance. Through the GitHub integration, Zenodo mints the DOI
   of a version when its Release is published. The tagged commit already exists then, so no
   tagged commit can contain its own version DOI. That is why `CITATION.cff` carries the concept
   DOI instead.
3. **In the release commit**, set `version:` in `CITATION.cff` to the tag without its leading
   `v`. Set `date-released:` to the release date (ISO 8601). Leave `doi:` alone. In the same
   commit, change `version` and `datePublished` in `codemeta.json` to the same two values. A test
   fails while the two files differ.
4. **After the Release is published**, open the concept DOI. Check that the new version appears
   in the version list of the record. Zenodo reads the metadata of the archive from
   `CITATION.cff` in the tagged tree.

**If the newest Release was published while the integration was off**, you can still archive it
without a new tag. Switch the Release to a draft, and publish it again. Publishing fires the
event that Zenodo listens for. `release.yml` does not run, because tag pushes trigger it, not
Release events. For a stable Release, `docs.yml` also runs on that event and redeploys the same
documentation that it already published. For a prerelease it skips.

Do this **only for the newest Release**. Re-publishing an older Release has side effects that
editing files cannot undo:

- Zenodo orders versions by the time at which their records were created. So the backfilled
  release would become the version that the concept DOI resolves to.
- GitHub may mark it as the *Latest* release of the repository.
- `docs.yml` would point the `latest` documentation at it.

An older Release published while the integration was off stays unarchived, as `v1.0.0-rc1`
does.

```bash
gh release edit <tag> -R bioedca/tether --draft=true
gh release edit <tag> -R bioedca/tether --draft=false   # add --prerelease for an rc tag
```

This is how the archive started. The integration was switched on on 2026-10-11, after
`v1.0.0-rc2` had been published. Re-publishing `v1.0.0-rc2` created the Zenodo record and the
concept DOI. `v1.0.0-rc1` was never archived.

## Publish the documentation

The documentation site is versioned with [`mike`](https://github.com/jimporter/mike), and the
`gh-pages` branch serves it. Each build lives under `/tether/<MAJOR.MINOR>/`. The `latest` alias
and the site default point at the current stable tree.

**A stable release publishes the site automatically.** The last step of the `release` job of
`release.yml` dispatches
[`.github/workflows/docs.yml`](https://github.com/bioedca/tether/blob/main/.github/workflows/docs.yml)
with the release tag. It does this right after it creates the GitHub Release.

That explicit dispatch exists because the obvious mechanism does not work. `docs.yml` does
trigger on `release: [published]`. But `release.yml` creates the Release with the default
`GITHUB_TOKEN`, and GitHub deliberately does **not** start new workflow runs from events that
token raises. Without the dispatch, `docs.yml` never fires on a release. The site would then stay
frozen on whatever was last published by hand.

**Pre-releases do not publish.** A hyphenated tag (`v1.0.0-rc1`) collapses to the same `1.0`
documentation label as the stable tag. So publishing it would repoint `latest` and the site
default at the docs of a release candidate. `release.yml` skips the dispatch for those tags and
logs a `::notice::` that says so. So a release candidate produces **no** `docs.yml` run at all.
The stable tag, not the rehearsal, is therefore the first proof of publishing the documentation
from a release.

The site is built **from the release tag**, not from whatever `main` holds. The dispatch passes
`--ref "$TAG"`. Without it, `gh workflow run` targets the default branch. A tag cut a few commits
back would then publish unreleased docs under the released version. So would `main` advancing
during the build matrix of about 15 minutes.

The source links follow the tag too. `docs.yml` sets `TETHER_DOCS_SOURCE_TAG` to the tag that it
builds from. The build hook in `scripts/mkdocs_hooks.py` then points each `blob/main` and
`tree/main` link at that tag. A run from `main`, such as `dev`, keeps the links on `main`.

`mike` handles the canonical URLs of each version, not `mkdocs.yml`. `mike deploy` injects its own
plugin and rewrites `site_url` to `<site_url>/<version>` at build time. So the published `1.0`
tree carries canonicals and a sitemap under `/tether/1.0/`. That is why `site_url` in
`mkdocs.yml` stays at the Pages root. Pointing it at `/tether/latest/` would produce
`/tether/latest/1.0/…`, which does not exist.

### Manual fallback

If the dispatch fails, or you need to republish, run `docs.yml` yourself. Whatever you publish
takes over the `latest` alias and the site default. `docs.yml` always runs
`mike deploy --update-aliases <label> latest`, and then `mike set-default --push latest`. You
cannot publish a version *without* promoting it. So do not dispatch an older branch to refresh an
old tree.

**Pass `--ref`, and pass the tag**, exactly as the automatic dispatch does. Without it, `gh`
targets the default branch. You would then publish what `main` holds now under the label of a
released version. That is the mistake that the manual path exists to recover from.

```bash
gh workflow run docs.yml --ref v1.0.0 -f version=1.0
```

The same rule applies to *Actions → docs → Run workflow*: set **Use workflow from** to the release
tag, not to `main`.

The `version` input accepts three forms:

- `MAJOR.MINOR`,
- `MAJOR.MINOR.PATCH`, where a leading `v` is stripped and the patch component is dropped to
  give the doc tree,
- the literal `dev`.

The version-resolution step **rejects** anything else, and does not silently truncate it to a
plausible label. A four-component `1.0.0.0` and a pre-release `1.0.0-rc1` are rejected too.

### Verify it published

Keep the quotes in the command below. `?` is a glob character in zsh, the default shell on macOS.
An unquoted URL fails with `no matches found` before `gh` runs.

```bash
gh api "repos/bioedca/tether/contents/versions.json?ref=gh-pages" \
  --jq '.content' | base64 -d
```

The new version must appear in the list with `latest` among its `aliases`, for example
`[{"version": "1.0", "title": "1.0", "aliases": ["latest"]}]`. Then load
<https://bioedca.github.io/tether/latest/>. Confirm that the version selector offers the new
version.

### After 1.0 is live: retire the `dev` tree

The site currently carries a placeholder `dev` tree, created by hand before any release existed.
You can remove it once `1.0` is published **and** verified as above.

> **Do not run this command before `1.0` is live.** At present `latest` aliases `dev`. Deleting
> `dev` first takes the published site down until the stable tree replaces it.

```bash
mike delete --push dev
```

After the delete, check again that `latest` and the site default both still resolve to `1.0`.

## OS code-signing: why there is none

Windows, macOS and Linux are all the same case, so this section replaced the three that used to
differ. **No installer is OS-code-signed.** The per-file and combined **`SHA256SUMS`** and the
build-provenance attestation are the integrity anchor everywhere. Users get a SmartScreen warning
on Windows and a Gatekeeper warning on macOS.

[ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md) records the
decision and the priced alternatives, so that nobody has to research them again. In short:

- **SignPath Foundation declined** `bioedca/tether` on 2026-07-26. The program wants signals of
  public visibility: stars, forks, contributors, external references and institutional backing.
  A pre-1.0 project by one maintainer does not have them. A paid subscription was offered.
- **Apple Developer ID** is out of budget at $99/yr. Enabling it was also never one setting away.
  Notarization needs every Mach-O binary in the bundled conda payload signed with `codesign`,
  with a *Developer ID Application* identity and a hardened runtime. The removed step signed only
  the outer `.pkg`.

The legs were **removed, not disabled**. Both were written green-before-secrets, and neither ever
ran once. The only annotation of `v1.0.0-rc1` was the "SignPath not configured" warning. A dormant
leg looks the same as a working one in a green run.

`tests/test_marker_contract.py` holds the shape. One guard fails if a signing token returns to
`release.yml`. Another guard fails if the manifests or the attestation that replaced the signing
are dropped.

[#244](https://github.com/bioedca/tether/issues/244) tracks a new application once the visibility
criteria are met. To reverse this decision, write a superseding ADR that updates those guards with
it. Do not only edit this page.
