<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0135 — A merge queue of our own tests each pull request on top of the ones ahead of it

- **Status:** accepted
- **Date:** 2026-11-19
- **Deciders:** bioedca
- **PRD anchor:** §12.3 (branch protection and how a merge is bound), §12.6 (CI)
- **Milestone:** none (cross-cutting)
- **Issue:** [#1266](https://github.com/bioedca/tether/issues/1266)

## Context and problem statement

PRD §12.3 dropped the strict up-to-date rule on purpose, and binds each merge to the reviewed head
with `--match-head-commit`. The post-merge runs on `main` were to catch a semantic conflict between
two green pull requests. Two things were measured on 2026-11-19:

- The last 80 merged pull requests were 2 to 39 commits behind `main` when they merged. Nothing
  tested the tree that landed before it landed.
- Eight pull requests merged between 16:29Z and 16:42Z. Each merge cancelled the run of the commit
  before it, so seven commits of `main` have no finished checks (#1265 ends the cancelling).

The maintainer merges in such bursts as reviews complete, and asked that every commit of `main`
finish its checks before the next merge, and that one conflict or failed check not stop a batch.

## Decision drivers

- The tree that lands on `main` has passed every required check before it lands.
- A burst of N pull requests takes about the time of one CI run, not N runs in a row.
- One bad pull request delays only the pull requests behind it.
- The merge stays bound to the 40-hex head that the clean review read (ADR-0065).
- No outside service gets write access, and the review seats and the docs address do not move.

## Considered options

1. **A queue of our own on GitHub Actions**, with each pull request tested on top of the ones
   ahead of it, all at the same time.
2. **GitHub's merge queue, after a move of the repository to an organization.** Only a repository
   that an organization owns can have the queue (GitHub Docs, "Managing a merge queue"). The move
   changes the repository and docs-site addresses, and the review seats must move too.
3. **Mergify**, free for a public repository. An outside application gets write access and does
   the merge, and it has no binding to the reviewed head.
4. **Our own queue, one pull request at a time.** Simple, but eight take about 2.5 hours.
5. **Our own queue, one test for the whole batch.** Fast, but the commits in between are not
   tested alone, and a failure costs more runs to find its cause.

## Decision outcome

Chosen option: **1**, by the maintainer on 2026-11-19. It is built in parts: #1266 (the requests
and the train), #1267 (the workflow), #1268 (the verdict and the merge) and #1269 (the contract).

- **A request is a ref.** `refs/merge-queue/pr-N` points at a commit whose only parent is the
  pinned head and whose message is the squash message. To create the ref is the mutex, as for an
  agent claim (ADR-0057). GitHub writes the commit, so its date, which orders the train, is server
  time. A ref outside `refs/heads/` starts no workflow. `join` refuses a pull request that is not
  open, is a draft, does not target `main`, has another head than the one given, or comes from a
  fork: a candidate branch runs workflows with the repository's own permissions.
- **The train.** The requests, the oldest first. The candidate of the first pull request is `main`
  plus its squash; each later candidate is the candidate ahead plus its own squash. Candidates
  go to `mq/pr-N` branches, where the required workflows run. Only the first five have a branch.
- **Same tree.** A candidate tree is `git merge-tree --write-tree <candidate ahead> <head>`. For
  the last 80 squash merges on `main` this was the tree GitHub merged, 80 of 80. The queue compares
  the two trees again after each merge.
- **Evidence follows the tree, not the commit.** A branch that already holds the tree of its
  candidate is not pushed again. When the pull request ahead merges, the candidates behind it get
  a new parent and keep their runs.
- **Hold and remove.** A pull request that conflicts only with one ahead of it is held until that
  one merges or leaves. A pull request that conflicts with `main`, whose candidate fails after
  every candidate ahead passed, whose head moved or which closed is removed with one comment. The
  train behind is built again without it.
- **A removed pull request returns without the maintainer.** Its worker merges `main`, fixes it,
  meets the review rules on the new head and joins again under the go-ahead it already has.
- **The merge** is a squash by the API with `sha` set to the pinned head, so a push after the join
  makes it fail safe. Until #1269 the contract keeps the direct merge, and the queue tolerates a
  `main` that moves without it.

### Consequences

- Good: every commit of `main` has a tree that passed all required checks, and its own post-merge
  runs finish (#1265). A release can be cut from any of them (#266).
- Good: a burst of eight takes about two CI runs of wall time with a window of five.
- Bad: each merged pull request costs one more full CI run, on the candidate.
- Bad: we own the queue. Its script is tested offline against real git repositories.
- The workflow token cannot push a commit that changes a workflow file, and an event it causes
  starts no run. The queue therefore acts as a private GitHub App whose key lives in an environment
  limited to `main` (#1267).

## More information

- The 80-merge check: for each squash commit `S` of `main` with parent `P` and pull request head
  `H`, `git merge-tree --write-tree P H` was compared with the tree of `S` (git 2.43.0).
- GitHub Docs, read through Context7 `/github/docs` on 2026-11-19: "Managing a merge queue"
  (availability; a failed or conflicting pull request is removed and the group behind it is built
  again) and "Use GITHUB_TOKEN for authentication in workflows" (events it causes start no run).
