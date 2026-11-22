# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the same held-out Deep-LASI traces with CPU and CUDA models (#760)."""

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from multiprocessing import get_context
from pathlib import Path

from perf_stage import _windows_job, measure_stage
from run_private_checks import (
    CUDA_UNAVAILABLE,
    DEEP_ARCHIVE_ID,
    DEEP_CHECK,
    DEEP_TRAINING_SEEDS,
    PrivateData,
)

from _deep_device import compare_device_scores
from _deep_lasi_reference import held_out_auc, load_reference, reference_subset, split_reference
from reference_data.fetch import load_manifest
from tether.ml.deep.model import train_classifier


def _measure_cpu(call):
    job = _windows_job() if os.name == "nt" else None
    return measure_stage(call, job=job)


def _measure(call, *, cuda):
    if cuda is not None:
        return measure_stage(call, cuda=cuda)
    # Each CPU peak starts in a process that has never loaded the archive or run another phase.
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
        return pool.submit(_measure_cpu, call).result()


def run_workload(dataset, *, cuda, training_seed: int) -> dict[str, float]:
    """Use the approved held-out rows and emit only aggregate numbers."""
    train, held = split_reference(dataset)
    training, held_out = reference_subset(dataset, train), reference_subset(dataset, held)
    scores = {}
    numbers = {"training_seed": training_seed}
    for device, backend in (("cpu", None), ("cuda", cuda)):
        model, training_metrics = _measure(
            partial(train_classifier, training, seed=training_seed, device=device), cuda=backend
        )
        scores[device], prediction_metrics = _measure(
            partial(model.predict_proba, held_out), cuda=backend
        )
        for phase, values in (("train", training_metrics), ("predict", prediction_metrics)):
            numbers[f"{device}_{phase}_s"] = values["time_s"]
            numbers[f"{device}_{phase}_peak_bytes"] = values["peak_bytes"]
        numbers[f"{device}_roc_auc"] = held_out_auc(dataset, held, scores[device])
        del model
    agreement = compare_device_scores(scores["cpu"], scores["cuda"], n_samples=len(held))
    return {**numbers, **agreement._asdict()}


def main(data: Path, output: Path, training_seed: int) -> int:
    if training_seed not in DEEP_TRAINING_SEEDS:
        raise ValueError("a calibration training seed must be an integer from 0 through 9")
    from torch import cuda

    if not cuda.is_available():
        return CUDA_UNAVAILABLE
    entry = load_manifest()["deeplasi-zenodo-classifier"]
    archive = PrivateData(data, DEEP_CHECK).path(DEEP_ARCHIVE_ID)
    if archive.resolve() != (data / entry.file).resolve():
        raise ValueError("the mapping must name the manifest's Deep-LASI classifier archive")
    dataset = load_reference(archive, sha256=entry.sha256)
    metrics = run_workload(dataset, cuda=cuda, training_seed=training_seed)
    output.write_text(json.dumps(metrics), encoding="utf-8")
    return 0


if __name__ == "__main__":
    data, output, seed = sys.argv[1:]
    sys.exit(main(Path(data), Path(output), int(seed)))
