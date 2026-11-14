# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pack the ebFRET session files of Zenodo record 13910538 into one LFS fixture (#719).

The record (Gilman, Shlosman, Samé Guerra, et al., CC-BY-4.0) holds ``Data.zip``. Below
``Data/ebFRET outputs/<figure>/<condition>/`` each of its 11 conditions has three files: the ebFRET
session (``<condition>-ebFRET.mat``, written by the MATLAB ebFRET program), a ``traces.dat`` export
and a ``summary.csv``. Only the session is read. It holds the traces that ebFRET fitted and the
result of the fit, so the input and the stored answer come from the same file. (In this record five
``traces.dat`` files do not belong to the session beside them: the two Figure 2 pairs are
exchanged, and ``MreC-F36R-MreD-traces.dat`` is a copy of ``MreC-L39E-MreD-traces.dat``. The
row and molecule counts show it.)

For each condition the fixture keeps, as one group:

``donor``, ``acceptor``
    ``(n_series, max_frames)`` float64, the session's ``series.donor`` and ``series.acceptor``,
    NaN past each series' ``length``. A series of fewer than two frames is left out
    (``n_left_out`` counts them); none is marked excluded in these sessions.
``length``, ``crop_min``, ``crop_max``
    Frames per series and the crop ebFRET fitted, as 0-based ``[crop_min, crop_max)``.
``prior_mu``, ``population``
    The stored result for the number of states the session shows (``@nstates``, the
    ``controls.ensemble.value`` of the session). ``prior_mu`` is the state means of the
    empirical-Bayes prior after the fit (``analysis(K).prior.mu``, which ebFRET's ``h_step``
    updates). ``population`` is the fraction of the expected state occupancy summed over the
    fitted series (``analysis(K).expect.z``), the quantity tMAVEN's ``frac`` is.

The script is dev-only and runs in the base environment (``scipy.io.loadmat``, ``h5py``)::

    python scripts/make_ebfret_sessions_fixture.py --source <dir-with-Data.zip>

The weekly ``reference-data`` job runs :func:`build` on the downloaded zip and compares every
array with the committed fixture (``tests/test_reference_data_download.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import zipfile
from pathlib import Path

import numpy as np

ZIP_NAME = "Data.zip"
PREFIX = "Data/ebFRET outputs/"
DOI = "10.5281/zenodo.13910538"
URL = "https://zenodo.org/records/13910538"
LICENSE = "CC-BY-4.0"
OUT = Path("tests/fixtures/large/ebfret_mrec_sessions.hdf5")
#: The arrays of each condition group, in the order :func:`read_sessions` returns them.
ARRAYS = ("donor", "acceptor", "length", "crop_min", "crop_max", "prior_mu", "population")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _session_members(archive: zipfile.ZipFile) -> list[str]:
    """The session files of the record, sorted, without the ``__MACOSX`` shadow copies."""
    return sorted(
        name
        for name in archive.namelist()
        if name.startswith(PREFIX) and name.endswith("-ebFRET.mat") and "/._" not in name
    )


def _read_session(blob: bytes) -> dict:
    """One session file -> the arrays and attributes of its fixture group."""
    from scipy.io import loadmat

    mat = loadmat(io.BytesIO(blob), squeeze_me=True, struct_as_record=False)
    controls = mat["controls"]
    nstates = int(controls.ensemble.value)
    series = np.atleast_1d(mat["series"])
    keep = [i for i, s in enumerate(series) if np.size(s.donor) >= 2]
    if any(int(series[i].exclude) for i in keep):
        raise ValueError("a series is marked excluded; this fixture layout has no field for it")

    lengths = np.array([np.size(series[i].donor) for i in keep], dtype=np.int64)
    donor = np.full((len(keep), int(lengths.max())), np.nan)
    acceptor = np.full_like(donor, np.nan)
    crop_min = np.empty(len(keep), dtype=np.int64)
    crop_max = np.empty(len(keep), dtype=np.int64)
    for row, i in enumerate(keep):
        s = series[i]
        n = lengths[row]
        donor[row, :n] = np.asarray(s.donor, dtype=np.float64)
        acceptor[row, :n] = np.asarray(s.acceptor, dtype=np.float64)
        crop_min[row] = int(s.crop.min) - 1  # MATLAB 1-based, inclusive
        crop_max[row] = int(s.crop.max)
    if np.any(crop_min < 0) or np.any(crop_max > lengths) or np.any(crop_max <= crop_min):
        raise ValueError("a crop falls outside its series")

    analysis = np.atleast_1d(mat["analysis"])[nstates - 1]
    if int(analysis.dim.states) != nstates:
        raise ValueError(f"analysis {nstates} holds {analysis.dim.states} states")
    expect = np.atleast_1d(analysis.expect)
    occupancy = sum(np.asarray(expect[i].z, dtype=np.float64) for i in keep)
    return {
        "arrays": {
            "donor": donor,
            "acceptor": acceptor,
            "length": lengths,
            "crop_min": crop_min,
            "crop_max": crop_max,
            "prior_mu": np.asarray(analysis.prior.mu, dtype=np.float64).reshape(-1),
            "population": occupancy / occupancy.sum(),
        },
        "attrs": {
            "nstates": nstates,
            "restarts": int(controls.restarts),
            "n_left_out": len(series) - len(keep),
            "mat_header": mat["__header__"].decode("ascii", "replace"),
        },
    }


def read_sessions(zip_path: Path) -> dict[str, dict]:
    """Every condition of ``Data.zip``, keyed ``<figure>/<condition>``."""
    out = {}
    with zipfile.ZipFile(zip_path) as archive:
        for member in _session_members(archive):
            figure, condition = member[len(PREFIX) :].split("/")[:2]
            blob = archive.read(member)
            entry = _read_session(blob)
            entry["attrs"].update(member=member, member_sha256=_sha256(blob))
            out[f"{figure}/{condition}"] = entry
    return out


def build(source: Path, out: Path = OUT) -> Path:
    """Write the fixture from ``source/Data.zip``; datasets carry no timestamps."""
    import h5py

    zip_path = source / ZIP_NAME
    sessions = read_sessions(zip_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(out, "w", track_order=True) as h5:
        h5.attrs["format"] = "ebfret-sessions"
        h5.attrs["doi"] = DOI
        h5.attrs["url"] = URL
        h5.attrs["license"] = LICENSE
        h5.attrs["source_zip"] = ZIP_NAME
        h5.attrs["source_sha256"] = _sha256(zip_path.read_bytes())
        for key, entry in sessions.items():
            group = h5.create_group(key, track_order=True)
            for name, value in entry["attrs"].items():
                group.attrs[name] = value
            for name in ARRAYS:
                group.create_dataset(
                    name,
                    data=entry["arrays"][name],
                    compression="gzip",
                    shuffle=True,
                    track_times=False,
                )
            print(f"{key}: {group['length'].size} series, K={group.attrs['nstates']}")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="directory with Data.zip")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    build(args.source, args.out)


if __name__ == "__main__":
    main()
