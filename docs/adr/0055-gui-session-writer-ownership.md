<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0055 — GUI session writer ownership

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
- Separate GUI shells in one process must not share writability through the same
  host/user/PID identity.
- The canonical HDF5 project must itself be update-openable before mutations are enabled;
  creating the sidecar alone is insufficient.
- No source-project writer—curation, return-leg import, or condition validation—may
  overlap an in-flight background idealization writer.
- A live GUI session must refresh ownership before the 30-minute stale timeout.
- A foreign or unavailable lock must preserve read-only browsing instead of making the
  project inaccessible.
- A background writer must not outlive the lock that protects it.
- Dialogs and popup menus must own their native keys without application-wide curation
  side effects.
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
un-reject, idealization, and other enabled write seams. Every source-project write entry
point is unavailable while background idealization is active: accept, reject, and
un-reject remain guarded, while the visible un-reject, return-leg import, and condition
validation controls are disabled until the future settles. Project replacement also
waits, so none of those HDF5 writers can overlap. Before enabling those seams, the shell
proves the `.tether` HDF5 can open in `r+` mode. A process-local path registry prevents a
second shell with the same host/user/PID identity from treating the canonical sidecar
refresh as independent ownership. A repeating GUI timer and each curation/idealization
action refresh ownership well before the 30-minute stale boundary. Refresh advances the
held timestamp while preserving and requiring the exact acquisition nonce; it never
recreates a vanished or replaced sidecar. If refresh or release encounters an I/O
failure, write seams fail closed while a separate lifecycle handle retains the acquired
nonce for retry and final teardown.

Because a sidecar fit can run for many minutes, GUI idealization requires the retained
session's exact held nonce before work starts and binds that starting epoch into the
check immediately before opening HDF5 for persistence. Legitimate timer refresh keeps
the same epoch, while a lock stolen and then released during the fit cannot be silently
reacquired and therefore still prevents the store write.

Replacing the loaded project releases the prior session lock after the new project has
opened successfully. Closing the shell releases the current lock immediately when idle;
a transient release failure retains the lifecycle handle and process-local claim while a
timer retries the exact held nonce instead of waiting for staleness.
If idealization is still running, the visual result is abandoned but the lock remains
held until the worker future finishes. Its completion callback attempts release without
dropping lifecycle state on failure, while a timer observes completion and retries
transient release failures without dropping the held nonce or process-local claim.
Both `QApplication.aboutToQuit` and the exposed main window's own Close event route
through the same idempotent teardown, so an embedding host cannot retain an invisible
shell's lock or application-wide event filter.

If lock acquisition fails because another owner holds it or the sidecar cannot be created,
the project HDF5 is not update-openable, or another shell in the process owns the path,
the shell opens the project read-only. Read seams remain available, including outbound
Hand-to-tMAVEN export to a separate SMD; a destination that identifies the loaded
`.tether` source (including a file-system alias) is rejected before any write.
Return-leg import and every source-project write
seam stay disabled. The Browser dock carries a persistent read-only banner so navigation
cannot erase the ownership warning. It also exposes **Un-reject selected** as the visible
reversal of a sticky reject and records the reversal through the same append-only labels
API. A successful curation write queues a short, coalescing refresh of any already-open
population histogram. This keeps the keystroke path independent of the project-wide
apparent-E recomputation while ensuring the visible pool is refreshed after the curator
pauses.

If the refresh timer detects ownership loss while the condition-validation modal is
already open, the shell rejects that dialog before clearing its writable Project seam.
The stale dialog therefore cannot write after the foreign owner subsequently releases
the sidecar. Each materialize or re-key action also requires the retained session's exact
nonce immediately before its Project mutation, so a steal-and-release entirely between
heartbeat ticks is refused too. Standalone dialogs retain the normal unlocked-or-self-owned
Project behavior.

The application-wide curation event filter dispatches only when the event and focus belong
to the registered main curation window. It passes all key presses through unchanged for
modal or modeless dialogs and active popup menus. This prevents Space, Backspace, or Delete
in a file dialog, cheat sheet, or menu from curating the trace behind it.
An explicitly registered shell-owned Browser dock remains in scope when floated, while
unregistered top-level dialogs and popup menus remain outside curation scope.
Auto-repeat remains enabled for navigation, but repeated accept/reject key presses are
consumed without dispatch so one physical key hold appends at most one provenance row.
Default one-click idealization also refuses a currently rejected selection; the curator
must visibly un-reject it before fitting, while the headless idealization API retains an
explicit include-rejected path.

### Consequences

- **Good.** Every writable GUI session has explicit, stable single-writer ownership.
- **Good.** Active sessions stay fresh and long-running fits fail closed if ownership changes.
- **Good.** Curation, return-leg import, condition validation, and background idealization
  cannot write the HDF5 project concurrently.
- **Good.** Contended projects remain browseable with an actionable read-only banner.
- **Good.** Safe outbound export remains available from read-only projects, while return-leg
  import remains disabled.
- **Good.** Open population histograms track successful accept/reject/un-reject writes
  without putting a project-wide recomputation in the synchronous curation path.
- **Good.** Sticky rejection has a discoverable, provenance-preserving reversal path.
- **Trade-off.** A writable project remains exclusively locked for the GUI session, and
  project switching waits for any active idealizer.
- **Follow-up.** GUI regression tests pin lock acquisition/refresh/release and retry,
  same-process shell serialization, direct-window teardown, HDF5 update probing,
  read-only fallback/export/banner state, histogram refresh, floating-dock scope,
  out-of-scope key bypass, un-reject behavior, and all source-write guards during
  background idealization.

## More information

- [ADR-0023](0023-curation-label-codec-and-labels-log.md) defines the append-only label log.
- `src/tether/project/core.py` owns canonical lock and curation writers.
- `src/tether/gui/shell.py` owns the GUI session lifecycle and write serialization.
- `src/tether/gui/curation.py` owns global shortcut routing and modal bypass.
- The installed h5py 3.16.0
  [File-mode contract](https://docs.h5py.org/en/stable/high/file.html#opening-creating-files)
  defines `r+` as read/write access to an existing file, so the probe cannot create or
  truncate a project.
