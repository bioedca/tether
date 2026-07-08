# 0042 — A1 histogram model overlay: the idealized model's per-state Gaussians, not a fresh GMM fit

- **Status:** accepted
- **Date:** 2026-07-08
- **Deciders:** bioedca
- **PRD anchor:** §7.7, §10 (FR-ANALYZE), Appendix C (plot A1)
- **Milestone:** M6

## Context and problem statement

The A1 population histogram (Appendix C plot A1) overlays a model curve on the
apparent-E histogram. tMAVEN's `data_hist1d.py` draws this overlay in its `model_on`
branch: for each state `i` of the *current idealized model* it plots
`frac[i]·𝒩(x; mean[i], var[i])` (dashed) and their sum (solid). The plan posed the
open decision for Tether's A1: **fit a fresh Gaussian mixture** (e.g.
`sklearn.mixture.GaussianMixture`) to the pooled apparent-E, **or** derive the overlay
from the *already-persisted* idealization model (`means`/`var`/`frac` from
[ADR-0041](0041-population-model-and-ebfret.md))? The two look similar on screen but
mean different things: the first invents a second model independent of the
idealization the user actually ran; the second reflects that idealization.

## Decision drivers

- **Faithful tMAVEN parity** (PRD §10, §9 M6: "each of the seven … visually matches its
  tMAVEN counterpart"): A1's counterpart overlays the idealized model's state Gaussians.
- **Provenance-first** ([ADR-0001](0001-provenance-first-data-model.md)): the histogram
  and its overlay should describe the *same* model the rest of the suite reads (the TDP,
  dwell and rate plots all read the persisted `/idealization` model), not a second one.
- **Never fabricate** (PRD §8; the leakage-α/parity precedent): a fresh GMM would conjure
  a model divorced from the chosen model-selection; a model with no per-state spread
  should be *withheld*, not silently GMM-substituted.
- **Science-grounded**: the multi-state FRET efficiency histogram *is* a sum of per-state
  Gaussians whose parameters are the states' FRET efficiencies and populations
  [Gopich2010].
- **Reuse over reinvent** (PRD §4): deriving from the stored model needs only numpy — no
  `sklearn.mixture` in the analysis hot path, no base-env change.

## Considered options

- **A.** Overlay the idealized model's own per-state Gaussians — `frac[i]·𝒩(mean[i],
  var[i])` summed over states — read from the persisted `/idealization/{model}`.
- **B.** Fit a fresh `sklearn.mixture.GaussianMixture` to the pooled apparent-E and
  overlay that mixture.
- **C.** Ship both — model overlay by default, an optional fresh-GMM overlay.

## Decision outcome

Chosen option: **"A"**, because it reproduces tMAVEN's A1 overlay faithfully (the §10
parity clause), reflects the exact idealization the user ran — the histogram and its
overlay then describe *one* model, consistent with the TDP/dwell/rate views that read the
same `/idealization/{model}` — and needs no new library (numpy-only; no `sklearn.mixture`),
so the base env is untouched. The overlay is the sum
`total(x) = Σ_i frac[i]·(1/√(2π·var[i]))·exp(−(x−mean[i])²/(2·var[i]))`, evaluated on the
histogram's `value_range` at `DEFAULT_OVERLAY_POINTS = 1001` points — tMAVEN's fixed grid,
a rendering-fidelity constant like `DEFAULT_NBINS`, **not** a §11.2 science tunable. No
renormalization to the finite range is applied (exactly as tMAVEN plots it), so mass that
spills past an edge is honestly missing rather than rescaled.

Option **B** was rejected: a fresh GMM (1) diverges from the idealization the rest of the
suite uses, (2) invents a state count and positions independent of the model-selection the
user chose, and (3) misleads — the curve reads as authoritative while describing a
*different* model than the trace idealizations beneath it. When a stored model carries no
per-state variances/populations (a threshold/k-means model, or a legacy model written
before [ADR-0041](0041-population-model-and-ebfret.md)), the overlay is **withheld with a
clear error**, never GMM-substituted to paper over the gap. Option **C** adds a second,
easily-misread curve for no parity benefit; a fresh-GMM QC view, if ever wanted, belongs
with the raw-FRET-cloud pre-idealization QC (PRD §7.7), not layered on the A1 model plot.

### Consequences

- Good: A1's overlay matches its tMAVEN counterpart — a locking test replays
  `data_hist1d.py`'s `model_on` loop verbatim and asserts equality.
- Good: numpy-only; no `sklearn.mixture` on the analysis path, no conda-lock change.
- Good: the overlay is a self-describing frozen `ModelGaussianOverlay` (carries
  `means`/`variances`/`frac`/`value_range`/`model_name`), so the view is reproducible
  (NFR-REPRO); it is read-only, so `schema-guard` stays green.
- Trade-off: the overlay requires a population model (vbFRET / vbconhmm / ebFRET); a
  threshold/k-means model gets no overlay (documented; withheld, not fabricated).
- Follow-up: wiring the overlay into the GUI A1 dock (drawn atop the histogram) is a
  separate concern, kept off this headless PR so the computer-use GUI gate is not on the
  critical path.

## More information

- PRD §7.7, §10, Appendix C plot A1; §9 M6 seven-plot parity clause.
- tMAVEN: `tmaven/tmaven/controllers/analysis_plots/data_hist1d.py` (the `model_on` branch).
- Core: `tether.analysis.histogram.model_gaussian_overlay` (pure) /
  `population_model_gaussian_overlay` (store-level), reading
  `StoredIdealization.means`/`.variances`/`.frac`.
- Related: [ADR-0041](0041-population-model-and-ebfret.md) (persists the `var`/`frac`
  members this overlay reads), [ADR-0024](0024-one-click-idealization-store.md) (the
  `/idealization` model layout).
- Consensus: the multi-state FRET efficiency histogram is a sum of per-state Gaussians —
  [Gopich & Szabo 2010, *J. Phys. Chem. B*](https://consensus.app/papers/details/5824f04e5c9b5bf091a5de406e38dd4b/).
