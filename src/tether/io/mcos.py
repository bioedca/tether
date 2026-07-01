# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode MATLAB v7.3 MCOS objects from the ``#subsystem#/MCOS`` ``FileWrapper__`` blob.

A MATLAB v7.3 ``.mat``/``.tdat`` stores genuine class instances (MATLAB Class
Object System — MCOS — objects) in an opaque ``#subsystem#/MCOS`` ``FileWrapper__``
cell array, referenced from the data tree by *object-reference markers*: a
``[0xDD000000, ndims, dims…, object_id, class_id]`` ``uint32`` vector carrying an
HDF5 ``MATLAB_class`` attribute naming the class. The bulk numeric leaves of a
``.tdat`` sit in plain ``#refs#`` datasets and are read without this module
(:mod:`tether.io.tdat`), but a handful of payloads M1 import needs — here the
per-channel :class:`~tether.io.tdat.TdatDetectionSettings` ``DetectionThreshold``
(``TIRFdata.m:63``, "% of max") — are MCOS object *properties*, reachable only
through the ``FileWrapper__`` blob.

This decoder resolves a **named property of a given object to its stored value
cell** — enough for the scalar factors Tether reads — without materialising the
whole object graph. The ``FileWrapper__`` metadata format is undocumented by
MathWorks; the layout below was reverse-engineered against the real UCKOPSB
``.tdat`` and cross-checked against independent anchors (each channel's colour
index ``'G'``/``'R'``, its crop rectangle, and empty rotation/flip — all matching
the companion ``.tmap`` decoded in :mod:`tether.io.register`).

FileWrapper metadata layout (the FileWrapper's first cell, a ``uint8`` blob; all
values little-endian ``uint32`` unless noted):

* **8-word header** ``[version, n_names, off1, off2, off3, off4, off5, off6]`` —
  the ``off*`` are byte offsets into the blob delimiting the regions below.
* **names** — NUL-terminated ASCII field/class names in ``[32 : off1]``. The two
  short leading tokens are a binary sub-header (not names); the following
  ``n_names`` tokens are the **1-based** name table (field/class id → name).
* **class table** — ``[off1 : off2]``, 4 ``uint32`` per class (row 0 is the null
  class); a class's name is ``names[col1]``.
* **type-1 property segments** — ``[off2 : off3]`` (unused for the properties
  read here; those objects reference type-2 segments).
* **object table** — ``[off3 : off4]``, 6 ``uint32`` per object (row 0 is the null
  object): ``[class_id, 0, 0, type1_seg, type2_seg, dependency_id]`` — object
  ``j`` is table row ``j``; its properties live in type-2 segment ``type2_seg``.
* **type-2 property segments** — ``[off4 : off5]``: a 1-word skip, then one
  segment per referenced object (segment 0 is the null object). Each segment is
  ``[n_props, n_props × (field_id, ptype, value)]``, padded to an 8-byte
  boundary. ``field_id`` is 1-based into the name table.

