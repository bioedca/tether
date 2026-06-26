# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shape/dtype and size-budget checks for the committed test fixtures.

These lock the fixtures derived by ``scripts/make_fixtures.py`` (PLAN §2.1) so a
silent corruption or an over-size commit is caught by CI. The large-tier
fixtures live in Git-LFS and are not pulled by the default checkout, so their
check is ``@pytest.mark.large`` and skips on an unmaterialized LFS pointer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
LARGE = FIXTURES / "large"

# Mirror the pre-commit `check-added-large-files --maxkb=512` gate: every fixture
# in plain git must stay under it (the large tier lives in LFS instead).
MAX_PLAIN_BYTES = 512 * 1024

PLAIN_FIXTURES = [
    FIXTURES / "movie_be_64x64x50.tif",
    FIXTURES / "smd_4mol.hdf5",
    FIXTURES / "smd_2mol.hdf5",
    FIXTURES / "aperture_oracle.npz",
    FIXTURES / "tdat_coloc_slice.tdat",
]


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is absent or a Git-LFS pointer stub (not real data)."""
    if not path.exists():
        return True
    if path.stat().st_size > 4096:
        return False
    return path.read_bytes()[:64].startswith(b"version https://git-lfs")


@pytest.mark.parametrize("path", PLAIN_FIXTURES, ids=lambda p: p.name)
def test_plain_fixture_under_size_gate(path: Path) -> None:
    assert path.is_file(), f"missing committed fixture: {path}"
    size = path.stat().st_size
    # `<=` mirrors pre-commit's `--maxkb=512`, which allows files at exactly 512 KiB.
    assert size <= MAX_PLAIN_BYTES, f"{path.name} is {size} B (> 512 KiB plain-git gate)"


def test_movie_is_big_endian_crop() -> None:
    tifffile = pytest.importorskip("tifffile")
    path = FIXTURES / "movie_be_64x64x50.tif"
    with tifffile.TiffFile(path) as tif:
        assert tif.byteorder == ">", "movie fixture must stay big-endian (M0 S7 reader)"
        series = tif.series[0]
        assert tuple(int(x) for x in series.shape) == (50, 64, 64)
        assert series.dtype == "uint16"


@pytest.mark.parametrize(
    ("name", "n_mol"),
    [("smd_4mol.hdf5", 4), ("smd_2mol.hdf5", 2)],
)
def test_small_smd_structure(name: str, n_mol: int) -> None:
    h5py = pytest.importorskip("h5py")
    with h5py.File(FIXTURES / name, "r") as f:
        raw = f["dataset/data/raw"]
        assert raw.shape == (n_mol, 1700, 2)
        assert raw.dtype == "float64"
        assert f["dataset/tMAVEN/classes"].shape == (n_mol,)


@pytest.mark.large
def test_population_smd_and_model() -> None:
    h5py = pytest.importorskip("h5py")
    smd = LARGE / "smd_281mol.hdf5"
    model = LARGE / "model_281mol.hdf5"
    if _is_lfs_pointer(smd) or _is_lfs_pointer(model):
        pytest.skip("LFS large-tier fixtures not materialized (default checkout)")
    with h5py.File(smd, "r") as f:
        assert f["dataset/data/raw"].shape == (281, 1700, 2)
    with h5py.File(model, "r") as f:
        assert int(f["model/nstates"][()]) == 4
        assert f["model/mean"].shape == (4,)
        assert f["model/idealized"].shape == (281, 1700)
