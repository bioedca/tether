# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the small committed Deep-LASI ``.mat`` / ``.txt`` reader fixtures (M1 S9).

The M1 extraction-vs-Deep-LASI acceptance oracle (PRD §9 M1, §8 NFR-VALID (a))
reads Deep-LASI's own export as ground truth via :mod:`tether.io.deeplasi`. The
full exports are large (≈ 9 MB ``.mat`` + ≈ 7.7 MB ``.txt``) and stay external
(PLAN §2.1/§2.2); this script derives a tiny, plain-git slice of each so the
required ``test`` matrix exercises the reader without the external files:

* ``tests/fixtures/deeplasi_export_slice.mat`` — the first ``N_MOL`` molecules ×
  first ``N_FRAMES`` frames of ``fret_pairs`` + the six raw/corrected/background
  trace arrays, re-saved as a compressed MATLAB **v5** ``.mat`` (the on-disk
  format of the real export). The real ``movie_name`` (the source-movie
  *filename*) is committed verbatim; the real ``movie_path`` is an absolute
  workstation *directory* and is **redacted** to a placeholder (not committed).
* ``tests/fixtures/deeplasi_traces_slice.txt`` — the matching first ``N_FRAMES``
  rows × first ``2·N_MOL`` columns of the ``…-donc-accc-w.txt`` (the same
  molecules, donor/acceptor interleaved, the source's 5-decimal text precision).

The two slices are mutually consistent by construction (both derive from the same
molecules/frames of the same acquisition), so the committed reader test can assert
the ``.txt`` corrected traces equal the ``.mat`` ``donc`` / ``accc`` to the text
rounding — the same property the data-present test locks on the full files.

Regenerate with::

    uv run --no-project --with scipy --with numpy \
        python scripts/make_deeplasi_fixture.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import scipy.io as sio

N_MOL = 4  # molecules kept (-> 8 interleaved .txt columns)
N_FRAMES = 80  # frames kept

_TRACE_FIELDS = ("don", "acc", "donc", "accc", "bdon", "bacc")


def _find_example_data() -> Path:
    """Locate the read-only ``example-data`` sibling by walking up from here."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "example-data"
        if candidate.is_dir():
            return candidate
    raise SystemExit("could not locate the external 'example-data' sibling directory")


SRC = _find_example_data() / "bla-uckopsb-tbox-video10"
MAT = SRC / "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"
TXT = SRC / "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010-donc-accc-w.txt"
MOVIE_NAME = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"  # real ``movie_name`` filename
REDACTED_MOVIE_PATH = "<redacted-directory>"  # the real ``movie_path`` is an absolute path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
OUT_MAT = FIXTURES / "deeplasi_export_slice.mat"
OUT_TXT = FIXTURES / "deeplasi_traces_slice.txt"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    mat = sio.loadmat(
        str(MAT),
        variable_names=["fret_pairs", *_TRACE_FIELDS, "exportedby"],
        squeeze_me=False,
        struct_as_record=True,
    )
    slice_mat: dict[str, object] = {
        "fret_pairs": np.asarray(mat["fret_pairs"], dtype=np.float64)[:N_MOL],
        # The real movie *filename* (committed verbatim); the real ``movie_path``
        # is an absolute workstation *directory* — redacted, not committed.
        "movie_name": MOVIE_NAME,
        "movie_path": REDACTED_MOVIE_PATH,
        "exportedby": str(np.asarray(mat["exportedby"]).ravel()[0]),
    }
    for key in _TRACE_FIELDS:
        slice_mat[key] = np.asarray(mat[key], dtype=np.float64)[:N_MOL, :N_FRAMES]

    FIXTURES.mkdir(parents=True, exist_ok=True)
    sio.savemat(str(OUT_MAT), slice_mat, format="5", do_compression=True)

    # First N_FRAMES rows, first 2*N_MOL columns of the interleaved .txt.
    txt = np.loadtxt(TXT, dtype=np.float64, max_rows=N_FRAMES, usecols=range(2 * N_MOL))
    np.savetxt(OUT_TXT, txt, fmt="%.5f")

    print(f"wrote {OUT_MAT} ({OUT_MAT.stat().st_size} B)")
    print(f"wrote {OUT_TXT} ({OUT_TXT.stat().st_size} B)")
    print(f"source .mat sha256: {_sha256(MAT)}")
    print(f"source .txt sha256: {_sha256(TXT)}")
    print(f"slice: {N_MOL} molecules x {N_FRAMES} frames")


if __name__ == "__main__":
    main()
