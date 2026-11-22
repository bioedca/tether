# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``map`` service command: a channel map file made from a bead video or image.

[`MapCreateRequest`][] holds what ``tether map create`` takes on its command line, with
the same defaults; [`run`][] makes the map with
[`tether.imaging.beadmap.bead_map_from_file`][] and saves it with
[`tether.imaging.calibrate.save_map`][]. The saved ``.npz`` is the file that
``tether extract --map`` applies to a later movie (#353). The application's
calibration action runs this same command, so the two cannot drift (ADR-0100).

The command line is ``tether map create``; the check and show commands and the map
report come with #918. The imaging stack is imported when the command runs, not when
this module is imported.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import ClassVar

from tether.services.base import CommandFailed, Request

__all__ = ["MapCreateRequest", "MapCreateResult", "run"]


@dataclasses.dataclass(frozen=True, kw_only=True)
class MapCreateRequest(Request):
    """Make a channel map file from a bead video or one bead image.

    The fields are the options of ``tether map create`` (``docs/cli.md``) under their
    ``argparse`` destination names, with the same defaults. ``frame_start`` and
    ``frame_stop`` are the 0-based half-open frame range of a video, given together or
    not at all (``None`` = every frame); with an image both stay ``None`` and
    ``projection`` stays ``"max"``.
    """

    COMMAND: ClassVar[str] = "map"
    VERSION: ClassVar[int] = 1
    PATH_FIELDS: ClassVar[tuple[str, ...]] = ("source", "output")

    source: str
    output: str
    overwrite: bool = False
    frame_start: int | None = None
    frame_stop: int | None = None
    projection: str = "max"
    donor_side: str = "left"
    rms_gate: float = 0.5


@dataclasses.dataclass(frozen=True)
class MapCreateResult:
    """The outcome of a ``map`` command: the file written and its quality record."""

    path: Path
    rms_residual: float
    n_pairs: int
    coverage: float
    low_confidence: bool
    source_id: str


def run(request: MapCreateRequest) -> MapCreateResult:
    """Run *request*; raise [`CommandFailed`][tether.services.base.CommandFailed] on a user error.

    The map is written to a sibling temporary file and moved into place in one step, so
    a failed run leaves no partial file and an existing map is replaced only by a
    complete one (and only with ``overwrite``). Without ``overwrite`` the move never
    replaces a file, so a map another session saves at ``output`` while this one is
    fitted is kept, and this run is refused. A map over the RMS gate is still saved:
    it carries its low-confidence verdict, as the native fit does (§7.1).

    An ``overwrite`` replacement holds the output's writer lock
    ([`publish_lock`][tether.project.lock.publish_lock]) as ``tether extract
    --overwrite`` does, so it is refused while another writer holds that lock.
    """
    import warnings
    from uuid import uuid4

    import tether
    from tether.imaging.beadmap import BeadMapError, bead_map_from_file
    from tether.imaging.calibrate import OverGateRegistrationWarning, save_map
    from tether.project.lock import LockError

    output = Path(request.output)
    if output.suffix.lower() != ".npz":
        raise CommandFailed(f"the map file must end in .npz, got {output.name!r}")
    if output.exists() and not request.overwrite:
        raise CommandFailed(f"output exists: {output} (use --overwrite)")
    if (request.frame_start is None) != (request.frame_stop is None):
        raise CommandFailed("--frames takes a start and a stop; pass both or neither")
    frames = None if request.frame_start is None else (request.frame_start, request.frame_stop)
    tmp = output.with_name(f"{output.stem}.{uuid4().hex}.tmp.npz")
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
        written = save_map(result.registration_map, tmp)
        _publish(written, output, overwrite=request.overwrite)
    except FileExistsError as exc:  # another session made the file while this one fitted
        raise CommandFailed(f"output exists: {output} (use --overwrite)") from exc
    except (BeadMapError, LockError, OSError, ValueError) as exc:
        raise CommandFailed(str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    reg_map = result.registration_map
    return MapCreateResult(
        path=output,
        rms_residual=float(reg_map.rms_residual),
        n_pairs=int(result.quality.n_pairs),
        coverage=float(result.quality.coverage),
        low_confidence=bool(reg_map.low_confidence),
        source_id=result.source.source_id,
    )


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
