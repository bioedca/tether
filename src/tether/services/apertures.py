# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The aperture and background review of a movie, before the run (#922; PRD §7.1 FR-EXTRACT).

The Qt-free core of the Prepare page **Apertures and background**
([`tether.gui.apertures`][]) and of ``tether apertures``. [`review_movie`][] runs the stage
preview of #348 ([`run_preview`][tether.services.preview.run_preview]) on a movie and returns
an [`ApertureReview`][]: every detected donor spot with its signal aperture (the PSF disk) and
its background ring in the raw camera frame, at the donor position and at the acceptor
position the registration maps it to, and two marks.

* **At an edge**: the ``window x window`` crop of the spot leaves the donor or the acceptor
  channel, so the run skips it (Appendix E stage 13). The test is
  [`aperture_in_frame`][tether.imaging.aperture.aperture_in_frame] on both channels, the
  predicate [`colocalize`][tether.imaging.coloc.colocalize] applies.
* **Overlaps a neighbour**: the PSF disk of a molecule overlaps the disk of its nearest
  neighbour, the rule of the curation overlap view,
  [`neighbor_report`][tether.analysis.overlap.neighbor_report] with the run's ``disk_radius``,
  over the molecules the run keeps.

These are the present guard rules, called as they are; the page adds no rule. A donor spot
that is not at an edge and is not kept has no acceptor partner under the ``partner-in-both``
rule.

The preview trace of a molecule is the trace the full run integrates for it: the preview runs
the same stages with the same arguments (``tests/test_extract_preview.py``), and
[`preview_trace`][] only reads a row of it. The review writes nothing to a project.

[`write_report`][] writes the report: a JSON file and a PNG of the frame with the apertures and
the marks. The page and the command line call it with the same arguments, so the two write the
same files.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tether

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import numpy as np

    from tether.imaging import MoleculeTraces
    from tether.project.extract import ExtractOptions
    from tether.project.preview import ExtractionPreview

__all__ = [
    "REPORT_FORMAT",
    "REPORT_VERSION",
    "ApertureReview",
    "ApertureSpot",
    "build_review",
    "preview_trace",
    "report_dict",
    "review_movie",
    "write_report",
]

#: The ``format`` field of the JSON report.
REPORT_FORMAT = "tether-aperture-report"
#: The version of the JSON report; a change of its fields raises it.
REPORT_VERSION = 1


@dataclass(frozen=True)
class ApertureSpot:
    """One detected donor spot of the review.

    ``donor_xy`` and ``acceptor_xy`` are ``(x, y)`` in the raw camera frame: the donor spot,
    and the donor position mapped into the acceptor channel by the registration, where the run
    reads the acceptor. ``molecule`` is the row of the spot among the molecules the run keeps
    (and among the preview traces), ``None`` when the run skips it. ``nn_distance`` is the
    distance to the nearest molecule, for a molecule with a neighbour.
    """

    spot: int
    donor_xy: tuple[float, float]
    acceptor_xy: tuple[float, float]
    at_edge: bool
    molecule: int | None
    overlaps: bool = False
    nn_distance: float | None = None

    @property
    def status(self) -> str:
        """``at-edge``, ``overlap``, ``no-partner`` or ``ok``: what the page marks."""
        if self.at_edge:
            return "at-edge"
        if self.molecule is None:
            return "no-partner"
        return "overlap" if self.overlaps else "ok"


@dataclass(frozen=True)
class ApertureReview:
    """The apertures of one movie: the spots, their marks and the aperture geometry."""

    movie: str
    frame_shape: tuple[int, int]
    window: int
    disk_radius: float
    ring_inner: float
    ring_outer: float
    overlap_distance: float
    policy: str
    options: dict[str, Any]
    spots: tuple[ApertureSpot, ...]
    traces: MoleculeTraces | None = field(default=None, repr=False, compare=False)

    @property
    def n_spots(self) -> int:
        """The number of detected donor spots."""
        return len(self.spots)

    @property
    def n_edge(self) -> int:
        """The number of spots at an edge, which the run skips."""
        return sum(spot.at_edge for spot in self.spots)

    @property
    def n_overlap(self) -> int:
        """The number of molecules whose aperture overlaps a neighbour's."""
        return sum(spot.overlaps for spot in self.spots)

    @property
    def n_molecules(self) -> int:
        """The number of molecules the run keeps."""
        return sum(spot.molecule is not None for spot in self.spots)

    def summary(self) -> str:
        """One line with the counts, as the page shows it."""
        return (
            f"{self.n_spots} donor spots: {self.n_molecules} molecules, "
            f"{self.n_edge} at an edge, {self.n_overlap} overlap a neighbour"
        )


def _frame_xy(xy: np.ndarray, geometry: Any, frame_shape: tuple[int, int]) -> np.ndarray:
    from tether.services.preview import _to_frame

    return _to_frame(xy, geometry, frame_shape)


