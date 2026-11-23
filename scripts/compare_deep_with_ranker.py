# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Score the quality ranker on the split of the deep classifier and report both (#759, T1: C).

Usage, in the base env (the ranker needs scikit-learn, which the deep env does not have)::

    TETHER_REFERENCE_DOWNLOAD=1 python scripts/compare_deep_with_ranker.py \\
        --deep-scores deep-scores.json

ADR-0098. The gradient-boosting ranker (``tether.ml.gbranker``, PRD §7.5) trains on the train
part of ``schema/deep_classifier_reference.json``, with the trace features of the frames the deep
model reads (the leading window of each labelled range), and scores the test part. The script
prints a Markdown table of both models' ROC AUC, balanced accuracy and precision@k on that split
and appends it to the GitHub job summary. ``--deep-scores`` is the file that
``tests/test_pysmacks_classifier_deep.py`` writes in ``deep / smoke``; when it is absent the table
says the deep classifier did not run in this job.

**This check has no limit.** It fails only when the ranker cannot train or score, when the
deep scores are not for the same ranges with the same labels or do not hold one finite score per
range, or when ``--require-deep-scores`` is given and the file is absent. The deep row is computed
from the stored scores, never from the metrics stored beside them (#1226). ``deep.yml`` passes
``--require-deep-scores`` when ``deep / smoke`` passed, so a lost artifact cannot leave a table
with the ranker row only.

Exit status: ``0`` when the table was written, ``1`` when the ranker failed, the deep scores
are for another split or a required scores file is absent, ``2`` when the data set is not here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import _deep_reference  # noqa: E402

TITLE = "Deep classifier and quality ranker on the same split (#759)"
DEEP = "deep classifier (CNN + LSTM)"
RANKER = "quality ranker (gradient boosting)"


def ranker_scores(
    train: Sequence[_deep_reference.Sample],
    test: Sequence[_deep_reference.Sample],
    window_length: int,
) -> _deep_reference.Scores:
    """Train the ranker on *train* at its default hyperparameters and score *test*."""
    from tether.ml.features import TRACE_FEATURE_NAMES
    from tether.ml.gbranker import train_quality_ranker

    ranker = train_quality_ranker(
        _deep_reference.ranker_features(train, window_length),
        np.array([s.accept for s in train], dtype=bool),
        TRACE_FEATURE_NAMES,
    )
    scores = np.asarray(
        ranker.score(_deep_reference.ranker_features(test, window_length)), dtype=np.float64
    )
    if scores.shape != (len(test),) or not np.all(np.isfinite(scores)):
        raise ValueError(f"the ranker gave {scores.shape} scores for {len(test)} test ranges")
    return _deep_reference.score(test, scores)


def report(
    reference: dict,
    samples: Sequence[_deep_reference.Sample],
    deep: _deep_reference.Scores | None,
) -> str:
    """The Markdown table of both models on the test part of *reference*."""
    train, test = _deep_reference.split(samples, reference["split"]["test_traces"])
    ranker = ranker_scores(train, test, int(reference["training"]["window_length"]))
    if deep is not None:
        if (deep.sample_ids, deep.y) != (ranker.sample_ids, ranker.y):
            raise ValueError("the deep scores are not for the ranges of this split")
        values = np.asarray(deep.scores, dtype=np.float64)
        if values.shape != (len(test),) or not np.all(np.isfinite(values)):
            raise ValueError(
                f"the deep scores file holds {values.size} scores for {len(test)} test ranges, "
                "or a non-finite one"
            )
        deep = _deep_reference.score(test, values)
    return _deep_reference.summary_table(
        {DEEP: deep, RANKER: ranker}, floor=float(reference["accuracy"]["floor"])
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--deep-scores", type=Path, help="the scores that the deep test wrote (may be absent)"
    )
    parser.add_argument(
        "--require-deep-scores",
        action="store_true",
        help="exit 1 when the --deep-scores file is not here (deep / smoke passed)",
    )
    args = parser.parse_args(argv)

    present = args.deep_scores is not None and args.deep_scores.is_file()
    if args.require_deep_scores and not present:
        print("error: deep / smoke passed but its scores file is not here", file=sys.stderr)
        return 1
    deep = None
    if present:
        doc = json.loads(args.deep_scores.read_text(encoding="utf-8"))
        deep = _deep_reference.scores_from_json(doc)
    reference = _deep_reference.load_reference()
    try:
        samples = _deep_reference.load_samples(reference["dataset"]["id"])
    except BaseException as exc:  # pytest's Skipped is a BaseException
        if type(exc).__name__ != "Skipped":
            raise
        print(f"error: {exc} (set TETHER_REFERENCE_DOWNLOAD=1)", file=sys.stderr)
        return 2
    try:
        table = report(reference, samples, deep)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(table)
    _deep_reference.write_summary(TITLE, table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