A property's stored value (for ``ptype == 1``, a heap reference) lives in
FileWrapper cell ``value + 2`` (cell 0 is the metadata blob; cell 1 is a reserved
slot), which dereferences to the plain ``#refs#`` dataset holding the value.
"""

from __future__ import annotations

import h5py
import numpy as np

__all__ = ["McosDecoder", "object_reference_id"]

_OBJECT_REF_MARKER = 0xDD000000  # first word of an MCOS object-reference vector
_HEADER_WORDS = 8  # 8 uint32 metadata header words
_NAMES_START = _HEADER_WORDS * 4  # names region begins at byte 32
_NAME_HEADER_TOKENS = 2  # two binary tokens precede the field-name strings
_OBJECT_ROW = 6  # uint32 per object-table row
_TYPE2_SKIP = 1  # leading skip word before the first type-2 segment
_PTYPE_HEAP = 1  # property stored as a FileWrapper heap-cell reference
_VALUE_CELL_OFFSET = 2  # heap value index -> FileWrapper cell (value + 2)


def object_reference_id(marker: object) -> int:
    """Return the MCOS object id from an object-reference marker vector.

    ``marker`` is the ``[0xDD000000, ndims, dim1…, obj_id…, class_id]`` ``uint32``
    vector stored where a MATLAB v7.3 file references an MCOS object. Only scalar
    (1×1) object references are read here, so the first object id is returned.
    Raises :class:`ValueError` if ``marker`` is not a well-formed reference.
    """
    m = np.asarray(marker).reshape(-1).astype(np.int64)
    if m.size < 1 or int(m[0]) != _OBJECT_REF_MARKER:
        first = int(m[0]) if m.size else None
        raise ValueError(f"not an MCOS object reference (first word {first!r})")
    ndims = int(m[1]) if m.size > 1 else 0
    # header word + ndims + dims + at least one object id + class id
    if ndims < 2 or m.size < 2 + ndims + 2:
        raise ValueError(f"malformed MCOS object reference (ndims={ndims}, len={m.size})")
    return int(m[2 + ndims])  # first object id, immediately after the dims


class McosDecoder:
    """Resolve named properties of MCOS objects in a MATLAB v7.3 ``FileWrapper__``.

    Construct via :meth:`from_file` (returns ``None`` when the file carries no MCOS
    subsystem). The decoder holds a reference to the open :class:`h5py.File`; use it
    only while that file is open.
    """

    def __init__(self, file: h5py.File, cells: np.ndarray, meta: np.ndarray) -> None:
        self._file = file
        self._cells = cells
        self._parse_metadata(meta)

    @classmethod
    def from_file(cls, file: h5py.File) -> McosDecoder | None:
        """Build a decoder for ``file``'s MCOS subsystem, or ``None`` if it has none.

        A ``.tdat`` slimmed to plain leaves (no ``#subsystem#/MCOS``) yields ``None``
        so callers cleanly fall back to defaults rather than crash.
        """
        sub = file.get("#subsystem#")
        if sub is None or "MCOS" not in sub:
            return None
        fw = sub["MCOS"]
        cells = np.asarray(fw[()]).reshape(-1)
        if cells.size == 0 or not cells[0]:
            return None
        meta = np.asarray(file[cells[0]][()]).reshape(-1).astype(np.uint8)
        return cls(file, cells, meta)

    def _parse_metadata(self, meta: np.ndarray) -> None:
        words = meta[: (meta.size // 4) * 4].view("<u4").astype(np.int64)
        if words.size < _HEADER_WORDS:
            raise ValueError("MCOS metadata too short for its header")
        n_names = int(words[1])
        offsets = [int(x) for x in words[2:_HEADER_WORDS]]  # 6 region byte-offsets
        if n_names <= 0 or offsets[0] <= _NAMES_START or offsets[3] > meta.size:
            raise ValueError("MCOS metadata header offsets are out of range")

        # Name table: the two leading binary tokens are a sub-header, not names.
        tokens = [t for t in meta[_NAMES_START : offsets[0]].tobytes().split(b"\x00") if t]
        names = [t.decode("ascii", "replace") for t in tokens[_NAME_HEADER_TOKENS:]]
        if len(names) < n_names:
            raise ValueError(f"MCOS name table has {len(names)} names, header declares {n_names}")
        self._field_id = {name: i + 1 for i, name in enumerate(names[:n_names])}  # 1-based

        # Object table: row j -> object j; column 4 is its type-2 property segment.
        obj = words[offsets[2] // 4 : offsets[3] // 4].reshape(-1, _OBJECT_ROW)
        self._object_segment = {j: int(obj[j, 4]) for j in range(obj.shape[0])}

        # Type-2 property segments.
        self._segments = self._parse_segments(words[offsets[3] // 4 : offsets[4] // 4].tolist())

    @staticmethod
    def _parse_segments(words: list[int]) -> list[list[tuple[int, int, int]]]:
        """Split the type-2 region into per-segment ``(field_id, ptype, value)`` lists."""
        segments: list[list[tuple[int, int, int]]] = []
        i = _TYPE2_SKIP
        n = len(words)
        while i < n:
            n_props = words[i]
            i += 1
            if n_props < 0 or i + 3 * n_props > n:
                break
            triples = [
                (words[i + 3 * k], words[i + 3 * k + 1], words[i + 3 * k + 2])
                for k in range(n_props)
            ]
            i += 3 * n_props
            segments.append(triples)
            if i % 2 == 1:  # pad to the 8-byte (2-word) segment boundary
                i += 1
        return segments

    def property_dataset(self, object_id: int, field_name: str) -> h5py.Dataset | None:
        """Return the ``#refs#`` dataset holding ``object_id``'s ``field_name``, or ``None``.

        ``None`` when the object or field is absent, the property is not a heap
        reference (``ptype != 1``), its value cell is a null reference, or the value
        is a nested object/struct (an HDF5 group rather than a leaf dataset) — i.e.
        the property has no leaf value to read.
        """
        field_id = self._field_id.get(field_name)
        if field_id is None:
            return None
        segment_index = self._object_segment.get(object_id)
        if segment_index is None or not 0 <= segment_index < len(self._segments):
            return None
        for fid, ptype, value in self._segments[segment_index]:
            if fid != field_id:
                continue
            if ptype != _PTYPE_HEAP:
                return None
            cell = value + _VALUE_CELL_OFFSET
            if not 0 <= cell < self._cells.size or not self._cells[cell]:
                return None
            target = self._file[self._cells[cell]]
            # A property whose value is a nested MCOS object/struct/cell resolves to
            # an HDF5 group, not a leaf dataset; treat it as "no readable value"
            # rather than let a group dereference crash property_scalar.
            return target if isinstance(target, h5py.Dataset) else None
        return None

    def property_scalar(self, object_id: int, field_name: str) -> float | None:
        """Return ``object_id``'s ``field_name`` as a finite scalar ``float``, or ``None``.

        ``None`` when the property is absent/unset (see :meth:`property_dataset`) or
        is not a single finite number — an empty ``[]`` default, a non-numeric, or a
        non-scalar value never yields a bogus threshold.
        """
        dataset = self.property_dataset(object_id, field_name)
        if dataset is None:
            return None
        array = np.asarray(dataset[()]).reshape(-1)
        if array.size != 1 or not np.issubdtype(array.dtype, np.number):
            return None
        value = float(array[0])
        return value if np.isfinite(value) else None
