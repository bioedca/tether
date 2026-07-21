# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""SMD-HDF5 interchange for the tMAVEN sidecar hand-off (PRD §7.4, Appendix D.1).

The SMD container [Greenfeld2015] is tMAVEN's on-disk trace format: a root group
(``dataset`` by convention) with ``@format='SMD'`` holding ``data/raw``
``(n_molecules, n_frames, 2)`` float64 (donor, acceptor), ``data/source_index``,
a ``sources/`` group, and an optional ``tMAVEN/`` group (``classes`` +
``pre_list``/``post_list`` analysis windows).

:func:`write_smd` reproduces the exact structure tMAVEN's own
``pysmd.save_smd_in_hdf5`` writes (so a Tether-authored SMD opens directly in the
standalone tMAVEN GUI — PRD §7.4), and additionally rides Tether's native
coordinates along in a ``dataset/tether/`` **superset** group. tMAVEN's loader
reads only ``data``/``sources``/``tMAVEN`` and rewrites only those on a save, so
the superset group is invisible to (and dropped by) a standalone tMAVEN
round-trip — the documented gap (§5, Appendix D.1) that the return-leg
intensity matcher (:mod:`tether.idealize.matcher`) closes. On a Tether→Tether
round-trip the superset group survives intact.
"""

from __future__ import annotations

import time
from ast import literal_eval
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from os import PathLike

# ``h5py`` is imported lazily inside :func:`read_smd` / :func:`write_smd` so that
# ``import tether.idealize`` (and :func:`tether.idealize.match_return_leg`, which
# is pure NumPy) does not require h5py.

#: Group attribute values tMAVEN recognises (``load_smd_in_hdf5`` accepts any case).
SMD_FORMAT = "SMD"
TMAVEN_FORMAT = "tMAVEN"
#: Conventional root group name for the single dataset in an SMD file.
DEFAULT_GROUP = "dataset"
#: ``@format`` of Tether's superset metadata group; not read by tMAVEN.
SUPERSET_FORMAT = "tether-smd-superset"
#: Monotonic version of the superset layout (distinct from the frozen .tether schema).
SUPERSET_VERSION = 1


@dataclass
class SMDData:
    """An SMD dataset in memory.

    ``raw`` is ``(n_molecules, n_frames, 2)`` float64 (donor, acceptor). The
    ``tMAVEN`` fields and the Tether superset fields are ``None`` when absent
    from the source file.
    """

    raw: np.ndarray
    source_names: list[str]
    source_index: np.ndarray
    classes: np.ndarray | None = None
    pre_list: np.ndarray | None = None
    post_list: np.ndarray | None = None
    donor_xy: np.ndarray | None = None
    acceptor_xy: np.ndarray | None = None
    molecule_keys: list[str] | None = None
    molecule_ids: list[str] | None = None
    date_created: str | None = None
    date_modified: str | None = None

    @property
    def n_molecules(self) -> int:
        """Number of molecules (traces) held in ``raw``."""
        return int(self.raw.shape[0])

    @property
    def n_frames(self) -> int:
        """Number of frames (time points) per trace."""
        return int(self.raw.shape[1])

    @property
    def n_channels(self) -> int:
        """Number of intensity channels per frame (2: donor, acceptor)."""
        return int(self.raw.shape[2])

    @property
    def has_tmaven(self) -> bool:
        """Whether tMAVEN per-trace metadata (classes/windows) is present."""
        return self.classes is not None

    @property
    def has_superset(self) -> bool:
        """Whether Tether coordinate superset metadata is present."""
        return self.donor_xy is not None


def _as_1d_int64(value, n: int, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype="int64").reshape(-1)
    if arr.shape[0] != n:
        raise ValueError(f"{name} must have length {n}, got {arr.shape[0]}")
    return arr


def write_smd(
    path: str | PathLike[str],
    raw,
    *,
    source_names: list[str] | None = None,
    source_index=None,
    classes=None,
    pre_list=None,
    post_list=None,
    donor_xy=None,
    acceptor_xy=None,
    molecule_keys: list[str] | None = None,
    molecule_ids: list[str] | None = None,
    group: str = DEFAULT_GROUP,
    description: str = "",
    date_created: str | None = None,
    date_modified: str | None = None,
    overwrite: bool = True,
) -> Path:
    """Write an SMD-HDF5 file tMAVEN can open, with optional Tether superset.

    Mirrors ``tmaven.pysmd.save_smd_in_hdf5``'s structure exactly for the
    ``data``/``sources``/``tMAVEN`` groups, so the result opens in the
    standalone tMAVEN GUI (PRD §7.4). Tether coordinates (``donor_xy``,
    ``acceptor_xy``, ``molecule_keys``, ``molecule_ids``), when supplied, are
    written to a ``<group>/tether`` superset group that tMAVEN ignores.

    Parameters
    ----------
    path:
        Destination ``.hdf5`` path (created or, if ``overwrite``, its ``group``
        replaced).
    raw:
        ``(n_molecules, n_frames, 2)`` array of (donor, acceptor) intensities.
    source_names / source_index:
        Provenance of each molecule's source file; default a single synthetic
        source with all molecules indexed to it.
    classes / pre_list / post_list:
        Optional tMAVEN per-trace integer class and analysis-window bounds. If
        any is given they are all written (defaulting the others).

    Returns
    -------
    pathlib.Path
        The written path.
    """
    import h5py

    raw = np.asarray(raw, dtype="float64")
    if raw.ndim != 3 or raw.shape[2] != 2:
        raise ValueError(f"raw must be (n_molecules, n_frames, 2); got shape {raw.shape}")
    n = raw.shape[0]

    if source_names is None:
        source_names = ["dataset"]
    if not source_names:
        raise ValueError("source_names must not be empty")
    if source_index is None:
        source_index = np.zeros(n, dtype="int64")
    else:
        source_index = _as_1d_int64(source_index, n, "source_index")
    if int(source_index.min(initial=0)) < 0:
        raise ValueError("source_index must be non-negative")
    if int(source_index.max(initial=-1)) >= len(source_names):
        raise ValueError("source_index references a source beyond source_names")

    want_tmaven = classes is not None or pre_list is not None or post_list is not None
    if want_tmaven:
        classes = (
            np.zeros(n, dtype="int64") if classes is None else _as_1d_int64(classes, n, "classes")
        )
        pre_list = (
            np.zeros(n, dtype="int64")
            if pre_list is None
            else _as_1d_int64(pre_list, n, "pre_list")
        )
        post_list = (
            np.full(n, raw.shape[1], dtype="int64")
            if post_list is None
            else _as_1d_int64(post_list, n, "post_list")
        )

    want_superset = donor_xy is not None or acceptor_xy is not None
    if want_superset:
        if donor_xy is None or acceptor_xy is None:
            raise ValueError("donor_xy and acceptor_xy must be supplied together")
        donor_xy = np.asarray(donor_xy, dtype="float64").reshape(n, 2)
        acceptor_xy = np.asarray(acceptor_xy, dtype="float64").reshape(n, 2)
    for label, keys in (("molecule_keys", molecule_keys), ("molecule_ids", molecule_ids)):
        if keys is not None and len(keys) != n:
            raise ValueError(f"{label} must have length {n}, got {len(keys)}")

    now = time.ctime()
    path = Path(path)
    with h5py.File(path, "a") as f:
        if group in f:
            if not overwrite:
                raise FileExistsError(f"group {group!r} already exists in {path}")
            del f[group]
            f.flush()

        g = f.create_group(group)
        g.attrs["format"] = SMD_FORMAT
        g.attrs["date_created"] = date_created if date_created is not None else now
        g.attrs["date_modified"] = date_modified if date_modified is not None else now

        gd = g.create_group("data")
        gd.attrs["description"] = description
        gd.create_dataset("raw", data=raw, compression="gzip")
        gd.create_dataset("source_index", data=source_index, dtype="int64", compression="gzip")

        gs = g.create_group("sources")
        gs.attrs["source_list"] = str(list(source_names))
        for i, name in enumerate(source_names):
            gsi = gs.create_group(str(i))
            gsi.attrs["source_name"] = name

        if want_tmaven:
            gt = g.create_group("tMAVEN")
            gt.attrs["format"] = TMAVEN_FORMAT
            gt.attrs["date_modified"] = date_modified if date_modified is not None else now
            gt.create_dataset("classes", data=classes, dtype="int64")
            gt.create_dataset("pre_list", data=pre_list, dtype="int64")
            gt.create_dataset("post_list", data=post_list, dtype="int64")

        if want_superset or molecule_keys is not None or molecule_ids is not None:
            gx = g.create_group("tether")
            gx.attrs["format"] = SUPERSET_FORMAT
            gx.attrs["superset_version"] = SUPERSET_VERSION
            if want_superset:
                gx.create_dataset("donor_xy", data=donor_xy)
                gx.create_dataset("acceptor_xy", data=acceptor_xy)
            str_dt = h5py.string_dtype(encoding="utf-8")
            if molecule_keys is not None:
                gx.create_dataset("molecule_key", data=list(molecule_keys), dtype=str_dt)
            if molecule_ids is not None:
                gx.create_dataset("molecule_id", data=list(molecule_ids), dtype=str_dt)

    return path


def _decode(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _read_str_dataset(group, name: str, n: int, path) -> list[str] | None:
    """Read an optional length-``n`` string dataset from ``group`` (or ``None``)."""
    if name not in group:
        return None
    values = [_decode(x) for x in group[name][:]]
    if len(values) != n:
        raise ValueError(f"{path}: tether/{name} length {len(values)} != {n}")
    return values


def read_smd(path: str | PathLike[str], group: str = DEFAULT_GROUP) -> SMDData:
    """Read an SMD-HDF5 file (Tether- or tMAVEN-authored) into :class:`SMDData`.

    Loads the ``data``/``sources``/``tMAVEN`` groups the same way tMAVEN does,
    plus the Tether ``tether/`` superset group when present. Sources are
    returned in ``source_index`` order (matching tMAVEN's reader).
    """
    import h5py

    path = Path(path)
    with h5py.File(path, "r") as f:
        if group not in f:
            raise KeyError(f"group {group!r} not found in {path}")
        g = f[group]
        fmt = _decode(g.attrs.get("format", ""))
        if fmt.lower() != "smd":
            raise ValueError(f"group {group!r} is not SMD format (got {fmt!r})")

        gd = g["data"]
        raw = gd["raw"][:].astype("float64")
        if raw.ndim != 3 or raw.shape[2] != 2:
            raise ValueError(
                f"{path}: data/raw must be (n_molecules, n_frames, 2); got {raw.shape}"
            )
        n = raw.shape[0]
        if "source_index" in gd:
            source_index = gd["source_index"][:].astype("int64").reshape(-1)
            if source_index.shape[0] != n:
                raise ValueError(f"{path}: data/source_index length {source_index.shape[0]} != {n}")
        else:
            source_index = np.zeros(n, dtype="int64")

        gs = g["sources"]
        try:
            order = np.argsort([int(k) for k in gs])
            keys = np.array(list(gs))[order]
            source_names = [_decode(gs[k].attrs["source_name"]) for k in keys]
        except (KeyError, ValueError):
            source_names = [_decode(x) for x in literal_eval(_decode(gs.attrs["source_list"]))]
        if source_index.size and (
            int(source_index.min()) < 0 or int(source_index.max()) >= len(source_names)
        ):
            raise ValueError(f"{path}: source_index out of range for {len(source_names)} sources")

        classes = pre_list = post_list = None
        if "tMAVEN" in g:
            gt = g["tMAVEN"]
            classes = gt["classes"][:].astype("int64").reshape(-1)
            pre_list = gt["pre_list"][:].astype("int64").reshape(-1)
            post_list = gt["post_list"][:].astype("int64").reshape(-1)
            for label, arr in (
                ("classes", classes),
                ("pre_list", pre_list),
                ("post_list", post_list),
            ):
                if arr.shape[0] != n:
                    raise ValueError(f"{path}: tMAVEN/{label} length {arr.shape[0]} != {n}")

        donor_xy = acceptor_xy = molecule_keys = molecule_ids = None
        if "tether" in g:
            gx = g["tether"]
            has_donor, has_acceptor = "donor_xy" in gx, "acceptor_xy" in gx
            if has_donor != has_acceptor:
                raise ValueError(f"{path}: tether superset has only one of donor_xy/acceptor_xy")
            if has_donor:
                donor_xy = gx["donor_xy"][:].astype("float64")
                acceptor_xy = gx["acceptor_xy"][:].astype("float64")
                for label, arr in (("donor_xy", donor_xy), ("acceptor_xy", acceptor_xy)):
                    if arr.shape != (n, 2):
                        raise ValueError(f"{path}: tether/{label} shape {arr.shape} != ({n}, 2)")
            molecule_keys = _read_str_dataset(gx, "molecule_key", n, path)
            molecule_ids = _read_str_dataset(gx, "molecule_id", n, path)

        return SMDData(
            raw=raw,
            source_names=source_names,
            source_index=source_index,
            classes=classes,
            pre_list=pre_list,
            post_list=post_list,
            donor_xy=donor_xy,
            acceptor_xy=acceptor_xy,
            molecule_keys=molecule_keys,
            molecule_ids=molecule_ids,
            date_created=_decode(g.attrs["date_created"]) if "date_created" in g.attrs else None,
            date_modified=_decode(g.attrs["date_modified"]) if "date_modified" in g.attrs else None,
        )
