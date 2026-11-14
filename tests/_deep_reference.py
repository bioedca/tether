# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The labelled public traces, the frozen split and the scores of the deep-classifier reference.

#759, ADR-0098. The labelled set is the traces file of the PySMACKS tutorial record (Zenodo
10.5281/zenodo.21793247, CC0-1.0; manifest id ``pysmacks-tutorial-traces``; #720). Each trace
label of that file, ``FRET``, ``DO`` or ``AO``, is a frame range ``[indmin, indmax)`` that a person
marked. One labelled range is one sample: its background-subtracted donor and acceptor under
donor excitation (``_openfret.OpenFretTrace.segment``), accept for ``FRET`` and reject for a
donor-only (``DO``) or an acceptor-only (``AO``) range. 123 traces carry 149 ranges: 52 accept and
97 reject.

The split, the training seed, the metric and the floor are in the committed file
:data:`REFERENCE` (``schema/deep_classifier_reference.json``). The split is by trace, never by
range, because 26 traces carry two ranges (a ``FRET`` range and then a ``DO`` or ``AO`` range of
the same molecule), and a split by range would put one molecule on both sides.

This module needs NumPy and the base-importable ``tether.ml`` modules only, so both the deep env
(``tests/test_pysmacks_classifier_deep.py``, ``scripts/update_deep_golden.py``) and the base env
(``scripts/compare_deep_with_ranker.py``, ``tests/test_deep_reference.py``) import it. It never imports torch or scikit-learn.
Like ``_golden`` and ``_openfret``, it is a plain helper module imported as the top-level
``_deep_reference``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "ACCEPT_LABELS",
    "DATASET",
    "GOLDEN_DIR",
    "GOLDEN_NAME",
    "REFERENCE",
    "REJECT_LABELS",
    "Run",
    "Sample",
    "Scores",
    "balanced_accuracy",
    "deep_dataset",
    "derive_split",
    "dump_golden",
    "labelled_samples",
    "load_reference",
    "load_samples",
    "ranker_features",
    "roc_auc",
    "score",
    "scores_from_json",
    "scores_to_json",
    "split",
    "summary_table",
    "train_and_score",
    "write_summary",
]

_TESTS = Path(__file__).resolve().parent
REFERENCE = _TESTS.parent / "schema" / "deep_classifier_reference.json"
GOLDEN_DIR = _TESTS / "golden" / "deep"
GOLDEN_NAME = "pysmacks_classifier"
DATASET = "pysmacks-tutorial-traces"

#: The PySMACKS labels a person sets on a trace range, mapped to the curation decision.
ACCEPT_LABELS = ("FRET",)
REJECT_LABELS = ("DO", "AO")
_LABEL_ORDER = ("FRET", "DO", "AO")

#: The layout version of the golden file (the layout of ``_golden``).
GOLDEN_FORMAT = 1
#: Environment variable that names a JSON file the deep test writes its test-set scores to,
#: for the ranker job of ``deep.yml`` (``scripts/compare_deep_with_ranker.py``) to read.
SCORES_OUT_ENV = "TETHER_DEEP_SCORES_OUT"


def load_reference(path: Path = REFERENCE) -> dict[str, Any]:
    """The committed split, seed, metric and floor (``schema/deep_classifier_reference.json``)."""
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Sample:
    """One labelled range of one trace: the donor and acceptor that the person labelled."""

    trace: str  # the trace UUID of the file (``UUID_v7``)
    label: str  # FRET, DO or AO
    donor: np.ndarray
    acceptor: np.ndarray

    @property
    def sample_id(self) -> str:
        return f"{self.trace}:{self.label}"

    @property
    def accept(self) -> int:
        return 1 if self.label in ACCEPT_LABELS else 0


def labelled_samples(traces: Sequence[Any]) -> list[Sample]:
    """Every labelled range of *traces* (``_openfret.OpenFretTrace``), in file and label order."""
    out: list[Sample] = []
    for trace in traces:
        for label in _LABEL_ORDER:
            if label in trace.labels:
                donor, acceptor = trace.segment(label)
                out.append(Sample(trace.uuid, label, donor, acceptor))
    return out


def _trace_codes(samples: Sequence[Sample]) -> tuple[list[str], np.ndarray]:
    """The labelled traces in first-seen order and the combination of labels each one carries."""
    labels: dict[str, list[str]] = {}
    for s in samples:
        labels.setdefault(s.trace, []).append(s.label)
    combos = sorted({"+".join(v) for v in labels.values()})
    codes = np.array([combos.index("+".join(v)) for v in labels.values()], dtype=np.int64)
    return list(labels), codes


