# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``tether`` command-line entry point (PRD §7.11, NFR-REPRO).

A thin headless front door over :mod:`tether.project`. ``tether --version``
reports the git-derived app version (NFR-REPRO: "the app version is derived from
git"); ``tether extract`` runs the M1 native extraction pipeline (movie ->
``.tether``) — see :mod:`tether.project.extract`.

The CLI deliberately uses the standard-library :mod:`argparse` rather than a
third-party framework: a base ``conda-lock`` regeneration to add click/typer is
not justified by the current command surface (the pin-and-hold invariant). The
heavy imaging/IO stack is imported lazily inside the ``extract`` handler so that
``tether --version`` stays dependency-light.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from tether import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level ``tether`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="tether",
        description="Tether - a single-molecule FRET desktop suite (headless core).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tether {__version__}",
        help="show the git-derived Tether version and exit",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _add_extract_parser(subparsers)
    return parser


def _add_extract_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``extract`` subcommand (movie -> ``.tether``)."""
    extract = subparsers.add_parser(
        "extract",
        help="extract traces from a dual-channel movie into a .tether project",
        description=(
            "Run the native extraction pipeline (split -> detect -> register -> "
            "colocalize -> integrate) on a dual-channel TIFF movie and write a "
            "new .tether project."
        ),
    )
    extract.add_argument("movie", help="path to the dual-channel TIFF movie")
    extract.add_argument(
        "-o", "--output", required=True, help="path to the .tether project to create"
    )
    extract.add_argument(
        "--overwrite", action="store_true", help="overwrite an existing output project"
    )
    extract.add_argument(
        "--donor-side",
        default="left",
        metavar="{left,right}",
        help="which horizontal half is the donor channel (default: left)",
    )
    extract.add_argument(
        "--detection-mode",
        default=None,
        metavar="{wavelet,intensity,bandpass}",
        help=(
            "particle-detection method (Deep-LASI findPart mode; default: wavelet). "
            "'intensity'/'bandpass' also honor --detection-threshold. "
            "Mutually exclusive with --tdat, which supplies the mode"
        ),
    )
    extract.add_argument(
        "--detection-threshold",
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "detection threshold as a fraction of the detection-image max, in [0, 1) "
            "(intensity/bandpass modes only; default: each mode's own — intensity 0.5, "
            "bandpass 0.98; ignored by wavelet). Mutually exclusive with --tdat"
        ),
    )
    extract.add_argument(
        "--window",
        type=int,
        default=21,
        help="aperture / crop-box side length in px, odd (default: 21)",
    )
    extract.add_argument(
        "--min-separation",
        type=float,
        default=None,
        help=(
            "minimum spot separation in px; unset uses each detection mode's faithful "
            "default (wavelet 8, intensity/bandpass 3)"
        ),
    )
    extract.add_argument(
        "--detection-block",
        type=int,
        default=50,
        help="moving-average block size for the detection image (default: 50)",
    )
    extract.add_argument(
        "--prealign",
        default="translation",
        metavar="{translation,similarity}",
        help="registration prealign degrees of freedom (default: translation)",
    )
    extract.add_argument(
        "--pair-tol",
        type=float,
        default=2.0,
        help="control-point pairing tolerance in px (default: 2)",
    )
    extract.add_argument(
        "--coloc-distance",
        type=float,
        default=3.0,
        help="acceptor colocalization distance in px (default: 3)",
    )
    extract.add_argument(
        "--rms-gate",
        type=float,
        default=0.5,
        help="registration RMS-residual gate in px (default: 0.5)",
    )
    extract.add_argument(
        "--tmap",
        default=None,
        metavar="PATH",
        help=(
            "apply an imported Deep-LASI .tmap instead of a native fit; splits at "
            "the .tmap's own channel geometry (--donor-side is then ignored)"
        ),
    )
    extract.add_argument(
        "--tdat",
        default=None,
        metavar="PATH",
        help=(
            "auto-apply the particle-detection mode decoded from a Deep-LASI .tdat "
            "(temp/ParticleDetectionMode), so extraction matches the method the movie "
            "was detected with; mutually exclusive with --detection-mode/-threshold"
        ),
    )


def _run_extract(args: argparse.Namespace) -> int:
    """Handle ``tether extract``; map :class:`ExtractionError` to exit code 1."""
    # Lazy: keep the imaging/IO/HDF5 stack off the ``--version`` path.
    from tether.project.extract import ExtractionError, ExtractOptions, extract_movie

    try:
        # --detection-mode/-threshold and --tdat are two ways to set the same
        # detection settings; combining them is ambiguous (which wins?), so refuse
        # it up front rather than silently letting one override the other.
        if args.tdat is not None and (
            args.detection_mode is not None or args.detection_threshold is not None
        ):
            raise ExtractionError(
                "--detection-mode/--detection-threshold cannot be combined with --tdat "
                "(the .tdat supplies the detection mode); pass one or the other"
            )
        options = ExtractOptions(
            donor_side=args.donor_side,
            # --detection-mode defaults to None on the CLI so an explicit choice is
            # distinguishable from no flag; resolve to the library default here. When
            # --tdat is given, extract_movie overrides this with the decoded mode.
            detection_mode=args.detection_mode or "wavelet",
            detection_threshold=args.detection_threshold,
            window=args.window,
            min_separation=args.min_separation,
            detection_block=args.detection_block,
            prealign=args.prealign,
            pair_tol=args.pair_tol,
            coloc_distance=args.coloc_distance,
            rms_gate=args.rms_gate,
        )
        summary = extract_movie(
            args.movie,
            args.output,
            options=options,
            tmap=args.tmap,
            tdat=args.tdat,
            overwrite=args.overwrite,
        )
    except ExtractionError as exc:
        print(f"tether extract: {exc}", file=sys.stderr)
        return 1

    print(f"Extracted {summary.n_molecules} molecule(s) -> {summary.output_path}")
    if summary.registration_source == "imported":
        print(f"  registration: imported from {args.tmap}")
    if args.tdat is not None:
        print(f"  detection: mode '{summary.detection_mode}' from {args.tdat}")
    if summary.low_confidence_registration:
        print(
            f"  warning: registration RMS {summary.rms_residual:.3f} px exceeds the "
            f"{args.rms_gate} px gate ({summary.n_control_points} control points); "
            "molecules tagged 'low-confidence-registration'.",
            file=sys.stderr,
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code (``0`` on success).

    ``--version`` is handled by argparse (which exits ``0``). With no subcommand
    this prints help and returns ``0`` — a no-op success, not an error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command is None:
        parser.print_help()
        return 0
    if command == "extract":
        return _run_extract(args)
    parser.error(f"unknown command: {command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
