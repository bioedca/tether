# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless NFR-PERF budget checks (PRD §8 NFR-PERF, §11.2, §12.10; ADR-0032).

NFR-PERF is a **light §9 gate, not an SLA matrix** (PRD §8), first verified at **M3**
(§12.10 — the trace dock whose render latency is budgeted lands at M2, and the overnight
extract → correct → idealize envelope is only end-to-end at M3). This module is the
Qt-free tooling behind that gate — named budget constants plus small, testable
measurement/projection helpers — so the perf tests assert against a single source of
truth rather than magic numbers.

Three budgets (PRD §8, "Targets"):

* **Per-trace render+navigate latency ≈ 100 ms** — the one PRD-registered value
  (§11.2 "Per-trace UI latency budget"), sustaining the 1–2 s/trace curation cadence.
  :data:`PER_TRACE_LATENCY_BUDGET_S`; measured with :func:`min_runtime` against a real
  :class:`tether.gui.trace_dock.TraceDock` in the GUI test tier.
* **A ~100-movie condition finishes extract + correct + idealize overnight** — a
  *slice-scaled* estimate. :func:`scale_seconds_to_reference_movie` projects a small
  slice's measured extraction time to the reference full movie (512×512×1700) by pixel
  volume; :func:`project_overnight` scales that across the condition and checks it fits
  the unattended :data:`OVERNIGHT_WINDOW_HOURS` window. This is an envelope, **not** a
  full-movie SLA — the full ≈0.9 GB movie and the real vbFRET sidecar are measured in the
  gated ``large-fixtures.yml`` tier (PLAN §2.2), never the default matrix.
* **A bounded ``.tether`` size envelope per condition** — a molecule's stored cost is
  dominated by its six redundant float32 intensity layers
  ({donor,acceptor}×{raw,corrected,background}, PRD §5.1 = ``6·4 = 24`` B/frame);
  gzip keeps the on-disk cost at/under that. :func:`measure_store_size` reads a real
  store's on-disk bytes; :func:`estimate_condition_bytes` projects a full condition and
  checks it stays modest vs the ~90 GB of raw movies on the §8 reference-hardware
  ~100 GB disk (OneDrive Files-On-Demand, not all hydrated locally).

Only the latency target is a PRD §11.2 tunable; the size/overnight figures are
*derived engineering envelopes* consequent on the frozen §5.1 data model and the §8
reference-hardware floor (deliberately soft — "not an SLA matrix"), so they live here as
documented constants rather than as new §11.2 rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PER_TRACE_LATENCY_BUDGET_S",
    "REFERENCE_MOVIE_HEIGHT",
    "REFERENCE_MOVIE_WIDTH",
    "REFERENCE_MOVIE_FRAMES",
    "REFERENCE_MOVIE_PIXELS",
    "REFERENCE_CONDITION_MOVIES",
    "REFERENCE_MOLECULES_PER_MOVIE",
    "OVERNIGHT_WINDOW_HOURS",
    "TRACE_LAYERS_PER_MOLECULE",
    "TRACE_BYTES_PER_MOLECULE_FRAME",
    "TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME",
    "FIXED_STORAGE_BYTES_PER_MOLECULE",
    "MAX_CONDITION_BYTES",
    "StoreSizeReport",
    "OvernightEstimate",
    "min_runtime",
    "measure_store_size",
    "estimate_molecule_bytes",
    "estimate_condition_bytes",
    "scale_seconds_to_reference_movie",
    "project_overnight",
]

# --- The one PRD-registered budget (§11.2 "Per-trace UI latency budget") ------

#: Per-trace render+navigate latency budget in seconds (PRD §8 / §11.2, ≈ 100 ms):
#: the cost of drawing one molecule's donor/acceptor + FRET curves and stepping to the
#: next must stay under this to sustain the 1–2 s/trace curation cadence.
PER_TRACE_LATENCY_BUDGET_S: float = 0.100

# --- Reference-hardware envelope (§8 NFR-PERF floor) --------------------------

#: The reference full movie geometry (PRD §8: "a big-endian 512×512×1700 TIFF").
REFERENCE_MOVIE_HEIGHT: int = 512
REFERENCE_MOVIE_WIDTH: int = 512
REFERENCE_MOVIE_FRAMES: int = 1700
#: Pixel volume of one reference movie — the scale target for the slice-scaled
#: extraction-time projection.
REFERENCE_MOVIE_PIXELS: int = (
    REFERENCE_MOVIE_HEIGHT * REFERENCE_MOVIE_WIDTH * REFERENCE_MOVIE_FRAMES
)

#: A "condition" is many movies across days/files (PRD §5.1); ~100 movies ≈ 90 GB of
#: raw data on the §8 reference disk (UC7 "overnight batch").
REFERENCE_CONDITION_MOVIES: int = 100
#: A representative curated molecule count per movie (the UCKOPSB ``…010`` movie carries
#: ~250; a conservative unit for the per-condition store projection).
REFERENCE_MOLECULES_PER_MOVIE: int = 250
#: The unattended window a condition's extract+correct+idealize must fit inside
#: ("overnight", PRD §8 UC7): 12 h.
OVERNIGHT_WINDOW_HOURS: float = 12.0

