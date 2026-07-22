---
name: run-issue-swarm
description: Coordinate a persistent pool of 1-8 isolated Codex worktree tasks that claim accepted GitHub issues with visible expiring leases, solve them through required review, and optionally have the coordinator perform explicitly authorized squash merges before refilling each slot. Use when the user asks to run X agents concurrently, clear or sweep the ready issue queue, keep issue workers running until stopped, drain active work gracefully, or report swarm status.
---

# Run Issue Swarm

Use root `AGENTS.md` as the authority and `$solve-issue-goal` inside every worker. Use Codex app
threads with worktree environments; never use same-directory forks or ordinary subagents for edits.
The protocol rationale and invariants are recorded in
`docs/adr/0052-concurrent-agent-swarm-coordination.md`.

## Start the run

1. Parse `count` as 1-8, repository, issue filter, owner, and terminal policy exactly `PR-ready` or
   `merge`. Default to `PR-ready`; only the current authenticated user request may initialize `merge`.
   When explicitly invoked under `/goal` and no goal exists, create the exact persistent goal.
2. Treat plain `stop` as graceful drain: stop claiming and let active workers reach the recorded
   policy. A `PR-ready` run ends with green checks, resolved reviews, and handoff; nobody merges. A
   `merge` run may finish guarded merges. `stop now`, `abort`, or `emergency stop` freezes and hands off.
3. Require a clean coordinator-only root checkout. Fetch/prune, record immutable `RUN_START_SHA`, and
   verify `AGENTS.md`, this skill, and `$solve-issue-goal` exist at that default-branch SHA. Do not run
   workers from unmerged policy or a dirty coordinator tree. Inventory and reconcile existing leases,
   app tasks, worktrees, branches, PRs, and jobs before the first claim.
4. Verify the GitHub connector's authenticated login matches the requested owner (`bioedca` here)
   and verify that login has `maintain`/`admin` repository permission or appears in a checked-in
   maintainer allowlist before accepting its approval. When CodeRabbit is required or selected and `gh`
   is used, require the same login and prove its token can enumerate every repository in which that owner
   can trigger the quota-limited review; otherwise queue CodeRabbit. This account-wide quota preflight
   does not apply to a Codex-selected low/standard lane or optional Copilot feedback.
   Comments appear under the owner account but must disclose the automated Codex worker.
5. Validate the first eligible scan. If none exist, finish only after the required second scan without
   creating public run state. Otherwise choose its lowest-numbered candidate as immutable `anchor_issue`.
   Create one `run_id` and `count` distinct active personas with
   `python .agents/skills/run-issue-swarm/scripts/swarm_lease.py identities --count X --owner bioedca`.
   Retain the output as the run's private ID allowlist and map each ID to one Codex thread. The helper
   validates ID shape, not provenance; reject any ID absent from this mapping. Never publish task IDs
   or local paths.
6. Keep the exact filter only in the private ledger; it must contain public issue metadata, never secrets.
   Put only its SHA-256 in the immutable `tether-swarm-run` JSON start record on `anchor_issue`, bound to
   run/repository/anchor/owner/count/running mode/policy/authority/start SHA/time. The helper is the only
   parser. For `merge`, only while handling the explicit authenticated request, first render/post its
   bound `tether-swarm-merge-authority` comment. Refetch it and pass its raw body plus server ID,
   repository, issue, author, and timestamps to `run-comment`; only that validated ID enters the run
   record. Require equal creation/update times and its record clock within five minutes. Never synthesize
   authority from an issue, resume, or worker. `PR-ready` requires null. An uncertain authority write
   freezes without retry. After posting the separate start comment, refetch all anchor comments and use
   `run-lineage` to require the authority's server ID/time strictly before/no later than the start, plus
   the same immutable server-time/record-clock checks.
   For one run ID, the lowest-ID valid exact-binding owner-authored record
   is canonical; higher duplicates are inert and freeze that run. On uncertain creation,
   never retry: adopt exactly one proven record or freeze. Retain its comment ID in the private ledger.