def build_review(
    preview: ExtractionPreview, frame_shape: tuple[int, int], *, movie: str = ""
) -> ApertureReview:
    """Return the [`ApertureReview`][] of a finished ``preview`` of a movie of ``frame_shape``.

    Raises ``ValueError`` when the preview did not reach the traces: the stage it stopped at,
    and why.
    """
    import numpy as np

    from tether.analysis.overlap import neighbor_report
    from tether.imaging.aperture import aperture_in_frame

    if not preview.completed or preview.traces is None or preview.registration is None:
        last = preview.stages[-1] if preview.stages else None
        where = "before the first stage" if last is None else f"at {last.name}"
        reason = "" if last is None or not last.message else f": {last.message}"
        raise ValueError(f"the preview stopped {where}{reason}")
    split, detect = preview.stage("split"), preview.stage("detect")
    coloc, extract = preview.stage("colocalize"), preview.stage("extract")
    assert split and detect and coloc and extract  # noqa: S101 - completed checked it
    molecules, traces, registration = preview.molecules, preview.traces, preview.registration
    assert molecules is not None  # noqa: S101 - completed checked it
    window = int(coloc.parameters["window"])
    donor_shape = split.outputs["donor_stack"].shape[1:]
    acceptor_shape = split.outputs["acceptor_stack"].shape[1:]

    donor_xy = np.asarray(detect.outputs["donor_spots"], dtype=np.float64).reshape(-1, 2)
    acceptor_xy = np.asarray(registration.apply_reference_to_moving(donor_xy), dtype=np.float64)
    acceptor_xy = acceptor_xy.reshape(-1, 2)
    # Stage 13, the colocalize guard: the crop must fit in both channels.
    in_frame = aperture_in_frame(donor_xy, shape=donor_shape, window=window) & aperture_in_frame(
        acceptor_xy, shape=acceptor_shape, window=window
    )
    molecule_of = {int(spot): row for row, spot in enumerate(molecules.donor_index)}
    # The curation overlap rule over the molecules the run keeps, with the run's disk.
    disk_radius = float(traces.disk_radius)
    neighbours = neighbor_report(molecules.donor_xy, aperture_radius=disk_radius)

    donor_frame = _frame_xy(donor_xy, split.outputs["donor_geometry"], frame_shape)
    acceptor_frame = _frame_xy(acceptor_xy, split.outputs["acceptor_geometry"], frame_shape)
    spots = []
    for index in range(donor_xy.shape[0]):
        row = molecule_of.get(index)
        distance = None if row is None else float(neighbours.nn_distance[row])
        spots.append(
            ApertureSpot(
                spot=index,
                donor_xy=(float(donor_frame[index, 0]), float(donor_frame[index, 1])),
                acceptor_xy=(float(acceptor_frame[index, 0]), float(acceptor_frame[index, 1])),
                at_edge=not bool(in_frame[index]),
                molecule=row,
                overlaps=row is not None and bool(neighbours.overlaps[row]),
                nn_distance=distance if distance is not None and math.isfinite(distance) else None,
            )
        )
    options = {name: value for stage in preview.stages for name, value in stage.parameters.items()}
    return ApertureReview(
        movie=movie,
        frame_shape=(int(frame_shape[0]), int(frame_shape[1])),
        window=window,
        disk_radius=disk_radius,
        ring_inner=float(traces.ring_inner),
        ring_outer=float(traces.ring_outer),
        overlap_distance=float(neighbours.overlap_distance),
        policy=str(molecules.policy),
        options=_jsonable(options),
        spots=tuple(spots),
        traces=traces,
    )


def preview_trace(review: ApertureReview, molecule: int) -> dict[str, np.ndarray]:
    """Return the preview traces of ``molecule``: ``donor`` and ``acceptor`` intensity.

    The background-corrected intensity per frame (``I = TOT - BG``) that the run stores as
    ``donor_corrected`` and ``acceptor_corrected``. Raises ``IndexError`` for a row the review
    does not hold.
    """
    traces = review.traces
    if traces is None or not 0 <= molecule < traces.n_molecules:
        raise IndexError(f"molecule {molecule} is not a molecule of the review")
    return {
        "donor": traces.donor.intensity[molecule],
        "acceptor": traces.acceptor.intensity[molecule],
    }


def review_movie(
    movie: str | PathLike[str],
    options: ExtractOptions,
    *,
    tmap: str | PathLike[str] | None = None,
    map_path: str | PathLike[str] | None = None,
) -> tuple[ApertureReview, np.ndarray]:
    """Preview ``movie`` and return its [`ApertureReview`][] and the frame to draw it on.

    The frame is the mean of every frame of the raw movie, as ``float32``. The registration
    source means what it means for [`run_preview`][tether.services.preview.run_preview]; the
    movie is closed before this returns. Raises ``ValueError`` when the preview stops before
    the traces, and what ``run_preview`` raises for inputs it cannot read.
    """
    import numpy as np

    from tether.services.preview import run_preview

    reader, preview = run_preview(movie, options, tmap=tmap, map_path=map_path)
    with reader:
        frame = np.asarray(reader.data, dtype=np.float64).mean(axis=0).astype(np.float32)
        review = build_review(preview, frame.shape, movie=Path(movie).name)
    return review, frame


