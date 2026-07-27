<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0054 — GUI session writer ownership

- **Status:** accepted
- **Date:** 2026-07-26
- **Deciders:** bioedca
- **PRD anchor:** §5.4, §7.3, §7.5, §7.10
- **Milestone:** M9

## Context and problem statement

The project API enforces a single-writer sidecar lock, but the GUI previously opened a
project without acquiring that lock. Accept, reject, and background idealization could
therefore write through an unlocked GUI session, and curation could overlap an in-flight
idealization writer. How should the interactive shell own the lock while preserving
read-only access and responsive background work?

## Decision drivers

- Every writable GUI project must participate in the canonical single-writer protocol.
- Curation writes must not overlap an in-flight background idealization writer.
- A foreign or unavailable lock must preserve read-only browsing instead of making the
  project inaccessible.
- A background writer must not outlive the lock that protects it.
- Modal dialogs must own their native keys without application-wide curation side effects.
- Reject reversal must remain a visible, one-click, append-only action.

## Considered options

1. **Assert only at each write.** This detects a foreign lock but still permits an
   unlocked GUI writer and provides no session ownership or serialization.
2. **Acquire and release around every GUI action.** This narrows each critical section,
   but allows another writer to take ownership between related interactive actions and
   complicates long-running background work.
3. **Retain one lock for the writable GUI session and serialize curation against
   background idealization** (chosen). This gives the shell one stable writer
   identity while leaving locked projects browseable.

## Decision outcome

When a project loads, the shell completes its fallible reads and then atomically attempts
`Project.acquire_lock()`. On success, the shell retains that lock across accept, reject,
un-reject, idealization, and other enabled write seams. Curation and project replacement
are unavailable while background idealization is active, so those HDF5 writers cannot
overlap.

Replacing the loaded project releases the prior session lock after the new project has
opened successfully. Closing the shell releases the current lock immediately when idle.
If idealization is still running, the visual result is abandoned but the lock remains
held until the worker future finishes; its completion callback then releases ownership.

If lock acquisition fails because another owner holds it or the sidecar cannot be created,
the shell opens the project read-only. Read seams remain available, all write seams stay
disabled, and the status bar explains why. The Browser dock exposes **Un-reject selected**
as the visible reversal of a sticky reject and records the reversal through the same
append-only labels API.

The application-wide curation event filter passes all key presses through unchanged while
a modal widget is active. This prevents Space, Backspace, or Delete in a file dialog or
message box from curating the trace behind it.

### Consequences

- **Good.** Every writable GUI session has explicit, stable single-writer ownership.
- **Good.** Curation and background idealization cannot write the HDF5 project concurrently.
- **Good.** Contended projects remain browseable with an actionable read-only banner.
- **Good.** Sticky rejection has a discoverable, provenance-preserving reversal path.
- **Trade-off.** A writable project remains exclusively locked for the GUI session, and
  project switching waits for any active idealizer.
- **Follow-up.** GUI regression tests pin lock acquisition/release, read-only fallback,
  writer serialization, modal-key bypass, and un-reject behavior.

## More information

- [ADR-0023](0023-curation-label-codec-and-labels-log.md) defines the append-only label log.
- `src/tether/project/core.py` owns canonical lock and curation writers.
- `src/tether/gui/shell.py` owns the GUI session lifecycle and write serialization.
- `src/tether/gui/curation.py` owns global shortcut routing and modal bypass.
