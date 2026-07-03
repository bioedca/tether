<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0031 — Batch sidecar supervision: liveness-deferred startup + transient auto-restart

- **Status:** accepted
- **Date:** 2026-07-03
- **Deciders:** bioedca (maintainer)
- **PRD anchor:** §7.11 (FR-BATCH), §11.2 ("Batch sidecar supervision"), §4.3 (sidecar), §9 M3
- **Milestone:** M3

## Context and problem statement

FR-BATCH (§7.11) requires the headless batch runner to **supervise** the tMAVEN
idealization sidecar: a per-IPC-call timeout, a liveness check, auto-restart up to
N = 3, failing only that movie's idealization on give-up, and an
**idealization-deferred startup mode** when the sidecar is absent/corrupt. ADR-0030 built
PR7-A (queue, per-movie isolation, per-stage checkpoint, log/summary, over-gate policy,
provenance) and deferred this supervision paragraph to **PR7-B** (this ADR), layering it
over PR7-A's single, error-isolated idealize call.

The design question is what "supervision" *means* for Tether's sidecar architecture. The
sidecar is **not** a long-lived daemon: `run_vbfret` (PRD §4.3, ADR-0004/0006) launches
the isolated interpreter as a **fresh short-lived subprocess per idealization**,
communicates over the filesystem (SMD in, model out) plus a one-line JSON status, waits
with a `timeout`, and raises `SidecarError` on crash/timeout/reported-error. tMAVEN pins
`numpy<2` + PyQt5 and cannot share the base process, so there is no in-process handle to
"ping" or a persistent process to keep alive between movies.

## Decision — "liveness" is a one-shot startup probe; "restart" is a re-launch

Because each idealization is already its own process, supervision maps cleanly onto the
per-movie subprocess model:

- **Liveness = a startup probe.** `_sidecar_runner.py` gains a `--probe` fast-path that
  imports and instantiates `tmaven.maven.maven_class` (plain objects, no Qt — the M0.5 S1
  recon) and exits. `tether.idealize.supervisor.probe_sidecar` runs it once, with its own
  short timeout, and **never raises** — a missing interpreter, a launch error, a timeout,
  or a non-`ok` status all return `ProbeResult(available=False, detail=…)`. This catches a
  sidecar env that is *present but broken* (`tmaven` unimportable), not just absent.
- **Auto-restart = re-launch on a transient failure.** `supervise_idealize` wraps the
  idealize runner and re-runs it up to `max_restarts` times (default **N = 3**, §11.2). A
  fresh subprocess is exactly what recovers a crash or a hang, so a re-launch is the whole
  mechanism — there is no worker to reset.
- **Per-call timeout** already exists on `run_vbfret`; supervision *owns* it (and the
  sidecar interpreter) so the probe and every attempt target the same env/timeout.

## Decision — restart only *transient* failures; a reported fit error is terminal

`SidecarError` gains a `transient` flag. A **timeout** or a **process crash** (non-zero
exit with no clean status) is `transient=True` — a liveness failure a fresh process may
recover. A status the sidecar *itself* emitted with `ok=False` (it loaded the data, ran,
and reported that these traces cannot be fit) is `transient=False`: re-running the same
input only repeats it, so `supervise_idealize` re-raises it immediately instead of
burning the restart budget (up to N × the 1800 s timeout) on a certain failure. When the
budget *is* spent on transient failures, the last error is re-raised as
`RestartsExhausted` (a `SidecarError` subclass), which PR7-A's existing `except Exception`
records as this movie's failed idealize stage — extract + correct stay checkpointed, and
the queue continues (fail-only-that-movie, unchanged).

Two boundary cases round out the classification (each with `transient=False` semantics):
a **configuration error** from `resolve_sidecar_python` (no interpreter set / a path that
does not exist) is terminal — no restart can conjure an interpreter mid-run, so under
`--no-defer` it fails fast rather than burning the budget; and a sidecar that reported
`ok=True` (the model file is fully written and closed before the status is flushed) but
then exits non-zero on a **teardown-phase crash** of the native stack is **not** a failure
at all — `run_vbfret` salvages the completed on-disk model instead of discarding a finished
fit to a restart, treating it as transient only if that model is unreadable.

## Decision — idealization-deferred startup mode is a new `deferred` stage status

When the startup probe reports unavailable and `defer_if_unavailable` is set (default),
the run enters **deferred mode**: every movie still extracts + corrects (and
checkpoints), and the idealize stage records the new `STATUS_DEFERRED` — distinct from
`failed` (a real error), `blocked` (an upstream stage did not complete), and
`not-requested` (`idealize=False`). Deferred is **not** a movie failure (`MovieResult.ok`
stays true; the run's exit code is unaffected), and because ADR-0030's checkpoint is
provenance-presence, a later run with a working sidecar resumes **only** the deferred
idealize stage. This is strictly additive to the status vocabulary — no `.tether` schema
change, `schema-guard` stays green.

`SidecarSupervision` is **opt-in**: `run_batch(supervision=None)` (the library default)
keeps PR7-A's exact single-attempt behavior, so every existing test and caller is
unchanged. The `tether batch` CLI, being the overnight tool FR-BATCH targets, constructs
a default `SidecarSupervision` (N = 3, defer-on) so batch runs are supervised by default,
with `--max-restarts` / `--sidecar-timeout` / `--sidecar-python` / `--no-defer` overrides.

## Considered options

- **A — Persistent sidecar daemon** with a heartbeat + restart controller. Rejected:
  contradicts the per-idealization-subprocess design (§4.3); a resident `numpy<2`/PyQt5
  process buys nothing when each fit already forks its own, and adds IPC-liveness
  machinery with no consumer.
- **B — Restart on *every* `SidecarError`.** Rejected: retrying a cleanly-reported fit
  error wastes up to N × timeout on a deterministic failure. The transient/terminal split
  restarts only what a restart can fix.
- **C — Fail (not defer) when the sidecar is absent at startup** (PR7-A behavior).
  Rejected as the default: it fails every movie's idealize stage for a *recoverable
  environment* condition; deferring lets the expensive extract + correct work land and
  checkpoint, and the operator re-runs idealization later — the payoff of the per-stage
  checkpoint. Kept as `--no-defer` for callers who want the old behavior.
- **D (chosen) — startup liveness probe → deferred mode; transient-only auto-restart up
  to N; opt-in `SidecarSupervision` owning the interpreter + timeout.**

## Consequences

- **Positive:** a transient sidecar crash/timeout is transparently retried; a broken/absent
  sidecar env defers idealization instead of failing it (extract + correct still land and
  checkpoint; resume re-runs only the deferred stage); a deterministic fit error fails fast
  without wasting the restart budget; all supervision is dependency-injected
  (`_probe`, the runner, the probe's `_run`), so it is fully tested headlessly without a
  real sidecar env — CI has none. Additive only: no schema change (`schema-guard` green),
  no `conda-lock` change; the one new §11.2 tunable (auto-restart N = 3) is registered.
- **Negative / follow-up:** the `tether batch` CLI now **defers** (exit 0, idealization
  queued) rather than **fails** (exit 1) when the sidecar is unavailable — a deliberate,
  strictly-better default, reversible with `--no-defer`. The startup probe pays one cold
  `tmaven` import per run. A GUI "run batch" surface over this headless core is a later
  thin wrapper. Per-condition α/γ aggregation remains an M4/M6 concern (ADR-0030).
- **Neutral:** with `supervision=None` the idealize stage is byte-for-byte PR7-A behavior;
  supervision is a superset entered only when a `SidecarSupervision` is passed.
