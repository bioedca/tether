# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Content, shape/dtype and size-budget checks for the committed test fixtures.

These lock the fixtures derived by ``scripts/make_fixtures.py`` (PRD §8 NFR-FIXTURES) so a
silent corruption or an over-size commit is caught by CI. Each plain-git fixture is
compared with a committed SHA-256, so a value change at the same shape and dtype also
fails (#736 IDZ-F18). The large-tier fixtures live in Git-LFS and are not pulled by
the default checkout, so their check is ``@pytest.mark.large`` and skips on an
unmaterialized LFS pointer.
"""

from __future__ import annotations

import hashlib
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
    FIXTURES / "acceptor_oracle.npz",
    FIXTURES / "tdat_coloc_slice.tdat",
    FIXTURES / "tmap_coeffs.npz",
    FIXTURES / "bead_prealign_oracle.npz",
    FIXTURES / "kinsoft_trace_sample.txt",
    FIXTURES / "deeplasi_export_slice.mat",
    FIXTURES / "deeplasi_traces_slice.txt",
    FIXTURES / "rf2_core_wtprensec_smd.hdf5",
    FIXTURES / "rf2_core_wtprensec_model.hdf5",
]

# The SHA-256 of each plain fixture's content. A fixture that a script regenerates on
# purpose gets its new digest here in the same pull request; any other change of a
# value, at the same shape and dtype, fails `test_plain_fixture_content_is_unchanged`.
PLAIN_FIXTURE_SHA256 = {
    "movie_be_64x64x50.tif": "ce39a36cf01fbe006e6c4c2225725adf9c522cd4172f8cbf35c02161d54b8999",
    "smd_4mol.hdf5": "227930deeb3ca03d9ec437d37d72432944028f2bf2ceb07b00e2b7fd35301fe8",
    "smd_2mol.hdf5": "95439c4bc54063a9abb753148e8ac6393396b095c4011c16818592dc3b5114db",
    "aperture_oracle.npz": "4ed503852b1ac8d746c230b1c252f24e9a087be1a9f51b9f156d11a2fc5af496",
    "acceptor_oracle.npz": "1dff8443a54912ddba63c0827e953c8d1ca46ff9892c08f493dda8540d8056ab",
    "tdat_coloc_slice.tdat": "e06034213d6e8580153e4043de8dd1a9c4a4edf430b9161c069b8b1bc2dac14c",
    "tmap_coeffs.npz": "4740f97ea9ec585a7948c218c4be031c2bcc99ba8abb37122a29d7d0b6b85c76",
    "bead_prealign_oracle.npz": "e7891918a1a0b9cf40ef4934a0b0c44f80c0744730d51f7a3d5548d2e6a23f48",
    "kinsoft_trace_sample.txt": "5eab80bc4a11b711712d78e898d9b79b3bc2997ce48a8fa2d0170ba883a23a05",
    "deeplasi_export_slice.mat": "5f164212d004801765de56da46677ccc0174c3f1ff22960ee88a2f332227cdcf",
    "deeplasi_traces_slice.txt": "6d5a88226e2f189449acb5a213bd38cf9b2fd0ff6d3f4f5b8232e3a9a8d3ddd8",
    "rf2_core_wtprensec_smd.hdf5": (
        "caef96af49c02a2c16729c5ddf32dcf8cb221e4d2c76841c9c9e08818da17926"
    ),
    "rf2_core_wtprensec_model.hdf5": (
        "edeaa298604895a842c3ab7692370d555b05b2e779d7bcb8b2f81b5d01d0be7a"
    ),
}

# `.gitattributes` gives these suffixes `text=auto`: the repository stores LF, and a
# Windows checkout with `core.autocrlf=true` writes CRLF. Their digest is therefore
# taken over the LF form. Every other fixture suffix is `binary` there, so its bytes
# are hashed as they are.
_TEXT_FIXTURE_SUFFIXES = frozenset({".txt"})


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


def _content_sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in _TEXT_FIXTURE_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def test_every_plain_fixture_has_a_digest() -> None:
    assert set(PLAIN_FIXTURE_SHA256) == {p.name for p in PLAIN_FIXTURES}


@pytest.mark.parametrize("path", PLAIN_FIXTURES, ids=lambda p: p.name)
def test_plain_fixture_content_is_unchanged(path: Path) -> None:
    """The fixture bytes equal the committed digest (values, not only shape and dtype)."""
    assert path.is_file(), f"missing committed fixture: {path}"
    actual = _content_sha256(path)
    assert actual == PLAIN_FIXTURE_SHA256[path.name], (
        f"{path.name} changed: SHA-256 {actual}. If a script regenerated it on purpose, "
        "record the new digest in PLAIN_FIXTURE_SHA256."
    )


def test_tmap_coeffs_structure() -> None:
    np = pytest.importorskip("numpy")
    data = np.load(FIXTURES / "tmap_coeffs.npz")
    ids = data["channel_ids"].tolist()
    assert len(ids) == 2
    assert int(data["reference_channel"]) in ids
    for cid in ids:
        for name in ("ref_to_channel", "channel_to_ref"):
            assert data[f"c{cid}_{name}_a"].shape == (6,)  # degree-2 coeffs
            assert data[f"c{cid}_{name}_b"].shape == (6,)
            assert data[f"c{cid}_{name}_norm_xy"].shape == (3, 3)
            assert data[f"c{cid}_{name}_norm_uv"].shape == (3, 3)


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
