# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The public RF2-ArfA tMAVEN data set of the tMAVEN authors (#708, ADR-0079).

Zenodo record 17246323 (doi:10.5281/zenodo.17246323, CC-BY-4.0) holds the tMAVEN SMD files of
[Michael2025] and the "vb Consensus HMM" models the authors fitted to them with tMAVEN. It is the
first tMAVEN parity evidence that Tether did not make itself. This module holds two of the four
checks of #708; ``test_rf2_arfa_sidecar.py`` holds the fit and the 2-D histogram, which need
tMAVEN.

1. **Import.** Tether reads each SMD file and each model file with no loss, and its two stores
   of them (the analysis-only ``.tether`` import and an ``/idealization`` entry) give the arrays
   back. The one loss is the documented one: the ``/traces`` layers are float32, so a float64
   intensity that is not a float32 value is rounded to the nearest one (at most ``2**-24``
   relative).
3. **Kinetics.** The rate matrix in each model file is the authors' rule applied to its
   transition matrix, ``k_ij = -ln(1 - p_ij) / tau`` per frame [Michael2025, Methods], so the
   rates Tether stores are the rates tMAVEN derived. From those rates, with the paper's
   photobleaching correction (subtract the Api137-bound rate), Tether reproduces Table 1 of
   [Michael2025]: 16 of its 21 numbers fall inside the stated error; the other 5 are pinned
   below as measured differences, not tolerated ones.

The committed extract (``tests/fixtures/rf2_core_wtprensec_*.hdf5``) runs in the required
matrix. The whole archive (11 SMD files, 9 models) runs in the weekly ``reference-data`` job,
under ``@pytest.mark.reference_download``, which also checks that the extract is what
``scripts/make_rf2_arfa_fixture.py`` says it is.

