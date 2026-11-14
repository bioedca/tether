<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0096 — The lock lifecycle guard is shared with everyone who can write the project directory

- **Status:** accepted; amends [ADR-0055](0055-gui-session-writer-ownership.md) on the guard's permissions
- **Date:** 2026-11-14
- **Deciders:** bioedca
- **PRD anchor:** §5.4 (single writer, steal-lock), §7.10 FR-CONCURRENCY
- **Milestone:** M12
- **Issue:** [#257](https://github.com/bioedca/tether/issues/257)

## Context and problem statement

[ADR-0055](0055-gui-session-writer-ownership.md) put every acquire, explicit steal, retained
refresh and release under an OS advisory lock on a stable `<project>.lock.guard` file. It created
that file with mode `0666` *subject to the site umask*, so that a group-writable project could keep
one cross-user `O_RDWR` guard.

That works only when the umask leaves group write in place (`umask 002`). Under the common
`umask 022` the guard is created `0644`, and only its first creator can open it `O_RDWR`. A second
user with write access to the project directory then gets `PermissionError` before `acquire()` —
or even an explicit `steal_lock()` — can read the sidecar. The documented stale-lock recovery
(troubleshooting: *"a stale lock is reclaimable only by an explicit steal"*) was unavailable to
everyone but the guard's creator until someone removed or `chmod`ed the guard by hand. Reproduced
on 2026-11-14 with two real POSIX users sharing a group, both under `umask 022`: the first user's
`acquire()` left a `0644` guard, and the second user's `steal_lock()` raised
`PermissionError: [Errno 13] Permission denied: '<project>.tether.lock.guard'`.

## Decision drivers

- The second user can already create, replace and delete the `.lock` sidecar, because they can
  write the directory. The guard must not demand more than that.
- The guard is never unlinked or path-replaced (ADR-0055), so a fix cannot recreate it; guards
  that earlier releases left `0644` must keep working.
- No change to the lock file format, the `.tether` schema, or the return-leg ownership guard of
  #237.

## Considered options

1. **Create the guard `0666` regardless of the umask** (`fchmod` after creation). Simple, but it
   makes the guard world-writable in a directory only one group can write — wider than needed.
2. **Grant read/write to each class that can write the directory, and open a guard you cannot
   write read-only.** The grant mirrors what the directory already allows; the read-only open
   covers guards owned by someone else that nobody has repaired yet.
3. **Replace an unwritable guard.** Rejected: path-replacing the guard while another process holds
   the old inode splits the mutex, which ADR-0055 forbids.

## Decision outcome

Chosen option: **2**.

- After opening the guard on POSIX, its **owner** adds read and write for the group if the project
  directory is group-writable, and for others if the directory is other-writable. Bits are only
  added, never removed; nothing changes for a directory only its owner can write. Because the
  owner does this on every lifecycle operation, a `0644` guard left by an earlier release is
  repaired the next time its owner acquires, refreshes, steals or releases. `fchmod` is best
  effort — a filesystem that refuses it does not fail the lock.
- Where the directory is group-writable, the owner also gives the guard the **directory's group**
  (`fchown`, best effort). Without a setgid directory Linux gives a new file its creator's primary
  group, which need not be the group the project is shared with, and the mode bits alone would
  then admit nobody.
- The guard is opened with `O_NOFOLLOW`, and its permissions or group change only when the
  descriptor is a regular file with one link that the guard path itself names. A directory writer
  can plant a symlink or a hard link at the guard path; a symlink now fails the operation closed,
  and neither can make Tether widen access to an unrelated file the caller owns.
- A user who is **not** the owner and cannot open the guard `O_RDWR` opens it read-only.
  `flock()` takes an exclusive lock whatever the descriptor's access mode, so that user still
  serializes with every other process and can run the explicit steal.
- Some network filesystems emulate `flock()` with byte-range locks, which need a writable
  descriptor. There the read-only lock fails with `EBADF`, and Tether raises a `PermissionError`
  that names the guard and the remedy (its owner makes it group-writable), instead of an
  unexplained `OSError`.
- Windows is unchanged: it has no POSIX mode bits, and its guard is a byte-range lock that needs a
  writable descriptor.

### Consequences

- Good: on a shared project under `umask 022`, a second user can open the guard and run the
  documented stale-lock recovery; `tests/test_lock.py` covers creation, repair, the directory's
  group, symlinked and hard-linked guards, the read-only fallback, its `EBADF` message, and (when
  run as root) two real users with different primary groups end to end.
- Good: the guard never grants a class write access that the directory does not already grant.
- Good: sharing the guard cannot change the mode or group of any file but the guard itself.
- Neutral: a guard path that is a symlink — which no release of Tether creates — now fails every
  lifecycle operation with `OSError` (`ELOOP`) until it is removed.
- Neutral: in a world-writable directory (for example a sticky `/tmp`) the guard becomes `0666`.
  That grants nothing new — any user who can read the guard can already `flock()` it.
- Bad: a guard owned by a user who never runs Tether again stays `0644`. On a local filesystem the
  read-only fallback covers it; on an NFS-like filesystem its owner (or an administrator) must
  `chmod g+rw` it once, which the error message says.

## More information

- [ADR-0055](0055-gui-session-writer-ownership.md) — the lifecycle guard this amends.
- `src/tether/project/lock.py` — `_open_guard` and `_share_guard_with_directory_writers`.
- Defect 1 of #257 (reopening the same project replaced its lock epoch) moved to #346.
