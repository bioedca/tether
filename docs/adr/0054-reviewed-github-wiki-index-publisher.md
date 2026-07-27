<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0054 — Reviewed GitHub wiki index with a fail-closed mirror publisher

- **Status:** accepted
- **Date:** 2026-07-27
- **Deciders:** bioedca
- **PRD anchor:** §12.7 (CI/CD and durable architectural decisions)
- **Milestone:** M10

## Context and problem statement

GitHub exposes a repository wiki as a separate `.wiki.git` repository and surfaces it
independently of the versioned MkDocs site. Tether's enabled wiki is uninitialized, which
leaves a dead-end documentation destination. Authoring a second set of documentation in
the wiki would instead create an unreviewed source that drifts from MkDocs.

Issue #189 selected one PR-governed delivery path: keep reviewed wiki source in this
repository and mechanically mirror it to `.wiki.git`. Its accepted implementation note
expected the first publication to create the uninitialized wiki without weakening
repository-wide permissions or introducing a broad, persistent credential.

## Decision drivers

- Wiki prose must pass the same branch, PR, REUSE, and review path as repository docs.
- The wiki must remain one screen of orientation and links, never a second manual.
- Pull requests and forks must never receive a publishing credential or mutate the wiki.
- The workflow must fail before mutation when its event, repository, wiki feature,
  credential, remote state, or fast-forward assumption is uncertain.
- A first publication may proceed only when the receive endpoint is authoritatively
  reachable; a 404 must not be guessed to mean "safe to create."
- No repository or wiki access setting may be relaxed to make automation succeed.

## Considered options

1. **Author the page directly in GitHub's wiki editor.** This initializes the wiki but
   bypasses the repository PR, REUSE, and local validation path.
2. **Store a classic or fine-grained personal access token.** This works across Git
   endpoints but creates a manually rotated, user-bound secret; a classic token is
   especially broader than this single-repository job.
3. **Store a writable SSH deploy key.** A deploy key is repository-specific, but it is
   persistent credential material and GitHub recommends a GitHub App where finer control
   is needed.
4. **Use the workflow's repository-scoped `GITHUB_TOKEN` and prove the exact Git
   operation with a dry run before a fast-forward update** (chosen).

## Decision outcome

Chosen option: **"Use the repository-scoped `GITHUB_TOKEN` and a mandatory dry-run
fast-forward mirror."**

`wiki/Home.md` is the entire publishable source. Its validator requires exactly that one
regular UTF-8 file, a first visible index-only warning, a two-sentence Tether orientation,
the version-selector link, the five approved section links, no `/dev/` URL, and a
110-visible-word ceiling. Additional pages, directories, links, images, tables, code
blocks, or sections fail before any Git transport begins.

`.github/workflows/wiki.yml` runs only from `bioedca/tether`'s `main` ref after a relevant
push, or by manual dispatch from that ref. Manual dispatch defaults to validation only.
The workflow has `contents: read` by default; only its publishing job receives
`contents: write`. It uses the SHA-pinned official checkout action with persisted
credentials disabled, verifies through the API that the parent repository still exposes
a wiki, and passes the short-lived token through `GIT_ASKPASS`. It never changes a
repository permission, wiki setting, collaborator, or public-editing control.

The standard-library publisher builds a temporary Git tree whose only file is an exact
byte copy of `wiki/Home.md`. If the wiki already has `master`, the new commit is based on
that fetched head and therefore preserves history. Stale manually authored pages are
deleted by the mirror commit. No force option exists. A concurrent manual edit or another
publisher makes the final update non-fast-forward and the job fails.

A reachable empty Git repository is a supported first-publication state: `ls-remote`
reaches the server but reports no `master`, and the publisher creates that ref only after
the Git receive path accepts a dry run. GitHub's currently uninitialized wiki is not in
that state. On 2026-07-27, both authenticated `ls-remote` and an authenticated
`push --dry-run` returned `Repository not found`; no live content was created. The
publisher therefore treats that 404 as uncertainty and stops before a real update.

This is an explicit external precondition, not an implementation assumption: before the
workflow can publish, GitHub must expose `bioedca/tether.wiki.git` as a reachable empty
repository or an existing wiki `master`, through a maintainer-approved bootstrap that
preserves the reviewed-source model and does not relax permissions. Current official
documentation describes creating an initial page before cloning a wiki; it does not
establish a supported Actions-only bootstrap for the 404 state. The worker does not
initialize or publish the live wiki.

### Permission-model evidence

GitHub documentation retrieved 2026-07-27 states that:

- [`GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token) is
  a short-lived GitHub App installation token whose permissions are limited to the
  repository containing the workflow.
- [Wiki pages are part of Git repositories](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)
  and can be changed with a normal local Git workflow after an initial page exists.
- [Wiki write access follows the parent repository](https://docs.github.com/en/communities/documenting-your-project-with-wikis/changing-access-permissions-for-wikis);
  collaborators can edit by default, without enabling public editing.
- [Deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
  are persistent repository credentials, and GitHub recommends an App for finer control.

The conclusion that the parent repository's job-scoped `contents: write` installation
token can address an initialized `.wiki.git` receive path is an inference from those
documented properties. The mandatory live `--dry-run` immediately before the update is
the fail-closed capability check: if GitHub treats that endpoint differently, publication
does not occur and permissions are not broadened as a workaround.

### Consequences

- **Good.** The visible wiki is mechanically derived from reviewed, licensed source.
- **Good.** Pull requests run tests but never run a credentialed publishing job.
- **Good.** No new persistent secret, service account, or repository-wide permission
  change is needed.
- **Good.** A reachable empty remote can be created by the same reviewed transport used
  for later mirrors; a 404 cannot silently enter that path.
- **Good.** A dry-run/manual dispatch can validate an initialized transport without
  publishing.
- **Trade-off.** The mirror intentionally removes wiki pages not present in source; all
  durable documentation must be authored under `docs/`.
- **Blocker.** GitHub currently returns 404 for the uninitialized wiki and rejected the
  non-mutating authenticated receive-path probe. A supported, permission-preserving
  bootstrap must be established before live publication; the workflow will fail closed
  until then.
- **Trade-off.** Publishing automation and its permission inference are high-risk review
  material and require the repository's high review path.

## More information

- Issue #189 records the accepted one-page content and PR-governed delivery path.
- `wiki/Home.md` is the reviewed source.
- `scripts/publish_wiki.py` owns validation, history preservation, dry run, and transport.
- `.github/workflows/wiki.yml` owns event, repository, and token scoping.
- `tests/test_wiki_publish.py` exercises creation of a reachable empty remote, exact
  mirroring, dry-run non-publication, uncertainty failure, content bounds, and the
  workflow contract.