7. Run mode is append-only: never edit a start/event comment. Before every run-authorized mutation or
   mode event, refetch every anchor comment. Build `run-lineage` input only from the authenticated
   connector's raw body, server ID, repository, issue, author, and creation/update times; never fabricate
   an envelope or hand-resolve state. Include every other owner-authored comment containing any
   `tether-swarm-` marker token as an event candidate, even when malformed. The helper re-parses each raw
   body, rejects mixed/malformed tokens, and binds the server metadata.
   It requires a single lineage rooted at the canonical start. Each `tether-swarm-run-transition` must
   have the same binding, a greater server ID, and the exact predecessor comment ID plus canonical-record
   SHA-256. Supply each comment's server creation/update timestamps; they must be equal and within five
   minutes of its record clock, so an edited leaf also freezes. The helper returns the unique leaf; call
   `run-transition` with that fetched body and both IDs, post one new event, then refetch and verify it.
   `running -> draining|frozen|completed` and `draining -> frozen|completed` are the only edges; terminal
   events have no descendants. Any malformed owner marker, edit/digest mismatch, orphan, duplicate child,
   fork, unexpected descendant, or uncertain write makes the effective state `frozen`; do not mutate,
   merge, refill, or clean up. Never retry an uncertain event. Policy and authority are immutable.

## Select and claim work

- Use the GitHub connector first. Eligible means an open public non-question issue with the exact
  `status:ready` label, no assignee other than the requested owner, acceptance criteria in its body,
  no unresolved blocking question, and no canonical lease. Compute the helper's normalized title/body
  hash and require a comment by the requested owner containing
  `<!-- tether-agent-ready {"version":1,"criteria_sha256":"HASH"} -->` for that exact hash. The swarm
  never creates/edits approval comments. Every declared dependency must be closed; absent means none.
- Sort `priority:P0` through `P3`, then unprioritized work, then oldest creation time. Re-scan before
  every claim. Blocked or human-input work is ineligible and does not consume a slot.
- Immediately before every candidate and again before worktree creation, inventory that item-linked
  task, lease, branch, worktree, PR, and job state. Require none, or adopt one exact authorized handoff;
  any manual/new/uncertain state freezes the claim.
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
- Before a renewal or transition, refetch the canonical server comment by ID; pass its full immutable
  run/repository/issue/owner/worker/base/branch/criteria/approval tuple to the helper, then update only
  that same comment ID. Prove this is the sole active coordinator task for the run; if the API exposes
  an ETag/revision, require a conditional update, then refetch and verify the result. Without either
  exclusivity proof or revision safety, freeze. Require the renewed `issued_at` within five minutes of
  server `updatedAt`; a binding mismatch, clock drift, or concurrent edit freezes.
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
2. Title the task `<run_id> | #<issue> | <agent-id>`. Its prompt must begin exactly
   `/goal $solve-issue-goal Solve issue #N to PR-ready in OWNER/REPOSITORY.` Then provide `run_id`,
   owner/persona/worker ID, lease comment ID and expiry, `CLAIM_BASE_SHA`, conventional branch, worker
   terminal `PR-ready`, and the separate coordinator run policy. End with an initialization hold: verify
   and report exact checkout/HEAD/lease, but do not rename or edit. After its acknowledgement, independently
   recheck the single worktree and active lease, then send `GO`; any drift archives/freeze-hands off.
   Every worker stops at PR-ready and never merges, claims another issue, or edits another checkout.
3. Keep at most `count` active worker tasks. The app title plus public lease are the durable join keys;
   record issue, thread/host IDs, worktree, branch, comment, heartbeat, PR, head/base/review state, and
   Slurm tuple. Reject a worker ID already present anywhere in this run's issue comments. The sole
   exception is resuming its parked PR-ready task: require exactly one unreleased latest `handoff`, no
   active lease, and the identical run/repository/issue/owner/base/branch/scope/approval tuple, inactive
   thread, and same worktree before a fresh active comment with that ID can compete. Once it wins,
   transition the superseded handoff to `released`. Never reuse the ID for other work.

