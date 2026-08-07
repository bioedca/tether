---
name: tether-worker
description: Work one accepted Tether issue as a short-lived peer worker — claim it with the atomic ref mutex, implement in an isolated worktree, hand off, and exit. A BUILD session opens the draft PR and the review lane on it; an AMEND session continues the pull request that already exists, answering one round on it; an ADVANCE session continues it too, moving the lane on by exactly one phase and taking no round. Neither of the latter two re-opens or re-drafts a pull request. Use when an agent is asked to solve, resume, or hand off a single work item, or when a launcher injects a task from .agents/tasks/. There is no coordinator to ask.
---

# Tether worker (pointer)

**The skill is `.agents/skills/tether-worker/SKILL.md`. Read that file now; it is the operative
text and this one carries none of it.**

This page exists because the two lanes look for skills in different places — Claude Code reads
`.claude/skills/`, the Codex lane reads `.agents/skills/` — while `AGENTS.md` prescribes one
invocation for both. Duplicating the skill here would mean two lanes could be told different things
about the same mutex, which is the one asymmetry this repository cannot afford. So this is a pointer,
and `tests/test_agent_entry_points.py` asserts it stays one.

Root `AGENTS.md` governs regardless; the worker skill only adds what is specific to being one peer
among several.
