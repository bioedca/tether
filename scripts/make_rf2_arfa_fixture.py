# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the committed RF2-ArfA SMD extract from the authors' Zenodo archive (#708, ADR-0079).

The RF2-ArfA record of the tMAVEN authors (Michael, Huang, Ray, Kinz-Thompson & Gonzalez;
Zenodo 10.5281/zenodo.17246323, CC-BY-4.0) holds tMAVEN SMD files and the "vb Consensus HMM"
models the authors fitted to them. The smallest pair is the RF2 core-domain signal on
ArfA-pre-incubated non-stop elongation complexes (``RF2_core_wtprensEC``). Its model file is
committed unchanged (``extract_member`` in ``tests/reference_data/manifest.toml``). Its SMD file
(1.16 MB) is larger than the 512 KiB plain-git limit of ``check-added-large-files``, and the
required test matrix reads no Git-LFS file, so this script makes a smaller extract of it:

* every frame inside a molecule's analysis window ``[pre_list, post_list)`` keeps its donor and
  acceptor value bit for bit; every frame outside the window is set to ``0.0``. tMAVEN fits and
  plots only the frames inside the window, and so does Tether, so the fit and the 2-D histogram
  of the extract are those of the original file;
* ``source_list`` and ``source_name`` name the archive member instead of the path on the
  authors' computer;
* every other array and attribute is copied unchanged, and the file is written with gzip level
  9 and the shuffle filter.

``tests/test_rf2_arfa.py`` checks, in the weekly reference-data job, that the extract and the
archive member agree in exactly this way.

This script is dev-only (GPL like the rest of ``scripts/``); it never runs in CI. Usage::

    python scripts/make_rf2_arfa_fixture.py --archive /path/to/ArfA_RF2_data.zip \
        --out tests/fixtures/rf2_core_wtprensec_smd.hdf5
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

import h5py
import numpy as np

#: SHA-256 of ``ArfA_RF2_data.zip`` (record 17246323, retrieved 2026-11-12).
ARCHIVE_SHA256 = "1ae523076f89a1e28db2f2035b76ecabd3b4c24e649083158a08ca1ef975c9b8"
#: The SMD member this script extracts.
MEMBER = "Data/Single molecule data/RF2_core/Steady state/RF2_core_wtprensEC_smd.hdf5"
#: The name the extract records as its source.
SOURCE_NAME = f"zenodo:17246323/{MEMBER}:/dataset/data/raw"

_COMPRESSION = {"compression": "gzip", "compression_opts": 9, "shuffle": True}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_attrs(src, dst) -> None:
    for key, value in src.attrs.items():
        dst.attrs[key] = value


def _dataset(group, name: str, data: np.ndarray) -> None:
    kwargs = dict(_COMPRESSION) if data.size > 1 else {}
    group.create_dataset(name, data=data, track_times=False, **kwargs)


def windowed_raw(raw: np.ndarray, pre: np.ndarray, post: np.ndarray) -> np.ndarray:
    """``raw`` with every frame outside ``[pre[i], post[i])`` set to ``0.0``."""
    out = np.zeros_like(raw)
    for i in range(raw.shape[0]):
        out[i, pre[i] : post[i]] = raw[i, pre[i] : post[i]]
    return out


def make_extract(member_bytes: bytes, out: Path) -> None:
    """Write the extract of the SMD member held in ``member_bytes`` to ``out``."""
    with h5py.File(io.BytesIO(member_bytes), "r") as src:
        g = src["dataset"]
        pre = g["tMAVEN/pre_list"][()]
        post = g["tMAVEN/post_list"][()]
        raw = windowed_raw(g["data/raw"][()], pre, post)
        out.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(out, "w", track_order=True) as dst:
            d = dst.create_group("dataset")
            _copy_attrs(g, d)
            data = d.create_group("data")
            _copy_attrs(g["data"], data)
            _dataset(data, "raw", raw)
            _dataset(data, "source_index", g["data/source_index"][()])
            sources = d.create_group("sources")
            sources.attrs["source_list"] = json.dumps([SOURCE_NAME])
            for key in g["sources"]:
                sub = sources.create_group(key)
                _copy_attrs(g["sources"][key], sub)
                sub.attrs["source_name"] = SOURCE_NAME
            tmaven = d.create_group("tMAVEN")
            _copy_attrs(g["tMAVEN"], tmaven)
            for name in ("classes", "post_list", "pre_list"):
                _dataset(tmaven, name, g["tMAVEN"][name][()])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--archive", type=Path, required=True, help="ArfA_RF2_data.zip")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    actual = _sha256(args.archive)
    if actual != ARCHIVE_SHA256:
        parser.error(f"{args.archive} has SHA-256 {actual}, expected {ARCHIVE_SHA256}")
    with zipfile.ZipFile(args.archive) as archive:
        make_extract(archive.read(MEMBER), args.out)
    print(f"wrote {args.out} ({args.out.stat().st_size} B, SHA-256 {_sha256(args.out)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
