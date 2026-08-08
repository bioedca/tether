---
name: tether-worker
description: Work one accepted Tether issue as a short-lived peer worker — claim it with the atomic ref mutex, implement it in an isolated worktree, open a draft pull request, get it reviewed, and hand off or merge. Use when an agent is asked to solve, resume, or hand off a single work item. There is no coordinator to ask.
---

# Tether worker

Root `AGENTS.md` is the operational contract and `docs/PRD.md` the product detail. This skill adds
only what is specific to being **one peer worker among several**; it never restates or relaxes them.

**GitHub is the coordinator; there is no coordinator agent** (ADR-0057). Nothing serializes you and
nothing renews a lease for you. You are short-lived: claim, work, push, get reviewed, hand off or
merge, exit.

## Shell

The lanes run in **different shells**: `claude` in WSL bash, `codex` and `copilot` in native
PowerShell. This file is **not templated** — both lanes read these same lines — so every command
below is written to be valid in either: a bare executable name followed by arguments. Never the
PowerShell `&` call operator, and never an absolute path such as
`C:\Program Files\GitHub CLI\gh.exe`, which does not exist inside WSL.

`<py>` is your lane's interpreter — **`python3` in WSL bash, `python` in native PowerShell**. WSL
provides no `python` at all, and on Windows the python.org installer registers `python` while
`python3` may be an unconfigured Store stub, so neither name is safe for both. Substitute it as you
read; that single token is the entire difference. If neither name resolves, report it; do not paste
a path. `gh` needs no such rule.

Where a command takes `--vendor`, pass your own lane.

## Claim

1. Confirm the issue number and the terminal condition. Eligibility is a **precondition** of the
   claim, not a consequence: open, `status:ready`, no competing assignee, a body declaring
   `agent-can-do-alone`, and a maintainer approval bound to the exact title/body snapshot you are
   about to act on.
2. Take the mutex. One call decides it — `201` is yours, `422` means someone else got there first:

   ```sh
   <py> .agents/bin/claim.py claim --issue N --vendor <your lane>
   ```

   Exit `3` is *ineligible* (do not work it), `4` is *lost* (stop; do not open a second branch or
   PR for that issue). Success prints the branch, the base SHA, and your **generation**.
3. Record the generation. Before every authoritative write — a push you intend to be merged, a PR
   state change — revalidate:

   ```sh
   <py> .agents/bin/claim.py check --issue N --generation G
   ```

   Exit `5` means a reaper reclaimed the claim and a successor owns it. **Stop writing.** Your work
   is not lost; it is simply no longer authoritative.
4. Work in your own worktree on `agent/issue-N` from the recorded base SHA. Never share a branch or
   worktree, and never edit another worker's checkout.

If you must abandon the work, release the claim rather than letting it rot:

```sh
<py> .agents/bin/claim.py release --issue N --generation G --vendor <your lane>
```

`release` refuses when the ref's generation is not yours, so a stale worker cannot delete a
successor's claim.

## Work

Follow `AGENTS.md` §Agile execution: smallest complete increment, implementation and tests and docs
and provenance in one PR, the local gates before review. Solve the claimed item and nothing else —
a reproducible unrelated finding becomes a separate templated issue, never extra scope here.

Need an ADR? Reserve the number atomically instead of picking one:

```sh
<py> .agents/bin/claim.py reserve-adr
```

Gaps in ADR numbering are legal and expected. Never reuse a number, and never renumber to close a
gap — that is what invalidated reviews across three PRs at once under the old model.

## Finish

**Open the PR as a draft** and get the checks green there. Then follow `AGENTS.md` §Review: an
external provider reads the final head, you fix what is serious and defer or drop the rest, and
CodeRabbit with no actionable comments is the last gate before merge.

You do not have to sit and watch it. A review takes as long as it takes, and a short-lived worker
that polls is spending tokens to wait — so **write the state into the PR body before you go**:
which step it is on, what was asked, what is outstanding. A later session reads that and continues.

Merge under explicit per-PR authority, then arm and exit:

```sh
gh pr merge N --auto --squash --match-head-commit <SHA>
```

`<SHA>` is the 40-hex head the clean review read, and you supply it from that review. Re-reading it
from the pull request while arming compares the head against itself and binds nothing. There is no
merge queue on this repository — it needs an organization-owned repo — so that flag is what
replaces it.

## Maintainer-side commands

Approving a scope snapshot is a maintainer action, not a worker one. It prints both the digest and
the marker to paste:

```sh
<py> .agents/bin/claim.py scope-hash --issue N
```

The digest covers the normalized title and body, so any later edit to either provably invalidates
the approval and a fresh one is required.
