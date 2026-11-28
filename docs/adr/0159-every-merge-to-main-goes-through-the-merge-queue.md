<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0159 — Every merge to main goes through the merge queue

- **Status:** accepted
- **Date:** 2026-11-28
- **Deciders:** bioedca
- **PRD anchor:** §12.3 (branch protection and how a merge is bound), §12.4 (pull requests)
- **Milestone:** none (cross-cutting)
- **Issue:** [#1269](https://github.com/bioedca/tether/issues/1269)

## Context and problem statement

ADR-0135 built a merge queue of our own in four parts. After #1266, #1267 and #1268 it works, and
the trial of #1268 merged three pull requests through it on 2026-11-27. But the queue was a route
that a pull request took only when the maintainer said so. The agent contract still told every
worker to merge directly with `gh pr merge --auto --squash --match-head-commit <SHA>`, and said
that the repository has no merge queue. While a direct merge works, two things stay open: a tree
that the queue did not test can land on `main`, and `main` can move between the queue's last read
and its merge call (the base window, #1480). How does the queue become the one way to `main`?

## Decision drivers

- Every commit of `main` has a tree that passed the required checks before it landed (ADR-0135).
- The merge stays bound to the 40-hex head that the clean review read (ADR-0065).
- GitHub, not a worker's discipline, refuses a merge that bypasses the queue.
- A pull request that the queue removes is fixed without the maintainer (maintainer decision 3,
  2026-11-19).
- The release gate (#266) and `claim.py doctor` keep reading the merge state correctly.

## Considered options

1. **The contract alone.** The pages say "join the queue", and a direct merge still works.
2. **The contract and a required status.** The pages say "join the queue", and the ruleset
   `main-baseline` requires the status `merge-queue`, accepted only from the queue's App.
3. **Restrict who can merge** with a ruleset bypass list that only the App is on.

## Decision outcome

Chosen option: **2**, by the maintainer on 2026-11-19 (#1269).

- **The join replaces the direct merge.** Under explicit per-PR authority a worker runs
  `gh workflow run merge-queue.yml --ref main -f action=join -f pr=<PR> -f sha=<SHA> -f message="<squash message>"`,
  then exits. `<SHA>` is the head the clean review read, supplied from that review. The queue
  refuses a join at any other head and merges with the API's `sha` pinned to it, so the binding
  that `--match-head-commit` gave is kept. The command is the same in WSL bash and in native
  PowerShell.
- **The required status.** The queue sets `merge-queue` to `success` on the pinned head just before
  it merges. With the status required and bound to the App, a direct merge, an auto-merge and a
  status that someone else posts are all refused, for every person and every agent. This also
  closes the base window, since nothing else can merge in that instant. The maintainer adds the
  rule after this record merges; the pull request carries the commands.
- **A removed pull request.** Its worker merges `main`, fixes it, meets the review rules on the new
  head and rejoins under the go-ahead already given. It asks the maintainer only when the fix lies
  outside its item. A candidate that failed for a cause outside the pull request (a runner fault,
  a flaky test) rejoins at the same head.
- **The release gate does not copy `merge-queue`.** `release.yml` keeps a frozen copy of the
  required contexts and checks them on the tagged commit. The status lands on a pull request's
  head, never on a commit of `main`, so it is left out, and a test fails if it is added.
- **`doctor` reports a finished pull request that has no request in the queue.** Once the status is
  required, no unqueued pull request reads `mergeable_state == "clean"`. `doctor` also counts a
  `blocked` one whose every other required check passed, and says that it did not read its threads.

Option 1 leaves a direct merge one habit away. Option 3 limits who merges but not what: an actor
on the bypass list can still merge any head, while the required status exists only on a head that
the queue pinned and tested.

### Consequences

- Good: every commit of `main` passed the required checks before it landed, and the base window of
  ADR-0135 is closed once the rule is added.
- Good: the contract has one merge command, and GitHub enforces it.
- Bad: a merge now needs the queue running. When the queue is stopped (`MERGE_QUEUE_MODE=off`),
  nothing merges until the maintainer removes the rule; the pull request gives that command too.
- Bad: on a `blocked` pull request `doctor` cannot tell an unresolved thread from the missing
  status through REST, so it flags such an entry as `"threads": "not_read"`.
- Follow-up: `tests/test_agent_contract_is_runnable.py` pins the join on every page that merges and
  refuses `--match-head-commit`; `tests/test_marker_contract.py` keeps `merge-queue` out of the
  release copy; `tests/test_claim.py` covers a queued and an unqueued finished pull request.

## More information

- ADR-0135 (the queue), ADR-0065 (the binding of the merge to the reviewed head), ADR-0064 (the
  agent layer is feature-complete; #1269 is the maintainer-opened issue for this change), #266
  (the release gate), #1480 (the base window).
- The trial of #1268: #1513, #1520 and #1511 merged through the queue on 2026-11-27.