References
----------
[Michael2025] Michael N, Huang BY, Ray KK, Kinz-Thompson CD & Gonzalez RL. "A cascade of
    structural rearrangements positions peptide release factor 2 for polypeptide hydrolysis on
    the ribosome." Proc. Natl. Acad. Sci. USA 122(46):e2509408122 (2025).
    doi:10.1073/pnas.2509408122. Table 1 and Methods ("Accounting for Photobleaching Using
    Api137"; frames of 100 ms).
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import h5py
import numpy as np
import pytest

from reference_data import fetch
from tether.idealize import read_model, read_smd, states_from_idealized, write_smd
from tether.imaging.extract import read_molecules, read_traces
from tether.project.analysis_import import import_analysis_only_project
from tether.project.idealize import read_idealization, write_idealization_model

#: Seconds per frame: "a 100 ms acquisition time per frame" [Michael2025, Methods].
FRAME_S = 0.1
#: Archive members, below ``Data/Single molecule data/``.
_ROOT = "Data/Single molecule data/"
SMD_MEMBERS = (
    "RF2_core/Steady state/RF2_core_TC_smd.hdf5",
    "RF2_core/Steady state/RF2_core_mutnsEC_smd.hdf5",
    "RF2_core/Steady state/RF2_core_wtnsEC_smd.hdf5",
    "RF2_core/Steady state/RF2_core_wtprensEC_smd.hdf5",
    "RF2_core/Stopped flow/RF2_core_TC_smd.hdf5",
    "RF2_core/Stopped flow/RF2_core_wtnsEC_smd.hdf5",
    "RF2_dIII/RF2_dIII_TC_Api_smd.hdf5",
    "RF2_dIII/RF2_dIII_TC_smd.hdf5",
    "RF2_dIII/RF2_dIII_mutnsEC_smd.hdf5",
    "RF2_dIII/RF2_dIII_wtnsEC_smd.hdf5",
    "RF2_dIII/RF2_dIII_wtprensEC_smd.hdf5",
)
#: Each model member and the SMD member it was fitted to.
MODEL_MEMBERS = {
    "RF2_core/Steady state/HMMs/RF2_core_TC_smd_HMM.hdf5": SMD_MEMBERS[0],
    "RF2_core/Steady state/HMMs/RF2_core_mutnsEC_smd_HMM.hdf5": SMD_MEMBERS[1],
    "RF2_core/Steady state/HMMs/RF2_core_wtnsEC_smd_HMM.hdf5": SMD_MEMBERS[2],
    "RF2_core/Steady state/HMMs/RF2_core_wtprensEC_smd_HMM.hdf5": SMD_MEMBERS[3],
    "RF2_dIII/HMMs/RF2_dIII_TC_Api_smd_HMM.hdf5": SMD_MEMBERS[6],
    "RF2_dIII/HMMs/RF2_dIII_TC_smd_HMM.hdf5": SMD_MEMBERS[7],
    "RF2_dIII/HMMs/RF2_dIII_mutnsEC_smd_HMM.hdf5": SMD_MEMBERS[8],
    "RF2_dIII/HMMs/RF2_dIII_wtnsEC_smd_HMM.hdf5": SMD_MEMBERS[9],
    "RF2_dIII/HMMs/RF2_dIII_wtprensEC_smd_HMM.hdf5": SMD_MEMBERS[10],
}
#: The model members a test compares member by member with the file.
_MODEL_ARRAYS = ("mean", "var", "tmatrix", "rates", "pi", "frac", "idealized", "likelihood")
_PRIORS = ("a_prior", "b_prior", "beta_prior", "mu_prior", "pi_prior", "tm_prior")


# --- helpers -----------------------------------------------------------------------------------


def _smd_groups(path: Path) -> list[str]:
    with h5py.File(path, "r") as f:
        return sorted(name for name in f if f[name].attrs.get("format") in ("SMD", b"SMD"))


def _check_smd(path: Path, group: str, tmp_path: Path) -> None:
    """Tether reads one SMD group with no loss, writes it back, and imports it as a project."""
    with h5py.File(path, "r") as f:
        g = f[group]
        raw = g["data/raw"][()]
        source_index = g["data/source_index"][()]
        windows = {name: g["tMAVEN"][name][()] for name in ("classes", "pre_list", "post_list")}
    tmp_path.mkdir(parents=True, exist_ok=True)
    smd = read_smd(path, group)
    np.testing.assert_array_equal(smd.raw, raw, strict=True)
    np.testing.assert_array_equal(smd.source_index, source_index)
    np.testing.assert_array_equal(smd.classes, windows["classes"])
    np.testing.assert_array_equal(smd.pre_list, windows["pre_list"])
    np.testing.assert_array_equal(smd.post_list, windows["post_list"])

    # Tether's own SMD writer gives the same arrays back.
    again = read_smd(
        write_smd(
            tmp_path / "again.hdf5",
            smd.raw,
            source_names=smd.source_names,
            source_index=smd.source_index,
            classes=smd.classes,
            pre_list=smd.pre_list,
            post_list=smd.post_list,
        )
    )
    for name in ("raw", "source_index", "classes", "pre_list", "post_list"):
        np.testing.assert_array_equal(getattr(again, name), getattr(smd, name), strict=True)
    assert again.source_names == smd.source_names

    # The analysis-only import keeps every intensity, at float32, and every window.
    project = tmp_path / "rf2.tether"
    import_analysis_only_project(project, source=smd, source_name=path.name, overwrite=True)
    traces = read_traces(project)
    for channel, layer in enumerate(("donor_corrected", "acceptor_corrected")):
        stored = traces[layer]
        np.testing.assert_array_equal(stored, raw[:, :, channel].astype(np.float32))
        lost = np.abs(stored.astype(np.float64) - raw[:, :, channel])
        assert np.all(lost <= np.abs(raw[:, :, channel]) * 2.0**-24), layer
    molecules = read_molecules(project)
    np.testing.assert_array_equal(molecules["analysis_window"][:, 0], smd.pre_list)
    np.testing.assert_array_equal(molecules["analysis_window"][:, 1], smd.post_list)


def _check_model(model_path: Path, smd_path: Path, tmp_path: Path) -> None:
    """Tether reads one model file with no loss and stores it as an ``/idealization`` entry."""
    model = read_model(model_path)
    with h5py.File(model_path, "r") as f:
        g = f["model"]
        assert model.model_type == g.attrs["type"] == "vb Consensus HMM"
        assert g.attrs["rate_type"] == "Transition Matrix"
        assert model.nstates == int(g["nstates"][()])
        stored = {"pi": model.pi, "frac": model.frac, "rates": model.rates}
        stored |= {"mean": model.means, "var": model.variances, "tmatrix": model.tmatrix}
        stored |= {"idealized": model.idealized, "likelihood": model.likelihood}
        for name in _MODEL_ARRAYS:
            np.testing.assert_array_equal(stored[name], g[name][()], err_msg=name)
        np.testing.assert_array_equal(model.ran, g["ran"][()])
        assert model.elbo == g["likelihood"][()][-1, 0]
        assert model.priors is not None
        assert set(model.priors) == set(_PRIORS)
        for name in _PRIORS:
            np.testing.assert_array_equal(model.priors[name], g["priors"][name][()])
        chain = g["chain"][()]

    # The model's own Viterbi path is what Tether derives from its idealized levels.
    smd = read_smd(smd_path)
    paths = states_from_idealized(model.idealized, model.means)
    for i in model.ran:
        lo, hi = smd.pre_list[i], smd.post_list[i]
        np.testing.assert_array_equal(paths[i, lo:hi], chain[i, lo:hi])

    # Stored as an /idealization entry of the imported project and read back, unchanged.
    project = tmp_path / "rf2-model.tether"
    import_analysis_only_project(project, source=smd, source_name=smd_path.name, overwrite=True)
    molecules = read_molecules(project)
    rows = np.asarray(model.ran, dtype=np.int64)
    write_idealization_model(
        project,
        model_name="authors",
        model_type=model.model_type,
        nstates=model.nstates,
        dtype=model.dtype,
        means=model.means,
        variances=model.variances,
        tmatrix=model.tmatrix,
        norm_tmatrix=model.norm_tmatrix,
        elbo=model.elbo,
        idealized=model.idealized[rows],
        state_paths=paths[rows],
        molecule_keys=[k.decode() for k in molecules["molecule_key"][rows]],
        molecule_ids=[k.decode() for k in molecules["molecule_id"][rows]],
        input_hashes=["-"] * rows.size,
        intensity_quantity="corrected",
        selected_by="imported",
        elbo_by_nstates=None,
        app_version="test",
        created_utc=datetime.now(UTC).isoformat(),
        overwrite=False,
        rates=model.rates,
        pi=model.pi,
        frac=model.frac,
        priors=model.priors,
    )
    back = read_idealization(project, "authors")
    assert (back.model_type, back.nstates, back.elbo) == (
        model.model_type,
        model.nstates,
        model.elbo,
    )
    for name in ("means", "variances", "tmatrix", "rates", "pi", "frac"):
        np.testing.assert_array_equal(getattr(back, name), getattr(model, name), err_msg=name)
    np.testing.assert_array_equal(back.idealized, model.idealized[rows])
    np.testing.assert_array_equal(back.state_paths, paths[rows])
    assert back.priors is not None
    for name in _PRIORS:
        np.testing.assert_array_equal(back.priors[name], model.priors[name])


def rates_from_tmatrix(tmatrix: np.ndarray) -> np.ndarray:
    """``k_ij = -ln(1 - p_ij)`` per frame, ``p`` the row-normalized transition matrix.

    The rule of [Michael2025, Methods] ("These transition probabilities from the HMM transition
    matrices were used to calculate the rate between specific states"); the diagonal is zero.
    """
    p = np.asarray(tmatrix, dtype=np.float64)
    p = p / p.sum(axis=1, keepdims=True)
    k = -np.log1p(-p)
    np.fill_diagonal(k, 0.0)
    return k


def _load_script() -> ModuleType:
    """``scripts/make_rf2_arfa_fixture.py``, which is not a package module."""
    path = Path(__file__).parents[1] / "scripts" / "make_rf2_arfa_fixture.py"
    spec = importlib.util.spec_from_file_location("make_rf2_arfa_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _member(archive: zipfile.ZipFile, member: str, tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / Path(member).name
    target.write_bytes(archive.read(_ROOT + member))
    return target


# --- 1. Import, on the committed extract (required matrix) --------------------------------------


def test_the_extract_imports_with_no_loss(tmp_path: Path) -> None:
    smd = fetch.path_for("rf2-core-wtprensec-smd")
    assert _smd_groups(smd) == ["dataset"]
    _check_smd(smd, "dataset", tmp_path)
    _check_model(fetch.path_for("rf2-core-wtprensec-model"), smd, tmp_path)


# --- 3. Kinetics, on the committed extract (required matrix) ------------------------------------


def test_the_stored_rates_are_the_authors_rule_on_the_stored_transition_matrix() -> None:
    model = read_model(fetch.path_for("rf2-core-wtprensec-model"))
    np.testing.assert_allclose(rates_from_tmatrix(model.tmatrix), model.rates, rtol=1e-12)
    # RF2 leaves the ribosome (0.91 -> 0.00 FRET) at 0.50 /s before the photobleaching
    # correction; Table 1 gives 0.26 +- 0.03 /s after it (see the weekly test below).
    assert model.means[1] > 0.9 > 0.01 > model.means[0]
    assert model.rates[1, 0] / FRAME_S == pytest.approx(0.501674, abs=5e-7)


# --- The whole archive (weekly reference-data job) ----------------------------------------------


@pytest.fixture(scope="module")
def archive() -> Iterator[zipfile.ZipFile]:
    with zipfile.ZipFile(fetch.path_for("rf2-arfa-archive")) as opened:
        yield opened


@pytest.mark.reference_download
@pytest.mark.parametrize("member", SMD_MEMBERS)
def test_every_smd_file_of_the_archive_imports_with_no_loss(
    archive: zipfile.ZipFile, member: str, tmp_path: Path
) -> None:
    path = _member(archive, member, tmp_path)
    groups = _smd_groups(path)
    assert groups, f"{member} holds no SMD group"
    for group in groups:
        _check_smd(path, group, tmp_path / group)


@pytest.mark.reference_download
@pytest.mark.parametrize("member", sorted(MODEL_MEMBERS))
def test_every_model_file_of_the_archive_imports_with_no_loss(
    archive: zipfile.ZipFile, member: str, tmp_path: Path
) -> None:
    model = _member(archive, member, tmp_path / "model")
    smd = _member(archive, MODEL_MEMBERS[member], tmp_path / "smd")
    _check_model(model, smd, tmp_path)
    stored = read_model(model)
    np.testing.assert_allclose(rates_from_tmatrix(stored.tmatrix), stored.rates, rtol=1e-12)


@pytest.mark.reference_download
def test_the_smd_extract_is_what_its_script_says(archive: zipfile.ZipFile) -> None:
    """In-window frames bit for bit, zero outside, the source renamed, the rest unchanged."""
    script = _load_script()
    member = archive.read(script.MEMBER)
    extract = fetch.path_for("rf2-core-wtprensec-smd")
    with h5py.File(io.BytesIO(member), "r") as src, h5py.File(extract, "r") as out:
        a, b = src["dataset"], out["dataset"]
        pre, post = a["tMAVEN/pre_list"][()], a["tMAVEN/post_list"][()]
        raw, kept = a["data/raw"][()], b["data/raw"][()]
        assert kept.dtype == raw.dtype and kept.shape == raw.shape
        inside = np.zeros(raw.shape[:2], dtype=bool)
        for i in range(raw.shape[0]):
            inside[i, pre[i] : post[i]] = True
        np.testing.assert_array_equal(kept[inside], raw[inside])
        assert not kept[~inside].any()
        for name in ("data/source_index", "tMAVEN/classes", "tMAVEN/pre_list", "tMAVEN/post_list"):
            np.testing.assert_array_equal(b[name][()], a[name][()], strict=True)
        for name in ("", "data", "tMAVEN"):
            left, right = (a[name] if name else a), (b[name] if name else b)
            assert dict(right.attrs) == dict(left.attrs), name or "dataset"
        assert json.loads(b["sources"].attrs["source_list"]) == [script.SOURCE_NAME]
        assert b["sources/0"].attrs["source_name"] == script.SOURCE_NAME
        assert b["sources/0"].attrs["date added"] == a["sources/0"].attrs["date added"]


# --- Table 1 of [Michael2025] ------------------------------------------------------------------

#: The archive models behind Table 1, by complex: the RF2-core signal (two states, zero and
#: bound) and the RF2-dIII signal (zero, distal and proximal; the mut-nsEC model has four states,
#: two of them zero, which the authors merge into one [Michael2025, Methods]).
_CORE = "RF2_core/Steady state/HMMs/RF2_core_{}_smd_HMM.hdf5"
_DIII = "RF2_dIII/HMMs/RF2_dIII_{}_smd_HMM.hdf5"
_API = _DIII.format("TC_Api")
#: For each RF2-dIII model: the zero states, the distal state and the proximal state.
_DIII_STATES = {
    "TC": ((0,), 1, 2),
    "wtnsEC": ((0,), 1, 2),
    "wtprensEC": ((0,), 1, 2),
    "mutnsEC": ((0, 1), 2, 3),
}

#: Table 1 of [Michael2025] (doi:10.1073/pnas.2509408122): ``(complex, quantity) -> (value, sd)``
#: in 1/s, "SD of the posteriors". ``kd`` is the (Cy5)RF2core dissociation rate; the other four
#: are (Cy5)RF2dIII rates: proximal->distal, distal->proximal, dissociation from proximal and
#: dissociation from distal. Every dissociation rate is photobleaching-corrected except the
#: Api137-bound TC, which IS the photobleaching rate.
TABLE_1 = {
    ("TC", "kd"): (0.16, 0.02),
    ("wtnsEC", "kd"): (0.66, 0.05),
    ("wtprensEC", "kd"): (0.26, 0.03),
    ("mutnsEC", "kd"): (0.47, 0.03),
    ("TC", "prox_to_dist"): (0.50, 0.02),
    ("TC", "dist_to_prox"): (0.35, 0.01),
    ("TC", "kd_prox"): (0.13, 0.02),
    ("TC", "kd_dist"): (0.41, 0.01),
    ("wtnsEC", "prox_to_dist"): (0.35, 0.02),
    ("wtnsEC", "dist_to_prox"): (0.32, 0.01),
    ("wtnsEC", "kd_prox"): (0.08, 0.02),
    ("wtnsEC", "kd_dist"): (0.30, 0.02),
    ("wtprensEC", "prox_to_dist"): (0.32, 0.02),
    ("wtprensEC", "dist_to_prox"): (0.25, 0.01),
    ("wtprensEC", "kd_prox"): (0.08, 0.02),
    ("wtprensEC", "kd_dist"): (0.18, 0.01),
    ("mutnsEC", "prox_to_dist"): (0.40, 0.02),
    ("mutnsEC", "dist_to_prox"): (0.33, 0.02),
    ("mutnsEC", "kd_prox"): (0.00, 0.01),
    ("mutnsEC", "kd_dist"): (1.11, 0.03),
    ("TC_Api", "kd_dist"): (0.24, 0.01),
}

#: MEASURED DIFFERENCES, not tolerances: the five Table 1 numbers the archived models do not
#: give within the stated SD, with the value Tether computes from them (1/s). The four
#: wt-pre-nsEC (Cy5)RF2dIII numbers all differ, which suggests the archived wt-pre-nsEC dIII
#: model is not the one behind the table; mut-nsEC kd_dist is the merged four-state model.
#: Reported in docs/validation.md and on #708; a change here is a change of the record or of
#: Tether's reading of it, never a widening.
TABLE_1_DIFFERENCES = {
    ("wtprensEC", "prox_to_dist"): 0.370530,
    ("wtprensEC", "dist_to_prox"): 0.275527,
    ("wtprensEC", "kd_prox"): 0.059335,
    ("wtprensEC", "kd_dist"): 0.198287,
    ("mutnsEC", "kd_dist"): 1.171025,
}


def _probabilities(archive: zipfile.ZipFile, member: str, tmp_path: Path) -> np.ndarray:
    model = read_model(_member(archive, member, tmp_path))
    return model.tmatrix / model.tmatrix.sum(axis=1, keepdims=True)


def _rate(p: np.ndarray, i: int, targets: tuple[int, ...]) -> float:
    """Rate (1/s) out of state ``i`` into the merged ``targets``, by the authors' rule."""
    return float(-np.log1p(-p[i, list(targets)].sum()) / FRAME_S)


def table_1_from_the_archive(archive: zipfile.ZipFile, tmp_path: Path) -> dict:
    """Every Table 1 number, as Tether computes it from the archived models."""
    api = _probabilities(archive, _API, tmp_path)
    bleach = _rate(api, 1, (0,))  # the Api137-bound TC traps the distal state
    out = {("TC_Api", "kd_dist"): bleach}
    for complex_ in ("TC", "wtnsEC", "wtprensEC", "mutnsEC"):
        core = _probabilities(archive, _CORE.format(complex_), tmp_path)
        out[(complex_, "kd")] = _rate(core, 1, (0,)) - bleach
        zero, dist, prox = _DIII_STATES[complex_]
        p = _probabilities(archive, _DIII.format(complex_), tmp_path)
        out[(complex_, "prox_to_dist")] = _rate(p, prox, (dist,))
        out[(complex_, "dist_to_prox")] = _rate(p, dist, (prox,))
        out[(complex_, "kd_prox")] = _rate(p, prox, zero) - bleach
        out[(complex_, "kd_dist")] = _rate(p, dist, zero) - bleach
    return out


@pytest.mark.reference_download
def test_the_archive_reproduces_table_1_of_the_paper(
    archive: zipfile.ZipFile, tmp_path: Path
) -> None:
    measured = table_1_from_the_archive(archive, tmp_path)
    assert set(measured) == set(TABLE_1)
    for key, (value, sd) in TABLE_1.items():
        print(f"Table 1 {key}: paper {value} +- {sd}, archive {measured[key]:.6f} /s")
        if key in TABLE_1_DIFFERENCES:
            assert measured[key] == pytest.approx(TABLE_1_DIFFERENCES[key], abs=5e-7), key
            assert abs(measured[key] - value) > sd, f"{key} now agrees: remove it from the list"
        else:
            assert abs(measured[key] - value) <= sd, (key, measured[key], value, sd)
