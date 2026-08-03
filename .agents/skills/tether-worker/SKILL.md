---
name: tether-worker
description: Work one accepted Tether issue as a short-lived peer worker — claim it with the atomic ref mutex, implement in an isolated worktree, open a draft PR, open the review lane, hand off, and exit. Use when an agent is asked to solve, resume, or hand off a single work item, or when a launcher injects a build or amend task. There is no coordinator to ask.
---

# Tether worker

Root `AGENTS.md` is the operational contract and `docs/PRD.md` the product detail. This skill adds
only what is specific to being **one peer worker among several**; it never restates or relaxes them.

**GitHub is the coordinator; there is no coordinator agent** (ADR-0057). Nothing serializes you,
nothing renews a lease for you, and nothing merges on your behalf. You are short-lived: claim, work,
push, open the review lane, hand off, exit. Auto-merge is armed at the **end** of that lane, by
whoever completes it — see §Finish. Arming it earlier merges the PR past the mandatory CodeRabbit
gate, because that gate is not a required check and nothing else is holding the merge.

## Shell

The lanes are dispatched into **different shells**: `claude` runs inside WSL bash, `codex` and
`copilot` run in native PowerShell. This file is **not templated** — both lanes read these same
lines — so every command below is written to be valid in either: a bare executable name followed by
arguments. Never the PowerShell `&` call operator, and never an absolute path such as
`C:\Program Files\GitHub CLI\gh.exe`, which does not exist inside WSL.

**Resolve the interpreter for your lane before running anything below.** One name does not fit both,
and pretending otherwise is the defect this section exists to prevent:

| lane | shell | interpreter |
|---|---|---|
| `claude` | WSL bash | `python3` |
| `codex` | native PowerShell | `python` |
| `copilot` | native PowerShell | `python` |

WSL provides no `python` at all, and on Windows the python.org installer registers `python` while
`python3` may be an unconfigured Store stub — so neither name is safe for both.

The commands below are written `python3`, the dispatched `claude` lane's name. **On a native lane,
substitute `python`.** That single substitution is the entire difference; no other token in any
command changes between lanes. The launcher already applies it for you in injected task text —
`{{PYTHON}}` renders from `swarm_slots.LANE_PYTHON`, which is the same table as above — so this
matters only for hand-driven runs. If neither name resolves, report it; do not paste a path.

`gh` needs no such rule: it resolves from `PATH` in both shells.

Where a command takes `--vendor`, pass **your own lane** — the `Vendor lane` row of the task text
the launcher injected, or, hand-driven, the vendor of the CLI you are running.

## Claim

1. Confirm the issue number and the terminal condition. Eligibility is a **precondition** of the
   claim, not a consequence: open, `status:ready`, no competing assignee, and a maintainer approval
   bound to the exact title/body snapshot you are about to act on.
2. Take the mutex. One call decides it — `201` is yours, `422` means someone else got there first:

   ```sh
   python3 .agents/bin/claim.py claim --issue N --vendor <your lane>
   ```

   Exit `3` is *ineligible* (do not work it), `4` is *lost* (stop; do not open a second branch or
   PR for that issue). Success prints the branch, the base SHA, and your **generation**.
3. Record the generation. Before every authoritative write — a push you intend to be merged, a PR
   state change — revalidate:

   ```sh
   python3 .agents/bin/claim.py check --issue N --generation G
   ```

   Exit `5` means a reaper reclaimed the claim and a successor owns it. **Stop writing.** Your work
   is not lost; it is simply no longer authoritative.
4. Work in your own worktree on `agent/issue-N` from the recorded base SHA. Never share a branch or
   worktree, and never edit another worker's checkout.

If you must abandon the work, release the claim rather than letting it rot:

```sh
python3 .agents/bin/claim.py release --issue N --generation G --vendor <your lane>
```

`release` refuses when the ref's generation is not yours, so a stale worker cannot delete a
successor's claim.

## Work

Follow `AGENTS.md` §Agile execution: smallest complete increment, implementation and tests and docs
and provenance in one PR, the local gates before review. Solve the claimed item and nothing else —
a reproducible unrelated finding becomes a separate templated issue, never extra scope here.

Need an ADR? Reserve the number atomically instead of picking one:

```sh
python3 .agents/bin/claim.py reserve-adr
```

Gaps in ADR numbering are legal and expected. Never reuse a number, and never renumber to close a
gap — that is what invalidated reviews across three PRs at once under the old model.

## Finish

**Open the PR as a draft**, get the checks green, record the review risk with its reason, and request
the first Codex review. Then **exit** — do not sit and poll.

You open the lane in `docs/agents/review.md`; you do not walk it to the end. Every later phase — the
optional Greptile credit, marking ready, the mandatory CodeRabbit gate, arming auto-merge — begins
only *after* a review lands, and waiting for one is exactly what a short-lived worker must not do. So
**write the lane state into the PR body before you go**: which phase it is in, what was asked, what
is outstanding. A later session reads that and continues. It is the only thing carrying the lane
forward.

> Dispatching this lane to a worker is gated on **two** independent blockers, and both must land:
> [#394](https://github.com/bioedca/tether/issues/394) — a clean review publishes no resumption
> authority, so nothing reopens the claim — and
> [#391](https://github.com/bioedca/tether/issues/391) — `swarm_slots.py` counts draft AMEND
> sessions against its permanent cap, so free draft iteration exhausts the launcher even when
> `triage.py` correctly reports zero rounds. Until both land the later phases are completed by hand.
> Never work around it by polling, by marking a PR ready before its draft phase is done, or by
> merging without the CodeRabbit gate.

Auto-merge is armed at the **end** of the lane, by whoever completes it — not on the draft:

```sh
gh pr merge N --auto --squash --match-head-commit <SHA>
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

```sh
python3 .agents/bin/claim.py scope-hash --issue N
```

The digest covers the normalized title and body, so any later edit to either provably invalidates
the approval and a fresh one is required.
