---
description: "Look at each extracted trace in the Tether application window and mark the molecule accepted or rejected for your analysis."
---

# Curate traces by accepting and rejecting molecules

## Who this page is for

You have a `.tether` project with extracted traces, from
[Extract traces from a dual-view movie](extract-traces.md) or from a
[Deep-LASI import](../io/legacy-import.md), and you want to decide which molecules go into
your analysis. You will look at each trace in the desktop application and mark it accepted or
rejected.

## Before you start

- The project file, here called `movie.tether`.
- The desktop application. After an installer install it starts with `tether-gui`; see
  [Launching Tether after an installer install](../cli.md#launching-tether-after-an-installer-install).
- No other Tether application window, script or batch run has the same project open. Only one writer may
  change a project at a time, and a second application window opens it read-only. See
  [The project is locked by another user or
  machine](../troubleshooting.md#the-project-is-locked-by-another-user-or-machine).
- Each movie file in the project once. A file extracted into the project twice gives both
  copies the same molecule keys, and accepting or rejecting a molecule labels every copy that
  carries its key. A project made by `tether extract` or `tether batch` holds one movie.

## 1. Open the project

```bash
tether-gui movie.tether
```

On Windows and Linux you can also start Tether from the Start menu or applications menu. The
application window then shows the start page. Click **Open project…** there, choose **File → Open
project…**, or drop the `.tether` file on the application window. A dropped file of a different type does
not open, and the status bar says why.

**File → Open recent** lists the last ten projects you opened on this computer. A project
whose file was moved or deleted shows grayed out, with its path.

When the project opens, the status bar at the bottom of the application window names it and its molecule
count:

```text
Opened movie.tether — 412 molecule(s)
```

If that line ends in `(read-only)` after the file name, another writer holds the project and
nothing you mark will be saved. Close the other writer and open the project again.

The **Movie** box above the list names each movie of the project by its file name. The
**Movie** panel on the right shows the chosen movie. A green dot marks each donor spot, and a
red dot marks each acceptor spot. The ring around each dot is the integration aperture, at the
radius the extraction used. To show another movie, choose it in the **Movie** box.

Tether looks for each movie file at the path recorded when the traces were extracted. A
movie whose file was moved or deleted shows grayed out, with `(file missing)`. The status
line then ends with, for example, `1 of 1 movie(s) not shown`.

Tether does not download a movie that is only in the cloud, such as a OneDrive file. Such a
movie shows `(file not on this disk)`. Make the file available on this computer, then open the
project again. A project imported without spot coordinates has no movie panel.

A project you opened before on this computer opens at the molecule you last had selected in it.

## 2. Move from trace to trace

The list on the left holds every molecule. The large panel shows the selected one: donor
(green), acceptor (red) and their total (gray) on top, its apparent FRET efficiency (blue)
underneath.

The bottom panel shows the donor–acceptor cross-correlation of the trace. While the total
intensity stays about constant, a change in FRET raises one channel and lowers the other.
The cross-correlation at lag 0 is then below zero. A change in the total intensity can make
it positive even when FRET changes too.

The lag axis uses the unit of the time axis, seconds or frames. It spans 50 frames either
side of 0. A trace shorter than 51 frames shows a note in that panel instead of a curve.
So does a trace with a missing value or a constant channel.

1. Press **Right arrow** or **Down arrow** for the next molecule.
2. Press **Left arrow** or **Up arrow** for the previous one.
3. Or click a name in the list.

**Curation → Keyboard shortcuts…** (also in the **Help** menu) shows every key at any time.

The curation keys act only in the **Curate and correct** section. In **Prepare** and
**Analyze** they change no molecule. There, **Help → Keyboard shortcuts…** lists no key. To
curate again, choose **View → Section → Curate and correct**.

The menu commands use your operating system's standard keys. Each key includes a modifier
key or is a function key, so no menu command replaces a curation key. On macOS, use Cmd
where this list shows Ctrl:

- **File → Open project…**: Ctrl+O.
- **File → Close project**: Ctrl+W.
- **File → Quit**: Ctrl+Q.
- **Help → Documentation**: F1. It opens the documentation for your version of Tether.

Tether saves each mark when you make it, so **File → Close project** has nothing to save. It
releases the project so that another application window can open it, and it shows the start page again. **File → Quit** releases the project in the
same way before Tether closes.

## 3. Accept or reject each molecule

1. Press **Space** to accept the selected molecule. The status bar reads `Accepted` followed
   by the molecule's ID.
2. Press **Backspace** or **Delete** to reject it. The status bar reads `Rejected` followed by
   the ID.

Each decision is written into the project as you make it; there is no save step.

After an accept or a reject, Tether shows the next molecule in the list. One key decides and
moves on. The status bar then reads `Accepted` or `Rejected` and the ID. After that it names the
new molecule and its place in the list, for example `Molecule mol-4 (5/412)`. Some actions do
not move the selection:

- If the decision was not saved, the selection stays where it is and the status bar gives the
  reason. This happens when the project is read-only or the write fails.
- **Un-reject** and the number keys do not move the selection.
- On the last molecule of the list, the selection stays and the status bar ends with
  `end of the list reached`.
- If some molecules are hidden from the list, Tether shows the next molecule that the list
  shows. Moving on never changes the order of the list.

This behavior is the preference **Advance after accept or reject**, which is on by default.
**Curation → Keyboard shortcuts…** shows whether it is on. There is no settings window for it
yet. A script that opens the window can turn it off with
`TetherShell(advance_after_curation=False)`. Then Space and Backspace store the decision and
keep the same molecule selected.

When a button has the keyboard focus, **Space** and **Enter** push that button. They do not
accept the molecule or jump to it. Click the molecule list to curate with the keys again.

> The number keys, the analysis-window keys and **P** answer with `unavailable` in the status
> bar and change nothing in the project. Categories, analysis-window edits and photobleach detection
> are not saved from the curation view.

## 4. Change your mind

- To accept a molecule you rejected, select it and press **Space**. Tether then shows the
  next molecule, as after any accept.
- To return a rejected molecule to *uncurated*, select it and click **Un-reject selected** in
  the panel on the left, or move the focus to it with **Tab** and press **Space** or **Enter**.
  The status bar reads `Un-rejected` and the ID. On a molecule that is
  not rejected it reads `Un-reject unavailable: selected molecule is not rejected` and changes
  nothing.

Nothing you do here deletes a molecule or a decision. Every accept, reject and un-reject is
added to the project's [curation log](../reference/tether-format.md#labels-the-append-only-curation-log),
so the history survives a reversal.

## What each label does downstream

- **Uncurated** is where every molecule starts. Uncurated molecules are **included** in
  histograms, idealization and the other analyses, exactly like accepted ones.
- **Accept** records your judgment. Accepting an uncurated molecule does not change which
  molecules an analysis uses, because only rejection removes a molecule. Accepting a
  rejected one does: it replaces the rejection and puts the molecule back in.
- **Reject** keeps the molecule out of population analyses and idealization, and out of the
  trace matrix and subset exports. The molecule-table CSV still lists it, with `reject` in
  its `curation_label` column, because that table is a full inventory
  ([Exports: selection](../reference/exports.md#selection)).

Accepts and rejects are also the training labels for the quality ranker that orders molecules
for review ([curation label](../glossary.md#curation-label-category-quality-class)).

## Who made each decision

Every row in the curation log records a `labeler`: the name of the curator who made the
decision. The desktop application fills it in with your login name on this computer, unless
you set a curator name. The status bar shows the name for new decisions, for example
`Curator: ana`.

To use a different name, choose **File → Curator name…** and type it. Do this on a lab
computer that several people share under one login. Decisions you make after that carry the
new name. Decisions already recorded keep their name. Leave the field empty to go back to your
login name. The application keeps the name for your next session on this computer.

From a script, pass the name with
`labeler=`. For example, this script un-rejects every rejected molecule in the project and
credits the change to `your-name`. Close the Tether application window first, then run it with the Python
that came with Tether (`~/tether/envs/tether/bin/python` on Linux,
`~/Tether/envs/tether/bin/python` on macOS, `$env:USERPROFILE\Tether\envs\tether\python.exe`
on Windows, at the default install locations):

```python
from tether.project.core import Project

project = Project.open("movie.tether")
for key in sorted(project.rejected_molecule_keys()):
    project.unreject(key, labeler="your-name")
```

`project.accept(key, labeler=…)` and `project.reject(key, labeler=…)` work the same way.

## What the application remembers on this computer

Between sessions the desktop application keeps these things for your user account on this
computer:

- The last ten projects you opened, for **File → Open recent**.
- For each project, the molecule you had selected when you closed it.
- The size of the window and the positions of its panels.
- The curator name, if you set one.

**View → Reset layout** puts the window and its panels back in their first positions. It also
forgets the saved positions.

The application writes none of this into the `.tether` file, so a project you share carries
none of it. It keeps it in a file named `preferences.json` in your own settings folder:

- Windows: `%LOCALAPPDATA%\MondragonLab\Tether\`
- macOS: `~/Library/Application Support/MondragonLab/Tether/`
- Linux: `~/.config/tether/`, or `$XDG_CONFIG_HOME/tether/` when that variable is set

Delete that file and the application forgets all of it. Nothing else changes. To use another
file, set the environment variable `TETHER_PREFERENCES_FILE` to its full path.

## Next

[Correct the dataset for leakage and gamma](apply-corrections.md), then
[idealize the traces](idealize.md).
