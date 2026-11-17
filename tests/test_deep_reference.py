# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The helpers of the deep-classifier reference, on synthetic ranges (#759, ADR-0098).

The deep tier and the ranker job read the public PySMACKS labels; this module runs in the required
matrix without them. It checks the metrics against hand-computed values, the trace-level split,
the committed reference file, the golden-file layout and the ranker comparison of
``scripts/compare_deep_with_ranker.py``. No torch: ``_deep_reference`` imports none.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

import _deep_reference as ref
import _golden

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"tether_{name}", _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Trace:
    """The two members of ``_openfret.OpenFretTrace`` that ``labelled_samples`` reads."""

    def __init__(self, uuid: str, ranges: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
        self.uuid = uuid
        self._ranges = ranges
        self.labels = {label: (0, len(d)) for label, (d, _) in ranges.items()}

    def segment(self, label: str) -> tuple[np.ndarray, np.ndarray]:
        return self._ranges[label]


def _synthetic_traces(n: int = 40, seed: int = 0) -> list[_Trace]:
    """FRET ranges (anticorrelated donor and acceptor), DO and AO ranges, some on one trace."""
    rng = np.random.default_rng(seed)
    traces = []
    for i in range(n):
        frames = int(rng.integers(60, 120))
        noise = rng.normal(0.0, 30.0, size=(2, frames))
        fret = (600.0 + noise[0], 900.0 + noise[1])
        donor_only = (1500.0 + noise[0], 60.0 + noise[1])
        acceptor_only = (15.0 + noise[0], 70.0 + noise[1])
        kind = i % 4
        ranges = {
            0: {"FRET": fret},
            1: {"DO": donor_only},
            2: {"AO": acceptor_only},
            3: {"FRET": fret, "DO": donor_only},
        }[kind]
        traces.append(_Trace(f"trace-{i:03d}", ranges))
    return traces


# --- metrics ---------------------------------------------------------------------------------


def test_the_roc_area_matches_hand_counted_pairs() -> None:
    # Accept scores 0.9, 0.4; reject 0.5, 0.1: three of the four pairs are ordered right.
    assert ref.roc_auc([1, 1, 0, 0], [0.9, 0.4, 0.5, 0.1]) == pytest.approx(0.75)
    assert ref.roc_auc([1, 0], [0.2, 0.8]) == 0.0
    assert ref.roc_auc([1, 0, 0], [0.5, 0.5, 0.1]) == pytest.approx(0.75)  # a tie counts 1/2


@pytest.mark.parametrize("constant", [0.0, 0.5, 1.0])
def test_a_constant_score_has_an_area_of_one_half(constant: float) -> None:
    y = [1, 0, 0, 1, 0, 0, 0]
    assert ref.roc_auc(y, [constant] * len(y)) == pytest.approx(0.5)


def test_the_roc_area_refuses_one_class_and_non_finite_scores() -> None:
    with pytest.raises(ValueError, match="both classes"):
        ref.roc_auc([1, 1], [0.1, 0.2])
    with pytest.raises(ValueError, match="finite"):
        ref.roc_auc([1, 0], [np.nan, 0.2])


def test_balanced_accuracy_is_the_mean_of_the_two_recalls() -> None:
    # Accept recall 1/2, reject recall 2/2.
    assert ref.balanced_accuracy([1, 1, 0, 0], [0.9, 0.2, 0.3, 0.1]) == pytest.approx(0.75)
    # The majority class (reject everything) gets 1/2 however unbalanced the set is.
    assert ref.balanced_accuracy([1, 0, 0, 0, 0], [0.0] * 5) == pytest.approx(0.5)


def test_score_uses_the_number_of_accept_ranges_as_k() -> None:
    samples = ref.labelled_samples(_synthetic_traces(8))
    perfect = ref.score(samples, [float(s.accept) for s in samples])
    assert perfect.k == sum(s.accept for s in samples)
    assert perfect.roc_auc == perfect.precision_at_k == perfect.balanced_accuracy == 1.0


# --- samples and split -----------------------------------------------------------------------


def test_each_labelled_range_is_one_sample_and_fret_is_the_only_accept() -> None:
    samples = ref.labelled_samples(_synthetic_traces(8))
    assert [s.sample_id for s in samples[:4]] == [
        "trace-000:FRET",
        "trace-001:DO",
        "trace-002:AO",
        "trace-003:FRET",
    ]
    assert samples[4].sample_id == "trace-003:DO"
    assert [s.accept for s in samples[:5]] == [1, 0, 0, 1, 0]


def test_the_split_is_by_trace_and_keeps_two_ranges_of_one_trace_together() -> None:
    samples = ref.labelled_samples(_synthetic_traces(40))
    test_traces = ref.derive_split(samples, test_fraction=0.3, seed=759)
    assert test_traces == ref.derive_split(samples, test_fraction=0.3, seed=759)
    assert test_traces != ref.derive_split(samples, test_fraction=0.3, seed=760)
    train, test = ref.split(samples, test_traces)
    assert {s.trace for s in train}.isdisjoint({s.trace for s in test})
    assert len(train) + len(test) == len(samples)
    # Each combination of labels is on both sides (stratified by trace).
    assert len(test_traces) == 12
    assert {s.label for s in test} == {"FRET", "DO", "AO"}


def test_a_split_that_names_an_unknown_trace_is_refused() -> None:
    samples = ref.labelled_samples(_synthetic_traces(4))
    with pytest.raises(ValueError, match="not in the labelled set"):
        ref.split(samples, ["no-such-trace"])


def test_the_committed_reference_file_is_complete_and_consistent() -> None:
    reference = ref.load_reference()
    rule = reference["split"]
    assert rule["unit"] == "trace"
    assert len(rule["test_traces"]) == len(set(rule["test_traces"]))
    assert rule["test_traces"] == sorted(rule["test_traces"])
    assert rule["n_train_ranges"] + rule["n_test_ranges"] == reference["samples"]["n_ranges"]
    assert rule["n_train_accept"] + rule["n_test_accept"] == reference["samples"]["n_accept"]
    assert reference["accuracy"]["metric"] == "roc_auc"
    assert 0.5 < float(reference["accuracy"]["floor"]) <= 1.0
    assert float(reference["self_drift"]["rtol"]) > 0.0
    assert reference["self_drift"]["golden"] == (
        (ref.GOLDEN_DIR / f"{ref.GOLDEN_NAME}.json").relative_to(_REPO).as_posix()
    )
    adr = _REPO / reference["adr"]
    assert adr.is_file(), adr
    from reference_data import fetch

    entry = fetch.load_manifest()[reference["dataset"]["id"]]
    assert (entry.sha256, entry.file, entry.license) == (
        reference["dataset"]["sha256"],
        reference["dataset"]["file"],
        reference["dataset"]["license"],
    )


def test_the_window_of_the_reference_is_the_default_of_the_classifier() -> None:
    from tether.ml.deep.dataset import DEFAULT_NORMALIZATION, DEFAULT_WINDOW_LENGTH

    training = ref.load_reference()["training"]
    assert training["window_length"] == DEFAULT_WINDOW_LENGTH
    assert training["normalization"] == DEFAULT_NORMALIZATION


# --- the golden file ------------------------------------------------------------------------


def test_the_golden_file_has_the_layout_of_the_self_drift_suite(tmp_path: Path) -> None:
    quantities = {"loss_history": [0.7, 0.5], "roc_auc": 0.97, "n_test_ranges": 45}
    path = ref.dump_golden(quantities, tmp_path)
    assert path.name == f"{ref.GOLDEN_NAME}.json"
    assert _golden.load_golden(ref.GOLDEN_NAME, tmp_path) == quantities
    assert path.read_bytes().endswith(b"}\n") and b"\r\n" not in path.read_bytes()


def test_the_committed_golden_file_holds_the_reference_quantities() -> None:
    golden = _golden.load_golden(ref.GOLDEN_NAME, ref.GOLDEN_DIR)
    assert golden is not None
    reference = ref.load_reference()
    assert golden["n_train_ranges"] == reference["split"]["n_train_ranges"]
    assert golden["n_test_ranges"] == len(golden["test_scores"])
    assert golden["n_test_ranges"] == reference["split"]["n_test_ranges"]
    assert len(golden["loss_history"]) > 1
    assert golden["roc_auc"] >= float(reference["accuracy"]["floor"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("key", ["loss_history", "train_input_sum"])
def test_a_non_finite_training_number_differs_from_the_golden_file(key: str, bad: float) -> None:
    golden = _golden.load_golden(ref.GOLDEN_NAME, ref.GOLDEN_DIR)
    assert golden is not None
    raw = dict(golden)
    raw[key] = [bad, *golden[key][1:]] if isinstance(golden[key], list) else bad
    diffs = _golden.compare(golden, ref.frozen_quantities(raw), rtol=1.0)
    assert [d.quantity for d in diffs] == [key]
    assert ref.frozen_quantities(golden) == golden


def test_a_changed_deep_golden_file_needs_the_old_new_table() -> None:
    check = _load_script("check_golden_table")
    changed = ["tests/golden/deep/pysmacks_classifier.json"]
    assert check.golden_files(changed) == changed
    assert check.problems("no table", changed)
    diffs = _golden.compare({"roc_auc": 0.97}, {"roc_auc": 0.96}, rtol=0.0)
    body = _golden.diff_table({ref.GOLDEN_NAME: diffs}) + "\n\nCause: a deliberate change.\n"
    assert check.problems(body, changed) == []


# --- the ranker comparison ------------------------------------------------------------------


def test_the_ranker_comparison_reports_both_models_on_the_same_ranges(capsys) -> None:
    compare = _load_script("compare_deep_with_ranker")
    samples = ref.labelled_samples(_synthetic_traces(40))
    test_traces = ref.derive_split(samples, test_fraction=0.3, seed=759)
    _, test = ref.split(samples, test_traces)
    reference = {
        "split": {"test_traces": test_traces},
        "training": {"window_length": 64},
        "accuracy": {"floor": 0.9},
    }
    deep = ref.score(test, [0.9 if s.accept else 0.1 for s in test])
    table = compare.report(reference, samples, deep)
    lines = table.splitlines()
    assert lines[0].startswith("| model | ROC AUC |")
    assert lines[2].startswith(f"| {compare.DEEP} | 1.0000 |")
    assert lines[3].startswith(f"| {compare.RANKER} |")
    # The synthetic classes are far apart, so the ranker separates them well too.
    assert float(lines[3].split("|")[2]) > 0.8
    # Scores for another split are refused rather than shown side by side.
    other = ref.score(test[:-1], [0.5] * (len(test) - 1))
    with pytest.raises(ValueError, match="not for the ranges of this split"):
        compare.report(reference, samples, other)


def test_the_ranker_comparison_says_when_the_deep_classifier_did_not_run() -> None:
    compare = _load_script("compare_deep_with_ranker")
    samples = ref.labelled_samples(_synthetic_traces(40))
    reference = {
        "split": {"test_traces": ref.derive_split(samples, test_fraction=0.3, seed=759)},
        "training": {"window_length": 64},
        "accuracy": {"floor": 0.9},
    }
    table = compare.report(reference, samples, None)
    assert f"| {compare.DEEP} | not run in this job |" in table


def test_the_deep_scores_round_trip_through_their_json_file(tmp_path: Path) -> None:
    samples = ref.labelled_samples(_synthetic_traces(8))
    result = ref.score(samples, np.linspace(0.0, 1.0, len(samples)))
    path = tmp_path / "deep-scores.json"
    path.write_text(json.dumps(ref.scores_to_json(result)), encoding="utf-8")
    assert ref.scores_from_json(json.loads(path.read_text(encoding="utf-8"))) == result


def test_the_summary_goes_to_the_job_summary_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    ref.write_summary("Title", "| a |")
    assert target.read_text(encoding="utf-8") == "### Title\n\n| a |\n\n"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY")
    ref.write_summary("Title", "| b |")  # no summary file outside a job: nothing happens
    assert "| b |" not in target.read_text(encoding="utf-8")
