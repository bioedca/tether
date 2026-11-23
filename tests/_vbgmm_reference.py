# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""An independent vbGMM fit of the committed ``smd_4mol.hdf5`` fixture (#1302, ADR-0138).

The sidecar key ``vbgmm`` runs tMAVEN's plain variational Bayesian Gaussian mixture
(``modeler.run_vbgmm``) on the pooled FRET of every molecule. The live sidecar tests check
the dispatch, the state count, the shapes and the reproducibility of that fit. This module
supplies the *numbers* to compare it with, from an implementation that shares no code with
tMAVEN: scikit-learn's ``BayesianGaussianMixture`` with a finite Dirichlet weight prior.

* `pooled_fret` builds the data tMAVEN fits from the SMD file alone, with ``h5py`` and NumPy:
  the FRET efficiency ``A / (D + A)`` of the raw intensities in each molecule's
  ``pre_list:post_list`` window, the molecules in SMD order, and tMAVEN's clip (each value
  outside ``[-1, 2]`` or NaN replaced by a uniform draw on ``[-1, 2]`` from a generator seeded
  with 666, molecule by molecule). Those are tMAVEN's defaults at the pinned commit
  (``modeler.clip`` true, ``modeler.clip_min`` -1, ``modeler.clip_max`` 2, and the seed that
  ``modeler.clip_traces`` sets), which the sidecar runner does not change.
* `fit_reference` fits that data with scikit-learn and tMAVEN's own vbGMM priors, mapped onto
  scikit-learn's Normal-Wishart parameters (`SKLEARN_PRIORS`).
* ``tests/reference_data/smd_4mol_vbgmm_sklearn.json`` keeps the result, its provenance and
  the tolerances the maintainer approved. ``python tests/_vbgmm_reference.py write`` writes it
  again; ``tests/test_vbgmm_reference.py`` (base tier) checks that a fresh fit still gives the
  committed numbers, and ``tests/test_sidecar_driver.py`` compares the live tMAVEN fit with
  them. The sidecar environment has no scikit-learn, so only the base side fits.

Run it in the **base** environment: scikit-learn is in the base lock and not in the sidecar
lock. `pooled_fret`, `load_reference` and `disagreement` need only NumPy and h5py, so the
sidecar tests import this module too.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "smd_4mol.hdf5"
REFERENCE = HERE / "reference_data" / "smd_4mol_vbgmm_sklearn.json"
GROUP = "dataset"

#: tMAVEN's clip of the modeled data (``modeler.clip_traces`` at the pinned commit).
CLIP_LOW, CLIP_HIGH, CLIP_SEED = -1.0, 2.0, 666

#: tMAVEN's default vbGMM priors at the pinned commit (``modeler.vbgmm.prior.*``).
TMAVEN_PRIORS = {"beta": 0.25, "a": 0.1, "b": 0.01, "pi": 1.0}

#: The same priors in scikit-learn's terms. tMAVEN puts a Gamma(a, b) prior on each state's
#: precision and a Normal prior of precision ``beta * lambda`` on its mean (Bronson et al. 2009,
#: doi:10.1016/j.bpj.2009.09.031). In one dimension scikit-learn's Wishart prior with ``nu``
#: degrees of freedom and scale ``W0`` is Gamma(nu / 2, 1 / (2 W0)), and its
#: ``covariance_prior`` is ``W0^-1``; hence ``nu = 2a`` and ``covariance_prior = 2b``.
SKLEARN_PRIORS = {
    "weight_concentration_prior_type": "dirichlet_distribution",
    "weight_concentration_prior": TMAVEN_PRIORS["pi"],
    "mean_precision_prior": TMAVEN_PRIORS["beta"],
    "degrees_of_freedom_prior": 2.0 * TMAVEN_PRIORS["a"],
    "covariance_prior": 2.0 * TMAVEN_PRIORS["b"],
}

#: The optimizer settings of the reference fit. Many starts from k-means, a tolerance far below
#: the compared digits, and a fixed seed; nine different starts reached the same optimum.
SKLEARN_SETTINGS = {
    "init_params": "kmeans",
    "n_init": 10,
    "max_iter": 20000,
    "tol": 1e-12,
    "random_state": 1302,
}

