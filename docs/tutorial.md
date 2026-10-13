# Your first analysis

**Who this page is for.** Someone who has just installed Tether by following
[Install Tether](install.md) and wants to see it work once, start to finish, before trying
their own data. You will take one small sample movie to one finished result: a project,
three molecules looked at and sorted, one idealized trace, one population histogram and one
exported table. It takes about fifteen minutes and needs no data of your own.

Each step shows what you should see when it worked. If what you see differs, stop there and
check [Troubleshooting](troubleshooting.md) before going on.

## 1. Check that Tether answers

Open a terminal and run:

```bash
tether --version
```

It prints `tether` and a version number, as in
[step 5 of the install guide](install.md#5-check-that-tether-runs). If it says the command is
not found, finish [step 4 of the install guide](install.md#4-put-tethers-launcher-folder-on-your-path)
first.

## 2. Download the sample movie

Make a new, empty folder for this tutorial, for example `tether-tutorial` in your home
folder, then download [tutorial_movie.tif](tutorial/data/tutorial_movie.tif) into it.

The file is 157,674 bytes. It is a short synthetic movie in the same layout as a real
dual-view acquisition: 24 frames of 40 × 80 pixels, the donor channel in the left half and
the acceptor channel in the right half, with three molecules in each half.

In the terminal, move into that folder:

```bash
cd ~/tether-tutorial
```

On Windows PowerShell the same folder is `cd $env:USERPROFILE\tether-tutorial`.

## 3. Extract the molecules

Run:

```bash
tether extract tutorial_movie.tif -o tutorial.tether
```

It finishes in a few seconds and prints one line:

```text
Extracted 3 molecule(s) -> tutorial.tether
```

Tether found the three molecules, matched each donor spot to its acceptor spot, and wrote
their intensity traces into a new project file, `tutorial.tether`, next to the movie. That one
file is where everything from here on is saved. The [Command line](cli.md#tether-extract)
reference lists the options `tether extract` accepts; this tutorial needs none of them.

## 4. Open the project

Start the desktop application with the project already open:

```bash
tether-gui tutorial.tether
```

You can also start Tether from your Start menu or applications menu and choose
**File → Open project…**, then pick `tutorial.tether`.

The status bar at the bottom of the window reads:

```text
Opened tutorial.tether — 3 molecule(s)
```

The list on the left holds three molecules, `mol-0`, `mol-1` and `mol-2`, with `mol-0`
selected. The large panel shows the selected molecule's trace: on top, the donor intensity
in green, the acceptor intensity in red and their total in grey; underneath, its apparent
FRET efficiency in blue.

## 5. Look at each trace

Press the **Right arrow** (or **Down arrow**) to move to the next molecule and the **Left
arrow** (or **Up arrow**) to go back. Clicking a name in the list works too. The status bar
names the molecule you are on, for example:

```text
Molecule mol-1 (2/3)
```

Look at all three:

- **`mol-0`**: green and red stay level for the first half, then **both jump up together**
  and stay up. The blue trace sits near 0.5 the whole time.
- **`mol-1`**: red starts high and green low, then halfway through they **swap**. The blue
  trace steps down once, from about 0.75 to about 0.25.
- **`mol-2`**: green and red swap places three times, so the blue trace steps between about
  0.25 and about 0.75 four times in all.

In `mol-1` and `mol-2` the green and red traces mirror each other: when one goes up, the
other goes down. That is what a change in FRET looks like. In `mol-0` they move the same way
at the same moment, which is a change in brightness, not in FRET, so this is the molecule
you will set aside.

## 6. Reject one molecule and accept two

Go back to `mol-0` and press **Backspace** to reject it. The status bar confirms it with the
molecule's 64-character ID:

```text
Rejected b4d11875527ee175c9c0e414d154db7a81a9bca669d3573f66cb1184a511a09d
```

Move to `mol-1` and press **Space** to accept it:

```text
Accepted 463104a0a9ee0dca67d4eb2981655e11d79ac500d70fbb3cdc5c388de7a77392
```

Move to `mol-2` and press **Space** again:

```text
Accepted 7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057
```

Each decision is saved into `tutorial.tether` as you make it; there is no separate save
step. Choose **Curation → Keyboard shortcuts…** any time to see every key Tether uses.

## 7. Idealize one trace

With `mol-2` still selected, press **I**. The status bar shows that a fit has started:

```text
Idealizing 7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057 (one-click vbFRET)…
```

The first fit after starting Tether takes longer than later ones while the fitting engine warms up;
the window stays usable meanwhile. When it is done the status bar reads:

```text
Idealized 7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057 (one-click vbFRET)
```

and a black stepped line is drawn over the blue trace, jumping between a low level near 0.25
and a high level near 0.75 at the same places the blue trace does.

This step uses the separate tMAVEN environment the installer sets up for you. If you run
Tether from a source checkout instead of an installer, set that environment up first as
[Standalone-tMAVEN hand-off](idealize/standalone-tmaven-handoff.md#guided-sidecar-setup)
describes.

## 8. Draw the population histogram

Choose **Analysis → Population histogram…**. A **Population histogram** panel opens and the
status bar reads:

```text
Population histogram — 2 molecule(s), 48 frame(s)
```

Two molecules, because the one you rejected is left out; 48 frames, because each of the two
has 24. The histogram has two peaks, one near 0.25 and one near 0.75, the two FRET levels
you saw in the traces.

## 9. Export the molecule table

Close the Tether window. Then start the Python that came with Tether, from the same
`tether-tutorial` folder, with the line for your system:

```bash
~/tether/envs/tether/bin/python     # Linux
~/Tether/envs/tether/bin/python     # macOS
```

```powershell
& "$env:USERPROFILE\Tether\envs\tether\python.exe"
```

These are the default install folders; if you chose another one, use that instead. At the
`>>>` prompt, type these three lines:

```pycon
>>> from tether.project.export import export_molecule_table_csv
>>> result = export_molecule_table_csv("tutorial.tether", "tutorial.csv", include_rejected=False)
>>> result.n_molecules
2
```

Type `exit()` to leave Python. Your folder now holds `tutorial.csv`, a table with one row
for each of the two molecules you accepted, and `tutorial.csv.provenance.json`, a record of
how the table was made. Open `tutorial.csv` in a spreadsheet: its `curation_label` column
reads `accept` on both rows, and `mean_apparent_e` holds each molecule's average FRET
efficiency. [Exports](reference/exports.md#molecule-table-columns) describes every column.

## You are done

You took one movie through every stage Tether has: extraction, curation, idealization, a
population histogram and an export. To do the same with your own movies:

- The how-to guides take each stage in turn on your own data, with the options this tutorial
  left out: [extract traces](how-to/extract-traces.md), [curate them](how-to/curate-traces.md),
  [correct for leakage and gamma](how-to/apply-corrections.md),
  [idealize them](how-to/idealize.md),
  [draw a histogram, TDP and dwell plots](how-to/analyze.md) and
  [export the results](how-to/export-results.md).
- [Does Tether fit my data?](compatibility.md) says which movies Tether reads.
- [How Tether works](pipeline.md) explains each stage you just used.
- [Command line](cli.md) covers `tether extract` options and `tether batch` for many movies
  at once.
- [Exports](reference/exports.md) covers the other files Tether can write.
