<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Tutorial: follow one experiment through Tether

This page is the canonical route through the current task-oriented documentation. It links
each stage instead of copying instructions, so fixes remain in the page that owns the
behavior.

1. **Check the input contract.** Start with
   [Does Tether fit my data?](compatibility.md) to confirm the acquisition geometry,
   supported files, scale, and current non-goals.
2. **Install the application.** Use the platform and signing guidance under
   [Packaging & installers](packaging.md), then verify the installed command with the
   [CLI reference](cli.md).
3. **Bring in an experiment.** For an existing Deep-LASI analysis, follow the
   [legacy import guide](io/legacy-import.md). The
   [`.tether` reference](reference/tether-format.md) explains the resulting
   provenance-preserving project store.
4. **Idealize without merging environments.** Follow the
   [standalone tMAVEN hand-off](idealize/standalone-tmaven-handoff.md) when the isolated
   sidecar or a manual round trip is needed.
5. **Interpret and export results.** Use the
   [validation evidence](validation.md), [analysis parameters](reference/parameters.md),
   and [export reference](reference/exports.md) together; each page states what has and
   has not been validated.

For a problem at any stage, use [Troubleshooting](troubleshooting.md). Architecture and
scientific decisions are indexed under [Architecture decisions](adr/README.md).
