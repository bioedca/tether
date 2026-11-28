# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``map`` service command: a channel map file made from a bead video or image.

[`MapCreateRequest`][] holds what ``tether map create`` takes on its command line, with
the same defaults; [`run`][] makes the map with
[`tether.imaging.beadmap.bead_map_from_file`][] and saves it with
[`tether.imaging.map_asset.save_map_asset`][], with the channel layout, the optics text
and the pixel size the request gives (#916). The saved ``.npz`` is the file that
``tether extract --map`` applies to a later movie (#353). The application's
calibration action runs this same command, so the two cannot drift (ADR-0100).

``tether map create`` also writes the map's report beside it (#918): ``<name>.json``
and ``<name>.png`` for ``<name>.npz``, the evidence an acceptance of the map records.
With ``accept`` a person accepts the map in ``project``; in a recipe run a ``map.accept``
policy accepts it (ADR-0102). Either way one ``map.accept`` row of
``/settings/approvals`` records it, with the map id as its subject
([`tether.project.approvals`][]), as an acceptance in the application does.

[`MapCheckRequest`][] (``tether map check``) runs the reuse check of #916 on a map and a
movie, as extraction runs it; [`MapShowRequest`][] (``tether map show``) reads a map's
record. The imaging stack is imported when a command runs, not when this module is
imported.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from tether.services.base import CommandFailed, Request, RequestRefused

if TYPE_CHECKING:
    import numpy as np

    from tether.imaging.map_asset import MapAsset
    from tether.services.recipe import ApprovalGate

__all__ = [
    "MAP_ACCEPT",
    "MapCheckRequest",
    "MapCheckResult",
    "MapCreateRequest",
    "MapCreateResult",
    "MapShowRequest",
    "MapShowResult",
    "map_summary",
    "report_paths",
    "run",
    "run_check",
    "run_show",
]

#: The approval step of a map (ADR-0102 §1).
MAP_ACCEPT = "map.accept"


@dataclasses.dataclass(frozen=True, kw_only=True)
class MapCreateRequest(Request):
    """Make a channel map file from a bead video or one bead image.

    The fields are the options of ``tether map create`` (``docs/cli.md``) under their
    ``argparse`` destination names, with the same defaults. ``frame_start`` and
    ``frame_stop`` are the 0-based half-open frame range of a video, given together or
    not at all (``None`` = every frame); with an image both stay ``None`` and
    ``projection`` stays ``"max"``. ``optics`` is the user's text for the optics
    (objective, filters, camera, binning) and ``pixel_size_um`` the camera pixel size in
    micrometres (``None`` = not known); both go into the map file, and the reuse check
    compares them with a later movie's (#916). ``frame_steps`` is the map's default frame
    steps in their text form (#1435), such as ``"rotate 90"``, or ``None`` for no default.

    ``project`` is the ``.tether`` project that records the map's acceptance. With
    ``accept`` the person ``by`` accepts the map there (``None`` reads the name as
    [`default_labeler`][tether.project.labels.default_labeler] does). In a recipe run
    ``accept`` stays false and the step's ``map.accept`` policy decides; without
    ``project`` nothing is accepted.
    """

    COMMAND: ClassVar[str] = "map"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("source", "output", "project")

    source: str
    output: str
    overwrite: bool = False
    frame_start: int | None = None
    frame_stop: int | None = None
    projection: str = "max"
    donor_side: str = "left"
    rms_gate: float = 0.5
    optics: str = ""
    pixel_size_um: float | None = None
    frame_steps: str | None = None
    project: str | None = None
    accept: bool = False
    by: str | None = None


@dataclasses.dataclass(frozen=True)
class MapCreateResult:
    """The outcome of a ``map`` command: the files written and the map's quality record.

    ``report`` and ``report_png`` are the map's report; ``accepted_by`` is ``"person"``
    or ``"policy"`` when an acceptance was recorded, else ``""``.
    """

    path: Path
    rms_residual: float
    n_pairs: int
    coverage: float
    low_confidence: bool
    source_id: str
    map_id: str = ""
    report: Path | None = None
    report_png: Path | None = None
    accepted_by: str = ""


def report_paths(output: str | os.PathLike[str]) -> tuple[Path, Path]:
    """Return the JSON and PNG report paths of the map file ``output``: beside it, same stem."""
    path = Path(output)
    return path.with_suffix(".json"), path.with_suffix(".png")


def run(request: MapCreateRequest, *, approvals: Any = None) -> MapCreateResult:
    """Run *request*; raise [`CommandFailed`][tether.services.base.CommandFailed] on a user error.

    The map is written to a sibling temporary file and moved into place in one step, so
    a failed run leaves no partial file and an existing map is replaced only by a
    complete one (and only with ``overwrite``). Without ``overwrite`` the move never
    replaces a file, so a map another session saves at ``output`` while this one is
    fitted is kept, and this run is refused. A map over the RMS gate is still saved:
    it carries its low-confidence verdict, as the native fit does (§7.1). The report
    ([`report_paths`][]) is moved into place after the map, by the same rule.

    An ``overwrite`` replacement holds the output's writer lock
    ([`publish_lock`][tether.project.lock.publish_lock]) as ``tether extract
    --overwrite`` does, so it is refused while another writer holds that lock.

    ``approvals`` is the gate of a recipe run (ADR-0102): with ``project`` set, the
    step's ``map.accept`` policy decides, and with no policy the run stops
    ([`ApprovalStop`][tether.services.recipe.ApprovalStop]) once the map and its report
    are written. A resumed run finds the map at ``output`` and goes on from it, without
    fitting again, once it is accepted.
    """
    import warnings
    from uuid import uuid4

    import tether
    from tether.imaging.beadmap import BeadMapError, bead_map_from_file
    from tether.imaging.calibrate import MapFileError, OverGateRegistrationWarning
    from tether.imaging.layout import LAYOUT_SIDE_BY_SIDE, ChannelLayout
    from tether.imaging.map_asset import MapAsset, load_map_asset, save_map_asset
    from tether.imaging.recipe import ChannelRecipe, ChannelRecipeError
    from tether.project.lock import LockError

    output = Path(request.output)
    report, png = report_paths(output)
    if output.suffix.lower() != ".npz":
        raise CommandFailed(f"the map file must end in .npz, got {output.name!r}")
    gate = approvals if request.project else None
    actor = _check_acceptance(request, approvals)
    if output.exists() and not request.overwrite:
        if gate is None:
            raise CommandFailed(f"output exists: {output} (use --overwrite)")
        try:  # a resumed recipe run: the map of the stopped run is at output
            asset = load_map_asset(output)
        except MapFileError as exc:
            raise CommandFailed(f"output exists: {output}: {exc}") from exc
        accepted = _accept(request, asset, report, gate=gate, actor=actor)
        return _result(asset, output, report, png, accepted)
    for path in (report, png):
        if path.exists() and not request.overwrite:
            raise CommandFailed(f"the map's report exists: {path} (use --overwrite)")
    if (request.frame_start is None) != (request.frame_stop is None):
        raise CommandFailed("--frames takes a start and a stop; pass both or neither")
    if request.pixel_size_um is not None and not request.pixel_size_um > 0:
        raise CommandFailed(f"--pixel-size must be > 0, got {request.pixel_size_um}")
    try:
        frame_steps = (
            None if request.frame_steps is None else ChannelRecipe.parse(request.frame_steps)
        )
    except ChannelRecipeError as exc:
        raise CommandFailed(f"--frame-steps {request.frame_steps!r}: {exc}") from exc
    frames = None if request.frame_start is None else (request.frame_start, request.frame_stop)
    token = uuid4().hex
    tmp = output.with_name(f"{output.stem}.{token}.tmp.npz")
    tmp_report = output.with_name(f"{output.stem}.{token}.tmp.json")
    tmp_png = output.with_name(f"{output.stem}.{token}.tmp.png")
    try:
        with warnings.catch_warnings():
            # The verdict is reported once, in the result; mute the library's warning.
            warnings.simplefilter("ignore", OverGateRegistrationWarning)
            result = bead_map_from_file(
                request.source,
                frames=frames,
                projection=request.projection,
                donor_side=request.donor_side,
                gate_px=request.rms_gate,
                on_over_gate="warn",
                app_version=tether.__version__,
            )
        # The bead map splits the frame side by side (#915); the layout goes with it.
        asset = MapAsset.from_registration_map(
            result.registration_map,
            channel_layout=ChannelLayout(kind=LAYOUT_SIDE_BY_SIDE, donor=request.donor_side),
            optics=request.optics,
            pixel_size_um=request.pixel_size_um,
            app_version=tether.__version__,
            frame_steps=frame_steps,
        )
        written = save_map_asset(asset, tmp)
        _write_report(asset, written, output.name, result.image, tmp_report, tmp_png)
        _publish(written, output, overwrite=request.overwrite)
        try:
            _publish(tmp_report, report, overwrite=request.overwrite)
            _publish(tmp_png, png, overwrite=request.overwrite)
        except FileExistsError as exc:
            raise CommandFailed(
                f"the map is written to {output}, but its report exists: {exc.filename}"
            ) from exc
    except FileExistsError as exc:  # another session made the file while this one fitted
        raise CommandFailed(f"output exists: {output} (use --overwrite)") from exc
    except (BeadMapError, LockError, OSError, ValueError) as exc:
        raise CommandFailed(str(exc)) from exc
    finally:
        for path in (tmp, tmp_report, tmp_png):
            path.unlink(missing_ok=True)
    accepted = _accept(request, asset, report, gate=gate, actor=actor)
    return _result(asset, output, report, png, accepted)


def _check_acceptance(request: MapCreateRequest, approvals: ApprovalGate | None) -> str:
    """Refuse an acceptance that cannot be recorded, before any work; return the person."""
    if request.accept and approvals is not None:
        raise RequestRefused("a recipe accepts a map only with a map.accept policy, not 'accept'")
    if request.accept and not request.project:
        raise RequestRefused("an acceptance needs the project that records it (--accept PROJECT)")
    if request.project and not request.accept and approvals is None:
        raise RequestRefused("a project records an acceptance only with 'accept' (--accept)")
    if request.project and not Path(request.project).is_file():
        raise RequestRefused(
            f"the project that records the acceptance is not a file: {request.project}"
        )
    if not request.accept:
        return ""
    from tether.project.labels import default_labeler  # noqa: PLC0415

    name = request.by if request.by is not None else default_labeler()
    if not name.strip():
        raise RequestRefused("an acceptance needs the name of the person (--by)")
    return name


def _accept(
    request: MapCreateRequest,
    asset: MapAsset,
    report: Path,
    *,
    gate: ApprovalGate | None,
    actor: str,
) -> str:
    """Record the map's acceptance as ``request`` asks; return ``person``, ``policy`` or ``""``."""
    from tether.services.recipe import ApprovalStop  # noqa: PLC0415

    project = request.project
    if not project or (gate is None and not request.accept):
        return ""
    subject = asset.map_id
    if gate is not None:
        policy = gate.decide(MAP_ACCEPT, subject, project=project, report=str(report))
        if policy is None:
            return ""  # accepted already, by a person or an earlier run's policy
        rms = float(asset.registration_map.rms_residual)
        gate_px = float(policy.values["gate_px"])
        if not math.isfinite(rms):
            approve = policy.values["no_residual"] == "approve"
            found = "the map has no measured RMS residual"
        else:
            approve = rms <= gate_px or policy.otherwise == "flag"
            found = f"the map's RMS residual {rms:.3f} px is over the policy's {gate_px:g} px"
        if not approve:
            if policy.otherwise == "fail":
                raise CommandFailed(f"{found} (map.accept otherwise: fail)")
            raise ApprovalStop(
                MAP_ACCEPT, subject, project=project, report=str(report), reason=found
            )
    _record(project, subject, report, gate=gate, actor=actor)
    return "policy" if gate is not None else "person"


def _record(
    project_path: str, subject: str, report: Path, *, gate: ApprovalGate | None, actor: str
) -> None:
    """Append the ``map.accept`` row to ``project_path`` under its writer lock."""
    from tether.project.approvals import record_approval  # noqa: PLC0415
    from tether.project.core import Project  # noqa: PLC0415
    from tether.project.lock import LockError  # noqa: PLC0415

    try:
        project = Project.open(project_path)
        project.acquire_lock()
    except (LockError, OSError, ValueError) as exc:
        raise CommandFailed(f"cannot record the acceptance in {project_path}: {exc}") from exc
    try:
        if gate is not None:
            gate.record(project, step=MAP_ACCEPT, subject=subject, report=str(report))
        else:
            record_approval(
                project, step=MAP_ACCEPT, subject=subject, by="person", actor=actor, report=report
            )
    except (LockError, OSError, ValueError) as exc:
        raise CommandFailed(f"cannot record the acceptance in {project_path}: {exc}") from exc
    finally:
        project.release_lock()


def _result(
    asset: MapAsset, output: Path, report: Path, png: Path, accepted_by: str
) -> MapCreateResult:
    reg_map = asset.registration_map
    quality = asset.quality or {}
    return MapCreateResult(
        path=output,
        rms_residual=float(reg_map.rms_residual),
        n_pairs=int(quality.get("n_pairs", reg_map.n_control_points)),
        coverage=float(quality.get("coverage", math.nan)),
        low_confidence=bool(reg_map.low_confidence),
        source_id=asset.source_ids[0] if asset.source_ids else "",
        map_id=asset.map_id,
        report=report,
        report_png=png,
        accepted_by=accepted_by,
    )


def _number(value: float) -> float | None:
    """``value`` for JSON: a number, or ``None`` for one that is not finite."""
    return float(value) if math.isfinite(value) else None


def map_summary(asset: MapAsset) -> dict[str, Any]:
    """Return what ``tether map show`` prints and the map report holds: the map's record.

    It is [`MapAsset.record`][tether.imaging.map_asset.MapAsset.record] with the quality
    record, the RMS residual and its gate, the low-confidence verdict, the number of
    control points, the channel geometry and the Tether version that saved the map. A
    residual that is not a number (an imported ``.tmap`` without control points) is
    ``null``. The transform itself is not in it.
    """
    from tether.imaging.recipe import ChannelRecipe  # noqa: PLC0415

    reg_map = asset.registration_map
    geometry = asset.geometry
    return {
        **asset.record(),
        "app_version": asset.app_version,
        "rms_residual": _number(float(reg_map.rms_residual)),
        "gate_px": _number(float(reg_map.gate_px)),
        "low_confidence": bool(reg_map.low_confidence),
        "n_control_points": int(reg_map.n_control_points),
        "geometry": None
        if geometry is None
        else {
            "donor": str(ChannelRecipe.from_geometry(geometry[0])),
            "acceptor": str(ChannelRecipe.from_geometry(geometry[1])),
        },
        "quality": asset.quality,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_report(
    asset: MapAsset, written: Path, name: str, image: np.ndarray, json_path: Path, png_path: Path
) -> None:
    """Write the map's JSON report and its PNG: the bead image and the pair residuals."""
    import numpy as np  # noqa: PLC0415
    from matplotlib.figure import Figure  # noqa: PLC0415

    summary = map_summary(asset)
    data = {"report": "tether-map", "version": 1, "map_file": name, "map_sha256": _sha256(written)}
    data.update(summary)
    json_path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    figure = Figure(figsize=(9.0, 3.6), dpi=100)
    left, right = figure.subplots(1, 2, width_ratios=(2, 1))
    frame = np.asarray(image, dtype=np.float64)
    low, high = np.percentile(frame, [1, 99.5]) if frame.size else (0.0, 1.0)
    left.imshow(frame, cmap="gray", vmin=low, vmax=max(high, low + 1e-9), origin="upper")
    left.set_axis_off()
    left.set_title(f"{name}: {asset.map_id}", fontsize=8)
    residuals = [r for r in (asset.quality or {}).get("pair_residuals") or [] if math.isfinite(r)]
    gate = summary["gate_px"]
    upper = max([*residuals, gate or 0.0, 1e-3]) * 1.05
    right.hist(residuals, bins=max(5, min(30, len(residuals))), range=(0.0, upper), color="0.4")
    if gate is not None:
        right.axvline(gate, color="tab:red", lw=1, label=f"gate {gate:g} px")
        right.legend(fontsize=7, frameon=False)
    rms = summary["rms_residual"]
    rms_text = "no residual" if rms is None else f"RMS {rms:.3f} px"
    right.set_title(f"{rms_text}, {summary['n_control_points']} pair(s)", fontsize=8)
    right.set_xlabel("pair residual (px)", fontsize=8)
    right.set_ylabel("pairs", fontsize=8)
    right.tick_params(labelsize=7)
    figure.tight_layout()
    figure.savefig(png_path, format="png", metadata={"Software": None})


def _publish(written: Path, output: Path, *, overwrite: bool) -> None:
    """Move the complete map file *written* to *output*; without *overwrite*, never replace.

    ``os.replace`` replaces a file at *output* in one step, under *output*'s writer
    lock ([`publish_lock`][tether.project.lock.publish_lock]). Without *overwrite* a
    hard link is made instead, which fails with [`FileExistsError`][] if *output*
    exists, also when it appeared after the first check. Where the file system has no
    hard links, the bytes are copied to a file opened with ``"xb"``, which refuses an
    existing file the same way. That copy runs under the same lock, taken before the
    file is made, so a failed copy can remove its own file without writing anything
    first (the lock's files exist before a full disk can refuse them), and no
    replacement can land between its check and its removal (#1240).
    """
    from tether.project.lock import publish_lock

    if overwrite:
        with publish_lock(output) as guard:
            guard()  # refuses if the lock was taken over since it was acquired
            os.replace(written, output)
        return
    try:
        os.link(written, output)
        return
    except FileExistsError:
        raise
    except OSError:  # no hard links here (a FAT drive, some network shares)
        pass
    with publish_lock(output):
        f = open(output, "xb")  # noqa: SIM115 - FileExistsError if another session made it
        made = os.fstat(f.fileno())  # which file is this call's, for the cleanup below
        try:
            with f:  # the close flushes, and a failed flush is a failed write
                f.write(written.read_bytes())
        except BaseException:
            _unlink_if_made(output, made)
            raise


def _unlink_if_made(output: Path, made: os.stat_result) -> None:
    """Remove *output* only while it is still the file this call created.

    The caller holds *output*'s writer lock, which an ``--overwrite`` publish holds
    across its ``os.replace``, so that replacement cannot land between this check and
    the removal. The removal writes nothing, so it still runs on a full disk.
    """
    try:
        if os.path.samestat(os.stat(output), made):
            output.unlink()
    except FileNotFoundError:
        pass


# --- tether map check and tether map show (#918) -------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class MapCheckRequest(Request):
    """Check whether the map file ``map_path`` may register ``movie`` (the reuse check, #916).

    The check is the one ``tether extract --map`` runs: the movie's pixel size, read from
    its file, against the map's, and ``optics`` (the movie's optics text, as
    ``--optics`` of ``tether extract``) against the map's. ``confirm_optics`` confirms an
    optics text that differs, as ``--confirm-optics`` does.
    """

    COMMAND: ClassVar[str] = "map check"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("map_path", "movie")

    map_path: str
    movie: str
    optics: str | None = None
    confirm_optics: bool = False


@dataclasses.dataclass(frozen=True)
class MapCheckResult:
    """The verdict of a ``map check``: ``usable``, and each difference the check found.

    ``refusals`` refuse the map; ``warnings`` are optics differences, which refuse it
    unless ``optics_confirmed``; ``not_compared`` names each field one side does not know.
    """

    map_id: str
    usable: bool
    refusals: tuple[str, ...]
    warnings: tuple[str, ...]
    not_compared: tuple[str, ...]
    optics_confirmed: bool
    movie_pixel_size_um: float | None


def run_check(request: MapCheckRequest) -> MapCheckResult:
    """Run the reuse check on the map and the movie of *request*.

    Raises [`CommandFailed`][tether.services.base.CommandFailed] when the map or the movie
    cannot be read. A map that may not be used is a result, not an error.
    """
    from tether.imaging.calibrate import MapFileError  # noqa: PLC0415
    from tether.imaging.map_asset import MapFacts, check_map_reuse, load_map_asset  # noqa: PLC0415
    from tether.io.frames import open_extraction_movie  # noqa: PLC0415

    try:
        asset = load_map_asset(request.map_path)
    except MapFileError as exc:
        raise CommandFailed(str(exc)) from exc
    try:
        with open_extraction_movie(request.movie) as movie:
            pixel_size = movie.pixel_size_um
    except Exception as exc:  # noqa: BLE001 - any reader refusal is "cannot be read"
        raise CommandFailed(f"cannot read the movie {request.movie}: {exc}") from exc
    # The movie carries no layout or geometry of its own: extraction splits it with the map's.
    check = check_map_reuse(
        asset.facts(), MapFacts(pixel_size_um=pixel_size, optics=request.optics or "")
    )
    confirmed = bool(check.warnings) and request.confirm_optics
    return MapCheckResult(
        map_id=asset.map_id,
        usable=not check.refused and (not check.warnings or confirmed),
        refusals=check.refusals,
        warnings=check.warnings,
        not_compared=check.not_compared,
        optics_confirmed=confirmed,
        movie_pixel_size_um=pixel_size,
    )


@dataclasses.dataclass(frozen=True, kw_only=True)
class MapShowRequest(Request):
    """Read the record of the map file ``map_path``."""

    COMMAND: ClassVar[str] = "map show"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("map_path",)

    map_path: str


@dataclasses.dataclass(frozen=True)
class MapShowResult:
    """The map file read and its record ([`map_summary`][])."""

    path: Path
    record: dict[str, Any]


def run_show(request: MapShowRequest) -> MapShowResult:
    """Read the map; raise [`CommandFailed`][tether.services.base.CommandFailed] if it cannot be."""
    from tether.imaging.calibrate import MapFileError  # noqa: PLC0415
    from tether.imaging.map_asset import load_map_asset  # noqa: PLC0415

    try:
        asset = load_map_asset(request.map_path)
    except MapFileError as exc:
        raise CommandFailed(str(exc)) from exc
    return MapShowResult(path=Path(request.map_path), record=map_summary(asset))