def _jsonable(value: Any) -> Any:
    """``value`` with tuples as lists and non-finite floats as ``None``, for JSON."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item") and callable(value.item) and getattr(value, "ndim", 1) == 0:
        return _jsonable(value.item())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def report_dict(review: ApertureReview, molecules: Iterable[int] = ()) -> dict[str, Any]:
    """Return the JSON report of ``review``, with the preview traces of ``molecules``."""
    selected = sorted({int(m) for m in molecules})
    traces = []
    for molecule in selected:
        trace = preview_trace(review, molecule)
        traces.append(
            {
                "molecule": molecule,
                "donor": [float(v) for v in trace["donor"]],
                "acceptor": [float(v) for v in trace["acceptor"]],
            }
        )
    return {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "tether_version": tether.__version__,
        "movie": review.movie,
        "frame": "mean of all frames",
        "frame_shape": list(review.frame_shape),
        "aperture": {
            "window": review.window,
            "disk_radius": review.disk_radius,
            "ring_inner": review.ring_inner,
            "ring_outer": review.ring_outer,
            "overlap_distance": review.overlap_distance,
        },
        "policy": review.policy,
        "options": review.options,
        "counts": {
            "spots": review.n_spots,
            "molecules": review.n_molecules,
            "at_edge": review.n_edge,
            "overlap": review.n_overlap,
        },
        "spots": [
            {
                "spot": s.spot,
                "status": s.status,
                "donor_xy": list(s.donor_xy),
                "acceptor_xy": list(s.acceptor_xy),
                "molecule": s.molecule,
                "nn_distance": s.nn_distance,
            }
            for s in review.spots
        ],
        "traces": traces,
    }


#: The colour of each mark on the PNG and on the page: ``(r, g, b)``.
STATUS_COLORS: dict[str, tuple[int, int, int]] = {
    "ok": (60, 200, 90),
    "overlap": (230, 60, 200),
    "at-edge": (255, 150, 0),
    "no-partner": (150, 150, 150),
}


def _draw_png(review: ApertureReview, frame: np.ndarray, path: Path) -> None:
    """Draw ``frame`` with every aperture and ring, coloured by mark, into ``path``."""
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle

    height, width = review.frame_shape
    figure = Figure(figsize=(max(4.0, width / 64), max(3.0, height / 64) + 0.6), dpi=100)
    axes = figure.add_subplot()
    image = np.asarray(frame, dtype=np.float64)
    low, high = np.percentile(image, [1, 99.5]) if image.size else (0.0, 1.0)
    axes.imshow(image, cmap="gray", vmin=low, vmax=max(high, low + 1e-9), origin="upper")
    for spot in review.spots:
        color = tuple(c / 255 for c in STATUS_COLORS[spot.status])
        for x, y in (spot.donor_xy, spot.acceptor_xy):
            axes.add_patch(Circle((x, y), review.disk_radius, fill=False, color=color, lw=0.8))
            for radius in (review.ring_inner, review.ring_outer):
                axes.add_patch(Circle((x, y), radius, fill=False, color=color, lw=0.4, ls=":"))
    handles = [
        Line2D([], [], color=tuple(c / 255 for c in rgb), lw=2, label=status)
        for status, rgb in STATUS_COLORS.items()
    ]
    axes.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=4,
        fontsize=7,
        frameon=False,
    )
    axes.set_title(f"{review.movie}: {review.summary()}", fontsize=8)
    axes.set_xlim(-0.5, width - 0.5)
    axes.set_ylim(height - 0.5, -0.5)
    axes.set_axis_off()
    figure.savefig(path, format="png", metadata={"Software": None})


def write_report(
    review: ApertureReview,
    frame: np.ndarray,
    json_path: str | PathLike[str],
    png_path: str | PathLike[str] | None = None,
    *,
    molecules: Sequence[int] = (),
) -> list[Path]:
    """Write the JSON report of ``review`` and, when ``png_path`` is given, its PNG.

    ``molecules`` are the molecules whose preview traces the JSON carries. Returns the paths
    written. The files are the same whoever calls this with the same arguments.
    """
    written = [Path(json_path)]
    text = json.dumps(report_dict(review, molecules), indent=2, allow_nan=False)
    written[0].write_text(text + "\n", encoding="utf-8")
    if png_path is not None:
        written.append(Path(png_path))
        _draw_png(review, frame, written[1])
    return written
