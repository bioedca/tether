# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The test-only OpenFRET reader and the committed PySMACKS reference (#720). No network.

``tests/_openfret.py`` reads the PySMACKS tutorial traces in the weekly job; here it reads small
OpenFRET files written in the test, so that the required matrix holds its rules: only the
donor-excitation channels come back (PRD §2.2 rule 1), a label is a frame range inside its trace,
and a malformed trace is refused with its index, never read in part. The second half holds
``tests/fixtures/pysmacks_tutorial_reference.json`` to the shape the weekly comparison reads.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from _openfret import DONOR_EXCITATION_CHANNELS, read_openfret
from reference_data import fetch

REFERENCE = Path(__file__).parent / "fixtures" / "pysmacks_tutorial_reference.json"
ALL_CHANNELS = ("DD", "DA", "AA", "back_DD", "back_DA", "back_AA")


def _trace(n: int = 6, offset: int = 0, **metadata) -> dict:
    channels = [
        {
            "channel_type": name,
            "data": [100 * (k + 1) + offset + i for i in range(n)],
            "metadata": {},
        }
        for k, name in enumerate(ALL_CHANNELS)
    ]
    meta = {"molecule_id": str(offset), "UUID_v7": f"uuid-{offset}", **metadata}
    return {"channels": channels, "metadata": meta}


def _write(tmp_path: Path, traces: list[dict], *, as_zip: bool = False) -> Path:
    doc = {"title": "t", "traces": traces, "metadata": {"ALEX": "yes"}}
    path = tmp_path / "set.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    if not as_zip:
        return path
    archive = tmp_path / "set.json.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(path, "inner/set.json")
    return archive


@pytest.mark.parametrize("as_zip", [False, True])
def test_it_reads_the_donor_excitation_channels_and_the_labels(tmp_path, as_zip) -> None:
    traces = [
        _trace(),
        _trace(offset=7, FRET={"indmin": 1, "indmax": 4}, DO={"indmin": 4, "indmax": 6}),
    ]
    data = read_openfret(_write(tmp_path, traces, as_zip=as_zip))
    assert data.metadata == {"ALEX": "yes"}
    assert [t.uuid for t in data.traces] == ["uuid-0", "uuid-7"]
    assert [t.uuid for t in data.labelled("FRET")] == ["uuid-7"]
    second = data.traces[1]
    assert second.labels == {"FRET": (1, 4), "DO": (4, 6)}
    np.testing.assert_array_equal(second.donor, 107 + np.arange(6))
    np.testing.assert_array_equal(second.acceptor_background, 507 + np.arange(6))
    assert second.donor.dtype == np.float64


def test_it_returns_nothing_of_the_acceptor_excitation_frames(tmp_path) -> None:
    (trace,) = read_openfret(_write(tmp_path, [_trace()])).traces
    arrays = {name for name, value in vars(trace).items() if isinstance(value, np.ndarray)}
    assert arrays == {"donor", "acceptor", "donor_background", "acceptor_background"}
    assert set(DONOR_EXCITATION_CHANNELS) == {"DD", "DA", "back_DD", "back_DA"}


def test_a_segment_is_its_frame_range_minus_the_background_frame_by_frame(tmp_path) -> None:
    traces = [_trace(offset=3, FRET={"indmin": 2, "indmax": 5})]
    (trace,) = read_openfret(_write(tmp_path, traces)).traces
    donor, acceptor = trace.segment("FRET")
    # DD = 103 + i, back_DD = 403 + i; DA = 203 + i, back_DA = 503 + i, frames 2, 3, 4.
    np.testing.assert_array_equal(donor, [-300.0, -300.0, -300.0])
    np.testing.assert_array_equal(acceptor, [-300.0, -300.0, -300.0])
    with pytest.raises(KeyError):
        trace.segment("AO")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t["channels"].pop(4), r"trace 0: no channel \['back_DA'\]"),
        (lambda t: t["channels"][1]["data"].pop(), "trace 0: the channels differ in length"),
        (
            lambda t: t["channels"].append(dict(t["channels"][0])),
            "trace 0: two channels of type DD",
        ),
        (lambda t: t["metadata"].update(FRET={"indmin": 2, "indmax": 9}), "outside the trace"),
        (lambda t: t["metadata"].update(FRET={"indmin": 3, "indmax": 3}), "outside the trace"),
        (lambda t: t["metadata"].update(AO=[0, 2]), "label AO is not an"),
    ],
)
def test_a_malformed_trace_is_refused_with_its_index(tmp_path, change, message) -> None:
    trace = _trace()
    change(trace)
    with pytest.raises(ValueError, match=message):
        read_openfret(_write(tmp_path, [trace]))


def test_a_zip_with_two_json_members_needs_the_member_named(tmp_path) -> None:
    path = _write(tmp_path, [_trace()])
    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(path, "a.json")
        z.write(path, "b.json")
    with pytest.raises(ValueError, match="name the JSON member"):
        read_openfret(archive)
    assert len(read_openfret(archive, member="b.json").traces) == 1


# --- The committed PySMACKS reference ----------------------------------------------------------


def test_the_reference_names_its_source_and_the_versions_that_made_it() -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    source = fetch.load_manifest()["pysmacks-tutorial-traces"]
    assert reference["format"] == "pysmacks-reference"
    assert reference["source"]["sha256"] == source.sha256
    assert reference["source"]["doi"] == source.source.removeprefix("doi:")
    assert reference["source"]["license"] == source.license
    packages = reference["generator"]["packages"]
    assert packages["PySMACKS"] == "1.0.0b4"
    assert {"openfret", "hmmlearn", "numpy"} <= set(packages)


def test_the_reference_histogram_and_hmm_are_whole() -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    hist = reference["histogram"]
    assert len(hist["counts"]) == hist["bins"]
    assert sum(hist["counts"]) + hist["n_outside_range"] == hist["n_frames"]
    assert hist["n_frames"] == sum(t["frames"] for t in reference["traces"])
    assert len(reference["traces"]) == 52
    hmm = reference["hmm"]
    matrix = np.asarray(hmm["transition_matrix_per_frame"])
    assert matrix.shape == (hmm["nstates"], hmm["nstates"])
    np.testing.assert_allclose(matrix.sum(axis=1), 1.0)
    assert hmm["state_apparent_e"] == sorted(hmm["state_apparent_e"])
