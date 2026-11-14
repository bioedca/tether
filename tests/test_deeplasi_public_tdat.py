# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Import the public Deep-LASI ``.tdat`` files and compare with the paper (#716).

Zenodo record 7561162 (CC BY 4.0) holds the data of Wanninger *et al.*, *Nat Commun*
**14**:6564 (2023). Two of its archives are checked here, both too large for CI, so the
checks that open them are ``workstation`` tests (ADR-0071): the weekly runner finds the
archives in its data folder, and ``tests/reference_data/manifest.toml`` holds their size and
SHA-256 (tier ``workstation``).

* ``Figure_2.zip`` holds the **one-colour** set of the paper's Fig. 2. It is imported and its
  molecules are counted; a one-colour set has no FRET, so nothing more is compared.
* ``Figure_3.zip`` holds the **two-colour** set of the paper's Fig. 3. It is imported, and
  Tether's FRET states and dwell times are compared with the numbers of the paper
  (:data:`_deeplasi_public.PAPER`).

The workstation runner records the numbers in its result file. Only the molecule counts are
asserted, because they are the same file read again; a limit on the states or the dwell
times needs the maintainer's sign-off (#716 item 4) and is not set here.

The tests without the marker run in the required matrix. They check the two parts of the
test-only reader that a workstation run cannot check every week: the MCOS header shim, on the
committed version 4 fixture made into version 3, and the analysis, on synthetic traces with
a known answer (``tests/_synth.py``).

