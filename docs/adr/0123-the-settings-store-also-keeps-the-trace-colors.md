<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0123 — The settings store also keeps the trace colors

- **Status:** accepted; amends [ADR-0095](0095-application-settings-live-outside-the-install-folder.md) on what the settings file stores
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §7.3 (familiar conventions); §4.1 (application shell)
- **Milestone:** M16 - Trace curation section
- **Issue:** [#636](https://github.com/bioedca/tether/issues/636)

## Context and problem statement

[#636](https://github.com/bioedca/tether/issues/636) makes the color-blind-safe trace palette the
default and keeps the classic green and red set as a preference, **View → Trace colors**, whose
choice must survive a restart. The issue names the application settings store of
[#330](https://github.com/bioedca/tether/issues/330) as its home. That store,
[ADR-0095](0095-application-settings-live-outside-the-install-folder.md), holds *exactly*
`{"schema": 1, "update_check": …}`, names `record_update_consent` as its only writer, and says
it is the store for that one answer "and deliberately nothing more". Keeping the preference there
needs that clause changed; keeping it anywhere else departs from the issue. The maintainer chose
the settings file on 2026-11-16, in the work thread of #636.

## Decision drivers

- The choice must survive an update, which ADR-0095 measured this file to do on every
  upgrade path the installers offer.
- A trace-color write must never write, change or fabricate an update-check consent answer,
  and a bad trace-color value must not make a good consent answer unreadable (ADR-0095: every
  failure is off).
- The store must still not become a preferences framework, a home for analysis parameters,
  or something the batch runner reads.

## Considered options

- **A. A second key in `settings.json`.** The file of ADR-0095, one more validated answer.
- **B. Qt `QSettings`, as the keymap uses.** Read after the application names are set, so the
  timing objection of ADR-0095 does not apply; but it is a registry or property-list entry on
  two of three systems, which the upgrade smoke does not measure, and it departs from the issue.

## Decision outcome

Chosen option: **A**.

**What the file stores** (replacing ADR-0095's *What it stores*): `schema` (the integer 1) and
at most two answers, `update_check` (`"allowed"` or `"declined"`) and `trace_colors` (`"safe"` or
`"classic"`). Still no time, identifier, user name, path or project data.

**Each answer is read on its own.** A file of another `schema`, or one that is not a file this
version wrote, reads as unset for both. Within a valid file, an unknown `trace_colors` value
reads as unset (the default palette) and leaves `update_check` readable, and the reverse.

**Two writers, each keeping the other's answer.** `record_update_consent` and
`record_trace_colors` both write through one atomic helper (a temporary file in the same folder,
`fsync`, `os.replace`, as ADR-0095 specified). Each sets its own key and carries over the other
key only when that key holds a valid answer; anything else in the file is dropped. So a
trace-color write on a file with no consent answer leaves the update check *pending*, and the
consent write keeps a stored palette.

**Who reads it.** `create_shell` reads the stored palette before it builds the shell and
applies it; unset is the default, `safe`. Nothing outside `tether.gui` imports the module, as
before, and the read still imports no Qt, NumPy or HDF5.

**The scope bound stays.** ADR-0095's "not a preferences framework" stands for anything else:
a further key needs its own record. The window theme (**View → Theme**) is not added here.

### Consequences

- Good: the preference survives updates with no new file, folder or per-OS behavior to measure.
- Good: an older Tether reading a newer file still reads its consent answer, because the reader
  of ADR-0095 ignores keys it does not know. An older Tether that writes its consent answer drops
  the palette, which then reads as the default; that is the safe direction.
- Bad / trade-off: the file is no longer single-purpose, so a reader of ADR-0095 alone has a
  stale picture of its contents; ADR-0095's index row and `docs/install.md` point here.

## More information

- Implementation: `src/tether/gui/app_settings.py` (`stored_trace_colors`, `record_trace_colors`),
  `src/tether/gui/theme.py` (`set_trace_palette`), `src/tether/gui/shell.py` (**View → Trace
  colors**), `src/tether/gui/app.py` (`create_shell`).
- Tests: `tests/test_gui_trace_colors.py`, `tests/test_app_settings.py`.
- User pages: `docs/reference/gui-theme.md` (*Select the trace colors*), `docs/install.md`
  (*Settings Tether keeps outside the install folder*).
