# Deep trace classifier (optional GPU add-on)

Tether's M8 milestone adds a **deep** trace classifier — a 1-D CNN/LSTM that scores each
molecule accept/reject from its raw windowed donor/acceptor intensities, in the
DeepFRET/Deep-LASI lineage of deep-learning smFRET trace selection
([Thomsen 2020](#deepfret); [Wanninger 2023](#deeplasi); [Li 2020](#autosim)). It is a
**terminal optional add-on**: the classifier is a strict superset of the CPU app, and the
load-bearing acceptance clause (PRD §9 M8) is that *"a deep classifier trains on the shared
label store **and is optional — the CPU base app is unaffected**."*

This page is the setup and usage reference for that add-on: where PyTorch lives, how to build
the CPU and GPU environments, the training/inference API, and the two advisory CI legs. The
design rationale is recorded in ADR-0047 (*Deep-model optional stack + torch-free dataset
substrate*).

> **You do not need any of this to run Tether.** The base app never imports a deep-learning
> framework. Everything below is opt-in, for users who want to train or run the deep model on
> their own labels. If you only want the classical per-condition ranker (M5), stop here.

## Why a third isolated environment

PyTorch is heavy and GPU-specific, so putting it in the base lock would bloat every install
and break the "optional / CPU base unaffected" contract. Tether therefore keeps **three**
pin-and-held conda stacks fully isolated (ADR-0004, extended by ADR-0047) — never merged:

| Stack | Lock | Carries | Consumed by |
|-------|------|---------|-------------|
| **base** | `conda-lock.yml` | PySide6 / napari / pyqtgraph on the Numba-bounded NumPy | the whole app |
| **sidecar** | `sidecar/conda-lock.yml` | PyQt5 + trimmed tMAVEN on `numpy<2` | the vbFRET idealization sidecar (over IPC) |
| **deep** | `deep/conda-lock.yml` | `pytorch-cpu` + NumPy/SciPy/h5py | the optional deep classifier (this page) |

Keeping the three locks separate is a load-bearing invariant. The `deep/` deps must **never**
be merged into the base or sidecar environment, and a re-lock of any stack is a deliberate,
maintainer-approved PR — not a casual bump.

The split runs through the code, too. The framework-agnostic **dataset substrate**
(`tether.ml.deep.dataset`, pure NumPy) lives in the base env and is covered by the default
3-OS test matrix; **PyTorch is imported lazily, inside functions** in `tether.ml.deep.model`,
so merely importing the module in the base env pulls no framework. Only calling
`train_classifier` / `predict_proba` needs the `deep/` stack.

## The `deep/` environment

`deep/environment.yml` expresses the constraints; the committed, multi-platform
`deep/conda-lock.yml` pins the exact versions and is the single source of truth (the required
`conda-lock-verify` check restores it and never solves fresh). It is **CPU-only and
cross-platform** (`linux-64`, `osx-64`, `osx-arm64`, `win-64`):

- `python=3.12.*` (matches the base pin so the shared substrate behaves identically)
- `pytorch-cpu>=2.2,<3` — the conda-forge metapackage that pulls the CPU PyTorch build
  (`cpu_mkl*` on Linux/Windows, `cpu_generic*` on macOS) and **never** a CUDA variant, so the
  environment stays lean (~200 MB vs ~2 GB for a CUDA build)
- `numpy>=1.26,<2.2`, `scipy>=1.13`, `h5py>=3.11` — the empirically verified import footprint
  of the shared substrate (importing `tether.ml.deep.dataset` runs the `tether.ml` package
  init, which pulls the M5 ranker's `scipy.spatial.cKDTree` and, transitively, the `h5py`
  store bridge; scikit-learn / xgboost stay lazy and are not needed here)

### CPU setup (local)

Build the environment from the committed lock — restore it, never solve fresh — then install
Tether without dependencies (the lock is authoritative), exactly as CONTRIBUTING describes for
the base and sidecar stacks:

```bash
conda-lock install --name tether-deep deep/conda-lock.yml
conda activate tether-deep
pip install -e . --no-deps
```

Verify the two halves co-import:

```bash
python -c "import torch, tether.ml.deep.dataset; print(torch.__version__)"
```

### GPU (CUDA) setup

The CUDA build is the **documented, unpinned** path (ADR-0047): the committed
`deep/conda-lock.yml` stays CPU-only for reproducible CI, and a CUDA build is selected at
install time on a GPU box. The RTX-4060 is the reference GPU floor. In the deep environment
(or a fresh virtualenv), install a CUDA PyTorch wheel from the PyTorch index whose channel
matches your installed NVIDIA driver (see the PyTorch
[Start Locally](https://pytorch.org/get-started/locally/) selector):

```bash
# cu126 / cu124 / cu128 — pick the channel matching your NVIDIA driver
pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cu126
```

The CUDA wheel bundles the CUDA runtime, so only an NVIDIA **driver** is required — no
system CUDA toolkit. This install is intentionally outside pin-and-hold; it is only ever
exercised on the advisory GPU CI leg (below), never on a gating check.

## Training and inference

The classifier consumes the raw windowed traces (not the M5 nine-feature vector) as
fixed-length tensors. Assemble a `DeepTraceDataset` from a `.tether` project, train, and score:

```python
from tether.project.deep_dataset import build_deep_dataset
from tether.ml.deep.model import train_classifier

# Reuses the M5 ranker's exact labeled set + cold-start weights, joined per molecule_id to the
# same analysis-window-sliced traces the engineered features use. Requires /features/table
# (run tether.project.features.compute_features first). Read-only over the frozen store.
dataset = build_deep_dataset(project)          # intensity_quantity="corrected" by default

trained = train_classifier(dataset)            # lazily imports torch; needs the deep/ env
scores = trained.predict_proba(dataset)        # per-molecule accept probability, rows aligned
```

`build_deep_dataset` builds the framework-free substrate (base env); `train_classifier` and
`predict_proba` are the torch consumers (deep env). The returned `TrainedDeepClassifier` is a
frozen record carrying the trained model, the per-epoch weighted-loss `history`, the full
hyperparameter set, and the **preprocessing provenance** (channels, window length,
normalization, intensity quantity) — the reproducibility stamp (NFR-REPRO).

That provenance is also a **safety contract**: `predict_proba` rejects an inference dataset
whose preprocessing (channel order, window length, normalization, intensity quantity) differs
from what the model was trained on, because a mismatch keeps a compatible tensor *shape* yet
would produce silently invalid scores. Pass a bare `nn.Module` to opt out and own the contract
yourself.

### Preprocessing (the never-fabricate discipline)

Two substrate defaults matter for correctness (both are PRD §11.2 tunables, retuned to the
trained model):

- **`per_trace_total` normalization** divides donor **and** acceptor by one shared per-trace
  scale (the max total intensity `D + A`), preserving their relative magnitude and hence the
  apparent-FRET ratio `E = A/(D + A)` — the very signal the classifier learns from. An
  independent per-channel standardization would rescale the two channels differently and
  destroy that ratio.
- **Windowing** crops a long trace to its leading, information-rich (pre-bleach) frames and
  zero-pads a short one to `window_length` (default 500), with a boolean **`mask`** marking
  the real observed frames. Padding is masked, **never zero-filled as real data**, and the
  LSTM consumes packed sequences so padded frames are excluded — never treated as observations.

Labels are the shared-store **binary** accept(1)/reject(0) curation labels; the six-way
DeepFRET taxonomy awaits the M4 category codec (ADR-0023) and is a later extension.

## Continuous integration

The deep add-on is validated by two **non-required** legs, so it never gates a merge and the
base app stays torch-free. The base 3-OS `test` matrix deselects the `deep` marker
(`-m "not large and not sidecar and not deep"`), and `tests/test_marker_contract.py` keeps the
`tests/test_*_deep.py` collection glob in lockstep with that marker so a new deep test can
neither escape these legs nor redden the base matrix.

| Leg | Workflow | Trigger | Runner | What it exercises |
|-----|----------|---------|--------|-------------------|
| **CPU smoke** | `deep.yml` | pull_request (deep paths), nightly cron, manual | `ubuntu-latest` | restores `deep/conda-lock.yml`; trains a tiny CNN/LSTM on synthetic labeled traces and asserts it trains, predicts, and is reproducible on CPU |
| **GPU smoke** | `deep-gpu.yml` | `workflow_dispatch` **only** | self-hosted CUDA runner | installs the documented CUDA wheel at run time and exercises the `device="cuda"` training + inference path; self-skips off-GPU |

`deep.yml` is non-required by design (its `paths:` filter is safe precisely because it never
gates). `deep-gpu.yml` is **non-required by construction**: it is triggered only by
`workflow_dispatch` — never `pull_request` or `push` — so it never reports a status on a PR and
therefore can never become a required merge check. To run the GPU leg, dispatch it against a
self-hosted runner labelled `self-hosted` + your GPU label (default `gpu`), choosing the CUDA
channel (`cu126` / `cu124` / `cu128`) that matches the runner's driver.

## Status and scope

The deep classifier is the M8 add-on; the milestone also carries kinSoftChallenge kinetics
validation and a classical→deep fine-tuning path as separate PRs. The classifier is optional,
the base app is unaffected, the schema is untouched (the substrate is read-only over the
M0-frozen store), and every tunable is registered in PRD §11.2 — the single source of truth.

## References

Verified via [Consensus](https://consensus.app) during authoring:

- <a id="deepfret"></a>**[Thomsen 2020]** Thomsen et al. [*DeepFRET, a software for rapid and
  automated single-molecule FRET data classification using deep
  learning.*](https://consensus.app/papers/details/65a1d86b8a7c53ed977c66ce608454c1/?utm_source=claude_code)
  eLife. The CNN+LSTM classifier on non-ALEX donor/acceptor (DD + DA) intensities, >95%
  ground-truth accuracy — the architecture Tether's deep model follows.
- <a id="deeplasi"></a>**[Wanninger 2023]** Wanninger et al. [*Deep-LASI: deep-learning
  assisted, single-molecule imaging analysis of multi-color DNA origami
  structures.*](https://consensus.app/papers/details/8dd11471bad556eb9f912f4773d27859/?utm_source=claude_code)
  Nature Communications. Deep neural networks that sort traces, determine FRET correction
  factors, and classify state transitions from the same intensity traces.
- <a id="autosim"></a>**[Li 2020]** Li, Zhang, Johnson-Buck & Walter. [*Automatic
  classification and segmentation of single-molecule fluorescence time traces with deep
  learning.*](https://consensus.app/papers/details/fe82061aebe75735a241f9d2be7e71a5/?utm_source=claude_code)
  Nature Communications. AutoSiM — a CNN trace selector reaching ~90% concordance with manual
  selection, adaptable to new datasets by transfer learning.
- <a id="kinsim"></a>**[Zhang 2025]** Zhang et al. [*Pre-trained Deep Neural Network Kin-SiM
  for Single-Molecule FRET Trace
  Idealization.*](https://consensus.app/papers/details/79134c89f84f5f35b5c5badbbb0e27bb/?utm_source=claude_code)
  The Journal of Physical Chemistry B. An LSTM that idealizes FRET traces without Markovian
  assumptions — the LSTM-for-smFRET precedent behind the model's recurrent stage.

The project spec (`docs/PRD.md` §4.1, §7.5, §9 M8, §11.2) and ADR-0047 in the repository carry
the full design rationale and the enumerated tunables.
