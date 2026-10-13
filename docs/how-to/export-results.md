# Export results for a figure or another tool

## Who this page is for

You have a `.tether` project you have curated, and probably corrected and idealized, and you
want its results in a file someone or something else can read: a table for a spreadsheet or a
plotting script, the traces for a Deep-LASI-compatible tool, or a smaller project to send a
collaborator. Figures of the analysis plots are made on [Draw a FRET histogram, a TDP and
dwell-time survival plots](analyze.md).

## Before you start

- The project file, here called `movie.tether`. Exporting reads it and never changes it.
- The Python that came with Tether: `~/tether/envs/tether/bin/python` on Linux,
  `~/Tether/envs/tether/bin/python` on macOS, or
  `$env:USERPROFILE\Tether\envs\tether\python.exe` on Windows, at the default install
  locations. Tether has no export menu item and no `tether export` command, so every export is
  a few lines of Python. Save the lines as a `.py` file beside the project and run it with
  that Python, or type them at its `>>>` prompt.

## Choose what to export

- **One row per molecule, for a spreadsheet, R, pandas, Prism or Origin:** the
  [molecule-table CSV](../reference/exports.md#1-molecule-table-csv).
- **The per-frame donor and acceptor traces, for Deep-LASI or a tool that reads its
  format:** the [Deep-LASI trace matrix](../reference/exports.md#2-deep-lasi-trace-matrix-txt).
- **A self-contained project of some molecules, for re-analysis without the movie:** a
  [subset `.tether`](../reference/exports.md#3-subset-tether).

Every one of them also writes a [provenance
sidecar](../reference/exports.md#provenance-sidecar) beside the file, named after it with
`.provenance.json` added. It records which Tether version, which project and which settings
made the file. Keep it with the file when you move or share it: for the CSV and the trace
matrix it is the only such record.

## Export the molecule table

```python
from tether.project.export import export_molecule_table_csv

result = export_molecule_table_csv("movie.tether", "molecules.csv")
print(f"{result.n_molecules} rows -> {result.path}")
```

This writes `molecules.csv` and `molecules.csv.provenance.json`. Rejected molecules are
**included**, with `reject` in the `curation_label` column, so filter on that column in your
own tool. To leave them out of the file instead, add `include_rejected=False`.

Two columns need care when you read the file:

- A blank cell means *not set*, not zero. Read blanks as missing values.
- `mean_apparent_e` and `median_apparent_e` are **apparent** efficiencies. The `alpha` and
  `gamma` columns carry the correction factors so you can compute a corrected value yourself;
  [Reading the exports elsewhere](../reference/exports.md#reading-the-exports-elsewhere) shows
  how.

## Export the traces for Deep-LASI

```python
from tether.project.export import export_deeplasi_txt

result = export_deeplasi_txt("movie.tether", "movie-donc-accc-w.txt")
print(f"{result.n_molecules} molecules -> {result.path}")
```

This writes the background-subtracted traces of every molecule you did not reject, one row
per frame and a donor column then an acceptor column per molecule, with no header. Tether
writes the `-donc-accc-w.txt` ending only if you name the file that way, as above.

The file has no molecule names, so its column pairs line up with the rows of the molecule
table only when both leave out the same molecules. This export leaves out rejected
molecules and the molecule table keeps them, so if you will pair the two, for example to
apply each molecule's γ to its trace, export the table with `include_rejected=False` as
well. The molecule table always covers the whole project and takes no `molecule_keys=`, so for
a project that combines movies, first make a subset of one movie and export both files from
it. The table's `movie_id` column identifies each molecule's movie, and `source_filename`
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
    one_movie = [row["molecule_key"] for row in csv.DictReader(f)
                 if row["movie_id"] == "mov-1"]

export_subset_tether("movie.tether", "movie_010.tether", molecule_keys=one_movie)
export_molecule_table_csv("movie_010.tether", "movie_010.csv", include_rejected=False)
export_deeplasi_txt("movie_010.tether", "movie_010-donc-accc-w.txt")
```

The first row of the file is not always frame 0 of the movie. The sidecar's `frame_range`
records which frames it covers. Every molecule in one file shares that one frame axis, so a
project that combines movies of different lengths has to be exported one movie at a time,
passing that movie's molecules as `molecule_keys=`; otherwise the export stops with an error
naming the frame ranges.

## Export a subset project

To send only the molecules you accepted, and not the uncurated ones:

```python
from tether.project.export import export_subset_tether
from tether.project.labels import CurationLabel, curation_labels

labels = curation_labels("movie.tether")
accepted = [key for key, label in labels.items() if label == CurationLabel.ACCEPT]

result = export_subset_tether("movie.tether", "accepted.tether", molecule_keys=accepted)
print(f"{result.n_molecules} molecules -> {result.path}")
```

Leave out `molecule_keys=` to export every molecule you did not reject. The subset carries the
molecules' background-subtracted traces, image crops, idealization models and curation
history, so it needs neither the movie nor the original project. It leaves out the raw traces
unless you add `include_raw=True`, and with them any idealization model fitted on raw traces;
the sidecar names a model left out that way under `skipped_idealization_models`. It refuses to replace an existing file unless you add
`overwrite=True`.

A project imported for analysis only, from a Deep-LASI `.txt` or a tMAVEN SMD file, has no
image crops, so it cannot be exported as a subset; the export stops with an error. Use the
molecule table and the trace export for such a project instead
([Legacy Deep-LASI import](../io/legacy-import.md)).

## Check the export

1. The number printed is the count of molecules that went into the file. Compare it with what
   you expect after curation.
2. The `.provenance.json` file sits beside the export. Open it in a text editor: its
   `parameters` say what was included.
3. Frame numbers in all three exports count frames, not seconds. To convert them, see
   [Frames to seconds](../reference/exports.md#frames-to-seconds).
