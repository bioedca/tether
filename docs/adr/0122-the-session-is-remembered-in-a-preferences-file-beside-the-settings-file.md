<!--
SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# 0122 — The session is remembered in a preferences file beside the settings file

- **Status:** accepted
- **Date:** 2026-11-16
- **Deciders:** bioedca
- **PRD anchor:** §7.3 (the curation shell); §5.1 `/labels` (the `labeler`); goal G6
- **Milestone:** M14
- **Issue:** [#628](https://github.com/bioedca/tether/issues/628)

## Context and problem statement

The desktop application remembered nothing between launches. Each sitting started with
**File → Open project…**, a file dialog and a scroll back to the molecule where the curator
stopped, and a curation pass over one condition takes many sittings. The curator name in every
label came from the operating-system user name with no way to change it, so a shared lab
computer logged every curator under one name.

\#628 asks the application to keep four things: the recent projects, the molecule selected when a
project was closed, the window layout, and a curator name. None of it may go into the project
file. It asks to build on the application settings store of #330
([ADR-0095](0095-application-settings-live-outside-the-install-folder.md)). That store, though,
was decided as a file for the update-check answers alone, with *no user name and no path* in it,
and explicitly not a preferences framework. Where do the four things live, in what form, and how
is a project recognised again?

## Decision drivers

- Nothing of this in the `.tether`: it describes this computer and this user, not the data, and a
  shared project must not carry it ([#620](https://github.com/bioedca/tether/issues/620) removes
  the curator's user name from a shared copy for the same reason).
- ADR-0095's consent file keeps its guarantees: no path, no user name, a format small enough that
  every damaged form reads as *off*.
- It must survive an update. ADR-0095 measured that only a per-user folder outside the install
  prefix does, on every platform.
- A damaged or foreign file must never stop the application, and one bad key must not lose the
  others.
- Tests must never read or write the real per-user folder.

## Considered options

- **A. More keys in `settings.json`.** One file; but it breaks ADR-0095's *no path, no user name*
  rule, which is part of what makes that file safe to read before any project is open, and its
  every-failure-is-off reading would then also throw away the session on any damage.
- **B. `QSettings`.** Qt's usual home for window state. ADR-0095 refused it for the consent answer:
  it is the registry on Windows and a `cfprefsd` property list on macOS, and its folder depends on
  when in startup it is asked. The same holds here, and the recent-project list and the curator
  name are not Qt data.
- **C. A second JSON file, `preferences.json`, in the same per-user folder as `settings.json`.**

## Decision outcome

Chosen option: **C**, because it keeps ADR-0095's file as it was decided and inherits its measured
survival of every upgrade path.

**Where.** `tether.gui.preferences.preferences_path()` is `settings_path()` with the file name
`preferences.json`: `%LOCALAPPDATA%\MondragonLab\Tether\` on Windows,
`~/Library/Application Support/MondragonLab/Tether/` on macOS, `$XDG_CONFIG_HOME/tether/` on Linux.
The environment variable `TETHER_PREFERENCES_FILE`, when it holds an absolute path, names another
file; the test suite sets it for every test (`tests/conftest.py`), and a lab running Tether from
one shared account can give each person their own file with it.

**What.** One JSON object, `schema` 1:

| Key | Holds | Limit |
|---|---|---|
| `recent_projects` | absolute paths, newest first | 10 |
| `resume` | project identity → `molecule_key` selected at close | 50 projects, least recently closed dropped |
| `layout` | base64 `saveGeometry` and `saveState`, and `section_hidden` | one |
| `curator_name` | the name new labels carry; absent means the OS user name | 100 characters, no control characters |

The molecule is stored by its `molecule_key`, not by its row, so a project whose molecule order
changes still resumes on the same molecule; a key no longer in the project opens at the first
molecule.

**Reading.** Each key is checked on its own: a missing or malformed key reads as its default and
the other keys still read. A file that is not a JSON object with `schema` 1 (empty, torn, oversized,
not UTF-8) reads as empty. Reading never raises and never creates a file or folder.

**Writing.** One writer reads the file, changes one key, and replaces the file atomically (a
temporary file in the same folder, `fsync`, `os.replace`), as ADR-0095's writer does. Keys this
version does not know are kept. A JSON object with another `schema`, or a file too large to read,
is never replaced: the write refuses and leaves it as it was, so a newer version's file survives a
session of this one. The read and the replace hold an operating-system lock on an empty file
beside it, `preferences.json.lock` (`fcntl.flock` on macOS and Linux, `msvcrt.locking` of one
byte on Windows), so two windows that write at the same moment take turns and neither update is
lost ([#1171](https://github.com/bioedca/tether/issues/1171)). The lock belongs to the open
handle, so it is released when an instance exits or crashes; the lock file is never deleted, so
every instance locks the same file, and a stale one blocks nothing. A write that cannot take the
lock within five seconds fails like any other failed write. Reading takes no lock. In the shell
every write goes through one guard that turns a failure (a full
disk, a read-only home folder, another version's file) into a status message, so remembering can
never stop an open, a close or a curation decision.

**A project's identity** is its resolved absolute path, case-folded on Windows. The `.tether`
format has no identifier of its own, adding one is a §5 schema change for a convenience, and the
inode changes whenever a project is rewritten. A moved or renamed project therefore opens at its
first molecule; that is the cost, and it fails safe.

**The layout and the sections.** The shell hides every dock of a section that is not showing
([ADR-0116](0116-the-shell-hosts-its-sections-in-one-stack.md)), and Qt's saved state cannot tell
such a dock from one the user closed. `section_hidden` names the docks hidden only by their
section, so on restore they count as shown; the section rule is then applied on top of the restored
state. A lazily built dock (Overlap, Population histogram) takes its stored place when it is first
built. **View → Reset layout** restores the default size and dock positions captured when the
window was built, shows every dock, and forgets the stored layout. The shell has no splitter today,
so there is no splitter position to keep; a future splitter belongs in the same `layout` object.

**The curator name** is a preference (**File → Curator name…**) and shows permanently in the status
bar as *Curator: name*. The shell passes it as `labeler` to accept, reject and un-reject, and to the
condition re-key audit entry. Labels already written keep their name: the label log is append-only.

**Who reads it.** Only `tether-gui` (`tether.gui.app.create_shell`). A `TetherShell` built without a
preferences file remembers nothing and writes nothing, which is what every existing GUI test and
the demo `launch` get. The batch runner and the command line never read it.

**Out of this record.** Reopening the last project in the section that the readiness function of
[#896](https://github.com/bioedca/tether/issues/896) names as the next step was split from #628 into
[#1159](https://github.com/bioedca/tether/issues/1159) by the maintainer on 2026-11-16; its on/off
preference will be one more key in this file.

### Consequences

- **Good.** A curator returns to the project and the molecule where they stopped, in the window
  they arranged, and a shared computer shows whose labels are being written.
- **Good.** `settings.json` stays exactly as ADR-0095 decided it.
- **Good.** Nothing in a `.tether` changes, so the schema is untouched and a shared project carries
  none of it.
- **Trade-off.** The file holds project paths and a name, which is personal data on the user's own
  computer; `docs/privacy.md` says what is stored and where.
- **Trade-off.** Two per-user files where there was one.
- **Trade-off.** A third file, `preferences.json.lock`, stays beside the other two. It is empty
  and is what lets two windows write without losing an update (#1171).
- **Trade-off.** Two windows still each write their own recent-project list in turn: the lock
  keeps both updates, it does not merge what each window shows.
- **Follow-up.** `tests/test_gui_preferences.py` pins the file (location, per-key reading, the
  writer, the limits, the import cost); `tests/test_shell_session_memory.py` pins each acceptance
  criterion of #628 in the shell, headless.

## More information

- `src/tether/gui/preferences.py`; `src/tether/gui/shell.py` (`_remember`, `_sync_recent_menu`,
  `_remember_position`, `_resume_position`, `_save_layout`, `_restore_layout`, `reset_layout`,
  `set_curator_name`); `src/tether/gui/app.py` (`create_shell`).
- [ADR-0095](0095-application-settings-live-outside-the-install-folder.md) (the folder and its
  measured upgrade behaviour), [ADR-0116](0116-the-shell-hosts-its-sections-in-one-stack.md) (the
  sections).
