---
name: tether-worker
description: Work one accepted Tether issue as a short-lived peer worker — claim it with the atomic ref mutex, implement in an isolated worktree, open a PR, arm auto-merge, and exit. Use when an agent is asked to solve, resume, or hand off a single work item, or when a launcher injects a build or amend task. There is no coordinator to ask.
---

# Tether worker

Root `AGENTS.md` is the operational contract and `docs/PRD.md` the product detail. This skill adds
only what is specific to being **one peer worker among several**; it never restates or relaxes them.

**GitHub is the coordinator; there is no coordinator agent** (ADR-0057). Nothing serializes you,
nothing renews a lease for you, and nothing merges on your behalf. You are short-lived: claim, work,
push, arm auto-merge, exit.

## Claim

1. Confirm the issue number and the terminal condition. Eligibility is a **precondition** of the
   claim, not a consequence: open, `status:ready`, no competing assignee, and a maintainer approval
   bound to the exact title/body snapshot you are about to act on.
2. Take the mutex. One call decides it — `201` is yours, `422` means someone else got there first:

   ```powershell
   python .agents/bin/claim.py claim --issue N --vendor claude
   ```

   Exit `3` is *ineligible* (do not work it), `4` is *lost* (stop; do not open a second branch or
   PR for that issue). Success prints the branch, the base SHA, and your **generation**.
3. Record the generation. Before every authoritative write — a push you intend to be merged, a PR
   state change — revalidate:

   ```powershell
   python .agents/bin/claim.py check --issue N --generation G
   ```

   Exit `5` means a reaper reclaimed the claim and a successor owns it. **Stop writing.** Your work
   is not lost; it is simply no longer authoritative.
4. Work in your own worktree on `agent/issue-N` from the recorded base SHA. Never share a branch or
   worktree, and never edit another worker's checkout.

If you must abandon the work, release the claim rather than letting it rot:

```powershell
python .agents/bin/claim.py release --issue N --generation G --vendor claude
```

`release` refuses when the ref's generation is not yours, so a stale worker cannot delete a
successor's claim.

## Work

Follow `AGENTS.md` §Agile execution: smallest complete increment, implementation and tests and docs
and provenance in one PR, the local gates before review. Solve the claimed item and nothing else —
a reproducible unrelated finding becomes a separate templated issue, never extra scope here.

Need an ADR? Reserve the number atomically instead of picking one:

```powershell
python .agents/bin/claim.py reserve-adr
```

Gaps in ADR numbering are legal and expected. Never reuse a number, and never renumber to close a
gap — that is what invalidated reviews across three PRs at once under the old model.

## Finish

Open the PR, get the checks green, classify the review risk, and take the review gate in
`AGENTS.md` §Review gate. Then **arm auto-merge and exit** — do not sit and poll:

```powershell
& "C:\Program Files\GitHub CLI\gh.exe" pr merge N --auto --squash --match-head-commit <SHA>
```

`--match-head-commit` binds the merge to the head your evidence covers. There is no merge queue on
this repository (it needs an organization-owned repo), so that guard is what replaces it.

## Rounds are issued to you, not requested by you

A review round is not yours to open. The launcher is the only issuer of AMEND turns and it counts
them against the cap in `AGENTS.md` §Review gate. So:

- **At most one self-review pass**, before the first external request.
- **Never post a review-request comment on a PR carrying `agent:review-capped`.** At the cap,
  safety-class findings escalate to the maintainer and the rest become follow-up issues. Asking for
  another round is a contract violation, not diligence.

## Maintainer-side commands

Approving a scope snapshot is a maintainer action, not a worker one. It prints both the digest and
the marker to paste:

```powershell
python .agents/bin/claim.py scope-hash --issue N
```

The digest covers the normalized title and body, so any later edit to either provably invalidates
the approval and a fresh one is required.
