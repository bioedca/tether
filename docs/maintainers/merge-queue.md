---
description: "How the merge queue works: join and leave, the train and a candidate, the states, the mode, and the maintainer setup."
---

# The merge queue

**Who this page is for.** Contributors and agent sessions whose pull requests merge to `main`,
and the maintainer, who sets up the App and the mode. To join or leave, you need write access to
the repository. Users do not need this page. The decision is
[ADR-0135](../adr/0135-a-merge-queue-of-our-own-tests-each-pull-request-on-the-ones-ahead-of-it.md).

The tree that lands on `main` passes every required check first, and one bad pull request delays
only the ones behind it. **The queue is in observe mode:** it tests candidates and prints a plan,
and writes no status, comment, label or merge. Pull requests merge as before until #1268 and #1269.

## Join and leave

A pull request joins with the 40-hex head that the clean review read and the squash message:

```sh
gh workflow run merge-queue.yml --ref main -f action=join -f pr=N -f sha=<head> -f message="<squash message>"
gh workflow run merge-queue.yml --ref main -f action=leave -f pr=N
```

A `join` refuses a pull request that is closed, a draft, from a fork, not for `main`, or at
another head. A `leave` deletes the request and the candidate. In observe mode, the request of a
pull request that merged stays, as `remove`, until a `leave`.

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
| `remove` | It closed, was retargeted, became a draft, its head moved, or it conflicts with `main`. The plan gives the reason, and the train is built without it. |

## When it runs, and the mode

It runs on a manual `join`, `leave` or `reconcile`, when a required workflow completes on an
`mq/**` branch, on a push to `main`, and once an hour. A `reconcile` builds the candidates and
prints the plan in the step summary. It is never cancelled, and a newer waiting `reconcile` reads
every request, so no request is lost. The repository variable `MERGE_QUEUE_MODE` is `off` (or not
set), `observe` or `on`. Off ends the run before any write. `on` fails until part 2.

The workflow never runs code from a pull request. It writes as a private GitHub App, because an
event that `GITHUB_TOKEN` causes starts no run, and only `main` can read the App key.

## Maintainer setup

1. On GitHub, go to Settings, Developer settings, GitHub Apps, New GitHub App. Name it
   `tether-merge-queue`, with the repository address as the homepage and no active webhook. Give
   it the repository permissions Contents, Pull requests, Commit statuses and Workflows read and
   write, and Checks, Actions and Metadata read. Install it only on this account.
2. On the App page, generate a private key. Install the App on `bioedca/tether` only.
3. In the repository, go to Settings, Environments, and make the environment `merge-queue` with
   the deployment branch `main` only. Add the secret `MERGE_QUEUE_APP_PRIVATE_KEY` (the whole key
   file) and the variable `MERGE_QUEUE_APP_CLIENT_ID` (the Client ID of the App).
4. Set the repository variable `MERGE_QUEUE_MODE` to `observe`. Set it to `off` to stop the queue.