## Monitor, review, merge, and refill

- Wait on up to eight worker threads with `wait_threads` using at most a 45-second timeout and saved
  cursors. After restart, list app threads and rebuild the ledger from titles, GitHub leases/PRs, and
  git; never trust only in-memory state.
- Renew active leases hourly. If a lease expires or ownership changes, tell that worker to freeze and
  require coordinator reauthorization. Send follow-ups to the same thread for CI/review fixes.
- The coordinator alone routes external reviews. Copilot is optional and best-effort; record whether it
  was not requested, unavailable, quota-exhausted, pending, or complete, but it never blocks a slot or
  merge. Low and standard lanes select Codex GitHub Code Review or CodeRabbit; high/load-bearing lanes
  require CodeRabbit after the stable diff is green. Accept only a substantive PR diff walkthrough from
  Codex GitHub Code Review or CodeRabbit, bound to the final head SHA. For either provider, refetch the
  expected reviewer identity, server-bound reviewed
  commit/head, and walkthrough before accepting evidence. Author-side `/review`, local CodeRabbit output,
  a status/check alone, denial, provider unavailability, or a summary without a diff walkthrough never
  satisfies that independent gate. Resolve every conversation and every actionable finding; any head
  change invalidates the SHA binding, and a material change requires each affected layer again.
- When CodeRabbit is required or selected, keep one durable PR comment keyed by
  `(repository, PR, head SHA)` with marker `<!-- tether-coderabbit-queue RECORD -->`; `RECORD` contains
  version, head SHA, state (`queued|triggered|rate-limited|complete`), attempt time, and retry time.
  Use fully paginated `gh api graphql` search for accessible PRs
  commented on by the owner since the UTC date containing the rolling-hour boundary, then fully paginate
  each result's comments. Prove every `hasNextPage` is false and the search has at most 1,000 results;
  otherwise queue. Automated triggering also requires the maintainer-designated singleton dispatcher
  to hold an exclusive account lock under `$CODEX_HOME`; never auto-expire or steal it. Other hosts,
  cloud runs, lock uncertainty, or a second dispatcher queue for manual dispatch. Count exact
  owner-authored `@coderabbitai review` comments by server `createdAt` in
  the rolling hour; queue at five, else trigger once. Retain only matching author/time/body predicates,
  not unrelated comment bodies. Refetch the queue comment by ID and require owner, repository, PR, and
  head; duplicate or uncertain creation freezes. Refetch the trigger, expected CodeRabbit bot author,
  reply, check, substantive walkthrough, and reviewed head. A denial is not a review: record its server
  retry time, or wait a full hour if absent, then retry that unperformed review once. Allow only one
  in-flight attempt per run;
  independent runs may race, so a denied attempt queues and never implies a reservation. Never
  retrigger a completed unchanged-head review; without a substantive required review, do not merge.
- A worker completion is only a PR-ready handoff. Verify clean tree, linked issue, final diff, tests,
  provenance, required review path, conversations, and exact PR/head/base state independently. For a
  `PR-ready` run this is terminal; do not enter the merge path, including while draining.
- A pending required review or CI run keeps that slot occupied and its lease renewed. CodeRabbit quota
  does so only when CodeRabbit is required or selected; optional Copilot quota never blocks. Never
  downgrade, duplicate the worker, or refill the slot merely to improve throughput.
- Only when the recorded policy is exactly `merge` and explicit run-scoped authority still exists may
  the coordinator refetch both comments, resolve their full server envelopes with `run-lineage`, and
  fetch a fresh full `MERGE_BASE_SHA`; require the intended repository,
  verified default base ref/object, expected head branch/SHA, and green final state. Re-read changed
  governing files, revalidate the approved criteria hash, require the selected substantive review to
  match the exact head, and pause for user resolution on conflict.
  With strict up-to-date protection, require
  the head contains that base and direct merge with `squash` plus `expected_head_sha`. With a verified
  queue, enqueue that head under the squash policy and monitor; never direct merge. Any drift freezes it.
