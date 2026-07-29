<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# WSL clusters and Slurm

These rules are part of the agent contract. `AGENTS.md` points here and grants no remote-compute
authority on its own: **an agent that has not read this page is not authorized to touch a cluster.**
Authorization still has to come from `AGENTS.md` — the goal or maintainer must explicitly name the
exact cluster, data, account, and resource ceiling.

- Use remote compute only when local execution is impractical and the goal or maintainer explicitly
  authorizes the exact cluster, data, account, and resource ceiling. From WSL set `CLUSTER` to
  exactly `zero`, `one`, or `two`; endpoints, users, keys, and tokens live only in `~/.ssh/config`.
- On first use each session, fail closed unless WSL, strict host keys, aliases, and `sbatch squeue
  sacct scancel srun sinfo` pass a noninteractive `BatchMode=yes`/`ConnectTimeout=10` probe. Never
  edit SSH state, accept an unknown host key, forward an agent, or weaken checks autonomously.
  Use `ssh -n -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1
  -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ForwardAgent=no "$CLUSTER"
  'hostname >/dev/null && for c in sbatch squeue sacct scancel srun sinfo; do command -v "$c"
  >/dev/null || exit 127; done'`.
- Never compute on login nodes, run daemons/nohup, or recurse through SSH. Submit with `sbatch`;
  use `srun` only inside an allocation and when site policy permits.
- Build one `git archive <SHA>` from a clean commit; reject links/devices/absolute/traversal entries.
  Secret-scan names and extracted bytes, record its digest, transfer those bytes, and verify remotely;
  allowlist data separately. Extract under atomic `mktemp` in verified scratch; require owner, mode
  700, resolved non-symlink path. Never copy `.git`, `.env`, credentials, or a home tree.
- Batch scripts use `set -euo pipefail`, `umask 077`, explicit environment/resources, `%x-%j` logs,
  and conservative limits. Never guess account, partition, QoS, or site policy. Use `--export=NIL`
  only if installed `sbatch --help` supports it; otherwise require the site-approved clean pattern.
- Submit once with `sbatch --parsable`; require a numeric job ID and retain the full tuple `(SSH
  alias, returned Slurm cluster if any, job ID, owner, submission time)`. Use that tuple for exact-ID
  `squeue`/`sacct` queries and poll no faster than 30 seconds.
- `scancel` only that task-created tuple, on explicit stop or a documented safety breach—never by
  user, name, or wildcard. Accept results only after logs, expected outputs, checksums, provenance,
  terminal state, exit code, and resources agree.
