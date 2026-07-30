<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Claude Code in this repository

**`AGENTS.md` is the contract and it governs you.** Read it before acting. This file exists only
because Claude Code loads `CLAUDE.md` and not `AGENTS.md`, so its job is to *route* you there — not
having read `AGENTS.md` is itself a bar to acting, and nothing on this page substitutes for it.

This file is deliberately short and deliberately **restates no rule**. A second contract drifts from
the first, and the drift is invisible until an agent follows the stale copy: #280 spent twelve sites
removing exactly that duplication, and a test in `tests/test_agent_entry_points.py` keeps this page
from growing back into one. If a rule is worth writing down, it belongs in `AGENTS.md` or
`docs/agents/`.

## Where the rules actually live

| | |
|---|---|
| **`AGENTS.md`** | operations and safety — authority, claiming, execution, review, handoff |
| `docs/agents/review.md` | the review gate: routing, materiality, severity floor, the round cap |
| `docs/agents/gates.md` | the local gate commands to run before review |
| `docs/agents/tools.md` | library/API/CLI/workflow behaviour — Context7 first, version-matched |
| `docs/agents/evidence.md` | scientific claims, oracles, provenance — Consensus first |
| `docs/agents/adr.md` | ADR numbering; reserve, never pick |
| `docs/agents/hpc.md` | WSL clusters and Slurm |
| `docs/PRD.md` | product and science (not served by the docs site — read it in the repo) |
| `CONTRIBUTING.md` | contributor-facing detail on branching, merging and the checks |

## What is specific to this lane

- **Working an issue?** Use the `tether-worker` skill. `.claude/skills/tether-worker/` is a pointer;
  the skill itself is `.agents/skills/tether-worker/SKILL.md`, shared with the Codex lane so the two
  lanes cannot be told different things.
- **Answering a question about this codebase?** `graphify` builds and queries a knowledge graph of
  it; `graphify query "<question>"` usually returns a smaller, better-targeted subgraph than a raw
  search. Rebuild with the `/graphify` skill after significant changes.
- **Do not assume a CLI is on `PATH`.** This project's tooling is split across native Windows and
  WSL, and which is which differs per machine — `claude`, `codex`, `gh` and the CodeRabbit CLI are
  not all reachable the same way. Check before scripting one, and record what you found in
  `CLAUDE.local.md` rather than here.

## Per-machine setup belongs in `CLAUDE.local.md`

Absolute paths, which shells and CLIs exist where, local virtual environments, credential locations:
all of that is true of one checkout and false of the next, so it lives in `CLAUDE.local.md`, which is
gitignored. Keeping it out of this file is what lets this one be reviewed.
