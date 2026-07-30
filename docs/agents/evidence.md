<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Scientific evidence and provenance

These rules are part of the agent contract. `AGENTS.md` points here and grants no authority to assert
a scientific claim on its own: **an agent that has not read this page may not land a scientific
claim, an algorithm choice, a validation oracle, or a dataset interpretation.** `AGENTS.md`'s
prohibition on sending sensitive or uncommitted material to an external service still applies to
every search described here.

- For scientific claims, algorithms, validation oracles, and dataset interpretation, search
  Consensus and `@Scite` first; use both for load-bearing claims. Then use the most specific
  Life-Science-Research or NGS-Analysis tool. Prefer primary evidence and official records; check
  retractions/corrections and reconcile conflicting evidence.
- Record DOI/accession, source and tool/database version, query/config, retrieval date, license,
  input/output checksums, transformations, parameters, and random seeds. Keep citations with claims.

`AGENTS.md` sets the two rules this page serves and neither is repeated here as advice: passing tests
never by themselves validate scientific truth, and a frozen oracle or tolerance is never weakened to
fit an implementation nor a reference value fabricated to pass.

See also [Library and tool routing](tools.md) for the *how does this API behave* half of the same
section — the two never overlap, and a change whose correctness depends on both must satisfy both.
