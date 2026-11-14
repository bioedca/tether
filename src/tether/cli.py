# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``tether`` command-line entry point (PRD §7.11, NFR-REPRO).

A thin headless front door over the service commands of :mod:`tether.services`
(ADR-0100). ``tether --version`` reports the git-derived app version (NFR-REPRO: "the
app version is derived from git"). Each subcommand is the command-line route of one
registered service command: it builds the command's request from its options, runs
the command, and prints the result. ``tether extract`` runs the M1 native extraction
pipeline (movie -> ``.tether``) — see :mod:`tether.services.extract`; ``tether batch``
runs the extract -> correct -> idealize pipeline over many movies — see
:mod:`tether.services.batch`. The subcommands are made from
:data:`tether.services.registry.COMMANDS`, and :func:`unrouted_commands` names any
registered command that has no route here.

The CLI deliberately uses the standard-library :mod:`argparse` rather than a
third-party framework: a base ``conda-lock`` regeneration to add click/typer is
not justified by the current command surface (the pin-and-hold invariant). The
heavy imaging/IO stack is imported only when a service command runs, so that
``tether --version`` stays dependency-light.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable, Sequence

from tether import __version__
from tether.services import (
    COMMANDS,
    BatchRequest,
    BatchResult,
    CommandFailed,
    ExtractRequest,
    ExtractResult,
    Request,
    RequestRefused,
)
from tether.services import run as run_service


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
    for entry in COMMANDS:
        # A registered command with no route fails here, for every CLI call, as well as
        # in the test that names it (unrouted_commands).
        _ROUTES[entry.name].add_parser(subparsers, entry.summary)
    return parser


def _add_extract_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``extract`` subcommand (movie -> ``.tether``)."""
    extract = subparsers.add_parser(
        "extract",
        help=summary,
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


def _extract_request(args: argparse.Namespace) -> ExtractRequest:
    """Build the ``extract`` request from the parsed options."""
    return ExtractRequest(
        movie=args.movie,
        output=args.output,
        overwrite=args.overwrite,
        donor_side=args.donor_side,
        detection_mode=args.detection_mode,
        detection_threshold=args.detection_threshold,
        window=args.window,
        min_separation=args.min_separation,
        detection_block=args.detection_block,
        prealign=args.prealign,
        pair_tol=args.pair_tol,
        coloc_distance=args.coloc_distance,
        rms_gate=args.rms_gate,
        tmap=args.tmap,
        tdat=args.tdat,
    )


def _run_extract(args: argparse.Namespace) -> int:
    """Handle ``tether extract``; a failed extraction is exit code 1."""
    request = _extract_request(args)
    try:
        result: ExtractResult = run_service(request)
    except CommandFailed as exc:
        print(f"tether extract: {exc}", file=sys.stderr)
        return 1

    summary = result.summary
    print(f"Extracted {summary.n_molecules} molecule(s) -> {summary.output_path}")
    if summary.registration_source == "imported":
        print(f"  registration: imported from {request.tmap}")
    if request.tdat is not None:
        print(f"  detection: mode '{summary.detection_mode}' from {request.tdat}")
    if summary.low_confidence_registration:
        print(
            f"  warning: registration RMS {summary.rms_residual:.3f} px exceeds the "
            f"{request.rms_gate} px gate ({summary.n_control_points} control points); "
            "molecules tagged 'low-confidence-registration'.",
            file=sys.stderr,
        )
    # The advisory donor-side check (#646, ADR-0070): a warning on stderr when the halves
    # look swapped, otherwise one informational line. It never changes the exit code.
    check = summary.donor_side_check
    if check is not None:
        note = check.describe(
            donor_side=request.donor_side,
            imported_map=summary.registration_source == "imported",
        )
        if check.swapped:
            print(f"  warning: {note}.", file=sys.stderr)
        else:
            print(f"  donor side: {note}.")
    return 0


def _add_batch_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``batch`` subcommand (many movies -> per-movie ``.tether``)."""
    batch = subparsers.add_parser(
        "batch",
        help=summary,
        description=(
            "Headless batch runner (FR-BATCH): process each movie into its own "
            ".tether under --out-dir, isolating failures (continue-on-error) and "
            "checkpointing per stage so a re-run resumes only the incomplete stages. "
            "Prints an end-of-run summary and writes a JSONL structured log."
        ),
    )
    batch.add_argument("movies", nargs="+", help="one or more dual-channel TIFF movies")
    batch.add_argument(
        "-d",
        "--out-dir",
        required=True,
        help="directory for the per-movie .tether projects (created if absent)",
    )
    batch.add_argument(
        "--tmap",
        default=None,
        metavar="PATH",
        help="a shared imported Deep-LASI .tmap applied to every movie",
    )
    batch.add_argument(
        "--tdat",
        default=None,
        metavar="PATH",
        help="a shared Deep-LASI .tdat detection config applied to every movie",
    )
    batch.add_argument(
        "--policy",
        default="warn",
        choices=("warn", "fail"),
        help=(
            "over-gate registration policy: 'warn' (default) keeps an over-gate "
            "movie with a flag; 'fail' fails it"
        ),
    )
    batch.add_argument(
        "--no-idealize",
        action="store_true",
        help="skip the idealization stage (extract + correct only)",
    )
    batch.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "re-extract an incomplete output or a completed extraction rejected by "
            "the current fail policy; accepted completed checkpoints still skip"
        ),
    )
    batch.add_argument(
        "--log",
        default=None,
        metavar="PATH",
        help="write a JSONL structured log here (default: <out-dir>/batch-log.jsonl)",
    )
    # --- Sidecar supervision (FR-BATCH PR7-B) --------------------------------
    batch.add_argument(
        "--max-restarts",
        type=int,
        default=None,
        metavar="N",
        help=(
            "auto-restart a movie's idealization up to N times on a transient sidecar "
            "failure (crash/timeout); default 3 (§11.2). 0 disables restarts"
        ),
    )
    batch.add_argument(
        "--sidecar-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="per-idealization-call sidecar timeout in seconds (default 1800)",
    )
    batch.add_argument(
        "--sidecar-python",
        default=None,
        metavar="PATH",
        help="sidecar interpreter for idealization (default: $TETHER_SIDECAR_PYTHON)",
    )
    batch.add_argument(
        "--no-defer",
        action="store_true",
        help=(
            "do not defer idealization when the sidecar is unavailable at startup; "
            "let each movie's idealize stage fail in isolation instead"
        ),
    )