The neutral ids of the data folder are ``dlasi-02`` and ``dlasi-03``
(``docs/maintainers/workstation-runner.md``). A test name never holds a file or folder name
of the data folder, because the runner withholds a result that does.
"""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

import h5py
import numpy as np
import pytest

from _deeplasi_public import (
    PAPER,
    mcos_metadata_for_decoder,
    read_donor_excitation_traces,
    two_state_analysis,
)
from _synth import fret_traces
from reference_data.fetch import path_for
from tether.io import read_tdat
from tether.io.mcos import McosDecoder, object_reference_id

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tdat_coloc_slice.tdat"

#: neutral id of the data folder -> (manifest id, molecules the record or the paper states)
_SETS = {
    "dlasi-02": ("deeplasi-zenodo-fig2", PAPER["one_colour_traces"][0]),
    "dlasi-03": ("deeplasi-zenodo-fig3", PAPER["two_colour_traces"][0]),
}


def _metadata(file: h5py.File) -> tuple[np.ndarray, np.ndarray]:
    cells = np.asarray(file["#subsystem#"]["MCOS"][()]).reshape(-1)
    return cells, np.asarray(file[cells[0]][()]).reshape(-1).astype(np.uint8)


def _channel_colours(file: h5py.File, decoder: McosDecoder) -> list[object]:
    colours = []
    for ref in np.asarray(file["temp"]["Channel"][()]).reshape(-1):
        if ref:
            oid = object_reference_id(np.asarray(file[ref][()]).reshape(-1))
            colours.append(decoder.property_value(oid, "ChannelColor"))
    return colours


# --- required matrix: the reader and the analysis -------------------------------------------


def test_the_shim_makes_a_version_3_header_readable_by_the_product_decoder() -> None:
    with h5py.File(FIXTURE, "r") as file:
        cells, meta = _metadata(file)
        assert int(meta[:4].view("<u4")[0]) == 4, "the committed fixture is version 4"
        expected = _channel_colours(file, McosDecoder(file, cells, meta))
        assert {"G", "R"} <= set(expected)

        # The public files are version 3: the version word is 3 and bytes 32-39 are zero.
        v3 = meta.copy()
        v3[:4] = np.array([3, 0, 0, 0], dtype=np.uint8)
        v3[32:40] = 0
        with pytest.raises(ValueError, match="name table"):
            McosDecoder(file, cells, v3)
        shimmed = McosDecoder(file, cells, mcos_metadata_for_decoder(v3))
        assert _channel_colours(file, shimmed) == expected


def test_the_shim_leaves_a_version_4_header_unchanged() -> None:
    with h5py.File(FIXTURE, "r") as file:
        _cells, meta = _metadata(file)
    out = mcos_metadata_for_decoder(meta)
    assert out is not meta
    assert np.array_equal(out, meta)


def test_the_analysis_recovers_a_known_two_state_answer() -> None:
    stay_low, stay_high = 0.96, 0.94
    synth = fret_traces(
        80,
        600,
        seed=716,
        efficiency=(0.2, 0.8),
        switch_probability=np.array([[stay_low, 1 - stay_low], [1 - stay_high, stay_high]]),
        total_intensity=1000.0,
        noise_sigma=60.0,
    )
    dt = 0.1
    result = two_state_analysis(synth.donor, synth.acceptor, dt)

    assert result.n_kept == 80
    assert result.n_dynamic == 80
    assert result.fret == pytest.approx((0.2, 0.8), abs=0.02)
    # A geometric dwell of stay probability p has survival p**n, so its fitted time
    # constant is -dt / ln(p).
    for got, stay in zip(result.dwell_s, (stay_low, stay_high), strict=True):
        assert got == pytest.approx(-dt / math.log(stay), rel=0.1)
    assert min(result.n_dwells) > 500


# --- workstation: the public archives --------------------------------------------------------


def _extract_tdat(archive: Path, into: Path) -> Path:
    with zipfile.ZipFile(archive) as zf:
        members = [
            m
            for m in zf.namelist()
            if m.endswith(".tdat") and not m.startswith("__MACOSX/")  # macOS resource forks
        ]
        assert len(members) == 1, f"expected one .tdat in the archive, found {len(members)}"
        return Path(zf.extract(members[0], into))


def _archive(private_data, neutral_id: str) -> Path:
    manifest_id = _SETS[neutral_id][0]
    path = path_for(manifest_id)  # skips without the variable; checks the SHA-256
    assert path.resolve() == private_data.path(neutral_id).resolve(), (
        f"{neutral_id}: the mapping file and the manifest name different files"
    )
    return path


@pytest.mark.workstation
@pytest.mark.parametrize("neutral_id", sorted(_SETS))
def test_a_public_archive_imports_every_molecule(private_data, tmp_path, neutral_id) -> None:
    tdat = _extract_tdat(_archive(private_data, neutral_id), tmp_path)
    decoded = read_tdat(tdat)
    private_data.record(
        neutral_id,
        n_molecules=decoded.colocalization.n_molecules,
        n_channels=len(decoded.channels_with_data),
        threshold_decoded=int(decoded.detection.threshold is not None),
        movie_reference_decoded=int(decoded.movie_reference is not None),
    )
    expected = _SETS[neutral_id][1]
    assert decoded.colocalization.n_molecules == expected, (
        f"{neutral_id}: {decoded.colocalization.n_molecules} molecules, the source states "
        f"{expected}"
    )


@pytest.mark.workstation
def test_the_two_colour_set_states_and_dwell_times_against_the_paper(
    private_data, tmp_path
) -> None:
    neutral_id = "dlasi-03"
    traces = read_donor_excitation_traces(
        _extract_tdat(_archive(private_data, neutral_id), tmp_path)
    )
    assert traces.donor.shape[0] == PAPER["two_colour_traces"][0]
    result = two_state_analysis(traces.donor, traces.acceptor, traces.dt_s)

    paper = {key: value for key, (value, _where) in PAPER.items()}
    tether = {
        "apparent_fret_low": result.fret[0],
        "apparent_fret_high": result.fret[1],
        "dwell_low_s": result.dwell_s[0],
        "dwell_high_s": result.dwell_s[1],
    }
    private_data.record(
        neutral_id,
        dt_s=traces.dt_s,
        n_kept=result.n_kept,
        n_fit=result.n_fit,
        n_dynamic=result.n_dynamic,
        n_dynamic_paper_deeplasi=paper["two_colour_dynamic_deeplasi"],
        n_dynamic_paper_manual=paper["two_colour_dynamic_manual"],
        sigma_low=result.fret_sigma[0],
        sigma_high=result.fret_sigma[1],
        n_dwells_low=result.n_dwells[0],
        n_dwells_high=result.n_dwells[1],
        r_squared_low=result.fit_r_squared[0],
        r_squared_high=result.fit_r_squared[1],
        hmm_converged=int(result.hmm_converged),
        **tether,
        **{f"{key}_paper": paper[key] for key in tether},
        **{f"{key}_rel_diff": tether[key] / paper[key] - 1.0 for key in tether},
    )
    # No frozen limit (#716 item 4): only that the analysis found two states and fit both.
    assert result.fret[0] < result.fret[1]
    assert all(math.isfinite(value) for value in tether.values())
