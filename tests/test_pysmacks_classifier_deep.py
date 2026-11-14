# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen training numbers and an accuracy floor for the deep classifier on public labels (#759).

ADR-0098. The labelled set is the traces file of the PySMACKS tutorial record (CC0-1.0; #720),
read through ``_openfret`` and split by trace as ``schema/deep_classifier_reference.json`` says
(``_deep_reference`` has the details). The module trains the classifier once, with the default
hyperparameters and the training seed of that file, on the train part, and scores the test part.

* **Self-drift (T1: A).** The loss of each epoch, the score of each test range, the three metrics
  and two sums of the train tensor equal ``tests/golden/deep/pysmacks_classifier.json`` within the
  relative limit ``self_drift.rtol`` of the reference file. The limit was measured in ten runs of
  the job on GitHub runners. ``scripts/update_deep_golden.py`` rewrites the golden file and prints
  the table a pull request must carry (the rule of #702).
* **Accuracy floor (T1: B).** The area under the ROC curve on the test part is at least
  ``accuracy.floor``. The floor sits below the scores of ten training seeds by a margin derived
  from their spread. A constant score, what a model that predicts the majority class gives, has an
  area of 0.5 and fails it; a test here shows that.

The job summary of ``deep.yml`` gets the scores, and the test writes them to the file that
``$TETHER_DEEP_SCORES_OUT`` names, for the ranker job to put beside the ranker's (T1: C).

The data are downloaded (``TETHER_REFERENCE_DOWNLOAD=1``, which ``deep.yml`` sets) and verified
against the SHA-256 of ``tests/reference_data/manifest.toml``. Where they are not here the tests
skip, and ``deep / smoke`` lists no such skip in ``tests/skip_budget.toml``, so in that job a skip
fails the job.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

import _deep_reference as ref
import _golden

pytestmark = pytest.mark.deep


@pytest.fixture(scope="module")
def reference() -> dict:
    return ref.load_reference()


@pytest.fixture(scope="module")
def samples() -> list[ref.Sample]:
    return ref.load_samples()


@pytest.fixture(scope="module")
def run(samples, reference) -> ref.Run:
    return ref.train_and_score(samples, reference)


def test_the_reference_file_names_the_downloaded_data(reference) -> None:
    from reference_data import fetch

    entry = fetch.load_manifest()[reference["dataset"]["id"]]
    assert reference["dataset"]["sha256"] == entry.sha256
    assert reference["dataset"]["file"] == entry.file


def test_the_labelled_ranges_are_the_ones_the_reference_counts(samples, reference) -> None:
    counts = reference["samples"]
    assert len({s.trace for s in samples}) == counts["n_traces"]
    assert len(samples) == counts["n_ranges"]
    assert sum(s.accept for s in samples) == counts["n_accept"]
    assert len({s.sample_id for s in samples}) == len(samples)


def test_the_split_rule_still_gives_the_committed_split(samples, reference) -> None:
    rule = reference["split"]
    derived = ref.derive_split(samples, test_fraction=rule["test_fraction"], seed=rule["seed"])
    assert derived == sorted(rule["test_traces"])
    train, test = ref.split(samples, rule["test_traces"])
    assert {s.trace for s in train}.isdisjoint({s.trace for s in test}), "a trace on both sides"
    assert (len(train), sum(s.accept for s in train)) == (
        rule["n_train_ranges"],
        rule["n_train_accept"],
    )
    assert (len(test), sum(s.accept for s in test)) == (
        rule["n_test_ranges"],
        rule["n_test_accept"],
    )


def test_the_training_numbers_equal_the_golden_file(run, reference) -> None:
    golden = _golden.load_golden(ref.GOLDEN_NAME, ref.GOLDEN_DIR)
    assert golden is not None, "no golden file: run scripts/update_deep_golden.py in the deep env"
    rtol = float(reference["self_drift"]["rtol"])
    diffs = _golden.compare(golden, run.quantities, rtol=rtol)
    assert not diffs, (
        f"the deep classifier drifted beyond rtol {rtol:g}; if the change is deliberate, run "
        "scripts/update_deep_golden.py and paste its table with a Cause: line\n"
        + _golden.diff_table({ref.GOLDEN_NAME: diffs})
    )


def test_the_deep_classifier_clears_the_accuracy_floor(run, reference) -> None:
    accuracy = reference["accuracy"]
    assert accuracy["metric"] == "roc_auc"
    floor = float(accuracy["floor"])
    result = run.scores
    table = ref.summary_table({"deep classifier (CNN + LSTM)": result}, floor=floor)
    ref.write_summary("Deep classifier on the PySMACKS tutorial labels (#759)", table)
    out = os.environ.get(ref.SCORES_OUT_ENV)
    if out:
        Path(out).write_text(json.dumps(ref.scores_to_json(result), indent=1), encoding="utf-8")
    print(f"\n[deep-reference]\n{table}")
    assert result.roc_auc >= floor, (
        f"ROC AUC {result.roc_auc:.4f} on the test part is below the floor {floor:.4f}"
    )


def test_a_model_that_predicts_the_majority_class_fails_the_floor(run, reference) -> None:
    """The negative control of the floor: constant scores, as the majority class gives them."""
    floor = float(reference["accuracy"]["floor"])
    test = list(run.test)
    majority = int(2 * sum(s.accept for s in test) > len(test))
    for constant in (0.0, 1.0, float(majority)):
        result = ref.score(test, np.full(len(test), constant))
        assert result.roc_auc == pytest.approx(0.5)
        assert result.roc_auc < floor
    # A model that ranks the reject ranges first is worse still.
    inverted = ref.score(test, 1.0 - np.asarray(run.scores.scores))
    assert inverted.roc_auc == pytest.approx(1.0 - run.scores.roc_auc)
    assert inverted.roc_auc < floor


def test_the_floor_was_derived_from_the_recorded_seeds(reference) -> None:
    """The floor and the limit are measured numbers, and the file keeps the measurement."""
    accuracy = reference["accuracy"]
    measured = accuracy["measured"]
    scores = np.asarray(measured["roc_auc"], dtype=np.float64)
    assert len(measured["seeds"]) == len(scores) == 10
    assert reference["training"]["seed"] in measured["seeds"]
    margin = float(measured["margin"])
    assert margin == pytest.approx(3.0 * float(np.std(scores, ddof=1)), abs=5e-5)
    assert float(accuracy["floor"]) <= float(scores.min()) - margin + 1e-12
    assert float(accuracy["floor"]) > 0.5, "a floor at or below 0.5 admits a constant score"
    drift = reference["self_drift"]["measured"]
    assert drift["runs"] == 10
    assert float(reference["self_drift"]["rtol"]) >= float(drift["largest_relative_difference"])
