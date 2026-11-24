---
description: "Look at each extracted trace in the Tether application window and mark the molecule accepted or rejected for your analysis."
---

# Curate traces by accepting and rejecting molecules

## Who this page is for

You have a `.tether` project with extracted traces. They come from
[Extract traces from a dual-view movie](extract-traces.md) or from a
[Deep-LASI import](../io/legacy-import.md). You want to decide which molecules go into your
analysis. You will look at each trace in the desktop application and mark it accepted or
rejected.

## Before you start

- The project file, here called `movie.tether`.
- The desktop application. After an installer install, it starts with `tether-gui`. See
  [Launching Tether after an installer install](../cli.md#launching-tether-after-an-installer-install).
- No other Tether application window, script or batch run has the same project open. Only one writer may
  change a project at a time, and a second application window opens it read-only. See
  [The project is locked by another user or
  machine](../troubleshooting.md#the-project-is-locked-by-another-user-or-machine).

## 1. Open the project

```bash
tether-gui movie.tether
```

On Windows and Linux, you can also start Tether from the Start menu or applications menu. The
application window then shows the start page. To open the project, do one of these:

- Click **Open project…** on the start page.
- Choose **File → Open project…**.
- Drop the `.tether` file on the application window.

A dropped file of a different type does not open, and the status bar says why.

**File → Open recent** lists the last ten projects you opened on this computer. A project
whose file was moved or deleted shows grayed out, with its path.

When the project opens, the status bar at the bottom of the application window names it and its molecule
count:

```text
Opened movie.tether — 412 molecule(s)
```

If that line ends in `(read-only)` after the file name, another writer holds the project.
Then nothing you mark will be saved. Close the other writer and open the project again.

The **Movie** box above the list names each movie of the project by its file name. The
**Movie** panel on the right shows the chosen movie. A green dot marks each donor spot, and a
red dot marks each acceptor spot. The ring around each dot is the integration aperture, at the
radius the extraction used. To show another movie, choose it in the **Movie** box.

Tether looks for each movie file at the path recorded when the traces were extracted. A
relative path is looked up in the project's folder first. A movie whose file was moved or
deleted shows grayed out, with `(file missing)`. A movie file whose size or modification time
changed since extraction shows grayed out, with `(file changed since extraction)`. The status
line then ends with, for example, `1 of 1 movie(s) not shown`.

Tether does not download a movie that is only in the cloud, such as a OneDrive file. Such a
movie shows `(file not on this disk)`. Make the file available on this computer, then open the
project again. A project imported without spot coordinates has no movie panel.

A [round-trip reconstruction](../io/legacy-import.md#round-trip-reconstruction) of a
Deep-LASI export does not record where each channel sits in the frame. Its movie shows
without dots, marked `(spot positions not recorded)`.

A project you opened before on this computer opens at the molecule you last had selected in it.

## 2. Move from trace to trace

The list on the left holds every molecule. The large panel shows the selected one. On top are
the donor (green), the acceptor (magenta) and their total (gray). Underneath is its apparent
FRET efficiency (blue).

Readers with a red-green color-vision deficiency can tell these colors apart. For a
red acceptor, select **View → Trace colors → Classic green and red**. See
[Trace colors](../reference/gui-theme.md#select-the-trace-colors).

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

### Go between a trace and its movie spot

The **Movie** panel follows the list. When you select a molecule, the panel shows the
molecule's own movie and puts its donor spot in the center. If the molecule is in another
movie, the panel changes to that movie, and the **Movie** box names it.

- To go back to the spot of the selected molecule, press **Enter**. Use this after you move
  the view or choose another movie. The status bar then reads, for example,
  `Jumped to mol-4 in movie movie_011.tif`.
- To select a molecule from the movie, click its donor or acceptor spot in the panel. The
  click must be inside the ring of the spot. The list then selects that molecule and the
  view does not move. A click outside all rings selects nothing, and a drag moves the view.

A molecule whose movie is not shown has no spot to go to. For such a molecule, **Enter**
gives the reason, for example
`Jump to movie spot unavailable: mol-4 has no spot in a movie shown here`.

### Move through the trace and the movie together

The trace plots have a **frame cursor**: a vertical line on the intensity plot and on the
FRET plot. The movie panel follows the cursor, and the cursor follows the movie.

- To see a frame of the trace in the movie, drag the cursor. It stops on whole frames, and
  the panel shows the same frame of the molecule's movie.
- To see where a movie frame is on the trace, move the time slider under the movie. The
  cursor moves to that frame. Before the trace starts or after it ends, the cursor stops at
  the first or last frame of the trace.
- When you select another molecule in the list, the cursor stays on its frame and the movie
  shows that frame.
- When you click a spot to select its molecule, the movie stays on its frame. The cursor
  moves to that frame.

The link works only while the panel shows the selected molecule's own movie. If you choose
another movie in the **Movie** box, the cursor and that movie do not move each other. When
you choose the molecule's movie again, the cursor moves to the frame that the movie shows.

To turn the link off, clear **Curation → Link trace and movie frames**. The setting is for
Curate and correct in this window only. It stays as you set it while the window is open, also
when you open another project. When you start Tether again, the link is on.

**Curation → Keyboard shortcuts…** (also in the **Help** menu) shows every key at any time.
The keys on this page are the ones Tether starts with. To move one, see
[Change a key](#change-a-key).

The curation keys act only in the **Curate and correct** section. In **Prepare** and
**Analyze** they change no molecule. There, **Help → Keyboard shortcuts…** lists no key. To
curate again, choose **View → Section → Curate and correct**.

You can open a section at any time. Click its name on the section bar under the menu bar,
or choose it in **View → Section**. Or press Ctrl+1 for Prepare, Ctrl+2 for Curate and correct
and Ctrl+3 for Analyze. On macOS, use Cmd.

The section bar also shows the next step that `tether status` gives for the open project.
Click **Go to** on the bar to open the section of that step. A project made from imported
traces opens in **Curate and correct**.

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

Each decision is written into the project as you make it. There is no save step.

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

## 4. Set a category and the analysis window

These keys save to the project at once, like accept and reject. None of them moves the
selection.

- **1** to **9** give the selected molecule a category from the list of its condition.
  **1** is the first name in the list, **2** the second, and so on. The status bar reads, for
  example, `Category 2 (bound) set for` and the ID. For a category after the ninth, choose
  **Curation → Assign category (overflow)…** and pick it from the list. A number past the end
  of the list changes nothing. The status bar then says how many categories the condition has.
- **0** clears the category. The molecule is then *uncategorized*, which is not the same as
  any named category.
- **-** and **=** move the start of the
  [analysis window](../reference/parameters.md) one frame earlier or later. **[** and **]**
  move its end. The analysis window is the part of the trace that the histograms,
  idealization and the other analyses read.
- **R** resets the analysis window to the whole trace.
- **P** finds where the total intensity photobleaches and ends the analysis window there. The
  start stays where it is. If the trace does not bleach, the window ends at the last frame.

The trace panel marks the analysis window with two dashed lines on the FRET plot. An
idealization is drawn only inside it. After each window key the status bar gives the window
that is now saved, for example `Analysis window [12, 340) for` and the ID. The first number
is the first frame inside the window and the second is the first frame after it. A window key
that would move a bound off the trace or empty the window changes nothing. The status bar
then says why.

Every one of these keys changes nothing while a one-click idealization is running or when the
project is read-only. The status bar then reads `unavailable` and gives the reason. Each saved
change is added to the project's molecule edit log, with your curator name.

A window you set by hand is kept when photobleach detection runs again over the whole project.
After **R**, that detection may set the window of the molecule automatically again.

## 5. Change your mind

- To accept a molecule you rejected, select it and press **Space**. Tether then shows the
  next molecule, as after any accept.
- To return a rejected molecule to *uncurated*, select it. Then click **Un-reject selected**
  in the panel on the left. You can also move the focus to that button with **Tab** and press
  **Space** or **Enter**. The status bar reads `Un-rejected` and the ID. On a molecule that is
  not rejected it reads `Un-reject unavailable: selected molecule is not rejected` and changes
  nothing.

Nothing you do here deletes a molecule or a decision. Every accept, reject and un-reject is
added to the project's [curation log](../reference/tether-format.md#labels-the-append-only-curation-log),
so the history survives a reversal.

## Change a key

You can move every curation key, for example to a key that is easier to reach. Choose
**Curation → Edit keyboard shortcuts…**. The window lists each action with its key.

1. Click the action, for example **Accept trace**.
2. Click the **New key** field and press the key you want. You can hold Shift, Ctrl or Alt
   with it.
3. If the key is free, click **Assign**. The table shows the new key.
4. Click **Save**. The new key works at once. **Curation → Keyboard shortcuts…** shows it.
   Tether uses it again the next time it starts.

**Cancel** closes the window and keeps the keys you had.

The window tells you when it cannot use a key, and **Assign** stays off:

- A key another action already has. Each key does one thing, so give that action another key
  first.
- **C** and **V**. They stay reserved and do nothing.
- A key a menu command already uses, such as Ctrl+O for **File → Open project…**.
- **Tab**, **Shift+Tab** and **Esc**. They move between the fields and close the window.

A letter or digit you assign still types into the **Category name** field. There it never
curates.

An action you give a new key loses its old keys. **Reject trace**, for example, then has
only the new key. To get an action's first keys back, click it and then **Restore default**.
When another action now has one of those keys, the window names it and changes nothing.
**Restore all defaults** gives every action its first keys.

Tether keeps the keys in a file named `keymap.json`. The editor shows where, under the table:

- Windows: `%LOCALAPPDATA%\MondragonLab\Tether\tether\`
- macOS: `~/Library/Preferences/MondragonLab/Tether/tether/`
- Linux: `~/.config/MondragonLab/Tether/tether/`, or under `$XDG_CONFIG_HOME` when that
  variable is set

Delete that file to start with the first keys again. Tether may not be able to use the file,
for example after an edit by hand. It then starts with the first keys and says so once in the
status bar. It does not change the file until you save new keys in the editor.

A file from an older version of Tether keeps your keys. An action that version did not have
gets its first key, unless you already use that key.

## What each label does downstream

- **Uncurated** is where every molecule starts. Uncurated molecules are **included** in
  histograms, idealization and the other analyses, exactly like accepted ones.
- **Accept** records your judgment. Accepting an uncurated molecule does not change which
  molecules an analysis uses, because only rejection removes a molecule. Accepting a
  rejected one does: it replaces the rejection and puts the molecule back in.
- **Reject** keeps the molecule out of population analyses and idealization, and out of the
  trace matrix and subset exports. The molecule-table CSV still lists it, with `reject` in
  its `curation_label` column. That table is a full inventory
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
credits the change to `your-name`. Close the Tether application window first. Then run the
script with the Python that came with Tether. At the default install locations, that is:

- `~/tether/envs/tether/bin/python` on Linux.
- `~/Tether/envs/tether/bin/python` on macOS.
- `$env:USERPROFILE\Tether\envs\tether\python.exe` on Windows.

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

Delete that file and the application forgets all of it. Nothing else changes. Beside it sits
an empty file, `preferences.json.lock`. It lets two open Tether windows take turns saving, so
neither loses what the other saved. To use another
file, set the environment variable `TETHER_PREFERENCES_FILE` to its full path.

## Next

[Correct the dataset for leakage and gamma](apply-corrections.md), then
[idealize the traces](idealize.md).
