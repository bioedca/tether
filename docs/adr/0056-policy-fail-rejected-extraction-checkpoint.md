<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0056 - Preserve policy-fail rejection across batch resume

- **Status:** accepted
- **Date:** 2026-07-26
- **Deciders:** bioedca
- **PRD anchor:** §6, §7.11 (FR-BATCH), §11.2 (over-gate batch policy)
- **Milestone:** M9

## Context and problem statement

ADR-0030 made provenance-group presence the batch checkpoint: a completed extraction
was skipped on resume. Extraction writes its project before the registration gate is
applied, however, so a complete but over-gate project failed only on its first
`policy=fail` run. A second run accepted the presence-only checkpoint and allowed
correction and idealization to proceed.

How should a completed extraction checkpoint retain its inspectability and remain the
single source of truth without losing the fail-policy verdict on resume?

## Decision drivers

- `policy=fail` must fail the same saved over-gate extraction on every resume.
- The completed project must remain available for inspection.
- An explicit recovery path must re-extract and apply the gate to the new result.
- Accepted completed checkpoints must keep the normal skip behavior.
- No second checkpoint file or HDF5 schema addition may be introduced.

## Considered options

1. **Keep accepting every completed extraction checkpoint.** This preserves ADR-0030
   literally but makes `policy=fail` a first-run-only gate.
2. **Delete or invalidate the project after an over-gate extraction.** This preserves
   failure on resume but discards the inspectable result and weakens atomic extraction.
3. **Persist a second batch verdict.** This duplicates state already present in the
   extraction profile and can drift from it.
4. **Re-evaluate the saved extraction profile when fail policy is active** (chosen).

## Decision outcome

Chosen option: **"Re-evaluate the saved extraction profile when fail policy is
active."**

`/settings/extraction` still proves that extraction completed. When `policy=fail`, the
batch runner also reads `registration_rms_px` and `rms_gate` from that group's saved
`profile_json`. A finite residual greater than a finite positive saved gate marks the
completed checkpoint as policy-rejected:

- without `overwrite`, extraction is recorded `failed` and downstream stages remain
  blocked;
- with `overwrite`, extraction runs again and the fail policy is applied to the new
  summary;
- under `policy=warn`, or when the saved result is not over its gate, the completed
  checkpoint remains skipped even if `overwrite` is set.

Missing or malformed legacy profile values do not invent a rejection; those completed
checkpoints retain ADR-0030's presence-only skip behavior. The store remains the only
checkpoint and the HDF5 schema is unchanged.

This decision supersedes only ADR-0030's unconditional acceptance of a completed
**extraction** checkpoint. Its per-movie isolation, correction and idealization
presence checks, sequencing, and sidecar boundary remain accepted.

### Consequences

- **Good.** A saved over-gate extraction cannot silently pass on the second run.
- **Good.** The rejected project remains inspectable and `--overwrite --policy fail`
  provides a bounded recovery path.
- **Good.** Accepted completed checkpoints still resume without redundant work.
- **Trade-off.** Fail-policy resume depends on the saved extraction profile. Legacy or
  malformed profiles keep the safe compatibility behavior of skipping rather than
  guessing a rejection.
- **Enforcement.** Batch unit tests cover fail/warn resume, overwrite re-gating, and
  accepted checkpoint skipping; a CLI end-to-end regression exercises two fail runs
  over the same on-disk project.

## More information

- [ADR-0030](0030-headless-batch-runner-isolation-and-checkpoint.md) defines the
  original provenance-derived checkpoint architecture.
- [ADR-0014](0014-registration-map-rms-gate-and-over-gate.md) defines the registration
  RMS gate and warn/fail policy.
- Issue #211 captures the accepted resume behavior and recovery criteria.
