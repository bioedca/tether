# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the small committed TIRFdata ``.tdat`` decode fixture (M0.5 S6).

The TIRFdata ``.tdat`` decode (PRD §7.8, Appendix A/B) needs a *real-data*
fixture that exercises the MATLAB v7.3 access path, but the source ``.tdat`` is
37 MB. This tool copies only the payload :mod:`tether.io.tdat` reads — the real
``ParticlesColocalized`` coordinate matrix, the three ``Default{Alpha,Beta,Gamma}``
scalars, the ``ParticleDetectionMode`` leaf, and the MCOS ``Channel`` object blob
needed to decode the per-channel ``DetectionThreshold`` — into a tiny
``.tdat``-format file, dropping the ~37 MB of trace/patch arrays.

The MCOS retention is faithful, not a stub: the real ``#subsystem#/MCOS``
``FileWrapper__`` metadata blob and ``temp/Channel`` object-reference markers are
copied **verbatim**, along with every FileWrapper heap cell small enough to be a
scalar/short-vector value (the large per-channel images and trace arrays are
dropped as null cells, preserving cell indices). So :mod:`tether.io.mcos` decodes
the committed fixture through the exact same path — object-reference marker ->
object table -> property segment -> heap cell ``value + 2`` — that it walks on the
real file. The output reproduces the original coordinate access path exactly
(``temp/ParticlesColocalized`` cell -> HDF5 object reference -> ``(17, N)``
``findColoc`` matrix in ``#refs#``), so the committed test genuinely decodes a
real file rather than a stub. The full ``.tdat`` stays external (PLAN §2.1/§2.2).

Regenerate with::

    uv run --no-project --with h5py --with numpy \
        python scripts/make_tdat_fixture.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import h5py
import numpy as np


def _find_example_data() -> Path:
    """Locate the read-only ``example-data`` sibling by walking up from here.

    Robust to running from either the main checkout (``Tether/scripts``) or a
    linked worktree (``Tether/.claude/worktrees/<branch>/scripts``).
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data"
        if candidate.is_dir():
            return candidate
    raise SystemExit("could not locate the external 'example-data' sibling directory")


# External read-only source (never committed).
SRC = _find_example_data() / "bla-uckopsb-tbox-video10"
TDAT = SRC / "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif2025-07-21_00-00.tdat"

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tdat_coloc_slice.tdat"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scalar(group: h5py.Group, name: str) -> float:
    return float(np.asarray(group[name][()]).reshape(-1)[0])


# FileWrapper heap cells at or below this size are copied verbatim (scalars, short
# vectors, colour/crop metadata, the per-channel DetectionThreshold); larger cells
# (per-channel cumulated images, 1700-frame trace arrays, 21x21 masks) drop out as
# null cells, keeping the fixture tiny while preserving cell indices so the
# decoder's ``value + 2`` heap lookup still lands. The metadata blob (cell 0) is
# always kept.
_MCOS_CELL_MAX_BYTES = 512


def _copy_leaf(store: h5py.Group, name: str, source: h5py.Dataset) -> h5py.Reference:
    """Copy a leaf dataset into ``store`` under ``name``, preserving MATLAB_class."""
    out = store.create_dataset(name, data=np.asarray(source[()]))
    cls = source.attrs.get("MATLAB_class")
    if cls is not None:
        out.attrs["MATLAB_class"] = cls
    return out.ref


def _copy_mcos_subsystem(f_src: h5py.File, g_out: h5py.File, store: h5py.Group) -> int:
    """Copy the ``#subsystem#/MCOS`` FileWrapper (metadata + small heap cells) verbatim.

    Returns the number of FileWrapper cells retained. Object-valued cells (nested
    cell/struct refs) and oversized cells are dropped as null cells — the decoder
    only follows the scalar value cells the ``DetectionThreshold`` path needs.
    """
    src_cells = np.asarray(f_src["#subsystem#"]["MCOS"][()]).reshape(-1)
    subsystem = g_out.create_group("#subsystem#")
    mcos = subsystem.create_dataset("MCOS", shape=(1, src_cells.size), dtype=h5py.ref_dtype)
    mcos.attrs["MATLAB_class"] = np.bytes_("FileWrapper__")
    retained = 0
    for i, ref in enumerate(src_cells):
        if not ref:
            continue
        target = f_src[ref]
        if not isinstance(target, h5py.Dataset):
            continue
        # Cell 0 is the metadata blob (kept verbatim regardless of size); the rest
        # only if they are small leaf values, not nested objects or bulk arrays.
        if i != 0 and (target.dtype == object or target.nbytes > _MCOS_CELL_MAX_BYTES):
            continue
        mcos[0, i] = _copy_leaf(store, f"mcos_{i}", target)
        retained += 1
    return retained


def _copy_channels(f_src: h5py.File, temp_out: h5py.Group, store: h5py.Group) -> int:
    """Copy ``temp/Channel`` object-reference markers into the fixture verbatim."""
    src_channel = f_src["temp"]["Channel"]
    markers = np.asarray(src_channel[()]).reshape(-1)
    channel = temp_out.create_dataset("Channel", shape=src_channel.shape, dtype=h5py.ref_dtype)
    channel.attrs["MATLAB_class"] = np.bytes_("cell")
    copied = 0
    for k, ref in enumerate(markers):
        if not ref:
            continue
        channel[k, 0] = _copy_leaf(store, f"channel_{k}", f_src[ref])
        copied += 1
    return copied


def main() -> None:
    with h5py.File(TDAT, "r") as f:
        temp = f["temp"]
        pc_ref = np.asarray(temp["ParticlesColocalized"][()]).reshape(-1)[0]
        table = np.asarray(f[pc_ref][()], dtype=np.float64)  # (17, N), MATLAB-transposed
        alpha = _scalar(temp, "DefaultAlpha")
        beta = _scalar(temp, "DefaultBeta")
        gamma = _scalar(temp, "DefaultGamma")
        channels = np.asarray(temp["ChannelsWithData"][()], dtype=np.float64).reshape(-1, 1)
        mapping_ref = _scalar(temp, "MappingReferenceChannel")
        # Mirror read_tdat's _detection_mode_code default: a source .tdat predating
        # the field falls back to mode 1 (wavelet) rather than raising KeyError.
        detection_mode = (
            _scalar(temp, "ParticleDetectionMode") if "ParticleDetectionMode" in temp else 1.0
        )

        OUT.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(OUT, "w") as g:
            refs = g.create_group("#refs#")
            coloc = refs.create_dataset("a", data=table)
            coloc.attrs["MATLAB_class"] = np.bytes_("double")
            temp_out = g.create_group("temp")
            pc = temp_out.create_dataset("ParticlesColocalized", shape=(1, 1), dtype=h5py.ref_dtype)
            pc[0, 0] = coloc.ref
            pc.attrs["MATLAB_class"] = np.bytes_("cell")
            for name, value in (
                ("DefaultAlpha", alpha),
                ("DefaultBeta", beta),
                ("DefaultGamma", gamma),
            ):
                scalar = temp_out.create_dataset(name, data=np.array([[value]], dtype=np.float64))
                scalar.attrs["MATLAB_class"] = np.bytes_("double")
            cwd = temp_out.create_dataset("ChannelsWithData", data=channels)
            cwd.attrs["MATLAB_class"] = np.bytes_("double")
            ref_ch = temp_out.create_dataset(
                "MappingReferenceChannel", data=np.array([[mapping_ref]], dtype=np.float64)
            )
            ref_ch.attrs["MATLAB_class"] = np.bytes_("double")
            # ParticleDetectionMode is a plain ``double`` leaf (findPart.m method code);
            # carry it so the committed fixture exercises read_detection_settings too.
            det_mode = temp_out.create_dataset(
                "ParticleDetectionMode", data=np.array([[detection_mode]], dtype=np.float64)
            )
            det_mode.attrs["MATLAB_class"] = np.bytes_("double")
            # Retain the MCOS Channel blob so the fixture decodes per-channel
            # DetectionThreshold (PR-C3c-decode-B) through the real access path.
            mcos_cells = _copy_mcos_subsystem(f, g, refs)
            n_channels = _copy_channels(f, temp_out, refs)

    print(f"wrote {OUT} ({OUT.stat().st_size} B)")
    print(f"  {table.shape[1]} molecules x {table.shape[0]} columns")
    print(f"  Deep-LASI factors: Alpha={alpha:g} Beta={beta:g} Gamma={gamma:g}")
    print(f"  ParticleDetectionMode: {detection_mode:g}")
    print(f"  MCOS cells retained: {mcos_cells}; Channel markers: {n_channels}")
    print(f"source .tdat size:   {TDAT.stat().st_size} B")
    print(f"source .tdat sha256: {_sha256(TDAT)}")


if __name__ == "__main__":
    main()
