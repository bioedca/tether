# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Seeded generators of synthetic FRET traces and two-channel movies, for tests only (#701).

Self-drift and speed tests need data of a controlled size with a known answer. This module
makes two kinds:

* :func:`fret_traces` -- donor and acceptor intensity traces of an N-state Markov chain with
  known per-frame switch probabilities, known FRET levels, known Gaussian-like noise and a
  known photobleaching frame per molecule.
* :func:`two_channel_movie` -- a small side-by-side (donor | acceptor) ``uint16`` movie with
  known spot positions, a known donor-to-acceptor translation and known per-frame spot
  intensities. :func:`write_movie_tiff` writes it as the big-endian TIFF that
  :func:`tether.project.extract.extract_movie` reads.

**This is not a product simulator** (PRD N2; #550 records the reword). The module lives
under ``tests/``, is imported as the top-level module ``_synth`` (pytest's default
``prepend`` import mode, no ``tests/__init__.py``; the pattern of ``_analysis_store``), and
``tests/test_synth.py`` fails if any module under ``src/tether`` imports it.

**Same seed, same bytes, on every OS.** The generators use only operations whose result
IEEE 754 fixes exactly: the integer and ``random()`` streams of NumPy's PCG64
``Generator``, elementwise ``+ - * /``, comparisons, ``np.rint`` and integer floor
division. They call no ``exp``, ``log``, ``sin`` or ``cos`` -- the platform maths library
may differ in the last bit -- and no reductions whose summation order NumPy may change.
The noise is therefore the Irwin-Hall sum of twelve uniforms minus six (mean 0, variance
exactly 1; bounded at +-6), and the point-spread function is a separable binomial kernel
held as integers. ``tests/test_synth.py`` pins the SHA-256 of one trace set and one movie;
the required CI matrix runs it on Linux, macOS and Windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = [
    "PSF_KERNEL",
    "PSF_SCALE",
    "SyntheticMovie",
    "SyntheticTraces",
    "fret_traces",
    "standard_noise",
    "step_traces",
    "two_channel_movie",
    "write_movie_tiff",
]

#: Separable binomial PSF ``outer(b, b)`` with ``b = [1, 6, 15, 20, 15, 6, 1]``
#: (standard deviation sqrt(1.5) ~ 1.22 px). Integer weights; the sum is :data:`PSF_SCALE`.
PSF_KERNEL: np.ndarray = np.outer(*(2 * [np.array([1, 6, 15, 20, 15, 6, 1], dtype=np.int64)]))
#: ``PSF_KERNEL.sum()`` (64 ** 2): a spot of amplitude ``A`` puts ``A`` counts in the frame,
#: less the floor-division remainder of at most one count per kernel pixel.
PSF_SCALE: int = 4096


def standard_noise(rng: np.random.Generator, shape: tuple[int, ...]) -> np.ndarray:
    """Draw unit-variance, zero-mean noise of ``shape`` with exact cross-OS bytes.

    The Irwin-Hall sum of twelve ``U(0, 1)`` draws minus 6. Each addition is one
    elementwise ufunc call in a fixed order, so no SIMD reduction reorders the sum.
    """
    total = np.zeros(shape, dtype=np.float64)
    for _ in range(12):
        total = total + rng.random(shape)
    return total - 6.0


def step_traces(n: int, t: int, *, low: float = 200.0, high: float = 800.0) -> np.ndarray:
    """``(n, t)`` noise-free two-level step; molecule ``i`` switches at frame ``t // 3 + i``.

    The rows are unique, which the idealize and hand-off suites need. Moved unchanged from
    ``tests/test_project_idealize.py`` and ``tests/test_handoff.py``.
    """
    out = np.full((n, t), low, dtype="float64")
    for i in range(n):
        out[i, (t // 3) + i :] = high
    return out


@dataclass(frozen=True)
class SyntheticTraces:
    """A generated trace set and the answer it was generated from.

    ``donor`` / ``acceptor`` are ``(n, t)`` ``float64`` intensities (noise included);
    ``states`` is the ``(n, t)`` ``int64`` true state path, ``-1`` from the bleach frame
    on; ``bleach_frame`` is ``(n,)`` (``t`` when the molecule does not bleach in the
    record); ``efficiency`` is the per-state FRET efficiency the chain emits.
    """

    donor: np.ndarray
    acceptor: np.ndarray
    states: np.ndarray
    bleach_frame: np.ndarray
    efficiency: np.ndarray
    switch_probability: np.ndarray


def _next_state(cumulative: np.ndarray, state: int, u: float) -> int:
    row = cumulative[state]
    for j in range(row.shape[0] - 1):
        if u < row[j]:
            return j
    return row.shape[0] - 1


def fret_traces(
    n_molecules: int,
    n_frames: int,
    *,
    seed: int,
    efficiency: tuple[float, ...] = (0.25, 0.75),
    switch_probability: np.ndarray | None = None,
    total_intensity: float = 1000.0,
    noise_sigma: float = 30.0,
    bleach_probability: float = 0.0,
) -> SyntheticTraces:
    """Generate donor/acceptor traces of an N-state Markov chain with a known answer.

    Parameters
    ----------
    n_molecules, n_frames:
        Output shape ``(n_molecules, n_frames)``.
    seed:
        Seed of the PCG64 stream; the same seed gives the same bytes on every OS.
    efficiency:
        FRET efficiency of each state; its length is the number of states (two or three
        states are the intended use).
    switch_probability:
        ``(k, k)`` row-stochastic per-frame transition matrix ``P[i, j]`` (the rate is
        ``P[i, j] / dt`` in the small-probability limit; the mean dwell in state ``i`` is
        ``1 / (1 - P[i, i])`` frames). Default: ``0.05`` to every other state.
    total_intensity:
        Noise-free ``D + A`` before bleaching (``gamma = 1``, no leakage).
    noise_sigma:
        Standard deviation of the additive noise on each channel, every frame.
    bleach_probability:
        Per-frame donor photobleaching probability; from the bleach frame on both channels
        carry noise only and ``states`` is ``-1``.
    """
    eff = np.asarray(efficiency, dtype=np.float64)
    k = eff.shape[0]
    if k < 1 or np.any((eff < 0.0) | (eff > 1.0)):
        raise ValueError(f"efficiency must hold 1+ values in [0, 1], got {efficiency!r}")
    if switch_probability is None:
        p = np.full((k, k), 0.05 if k > 1 else 0.0)
        np.fill_diagonal(p, 0.0)
        np.fill_diagonal(p, 1.0 - p.sum(axis=1))
    else:
        # A copy: the result records the matrix, and a caller's later edit must not move it.
        p = np.array(switch_probability, dtype=np.float64, copy=True)
    if p.shape != (k, k) or np.any(p < 0.0) or not np.allclose(p.sum(axis=1), 1.0):
        raise ValueError(f"switch_probability must be a ({k}, {k}) row-stochastic matrix")
    if not 0.0 <= bleach_probability <= 1.0:
        raise ValueError(f"bleach_probability must be in [0, 1], got {bleach_probability}")

    # Row-wise cumulative sums in a fixed left-to-right order (no reordering reduction).
    cumulative = np.zeros_like(p)
    for j in range(k):
        cumulative[:, j] = p[:, j] if j == 0 else cumulative[:, j - 1] + p[:, j]

    rng = np.random.default_rng(seed)
    states = np.empty((n_molecules, n_frames), dtype=np.int64)
    bleach = np.full(n_molecules, n_frames, dtype=np.int64)
    for m in range(n_molecules):
        s = int(rng.integers(k))
        switch_u = rng.random(n_frames)
        bleach_u = rng.random(n_frames)
        for f in range(n_frames):
            if bleach_u[f] < bleach_probability:
                bleach[m] = f
                break
            states[m, f] = s
            s = _next_state(cumulative, s, float(switch_u[f]))
        states[m, bleach[m] :] = -1

    alive = states >= 0
    e_path = np.where(alive, eff[np.maximum(states, 0)], 0.0)
    signal = np.where(alive, total_intensity, 0.0)
    donor = signal * (1.0 - e_path) + noise_sigma * standard_noise(rng, states.shape)
    acceptor = signal * e_path + noise_sigma * standard_noise(rng, states.shape)
    return SyntheticTraces(donor, acceptor, states, bleach, eff, p)


@dataclass(frozen=True)
class SyntheticMovie:
    """A generated two-channel movie and the answer it was generated from.

    ``frames`` is ``(t, h, 2 * w)`` ``uint16``, donor half on the left. ``donor_xy`` /
    ``acceptor_xy`` are ``(n, 2)`` ``[x, y]`` spot centres in each half's own pixel
    coordinates; ``acceptor_xy = donor_xy + offset``. ``donor_amplitude`` /
    ``acceptor_amplitude`` are the ``(n, t)`` integer counts each spot puts in its half
    (before the floor-division remainder); ``background`` is the constant pixel offset.
    """

    frames: np.ndarray
    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    offset: tuple[int, int]
    donor_amplitude: np.ndarray
    acceptor_amplitude: np.ndarray
    background: int

    def disk_intensity(self, channel: str, disk_radius: float = 3.0) -> np.ndarray:
        """The known ``(n, t)`` counts each spot puts inside a disk of ``disk_radius``.

        The sum of the spot's floor-divided PSF patch over the pixels at Euclidean
        distance ``<= disk_radius`` from the spot centre, background excluded. This is
        what a background-subtracted disk aperture recovers from a noise-free movie.
        """
        amp = {"donor": self.donor_amplitude, "acceptor": self.acceptor_amplitude}[channel]
        half = PSF_KERNEL.shape[0] // 2
        rows, cols = np.mgrid[-half : half + 1, -half : half + 1]
        disk = rows * rows + cols * cols <= disk_radius * disk_radius
        patches = (amp[:, :, None, None] * PSF_KERNEL[None, None]) // PSF_SCALE
        return np.where(disk, patches, 0).sum(axis=(2, 3))


def _default_positions(height: int, width: int, spacing: int, margin: int) -> np.ndarray:
    xs = range(margin, width - margin, spacing)
    ys = range(margin, height - margin, spacing)
    return np.array([[x, y] for y in ys for x in xs], dtype=np.int64)


def two_channel_movie(
    *,
    seed: int,
    n_frames: int = 40,
    height: int = 64,
    half_width: int = 64,
    offset: tuple[int, int] = (1, -1),
    background: int = 100,
    spot_intensity: int = 6000,
    efficiency: tuple[float, ...] = (0.35, 0.65),
    noise_sigma: float = 0.0,
    spacing: int = 19,
    margin: int = 12,
) -> SyntheticMovie:
    """Generate a side-by-side donor | acceptor movie with known spots and intensities.

    Spots sit on an integer grid (``spacing`` apart, ``margin`` from each half's edge, so
    the default 21-px aperture fits and no spot's PSF reaches another spot's background
    ring), each moved by a seeded integer jitter of at most 1 px per axis. Each spot
    follows a noise-free :func:`fret_traces` path with ``total_intensity=spot_intensity``;
    the per-frame amplitudes are rounded to integers and are the known answer. The
    acceptor copy of each spot is moved by the integer ``offset = (dx, dy)``: the known
    channel mapping is a pure translation.
    ``noise_sigma`` adds rounded :func:`standard_noise` to every pixel.
    """
    rng = np.random.default_rng(seed)
    grid = _default_positions(height, half_width, spacing, margin)
    # A +-1 px jitter breaks the lattice symmetry: a perfectly periodic spot grid makes the
    # translation pre-alignment ambiguous up to one lattice period.
    donor_xy = grid + rng.integers(-1, 2, size=grid.shape)
    acceptor_xy = donor_xy + np.asarray(offset, dtype=np.int64)
    n = donor_xy.shape[0]
    path = fret_traces(
        n,
        n_frames,
        seed=int(rng.integers(2**31)),
        efficiency=efficiency,
        total_intensity=float(spot_intensity),
        noise_sigma=0.0,
    )
    donor_amp = np.rint(path.donor).astype(np.int64)
    acceptor_amp = np.rint(path.acceptor).astype(np.int64)

    frames = np.full((n_frames, height, 2 * half_width), background, dtype=np.int64)
    half = PSF_KERNEL.shape[0] // 2
    for xy_set, amp, x0 in ((donor_xy, donor_amp, 0), (acceptor_xy, acceptor_amp, half_width)):
        for i, (x, y) in enumerate(xy_set):
            if not (half <= x < half_width - half and half <= y < height - half):
                raise ValueError(f"spot {i} at {(int(x), int(y))} leaves its half")
            patch = (amp[i][:, None, None] * PSF_KERNEL[None, :, :]) // PSF_SCALE
            frames[:, y - half : y + half + 1, x0 + x - half : x0 + x + half + 1] += patch
    if int(frames.max()) > np.iinfo(np.uint16).max:
        # Clipping a saturated spot would make the recorded counts unrecoverable.
        raise ValueError(
            f"noise-free pixels reach {int(frames.max())}, above the uint16 maximum; "
            "lower spot_intensity or background"
        )
    if noise_sigma > 0.0:
        frames = frames + np.rint(noise_sigma * standard_noise(rng, frames.shape)).astype(np.int64)
    frames = np.clip(frames, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    return SyntheticMovie(
        frames=frames,
        donor_xy=donor_xy,
        acceptor_xy=acceptor_xy,
        offset=(int(offset[0]), int(offset[1])),
        donor_amplitude=donor_amp,
        acceptor_amplitude=acceptor_amp,
        background=int(background),
    )


def write_movie_tiff(movie: SyntheticMovie, path: str | Path) -> Path:
    """Write ``movie.frames`` as the uncompressed big-endian TIFF the extractor reads."""
    import tifffile

    path = Path(path)
    be = np.ascontiguousarray(movie.frames, dtype=">u2")
    tifffile.imwrite(path, be, photometric="minisblack", byteorder=">")
    return path