# --- .tether size model (PRD §5.1: six redundant float32 intensity layers) ----

#: {donor,acceptor} × {raw, corrected, background} cached intensity arrays per molecule.
TRACE_LAYERS_PER_MOLECULE: int = 6
_FLOAT32_BYTES: int = 4
#: Uncompressed per-molecule-per-frame trace cost: the six float32 layers dominate the
#: store (``6 · 4 = 24`` B/frame).
TRACE_BYTES_PER_MOLECULE_FRAME: int = TRACE_LAYERS_PER_MOLECULE * _FLOAT32_BYTES
#: On-disk envelope for the six gzip-chunked float32 trace arrays: gzip keeps them at/
#: under their uncompressed size; a 1.5× headroom absorbs per-chunk filter overhead while
#: still flagging a storage-dtype regression (float64 six-layer = 48 B/frame > 36).
TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME: float = TRACE_BYTES_PER_MOLECULE_FRAME * 1.5
#: Per-molecule fixed overhead: two ``window×window`` float32 patches (default
#: 21² · 4 · 2 ≈ 3.5 KB) + the ``/molecules`` row + registration tags, rounded up.
FIXED_STORAGE_BYTES_PER_MOLECULE: int = 5000
#: A condition's projected ``.tether`` must stay modest vs the ~90 GB of raw movies on
#: the §8 reference ~100 GB disk: cap the per-condition store projection at 5 GiB
#: (~5.5% of the movie footprint).
MAX_CONDITION_BYTES: int = 5 * 1024**3

# Store paths (mirror tether.imaging.extract's frozen §5.1 layout).
_TRACES_GROUP = "traces"
_PATCHES_GROUP = "patches"
_MOLECULES_TABLE = "molecules/table"


# --- Timing ------------------------------------------------------------------


def min_runtime(fn: Callable[[], object], *, repeats: int = 5) -> float:
    """Minimum wall-clock seconds of calling ``fn`` over ``repeats`` runs.

    The minimum isolates the operation's compute cost from OS-scheduler jitter — the
    standard microbenchmark reduction — so a latency assertion against a shared CI
    runner is robust to transient noise (a slow sample never lowers the minimum).

    Parameters
    ----------
    fn
        A zero-argument callable to time; its return value is ignored.
    repeats
        Number of runs (``>= 1``). The first run also serves as a warm-up whose cost is
        naturally discarded once a faster run appears.
    """
    if repeats < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats}")
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


# --- .tether size envelope ---------------------------------------------------


@dataclass(frozen=True)
class StoreSizeReport:
    """On-disk size breakdown of a ``.tether`` project (from :func:`measure_store_size`)."""

    total_bytes: int
    traces_bytes: int
    patches_bytes: int
    n_molecules: int
    n_frames: int

    @property
    def bytes_per_molecule(self) -> float:
        """Whole-store on-disk bytes amortized per molecule (0 for an empty store)."""
        return self.total_bytes / self.n_molecules if self.n_molecules else 0.0

    @property
    def traces_bytes_per_molecule_frame(self) -> float:
        """The dominant term: ``/traces`` on-disk bytes per molecule per frame.

        Compared against :data:`TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME`; this is
        the N-robust size claim (independent of the fixed skeleton/registration overhead
        that inflates :attr:`bytes_per_molecule` at small molecule counts). The envelope
        assumes a realistic frame count — the six-layer term dominates only once ``T`` is
        large enough to amortize per-chunk gzip/HDF5 overhead (real movies are ~1700
        frames; the 1.5× headroom absorbs the amortization at the hundreds-of-frames
        scale the size test uses).
        """
        denom = self.n_molecules * self.n_frames
        return self.traces_bytes / denom if denom else 0.0

    @property
    def within_trace_envelope(self) -> bool:
        """Whether the measured ``/traces`` cost is within the float32 six-layer envelope."""
        return (
            self.traces_bytes_per_molecule_frame <= TRACE_STORAGE_ENVELOPE_BYTES_PER_MOLECULE_FRAME
        )


def measure_store_size(path: str | Path) -> StoreSizeReport:
    """Measure a ``.tether``'s real on-disk dataset bytes (HDF5 ``get_storage_size``).

    Walks every dataset and sums its *compressed, on-disk* storage size, separating the
    ``/traces`` and ``/patches`` contributions, and reads the molecule count
    (``/molecules/table``) and frame count (a ``/traces`` array's last axis). Uses the
    per-dataset storage size rather than the file size so the measurement is independent
    of HDF5 free-space/metadata slack.
    """
    import h5py  # noqa: PLC0415

    total = 0
    traces = 0
    patches = 0
    n_frames = 0

    def visit(name: str, obj: object) -> None:
        nonlocal total, traces, patches, n_frames
        if isinstance(obj, h5py.Dataset):
            size = int(obj.id.get_storage_size())
            total += size
            if name.startswith(f"{_TRACES_GROUP}/"):
                traces += size
                if obj.ndim >= 1:
                    n_frames = max(n_frames, int(obj.shape[-1]))
            elif name.startswith(f"{_PATCHES_GROUP}/"):
                patches += size

    with h5py.File(path, "r") as f:
        f.visititems(visit)
        n_molecules = int(f[_MOLECULES_TABLE].shape[0]) if _MOLECULES_TABLE in f else 0

    return StoreSizeReport(
        total_bytes=total,
        traces_bytes=traces,
        patches_bytes=patches,
        n_molecules=n_molecules,
        n_frames=n_frames,
    )


