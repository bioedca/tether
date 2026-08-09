---
name: tether-worker
description: Work one accepted Tether issue as a short-lived peer worker — claim it with the atomic ref mutex, implement it in an isolated worktree, open a draft pull request, get it reviewed, and finish with either a PR-ready handoff or an explicitly authorized merge — never infer merge authority. Use when an agent is asked to solve, resume, or hand off a single work item. There is no coordinator to ask.
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
