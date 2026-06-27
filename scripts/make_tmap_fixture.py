# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the small committed ``.tmap`` registration fixture (M0.5 S6 PR-2).

The registration validation (PRD Appendix E Stages 6-10; §9 M0.5(b)) compares a
*native* degree-2 polynomial fit against Deep-LASI's stored map. The source
``.tmap`` is a ~3.7 MB MATLAB v5 MAT-file dominated by a per-channel result image;
the registration *transforms* themselves are tiny. We run the real decoder
(:func:`tether.imaging.register.read_tmap`) on the external ``.tmap`` and commit
only the decoded degree-2 coefficients + normalisation affines + bead control
points (a few KB), with provenance. The committed ``.tdat`` colocalization slice
provides matched molecule pairs, so CI validates the decoded coefficients
*independently* (native-vs-.tmap RMS) without the bulky source file.

This script also prints the §9 M0.5(b) numbers it measures (native registration
RMS vs the ``.tmap`` and the fraction of Deep-LASI molecules where the native fit
reproduces the imported map within 1 px), so the derivation itself proves the
gate passes on real data before the fixture is committed.

Regenerate with::

    uv run --no-project --with scipy --with numpy --with h5py \
        python scripts/make_tmap_fixture.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

# Use the in-tree package without installing (no local base env).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tether.imaging.register import (  # noqa: E402
    TmapChannel,
    fit_polynomial_transform,
    point_rms,
    read_tmap,
)
from tether.io import read_tdat  # noqa: E402


def _find_example_data() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data"
        if candidate.is_dir():
            return candidate
    raise SystemExit("could not locate the external 'example-data' sibling directory")


SRC = _find_example_data() / "bla-uckopsb-tbox-video10"
TMAP = SRC / "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_20250718_2025-07-18_13-40.tmap"
TDAT = SRC / "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif2025-07-21_00-00.tdat"

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tmap_coeffs.npz"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _flatten(channel: TmapChannel) -> dict[str, np.ndarray]:
    cid = channel.channel_id
    out = {
        f"c{cid}_crop": np.asarray(channel.crop),
        f"c{cid}_map_particles": np.asarray(channel.map_particles, dtype=np.float64),
    }
    for name, transform in (
        ("ref_to_channel", channel.ref_to_channel),
        ("channel_to_ref", channel.channel_to_ref),
    ):
        out[f"c{cid}_{name}_a"] = transform.a
        out[f"c{cid}_{name}_b"] = transform.b
        out[f"c{cid}_{name}_norm_xy"] = transform.norm_xy
        out[f"c{cid}_{name}_norm_uv"] = transform.norm_uv
    return out


def main() -> None:
    channels = read_tmap(TMAP)
    reference_id = min(channels)  # the donor half (ChannelID 1); its maps are identity

    arrays: dict[str, np.ndarray] = {
        "channel_ids": np.array(sorted(channels), dtype=np.int64),
        "reference_channel": np.array(reference_id, dtype=np.int64),
    }
    for ch in channels.values():
        arrays.update(_flatten(ch))

    # Structural sanity: the reference channel's map is identity; a non-reference
    # forward+inverse compose to ~identity over a grid (independently fitted).
    rng = np.random.RandomState(0)
    grid = rng.uniform([10, 10], [250, 500], (500, 2))
    ref = channels[reference_id]
    print(
        f"reference channel {reference_id} identity RMS: "
        f"{point_rms(ref.reference_to_channel(grid), grid):.2e} px"
    )
    for cid, ch in channels.items():
        if cid == reference_id:
            continue
        roundtrip = point_rms(ch.channel_to_reference(ch.reference_to_channel(grid)), grid)
        print(f"channel {cid} ref->ch->ref round-trip RMS: {roundtrip:.2e} px")

    # §9 M0.5(b) numbers on the committed .tdat molecule pairs. The native fit maps
    # donor(reference) -> acceptor(channel); the .tmap's ref_to_channel does the
    # same. Their agreement at the Deep-LASI molecule positions is the gate.
    tdat = read_tdat(TDAT)
    coords = tdat.colocalization.coords
    donor = coords[tdat.reference_channel]  # 0-based [x, y] in the reference frame
    print(f"\ncolocalized molecules in .tdat: {len(donor)}")
    for cid, ch in channels.items():
        if cid == reference_id:
            continue
        acceptor = coords[cid - 1]
        native = fit_polynomial_transform(donor, acceptor)
        native_pred = native.apply(donor)
        tmap_pred = ch.reference_to_channel(donor)
        rms = point_rms(native_pred, tmap_pred)
        within = np.linalg.norm(native_pred - tmap_pred, axis=1) <= 1.0
        coloc = np.median(np.linalg.norm(tmap_pred - acceptor, axis=1))
        # NB: `within` measures native-fit-vs-imported-.tmap transform agreement at
        # the molecule positions (the registration-faithfulness gate), NOT
        # colocalization recall. The .tmap's own residual to the actual acceptor
        # molecules (coloc, median > 1 px) is the recall-flavoured number, which
        # needs the M1 detection+colocalization pipeline to close.
        print(
            f"channel {cid}: native-vs-.tmap RMS={rms:.3f}px  "
            f"native-matches-.tmap-within-1px={within.mean():.3f}  "
            f"(.tmap->molecule median={coloc:.3f}px)"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, **arrays)
    print(f"\nwrote {OUT} ({OUT.stat().st_size} B)")
    print(f"source .tmap sha256: {_sha256(TMAP)}")
    print(f"source .tmap size:   {TMAP.stat().st_size} B")


if __name__ == "__main__":
    main()
