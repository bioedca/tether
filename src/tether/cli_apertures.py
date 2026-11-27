# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether apertures``: the aperture and background report from the command line (#922).

The report of the Prepare page **Apertures and background**
([`tether.gui.apertures`][]): the command previews the movie with
[`review_movie`][tether.services.apertures.review_movie] and writes the report with
[`write_report`][tether.services.apertures.write_report], the two functions the page calls, so
for the same movie, options, registration source and molecules the files are the same.

The options are those of an extraction profile (``--profile``, the file the GUI and
``tether extract --profile`` read), with the default options without one, and the four
aperture options on top. The review writes nothing to a project. Exit codes: 0 written,
1 the review failed, 2 refused (an existing output without ``--overwrite``, bad options).

Only `argparse` is imported at module level, so ``tether --version`` stays free of the
imaging stack.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["add_apertures_parser", "run_apertures"]

_APERTURE_FLAGS = (
    ("window", int, "aperture / crop-box side length in px, odd"),
    ("disk_radius", float, "radius of the signal aperture (PSF disk) in px"),
    ("ring_inner", float, "inner radius of the background ring in px"),
    ("ring_outer", float, "outer radius of the background ring in px"),
)


def add_apertures_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``apertures`` subcommand."""
    parser = subparsers.add_parser(
        "apertures",
        help="write the aperture and background report of a movie, before the run",
        description=(
            "Preview a movie as the extraction would run it, and write the report of the "
            "Prepare page 'Apertures and background': every spot with its signal aperture "
            "and background ring, the spots at an edge and the overlapping ones with their "
            "counts, and the preview traces of the molecules named with --molecule. "
            "Writes nothing to a project. Exit codes: 0 written, 1 failed, 2 refused."
        ),
    )
    parser.add_argument("movie", help="the dual-channel movie")
    parser.add_argument("--json", required=True, help="the JSON report to write")
    parser.add_argument("--png", help="the PNG of the frame with the apertures to write")
    parser.add_argument("--profile", help="an extraction profile (.json) with the options")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--map", dest="map_path", help="a channel map file (.npz)")
    source.add_argument("--tmap", help="a Deep-LASI .tmap")
    for name, kind, text in _APERTURE_FLAGS:
        parser.add_argument(
            "--" + name.replace("_", "-"),
            type=kind,
            default=None,
            help=f"{text} (default: profile)",
        )
    parser.add_argument(
        "--molecule",
        type=int,
        action="append",
        default=[],
        metavar="N",
        help="a molecule whose preview traces the report carries (repeatable)",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")


def run_apertures(args: argparse.Namespace) -> int:
    """Handle ``tether apertures``; return the exit code."""
    import dataclasses

    from tether.project.extract import ExtractionError, ExtractOptions
    from tether.services.apertures import review_movie, write_report

    outputs = [Path(args.json)] + ([Path(args.png)] if args.png else [])
    existing = [str(p) for p in outputs if p.exists()]
    if existing and not args.overwrite:
        print(f"tether apertures: {', '.join(existing)} exists; pass --overwrite", file=sys.stderr)
        return 2
    try:
        if args.profile:
            from tether.services.profile import read_profile

            options = read_profile(args.profile).extract_options()
        else:
            options = ExtractOptions()
        given = {name: getattr(args, name) for name, *_ in _APERTURE_FLAGS}
        options = dataclasses.replace(options, **{k: v for k, v in given.items() if v is not None})
    except (ExtractionError, OSError, ValueError) as exc:
        print(f"tether apertures: {exc}", file=sys.stderr)
        return 2
    try:
        review, frame = review_movie(args.movie, options, tmap=args.tmap, map_path=args.map_path)
        written = write_report(review, frame, args.json, args.png, molecules=args.molecule)
    except (ExtractionError, OSError, ValueError, IndexError) as exc:
        print(f"tether apertures: {exc}", file=sys.stderr)
        return 1
    print(review.summary())
    for path in written:
        print(f"  wrote {path}")
    return 0
