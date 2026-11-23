# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The helpers of the deep-classifier reference, on synthetic ranges (#759, ADR-0098).

The deep tier and the ranker job read the public PySMACKS labels; this module runs in the required
matrix without them. It checks the metrics against hand-computed values, the trace-level split,
the committed reference file, the golden-file layout and the ranker comparison of
``scripts/compare_deep_with_ranker.py``. No torch: ``_deep_reference`` imports none.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import types
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


# --- the hardening of #1226 -----------------------------------------------------------------


def _synthetic_reference(samples: list[ref.Sample]) -> dict:
    return {
        "split": {"test_traces": ref.derive_split(samples, test_fraction=0.3, seed=759)},
        "training": {"window_length": 64, "seed": 0},
        "accuracy": {"floor": 0.9},
    }


def test_the_deep_row_is_computed_from_the_stored_scores() -> None:
    compare = _load_script("compare_deep_with_ranker")
    samples = ref.labelled_samples(_synthetic_traces(40))
    reference = _synthetic_reference(samples)
    _, test = ref.split(samples, reference["split"]["test_traces"])
    deep = ref.score(test, [0.9 if s.accept else 0.1 for s in test])
    # Metrics stored beside the scores are not what the table shows.
    tampered = dataclasses.replace(deep, roc_auc=0.1, balanced_accuracy=0.1, precision_at_k=0.1)
    table = compare.report(reference, samples, tampered)
    assert table.splitlines()[2].startswith(f"| {compare.DEEP} | 1.0000 | 1.0000 | 1.0000 |")
    # Every matched range needs one finite score.
    for scores in ((), deep.scores[:-1], (float("nan"), *deep.scores[1:])):
        with pytest.raises(ValueError, match="scores for"):
            compare.report(reference, samples, dataclasses.replace(deep, scores=scores))


def test_the_ranker_comparison_fails_when_required_scores_are_absent(tmp_path: Path) -> None:
    compare = _load_script("compare_deep_with_ranker")
    absent = tmp_path / "deep-scores.json"
    assert compare.main(["--require-deep-scores"]) == 1
    assert compare.main(["--deep-scores", str(absent), "--require-deep-scores"]) == 1


def _workflow() -> dict:
    import yaml

    return yaml.safe_load((_REPO / ".github" / "workflows" / "deep.yml").read_text("utf-8"))


def test_a_base_lock_change_runs_the_deep_workflow() -> None:
    doc = _workflow()
    triggers = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` as true
    assert "conda-lock.yml" in triggers["pull_request"]["paths"]


def test_the_ranker_job_requires_the_scores_when_smoke_passed() -> None:
    steps = _workflow()["jobs"]["ranker"]["steps"]
    download = next(s for s in steps if s.get("id") == "download")
    assert download["uses"].startswith("actions/download-artifact@")
    assert download["continue-on-error"] is True
    run = next(s for s in steps if "compare_deep_with_ranker.py" in s.get("run", ""))
    assert run["env"] == {
        "SMOKE_RESULT": "${{ needs.smoke.result }}",
        "DOWNLOAD_OUTCOME": "${{ steps.download.outcome }}",
    }
    script = run["run"]
    # The file is read only after a successful download; a passed smoke requires it.
    assert 'if [ "$DOWNLOAD_OUTCOME" = "success" ]; then\n  args+=(--deep-scores ' in script
    assert 'if [ "$SMOKE_RESULT" = "success" ]; then\n  args+=(--require-deep-scores)' in script


def test_the_golden_updater_refuses_check_together_with_measure(tmp_path: Path) -> None:
    update = _load_script("update_deep_golden")
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as stop:
        update.main(["--check", "--measure", str(out)])
    assert stop.value.code == 2
    assert not out.exists()


def _fake_model(monkeypatch, history: list[float]) -> list[tuple[int, ...]]:
    """Stand in for the torch module: record the input shapes, give the scores of the labels."""
    shapes: list[tuple[int, ...]] = []

    def train_classifier(data, *, seed):
        shapes.append(data.X.shape)
        return types.SimpleNamespace(history=history, hyperparameters={"seed": seed})

    def predict_proba(trained, data):
        shapes.append(data.X.shape)
        return np.where(np.asarray(data.y) == 1, 0.9, 0.1)

    module = types.ModuleType("tether.ml.deep.model")
    module.train_classifier = train_classifier
    module.predict_proba = predict_proba
    monkeypatch.setitem(sys.modules, "tether.ml.deep.model", module)
    return shapes


def test_both_datasets_use_the_window_of_the_reference(monkeypatch) -> None:
    samples = ref.labelled_samples(_synthetic_traces(40))
    reference = _synthetic_reference(samples)
    shapes = _fake_model(monkeypatch, [0.7, 0.5])
    run = ref.train_and_score(samples, reference)
    assert [shape[-1] for shape in shapes] == [64, 64]
    assert run.scores.roc_auc == 1.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_training_number_is_never_frozen(monkeypatch, bad: float) -> None:
    samples = ref.labelled_samples(_synthetic_traces(40))
    _fake_model(monkeypatch, [0.7, bad])
    with pytest.raises(ValueError, match="non-finite training numbers.*loss_history"):
        ref.train_and_score(samples, _synthetic_reference(samples))
    with pytest.raises(ValueError, match="train_input_sum$"):
        ref.require_finite({"train_input_sum": bad, "roc_auc": 0.9, "n_test_ranges": 3})
    ref.require_finite({"loss_history": [0.7, 0.5], "train_input_sum": 1.0})


def test_the_samples_are_read_from_the_dataset_the_caller_names(monkeypatch) -> None:
    from reference_data import fetch

    asked: list[str] = []

    def path_for(dataset_id: str, **_: object) -> Path:
        asked.append(dataset_id)
        raise LookupError(dataset_id)

    monkeypatch.setattr(fetch, "path_for", path_for)
    for dataset in ("another-id", ref.load_reference()["dataset"]["id"]):
        with pytest.raises(LookupError):
            ref.load_samples(dataset)
    assert asked == ["another-id", ref.DATASET]
