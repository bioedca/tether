# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether map``: make, check and show a channel map file (#353, #918).

Three registered service commands of [`tether.services.maps`][] share the ``map``
subcommand: ``tether map create`` (the ``map`` command), ``tether map check`` and
``tether map show``. Each action parser names its command in the namespace attribute
[`MAP_ROUTE`][], so ``tether.cli.main`` runs the route of that command.

``tether map create`` writes the map file and its report (JSON and PNG); ``--accept
PROJECT`` records the acceptance of the map in that project (ADR-0102). ``tether map
check MAP MOVIE`` exits ``0`` for a map that may register the movie, ``1`` with the reason
for one that may not, and ``2`` for bad arguments or a map or movie that cannot be read.
``tether map show MAP`` prints the map's record, as text or with ``--json``.

Only `argparse` is imported at module level, so ``tether --version`` stays free of the
imaging stack.
"""

from __future__ import annotations

import argparse
import json
import sys

from tether.extract_choices import DONOR_SIDES
from tether.services import CommandFailed, RequestRefused
from tether.services import run as run_service
from tether.services.maps import (
    MapCheckRequest,
    MapCheckResult,
    MapCreateRequest,
    MapCreateResult,
    MapShowRequest,
    MapShowResult,
)

__all__ = [
    "MAP_ROUTE",
    "add_map_check_parser",
    "add_map_create_parser",
    "add_map_show_parser",
    "map_check_request",
    "map_create_request",
    "map_show_request",
    "run_map_check",
    "run_map_create",
    "run_map_show",
]

#: The namespace attribute that names the registered command of a ``map`` action.
MAP_ROUTE = "map_service_command"


def _actions(subparsers: argparse._SubParsersAction) -> argparse._SubParsersAction:
    """Return the actions of the ``map`` subcommand, adding the subcommand the first time."""
    existing = subparsers.choices.get("map")
    if existing is not None:
        return next(
            a
            for a in existing._actions  # noqa: SLF001 - argparse has no public accessor
            if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
        )
    map_parser = subparsers.add_parser(
        "map",
        help="make, check or show a channel map file (.npz)",
        description=(
            "Channel map files: the saved donor<->acceptor registration that "
            "'tether extract --map' applies to a later movie."
        ),
    )
    return map_parser.add_subparsers(dest="map_action", metavar="<action>", required=True)


def add_map_create_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register ``tether map create`` (the ``map`` service command)."""
    actions = _actions(subparsers)
    create = actions.add_parser(
        "create",
        help=summary,
        description=(
            "Detect the beads of both halves, pair them and fit the degree-2 map "
            "(Stages 6-9), then save it as a .npz map file with its source id, its "
            "projection settings and its quality record, and write its report beside it "
            "(NAME.json and NAME.png for NAME.npz). A map over --rms-gate is still "
            "saved, flagged low-confidence; the command then exits 0 and warns."
        ),
    )
    create.add_argument("source", help="a bead TIFF: one image, or a video of frames")
    create.add_argument(
        "-o", "--output", required=True, help="the .npz map file to write (must end in .npz)"
    )
    create.add_argument(
        "--overwrite", action="store_true", help="replace an existing map file and its report"
    )
    create.add_argument(
        "--frames",
        nargs=2,
        type=int,
        default=None,
        metavar=("START", "STOP"),
        help="for a video: the 0-based half-open frame range to project (default: all)",
    )
    create.add_argument(
        "--projection",
        default="max",
        choices=("max", "mean"),
        help="for a video: how the frames are reduced to one image (default: max)",
    )
    create.add_argument(
        "--donor-side",
        default="left",
        choices=DONOR_SIDES,
        help="which half of the frame is the donor channel (default: left)",
    )
    create.add_argument(
        "--rms-gate",
        type=float,
        default=0.5,
        help="registration RMS-residual gate in px (default: 0.5)",
    )
    create.add_argument(
        "--optics",
        default=None,
        metavar="TEXT",
        help=(
            "a text for the optics (objective, filters, camera, binning), kept in the map; "
            "'tether extract --map --optics' compares it with a later movie's"
        ),
    )
    create.add_argument(
        "--pixel-size",
        dest="pixel_size_um",
        type=float,
        default=None,
        metavar="UM",
        help=(
            "the camera pixel size in micrometres, kept in the map; a movie with another "
            "pixel size is refused (default: not known, not compared)"
        ),
    )
    create.add_argument(
        "--frame-steps",
        default=None,
        metavar="STEPS",
        help=(
            "the map's default frame steps, kept in the map: how the frames it is made on "
            "relate to the movies it will be used with (e.g. 'rotate 90'); a movie's own "
            "'tether extract --frame-steps' replaces it (default: none)"
        ),
    )
    create.add_argument(
        "--accept",
        dest="project",
        default=None,
        metavar="PROJECT",
        help=(
            "accept the map in this .tether project, on the evidence of its report: a "
            "map.accept approval with the map id as its subject (ADR-0102)"
        ),
    )
    create.add_argument(
        "--by",
        default=None,
        metavar="NAME",
        help="with --accept: the name of the person who accepts (default: the login name)",
    )
    create.set_defaults(**{MAP_ROUTE: "map"})


