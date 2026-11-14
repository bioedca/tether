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

Native extraction has three spot detectors, and they come from different papers. The one
that ran is the `detection_mode` attribute of `/settings/extraction` in your `.tether`
project (see [the parameter reference](reference/parameters.md#param-detection-mode)). It is
`wavelet` unless you chose another mode or a Deep-LASI `.tdat` file supplied one.

| What you ran | Cite |
|---|---|
| Photobleaching (bleach-step) detection, Tether's native reimplementation of tMAVEN's Bayesian single-step model | Verma 2024 |
| Leakage (α) and γ (detection-efficiency and quantum-yield ratio) corrections, and the gamma-corrected FRET efficiency | Lee 2005; Roy 2008; McCann 2010; Hellenkamp 2018 |
| Native movie-to-trace extraction and channel registration, in any detection mode | Wanninger 2023 |
| Spot detection in `wavelet` mode (the default): the à trous wavelet detector and its centroid localization | Olivo-Marin 2002; Izeddin 2012 |
| Spot detection in `intensity` or `bandpass` mode: the Crocker-Grier band-pass filter | Crocker 1996 |
| The deep trace classifier (GPU add-on): its CNN/LSTM model, training and fine-tuning | Thomsen 2020; Wanninger 2023; Li 2020; Zhang 2025 |
| Saving or exporting the `.tether` / SMD project format | Greenfeld 2015 |

### References

- **Crocker 1996.** Crocker JC, Grier DG. "Methods of digital video microscopy for colloidal
  studies." *Journal of Colloid and Interface Science* 179(1):298–310 (1996).
  [https://doi.org/10.1006/jcis.1996.0217](https://doi.org/10.1006/jcis.1996.0217)
- **Olivo-Marin 2002.** Olivo-Marin J-C. "Extraction of spots in biological images using
  multiscale products." *Pattern Recognition* 35(9):1989–1996 (2002).
  [https://doi.org/10.1016/S0031-3203(01)00127-3](https://doi.org/10.1016/S0031-3203%2801%2900127-3)
- **Lee 2005.** Lee NK, Kapanidis AN, Wang Y, Michalet X, Mukhopadhyay J, Ebright RH, Weiss S.
  "Accurate FRET measurements within single diffusing biomolecules using alternating-laser
  excitation." *Biophysical Journal* 88(4):2939–2953 (2005).
  [https://doi.org/10.1529/biophysj.104.054114](https://doi.org/10.1529/biophysj.104.054114)
- **Roy 2008.** Roy R, Hohng S, Ha T. "A practical guide to single-molecule FRET." *Nature
  Methods* 5(6):507–516 (2008).
  [https://doi.org/10.1038/nmeth.1208](https://doi.org/10.1038/nmeth.1208)
- **Bronson 2009.** Bronson JE, Fei J, Hofman JM, Gonzalez RL Jr, Wiggins CH. "Learning
  rates and states from biophysical time series: a Bayesian approach to model selection and
  single-molecule FRET data." *Biophysical Journal* 97(12):3196–3205 (2009).
  [https://doi.org/10.1016/j.bpj.2009.09.031](https://doi.org/10.1016/j.bpj.2009.09.031)
- **McCann 2010.** McCann JJ, Choi UB, Zheng L, Weninger K, Bowen ME. "Optimizing methods to
  recover absolute FRET efficiency from immobilized single molecules." *Biophysical Journal*
  99(3):961–970 (2010).
  [https://doi.org/10.1016/j.bpj.2010.04.063](https://doi.org/10.1016/j.bpj.2010.04.063)
- **Izeddin 2012.** Izeddin I, Boulanger J, Racine V, Specht CG, Kechkar A, Nair D,
  Triller A, Choquet D, Dahan M, Sibarita JB. "Wavelet analysis for single molecule
  localization microscopy." *Optics Express* 20(3):2081–2095 (2012).
  [https://doi.org/10.1364/OE.20.002081](https://doi.org/10.1364/OE.20.002081)
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
- **Li 2020.** Li J, Zhang L, Johnson-Buck A, Walter NG. "Automatic classification and
  segmentation of single-molecule fluorescence time traces with deep learning." *Nature
  Communications* 11:5833 (2020).
  [https://doi.org/10.1038/s41467-020-19673-1](https://doi.org/10.1038/s41467-020-19673-1)
- **Wanninger 2023.** Wanninger S, Asadiatouei P, Bohlen J, Salem CB, Tinnefeld P, Ploetz
  E, Lamb DC. "Deep-LASI: deep-learning assisted, single-molecule imaging analysis of
  multi-color DNA origami structures." *Nature Communications* 14:6564 (2023).
  [https://doi.org/10.1038/s41467-023-42272-9](https://doi.org/10.1038/s41467-023-42272-9)
- **Verma 2024.** Verma AR, Ray KK, Bodick M, Kinz-Thompson CD, Gonzalez RL Jr. "Increasing
  the accuracy of single-molecule data analysis using tMAVEN." *Biophysical Journal*
  123(17):2765–2780 (2024).
  [https://doi.org/10.1016/j.bpj.2024.01.022](https://doi.org/10.1016/j.bpj.2024.01.022)
- **Zhang 2025.** Zhang L, Li J, Walter NG. "Pretrained deep neural network Kin-SiM for
  single-molecule FRET trace idealization." *The Journal of Physical Chemistry B*
  129(4):1167–1175 (2025).
  [https://doi.org/10.1021/acs.jpcb.4c05276](https://doi.org/10.1021/acs.jpcb.4c05276)

These are the papers the tables above cite. The `references:` list in `CITATION.cff` holds
them all, plus works Tether credits that a citing user does not need here (MASH-FRET and the
kinSoftChallenge benchmark and dataset used in Tether's own tests). Licensing credit for the
projects Tether builds on is in
[`NOTICE`](https://github.com/bioedca/tether/blob/main/NOTICE).

## Reporting a Tether analysis

Quote the version that produced the results you report, not the version you have installed
today. When you run an analysis that you will publish, write down the output of
`tether --version` (see the [command-line reference](cli.md)). Not every step records a
version in the project, so your own note is the dependable record.

Cite Tether with the concept DOI in [Cite Tether](#cite-tether). If one exact release
matters, cite the version DOI of that release instead. Then cite the papers in
[Also cite the methods you used](#also-cite-the-methods-you-used) for each step that you ran.

[Reproducibility and reporting](reproducibility.md) is the one page for the rest. It lists
[the version and settings stamps](reproducibility.md#what-is-recorded) on each pipeline
stage and export, and [the steps and settings with no stamp](reproducibility.md#what-is-not-recorded).
It gives [a script that reads the stamps back](reproducibility.md#how-to-read-it-back) and
[a sample Methods paragraph](reproducibility.md#a-sample-methods-paragraph). Start from that
paragraph. It says which correction factors were estimated, and that the trajectories were
idealized on the apparent FRET efficiency.