- For a `PR-ready` outcome, require the worker turn to end, send no further mutation prompt, set status
  `in-review`, transition the lease to `handoff`, record the clean handoff, and preserve its parked task,
  branch, and single worktree; do not perform merge cleanup. Its handoff lease forbids further writes.
  Resume review fixes only in that same task/worktree after a fresh active lease for the same identity,
  branch, and approved scope wins; never create a second checkout. After an authorized merge,
  confirm the issue outcome, transition the lease to `completed`, set status `done`, archive the worker
  task, and remove only exact run/agent-namespaced worktree/branch/ref after proving each clean, unused,
  and matched to the merged PR.
- After either recorded terminal outcome, running mode claims the next eligible issue with a new ID and
  persona distinct from active workers, using the existing `run_id` and one `--exclude-persona` per
  active persona. Drain mode leaves the slot empty.
- Finish only after two scans separated by a polling interval find no eligible issues or active workers,
  or after graceful drain reaches every active item's policy: handoff for `PR-ready`, guarded merge for
  `merge`. If a run record exists, append a verified `completed` event from its unique predecessor.
  Report merged PRs or PR-ready handoffs, skipped work, blockers, reviews, leases, and cleanup.

## Stop and recovery

- Plain `stop`: append and verify a `draining` event, visibly report it, create no new claims, and let
  active workers
  reach the immutable run policy—PR-ready handoff without merge, or guarded merge when authorized.
  Do not send them the single-worker stop command.
- `Emergency stop`: append and verify a `frozen` event, cease new mutations, send each worker the
  `$solve-issue-goal` safe-stop command,
  and cancel only recorded task-owned jobs. Transition a lease to `handoff` only after the worker
  acknowledges and its thread is inactive; otherwise leave it visible, report an unconfirmed freeze,
  preserve its worktree, and never refill or clean that slot.
- A worker crash does not release ownership or permit a second branch or writable worktree. First
  resume the same task, identity, branch, and worktree. If impossible, freeze and inventory its PR,
  jobs, branch, and checkout. Preserve recoverable work in an authorized recorded commit or sanitized
  checksum-recorded handoff archive; prove the old task cannot mutate and safely retire its writable
  checkout, then transition its active lease to `handoff` or `released` and refetch that state. Only then
  may a replacement task resume the same issue branch from that handoff with a new worker ID. Never let
  old and replacement tasks mutate concurrently or reuse an ID for different work.
- On coordinator restart, list tasks and join title IDs to leases, branches, PRs, and jobs. Reconcile
  orphaned `in-progress` items before refilling. Fully paginate repository issue comments to find strict
  owner-authored run records; join their run IDs to task titles and leases, then refetch the canonical
  anchor comment by ID. If repository, exact private filter/digest, owner, count, effective mode, terminal
  policy, or run identity is missing/ambiguous, freeze all mutations and claims. Never reconstruct
  `running` over `draining`, `frozen`, or `completed`. If merge authority is missing or invalid, never
  merge in that run; finish safe PR-ready handoffs, then require a new explicitly authorized run.

## Lease helper

Run `python .agents/skills/run-issue-swarm/scripts/swarm_lease.py --help`. The standard-library-only
helper generates and strictly inspects run, merge-authority, identity, scope, and lease records; it also
resolves complete lineages, renders predecessor-bound append-only run events, and performs lease
renewals/transitions. Exactly four hours
is the only accepted lease duration. New lease comments require the approved
`--repository`, `--criteria-sha256`, and `--approval-comment-id`; renewal requires expected repository,
run, issue, owner, agent, base, branch, criteria, and approval arguments; transitions require the same
binding. An expired active lease may transition only to `handoff` or `released`. Never set the isolated
test-clock environment or use `--now` in a live run. The helper never calls GitHub; the coordinator posts
its output through the authenticated connector.