def estimate_molecule_bytes(n_frames: int) -> float:
    """Analytic upper-envelope bytes for one molecule's stored data at ``n_frames``.

    ``TRACE_BYTES_PER_MOLECULE_FRAME · n_frames`` (the six uncompressed float32 layers,
    the dominant term) plus :data:`FIXED_STORAGE_BYTES_PER_MOLECULE` (patches + row).
    An upper bound: real gzip'd traces store *below* the uncompressed term.
    """
    if n_frames < 0:
        raise ValueError(f"n_frames must be non-negative, got {n_frames}")
    return TRACE_BYTES_PER_MOLECULE_FRAME * n_frames + FIXED_STORAGE_BYTES_PER_MOLECULE


def estimate_condition_bytes(
    *,
    molecules_per_movie: int = REFERENCE_MOLECULES_PER_MOVIE,
    n_movies: int = REFERENCE_CONDITION_MOVIES,
    n_frames: int = REFERENCE_MOVIE_FRAMES,
) -> float:
    """Project a full condition's ``.tether`` bytes from the per-molecule envelope.

    ``estimate_molecule_bytes(n_frames) · molecules_per_movie · n_movies`` — a
    conservative (uncompressed-trace) upper envelope; compared against
    :data:`MAX_CONDITION_BYTES` to assert the per-condition store stays modest.
    """
    if molecules_per_movie < 0 or n_movies < 0:
        raise ValueError("molecules_per_movie and n_movies must be non-negative")
    return estimate_molecule_bytes(n_frames) * molecules_per_movie * n_movies


# --- Overnight batch envelope ------------------------------------------------


def scale_seconds_to_reference_movie(measured_seconds: float, measured_pixels: int) -> float:
    """Project a slice's measured extraction time to the reference full movie by pixels.

    Extraction is dominated by per-pixel work (block I/O + per-frame detection +
    aperture integration), so wall-time scales ~linearly with pixel volume to first
    order: ``measured_seconds · REFERENCE_MOVIE_PIXELS / measured_pixels``. This is a
    *slice-scaled envelope* (PRD §8 "a scaled estimate from the slice"), not a full-movie
    SLA — the full-movie measurement rides in the gated ``large-fixtures.yml`` tier.
    """
    if measured_pixels <= 0:
        raise ValueError(f"measured_pixels must be positive, got {measured_pixels}")
    if measured_seconds < 0:
        raise ValueError(f"measured_seconds must be non-negative, got {measured_seconds}")
    return measured_seconds * REFERENCE_MOVIE_PIXELS / measured_pixels


@dataclass(frozen=True)
class OvernightEstimate:
    """A condition-scale extract-time projection vs the overnight window."""

    per_movie_seconds: float
    n_movies: int
    window_hours: float

    @property
    def total_seconds(self) -> float:
        return self.per_movie_seconds * self.n_movies

    @property
    def total_hours(self) -> float:
        return self.total_seconds / 3600.0

    @property
    def fits_window(self) -> bool:
        """Whether the whole condition finishes within :attr:`window_hours`."""
        return self.total_hours <= self.window_hours

    @property
    def headroom(self) -> float:
        """``window_hours / total_hours`` — the safety factor (``inf`` for zero work)."""
        return self.window_hours / self.total_hours if self.total_hours > 0 else float("inf")


def project_overnight(
    per_movie_seconds: float,
    *,
    n_movies: int = REFERENCE_CONDITION_MOVIES,
    window_hours: float = OVERNIGHT_WINDOW_HOURS,
) -> OvernightEstimate:
    """Project a per-movie time across ``n_movies`` and test it fits ``window_hours``.

    A pure scaling of the (already reference-scaled) per-movie estimate; leaves headroom
    within the window for correction (negligible pure-numpy ``O(N·T)``) and the
    per-molecule vbFRET idealization (bounded + parallelizable across molecules, PRD §8;
    its full-movie SLA is the gated tier's concern).
    """
    if per_movie_seconds < 0:
        raise ValueError(f"per_movie_seconds must be non-negative, got {per_movie_seconds}")
    if n_movies < 1:
        raise ValueError(f"n_movies must be >= 1, got {n_movies}")
    if window_hours <= 0:
        raise ValueError(f"window_hours must be positive, got {window_hours}")
    return OvernightEstimate(
        per_movie_seconds=float(per_movie_seconds),
        n_movies=int(n_movies),
        window_hours=float(window_hours),
    )