def _batch_request(args: argparse.Namespace) -> BatchRequest:
    """Build the ``batch`` request from the parsed options."""
    return BatchRequest(
        movies=tuple(args.movies),
        out_dir=args.out_dir,
        tmap=args.tmap,
        tdat=args.tdat,
        policy=args.policy,
        idealize=not args.no_idealize,
        overwrite=args.overwrite,
        log=args.log,
        max_restarts=args.max_restarts,
        sidecar_timeout=args.sidecar_timeout,
        sidecar_python=args.sidecar_python,
        defer_if_unavailable=not args.no_defer,
    )


def _run_batch(args: argparse.Namespace) -> int:
    """Handle ``tether batch``; exit 2 if it refused to start, 1 if a stage failed."""
    try:
        result: BatchResult = run_service(_batch_request(args))
    except RequestRefused as exc:
        print(f"tether batch: {exc}", file=sys.stderr)
        return 2
    print(result.summary.format_report())
    print(f"  structured log: {result.log_path}", file=sys.stderr)
    return 0 if result.summary.n_failed == 0 else 1


@dataclasses.dataclass(frozen=True)
class _Route:
    """The command-line route of one service command."""

    add_parser: Callable[[argparse._SubParsersAction, str], None]
    to_request: Callable[[argparse.Namespace], Request]
    invoke: Callable[[argparse.Namespace], int]


_ROUTES: dict[str, _Route] = {
    "extract": _Route(_add_extract_parser, _extract_request, _run_extract),
    "batch": _Route(_add_batch_parser, _batch_request, _run_batch),
}


def unrouted_commands() -> list[str]:
    """Name each registered service command that has no command-line route."""
    return [entry.name for entry in COMMANDS if entry.name not in _ROUTES]


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
    route = _ROUTES.get(command)
    if route is None:  # pragma: no cover - argparse accepts only routed commands
        parser.error(f"unknown command: {command}")
    return route.invoke(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