#: The fit the sidecar test compares, the same as ``test_vbgmm_key_fits_the_requested_states_
#: reproducibly``: three states, one restart, seed 1076.
NSTATES = 3


class VbgmmDisagreement(AssertionError):
    """The tMAVEN fit is outside the approved tolerance of the independent reference."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pooled(path: Path, group: str) -> tuple[np.ndarray, np.ndarray]:
    import h5py

    with h5py.File(path, "r") as f:
        raw = np.asarray(f[f"{group}/data/raw"][()], dtype="float64")
        pre = np.asarray(f[f"{group}/tMAVEN/pre_list"][()]).astype("int64").reshape(-1)
        post = np.asarray(f[f"{group}/tMAVEN/post_list"][()]).astype("int64").reshape(-1)
    fret = raw[:, :, 1] / (raw.sum(axis=2) + 1e-300)
    rng = np.random.RandomState(CLIP_SEED)
    pieces, masks = [], []
    for i in range(raw.shape[0]):
        y = fret[i, pre[i] : post[i]].copy()
        bad = (y < CLIP_LOW) | (y > CLIP_HIGH) | np.isnan(y)
        y[bad] = rng.uniform(low=CLIP_LOW, high=CLIP_HIGH, size=int(bad.sum()))
        pieces.append(y)
        masks.append(bad)
    return np.concatenate(pieces), np.concatenate(masks)


def pooled_fret(path: Path = FIXTURE, group: str = GROUP) -> np.ndarray:
    """The 1-D FRET data tMAVEN's ``run_vbgmm`` fits, built without tMAVEN (see the module doc)."""
    return _pooled(path, group)[0]


def data_fingerprint(path: Path = FIXTURE, group: str = GROUP) -> dict:
    """What pins the data preparation, in a form that is the same on every platform.

    The values kept from the SMD are a correctly rounded division, so their bytes are hashed.
    The clip draws are not hashed: NumPy's ``uniform`` computes ``low + (high - low) * u``, which
    a compiler may fuse into one multiply-add on one platform and not on another. A hash of the
    whole array matched on Linux and not on the macOS runner, while the fit agreed to 1e-6
    there (#1302). Their count, positions and sum pin the draws instead.
    """
    x, clipped = _pooled(path, group)
    kept = np.ascontiguousarray(x[~clipped], dtype="<f8")
    return {
        "n_points": int(x.size),
        "n_clipped": int(clipped.sum()),
        "sha256_kept_float64_le": hashlib.sha256(kept.tobytes()).hexdigest(),
        "sha256_clipped_mask": hashlib.sha256(np.packbits(clipped).tobytes()).hexdigest(),
        "clipped_sum": float(x[clipped].sum()),
    }


def fit_reference(x: np.ndarray, nstates: int = NSTATES) -> dict:
    """Fit ``x`` with scikit-learn's variational Gaussian mixture; states sorted by mean.

    ``fractions`` is the share of the responsibilities in each state, which is how tMAVEN
    defines ``frac`` (``r.sum(0) / r.sum()``); scikit-learn's own ``weights_`` adds the
    Dirichlet prior and is kept apart. ``variances`` uses tMAVEN's definition
    ``1 / exp(E[ln lambda]) = b / exp(psi(a))`` and is reported, not gated.
    """
    import sklearn
    from scipy.special import digamma
    from sklearn.mixture import BayesianGaussianMixture

    params = dict(SKLEARN_PRIORS)
    params["covariance_prior"] = np.array([[params["covariance_prior"]]])
    model = BayesianGaussianMixture(
        n_components=nstates, covariance_type="full", **params, **SKLEARN_SETTINGS
    ).fit(x[:, None])
    order = np.argsort(model.means_.ravel())
    shape = model.degrees_of_freedom_ / 2.0
    rate = model.covariances_.ravel() * model.degrees_of_freedom_ / 2.0
    resp = model.predict_proba(x[:, None])
    return {
        "sklearn_version": sklearn.__version__,
        "converged": bool(model.converged_),
        "n_iter": int(model.n_iter_),
        "lower_bound": float(model.lower_bound_),
        "means": model.means_.ravel()[order].tolist(),
        "fractions": (resp.sum(axis=0) / resp.sum())[order].tolist(),
        "variances": (rate / np.exp(digamma(shape)))[order].tolist(),
        "sklearn_weights": model.weights_[order].tolist(),
    }


