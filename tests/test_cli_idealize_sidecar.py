# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live ``tether idealize`` against ``idealize_molecules`` on a real sidecar (#657).

The issue asks that a project fitted by the command store the same result as
[`idealize_molecules`][tether.project.idealize.idealize_molecules] with the same arguments
and seed. ``tests/test_cli_idealize.py`` checks that with a fake sidecar in the base matrix;
this module repeats it with the tMAVEN sidecar named by ``$TETHER_SIDECAR_PYTHON``, so the
seed reaching the fit (ADR-0075) is what makes the two runs agree, not a canned answer.

``@pytest.mark.sidecar``: deselected from the base matrix and run by the ``sidecar /
parity`` job. The store is built with ``tests/_analysis_store.py``, whose imports (NumPy,
h5py, SciPy and the Tether core) are all in the sidecar lock. No parity tolerance is read or
changed here: two seeded fits on one build must be equal value for value.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from _analysis_store import build_store_with_channels
from _sidecar_gate import SIDECAR, requires_sidecar
from tether import cli
from tether.project.idealize import idealize_molecules, read_idealization

pytestmark = pytest.mark.sidecar

SEED = 11
N_MOLECULES = 6
N_FRAMES = 160


def _two_state_channels() -> tuple[np.ndarray, np.ndarray]:
    """Donor and acceptor of molecules that switch once between E = 0.3 and E = 0.7."""
    rng = np.random.default_rng(657)
    total = 1000.0
    efficiency = np.full((N_MOLECULES, N_FRAMES), 0.3)
    for i in range(N_MOLECULES):
        efficiency[i, 40 + 12 * i :] = 0.7
    acceptor = total * efficiency + rng.normal(0.0, 40.0, efficiency.shape)
    donor = total * (1.0 - efficiency) + rng.normal(0.0, 40.0, efficiency.shape)
    return donor, acceptor


@requires_sidecar
def test_the_command_stores_what_idealize_molecules_stores_on_a_live_sidecar(
    tmp_path: Path,
) -> None:
    donor, acceptor = _two_state_channels()
    by_command, _ = build_store_with_channels(tmp_path, donor, acceptor, name="cmd.tether")
    by_function, _ = build_store_with_channels(tmp_path, donor, acceptor, name="fn.tether")

    argv = ["idealize", str(by_command.path), "--nstates", "2", "--seed", str(SEED)]
    assert cli.main([*argv, "--sidecar-python", str(SIDECAR)]) == 0
    expected = idealize_molecules(by_function, nstates=2, seed=SEED, sidecar_python=SIDECAR)
    stored = read_idealization(by_command, "vbconhmm")

    assert (stored.model_type, stored.nstates) == ("vbconhmm", 2)
    assert stored.model_name == expected.model_name == "vbconhmm"
    assert stored.molecule_keys == expected.molecule_keys
    assert stored.input_hashes == expected.input_hashes
    assert stored.elbo == expected.elbo
    np.testing.assert_array_equal(stored.means, expected.means)
    np.testing.assert_array_equal(stored.state_paths, expected.state_paths)
    np.testing.assert_array_equal(stored.idealized, expected.idealized)
