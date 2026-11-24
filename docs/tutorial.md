---
description: "Take a small sample movie through Tether from extraction to an exported table in about fifteen minutes, with no data of your own."
---

# Your first analysis

**Who this page is for.** Someone who has just installed Tether by following
[Install Tether](install.md). They want to see it work once, start to finish, before they try
their own data.

You will take one small sample movie to one finished result. The result is a
project with three molecules looked at and sorted. It also has one idealized trace, one
population histogram and one exported table. It takes about fifteen minutes and needs no data of your own.

Each step shows what you should see when it worked. If what you see differs, stop there. Read
[Troubleshooting](troubleshooting.md) before you go on.

## 1. Check that Tether answers

Open a terminal and run:

```bash
tether --version
```

It prints `tether` and a version number, as in
[step 5 of the install guide](install.md#5-check-that-tether-runs).

If it says the command is not found, open a terminal as
[step 4 of the install guide](install.md#4-open-a-terminal-where-tether-works) tells. On
Windows, from the next release, that is the **Tether Command Prompt**. On other systems, do the
optional `PATH` step there. Or type the launcher's full path for `tether` and `tether-gui` in
every command of this tutorial.

## 2. Download the sample movie

1. Make a new, empty folder for this tutorial, for example `tether-tutorial` in your home
   folder.
2. Download [tutorial_movie.tif](tutorial/data/tutorial_movie.tif) into it.

The file is 157,674 bytes. It is a short synthetic movie in the same layout as a real
dual-view acquisition. It has 24 frames of 40 × 80 pixels. The donor channel is in the left
half and the acceptor channel is in the right half. Each half holds three molecules.

The movie shows no real sample. Tether's own script
[`scripts/make_tutorial_movie.py`](https://github.com/bioedca/tether/blob/main/scripts/make_tutorial_movie.py)
made it. You can reuse it under the same license as Tether, GPL-3.0-or-later.

In the terminal, move into that folder:

```bash
cd ~/tether-tutorial
```

In Windows PowerShell, the command is `cd $env:USERPROFILE\tether-tutorial`. In the Tether
Command Prompt, it is `cd /d "%USERPROFILE%\tether-tutorial"`.

## 3. Extract the molecules

Run:

```bash
tether extract tutorial_movie.tif -o tutorial.tether
```

It finishes in a few seconds and prints three lines:

```text
Extracted 3 molecule(s) -> tutorial.tether
  colocalization: partner-in-both kept 3 (donor-anchored would keep 3)
  donor side: not enough bleach events to check the donor side (0 found, 15 needed).
```

Tether found the three molecules and matched each donor spot to its acceptor spot. It wrote
their intensity traces into a new project file, `tutorial.tether`, next to the movie. That one
file is where everything from here on is saved.

The second line says that Tether kept a molecule only where it found an acceptor spot too. It
also says that to keep every donor spot would give the same three here
([choosing the rule](cli.md#choose-the-colocalization-rule)).

The third line is the check that the donor and acceptor halves are not swapped. The check needs
at least 15 bleach events in which one channel drops and the other clearly rises. On three
molecules, it says that it cannot tell. That is expected here.

The [Command line](cli.md#tether-extract) reference lists the options that `tether extract`
accepts. This tutorial needs none of them.

## 4. Open the project

Start the desktop application with the project already open:

```bash
tether-gui tutorial.tether
```

You can also start Tether from your Start menu or applications menu. Then choose
**File → Open project…** and pick `tutorial.tether`.

The status bar at the bottom of the application window reads:

```text
Opened tutorial.tether — 3 molecule(s)
```

The list on the left holds three molecules, `mol-0`, `mol-1` and `mol-2`, with `mol-0`
selected. The large panel shows the trace of the selected molecule. On top are the donor
intensity in green, the acceptor intensity in magenta and their total in gray. Underneath is
its apparent FRET efficiency in blue.

These are the default **Color-blind safe** trace colors. You may have selected
**View → Trace colors → Classic green and red** before. If so, Tether keeps that choice, and
your acceptor trace is red. Wherever this tutorial says magenta, read red. In that menu, the
selected set has a check mark.
[Select the trace colors](reference/gui-theme.md#select-the-trace-colors) tells how to change it.

## 5. Look at each trace

1. Press the **Right arrow** (or **Down arrow**) to move to the next molecule.
2. Press the **Left arrow** (or **Up arrow**) to go back.

You can also click a name in the list. The status bar names the molecule you are on, for
example:

```text
Molecule mol-1 (2/3)
```

Look at all three:

- **`mol-0`**: green and magenta stay level for the first half. Then they **both jump up
  together** and stay up. The blue trace sits near 0.5 the whole time.
- **`mol-1`**: magenta starts high and green low. Halfway through, they **swap**. The blue
  trace steps down once, from about 0.75 to about 0.25.
- **`mol-2`**: green and magenta swap places three times. So the blue trace steps between about
  0.25 and about 0.75 four times in all.

In `mol-1` and `mol-2`, the green and magenta traces mirror each other. When one goes up, the
other goes down. That is what a change in FRET looks like. In `mol-0`, they move the same way
at the same moment. That is a change in brightness, not in FRET. So this is the molecule that
you will set aside.

## 6. Reject one molecule and accept two

1. Go back to `mol-0`.
2. Press **Backspace** to reject it.

The status bar confirms it with the 64-character ID of the molecule. Tether then shows the next
molecule by itself. So the same line also names `mol-1`, which is now selected:

```text
Rejected b4d11875527ee175c9c0e414d154db7a81a9bca669d3573f66cb1184a511a09d; Molecule mol-1 (2/3)
```

Press **Space** to accept `mol-1`. Tether moves on to `mol-2`:

```text
Accepted 463104a0a9ee0dca67d4eb2981655e11d79ac500d70fbb3cdc5c388de7a77392; Molecule mol-2 (3/3)
```

Press **Space** again to accept `mol-2`. It is the last molecule, so it stays selected:

```text
Accepted 7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057; end of the list reached
```

Each decision is saved into `tutorial.tether` as you make it. There is no separate save step.
To see every key that Tether uses, choose **Curation → Keyboard shortcuts…** at any time.

## 7. Idealize one trace

This step uses the separate tMAVEN environment that the installer sets up for you. If you run
Tether from a source checkout instead of an installer, set that environment up first.
[Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md#guided-sidecar-setup) tells
you how.

With `mol-2` still selected, press **I**. The status bar shows that a fit has started:

```text
Idealizing 7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057 (one-click vbFRET)…
```

The first fit after Tether starts takes longer than later ones, while the fitting engine warms
up. The application window stays usable meanwhile. When the fit is done, the status bar reads:

```text
Idealized 7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057 (one-click vbFRET)
```

A stepped line is then drawn over the blue trace. It jumps between a low level near 0.25 and a
high level near 0.75. It jumps at the same places as the blue trace. The line is black in the light
theme and white in the dark theme
([Application window themes and trace colors](reference/gui-theme.md#trace-colors)).

## 8. Draw the population histogram

1. Choose **View → Section → Analyze**. You can also click **Analyze** on the section bar, or
   press Ctrl+3 (Cmd+3 on macOS). Each section has its own menus, and the **Analysis** menu is
   in this one.
2. Choose **Analysis → Population histogram…**.

A **Population histogram** panel opens, and the status bar reads:

```text
Population histogram — 2 molecule(s), 48 frame(s)
```

There are two molecules, because the histogram leaves out the one that you rejected. There are
48 frames, because each of the two molecules has 24. The histogram has two peaks, one near 0.25
and one near 0.75. These are the two FRET levels that you saw in the traces.

To go back to the traces, choose **View → Section → Curate and correct**.

## 9. Export the molecule table

The commands below use the default install folders. If you chose another folder, use that
folder instead.

1. Close the Tether application window.
2. In the same `tether-tutorial` folder, start the Python that came with Tether. Use the line
   for your system.

```bash
~/tether/envs/tether/bin/python     # Linux
~/Tether/envs/tether/bin/python     # macOS
```

```powershell
& "$env:USERPROFILE\Tether\envs\tether\python.exe"
```

In the Tether Command Prompt on Windows, the line is:

```bat
"%USERPROFILE%\Tether\envs\tether\python.exe"
```

At the `>>>` prompt, type these three lines:

```pycon
>>> from tether.project.export import export_molecule_table_csv
>>> result = export_molecule_table_csv("tutorial.tether", "tutorial.csv", include_rejected=False)
>>> result.n_molecules
2
```

Type `exit()` to leave Python.

Your folder now holds two new files:

- `tutorial.csv`, a table with one row for each of the two molecules that you accepted.
- `tutorial.csv.provenance.json`, a record of how Tether made the table.

Open `tutorial.csv` in a spreadsheet. Its `curation_label` column reads `accept` on both rows.
Its `mean_apparent_e` column holds the average FRET efficiency of each molecule.
[Exports](reference/exports.md#molecule-table-columns) describes every column.

## You are done

You took one movie through every stage Tether has: extraction, curation, idealization, a
population histogram and an export. To do the same with your own movies, follow the how-to
guides. They take each stage in turn on your own data, with the options that this tutorial left
out:

- [Extract traces](how-to/extract-traces.md).
- [Curate them](how-to/curate-traces.md).
- [Correct for leakage and gamma](how-to/apply-corrections.md).
- [Idealize them](how-to/idealize.md).
- [Draw a histogram, TDP and dwell plots](how-to/analyze.md).
- [Export the results](how-to/export-results.md).

Other pages tell you more:

- [Does Tether fit my data?](compatibility.md) says which movies Tether reads.
- [How Tether works](pipeline.md) explains each stage you just used.
- [Command line](cli.md) covers `tether extract` options and `tether batch` for many movies
  at once.
- [Exports](reference/exports.md) covers the other files Tether can write.
