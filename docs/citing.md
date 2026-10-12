# Citing Tether

**Who this page is for.** Anyone publishing results produced with Tether. It gives the
citation for Tether itself and, for each part of the pipeline you ran, the principal
upstream papers behind the methods that part implements, which you should cite alongside it.

## Cite Tether

Tether is archived on Zenodo. Its **concept DOI** always resolves to the newest archived
version:

> Campos-Chávez E, Mondragon A. *Tether*. Zenodo.
> [https://doi.org/10.5281/zenodo.23306397](https://doi.org/10.5281/zenodo.23306397)

The same metadata is in
[`CITATION.cff`](https://github.com/bioedca/tether/blob/main/CITATION.cff), which is what
GitHub's **Cite this repository** button on the repository page reads; it exports APA and
BibTeX.

Every release archived on Zenodo also has its own **version DOI**. To cite the exact
version you ran, open the concept DOI above, pick that version from the record's version
list, and cite the DOI shown on that version's record. Archiving started with
`v1.0.0-rc2`, so `v1.0.0-rc1` has no version DOI: if you ran it, cite the concept DOI and
give the version number in the text.

## Also cite the methods you used

Tether reimplements or wraps published methods, and the authors of those methods should be
credited for the parts you used. Find the rows that match your analysis.

### Idealization

The idealization backend is the `type` attribute of the model's group under
`/idealization/` in your `.tether` project. The group itself is named after the backend
unless a different name was chosen when the model was written. A model imported from
tMAVEN keeps tMAVEN's own label in `type` (for example `vb Consensus HMM` or `vbFRET`)
rather than one of Tether's six backend keys. `vbFRET` has its own row below; for any
other label, find the row that describes the same method and cite that.

| Backend (model type) | What it is | Cite |
|---|---|---|
| `vbconhmm` | Consensus (global) variational-Bayes HMM, run in the tMAVEN sidecar. Tether's default. | Bronson 2009; Verma 2024 |
| `vbconhmm_modelselection` | The same consensus VB-HMM, with the number of states chosen by model selection. | Bronson 2009; Verma 2024 |
| `ebhmm` | ebFRET empirical-Bayes HMM, run in the tMAVEN sidecar. | van de Meent 2014; Verma 2024 |
| `ebhmm_modelselection` | The same ebFRET HMM, with the number of states chosen by model selection. | van de Meent 2014; Verma 2024 |
| `vbgmm_vbhmm` | VB-HMM seeded from a variational-Bayes Gaussian mixture, with the number of states chosen by model selection, run in the tMAVEN sidecar. | Verma 2024 |
| `kmeans_vbhmm` | VB-HMM seeded from k-means, run in the tMAVEN sidecar. | Verma 2024 |
| `vbFRET` (imported only) | Per-trace variational-Bayes HMM (vbFRET), fitted in standalone tMAVEN and imported into the project. | Bronson 2009; Verma 2024 |

### The rest of the pipeline

| What you ran | Cite |
|---|---|
| Photobleaching (bleach-step) detection, Tether's native reimplementation of tMAVEN's Bayesian single-step model | Verma 2024 |
| Leakage (α) and γ (detection-efficiency and quantum-yield ratio) corrections, and the gamma-corrected FRET efficiency | McCann 2010; Hellenkamp 2018 |
| Native movie-to-trace extraction and channel registration | Wanninger 2023 |
| The deep trace classifier (GPU add-on) | Thomsen 2020; Wanninger 2023 |
| Saving or exporting the `.tether` / SMD project format | Greenfeld 2015 |

### References

- **Bronson 2009.** Bronson JE, Fei J, Hofman JM, Gonzalez RL Jr, Wiggins CH. "Learning
  rates and states from biophysical time series: a Bayesian approach to model selection and
  single-molecule FRET data." *Biophysical Journal* 97(12):3196–3205 (2009).
  [https://doi.org/10.1016/j.bpj.2009.09.031](https://doi.org/10.1016/j.bpj.2009.09.031)
- **McCann 2010.** McCann JJ, Choi UB, Zheng L, Weninger K, Bowen ME. "Optimizing methods to
  recover absolute FRET efficiency from immobilized single molecules." *Biophysical Journal*
  99(3):961–970 (2010).
  [https://doi.org/10.1016/j.bpj.2010.04.063](https://doi.org/10.1016/j.bpj.2010.04.063)
- **van de Meent 2014.** van de Meent J-W, Bronson JE, Wiggins CH, Gonzalez RL Jr.
  "Empirical Bayes methods enable advanced population-level analyses of single-molecule
  FRET experiments." *Biophysical Journal* 106(6):1327–1337 (2014).
  [https://doi.org/10.1016/j.bpj.2013.12.055](https://doi.org/10.1016/j.bpj.2013.12.055)
- **Greenfeld 2015.** Greenfeld M, van de Meent J-W, Pavlichin DS, Mabuchi H, Wiggins CH,
  Gonzalez RL Jr, Herschlag D. "Single-molecule dataset (SMD): a generalized storage format
  for raw and processed single-molecule data." *BMC Bioinformatics* 16:3 (2015).
  [https://doi.org/10.1186/s12859-014-0429-4](https://doi.org/10.1186/s12859-014-0429-4)
- **Hellenkamp 2018.** Hellenkamp B, Schmid S, Doroshenko O, et al. "Precision and accuracy
  of single-molecule FRET measurements—a multi-laboratory benchmark study." *Nature
  Methods* 15(9):669–676 (2018).
  [https://doi.org/10.1038/s41592-018-0085-0](https://doi.org/10.1038/s41592-018-0085-0)
- **Thomsen 2020.** Thomsen J, Sletfjerding MB, Jensen SB, et al. "DeepFRET, a software for
  rapid and automated single-molecule FRET data classification using deep learning."
  *eLife* 9:e60404 (2020).
  [https://doi.org/10.7554/eLife.60404](https://doi.org/10.7554/eLife.60404)
- **Wanninger 2023.** Wanninger S, Asadiatouei P, Bohlen J, Salem CB, Tinnefeld P, Ploetz
  E, Lamb DC. "Deep-LASI: deep-learning assisted, single-molecule imaging analysis of
  multi-color DNA origami structures." *Nature Communications* 14:6564 (2023).
  [https://doi.org/10.1038/s41467-023-42272-9](https://doi.org/10.1038/s41467-023-42272-9)
- **Verma 2024.** Verma AR, Ray KK, Bodick M, Kinz-Thompson CD, Gonzalez RL Jr. "Increasing
  the accuracy of single-molecule data analysis using tMAVEN." *Biophysical Journal*
  123(17):2765–2780 (2024).
  [https://doi.org/10.1016/j.bpj.2024.01.022](https://doi.org/10.1016/j.bpj.2024.01.022)

These are the papers the tables above cite. The `references:` list in `CITATION.cff` holds
them all, plus works Tether credits that a citing user does not need here (MASH-FRET and the
kinSoftChallenge benchmark and dataset used in Tether's own tests). Licensing credit for the
projects Tether builds on is in
[`NOTICE`](https://github.com/bioedca/tether/blob/main/NOTICE).

## Reporting a Tether analysis

Quote the version that actually produced the results you report, not the version you have
installed today. The dependable record is the one you keep yourself: when you run an
analysis you will publish, note the output of `tether --version` (see the
[command-line reference](cli.md)). Not every step leaves a version in the project.

A `.tether` project does record versions in several places, which help you reconstruct
them afterwards. A project created with one release can be corrected or idealized later
with another, so they can differ:

- the corrections stamp `app_version` on `/settings/leakage`, `/settings/gamma` and
  `/settings/correction`, rewritten each time they run, and each idealization run in
  Tether stamps its own `/idealization/<model>` group;
- a model imported from standalone tMAVEN (its group's `nstates_selected_by` attribute
  is `imported`) is stamped with the Tether version that *imported* it, not the software
  that ran the fit, so for those results report the tMAVEN version you ran;
- extraction stamps `/settings/extraction` only the *first* time a movie is extracted
  into the project. Movies added later under a newer release leave it unchanged, and
  Tether records no per-movie version, so if you extracted movies into one project with
  different releases, the stamp names only the first;
- the publication exports listed in the [exports reference](reference/exports.md) (the
  molecule-table CSV, the Deep-LASI text export, the subset `.tether` and figures) carry
  the version that wrote them in a `.provenance.json` sidecar. The SMD file handed off
  to standalone tMAVEN has none;
- the root `app_version` attribute records the version that *created* the project. It is
  optional, so a valid project can lack it.

Steps with **no** version stamp in the project include photobleach detection (rerunning
it overwrites the bleach frames and analysis windows without recording a version), and
ranking and classification runs. A saved classical ranker records the version it was
trained under (`tether_version` in its model manifest); the deep classifier records
none. For those steps, rely on the version you noted.

To read the stamps that exist, use any HDF5 viewer, or `h5py`:

```python
import h5py

with h5py.File("experiment.tether", "r") as f:
    print("created by:", f.attrs.get("app_version", "not recorded"))
    for name in ("extraction", "leakage", "gamma", "correction"):
        group = f.get(f"settings/{name}")
        if group is not None:
            print(name, group.attrs.get("app_version", "not recorded"))
    for name, group in f.get("idealization", {}).items():
        print("idealization", name, group.attrs.get("app_version", "not recorded"))
```

If the steps you report ran under different versions, name each one.

A sample methods sentence, to adapt to what you ran:

> Single-molecule FRET movies were processed with Tether version 1.0.0
> (https://doi.org/10.5281/zenodo.23306397). Traces were corrected for donor leakage (α)
> and for the detection-efficiency and quantum-yield ratio (γ) as described by McCann et al. (2010) and Hellenkamp et al.
> (2018), and idealized with the consensus variational-Bayes HMM (Bronson et al., 2009)
> run through tMAVEN (Verma et al., 2024).

Replace `1.0.0` with the version you noted when you ran the analysis (a stamp from the
steps you report will do where one exists), and swap the corrections and the idealization
backend for the rows above that match your analysis. If you need to
point at one exact release, use that version's own DOI from its Zenodo record instead of
the concept DOI.
