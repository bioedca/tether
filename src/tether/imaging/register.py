# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dual-view channel registration (PRD Appendix E Stages 6-10; §11.1/§11.2; M0.5 S6).

Tether splits one camera frame into a donor (reference) and an acceptor half and
must map a coordinate from one half onto the other. Deep-LASI persists that map in
a ``.tmap`` file as MATLAB ``images.geotrans.PolynomialTransformation2D`` objects;
Tether validates a *native* polynomial fit against it.

This module provides the thin M0.5 preview of that pipeline (the full
bead-detection -> phase-correlation prealign -> nearest-neighbour pairing -> fit
pipeline is M1 S5/S6):

* :class:`PolyTransform2D` -- a degree-2 2-D polynomial warp in the exact MATLAB
  form (per-output coefficient vectors ``A`` (x) and ``B`` (y) in the basis
  ``[1, x, y, x*y, x**2, y**2]`` with input/output normalisation affines), shared
  by both the decoded ``.tmap`` transform and the native fit so a residual is a
  like-for-like point comparison;
* :func:`fit_polynomial_transform` -- a normalised least-squares degree-2 fit from
  matched control points (mirrors ``fitgeotrans(...,'polynomial',2)``);
* :func:`read_tmap` -- decode a Deep-LASI ``.tmap`` (a classic MATLAB v5 MAT-file
  whose transform coefficients live in the MCOS ``__function_workspace__`` blob);
* :func:`point_rms` -- the RMS of per-point Euclidean residuals, the registration
  quality number (§9 M0.5(b): native RMS <= 0.5 px vs the ``.tmap``).

Coordinate convention follows the rest of :mod:`tether.imaging`: points are
``(N, 2)`` arrays of ``[x, y] = [col, row]``. ``PolyTransform2D`` evaluates the
raw polynomial in whatever frame it was built; the decoded ``.tmap`` transforms
are in MATLAB 1-based pixel coordinates, so :class:`TmapChannel` exposes
``reference_to_channel``/``channel_to_reference`` helpers that convert Tether's
0-based ``[x, y]`` across that boundary (PRD §11.1).

Reference: Deep-LASI ``mapping/createMap.m``, ``mapping/createMapPhaseCorr.m``,
``mapping/findColoc.m``, ``classes/TIRFdata.m`` (read-only sibling).
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import scipy.io as sio

__all__ = [
    "PolyTransform2D",
    "TmapChannel",
    "fit_polynomial_transform",
    "point_rms",
    "poly_basis_deg2",
    "read_tmap",
]


