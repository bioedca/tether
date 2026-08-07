<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Tether agent contract — Claude Code (pointer)

**`AGENTS.md` is the contract and `AGENTS.md` is authoritative. Read it now; it is the operative
text and this file carries none of it.**

@AGENTS.md

This page exists only because Claude Code loads `CLAUDE.md` and not `AGENTS.md`. It used to be a
hand-written *adaptation* — the same rules reworded — and the cost of that was drift in both
directions at once: two files to edit for every contract change, only 49 of ~150 lines byte-identical,
and 151 lines of test written to police the gap. ADR-0064 ends it. `.claude/skills/tether-worker/SKILL.md`
already resolved the identical problem the identical way, and its reasoning applies verbatim here:
duplicating the text would mean two lanes could be told different things about the same mutex, which
is the one asymmetry this repository cannot afford.

Nothing here is lane-specific, deliberately — including the interpreter, which `AGENTS.md` already
resolves for both lanes. A pointer that started carrying commands would be an adaptation again.
