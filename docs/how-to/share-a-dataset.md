# Share and deposit a dataset

## Who this page is for

You have finished an analysis in Tether. You want to give the data to a reviewer, a
collaborator or a public repository, so that another person can find it, open it and reuse it.
Journals and funders often ask for this, and the FAIR principles (findable, accessible,
interoperable, reusable) are the usual checklist. This page assumes that you can run a few
lines of Python and that you know your own sample and instrument. It does not assume that you
have used a data repository before.

## Before you start

- The project file, here called `tutorial.tether`. Change the name to the name of your own
  project file. The steps on this page read the project file and never change it.
- The Python that came with Tether. [Export results](export-results.md#before-you-start)
  shows where it is. Run all the Python blocks on this page in one session, in this order,
  because a later block uses a name that an earlier block defines.
- The raw movies that the project was made from.
- The output of `tether --version` from the computer that made the results.

## 1. Decide what to deposit

Put these files in one folder for the deposit:

- **The project file, or a subset of it.** A `.tether` file holds the traces, the image crops,
  the curation history, the correction factors α and γ, the idealization models and the
  settings of each step. A reader opens it with Tether or with any HDF5 reader, for example
  `h5py`. A subset holds only the molecules that you choose.
- **A provenance file beside each export.** Tether writes a `.provenance.json` file beside
  each export. It records the Tether version, the source project and the settings of the
  export. It is plain JSON text, so a reader needs only a text editor.
- **The molecule table.** This CSV file has one row per molecule. A reader opens it in a
  spreadsheet, R or pandas without Tether.
- **The figures** that you exported, with their provenance files.
- **The raw movies.** The movies are the only way back to the pixels. A reader opens them with
  any TIFF reader.
- **The registration input, if you used one.** This is the bead movie or the imported `.tmap`
  file that registered the two channels.
- **A `README.md` file** that describes the deposit. Step 4 gives a template.

Deposit the raw movies with the project. A reader can then check each step of the analysis,
from the pixels to the result. The movies are often much larger than the project file. If
their size prevents a deposit, put this statement in the README in their place:

> The raw movies (*number* files, *size* GB) are held by *laboratory* at *institution*.
> Send a request to *contact address* to get a copy.

The smFRET community gives the same advice (Lerner et al. 2021). Deposit raw datasets in a
repository that gives a DOI. Keep the processed data connected to the raw data. Deposit FRET
results with the donor and acceptor intensities, the background intensities and the
correction factors. Store all the parameters and settings of the analysis. The subset below
keeps the raw and background trace layers for this reason.

## 2. Make the deposit files

```python
from pathlib import Path

from tether.project.export import export_molecule_table_csv, export_subset_tether
from tether.project.labels import CurationLabel, curation_labels

Path("deposit").mkdir(exist_ok=True)
labels = curation_labels("tutorial.tether")
accepted = [key for key, label in labels.items() if label == CurationLabel.ACCEPT]

subset = export_subset_tether(
    "tutorial.tether", "deposit/accepted.tether", molecule_keys=accepted, include_raw=True
)
table = export_molecule_table_csv("deposit/accepted.tether", "deposit/accepted.csv")
print(f"{subset.n_molecules} molecules in the subset, {table.n_molecules} rows in the table")
```

This writes four files into the `deposit` folder. They are `accepted.tether`, `accepted.csv`
and a provenance file beside each one. The table comes from the subset, so its rows are the
molecules of the subset. `include_raw=True` keeps the raw and background trace layers in the
subset. [Export results](export-results.md) gives the other options and the Deep-LASI trace
export.

A project imported from a Deep-LASI `.txt` file or a tMAVEN SMD file has no image crops. Tether
cannot export a subset of such a project. Deposit the full project file in its place.

## 3. Check what the files reveal

Do this check before you make a file public. A project file stores two kinds of personal or
local data:

- **The curator's name.** Each curation decision in `/labels/table` records your
  operating-system user name in the `labeler` field.
- **The movie paths.** Each row of `/movies/table` records the path of a source movie in the
  `uri` field. A movie that you selected in a file dialog has its full path, which can include
  your user name and your folder names.

These lines print both fields, and the SHA-256 checksum of each movie:

```python
import h5py


def show_identity(project_file):
    with h5py.File(project_file, "r") as f:
        for movie in f["/movies/table"][:]:
            path = movie["uri"].decode("utf-8")
            print("movie path:", path, "sha256:", movie["sha256"].decode("utf-8"))
        curators = {row["labeler"].decode("utf-8") for row in f["/labels/table"][:]}
        print("curators:", sorted(curators))


show_identity("tutorial.tether")
show_identity("deposit/accepted.tether")
```

The full project prints one line for each movie and then the curator names. The subset prints
no movie line, because a subset has no movie rows. It keeps the curator names, because it
keeps the curation history.

The other files carry neither field:

- The molecule table has a `source_filename` column. It holds the file name of the movie,
  without the folder.
- The Deep-LASI trace matrix holds only numbers.
- A provenance file names the source project by its file name only.

Tether has no option that removes the curator names from a project file. If the names must not
be public, deposit the molecule table and the trace matrix and leave out the project file. The
[privacy policy](../privacy.md#the-desktop-application) says what else Tether records on your
computer.

Copy the SHA-256 value of each movie into the README. A reader can then check that a deposited
movie is the movie that the project was made from.

## 4. Describe the data

Put a `README.md` file in the deposit folder. Start from this template and fill in each line:

```markdown
# <Title of the dataset>

## Sample
- Molecule and construct: <name, sequence or plasmid, dye pair and labeling positions>
- Buffer and temperature: <...>
- Surface and immobilization: <...>

## Instrument and acquisition
- Microscope and excitation: <TIRF or other, laser lines, power>
- Camera and detection: <model, emission filters, dual-view layout>
- Frame time and number of frames: <...>

## Analysis
- Tether version: <output of `tether --version`>
- Tether version DOI: <the DOI of that version on Zenodo>
- Correction factors: <how alpha and gamma were estimated, and whether a reported value uses them>
- Idealization: <backend, number of states, and the FRET values the model used>

## Files
- accepted.tether: the curated molecules, with raw and background traces
- accepted.csv: one row per molecule, with the columns that the Tether exports reference describes
- *.provenance.json: the Tether version and settings that made the file beside it
- movies/: the raw movies, with the SHA-256 of each one
- figures/: the exported figures

## License
<the license of this dataset>
```

The [sample Methods paragraph](../reproducibility.md#a-sample-methods-paragraph) shows how to
read the version, the settings and the correction stamps back from the project. Use the same
values in the README.

Link the readers of `accepted.csv` to the
[molecule-table reference](../reference/exports.md#1-molecule-table-csv). It describes each
column and its units. Link the readers who open the `.tether` file directly to the
[project file format](../reference/tether-format.md). It describes each group and field, and
it shows how to open the file with `h5py`.

## 5. Choose a repository and a license

Use a repository that gives the deposit a DOI. Zenodo is one example. Give the deposit an
explicit open license. Creative Commons Attribution 4.0 (CC BY 4.0) is one example. Any
repository that issues a DOI and any explicit open license will do.

Check the requirements of your funder and your journal first. If they name a repository or a
license, use that one.

## 6. Cite both ways

1. In the paper, cite the DOI of the dataset.
2. In the dataset description, cite the DOI of the Tether version that made the results.
   [Citing Tether](../citing.md#cite-tether) tells you how to find that DOI.
3. In the paper, also cite Tether and the methods that you used, as
   [Citing Tether](../citing.md) shows.

## 7. Write the data availability statement

Change each value in italics to your own:

> The single-molecule FRET movies, the Tether project file of the curated molecules, the
> molecule table and the provenance files are available in *repository* at *dataset DOI*,
> under the *license* license. The data were analyzed with Tether version *version*
> (*Tether version DOI*).

## References

- **Lerner et al. 2021.** Lerner E, Barth A, Hendrix J, et al. "FRET-based dynamic structural
  biology: Challenges, perspectives and an appeal for open-science practices." *eLife*
  10:e60416 (2021). [https://doi.org/10.7554/eLife.60416](https://doi.org/10.7554/eLife.60416)
