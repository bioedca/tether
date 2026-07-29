---
name: run-issue-swarm
description: Compute and verify the SHA-256 scope digest that binds a maintainer's swarm approval comment to the exact issue snapshot they approved. Use when checking whether an approval still binds after an issue edit, or when rendering a new approval marker. This is not a coordinator - it starts nothing and calls no network service.
---

# Swarm approval digest

The coordinator runbook that used to live here is retired. Claims are git refs, not comments:
`POST /git/refs` returns `201` to the first writer and `422 Reference already exists` to everyone
after, so there is no lease, no TTL, no heartbeat, no lowest-comment-ID election, and no
coordinator agent. The decision is recorded in
`docs/adr/0057-github-native-swarm-coordination.md`, and the worker contract is root `AGENTS.md`.

What survives is the **approval binding**. A maintainer approves an exact issue snapshot by
commenting a marker carrying the SHA-256 of its normalized title and body:

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
comment that mixes or repeats coordination markers, so an approval sitting alongside stale lease
or run state from the retired model is rejected rather than half-read; a marker name merely
mentioned in prose is not a claim and does not trip it.

Digests published before this rewrite still verify — the normalization is frozen and pinned by a
regression test.
