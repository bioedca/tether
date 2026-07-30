<!--
SPDX-FileCopyrightText: 2026 The Tether Authors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Local gates before review

These rules are part of the agent contract. `AGENTS.md` points here and does not itself say which
commands to run: **an agent that has not read this page has not run the gates, and a diff whose
local gates have not been run is not final and may not be declared so.**

- Run the narrowest relevant tests first, then the required local gates before review:
  - `pre-commit run --all-files`
  - PowerShell: `$env:QT_QPA_PLATFORM='offscreen'; pytest -m "not large and not sidecar and not deep"`
  - Docs changes: `mkdocs build --strict`
  - Schema changes: `python scripts/dump_schema.py --check`

  A bare `pytest` includes optional large, sidecar, and deep tiers; invoke those only when relevant.

These are the *local* gates. They do not replace the required CI contexts, which run on three
operating systems and in the isolated sidecar and deep environments — a gate that passes here and
fails there is a real failure, and `main` staying green is what the pre-merge check protects.
