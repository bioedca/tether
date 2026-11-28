# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""A laboratory cellSens ``.vsi`` movie through the Bio-Formats route (#907, ADR-0147, #887).

No redistributable ``.vsi`` movie is known, so this check runs on the maintainer's
workstation (ADR-0071; maintainer decision D2 of #907). The mapping file names each movie
under a ``vsi-NN`` id with two roles: ``vsi``, the ``.vsi`` file (its ``_<name>_`` folder of
``.ets`` frames beside it), and ``tiff_export``, a TIFF made from the ``.vsi`` in ImageJ (the
Bio-Formats importer, then a turn in an ImageJ batch step; cellSens itself exports no TIFF,
maintainer statement of 2026-11-25). The role keeps its name. The run needs
``$TETHER_BIOFORMATS_PYTHON`` as well (``docs/install.md``, "The Bio-Formats stack"); the runner
passes its own environment on.

For each movie the check opens the ``.vsi`` through the adapter registry and holds it to the
conformance rules of #901 (geometry, frame-range reads that do not load the movie, values that
agree between ranges, metadata with an origin, a clean close). It then compares the direct
frames with the export under each of the eight rotations and mirror images of the frame, and
records which one matches, if any: that is the number the rotation finding of #887 needs. The
comparison is recorded, not asserted, because the export route is the thing under test.

Where the export matches under one of them, the check also extracts the ``.vsi`` itself
(``tether extract``, through the spool of #1388) and the export turned back into the
``.vsi``'s orientation, and records whether the two give the same traces, bit for bit (#1398).
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

pytestmark = pytest.mark.workstation

np = pytest.importorskip("numpy")

from tether.io.adapters import ORIGINS, MovieSource, open_movie_source, select_adapter  # noqa: E402
from tether.io.bioformats import BIOFORMATS_ADAPTER, bioformats_python  # noqa: E402

PREFIX = "vsi-"
VSI, EXPORT = "vsi", "tiff_export"
#: The frames compared with the export: enough to see a mismatch, few enough to stay quick.
COMPARED = 20

#: The eight symmetries of a frame: (quarter turns counter-clockwise, then a left-right mirror).
TRANSFORMS = [(k, flip) for flip in (False, True) for k in range(4)]


def _transform(frames: np.ndarray, k: int, flip: bool) -> np.ndarray:
    out = np.rot90(frames, k=k, axes=(1, 2))
    return out[:, :, ::-1] if flip else out


def _untransform(frames: np.ndarray, k: int, flip: bool) -> np.ndarray:
    """The inverse of [`_transform`][]: from the export's orientation back to the ``.vsi``'s."""
    out = frames[:, :, ::-1] if flip else frames
    return np.rot90(out, k=-k, axes=(1, 2))


#: Frames turned and written at a time, so the export is never held in memory whole.
_CHUNK = 64


def _extracted_traces_match(vsi: Path, export: Path, k: int, flip: bool, tmp: Path) -> dict:
    """Extract the ``.vsi`` and the export in the ``.vsi``'s orientation; compare the traces."""
    from tether.cli import main  # noqa: PLC0415
    from tether.imaging.extract import read_molecules, read_traces  # noqa: PLC0415

    tifffile = pytest.importorskip("tifffile")
    turned = tmp / "export-turned.tif"
    with open_movie_source(export) as source, tifffile.TiffWriter(turned) as writer:
        for start in range(0, source.n_frames, _CHUNK):
            stop = min(start + _CHUNK, source.n_frames)
            block = _untransform(np.asarray(source.read_frames(start, stop)), k, flip)
            for frame in block:
                writer.write(np.ascontiguousarray(frame), contiguous=True)
    out_vsi, out_export = tmp / "vsi.tether", tmp / "export.tether"
    rc_vsi = main(["extract", str(vsi), "-o", str(out_vsi)])
    rc_export = main(["extract", str(turned), "-o", str(out_export)])
    numbers = {"extract_vsi_rc": rc_vsi, "extract_export_rc": rc_export}
    if rc_vsi == 0 and rc_export == 0:
        a, b = read_traces(out_vsi), read_traces(out_export)
        same = sorted(a) == sorted(b) and all(np.array_equal(a[n], b[n]) for n in a)
        numbers["molecules_vsi"] = len(read_molecules(out_vsi))
        numbers["molecules_export"] = len(read_molecules(out_export))
        numbers["traces_match"] = int(same)
    return numbers


def _ids(private_data) -> list[str]:
    return [i for i in private_data.ids if i.startswith(PREFIX) and VSI in private_data.roles(i)]


def test_a_vsi_movie_conforms_and_is_compared_with_its_export(private_data, tmp_path) -> None:
    ids = _ids(private_data)
    if not ids:
        pytest.skip(f"no {PREFIX}NN id with the role {VSI!r} in the mapping")
    if bioformats_python() is None:
        pytest.skip("the Bio-Formats stack is not installed ($TETHER_BIOFORMATS_PYTHON)")
    for neutral_id in ids:
        path = private_data.path(neutral_id, VSI)
        assert select_adapter(path) is BIOFORMATS_ADAPTER
        with open_movie_source(path) as source:
            assert isinstance(source, MovieSource)
            n, h, w = source.shape
            assert n == source.n_frames >= 2 and h > 0 and w > 0
            pixel = np.dtype(source.pixel_type)
            assert pixel.kind in "uif"
            for name in ("frame_time", "pixel_size", "channels"):
                assert getattr(source.metadata, name).origin in ORIGINS, name
            with pytest.raises(IndexError):
                source.read_frames(0, n + 1)
            count = min(n, COMPARED)
            direct = np.asarray(source.read_frames(0, count))
            assert direct.shape == (count, h, w)
            np.testing.assert_array_equal(source.read_frames(1, 2), direct[1:2])
            tracemalloc.start()
            try:
                source.read_frames(0, 2)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            frame_bytes = h * w * pixel.itemsize
            # Two frames and the pipe's overhead, far below the movie (a full load is n frames).
            assert peak < max(n * frame_bytes // 4, 4 * frame_bytes + 65536), peak
            frame_time = source.metadata.frame_time.value
        source.close()
        with pytest.raises(ValueError, match="closed"):
            source.read_frames(0, 1)

        numbers: dict[str, float] = {
            "frames": n,
            "height": h,
            "width": w,
            "frame_time_known": int(frame_time is not None),
        }
        if EXPORT in private_data.roles(neutral_id):
            with open_movie_source(private_data.path(neutral_id, EXPORT)) as export:
                numbers["export_frames"] = export.n_frames
                theirs = np.asarray(export.read_frames(0, min(export.n_frames, count)))
            match = -1
            for index, (k, flip) in enumerate(TRANSFORMS):
                ours = _transform(direct[: theirs.shape[0]], k, flip)
                if ours.shape == theirs.shape and np.array_equal(ours, theirs):
                    match = index
                    break
            numbers["export_match"] = int(match >= 0)
            numbers["export_quarter_turns"] = TRANSFORMS[match][0] if match >= 0 else -1
            numbers["export_mirrored"] = int(TRANSFORMS[match][1]) if match >= 0 else -1
            if match >= 0:
                work = tmp_path / neutral_id
                work.mkdir()
                k, flip = TRANSFORMS[match]
                numbers.update(
                    _extracted_traces_match(
                        path, private_data.path(neutral_id, EXPORT), k, flip, work
                    )
                )
        private_data.record(neutral_id, **numbers)