def derive_split(samples: Sequence[Sample], *, test_fraction: float, seed: int) -> list[str]:
    """The test traces that the split rule of the reference file gives, sorted.

    The traces are stratified by the combination of labels they carry (``FRET``, ``DO``, ``AO``,
    ``FRET+DO``, ``FRET+AO``) and drawn with ``tether.ml.deep.train_val_split``. The committed
    file stores the result as a list, and a test checks that this rule still gives that list.
    """
    from tether.ml.deep.dataset import train_val_split

    traces, codes = _trace_codes(samples)
    _, test_idx = train_val_split(codes, val_fraction=test_fraction, seed=seed, stratify=True)
    return sorted(traces[i] for i in test_idx.tolist())


def split(
    samples: Sequence[Sample], test_traces: Sequence[str]
) -> tuple[list[Sample], list[Sample]]:
    """``(train, test)`` samples: the ranges of the traces in *test_traces* make the test part."""
    test = set(test_traces)
    unknown = test - {s.trace for s in samples}
    if unknown:
        raise ValueError(f"test traces not in the labelled set: {sorted(unknown)[:3]}")
    return [s for s in samples if s.trace not in test], [s for s in samples if s.trace in test]


def deep_dataset(samples: Sequence[Sample], *, window_length: int | None = None):
    """The ``DeepTraceDataset`` of *samples*, every sample at unit weight (all labels are human)."""
    from tether.ml.deep.dataset import DEFAULT_WINDOW_LENGTH, assemble_dataset

    y = np.array([s.accept for s in samples], dtype=np.int64)
    return assemble_dataset(
        [s.sample_id for s in samples],
        [s.donor for s in samples],
        [s.acceptor for s in samples],
        y,
        np.ones(len(samples), dtype=np.float64),
        window_length=DEFAULT_WINDOW_LENGTH if window_length is None else window_length,
    )


def ranker_features(samples: Sequence[Sample], window_length: int) -> np.ndarray:
    """The trace features of the gradient-boosting ranker over the frames the deep model reads.

    Both models see the same frames: the leading ``window_length`` frames of each range, which is
    the crop of ``assemble_dataset``. The spatial features need positions in a movie, which the
    file does not hold, so only ``TRACE_FEATURE_NAMES`` are used.
    """
    from tether.ml.features import compute_trace_features

    rows = [
        compute_trace_features(s.donor[:window_length], s.acceptor[:window_length]).as_vector()
        for s in samples
    ]
    return np.vstack(rows)


