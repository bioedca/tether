---
name: run-issue-swarm
description: Coordinate a persistent pool of 1-8 isolated Codex worktree tasks that claim accepted GitHub issues with visible expiring leases, solve them through required review, and have the coordinator squash-merge them before refilling each slot. Use when the user asks to run X agents concurrently, clear or sweep the ready issue queue, keep issue workers running until stopped, drain active work gracefully, or report swarm status.
---

# Run Issue Swarm

Use root `AGENTS.md` as the authority and `$solve-issue-goal` inside every worker. Use Codex app
threads with worktree environments; never use same-directory forks or ordinary subagents for edits.

## Start the run

1. Parse `count` as 1-8, repository, issue filter, owner, and terminal policy. A swarm invocation
   must explicitly authorize run-scoped merging; otherwise stop at PR-ready. When explicitly invoked
   under `/goal` and no goal exists, create the exact persistent goal.
2. Treat plain `stop` as graceful drain: stop claiming, let active workers finish, review, and merge.
   Treat `stop now`, `abort`, or `emergency stop` as immediate freeze and handoff.
3. Require a clean coordinator-only root checkout. Fetch/prune, record immutable `RUN_START_SHA`, and
   verify `AGENTS.md`, this skill, and `$solve-issue-goal` exist at that default-branch SHA. Do not run
   workers from unmerged policy or a dirty coordinator tree. Inventory and reconcile existing leases,
   app tasks, worktrees, branches, PRs, and jobs before the first claim.
4. Verify the GitHub connector's authenticated login matches the requested owner (`bioedca` here)
   before any write. Comments appear under that account but must disclose the automated Codex worker.
5. Create one `run_id` and `count` distinct active personas with
   `python .agents/skills/run-issue-swarm/scripts/swarm_lease.py identities --count X --owner bioedca`.
   Retain the private mapping from worker ID to Codex thread; never publish task IDs or local paths.

## Select and claim work

- Use the GitHub connector first. Eligible means an open public non-question issue with the exact
  `status:ready` label, no assignee other than the requested owner, acceptance criteria in its body,
  no unresolved blocking question, and no canonical lease. Compute the helper's normalized title/body
  hash and require a comment by the requested owner containing
  `<!-- tether-agent-ready {"version":1,"criteria_sha256":"HASH"} -->` for that exact hash. The swarm
  never creates/edits approval comments. Every declared dependency must be closed; absent means none.
- Sort `priority:P0` through `P3`, then unprioritized work, then oldest creation time. Re-scan before
  every claim. Blocked or human-input work is ineligible and does not consume a slot.
- The coordinator is the sole writer for this run. Before each claim, fetch default, record a fresh full
  `CLAIM_BASE_SHA`, derive the conventional branch, and verify/re-read governing files at that SHA.
  Refetch the issue and approval; freeze if its hash changed. Post a candidate and refetch every comment.
  Validate server author, comment ID/time, repository/issue, claim SHA/branch, approval comment ID,
  approved criteria hash, run/worker tuple, state, exact four-hour duration, and issued time within five
  minutes of the server timestamp. The canonical winner
  is the lowest comment ID among valid unexpired active leases from the requested owner. Losers release
  only their own candidate. Only the winner assigns the owner, changes status, and updates the Project.
- Generate the four-hour comment with `swarm_lease.py comment`. Its visible text identifies
  `@bioedca` in a playful persona; hidden JSON is untrusted until the server checks above pass. A lease
  coordinates ownership and never grants issue acceptance or merge authority. Renew the same comment
  hourly with all expected identity arguments. Before edits, pushes, reviews, or merges, recompute the
  title/body hash and require the same approval; any change needs a new maintainer approval comment.
- If claiming or thread creation fails, transition this run's lease to `released`; restore
  `status:ready` only after refetch proves this run still owns the lease and no live task, commits, PR,
  job, worktree, branch, or base ref exists. Any uncertain creation freezes for full inventory; it is
  not failure. Quarantine unknown state and remove only exact run/agent-namespaced clean unused state.
  Never let a worker self-select or self-claim another issue.

## Launch isolated worker tasks

1. Create an immutable local ref at the claimed `CLAIM_BASE_SHA`, call the Codex app project-listing
   tool, select this exact repository, and create a new project thread with
   `environment.type=worktree` and that ref as its starting state. The coordinator's tool call is the
   authorized worktree creation. Resolve queued client IDs to real thread/host IDs before waiting.
