# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Workstation: the stored tMAVEN files of Campos-Chavez et al. 2024 (#757 step 4, ADR-0106).

The trace files, model files and model summaries of the paper are private (maintainer decision
S5: A) and reach this test only through neutral ids of the workstation runner (ADR-0071): every
id whose name starts with ``tbox-`` and whose role is ``smd``, ``model`` or ``summary``.

* Each trace file is matched to a sample by comparison with the Source Data (C3: A): the share of
  its molecules whose donor trace equals one of the sample's matrix columns.
* Each stored model, and each summary that names a model, is matched to the fit with its number
  of states and of traces, and compared with the published values and with the frozen fresh fit
  of the limits file. The published Source Data is the authority: a stored model that does not
  agree is reported, not failed (C1: C; the two wild-type models are reported, as is every model).

The result file gets numbers only, under the neutral ids. No file name, path or trace leaves.
"""

from __future__ import annotations

import functools

import numpy as np
import pytest

import _campos_chavez_hmm as cc
from reference_data import fetch

pytestmark = pytest.mark.workstation

SETTINGS = cc.load_settings()
FITS = SETTINGS["fits"]
KEYS = sorted(FITS)
PREFIX = "tbox-"


@functools.cache
def _archive():
    return fetch.path_for(SETTINGS["source_data"])


@functools.cache
def _published(key: str) -> dict:
    return cc.published(FITS[key], cc.read_book(_archive()), _archive())


def _ids(private_data, role: str) -> list[str]:
    return [i for i in private_data.ids if i.startswith(PREFIX) and role in private_data.roles(i)]


def _deviations(means: np.ndarray, counts: np.ndarray | None, key: str) -> dict[str, float]:
    order = np.argsort(means)
    pub = _published(key)
    fresh = cc.load_limits()["fresh"][key]
    out = {
        "means_max_abs_published": float(np.max(np.abs(means[order] - pub["means"]))),
        "means_max_abs_fresh": float(np.max(np.abs(means[order] - np.asarray(fresh["means"])))),
    }
    if counts is not None and "counts" in pub:
        c = counts[np.ix_(order, order)]
        out["counts_max_rel_published"] = float(np.max(np.abs(c - pub["counts"]) / pub["counts"]))
    return out


def test_each_stored_trace_file_is_matched_to_a_sample(private_data) -> None:
    from tether.idealize.smd import read_smd

    matrices = sorted({f["matrix"] for f in FITS.values()})
    for neutral_id in _ids(private_data, "smd"):
        raw = read_smd(private_data.path(neutral_id, "smd")).raw
        best = (0.0, -1)
        for index, name in enumerate(matrices):
            donor, _ = cc.read_traces(_archive(), name)
            if donor.shape[0] != raw.shape[1]:
                continue
            columns = {tuple(np.round(c, 3)) for c in donor.T}
            hits = sum(tuple(np.round(m[:, 0], 3)) in columns for m in raw)
            if hits / raw.shape[0] > best[0]:
                best = (hits / raw.shape[0], index)
        private_data.record(
            neutral_id,
            molecules=raw.shape[0],
            frames=raw.shape[1],
            matched_share=best[0],
            sample_index=best[1],
        )


def test_each_stored_model_is_compared_with_the_published_fit(private_data) -> None:
    from tether.idealize.driver import read_model

    for neutral_id in _ids(private_data, "model"):
        model = read_model(private_data.path(neutral_id, "model"))
        key = cc.fit_of(int(model.nstates), int(model.ran.size))
        numbers = {"nstates": int(model.nstates), "traces": int(model.ran.size)}
        if key is not None:
            numbers["fit_index"] = KEYS.index(key)
            numbers.update(_deviations(np.asarray(model.means), model.tmatrix, key))
        private_data.record(neutral_id, **numbers)


def test_each_stored_summary_is_compared_with_the_published_fit(private_data) -> None:
    for neutral_id in _ids(private_data, "summary"):
        text = private_data.path(neutral_id, "summary").read_text(
            encoding="utf-8", errors="replace"
        )
        got = cc.parse_summary(text)
        if got is None:
            private_data.record(neutral_id, parsed=0)
            continue
        numbers = {"parsed": 1, "nstates": got["means"].size, "traces": got["traces"]}
        key = cc.fit_of(got["means"].size, got["traces"])
        if key is not None:
            numbers["fit_index"] = KEYS.index(key)
            numbers.update(_deviations(got["means"], got["counts"], key))
        private_data.record(neutral_id, **numbers)
