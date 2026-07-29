---
name: run-issue-swarm
description: Compute and verify the SHA-256 scope digest that binds a maintainer's swarm approval comment to the exact issue snapshot they approved. Use when checking whether an approval still binds after an issue edit, or when rendering a new approval marker. This is not a coordinator - it starts nothing and calls no network service.
---

# Swarm approval digest

The coordinator runbook that used to live here has been withdrawn, along with the tooling for the
lease and run-record protocol it drove.

**Read this before assuming how work is claimed.** `docs/adr/0057-github-native-swarm-coordination.md`
*decided* that a claim becomes an atomic git ref — `POST /git/refs` returns `201` to the first
writer and `422 Reference already exists` to everyone after — but its **Adoption status** section
records that the claim mutex, `agent/issue-<N>` branches, and the removal of the coordinator and
leases are **not yet implemented**. Nothing in this repository creates or checks such a ref today.
Root `AGENTS.md` still describes the coordinator-serialized protocol, and it remains the contract.

So the honest state is: the ADR-0052 lease protocol is **decided against but still written down**,
and its tooling is now gone. There is no automated claim mechanism in the interim. Work proceeds
under direct maintainer instruction, one agent at a time, until `claim.py` lands. Do not read the
paragraph above as permission to skip anything `AGENTS.md` requires.

What survives here is the **approval binding**, which is unaffected by any of that. A maintainer
approves an exact issue snapshot by commenting a marker carrying the SHA-256 of its normalized
title and body:

```
<!-- tether-agent-ready {"version":1,"criteria_sha256":"HASH"} -->
```

Because the digest covers the snapshot, any later edit to the title or body provably invalidates
the approval, and a fresh one is required. The swarm never creates or edits approval comments.

## Commands

Standard library only; the helper never calls GitHub. Fetch bodies from the server
(`gh issue view N --json title,body`) and write them with `newline=""` — a shell round-trip that
rewrites line endings silently changes the digest.

| Command | Purpose |
|---|---|
| `scope-hash --title T --body-file F` | print the digest for a snapshot |
| `ready-marker --title T --body-file F` | print the approval marker for a snapshot |
| `ready-inspect --file F [--comment-id N]` | parse and check a fetched approval comment |
| `verify --title T --body-file B --approval-file A` | check a fetched approval still binds the current snapshot |

```powershell
python .agents/skills/run-issue-swarm/scripts/swarm_lease.py --help
```

`verify` exits `0` when the approval binds and `2` when it does not. `ready-inspect` refuses a
comment that mixes or repeats coordination markers, so an approval sitting alongside stale lease or
run state from the withdrawn model is rejected rather than half-read.

The mixing check keys on an enumerated list of coordination marker names, so a *different* anchor
such as `tether-grooming-v1` or `tether-rescope-v1` does not trip it even when a comment names one.
It is not a Markdown parser: it does not know about code fences, and a real approval marker is
recognized wherever it appears in the comment body.

Digests published before this rewrite still verify — the normalization is frozen and pinned by a
regression test.