def map_create_request(args: argparse.Namespace) -> MapCreateRequest:
    """Build the ``map`` request from the parsed options."""
    start, stop = args.frames if args.frames is not None else (None, None)
    return MapCreateRequest(
        source=args.source,
        output=args.output,
        overwrite=args.overwrite,
        frame_start=start,
        frame_stop=stop,
        projection=args.projection,
        donor_side=args.donor_side,
        rms_gate=args.rms_gate,
        optics=args.optics or "",
        pixel_size_um=args.pixel_size_um,
        frame_steps=args.frame_steps,
        project=args.project,
        accept=args.project is not None,
        by=args.by,
    )


def run_map_create(args: argparse.Namespace) -> int:
    """Handle ``tether map create``; a failed or refused map is exit code 1."""
    if args.by is not None and args.project is None:
        print("tether map create: --by is the person of --accept; give --accept", file=sys.stderr)
        return 2
    request = map_create_request(args)
    try:
        result: MapCreateResult = run_service(request)
    except CommandFailed as exc:
        print(f"tether map create: {exc}", file=sys.stderr)
        return 1
    except RequestRefused as exc:
        print(f"tether map create: {exc}", file=sys.stderr)
        return 2
    print(
        f"Map -> {result.path}: RMS {result.rms_residual:.3f} px over {result.n_pairs} "
        f"bead pair(s), {result.coverage:.0%} of the field covered"
    )
    print(f"  map id: {result.map_id}")
    print(f"  report: {result.report} and {result.report_png}")
    if result.accepted_by:
        print(f"  accepted in {request.project} (map.accept, subject {result.map_id})")
    if result.low_confidence:
        print(
            f"  warning: the RMS residual exceeds the {request.rms_gate} px gate; the map is "
            "flagged low-confidence and its molecules will be tagged "
            "'low-confidence-registration'.",
            file=sys.stderr,
        )
    return 0


def add_map_check_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register ``tether map check``."""
    check = _actions(subparsers).add_parser(
        "check",
        help=summary,
        description=(
            "Run the reuse check that 'tether extract --map' runs: the movie's pixel size, "
            "read from its file, and its optics text (--optics) against the map's. Exit "
            "codes: 0 the map may register the movie, 1 it may not (the reason is on "
            "stderr), 2 bad arguments or a map or movie that cannot be read."
        ),
    )
    check.add_argument("map", help="the .npz map file")
    check.add_argument("movie", help="the movie the map would register")
    check.add_argument(
        "--optics",
        default=None,
        metavar="TEXT",
        help="the optics text of the movie, as 'tether extract --optics' gives it",
    )
    check.add_argument(
        "--confirm-optics",
        action="store_true",
        help="accept an optics text that differs from the map's, as 'tether extract' does",
    )
    check.set_defaults(**{MAP_ROUTE: "map check"})


def map_check_request(args: argparse.Namespace) -> MapCheckRequest:
    """Build the ``map check`` request from the parsed options."""
    return MapCheckRequest(
        map_path=args.map,
        movie=args.movie,
        optics=args.optics,
        confirm_optics=args.confirm_optics,
    )


def run_map_check(args: argparse.Namespace) -> int:
    """Handle ``tether map check``: ``0`` usable, ``1`` not usable, ``2`` not readable."""
    try:
        result: MapCheckResult = run_service(map_check_request(args))
    except CommandFailed as exc:
        print(f"tether map check: {exc}", file=sys.stderr)
        return 2
    for warning in result.warnings:
        state = "confirmed" if result.optics_confirmed else "not confirmed (--confirm-optics)"
        print(f"  warning: {warning}; {state}", file=sys.stderr)
    if result.not_compared:
        print(f"  not compared: {', '.join(result.not_compared)}")
    if not result.usable:
        reasons = list(result.refusals) or ["the optics differ and are not confirmed"]
        print(
            f"tether map check: {args.map} cannot register {args.movie}: " + "; ".join(reasons),
            file=sys.stderr,
        )
        return 1
    print(f"Map {result.map_id} can register {args.movie}")
    return 0


def add_map_show_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register ``tether map show``."""
    show = _actions(subparsers).add_parser(
        "show",
        help=summary,
        description=(
            "Print the record of a map file: its id, origin, sources, channel layout and "
            "geometry, optics, pixel size, quality record and the time it was saved. Exit "
            "codes: 0 printed, 2 bad arguments or a map that cannot be read."
        ),
    )
    show.add_argument("map", help="the .npz map file")
    show.add_argument("--json", action="store_true", help="print the record as one JSON object")
    show.set_defaults(**{MAP_ROUTE: "map show"})


def map_show_request(args: argparse.Namespace) -> MapShowRequest:
    """Build the ``map show`` request from the parsed options."""
    return MapShowRequest(map_path=args.map)


def _text(value: object) -> str:
    if value is None or value == "":
        return "not known"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def run_map_show(args: argparse.Namespace) -> int:
    """Handle ``tether map show``; a map that cannot be read is exit code 2."""
    try:
        result: MapShowResult = run_service(map_show_request(args))
    except CommandFailed as exc:
        print(f"tether map show: {exc}", file=sys.stderr)
        return 2
    record = result.record
    if args.json:
        print(json.dumps(record, sort_keys=True, allow_nan=False))
        return 0
    print(f"Map {result.path}")
    for key, value in record.items():
        if key == "quality" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "pair_residuals"}
        print(f"  {key}: {_text(value)}")
    return 0
