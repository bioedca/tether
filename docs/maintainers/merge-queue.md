---
description: "How the merge queue works: join and leave, the train, the verdict, the merge, a removal and the rejoin, the mode, the App permissions and the stop procedure."
---

# The merge queue

**Who this page is for.** Contributors and agent sessions whose pull requests merge to `main`,
and the maintainer, who sets up the App and the mode. To join or leave, you need write access to
the repository. Users do not need this page. The decision is
[ADR-0135](../adr/0135-a-merge-queue-of-our-own-tests-each-pull-request-on-the-ones-ahead-of-it.md).

The tree that lands on `main` passes every required check first, and one bad pull request delays
only the ones behind it. In `observe` mode the queue tests candidates and prints a plan, and writes
no status, comment, label or merge. In `on` mode it also judges the candidates, merges the front of
the train and removes a pull request that cannot merge. **Until #1269, a pull request takes the queue only
when the maintainer says so**, and the direct merge still works. The queue tolerates a `main` that
moves without it, and builds the train again from the new `main`.

## Join and leave

A pull request joins with the 40-hex head that the clean review read and the squash message:

```sh
gh workflow run merge-queue.yml --ref main -f action=join -f pr=N -f sha=<head> -f message="<squash message>"
gh workflow run merge-queue.yml --ref main -f action=leave -f pr=N
```

A `join` refuses a pull request that is closed, a draft, from a fork, not for `main`, or at
another head. A `leave` deletes only the request. The reconcile that runs after it deletes the
candidate and, in `on` mode, takes the label off. In observe mode, the request of a pull request
that merged stays, as `remove`, until a `leave`. In `on` mode the reconcile deletes it.

**One owner.** Every write but the request ref is the reconcile's. Reconciles run one at a time
and one follows every `leave`, so the last writer of `mq/pr-N` always read after the `leave`. Before
a merge or a removal the reconcile reads the request ref again. If it changed, because the pull request left or
joined again, the run builds the train again and merges only the request it now reads.

## The train and a candidate

A **request** is the ref `refs/merge-queue/pr-N`: a commit whose only parent is the pinned head
and whose message is the squash message. The **train** is the requests, the oldest first.

A **candidate** is the candidate ahead (or `main`) plus its own squash, with the tree of
`git merge-tree --write-tree <candidate ahead> <head>`. The first five go to `mq/pr-N`, where
`ci.yml`, `sidecar.yml` and `schema-guard.yml` run, and no advisory workflow. A branch that holds
its tree already is not pushed again, so it keeps its runs.

| State | Meaning |
|---|---|
| `candidate` | It merges cleanly on top of the candidate ahead. |
| `held` | It conflicts with a pull request ahead, not with `main`. The plan names the blocker. |
| `remove` | It closed, was retargeted, comes from a fork, became a draft, its head moved, or it conflicts with `main`. The plan gives the reason, and the train is built without it. |

## The verdict (mode `on`)

The required checks are the ones the active rules of `main` require, without `merge-queue` itself.
When the rules cannot be read, or require nothing, there is no verdict and no merge, and the run
fails. A candidate is judged on the commit its branch holds now, so a branch kept for its tree keeps
its runs.

The queue imports two rules from the release guard, `scripts/verify_postmerge_checks.py`. One picks
the run that decides each check. The other is the one exemption: `commitlint` reports `skipped` on a
push. A candidate is `passed` when every required check passed, and `failed` when one failed.
Otherwise it is `pending`. A check with no run, or whose deciding run has not finished, is pending
and never passed.

A candidate counts as `failed` only when every candidate ahead of it passed, because a failure can
come from a pull request ahead. Until then it is `pending`.

## The merge

When the front candidate passed, the queue sets the status `merge-queue` to `success` on the pinned
head. It squash-merges the pull request through the API with `sha` equal to the pinned head. The
title is `<pull request title> (#N)` and the message is the squash message of the join. A push
after the join makes GitHub refuse.

Then the queue deletes the request and the candidate and takes the label off. It compares the tree
of the new `main` commit with the tree of the candidate. When they differ, the run fails with an
error that names both, and it merges nothing more in that run. Otherwise the run builds the train
again and goes on, so several passed candidates at the front merge in order in one run.

## A removal and the rejoin

A pull request is removed for a `remove` state of the plan, or when GitHub refuses its merge. It is
also removed when its candidate failed and every candidate ahead passed. The queue sets the status
`merge-queue` to `failure` on the pinned head, deletes the request, writes **one** comment, and
deletes the candidate. The request goes before the comment, so a later run cannot comment twice.

The comment gives the reason, which is one of these:

