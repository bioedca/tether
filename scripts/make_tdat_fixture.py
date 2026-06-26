# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the small committed TIRFdata ``.tdat`` decode fixture (M0.5 S6).

The TIRFdata ``.tdat`` decode (PRD §7.8, Appendix A/B) needs a *real-data*
fixture that exercises the MATLAB v7.3 access path, but the source ``.tdat`` is
37 MB. This tool copies only the payload :func:`tether.io.read_tdat` reads — the
real ``ParticlesColocalized`` coordinate matrix and the three
``Default{Alpha,Beta,Gamma}`` scalars — into a tiny ``.tdat``-format file,
dropping the ~37 MB of trace/patch arrays and the MCOS object blob. The output
reproduces the original access path exactly (``temp/ParticlesColocalized`` cell
-> HDF5 object reference -> ``(17, N)`` ``findColoc`` matrix in ``#refs#``), so
the committed test genuinely decodes a real file rather than a stub. The full
``.tdat`` stays external (PLAN §2.1/§2.2).

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

    print(f"wrote {OUT} ({OUT.stat().st_size} B)")
    print(f"  {table.shape[1]} molecules x {table.shape[0]} columns")
    print(f"  Deep-LASI factors: Alpha={alpha:g} Beta={beta:g} Gamma={gamma:g}")
    print(f"source .tdat size:   {TDAT.stat().st_size} B")
    print(f"source .tdat sha256: {_sha256(TDAT)}")


if __name__ == "__main__":
    main()