def poly_basis_deg2(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Degree-2 polynomial design matrix in MATLAB's term order.

    Returns the ``(N, 6)`` basis ``[1, x, y, x*y, x**2, y**2]`` used by
    ``images.geotrans.PolynomialTransformation2D`` (and reproduced by the native
    fit). The term order is load-bearing: it is the order the decoded ``.tmap``
    ``A``/``B`` coefficient vectors are stored in.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    return np.stack([np.ones_like(x), x, y, x * y, x * x, y * y], axis=1)


@dataclass(frozen=True)
class PolyTransform2D:
    """A degree-2 2-D polynomial geometric transform.

    Evaluates ``out = denorm(P(norm(pt)))`` where ``norm`` applies
    ``inv(norm_xy)`` to the input point, ``P`` is the degree-2 polynomial with
    per-output coefficient vectors ``A`` (output x) and ``B`` (output y) in the
    :func:`poly_basis_deg2` basis, and ``denorm`` applies ``norm_uv`` to the
    polynomial output. ``norm_xy``/``norm_uv`` are ``3x3`` affines in MATLAB's
    post-multiply convention (``[x, y, 1] @ M``); identity normalisation reduces
    the transform to a plain polynomial in the input frame.
    """

    a: np.ndarray  # (6,) output-x coefficients
    b: np.ndarray  # (6,) output-y coefficients
    norm_xy: np.ndarray  # (3, 3) input normalisation affine
    norm_uv: np.ndarray  # (3, 3) output denormalisation affine

    def __post_init__(self) -> None:
        for name, arr, shape in (
            ("a", self.a, (6,)),
            ("b", self.b, (6,)),
            ("norm_xy", self.norm_xy, (3, 3)),
            ("norm_uv", self.norm_uv, (3, 3)),
        ):
            if np.asarray(arr).shape != shape:
                raise ValueError(f"PolyTransform2D.{name} must have shape {shape}")

    def apply(self, points: np.ndarray) -> np.ndarray:
        """Map ``(N, 2)`` ``[x, y]`` points through the transform (returns ``(N, 2)``)."""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise ValueError("points must be an (N, 2) array of [x, y]")
        homog = np.hstack([pts, np.ones((len(pts), 1))])
        xn = (homog @ np.linalg.inv(self.norm_xy))[:, :2]  # pixel -> normalised input
        terms = poly_basis_deg2(xn[:, 0], xn[:, 1])
        uv = np.stack([terms @ self.a, terms @ self.b], axis=1)  # normalised output
        homog_uv = np.hstack([uv, np.ones((len(uv), 1))])
        return (homog_uv @ self.norm_uv)[:, :2]  # normalised -> pixel


def fit_polynomial_transform(
    src: np.ndarray, dst: np.ndarray, *, normalize: bool = True
) -> PolyTransform2D:
    """Least-squares degree-2 polynomial fit mapping ``src`` -> ``dst``.

    ``src`` and ``dst`` are matched ``(N, 2)`` ``[x, y]`` control points (e.g. the
    colocalized molecule pairs from a ``.tdat``). Mirrors MATLAB
    ``fitgeotrans(src, dst, 'polynomial', 2)``: by default the input points are
    centred/scaled before the fit (``normalize=True``) for conditioning, exactly
    as ``images.geotrans.PolynomialTransformation2D`` does, with the normalisation
    folded into ``norm_xy`` so :meth:`PolyTransform2D.apply` consumes raw pixels.
    The output stays in pixel coordinates (``norm_uv`` is identity), so the fit
    lives in whatever frame ``src``/``dst`` are given in.

    A degree-2 fit needs at least 6 non-degenerate control points.
    """
    src = np.atleast_2d(np.asarray(src, dtype=np.float64))
    dst = np.atleast_2d(np.asarray(dst, dtype=np.float64))
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2:
        raise ValueError("src and dst must be matching (N, 2) arrays of [x, y]")
    if len(src) < 6:
        raise ValueError(f"degree-2 fit needs >= 6 control points, got {len(src)}")

    if normalize:
        mean = src.mean(axis=0)
        scale = src.std(axis=0)
        scale = np.where(scale > 0, scale, 1.0)  # guard a degenerate (collinear) axis
        xn = (src - mean) / scale
        # norm_xy maps normalised -> pixel ([x, y, 1] @ norm_xy); its inverse, used
        # in apply(), maps pixel -> normalised: (pixel - mean) / scale.
        norm_xy = np.array([[scale[0], 0.0, 0.0], [0.0, scale[1], 0.0], [mean[0], mean[1], 1.0]])
    else:
        xn = src
        norm_xy = np.eye(3)

    terms = poly_basis_deg2(xn[:, 0], xn[:, 1])
    a, *_ = np.linalg.lstsq(terms, dst[:, 0], rcond=None)
    b, *_ = np.linalg.lstsq(terms, dst[:, 1], rcond=None)
    return PolyTransform2D(a=a, b=b, norm_xy=norm_xy, norm_uv=np.eye(3))


def point_rms(a: np.ndarray, b: np.ndarray) -> float:
    """RMS of per-point Euclidean residuals between two ``(N, 2)`` point sets."""
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    b = np.atleast_2d(np.asarray(b, dtype=np.float64))
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2:
        raise ValueError("a and b must be matching (N, 2) arrays of [x, y]")
    return float(np.sqrt(np.mean(np.sum((a - b) ** 2, axis=1))))


@dataclass(frozen=True)
class TmapChannel:
    """One channel's decoded ``.tmap`` registration (MATLAB 1-based pixel frame).

    MATLAB's ``images.geotrans.PolynomialTransformation2D`` only implements
    ``transformPointsInverse``, so the stored coefficients evaluate in the
    *inverse* direction relative to the field name (deeplasi
    ``classes/TIRFdata.m``): the decoded ``MapToReference`` actually maps
    reference -> this channel (:attr:`ref_to_channel`) and ``MapFromReference``
    maps this channel -> reference (:attr:`channel_to_ref`). The two are
    independently fitted, not algebraic inverses. Transforms are in MATLAB
    1-based pixel coordinates; the :meth:`reference_to_channel` /
    :meth:`channel_to_reference` helpers accept and return Tether 0-based
    ``[x, y]`` and handle that boundary (PRD §11.1).
    """

    channel_id: int
    crop: np.ndarray  # the channel's crop rect, as stored in the .tmap
    map_particles: np.ndarray  # (M, 2) bead control points, as stored
    ref_to_channel: PolyTransform2D  # decoded MapToReference; apply: reference -> channel
    channel_to_ref: PolyTransform2D  # decoded MapFromReference; apply: channel -> reference

    def reference_to_channel(self, points0: np.ndarray) -> np.ndarray:
        """Map 0-based ``[x, y]`` from the reference channel into this channel (0-based)."""
        return self.ref_to_channel.apply(np.asarray(points0, dtype=np.float64) + 1.0) - 1.0

    def channel_to_reference(self, points0: np.ndarray) -> np.ndarray:
        """Map 0-based ``[x, y]`` from this channel into the reference channel (0-based)."""
        return self.channel_to_ref.apply(np.asarray(points0, dtype=np.float64) + 1.0) - 1.0


# --- .tmap MCOS decode -------------------------------------------------------
#
# A ``.tmap`` is a classic MATLAB v5 MAT-file: variable ``m`` is a 1xN cell of
# per-channel structs whose MapToReference/MapFromReference fields are MCOS
# ``images.geotrans.PolynomialTransformation2D`` objects. scipy surfaces those as
# opaque handles; the actual coefficients live in the file's
# ``__function_workspace__`` (the MCOS subsystem). We re-parse that blob and
# resolve each handle to its property struct.
#
# NB: this leans on a scipy private reader (``scipy.io.matlab._mio5``) to re-read
# the embedded MAT stream -- the only practical path to MCOS coefficients. It runs
# in the fixture-derivation script and a data-present (non-CI) test, never in the
# default required matrix, so a scipy-version drift cannot redden ``main``.


def _opaque_payload(void_record: np.void) -> np.ndarray:
    """Return the trailing payload field of a scipy ``MatlabOpaque`` void record.

    scipy renamed the opaque fields across versions (older: ``s0,s1,s2,arr``;
    >=1.x: ``_TypeSystem,_Class,_ObjectMetadata``); the payload (object cell array
    for FileWrapper, or the handle vector for a class instance) is always last.
    """
    return np.asarray(void_record[void_record.dtype.names[-1]])


def _read_filewrapper(func_ws_bytes: bytes) -> np.ndarray:
    """Re-parse the ``__function_workspace__`` MCOS blob into its object cell array.

    The blob is an 8-byte sub-header followed by a standard MAT-5 element stream;
    we prepend a synthetic 128-byte v5 header and re-read it. ``read_file_header``
    must run before ``read_var_header`` (``get_variables`` does this internally),
    otherwise scipy raises "Expecting miMATRIX".
    """
    from scipy.io.matlab._mio5 import MatFile5Reader  # noqa: PLC0415 (private, isolated)

    header = b"MATLAB 5.0 MAT-file" + b" " * (116 - 19) + b"\x00" * 8 + b"\x00\x01" + b"IM"
    stream = header + func_ws_bytes[8:]
    reader = MatFile5Reader(BytesIO(stream), struct_as_record=True, squeeze_me=False)
    reader.mat_stream.seek(0)
    reader.initialize_read()
    reader.read_file_header()  # consume the 128-byte header
    var_header, _ = reader.read_var_header()
    file_wrapper = reader.read_var_array(var_header, process=False)
    # The opaque record's payload is the FileWrapper object cell array.
    return _opaque_payload(file_wrapper[0, 0]["MCOS"][0]).ravel()


def _is_poly_cell(cell: np.ndarray) -> bool:
    """True if an object cell is a PolynomialTransformation2D property struct."""
    try:
        names = cell[0, 0].dtype.names
    except (AttributeError, IndexError, TypeError):
        return False
    return bool(names) and {"A", "B", "normTransformXY", "normTransformUV"} <= set(names)


def _affine_matrix(arr: np.ndarray, objid: int) -> np.ndarray:
    """Resolve an affine2d/imref2d normalisation handle to its 3x3 matrix."""
    cell = arr[objid][0, 0]
    return np.asarray(cell["TransformationMatrix"], dtype=np.float64)


def _read_poly(arr: np.ndarray, cell_index: int) -> PolyTransform2D:
    struct = arr[cell_index][0, 0]
    a = np.asarray(struct["A"], dtype=np.float64).ravel()
    b = np.asarray(struct["B"], dtype=np.float64).ravel()
    if a.size != 6 or b.size != 6:
        raise ValueError(f"expected 6 degree-2 coefficients, got A={a.size} B={b.size}")
    norm_xy_id = int(np.asarray(struct["normTransformXY"]).ravel()[4])
    norm_uv_id = int(np.asarray(struct["normTransformUV"]).ravel()[4])
    return PolyTransform2D(
        a=a,
        b=b,
        norm_xy=_affine_matrix(arr, norm_xy_id),
        norm_uv=_affine_matrix(arr, norm_uv_id),
    )


def read_tmap(path: str | Path) -> dict[int, TmapChannel]:
    """Decode a Deep-LASI ``.tmap`` into ``{channel_id: TmapChannel}``.

    Resolves the MCOS ``PolynomialTransformation2D`` coefficients without MATLAB.
    The object-id -> property-cell mapping is derived generically (not hardcoded):
    the four ``Map{To,From}Reference`` handles give four polynomial object ids,
    which rank-zip onto the four polynomial property cells (MCOS stores objects of
    one class consecutively in creation/id order). Decoded transforms are in the
    file's native MATLAB 1-based pixel frame.
    """
    data = sio.loadmat(str(path))
    if "m" not in data:
        raise ValueError(f"{path}: not a Deep-LASI .tmap (no 'm' cell)")
    cells = data["m"]
    arr = _read_filewrapper(data["__function_workspace__"].tobytes())

    # Polynomial property cells in arr order; their ranks map to sorted object ids.
    poly_cell_indices = sorted(i for i in range(1, len(arr)) if _is_poly_cell(arr[i]))

    handles: dict[int, tuple[int, int]] = {}  # channel_id -> (to_objid, from_objid)
    for idx in range(cells.shape[1]):
        struct = cells[0, idx][0, 0]
        channel_id = int(np.asarray(struct["ChannelID"]).ravel()[0])
        # The opaque handle's 5th element (index 4) is the polynomial object id.
        to_objid = int(_opaque_payload(struct["MapToReference"][0]).ravel()[4])
        from_objid = int(_opaque_payload(struct["MapFromReference"][0]).ravel()[4])
        handles[channel_id] = (to_objid, from_objid)

    referenced = sorted({oid for pair in handles.values() for oid in pair})
    if len(referenced) != len(poly_cell_indices):
        raise ValueError(
            f"{path}: {len(referenced)} polynomial handles but "
            f"{len(poly_cell_indices)} polynomial cells (unexpected .tmap layout)"
        )
    objid_to_cell = dict(zip(referenced, poly_cell_indices, strict=True))

    out: dict[int, TmapChannel] = {}
    for idx in range(cells.shape[1]):
        struct = cells[0, idx][0, 0]
        channel_id = int(np.asarray(struct["ChannelID"]).ravel()[0])
        to_objid, from_objid = handles[channel_id]
        out[channel_id] = TmapChannel(
            channel_id=channel_id,
            crop=np.asarray(struct["Crop"]),
            map_particles=np.asarray(struct["MapParticles"], dtype=np.float64),
            ref_to_channel=_read_poly(arr, objid_to_cell[to_objid]),
            channel_to_ref=_read_poly(arr, objid_to_cell[from_objid]),
        )
    return out
