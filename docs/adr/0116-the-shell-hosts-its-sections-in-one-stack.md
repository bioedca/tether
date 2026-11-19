<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0116 — The shell hosts its sections in one stack, and each dock belongs to one section

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §4.2 (`tether.gui`), §7.3 (the curation shell), §7.10 FR-CONCURRENCY, FR-WORKFLOW
- **Milestone:** M14
- **Issue:** [#341](https://github.com/bioedca/tether/issues/341)

## Context and problem statement

The design of the unified shell (M14, the workflow plan #890) has sections: Prepare, Curate and
correct, Analyze and Deposit (#897). The Qt-free section registry of #340
(`tether.gui.sections`) describes each section as data. But the shell had no place for a section:
its central widget held only the start page and the trace dock, and the Browser, Overlap and
Population histogram docks were attached to the window with no owner. The same class,
`TetherShell`, owns the GUI session lifecycle of
[ADR-0055](0055-gui-session-writer-ownership.md) — the retained lock, the process-local owner
registry, the write freeze while idealization runs, and two teardown routes. How does the shell
host the sections without a second window and without a change to that lifecycle?

## Decision drivers

- One `QMainWindow`, so one session, one lock and one owner-registry entry per open project.
- The registry of #340 is the one description of the sections; the shell must build from it, not
  from a second list.
- A section switch must not be a lifecycle event: it must not acquire, refresh or release the lock,
  and it must not enable a write control that ADR-0055 disables.
- The entry point (`tether-gui = "tether.gui.app:main"`, [ADR-0051](0051-installed-app-launch-surface.md))
  must still open a usable empty shell.
- The precedent in the repository: `deeplasi_wizard_ui.py` already moves through its pages with a
  `QStackedWidget`.

## Considered options

1. **One window per section.** Each section is a top-level window. This gives each section a
   window to own, but a second window is a second owner of the open project: the owner registry of
   ADR-0055 would refuse it a writable session, or two windows would have to share one session.
2. **A `QTabWidget` as the central widget.** The tab bar is the navigation. But the guided mode and
   the direct navigation are #897's decision, and the tab bar would decide them now; a tab bar also
   cannot keep a section hidden (Deposit, until #956) without code of its own.
3. **A `QStackedWidget` as the central widget, one page per registered section, and each dock
   owned by one section** (chosen). The stack shows one page and has no navigation of its own, so
   #897 adds the navigation it decides.

## Decision outcome

Chosen option: **3**, because it keeps one window and one session and leaves the navigation to
\#897.

- **The stack.** `TetherShell.window` is still one `QMainWindow`. Its central widget is a
  `QStackedWidget` (`TetherShell.section_stack`) with one page per section of the registry
  (`TetherShell.sections`), in the registry's order. Each page is what the section's `central`
  factory returns. The page of Curate and correct is the former central widget: the start page
  while nothing is loaded, else the trace dock.
- **The sections today.** The shell registers three: Prepare (order 1), Curate and correct
  (order 2) and Analyze (order 3). The issue's text said "extraction, curation and analysis"; the
  plan change of 2026-11-14 made it the sections of the registry, with Prepare in place of
  extraction. Deposit (order 4) is not registered here: #897 adds it with the one registry flag that
  keeps it hidden until #956 lands. Prepare and Analyze show a plain guide page until their own
  content lands; it names the menu item that does the work today and offers no action the shell
  cannot do.
- **`section` and `set_section`.** `TetherShell.section` is the id of the section that shows.
  `set_section(id)` shows that section's page and docks and hides every other section's docks; an
  unknown id raises `KeyError` and changes nothing. **View → Section** has one checkable item per
  section, so the shell is usable from the mouse and the keyboard until #897 replaces the list with
  its guided mode and direct navigation. The shell opens on Curate and correct, which holds the
  start page, and *File → Close project* returns to it.
- **Section-scoped docks.** Each dock belongs to one section and shows only while that section is
  current. The Browser is a `DockSpec` of Curate and correct. The Overlap dock (Curate and correct)
  and the Population histogram dock (Analyze) are still built lazily, on the first selection and on
  the first *Analysis → Population histogram…*, and attach to their section through the same
  method. Every dock area is a `DockArea` converted through `DockArea.qt_name` in one place, so no
  area has its own code. A dock the user closed stays closed when its section shows again. Showing
  the histogram shows the Analyze section, because the histogram is that section's dock; the
  debounced refresh after a curation write (ADR-0055) still runs while the dock is hidden.

### What of ADR-0055 is preserved verbatim, and what is restated

Preserved verbatim, with no change to the code that implements it: the retained lock and its
refresh timer and nonce; the process-local owner registry (`_GUI_PROJECT_OWNERS`, keyed by
`_project_owner_key`); the `r+` probe; the write freeze (`_sync_project_write_controls`,
`_idealization_blocks_write`); `_release_session_once` and `_release_rollback_session_once`; and
both teardown routes, the main window's `Close` event and `QApplication.aboutToQuit`.

Restated for sections: a section switch is **not** a lifecycle event. It does not acquire,
refresh or release the lock, it does not claim or release the owner registry, and it does not
enable or disable a write control. The window that the Close-event filter watches is the same one
window, whatever section shows, so both teardown routes release the lock from any section.

### Extraction appends into the open project

When the Prepare section gains extraction (#352), the extraction **appends into the open project**
under the destination lock of [ADR-0112](0112-every-publishing-writer-holds-its-destination-lock.md);
it does not open a second window or a second session. This is why Prepare declares
`writes_project=True` and why option 1 was refused. Curate and correct and Analyze declare
`writes_project=True` too (curation writes the labels; analysis results are stamped into the
project); the publish path of #352 reads the flag from `SectionRegistry.may_write` only.

### The Qt import convention

The new code follows the one convention of #339 (`tether.gui` docstring): it imports Qt through
`pyqtgraph.Qt`, lazily inside the functions that build widgets, and `tether.gui.sections` stays
Qt-free. `tests/test_gui_qt_imports.py` enforces it unchanged.

### Consequences

- **Good.** One window and one session host every section, so ADR-0055 holds unchanged.
- **Good.** The registry of #340 is the one list of sections; the stack, the docks and
  **View → Section** are built from it.
- **Good.** A section's docks do not crowd another section's page.
- **Trade-off.** The menus and the curation keymap are still global: a curation key pressed while
  Analyze shows still curates the selected molecule. #342 builds the menus from the registry and
  scopes the keymap to the section.
- **Trade-off.** *Analysis → Population histogram…* now moves the window to Analyze; the curator
  returns with **View → Section → Curate and correct**.
- **Follow-up.** `tests/test_shell_sections.py` pins the stack, `set_section`, the dock ownership,
  and the lifecycle across a switch: no acquire, refresh or release on a switch; a second shell
  still refused; every write control disabled while idealizing in each section; and both teardown
  routes after a switch. `tests/test_mvp_northstar.py` passes unchanged.

## More information

- `src/tether/gui/shell.py` — `TetherShell._build_sections`, `set_section`, `_show_section`,
  `_attach_section_dock`.
- `src/tether/gui/sections.py` — the registry of #340.
- `src/tether/gui/deeplasi_wizard_ui.py` — the `QStackedWidget` precedent.
- [#897](https://github.com/bioedca/tether/issues/897) (four sections, guided mode, navigation),
  [#342](https://github.com/bioedca/tether/issues/342) (menus and keymap per section),
  [#352](https://github.com/bioedca/tether/issues/352) (extraction into the open project),
  [#956](https://github.com/bioedca/tether/issues/956) (the Deposit section).
