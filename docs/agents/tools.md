<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Library and tool routing

These rules are part of the agent contract. `AGENTS.md` points here and grants no authority to write
against an external interface on its own: **an agent that has not read this page may not write or
modify code calling a third-party library, API, CLI, or file format.** `AGENTS.md`'s prohibition on
sending sensitive or uncommitted material to an external service still applies to every query
described here.

- For external library, API, CLI, file-format, or workflow behavior, query Context7 first using the
  locked/installed version. Use `@Browser` when Context7 is insufficient or live/visual UI state is
  material. Record version and authoritative finding; do not rely on memory for unstable behavior.

"The locked/installed version" is not one version. Tether keeps three isolated dependency stacks and
a query answered against the wrong one is worse than no query, because it reads as authoritative:

| stack | what it holds |
|---|---|
| base `conda-lock` | the GUI and compute stack — PySide6, napari, pyqtgraph, NumPy, Numba |
| `sidecar/conda-lock.yml` | PyQt5, `numpy<2`, bounded numba, the trimmed tMAVEN deps |
| `deep/conda-lock.yml` | the optional torch stack |

See also [Scientific evidence and provenance](evidence.md) for the *is this true* half of the same
section. They never overlap: this page governs how an interface behaves, that one governs what is
empirically or methodologically the case. A change whose correctness depends on both — a statistical
routine whose validity turns on the test being the right test — must satisfy both.