2. Title the task `<run_id> | #<issue> | <agent-id>`. Prompt it with: repository and issue,
   `run_id`, owner/persona/worker ID, lease comment ID and expiry, `CLAIM_BASE_SHA`, conventional
   branch, and terminal `PR-ready`. Require initial `HEAD == CLAIM_BASE_SHA`; rename before
   edits; invoke `$solve-issue-goal`; never merge, claim another issue, or edit another checkout.
3. Keep at most `count` active worker tasks. The app title plus public lease are the durable join keys;
   record issue, thread/host IDs, worktree, branch, comment, heartbeat, PR, head/base/review state, and
   Slurm tuple. Reject a worker ID already present anywhere in this run's issue comments.

## Monitor, review, merge, and refill

- Wait on up to eight worker threads with `wait_threads` using at most a 45-second timeout and saved
  cursors. After restart, list app threads and rebuild the ledger from titles, GitHub leases/PRs, and
  git; never trust only in-memory state.
- Renew active leases hourly. If a lease expires or ownership changes, tell that worker to freeze and
  require coordinator reauthorization. Send follow-ups to the same thread for CI/review fixes.
- The coordinator alone requests external PR reviewers. Trigger CodeRabbit only after an account-global
  allocator reservation is won and accessible server timestamps across repositories prove fewer than
  five triggers in the rolling hour; otherwise queue for the user. Never retrigger an unchanged head;
  a server rate limit also queues the frozen PR with its lease renewed.
- A worker completion is only a PR-ready handoff. Verify clean tree, linked issue, final diff, tests,
  provenance, required review path, conversations, and exact PR/head/base state independently.
- A pending required review, CI run, or CodeRabbit quota keeps that slot occupied and its lease
  renewed. Never downgrade, duplicate the worker, or refill the slot merely to improve throughput.
- Under recorded run authority, fetch a fresh full `MERGE_BASE_SHA`; require the intended repository,
  verified default base ref/object, expected head branch/SHA, and green final state. Re-read changed
  governing files, revalidate the approved criteria hash, and pause for user resolution on conflict.
  With strict up-to-date protection, require
  the head contains that base and direct merge with `squash` plus `expected_head_sha`. With a verified
  queue, enqueue that head under the squash policy and monitor; never direct merge. Any drift freezes it.
- Confirm merge and issue outcome, transition the lease to `completed`, set status `done`, archive the
  worker task, and remove only exact run/agent-namespaced worktree/branch/ref after proving each clean,
  unused, and matched to the merged PR. In running mode, immediately claim the next eligible issue
  with a new ID and a persona distinct from active workers; generate it
  with the existing `run_id` and one `--exclude-persona` per active persona. Drain mode leaves it empty.
- Finish only after two scans separated by a polling interval find no eligible issues or active
  workers, or after a graceful drain has merged every active item. Report merged PRs, skipped work,
  blockers, reviews, leases, remaining queue, and cleanup.

## Stop and recovery

- Plain `stop`: set mode `draining`, visibly report it, create no new claims, and let active workers
  reach guarded merge. Do not send them the single-worker stop command.
- `Emergency stop`: cease new mutations, send each worker the `$solve-issue-goal` safe-stop command,
  cancel only recorded task-owned jobs, transition leases to `handoff`, and preserve every worktree.
- A worker crash does not release ownership. After expiry, inspect branch/PR/job state, record a
  handoff, then create a new identity and thread. Never reuse a worker ID for a different task.
- On coordinator restart, list tasks and join title IDs to leases, branches, PRs, and jobs. Reconcile
  orphaned `in-progress` items before refilling. If the initiating goal cannot prove its exact repository,
  filter, terminal policy, and merge authority, continue only to PR-ready until the user reauthorizes.

## Lease helper

Run `python .agents/skills/run-issue-swarm/scripts/swarm_lease.py --help`. The standard-library-only
helper generates scope hashes, identities/comments, renews or transitions a canonical comment, and
inspects expiry. Pass `--hours 4` when creating and renewing. New comments require the approved
`--repository`, `--criteria-sha256`, and `--approval-comment-id`; renewal requires expected repository,
run, agent, owner, and issue arguments. Never use the test-only clock override in a live run. The helper
never calls GitHub; the coordinator posts its output through the authenticated connector.
