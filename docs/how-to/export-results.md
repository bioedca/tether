---
description: "Write the results of a project to a table, to Deep-LASI-compatible trace files or to a smaller project for a collaborator."
---

# Export results for a figure or another tool

## Who this page is for

You have a `.tether` project that you have curated, and probably corrected and idealized. You
want its results in a file that a person or another program can read. That can be one of
these:

- A table for a spreadsheet or a plotting script.
- The traces for a Deep-LASI-compatible tool.
- A smaller project to send a collaborator.

[Draw a FRET histogram, a TDP and dwell-time survival plots](analyze.md) makes the figures of
the analysis plots.

## Before you start

- The project file, here called `movie.tether`. Exporting reads it and never changes it. It
  takes no lock, so you can export a project that another user has open.
- For the menu way, first in each section: the Tether application, with the project open. You
  do not close it to export.
- Only for the command way: the `tether` command. If typing `tether --version` does not print a version, put the
  launcher folder on your `PATH` first ([Command line](../cli.md#launching-tether-after-an-installer-install)).
  Every export on this page is one `tether export` command. The [Command
  line](../cli.md#tether-export) page lists all of its options.
- Only for the Python way at the end of each section: the Python that came with Tether. At
  the default install locations, it is `~/tether/envs/tether/bin/python` on Linux and
  `~/Tether/envs/tether/bin/python` on macOS. On Windows, it is
  `$env:USERPROFILE\Tether\envs\tether\python.exe`. Save the lines as a `.py` file beside
  the project, and run it with that Python. Or type the lines at its `>>>` prompt.

## Choose what to export

- **One row per molecule, for a spreadsheet, R, pandas, Prism or Origin:** the
  [molecule-table CSV](../reference/exports.md#1-molecule-table-csv).
- **The per-frame donor and acceptor traces, for Deep-LASI or a tool that reads its
  format:** the [Deep-LASI trace matrix](../reference/exports.md#2-deep-lasi-trace-matrix-txt).
- **A self-contained project of some molecules, for re-analysis without the movie:** a
  [subset `.tether`](../reference/exports.md#3-subset-tether).

Every one of them also writes a [provenance
file](../reference/exports.md#provenance-file) beside the file, named after it with
`.provenance.json` added. It records which Tether version, which project and which settings
made the file. Keep it with the file when you move or share it. For the CSV and the trace
matrix, it is the only such record. To put these files in a public repository, follow
[Share and deposit a dataset](share-a-dataset.md).

## Export from the application

Each export is one item of **File → Export** in the application. It exports the open project,
which stays open. While it runs, the busy indicator at the right of the status bar shows its
name and a **Cancel** button. A cancelled export writes nothing. Changes to the project, such
as a curation decision, wait until the export ends.

When it ends, the status bar names the count and the file, for example
`Exported 412 molecule(s) -> molecules.csv`. When the export function refuses, the status bar
shows its message after the name of the export, for example
`Export the traces for Deep-LASI failed: no molecules selected to export (…)`.
[An export stops with a message](../troubleshooting.md#an-export-stops-with-a-message) lists
them.

## Export the molecule table

1. Choose **File → Export → Molecule table (CSV)…**.
2. **Destination** proposes `movie.csv` beside the project. Type another path, or click
   **Browse…**.
3. **Include rejected molecules** is ticked, as for the command. Clear it to leave the rejected
   molecules out.
4. Click **Export**.

The same export from the command line:

```bash
tether export table movie.tether -o molecules.csv
```

This writes `molecules.csv` and `molecules.csv.provenance.json`. Rejected molecules are
**included**, with `reject` in the `curation_label` column, so filter on that column in your
own tool. To leave them out of the file instead, add `--exclude-rejected`.

You can export the table of every project in a folder, for example after `tether batch`. To
do this, give all the projects and a folder. Each table is named after its project, here
`tables/movie_010.csv` and so on. Make the folder first. If the pattern matches only one
project, `-o` names the file to write, unless it is an existing folder.

```bash
mkdir tables
tether export table projects/*.tether -o tables
```

The command refuses to replace a file that exists. Add `--overwrite` to replace it.

The same export from Python:

```python
from tether.project.export import export_molecule_table_csv

result = export_molecule_table_csv("movie.tether", "molecules.csv")
print(f"{result.n_molecules} rows -> {result.path}")
```

There, `include_rejected=False` leaves the rejected molecules out.

Two columns need care when you read the file:

- A blank cell means *not set*, not zero. Read blanks as missing values.
- `mean_apparent_e` and `median_apparent_e` are **apparent** efficiencies. The `alpha` and
  `gamma` columns carry the correction factors, so you can compute a corrected value
  yourself.
  [Reading the exports elsewhere](../reference/exports.md#reading-the-exports-elsewhere) shows
  how.

## Export the traces for Deep-LASI

1. Select a molecule of the movie that you want, if the project holds several movies.
2. Choose **File → Export → Traces (Deep-LASI text)…**.
3. **Destination** proposes `movie-donc-accc-w.txt` beside the project.
4. Tick **Include rejected molecules** to keep them.
5. Tick **Only the movie of the selected molecule** to export one movie. A project of movies of
   different lengths needs it, as the end of this section explains.
6. Click **Export**.

The same export from the command line:

```bash
tether export traces movie.tether -o traces
```

This writes `traces/movie-donc-accc-w.txt`. It holds the background-subtracted traces of every
molecule that you did not reject. It has one row per frame and no header. Each molecule has a
donor column, then an acceptor column.

`-o` names a folder, and the command names the file after the
project. To keep the rejected molecules, add `--include-rejected`. To export only some
molecules, add `--molecule-key` once for each molecule.

The same export from Python, where you choose the file name:

```python
from tether.project.export import export_deeplasi_txt

result = export_deeplasi_txt("movie.tether", "movie-donc-accc-w.txt")
print(f"{result.n_molecules} molecules -> {result.path}")
```

Tether writes the `-donc-accc-w.txt` ending only if you name the file that way, as above.

The file has no molecule names. So its column pairs line up with the molecule-table rows only
when both leave out the same molecules. This export leaves out rejected molecules,
and the molecule table keeps them. You may want to pair the two, for example to apply the γ
of each molecule to its trace. Then export the table with `include_rejected=False` as well
(`--exclude-rejected`).

The molecule table always covers the whole project and takes no
`molecule_keys=`. So for a project of several movies, first make a subset of one movie and
export both files from it. The table's `movie_id` column identifies each molecule's movie, and `source_filename`
beside it names the movie's file. Two movies can share a file name, so pick the movie by its
`movie_id`, here `mov-1`:

```python
import csv

from tether.project.export import (
    export_deeplasi_txt,
    export_molecule_table_csv,
    export_subset_tether,
)

export_molecule_table_csv("movie.tether", "molecules.csv", include_rejected=False)
with open("molecules.csv", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
one_movie = [row["molecule_key"] for row in rows if row["movie_id"] == "mov-1"]

export_subset_tether("movie.tether", "movie_010.tether", molecule_keys=one_movie)
export_molecule_table_csv("movie_010.tether", "movie_010.csv", include_rejected=False)
export_deeplasi_txt("movie_010.tether", "movie_010-donc-accc-w.txt")
```

The first row of the file is not always frame 0 of the movie. The provenance file's `frame_range`
records which frames it covers. Every molecule in one file shares that one frame axis. So
export a project of movies of different lengths one movie at a time. Pass that movie's
molecules as `molecule_keys=` (or `--molecule-key`), like `one_movie` in the script above.
Otherwise the export stops with an error naming the frame ranges.

## Export a subset project

1. Choose **File → Export → Subset project…**.
2. **Destination** proposes `movie-subset.tether` beside the project. It cannot be the project
   itself.
3. Tick **Include rejected molecules** or **Include raw traces** if you want them.
4. Tick **Replace curator names** to send a copy without the user names. Keep the prefix
   `curator` or type another
   ([Share a subset without your user name](#share-a-subset-without-your-user-name)).
5. Click **Export**. A file of that name is replaced.

The same export from the command line:

```bash
tether export subset movie.tether -o subset.tether
```

This writes `subset.tether` with every molecule you did not reject. To choose the molecules,
add `--molecule-key` once for each one. You may want to send only the molecules
that you accepted, and not the uncurated ones. Then the Python way is shorter, because it can
read the curation labels first:

```python
from tether.project.export import export_subset_tether
from tether.project.labels import CurationLabel, curation_labels

labels = curation_labels("movie.tether")
accepted = [key for key, label in labels.items() if label == CurationLabel.ACCEPT]

result = export_subset_tether("movie.tether", "accepted.tether", molecule_keys=accepted)
print(f"{result.n_molecules} molecules -> {result.path}")
```

Leave out `molecule_keys=` to export every molecule you did not reject. The subset carries the
background-subtracted traces, image crops, idealization models and curation history of the
molecules. So it needs neither the movie nor the original project. It leaves out the raw
traces unless you add `--include-raw` (`include_raw=True` in Python). Without them, it also
leaves out any idealization model fitted on raw traces.

The provenance file names a model left out that way under
`skipped_idealization_models`. It refuses to replace an existing file unless you add
`--overwrite` (`overwrite=True`).

### Share a subset without your user name

The project records your operating-system user name with each label and with each
condition change. To send a copy without the names, give a prefix:

```python
from tether.project.export import export_subset_tether

export_subset_tether("movie.tether", "shared.tether", curator_alias="curator")
```

Each person becomes `curator-1`, `curator-2`, and so on. One person keeps one alias, so
you can still compare curators in the copy. The `.provenance.json` file records that names
were replaced and how many, but not the names. Your project does not change. The copy
still has the file name of each source movie in `source_filename`. A file name can identify
a sample, so read it before you share the copy
([User names](../reference/exports.md#3-subset-tether)).

The path of a model file keeps only the file name. A folder name can have your user name
in it. A job record keeps the options of the command that ran. They can hold a full path,
so read them too.

A project imported for analysis only, from a Deep-LASI `.txt` or a tMAVEN SMD file, has no
image crops. So it cannot be exported as a subset. The export stops with an error. Use the
molecule table and the trace export for such a project instead
([Legacy Deep-LASI import](../io/legacy-import.md)).

## Check the export

1. The number printed, `Exported 412 molecule(s) -> molecules.csv` for example, is the count
   of molecules that went into the file. Compare it with what you expect after curation.
2. The `.provenance.json` file sits beside the export. Open it in a text editor. Its
   `parameters` say what was included.
3. Frame numbers in all three exports count frames, not seconds. To convert them, see
   [Frames to seconds](../reference/exports.md#frames-to-seconds).