# --- metrics ---------------------------------------------------------------------------------


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks with ties given their mean rank (the ``rankdata`` "average" rule)."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.shape[0], dtype=np.float64)
    sorted_values = values[order]
    i = 0
    while i < values.shape[0]:
        j = i
        while j + 1 < values.shape[0] and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def roc_auc(y: Sequence[int], scores: Sequence[float]) -> float:
    """Area under the ROC curve: the chance that an accept range scores above a reject range.

    The Mann-Whitney form, with ties counted as one half. A constant score, which is what a
    model that predicts the majority class gives, has an area of exactly 0.5.
    """
    y_arr = np.asarray(y).astype(bool)
    s = np.asarray(scores, dtype=np.float64)
    if y_arr.shape != s.shape or s.ndim != 1:
        raise ValueError(f"y and scores must be equal-length 1-D arrays: {y_arr.shape} {s.shape}")
    if not np.all(np.isfinite(s)):
        raise ValueError("scores must be finite")
    n_pos = int(y_arr.sum())
    n_neg = int(y_arr.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        raise ValueError("the area under the ROC curve needs both classes")
    ranks = _average_ranks(s)
    return float((ranks[y_arr].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def balanced_accuracy(y: Sequence[int], scores: Sequence[float], threshold: float = 0.5) -> float:
    """The mean of the accept recall and the reject recall at ``score >= threshold``."""
    y_arr = np.asarray(y).astype(bool)
    pred = np.asarray(scores, dtype=np.float64) >= threshold
    return float(0.5 * (pred[y_arr].mean() + (~pred[~y_arr]).mean()))


@dataclass(frozen=True)
class Scores:
    """The test-set scores of one model, in sample order, and the three summary numbers."""

    sample_ids: tuple[str, ...]
    y: tuple[int, ...]
    scores: tuple[float, ...]
    roc_auc: float
    balanced_accuracy: float
    precision_at_k: float
    k: int


def score(samples: Sequence[Sample], scores: Sequence[float]) -> Scores:
    """The metrics of *scores* on *samples*; ``k`` of precision@k is the number of accept ranges."""
    from tether.ml.ranking import precision_at_k, rank_by_score

    ids = [s.sample_id for s in samples]
    y = [s.accept for s in samples]
    values = [float(v) for v in scores]
    ranked = rank_by_score(ids, np.asarray(values))
    by_id = dict(zip(ids, y, strict=True))
    relevance = np.array([by_id[m] for m in ranked.molecule_ids], dtype=bool)
    k = int(sum(y))
    return Scores(
        sample_ids=tuple(ids),
        y=tuple(y),
        scores=tuple(values),
        roc_auc=roc_auc(y, values),
        balanced_accuracy=balanced_accuracy(y, values),
        precision_at_k=precision_at_k(relevance, k),
        k=k,
    )


# --- one training run -----------------------------------------------------------------------


def load_samples() -> list[Sample]:
    """The labelled ranges of the downloaded file; a skip (from ``fetch``) when it is not here."""
    from _openfret import read_openfret
    from reference_data import fetch

    return labelled_samples(read_openfret(fetch.path_for(DATASET)).traces)


@dataclass(frozen=True)
class Run:
    """One training of the deep classifier on the train part, scored on the test part."""

    trained: Any
    train: tuple[Sample, ...]
    test: tuple[Sample, ...]
    scores: Scores
    quantities: dict[str, Any]


def train_and_score(
    samples: Sequence[Sample], reference: Mapping[str, Any], *, seed: int | None = None
) -> Run:
    """Train with the default hyperparameters on the train part and score the test part.

    Needs the deep env (torch loads inside ``train_classifier``). *seed* defaults to the training
    seed of the reference file. ``quantities`` are what the golden file freezes: the loss of each
    epoch, the score of each test range, the three metrics and two sums of the train tensor
    (which move when the data preparation changes even if no score does).
    """
    from tether.ml.deep.model import predict_proba, train_classifier

    train, test = split(samples, reference["split"]["test_traces"])
    data_train = deep_dataset(train)
    data_test = deep_dataset(test)
    seed = int(reference["training"]["seed"] if seed is None else seed)
    trained = train_classifier(data_train, seed=seed)
    proba = np.asarray(predict_proba(trained, data_test), dtype=np.float64)
    result = score(test, proba)
    x = data_train.X.astype(np.float64)
    quantities = {
        "n_train_ranges": len(train),
        "n_test_ranges": len(test),
        "train_input_sum": float(x.sum()),
        "train_input_sum_of_squares": float((x * x).sum()),
        "loss_history": [float(v) for v in trained.history],
        "test_scores": [float(v) for v in proba],
        "roc_auc": result.roc_auc,
        "balanced_accuracy": result.balanced_accuracy,
        "precision_at_k": result.precision_at_k,
    }
    return Run(trained, tuple(train), tuple(test), result, quantities)


# --- the golden file and the job summary ------------------------------------------------------


def dump_golden(quantities: Mapping[str, Any], directory: Path = GOLDEN_DIR) -> Path:
    """Write ``tests/golden/deep/pysmacks_classifier.json`` in the layout of ``_golden``."""
    doc = {
        "format": GOLDEN_FORMAT,
        "input": GOLDEN_NAME,
        "note": (
            "Regenerate with `python scripts/update_deep_golden.py` in the deep env; never edit "
            "by hand (#759, the rule of #702)."
        ),
        "quantities": {k: quantities[k] for k in sorted(quantities)},
    }
    path = directory / f"{GOLDEN_NAME}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=1, allow_nan=False)
    path.write_bytes((body + "\n").encode("utf-8"))
    return path


def summary_table(rows: Mapping[str, Scores | None], *, floor: float | None = None) -> str:
    """A Markdown table of the scores of each model on the same test split."""
    lines = [
        "| model | ROC AUC | balanced accuracy | precision@k | k | test ranges |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in rows.items():
        if s is None:
            lines.append(f"| {name} | not run in this job | | | | |")
            continue
        lines.append(
            f"| {name} | {s.roc_auc:.4f} | {s.balanced_accuracy:.4f} | {s.precision_at_k:.4f} "
            f"| {s.k} | {len(s.y)} |"
        )
    if floor is not None:
        lines.append("")
        lines.append(f"Floor of the deep classifier (ROC AUC, ADR-0098): {floor:.4f}.")
    return "\n".join(lines)


def write_summary(title: str, text: str) -> None:
    """Append *text* under *title* to the GitHub job summary when the job has one."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"### {title}\n\n{text}\n\n")


def scores_to_json(s: Scores) -> dict[str, Any]:
    return {
        "sample_ids": list(s.sample_ids),
        "y": list(s.y),
        "scores": list(s.scores),
        "roc_auc": s.roc_auc,
        "balanced_accuracy": s.balanced_accuracy,
        "precision_at_k": s.precision_at_k,
        "k": s.k,
    }


def scores_from_json(doc: Mapping[str, Any]) -> Scores:
    return Scores(
        sample_ids=tuple(doc["sample_ids"]),
        y=tuple(int(v) for v in doc["y"]),
        scores=tuple(float(v) for v in doc["scores"]),
        roc_auc=float(doc["roc_auc"]),
        balanced_accuracy=float(doc["balanced_accuracy"]),
        precision_at_k=float(doc["precision_at_k"]),
        k=int(doc["k"]),
    )