def load_reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def deviations(means, fractions, reference: dict) -> dict[str, float]:
    """Largest absolute differences from the reference, states sorted by mean."""
    order = np.argsort(np.asarray(means, dtype="float64"))
    fit = reference["fit"]
    return {
        "means_max_abs": float(
            np.max(np.abs(np.asarray(means, dtype="float64")[order] - np.asarray(fit["means"])))
        ),
        "fractions_max_abs": float(
            np.max(
                np.abs(np.asarray(fractions, dtype="float64")[order] - np.asarray(fit["fractions"]))
            )
        ),
    }


def disagreement(means, fractions, reference: dict) -> str | None:
    """A message naming each deviation over its tolerance, or ``None`` when all are inside."""
    found = deviations(means, fractions, reference)
    limits = reference["tolerance"]
    over = [f"{k} {v:.4g} > {limits[k]}" for k, v in found.items() if not v <= limits[k]]
    return "; ".join(over) or None


def build_reference() -> dict:
    """The reference file's content, fitted afresh; `write` keeps the committed tolerance."""
    import h5py
    import scipy

    x = pooled_fret()
    fit = fit_reference(x)
    return {
        "schema_version": 1,
        "issue": 1302,
        "adr": "0138",
        "description": (
            "Independent reference for tMAVEN's plain vbGMM (sidecar key vbgmm) on "
            "tests/fixtures/smd_4mol.hdf5: scikit-learn BayesianGaussianMixture with tMAVEN's "
            "vbGMM priors on the same pooled, clipped FRET. Written by "
            "`python tests/_vbgmm_reference.py write` in the base environment; checked by "
            "tests/test_vbgmm_reference.py and compared with the live fit by "
            "tests/test_sidecar_driver.py."
        ),
        "fixture": {
            "path": FIXTURE.relative_to(HERE.parent).as_posix(),
            "group": GROUP,
            "sha256": _sha256(FIXTURE),
        },
        "data": {
            "definition": (
                "FRET = raw[:, :, 1] / (raw.sum(2) + 1e-300) in each molecule's "
                "tMAVEN pre_list:post_list window, molecules in SMD order, concatenated; values "
                f"outside [{CLIP_LOW}, {CLIP_HIGH}] or NaN replaced by "
                f"numpy.random.RandomState({CLIP_SEED}).uniform draws, molecule by molecule"
            ),
            **data_fingerprint(),
        },
        "tmaven": {
            "commit": "10f4230",
            "method": "modeler.run_vbgmm",
            "priors": TMAVEN_PRIORS,
            "nstates": NSTATES,
            "nrestarts": 1,
            "seed": 1076,
        },
        "comparator": {
            "library": "scikit-learn",
            "class": "sklearn.mixture.BayesianGaussianMixture",
            "covariance_type": "full",
            "priors": SKLEARN_PRIORS,
            "settings": SKLEARN_SETTINGS,
            "numpy_version": np.__version__,
            "scipy_version": scipy.__version__,
            "h5py_version": h5py.__version__,
        },
        "fit": fit,
    }


def write() -> None:
    content = build_reference()
    old = load_reference() if REFERENCE.exists() else {}
    content["tolerance"] = old.get("tolerance", {})
    content["measured"] = old.get("measured", {})
    REFERENCE.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["write"]:
        write()
    elif sys.argv[1:] == ["show"]:
        print(json.dumps(build_reference()["fit"], indent=2))
    else:
        sys.stderr.write("usage: python tests/_vbgmm_reference.py write|show\n")
        sys.exit(2)