- the failed checks, with links to their runs
- the reason GitHub gave when it refused the merge
- the paths that conflict with `main`
- that the head moved after the join

The train behind is built again without it, and the other pull requests go on.

A pull request that closed (it merged the direct way, say) leaves quietly, with no status or comment.

**The rejoin.** The thread of the removed pull request merges `main` and fixes the cause. It gets
the closing review on the new head and joins again at that head, under the go-ahead it already had.
The comment gives the command.

## Statuses and the label (mode `on`)

| The pull request | Status `merge-queue` on the pinned head | Label `merge-queue` |
|---|---|---|
| queued (a candidate) | `pending`, with its place in the train | yes |
| held | `pending`, with the number of the pull request it waits for; no comment | yes |
| merged | `success` | removed |
| removed | `failure`, and one comment | removed |

The label goes when the pull request leaves, by any route.

## When it runs, and the mode

It runs on a manual `join`, `leave` or `reconcile`. It also runs when a required workflow completes
on an `mq/**` branch, on a push to `main`, and once an hour. A `reconcile` builds the candidates and
prints the plan in the step summary. It is never cancelled, and a newer waiting `reconcile` reads
every request, so no request is lost. The repository variable `MERGE_QUEUE_MODE` is `off` (or not
set), `observe` or `on`. Off ends the run before any write.

The workflow never runs code from a pull request. It writes as a private GitHub App, because an
event that `GITHUB_TOKEN` causes starts no run, and only `main` can read the App key.

## App permissions

The App's install permissions are only what `on` mode needs. The token of each run asks for less.
A `join`, a `leave` and every `observe` run ask only for Contents and Workflows write and Pull
requests read.

| Permission | Access | The calls that need it |
|---|---|---|
| Contents | write | push and delete `mq/pr-N`; create and delete `refs/merge-queue/pr-N` (`POST /git/commits`, `POST /git/refs`, `DELETE /git/refs/...`); the merge (`PUT /pulls/N/merge`) |
| Workflows | write | push a candidate whose tree changes a file under `.github/workflows/` |
| Pull requests | write | read a pull request (`GET /pulls/N`); the comment (`POST /issues/N/comments`); the label (`POST /issues/N/labels`, `DELETE /issues/N/labels/merge-queue`, `GET /issues?labels=merge-queue`) |
| Commit statuses | write | the status `merge-queue` (`POST /statuses/SHA`) |
| Checks | read | the runs of a candidate (`GET /commits/SHA/check-runs`) |
| Metadata | read | the required checks (`GET /rules/branches/main`); every App has it |

GitHub's REST reference names these permissions for these endpoints (read through Context7
`/github/docs`, data version fpt-2026-03-10, on 2026-11-26).

**Candidate runs.** A run on `mq/**` gets no secret or write grant that the `pull_request` run of
the same pull request lacks. The three required workflows name no secret but `GITHUB_TOKEN`. They
use no environment, and they declare each permission as a literal. Their one job that writes acts on
a push only to the default branch.

`tests/test_merge_queue_workflow.py` checks all of this. The reconcile also removes a request from
a fork.

## The stop procedure

1. Set the repository variable `MERGE_QUEUE_MODE` to `off`. New runs end before any write.
2. Drain the runs: on the Actions page, wait for the `merge-queue` runs that started before the
   change, or cancel them.
3. Delete the candidate branches, because their CI still runs on a push and they stay with `off`:
   `git ls-remote origin 'refs/heads/mq/*'`, then `git push origin --delete mq/pr-N` for each.
   Delete the requests the same way (`git push origin --delete refs/merge-queue/pr-N`) to empty the
   queue, and take the label `merge-queue` off the pull requests.

## Maintainer setup

1. On GitHub, go to Settings, Developer settings, GitHub Apps, New GitHub App. Name it
   `tether-merge-queue`, with the repository address as the homepage and no active webhook. Give
   it exactly the repository permissions in [App permissions](#app-permissions), and no other.
   Install it only on this account. An App made before #1268 had Actions read: remove it.
2. On the App page, generate a private key. Install the App on `bioedca/tether` only.
3. In the repository, go to Settings, Environments, and make the environment `merge-queue` with
   the deployment branch `main` only. Add the secret `MERGE_QUEUE_APP_PRIVATE_KEY` (the whole key
   file) and the variable `MERGE_QUEUE_APP_CLIENT_ID` (the Client ID of the App).
4. Make the label `merge-queue` on the repository's Labels page.
5. Set the repository variable `MERGE_QUEUE_MODE` to `observe`, or to `on` for a batch the
   maintainer sends through the queue. To stop it, follow [the stop procedure](#the-stop-procedure).
