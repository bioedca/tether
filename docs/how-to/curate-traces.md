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
- No other Tether window, script or batch run has the same project open. Only one writer may
  change a project at a time, and a second window opens it read-only. See
  [The project is locked by another user or
  machine](../troubleshooting.md#the-project-is-locked-by-another-user-or-machine).

## 1. Open the project

```bash
tether-gui movie.tether
```

On Windows and Linux you can also start Tether from the Start menu or applications menu and
choose **File → Open project…**.
When the project opens, the status bar at the bottom of the window names it and its molecule
count:

```text
Opened movie.tether — 412 molecule(s)
```

If that line ends in `(read-only)` after the file name, another writer holds the project and
nothing you mark will be saved. Close the other writer and open the project again.

## 2. Move from trace to trace

The list on the left holds every molecule. The large panel shows the selected one: donor
(green), acceptor (red) and their total (grey) on top, its apparent FRET efficiency (blue)
underneath.

1. Press **Right arrow** or **Down arrow** for the next molecule.
2. Press **Left arrow** or **Up arrow** for the previous one.
3. Or click a name in the list.

**Curation → Keyboard shortcuts…** shows every key at any time.

## 3. Accept or reject each molecule

1. Press **Space** to accept the selected molecule. The status bar reads `Accepted` followed
   by the molecule's ID.
2. Press **Backspace** or **Delete** to reject it. The status bar reads `Rejected` followed by
   the ID.

Each decision is written into the project as you make it; there is no save step.

> The number keys, the analysis-window keys and **P** answer with `unavailable` in the status
> bar and change nothing in the project. Categories, window edits and photobleach detection
> are not saved from the curation view.

## 4. Change your mind

- To accept a molecule you rejected, select it and press **Space**.
- To return a rejected molecule to *uncurated*, select it and click **Un-reject selected** in
  the panel on the left. The status bar reads `Un-rejected` and the ID. On a molecule that is
  not rejected it reads `Un-reject unavailable: selected molecule is not rejected` and changes
  nothing.

Nothing you do here deletes a molecule or a decision. Every accept, reject and un-reject is
added to the project's [curation log](../reference/tether-format.md#labels-the-append-only-curation-log),
so the history survives a reversal.

## What each label does downstream

- **Uncurated** is where every molecule starts. Uncurated molecules are **included** in
  histograms, idealization and the other analyses, exactly like accepted ones.
- **Accept** records your judgement. Accepting an uncurated molecule does not change which
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
decision. The desktop application fills it in with your login name on the computer you curate
on, and has no field to change it.

To record a different name, make the change from Python instead, with
`labeler=`. For example, this script un-rejects every rejected molecule in the project and
credits the change to `your-name`. Close the Tether window first, then run it with the Python
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

## Next

[Correct the dataset for leakage and gamma](apply-corrections.md), then
[idealize the traces](idealize.md).
