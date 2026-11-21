# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's leakage correction and γ-corrected FRET against MASH-FRET's own results (#711).

The reference is the set of MASH-FRET project files that authors of MASH-FRET published with
their group IIB intron study (Zenodo doi:10.5281/zenodo.24104528, CC-BY-4.0). The required
matrix reads the committed extract of the smallest file (``scripts/make_mashfret_fixture.py``);
the weekly workstation run reads the full archive. ADR-0087 records MASH-FRET as a reference, the
commit that wrote the files, the convention mapping and the ALEX rule. What the files allow:

* **Leakage.** MASH-FRET subtracts ``coeff · I_bgCorr`` of the leaking channel from the other
  channel on every laser (``crossCorr.m``), the same additive form as Tether's
  ``I_A,corr = I_A − α·I_D``. Every file of the record has coefficient 0, and its
  ``intensities_crossCorr`` equals its ``intensities_bgCorr``. So the leakage test shows that
  Tether reads the coefficient and gives MASH-FRET's corrected intensities at it; it cannot catch a
  sign error, because ``−0·I_D`` and ``+0·I_D`` are equal. So a second reference (#870) is a test
  project of the MASH-FRET repository that MASH-FRET wrote with a coefficient of 0.0775
  (``scripts/make_mashfret_leakage_fixture.py``). Its direct-excitation coefficient is not 0, and
  MASH-FRET applies that correction after the leakage one (``tests/_mashfret.py``): under the
  acceptor's laser the acceptor holds the leakage correction only, and under the donor's laser
  the test adds MASH-FRET's own direct-excitation term back before it compares.
* **FRET.** The files hold no FRET value for each frame. ``FRET_DTA`` holds, for each frame, the
  value of its state, and with "recalculate" on (``prmTT{m}{4}{1}(3) = 1``) MASH-FRET sets each
  state value to the mean of its γ-corrected FRET over the frames of that state (``getDiscr.m``,
  ``aveStates``). So the FRET test compares that mean of Tether's per-frame FRET with MASH-FRET's
  state value, state by state. A γ on the wrong channel moves the mean of every state, and the
  test fails (the pull request records the demonstration).
* **Background.** The files hold no video, so MASH-FRET's background subtraction cannot be
  repeated; both tests start from ``intensities_bgCorr``.
* **ALEX.** The set alternates a 532 nm and a 638 nm laser. The FRET test uses only the donor and
  the acceptor channels under the donor's laser (PRD §2.2, "What the tests can use"); the leakage
  test uses both lasers. Both tests require MASH-FRET's direct-excitation coefficient, an ALEX-only
  correction, to be 0.
"""

from __future__ import annotations

import zipfile

import numpy as np
import pytest

from _mashfret import (
    CrosstalkProject,
    MashProject,
    UnsupportedProject,
    read_crosstalk_project,
    read_project,
)
from reference_data.fetch import path_for
from tether.fret.efficiency import corrected_fret
from tether.fret.leakage import apply_leakage

#: The MASH-FRET commit that wrote the committed extract's source (its "prev. commit").
PINNED_COMMIT = "1a1f465"
#: The relative limits. Measured (see the pull request): the leakage difference is exactly 0 on
#: every file; the worst of the 34 states of the extract differs by 1.4e-15, the worst of the 4442
#: states of the eight readable files of the full archive by 6.3e-14 (a mean over up to 2000
#: frames, summed in another order than MATLAB's `mean`).
LEAKAGE_REL_LIMIT = 1e-15
STATE_REL_LIMIT = 1e-13

EXTRACT = "mashfret-ed135e-30mm-extract"
ARCHIVE = "mashfret-group-iib-intron-archive"

#: The non-zero leakage reference (#870): the MASH-FRET commit before the one that wrote it, and
#: its relative limit. Measured (see the pull request): exactly 0 under the acceptor's laser; under
#: the donor's laser, where the direct-excitation term is added back, at most 1.1e-16 on the 10
#: molecules of the extract and 1.7e-16 on all 30 of the source.
LEAKAGE_NONZERO_EXTRACT = "mashfret-2chan2exc-leakage-extract"
LEAKAGE_NONZERO_COMMIT = "3471fdf3"
LEAKAGE_NONZERO_REL_LIMIT = 1e-15


@pytest.fixture(scope="module")
def extract() -> MashProject:
    return read_project(path_for(EXTRACT))


def _donor_acceptor(project: MashProject) -> tuple[int, int, int]:
    ((donor, acceptor),) = project.fret_pairs  # one FRET pair in every file of the record
    return int(donor), int(acceptor), project.laser_of(donor)


def _check_preconditions(project: MashProject) -> None:
    """What the comparison needs from a project, so a file that breaks it fails loudly."""
    donor, acceptor, _ = _donor_acceptor(project)
    assert project.n_channels >= 2 and acceptor == donor + 1
    # Tether has no acceptor -> donor leakage and no direct-excitation term (delta = 0).
    assert project.leakage[acceptor, donor] == 0.0  # row acceptor, column 0 = the donor
    assert not project.direct_excitation.any()
    # No smoothing: MASH-FRET's FRET reads `denoise`, which then equals `crossCorr`.
    assert np.array_equal(project.denoised, project.crosstalk_corrected, equal_nan=True)
    # Each state value is the mean of the FRET over its frames (getDiscr.m, aveStates).
    assert (project.dta_settings[:, 2] == 1).all()


def _leakage_rel_error(project: MashProject) -> float:
    donor, acceptor, _ = _donor_acceptor(project)
    alpha = project.leakage[donor, 0]  # row donor, column 0 = the first other channel
    worst = 0.0
    for m in range(project.n_molecules):
        for laser in range(project.excitations.size):
            i_d = project.trace(project.background_corrected, m, donor, laser)
            i_a = project.trace(project.background_corrected, m, acceptor, laser)
            want_a = project.trace(project.crosstalk_corrected, m, acceptor, laser)
            want_d = project.trace(project.crosstalk_corrected, m, donor, laser)
            ok = ~np.isnan(want_a)
            if not ok.any():
                continue
            scale = np.max(np.abs(want_a[ok]))
            got_a = apply_leakage(i_d[ok], i_a[ok], alpha)
            error = float(np.max(np.abs(got_a - want_a[ok]))) / scale
            assert np.isfinite(error), f"molecule {m}, laser {laser}: non-finite leakage result"
            worst = max(worst, error)
            # The donor is not changed (Tether's bare-donor convention, ADR-0028).
            assert np.array_equal(i_d[ok], want_d[ok])
    return worst


def _state_rel_errors(project: MashProject) -> np.ndarray:
    """For each state of each molecule: |mean of Tether's FRET - MASH-FRET's value| / |value|."""
    donor, acceptor, laser = _donor_acceptor(project)
    alpha = project.leakage[donor, 0]
    errors: list[float] = []
    for m in range(project.n_molecules):
        frames = project.included[:, m]
        if not frames.any():
            continue
        i_d = project.trace(project.background_corrected, m, donor, laser)[frames]
        i_a = project.trace(project.background_corrected, m, acceptor, laser)[frames]
        fret = corrected_fret(i_d, i_a, alpha=alpha, gamma=project.gamma[m, 0])
        # calcFRET.m sets an undefined FRET to 0 and Tether to NaN; no frame here is undefined.
        assert not np.isnan(fret).any()
        states = project.fret_dta[frames, m]
        for value in np.unique(states):
            assert value != 0.0
            errors.append(abs(float(fret[states == value].mean()) - value) / abs(value))
    return np.asarray(errors)


# --- The required matrix: the committed extract ------------------------------------------------


def test_the_reader_gives_the_meaning_of_the_extract(extract: MashProject) -> None:
    assert extract.commit == PINNED_COMMIT
    assert extract.version.startswith("1.1.2")
    assert extract.n_molecules == 18
    assert extract.intensities is None  # the extract leaves the raw intensities out
    assert extract.labels == ("Cy3", "Cy5")
    assert extract.excitations.tolist() == [532.0, 638.0]
    assert extract.chan_exc.tolist() == [532.0, 638.0]
    assert extract.fret_pairs.tolist() == [[0, 1]]
    assert _donor_acceptor(extract) == (0, 1, 0)  # the donor under the 532 nm laser
    assert extract.leakage.tolist() == [[0.0], [0.0]]
    assert extract.direct_excitation.tolist() == [[0.0, 0.0]]
    assert np.all(extract.gamma == 0.45) and np.all(extract.beta == 1.0)
    assert np.all(extract.gamma_method == 0)  # γ set by hand
    assert extract.background_corrected.shape == (666, 36, 2)
    assert extract.fret_dta.shape == (666, 18)


def test_leakage_gives_mashfret_crosstalk_corrected_intensities(extract: MashProject) -> None:
    _check_preconditions(extract)
    assert _leakage_rel_error(extract) <= LEAKAGE_REL_LIMIT


def test_corrected_fret_gives_mashfret_state_values(extract: MashProject) -> None:
    _check_preconditions(extract)
    errors = _state_rel_errors(extract)
    assert errors.size >= 30  # every included molecule contributes at least one state
    assert errors.max() <= STATE_REL_LIMIT, f"worst state {errors.max():.3g}"


def test_gamma_on_the_acceptor_does_not_give_mashfret_state_values(
    extract: MashProject,
) -> None:
    # The convention check has teeth: MASH-FRET's 1 / (1 + γ·I_D/I_A) is Tether's
    # I_A / (I_A + γ·I_D); γ on the acceptor instead (γ·I_A / (γ·I_A + I_D)) misses every state.
    donor, acceptor, laser = _donor_acceptor(extract)
    m = int(np.flatnonzero(extract.included.any(axis=0))[0])
    frames = extract.included[:, m]
    i_d = extract.trace(extract.background_corrected, m, donor, laser)[frames]
    i_a = extract.trace(extract.background_corrected, m, acceptor, laser)[frames]
    gamma = extract.gamma[m, 0]
    wrong = corrected_fret(i_d, i_a * gamma, alpha=0.0, gamma=1.0)
    states = extract.fret_dta[frames, m]
    worst = max(abs(wrong[states == v].mean() - v) / abs(v) for v in np.unique(states))
    assert worst > 1e3 * STATE_REL_LIMIT


def test_the_reader_refuses_a_later_project_layout(tmp_path) -> None:
    import scipy.io as sio

    path = tmp_path / "later.mash"
    sio.savemat(path, {"MASH_version": "1.3.4", "TP": {"fix": 0}})
    with pytest.raises(UnsupportedProject, match="TP structure"):
        read_project(path)


# --- The required matrix: the non-zero leakage reference (#870) -------------------------------


@pytest.fixture(scope="module")
def leaky() -> CrosstalkProject:
    return read_crosstalk_project(path_for(LEAKAGE_NONZERO_EXTRACT))


def nonzero_leakage_rel_error(project: CrosstalkProject, sign: float = 1.0) -> float:
    """The worst relative difference of Tether's corrected acceptor from MASH-FRET's.

    *sign* = -1 applies the leakage with the wrong sign, for the teeth test. Under the donor's
    laser MASH-FRET has also subtracted ``δ·I_A,corr`` at the acceptor's laser (direct
    excitation, which Tether does not correct), so its own term is added back first.
    """
    ((donor, acceptor),) = project.fret_pairs
    alpha = float(project.leakage[donor, 0])
    own = project.laser_of(acceptor)
    worst = 0.0
    for m in range(project.n_molecules):
        own_corr = project.trace(project.crosstalk_corrected, m, acceptor, own)
        for laser in range(project.excitations.size):
            i_d = project.trace(project.background_corrected, m, donor, laser)
            i_a = project.trace(project.background_corrected, m, acceptor, laser)
            want = project.trace(project.crosstalk_corrected, m, acceptor, laser)
            if laser != own:
                n = laser if laser < own else laser - 1  # the n-th laser other than its own
                want = want + project.direct_excitation[n, acceptor] * own_corr
            got = apply_leakage(i_d, i_a, sign * alpha)
            error = float(np.max(np.abs(got - want))) / float(np.max(np.abs(want)))
            assert np.isfinite(error), f"molecule {m}, laser {laser}: non-finite leakage result"
            worst = max(worst, error)
            # The donor is not changed (Tether's bare-donor convention, ADR-0028).
            want_d = project.trace(project.crosstalk_corrected, m, donor, laser)
            assert np.array_equal(i_d, want_d)
    return worst


def test_the_reader_gives_the_meaning_of_the_nonzero_leakage_extract(
    leaky: CrosstalkProject,
) -> None:
    assert leaky.commit == LEAKAGE_NONZERO_COMMIT
    assert leaky.version.startswith("1.3.2")
    assert leaky.n_molecules == 10
    assert leaky.labels == ("don", "acc")
    assert leaky.excitations.tolist() == leaky.chan_exc.tolist() == [353.0, 387.0]
    assert leaky.fret_pairs.tolist() == [[0, 1]]
    assert leaky.laser_of(0) == 0 and leaky.laser_of(1) == 1
    assert leaky.leakage[0, 0] == 0.0775344654974015
    assert leaky.leakage[1, 0] == 0.0  # Tether has no acceptor -> donor leakage
    assert leaky.direct_excitation.tolist() == [[0.0, 0.0175978433518244]]
    assert leaky.background_corrected.shape == (2000, 20, 2)
    assert not np.isnan(leaky.crosstalk_corrected).any()


def test_nonzero_leakage_gives_mashfret_crosstalk_corrected_intensities(
    leaky: CrosstalkProject,
) -> None:
    worst = nonzero_leakage_rel_error(leaky)
    assert worst <= LEAKAGE_NONZERO_REL_LIMIT, f"worst {worst:.3g}"


def test_a_sign_error_in_leakage_does_not_give_mashfret_intensities(
    leaky: CrosstalkProject,
) -> None:
    # I_A + α·I_D instead of I_A − α·I_D misses by 2α·I_D, at least 2 % of the acceptor here.
    assert nonzero_leakage_rel_error(leaky, sign=-1.0) > 1e12 * LEAKAGE_NONZERO_REL_LIMIT


# --- The weekly workstation run: the full archive ---------------------------------------------


@pytest.mark.workstation
def test_the_full_archive_agrees_with_tether_and_gives_the_extract(tmp_path) -> None:
    import importlib.util
    from pathlib import Path

    archive = path_for(ARCHIVE)
    script = Path(__file__).resolve().parents[1] / "scripts" / "make_mashfret_fixture.py"
    spec = importlib.util.spec_from_file_location("make_mashfret_fixture", script)
    maker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    assert maker.extract(maker.source_bytes(archive)) == path_for(EXTRACT).read_bytes()

    read = skipped = 0
    with zipfile.ZipFile(archive) as zf:
        for name in sorted(n for n in zf.namelist() if n.endswith(".mash")):
            target = tmp_path / Path(name).name
            target.write_bytes(zf.read(name))
            try:
                project = read_project(target)
            except UnsupportedProject:
                skipped += 1  # the MASH-FRET 1.3.4 file keeps its settings in a TP structure
                continue
            _check_preconditions(project)
            assert _leakage_rel_error(project) <= LEAKAGE_REL_LIMIT, name
            errors = _state_rel_errors(project)
            assert errors.max() <= STATE_REL_LIMIT, f"{name}: worst state {errors.max():.3g}"
            target.unlink()
            read += 1
    assert (read, skipped) == (8, 1)
