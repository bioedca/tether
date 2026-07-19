# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live standalone-tMAVEN hand-off: a Tether SMD opens in tMAVEN with coords intact.

The scripted half of the M9 2-OS standalone-tMAVEN GUI hand-off verification
(``@pytest.mark.sidecar`` — deselected from the CI matrix, run in the isolated
sidecar env; issue #13, ADR-0010). It proves the outbound-leg claim of PRD §7.4:
a Tether-authored SMD (:func:`tether.idealize.write_smd`) opens in the standalone
tMAVEN GUI, because :func:`tether.idealize.check_smd_opens` drives *tMAVEN's own
loader* (``maven.io.load_smdtmaven_hdf5`` → ``pysmd.load_smd_in_hdf5``) — the exact
code path behind the GUI's *File → Load SMD* menu — and reports what it parsed. The
per-trace analysis windows Tether rides along come back through tMAVEN's ``pre_list``/
``post_list``, and the Tether coordinate superset survives in the file that tMAVEN
opened (verified with :func:`tether.idealize.read_smd`).

The remaining *manual* leg — a human opening the same SMD in the tMAVEN **GUI** on a
second OS and eyeballing the traces — is documented, with a cross-OS result table, in
``docs/idealize/standalone-tmaven-handoff.md``.

Like ``test_sidecar_driver.py`` this needs an interpreter in ``$TETHER_SIDECAR_PYTHON``
(an env built from ``sidecar/conda-lock.yml`` with tMAVEN — e.g. via
``scripts/setup_sidecar.py``). It imports **only** ``tether.idealize`` so it collects and
runs in the isolated sidecar env (no base GUI/IO stack). CI has no sidecar env, so the
whole module is excluded by ``-m "not ... and not sidecar"``.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from tether.idealize import SMDOpenCheck, check_smd_opens, read_smd, write_smd

pytestmark = pytest.mark.sidecar

_SIDECAR = os.environ.get("TETHER_SIDECAR_PYTHON")
requires_sidecar = pytest.mark.skipif(
    not _SIDECAR, reason="set TETHER_SIDECAR_PYTHON to a tMAVEN sidecar interpreter"
)


def _write_superset_smd(path: Path) -> dict:
    """Write a small Tether SMD with per-trace windows + a coordinate superset.

    Returns the ground-truth values the tMAVEN load-check and the superset re-read are
    asserted against.
    """
    n, t = 3, 40
    rng = np.random.default_rng(1300 + n)  # deterministic; no argless Random
    donor = rng.uniform(200.0, 1000.0, size=(n, t))
    acceptor = rng.uniform(200.0, 1000.0, size=(n, t))
    raw = np.stack([donor, acceptor], axis=-1)  # (n, t, 2)

    pre = np.array([0, 3, 5], dtype="int64")
    post = np.array([t, t - 2, t - 1], dtype="int64")
    classes = np.zeros(n, dtype="int64")  # neutral at M2 (category<->class map is M4)
    donor_xy = np.array([[10.0, 20.0], [11.5, 21.5], [12.0, 22.0]])
    acceptor_xy = np.array([[110.0, 20.0], [111.5, 21.5], [112.0, 22.0]])
    keys = [f"mol-{i:03d}" for i in range(n)]
    ids = [f"id-{i:03d}" for i in range(n)]

    write_smd(
        path,
        raw,
        classes=classes,
        pre_list=pre,
        post_list=post,
        donor_xy=donor_xy,
        acceptor_xy=acceptor_xy,
        molecule_keys=keys,
        molecule_ids=ids,
    )
    return {
        "n": n,
        "t": t,
        "raw_sum": float(np.nansum(raw)),
        "pre": pre,
        "post": post,
        "donor_xy": donor_xy,
        "acceptor_xy": acceptor_xy,
        "keys": keys,
        "ids": ids,
    }


@requires_sidecar
def test_tether_smd_opens_in_tmaven(tmp_path):
    """tMAVEN's own loader opens the Tether SMD and parses every trace + window."""
    smd = tmp_path / "handoff.hdf5"
    truth = _write_superset_smd(smd)

    check = check_smd_opens(smd)

    assert isinstance(check, SMDOpenCheck)
    # tMAVEN parsed the standard SMD the GUI reads: the full trace block.
    assert check.n_molecules == truth["n"]
    assert check.n_frames == truth["t"]
    assert check.n_channels == 2
    assert check.raw_shape == (truth["n"], truth["t"], 2)
    # The intensities survived the hand-off byte-for-byte (checksum).
    assert check.raw_sum == pytest.approx(truth["raw_sum"], rel=1e-9)
    # The per-trace analysis windows Tether rode along came back through tMAVEN's
    # pre_list/post_list (the coordinate-of-time metadata the GUI opens with set).
    assert np.array_equal(check.pre_list, truth["pre"])
    assert np.array_equal(check.post_list, truth["post"])


@requires_sidecar
def test_coordinate_superset_intact_after_tmaven_open(tmp_path):
    """The Tether coordinate superset is intact in the SMD tMAVEN just opened (#13)."""
    smd = tmp_path / "handoff.hdf5"
    truth = _write_superset_smd(smd)

    # Prove tMAVEN opens it (the GUI's load path), then that the coordinate metadata
    # riding in the tether/ superset group survived in that same file.
    check_smd_opens(smd)

    back = read_smd(smd)
    assert back.has_superset
    assert np.allclose(back.donor_xy, truth["donor_xy"])
    assert np.allclose(back.acceptor_xy, truth["acceptor_xy"])
    assert back.molecule_keys == truth["keys"]
    assert back.molecule_ids == truth["ids"]


@requires_sidecar
def test_check_smd_opens_rejects_non_smd(tmp_path):
    """A non-SMD HDF5 fails the open-check with a clean deterministic error."""
    import h5py

    from tether.idealize import SidecarError

    bad = tmp_path / "not_smd.hdf5"
    with h5py.File(bad, "w") as f:
        f.create_group("dataset")  # no @format='SMD', no data/raw

    with pytest.raises(SidecarError):
        check_smd_opens(bad)
