# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``tether`` command-line entry point (PRD §7.11, NFR-REPRO).

A thin headless front door over the service commands of [`tether.services`][]
(ADR-0100) and over [`tether.project`][]. ``tether --version`` reports the git-derived app
version (NFR-REPRO: "the app version is derived from git"). ``tether extract`` and
``tether batch`` are the command-line routes of two registered service commands: each
builds the command's request from its options, runs the command, and prints the result.
``tether extract`` runs the M1 native extraction pipeline (movie -> ``.tether``) — see
[`tether.services.extract`][]; ``tether batch`` runs the extract -> correct -> idealize
pipeline over many movies — see [`tether.services.batch`][]; ``tether idealize`` fits the
molecules of one project, or refits its stale models — see [`tether.services.idealize`][];
``tether map create`` makes a channel map file from a bead video or image for
``tether extract --map`` — see [`tether.services.maps`][]. Their subcommands are made
from [`tether.services.registry.COMMANDS`][], and [`unrouted_commands`][] names any
registered command that has no route here. ``tether info`` prints a read-only summary of
what a project holds — see [`tether.project.summary`][]. ``tether metadata`` shows and
gives a project's movie metadata with its origin — see
[`tether.project.movie_metadata`][]. ``tether export`` writes the molecule table, the
trace text file or a subset project — see [`tether.cli_export`][]. ``tether status`` prints
the readiness of each stage of a project and its next step — see
[`tether.project.readiness`][].

The CLI deliberately uses the standard-library `argparse` rather than a
third-party framework: a base ``conda-lock`` regeneration to add click/typer is
not justified by the current command surface (the pin-and-hold invariant). The
heavy imaging/IO stack is imported only when a service command runs, so that
``tether --version`` stays dependency-light.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from tether import __version__
from tether.cli_export import add_export_parser, run_export
from tether.extract_choices import (
    COLOCALIZATION_POLICIES,
    DEFAULT_COLOCALIZATION_POLICY,
    DETECTION_MODES,
    DONOR_SIDES,
    PREALIGN_MODES,
)
from tether.idealize_choices import IDEALIZE_METHODS
from tether.services import (
    COMMANDS,
    ApproveRequest,
    ApproveResult,
    BatchRequest,
    BatchResult,
    CommandFailed,
    ExtractRequest,
    ExtractResult,
    IdealizeRequest,
    IdealizeResult,
    MapCreateRequest,
    MapCreateResult,
    Request,
    RequestError,
    RequestRefused,
    SidecarUnavailable,
)
from tether.services import run as run_service
from tether.services.profile import read_profile

if TYPE_CHECKING:
    # Annotation-only: the summary module pulls in h5py, which ``--version`` must not.
    from tether.project.batch import BatchSummary
    from tether.project.readiness import ProjectReadiness
    from tether.project.summary import CurationCounts, ProjectSummary


#: The attribute of the parsed namespace that names the extraction flags given on the
#: command line (``_ExplicitStore``), so ``--profile`` can refuse them (#349).
_EXPLICIT = "explicit_extraction_flags"

#: What ``--profile`` and ``--tdat`` say about each other, in the help of both commands.
_TDAT_RULE = (
    "a .tdat sets detection_mode and detection_threshold, and no other option; it is "
    "refused with --detection-mode/--detection-threshold or with a --profile that sets "
    "either, and every other option keeps its value"
)


class _ExplicitStore(argparse.Action):
    """Store an option's value and remember that the option was given (#349).

    argparse calls an action only for an option on the command line, so this tells a flag
    given at its default from a flag left out, which a default cannot.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        given = getattr(namespace, _EXPLICIT, frozenset())
        setattr(namespace, _EXPLICIT, given | {option_string or self.dest})


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
    _add_run_parser(subparsers)
    _add_info_parser(subparsers)
    _add_metadata_parser(subparsers)
    add_export_parser(subparsers)
    _add_status_parser(subparsers)
    return parser


def _warn_conflict_copies(project_path: str | os.PathLike[str]) -> None:
    """Print one stderr warning when ``project_path`` has sync-conflict siblings (§5.4).

    The CLI's half of detect-and-surface: the same text the GUI's open-project banner
    shows ([`tether.services.notices.conflict_copy_warning`][]). Advisory only — it never
    changes the exit code and never touches the conflict copies.
    """
    from tether.services.notices import conflict_copy_warning

    warning = conflict_copy_warning(project_path)
    if warning is not None:
        print(f"  warning: {warning}.", file=sys.stderr)


def _add_extract_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``extract`` subcommand (movie -> ``.tether``)."""
    extract = subparsers.add_parser(
        "extract",
        help=summary,
        description=(
            "Run the native extraction pipeline (split -> detect -> register -> "
            "colocalize -> integrate) on a dual-channel movie (TIFF, ND2 or cellSens .vsi) and write a "
            "new .tether project."
        ),
    )
    extract.add_argument(
        "movie", help="path to the dual-channel movie (TIFF, ND2 or cellSens .vsi)"
    )
    extract.add_argument(
        "-o", "--output", required=True, help="path to the .tether project to create"
    )
    extract.add_argument(
        "--overwrite", action="store_true", help="overwrite an existing output project"
    )
    extract.add_argument(
        "--donor-side",
        action=_ExplicitStore,
        default="left",
        choices=DONOR_SIDES,
        help="which horizontal half is the donor channel (default: left)",
    )
    extract.add_argument(
        "--detection-mode",
        action=_ExplicitStore,
        # None, not "wavelet": an explicit choice must stay distinguishable from no
        # flag, for the --tdat conflict check in _run_extract.
        default=None,
        choices=DETECTION_MODES,
        help=(
            "particle-detection method (Deep-LASI findPart mode; default: wavelet). "
            "'intensity'/'bandpass'/'zmw' also honor --detection-threshold. "
            "Mutually exclusive with --tdat, which supplies the mode"
        ),
    )
    extract.add_argument(
        "--detection-threshold",
        action=_ExplicitStore,
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "detection threshold as a fraction of the detection-image max, in [0, 1) "
            "(intensity/bandpass/zmw modes only; default: each mode's own — intensity 0.5, "
            "bandpass 0.98, zmw 0.5; ignored by wavelet). Mutually exclusive with --tdat"
        ),
    )
    extract.add_argument(
        "--window",
        action=_ExplicitStore,
        type=int,
        default=21,
        help="aperture / crop-box side length in px, odd (default: 21)",
    )
    extract.add_argument(
        "--min-separation",
        action=_ExplicitStore,
        type=float,
        default=None,
        help=(
            "minimum spot separation in px; unset uses each detection mode's faithful "
            "default (wavelet 8, intensity/bandpass/zmw 3)"
        ),
    )
    extract.add_argument(
        "--detection-block",
        action=_ExplicitStore,
        type=int,
        default=50,
        help="moving-average block size for the detection image (default: 50)",
    )
    extract.add_argument(
        "--prealign",
        action=_ExplicitStore,
        default="translation",
        choices=PREALIGN_MODES,
        help="registration prealign degrees of freedom (default: translation)",
    )
    extract.add_argument(
        "--pair-tol",
        action=_ExplicitStore,
        type=float,
        default=2.0,
        help="control-point pairing tolerance in px (default: 2)",
    )
    extract.add_argument(
        "--coloc-distance",
        action=_ExplicitStore,
        type=float,
        default=3.0,
        help="acceptor colocalization distance in px (default: 3)",
    )
    extract.add_argument(
        "--colocalization",
        action=_ExplicitStore,
        default=DEFAULT_COLOCALIZATION_POLICY,
        choices=COLOCALIZATION_POLICIES,
        help=(
            "which molecules to keep: 'partner-in-both' keeps a donor spot only when an "
            "acceptor spot was detected at its mapped position (Deep-LASI's rule); "
            "'donor-anchored' keeps every donor spot, for weak-acceptor and low-FRET "
            "samples (default: partner-in-both)"
        ),
    )
    extract.add_argument(
        "--count-only",
        action="store_true",
        help=(
            "run detection, registration and colocalization, print the molecule count "
            "of each colocalization rule, and write nothing"
        ),
    )
    extract.add_argument(
        "--rms-gate",
        action=_ExplicitStore,
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
            "the .tmap's own channel geometry (--donor-side is then ignored); "
            "cannot be combined with --map"
        ),
    )
    extract.add_argument(
        "--map",
        dest="map_path",
        default=None,
        metavar="PATH",
        help=(
            "apply a saved Tether .npz map file (from 'tether map create') instead "
            "of a native fit; splits at the map's own channel geometry (--donor-side is "
            "then ignored). Precedence: the native fit runs only when neither --tmap nor "
            "--map is given, and the two cannot be combined; --tdat sets detection only "
            "and composes with any of them"
        ),
    )
    extract.add_argument(
        "--tdat",
        default=None,
        metavar="PATH",
        help=(
            "auto-apply the particle-detection mode decoded from a Deep-LASI .tdat "
            "(temp/ParticleDetectionMode), so extraction matches the method the movie "
            f"was detected with; {_TDAT_RULE}"
        ),
    )
    extract.add_argument(
        "--profile",
        default=None,
        metavar="PATH",
        help=(
            "read every extraction option from an extraction profile (a JSON "
            "file; see docs/cli.md); an option the file leaves out keeps its default. "
            "It cannot be combined with an option flag (--donor-side ... --rms-gate)"
        ),
    )


class _ProfileRefused(Exception):
    """The ``--profile`` of a command line cannot be used; the message says why."""


def _profile_values(args: argparse.Namespace) -> dict[str, object] | None:
    """Read ``--profile`` into the extraction fields, or ``None`` without one.

    Raises [`_ProfileRefused`][] when the file is not a valid profile, when an extraction
    flag is given beside it, or when it sets a detection field beside ``--tdat``.
    """
    if args.profile is None:
        return None
    given = sorted(getattr(args, _EXPLICIT, frozenset()))
    if given:
        raise _ProfileRefused(
            f"--profile cannot be combined with {', '.join(given)}: the profile sets every "
            "extraction option; change the value in the profile instead"
        )
    try:
        profile = read_profile(args.profile)
    except RequestError as exc:
        raise _ProfileRefused(str(exc)) from exc
    if args.tdat is not None and profile.sets_detection():
        raise _ProfileRefused(
            f"extraction profile {args.profile} sets detection_mode/detection_threshold, "
            "which cannot be combined with --tdat (the .tdat supplies them); set them to "
            "null in the profile or drop --tdat"
        )
    return profile.extraction_values()


def _extract_request(args: argparse.Namespace) -> ExtractRequest:
    """Build the ``extract`` request from the parsed options or the ``--profile``.

    Raises [`_ProfileRefused`][] for a ``--profile`` that cannot be used.
    """
    values = _profile_values(args)
    if values is None:
        # The seven fields with no flag keep their request defaults.
        values = {
            "donor_side": args.donor_side,
            "detection_mode": args.detection_mode,
            "detection_threshold": args.detection_threshold,
            "window": args.window,
            "min_separation": args.min_separation,
            "detection_block": args.detection_block,
            "prealign": args.prealign,
            "pair_tol": args.pair_tol,
            "coloc_distance": args.coloc_distance,
            "colocalization": args.colocalization,
            "rms_gate": args.rms_gate,
        }
    return ExtractRequest(
        movie=args.movie,
        output=args.output,
        overwrite=args.overwrite,
        tmap=args.tmap,
        tdat=args.tdat,
        map_path=args.map_path,
        count_only=args.count_only,
        **values,  # type: ignore[arg-type]
    )


def _describe_rule_counts(chosen: str, counts: Mapping[str, int]) -> str:
    """One line naming the rule that ran and what the other rule would have kept."""
    others = ", ".join(f"{rule} would keep {n}" for rule, n in counts.items() if rule != chosen)
    return f"{chosen} kept {counts.get(chosen, 0)}" + (f" ({others})" if others else "")


def _run_extract(args: argparse.Namespace) -> int:
    """Handle ``tether extract``; a failed extraction or an unusable profile is exit code 1."""
    try:
        request = _extract_request(args)
    except _ProfileRefused as exc:
        print(f"tether extract: {exc}", file=sys.stderr)
        return 1
    try:
        result: ExtractResult = run_service(request)
    except CommandFailed as exc:
        print(f"tether extract: {exc}", file=sys.stderr)
        return 1

    summary = result.summary
    if summary is None:  # --count-only: the counts before a run, nothing written
        print(f"Molecules in {request.movie} under each colocalization rule (nothing written):")
        for rule, count in result.rule_counts.items():
            chosen = " (selected)" if rule == request.colocalization else ""
            print(f"  {rule}: {count}{chosen}")
        return 0
    print(f"Extracted {summary.n_molecules} molecule(s) -> {summary.output_path}")
    print(f"  colocalization: {_describe_rule_counts(summary.colocalization, result.rule_counts)}")
    if summary.registration_source == "imported":
        print(f"  registration: imported from {request.tmap}")
    elif summary.registration_source == "map-file":
        print(f"  registration: map file {request.map_path}")
    if request.tdat is not None:
        print(f"  detection: mode '{summary.detection_mode}' from {request.tdat}")
    if summary.low_confidence_registration:
        # A map file is judged against its own gate, not --rms-gate (#353).
        gate = request.rms_gate if summary.rms_gate is None else summary.rms_gate
        print(
            f"  warning: registration RMS {summary.rms_residual:.3f} px exceeds the "
            f"{gate:g} px gate ({summary.n_control_points} control points); "
            "molecules tagged 'low-confidence-registration'.",
            file=sys.stderr,
        )
    # The advisory donor-side check (#646, ADR-0070): a warning on stderr when the halves
    # look swapped, otherwise one informational line. It never changes the exit code.
    check = summary.donor_side_check
    if check is not None:
        note = check.describe(
            donor_side=request.donor_side,
            # A .tmap or a map file sets the channel split, not --donor-side.
            imported_map=summary.registration_source != "native",
            # A --map run names its map file, never the .tmap it did not use.
            map_file=None if request.map_path is None else os.path.basename(request.map_path),
            channel_layout=summary.channel_layout,
        )
        if check.swapped:
            print(f"  warning: {note}.", file=sys.stderr)
        else:
            print(f"  donor side: {note}.")
    _warn_conflict_copies(summary.output_path)
    return 0


#: ``tether batch`` exit codes (#647). ``1`` takes precedence over ``3``: a run with a
#: failed movie exits ``1`` whether or not a stage was also deferred.
BATCH_EXIT_OK = 0
BATCH_EXIT_FAILED = 1
BATCH_EXIT_USAGE = 2
BATCH_EXIT_DEFERRED = 3

BATCH_EXIT_MEANINGS: dict[int, str] = {
    BATCH_EXIT_OK: "every requested stage ran for every movie",
    BATCH_EXIT_FAILED: "at least one movie had a failed stage, or the output could not be written",
    BATCH_EXIT_USAGE: "a usage error: bad arguments, or the run refused to start",
    BATCH_EXIT_DEFERRED: (
        "no movie failed, but a requested stage was deferred for at least one movie"
    ),
}


def _batch_exit_code_epilog() -> str:
    """The ``exit codes:`` block of ``tether batch --help``, one code per line."""
    lines = ["exit codes:"]
    for code, meaning in sorted(BATCH_EXIT_MEANINGS.items()):
        lines.append(f"  {code}  {meaning}")
    lines.append(f"  {BATCH_EXIT_FAILED} takes precedence over {BATCH_EXIT_DEFERRED}.")
    return "\n".join(lines)


def batch_exit_code(summary: BatchSummary) -> int:
    """The exit code for a finished batch run: ``1``, then ``3``, then ``0``.

    A stage the user did not ask for is recorded
    ``not-requested``, never ``deferred``, so ``--no-idealize`` alone never yields ``3``.
    """
    if summary.n_failed > 0:
        return BATCH_EXIT_FAILED
    if summary.n_deferred > 0:
        return BATCH_EXIT_DEFERRED
    return BATCH_EXIT_OK


def _add_batch_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``batch`` subcommand (many movies -> per-movie ``.tether``)."""
    batch = subparsers.add_parser(
        "batch",
        help=summary,
        # Raw, so that the exit-code table in the epilog keeps one code per line; the
        # description is wrapped by hand for the same reason.
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Headless batch runner (FR-BATCH): process each movie into its own\n"
            ".tether under --out-dir, or every movie into one project with --into,\n"
            "isolating failures (continue-on-error) and checkpointing per stage so a\n"
            "re-run resumes only the incomplete stages. Prints an end-of-run summary\n"
            "and writes a JSONL structured log."
        ),
        epilog=_batch_exit_code_epilog(),
    )
    batch.add_argument(
        "movies",
        nargs="+",
        help=(
            "dual-channel movies (TIFF, ND2 or cellSens .vsi): files, folders (the movies directly inside) or "
            "quoted patterns such as 'data/*.tif', which Tether expands in every shell"
        ),
    )
    batch.add_argument(
        "--recursive",
        action="store_true",
        help="a folder argument also includes the movies in its sub-folders",
    )
    batch.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "print each movie, its output and what the run would do, then exit 0; "
            "nothing is written, and no movie is read except to hash it under --into"
        ),
    )
    destination = batch.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "-d",
        "--out-dir",
        default=None,
        help="directory for the per-movie .tether projects (created if absent)",
    )
    destination.add_argument(
        "--into",
        default=None,
        metavar="PROJECT.tether",
        help=(
            "append every movie to this one project (created by the first movie if "
            "absent); a movie already in it is skipped, and correct and idealize run "
            "once over the whole project"
        ),
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
        help=f"a shared Deep-LASI .tdat detection config applied to every movie; {_TDAT_RULE}",
    )
    batch.add_argument(
        "--profile",
        default=None,
        metavar="PATH",
        help=(
            "extract every movie with the extraction options of an extraction "
            "profile (a JSON file; see docs/cli.md); an option the file leaves out keeps "
            "its default, and without a profile every option but --colocalization is at "
            "its default. It cannot be combined with --colocalization"
        ),
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
        "--colocalization",
        action=_ExplicitStore,
        default=DEFAULT_COLOCALIZATION_POLICY,
        choices=COLOCALIZATION_POLICIES,
        help=(
            "which molecules each extraction keeps: 'partner-in-both' (default) or "
            "'donor-anchored'; see tether extract --colocalization"
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
        help=(
            "write a JSONL structured log here (default: <out-dir>/batch-log.jsonl, or "
            "<project>-batch-log.jsonl beside the --into project)"
        ),
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
    # --- Progress on stderr (#648) -------------------------------------------
    chatter = batch.add_mutually_exclusive_group()
    chatter.add_argument(
        "--quiet",
        action="store_true",
        help="print no progress lines on stderr; the summary on stdout is unchanged",
    )
    chatter.add_argument(
        "--verbose",
        action="store_true",
        help="also print every stage event on stderr, as the structured log records it",
    )


def _batch_request(args: argparse.Namespace) -> BatchRequest:
    """Build the ``batch`` request from the parsed options and the ``--profile``.

    Raises [`_ProfileRefused`][] for a ``--profile`` that cannot be used.
    """
    values = _profile_values(args)
    if values is None:
        # Without a profile, --colocalization is the one extraction field with a flag.
        values = {"colocalization": args.colocalization}
    return BatchRequest(
        movies=tuple(args.movies),
        out_dir=args.out_dir,
        into=args.into,
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
        recursive=args.recursive,
        **values,  # type: ignore[arg-type]
    )


def _run_batch(args: argparse.Namespace) -> int:
    """Handle ``tether batch``; exit 2 if it refused to start, 1 if it or a stage failed.

    A finished run's code is [`batch_exit_code`][]'s, so it is ``3`` when no movie failed and
    a requested stage was deferred (#647).
    """
    if args.dry_run:
        from tether.services.batch import plan  # noqa: PLC0415

        try:
            print(plan(_batch_request(args)).format_plan())
        except (RequestRefused, _ProfileRefused) as exc:
            print(f"tether batch: {exc}", file=sys.stderr)
            return 2
        return 0
    from tether.project.batch import TextProgress

    # Progress goes to stderr so that stdout keeps only the summary (#648).
    progress = None if args.quiet else TextProgress(sys.stderr, verbose=args.verbose)
    try:
        result: BatchResult = run_service(_batch_request(args), progress=progress)
    except (RequestRefused, _ProfileRefused) as exc:
        print(f"tether batch: {exc}", file=sys.stderr)
        return 2
    except CommandFailed as exc:
        print(f"tether batch: {exc}", file=sys.stderr)
        return 1
    code = batch_exit_code(result.summary)
    print(result.summary.format_report())
    print(f"Exit code {code}: {BATCH_EXIT_MEANINGS[code]}.")
    # Check the projects the run actually used: folders and patterns are expanded, and
    # colliding movie names get longer output names (output_stems), so the raw arguments
    # do not name them.
    for movie_result in result.summary.results:
        _warn_conflict_copies(movie_result.job.output_path)
    print(f"  structured log: {result.log_path}", file=sys.stderr)
    return code


#: ``tether idealize`` exit code when the sidecar cannot start (#657); 1 and 2 are the
#: shared "failed" and "usage" codes.
EXIT_SIDECAR_UNAVAILABLE = 3


def _add_idealize_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``idealize`` subcommand (fit a project, or refit its stale models)."""
    idealize = subparsers.add_parser(
        "idealize",
        help=summary,
        description=(
            "Fit a hidden Markov model to the molecules of a .tether project (FR-IDEALIZE) "
            "and store it under /idealization/<model>. Rejected molecules are left out. "
            "With --stale-only, refit each stored model that has a molecule whose inputs "
            "changed since its fit. The fit runs in the tMAVEN sidecar; the command holds "
            "the project's lock while it runs. Exit codes: 0 fitted (or nothing stale), "
            "1 failed, 2 usage, 3 the sidecar was not available (nothing written)."
        ),
    )
    idealize.add_argument("project", help="the .tether project to fit")
    idealize.add_argument(
        "--method",
        default=None,
        choices=IDEALIZE_METHODS,
        help=f"the fit: consensus VB-HMM or ebFRET (default {IDEALIZE_METHODS[0]})",
    )
    states = idealize.add_mutually_exclusive_group()
    states.add_argument(
        "--nstates",
        type=int,
        default=None,
        metavar="N",
        help="fit exactly N states (default: choose the state count by the largest ELBO)",
    )
    states.add_argument(
        "--nstates-grid",
        default=None,
        metavar="LIST",
        help="the state counts the ELBO choice tries, comma-separated (default 1,2,3,4)",
    )
    idealize.add_argument(
        "--model-name",
        default=None,
        metavar="NAME",
        help=(
            "store the fit as /idealization/NAME (default: the method); with --stale-only, "
            "refit only this model"
        ),
    )
    idealize.add_argument(
        "--condition",
        action="append",
        default=None,
        dest="conditions",
        metavar="ID",
        help="fit only the molecules of this condition ID (repeat for more; default: all)",
    )
    idealize.add_argument(
        "--include-rejected",
        action="store_true",
        help="also fit the molecules marked rejected",
    )
    idealize.add_argument(
        "--overwrite",
        action="store_true",
        help="replace a model of the same name (default: refuse)",
    )
    idealize.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="seed every fit, so the same inputs and seed give the same model (default: unseeded)",
    )
    idealize.add_argument(
        "--stale-only",
        action="store_true",
        help=(
            "refit, with its stored settings, each model that has a stale molecule, and "
            "report how many molecules were stale and how many current"
        ),
    )
    # --- Sidecar supervision: the options tether batch has -------------------
    idealize.add_argument(
        "--max-restarts",
        type=int,
        default=None,
        metavar="N",
        help=(
            "restart a fit up to N times on a sidecar crash or timeout; default 3 (§11.2). "
            "0 disables restarts"
        ),
    )
    idealize.add_argument(
        "--sidecar-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="per-fit sidecar timeout in seconds (default 1800)",
    )
    idealize.add_argument(
        "--sidecar-python",
        default=None,
        metavar="PATH",
        help="sidecar interpreter (default: $TETHER_SIDECAR_PYTHON)",
    )


def _idealize_request(args: argparse.Namespace) -> IdealizeRequest:
    """Build the ``idealize`` request from the parsed options."""
    return IdealizeRequest(
        project=args.project,
        method=args.method,
        nstates=args.nstates,
        nstates_grid=args.nstates_grid,
        model_name=args.model_name,
        conditions=tuple(args.conditions or ()),
        include_rejected=args.include_rejected,
        overwrite=args.overwrite,
        seed=args.seed,
        stale_only=args.stale_only,
        max_restarts=args.max_restarts,
        sidecar_timeout=args.sidecar_timeout,
        sidecar_python=args.sidecar_python,
    )


def _format_elbo(value: float | None) -> str:
    return "not recorded" if value is None else f"{value:.6g}"


def _format_idealize(result: IdealizeResult) -> str:
    """The text report of an ``idealize`` result."""
    lines = [str(result.project)]
    fitted = result.fitted
    if fitted is not None:
        lines.append(f"  model: {fitted.model_name}")
        lines.append(f"  method: {fitted.model_type}")
        how = "largest ELBO" if fitted.nstates_selected_by == "max-elbo" else "fixed"
        lines.append(f"  states: {fitted.nstates} ({how})")
        if fitted.elbo_by_nstates:
            lines.append("  ELBO by state count:")
            for k in sorted(fitted.elbo_by_nstates):
                lines.append(f"    {k}: {_format_elbo(fitted.elbo_by_nstates[k])}")
        else:
            lines.append(f"  ELBO: {_format_elbo(fitted.elbo)}")
        lines.append(f"  molecules fitted: {fitted.n_molecules}")
        return "\n".join(lines)
    if not result.refits:
        lines.append("  no idealization model to check")
    for refit in result.refits:
        if refit.imported:
            verdict = "imported from tMAVEN, not refitted"
        elif refit.refit is not None:
            verdict = "refitted"
        else:
            verdict = "current, not refitted"
        lines.append(
            f"  {refit.model_name}: {refit.n_stale} stale, {refit.n_current} current; {verdict}"
        )
        if refit.moved_keys:
            lines.append(
                f"    {len(refit.moved_keys)} moved in a new trace generation, not refitted: "
                + ", ".join(refit.moved_keys)
            )
    return "\n".join(lines)


def _run_idealize(args: argparse.Namespace) -> int:
    """Handle ``tether idealize``; exit 2 on a refused request, 3 without a sidecar."""
    try:
        result: IdealizeResult = run_service(_idealize_request(args))
    except RequestRefused as exc:
        print(f"tether idealize: {exc}", file=sys.stderr)
        return 2
    except SidecarUnavailable as exc:
        print(f"tether idealize: {exc}", file=sys.stderr)
        return EXIT_SIDECAR_UNAVAILABLE
    except CommandFailed as exc:
        print(f"tether idealize: {exc}", file=sys.stderr)
        return 1
    print(_format_idealize(result))
    return 0


def _add_map_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``map`` subcommand and its one action, ``map create`` (#353)."""
    map_parser = subparsers.add_parser(
        "map",
        help=summary,
        description=(
            "Channel map files: the saved donor<->acceptor registration that "
            "'tether extract --map' applies to a later movie."
        ),
    )
    actions = map_parser.add_subparsers(dest="map_action", metavar="<action>", required=True)
    create = actions.add_parser(
        "create",
        help="make a map file from a bead video or one bead image",
        description=(
            "Detect the beads of both halves, pair them and fit the degree-2 map "
            "(Stages 6-9), then save it as a .npz map file with its source id, its "
            "projection settings and its quality record. A map over --rms-gate is still "
            "saved, flagged low-confidence; the command then exits 0 and warns."
        ),
    )
    create.add_argument("source", help="a bead TIFF: one image, or a video of frames")
    create.add_argument(
        "-o", "--output", required=True, help="the .npz map file to write (must end in .npz)"
    )
    create.add_argument("--overwrite", action="store_true", help="replace an existing map file")
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


def _map_request(args: argparse.Namespace) -> MapCreateRequest:
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
    )


def _run_map(args: argparse.Namespace) -> int:
    """Handle ``tether map create``; a failed map is exit code 1."""
    request = _map_request(args)
    try:
        result: MapCreateResult = run_service(request)
    except CommandFailed as exc:
        print(f"tether map create: {exc}", file=sys.stderr)
        return 1
    print(
        f"Map -> {result.path}: RMS {result.rms_residual:.3f} px over {result.n_pairs} "
        f"bead pair(s), {result.coverage:.0%} of the field covered"
    )
    if result.low_confidence:
        print(
            f"  warning: the RMS residual exceeds the {request.rms_gate} px gate; the map is "
            "flagged low-confidence and its molecules will be tagged "
            "'low-confidence-registration'.",
            file=sys.stderr,
        )
    return 0


def _add_approve_parser(subparsers: argparse._SubParsersAction, summary: str) -> None:
    """Register the ``approve`` subcommand (a person approves an approval step, ADR-0102)."""
    from tether.services.recipe import APPROVAL_STEPS  # noqa: PLC0415

    approve = subparsers.add_parser(
        "approve",
        help=summary,
        description=(
            "Record that a person approved a step that needs a person, on the evidence of "
            "its report (ADR-0102). When 'tether run' stopped at the step (exit code 4), "
            "the record names that recipe, and 'tether run RECIPE --resume' goes on past "
            "the step. Exit codes: 0 recorded, 1 failed, 2 refused (nothing written)."
        ),
    )
    approve.add_argument("project", help="the .tether project to record the approval in")
    approve.add_argument("step", choices=APPROVAL_STEPS, help="the approval step")
    approve.add_argument(
        "--subject", required=True, help="what is approved: the map, the condition or the set"
    )
    approve.add_argument(
        "--report",
        required=True,
        help="the report that is the evidence (.json; its PNG images beside it are recorded too)",
    )
    approve.add_argument(
        "--by",
        default=None,
        metavar="NAME",
        help="the name of the person (default: the curator name of new labels)",
    )


def _approve_request(args: argparse.Namespace) -> ApproveRequest:
    """Build the ``approve`` request from the parsed options."""
    return ApproveRequest(
        project=args.project,
        step=args.step,
        subject=args.subject,
        report=args.report,
        by=args.by,
    )


def _run_approve(args: argparse.Namespace) -> int:
    """Handle ``tether approve``; exit 2 on a refused request, 1 on a failed write."""
    try:
        result: ApproveResult = run_service(_approve_request(args))
    except RequestRefused as exc:
        print(f"tether approve: {exc}", file=sys.stderr)
        return 2
    except CommandFailed as exc:
        print(f"tether approve: {exc}", file=sys.stderr)
        return 1
    approval = result.approval
    print(
        f"{result.project}: {approval.step} for {approval.subject!r} approved by {approval.actor}"
    )
    if approval.recipe_sha256:
        print(f"  for the recipe {approval.recipe_sha256[:12]}; continue it with --resume")
    return 0


#: Exit code of every ``tether`` command that stopped because a person must act
#: (ADR-0102 §2); no command uses ``4`` for another meaning.
EXIT_APPROVAL_NEEDED = 4


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``run`` subcommand (run a recipe file, #899)."""
    run = subparsers.add_parser(
        "run",
        help="run the steps of a recipe file, each as a stored job",
        description=(
            "Run a recipe: a JSON file that lists service requests "
            "(ADR-0100). Each step runs through its service command as a job stored in the "
            "project it writes. At a step that needs a person, a policy that the recipe "
            "names decides; with no policy the run stops (exit code 4) and names the "
            "command that approves and the one that continues. Exit codes: 0 every step "
            "ran, 1 a step failed, 2 the recipe was refused (nothing ran), 3 no step failed "
            "but a stage was deferred, 4 stopped at a step that needs a person."
        ),
    )
    run.add_argument("recipe", help="the recipe file (.json)")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the steps, the inputs of each and each approval step; change nothing",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="skip the steps that a run of this recipe completed and go on from there",
    )


#: A word that the shell of the platform reads as itself, so it is printed with no quotes.
_PLAIN_WINDOWS_WORD = re.compile(r"[\w+=:,./\\-]+")


def _windows_word(word: str) -> str:
    """Quote ``word`` for the Tether Command Prompt (``cmd.exe``) of Windows.

    A word with a space or a sign that the console reads (``&``, ``|``, ``<``, ``>``, ``^``)
    goes in double quotes. Inside them a ``"`` gets a backslash, and so does each backslash
    before a ``"``, which is how a Windows program splits its command line into words.
    Quotes do not stop ``cmd.exe`` from expanding a ``%NAME%`` of the environment, so a
    word that holds one can change when it is pasted; a Windows path seldom holds one.
    """
    if _PLAIN_WINDOWS_WORD.fullmatch(word):
        return word
    quoted, backslashes = ['"'], 0
    for char in word:
        if char == "\\":
            backslashes += 1
            continue
        escape = "\\" * (2 * backslashes + 1) if char == '"' else "\\" * backslashes
        quoted.append(escape + char)
        backslashes = 0
    quoted.append("\\" * (2 * backslashes) + '"')
    return "".join(quoted)


def _command_line(*words: str, windows: bool | None = None) -> str:
    """Write ``words`` as one command that a person can copy and run as printed (#1310).

    A path or a subject with a space stays one word: in quotes for ``cmd.exe`` on Windows,
    the shell the install guide gives there, and in POSIX shell quotes on macOS and Linux.
    A plain word is printed as it is. ``windows`` is the platform, ``None`` for this one.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return " ".join(_windows_word(word) for word in words)
    return shlex.join(words)


def _print_stop(args: argparse.Namespace, stop: object) -> None:
    from tether.services.recipe import ApprovalStop  # noqa: PLC0415

    assert isinstance(stop, ApprovalStop)
    print(f"tether run: {stop}.", file=sys.stderr)
    if stop.report:
        print(f"  evidence: {stop.report}", file=sys.stderr)
    report = stop.report or "REPORT.json"
    approve = ("tether", "approve", stop.project, stop.step, "--subject", stop.subject)
    print(f"  approve:  {_command_line(*approve, '--report', report)}", file=sys.stderr)
    print(f"  continue: {_command_line('tether', 'run', args.recipe, '--resume')}", file=sys.stderr)


def _run_recipe(args: argparse.Namespace) -> int:
    """Handle ``tether run``: ``0``, ``1`` failed, ``2`` refused, ``3`` deferred, ``4`` stop."""
    from tether.services.recipe import (  # noqa: PLC0415
        RecipeError,
        plan_recipe,
        read_recipe,
        run_recipe,
    )

    try:
        recipe = read_recipe(args.recipe)
        if args.dry_run:
            for line in plan_recipe(recipe, resume=args.resume):
                print(line)
            return 0
        total = len(recipe.steps)

        def report(outcome: object) -> None:
            text = f"step {outcome.number}/{total} {outcome.command}: {outcome.state}"
            print(text + (f": {outcome.message}" if outcome.message else ""))

        result = run_recipe(recipe, resume=args.resume, progress=report)
    except RecipeError as exc:
        print(f"tether run: {exc}", file=sys.stderr)
        return 2
    if result.failed:
        resume = _command_line("tether", "run", args.recipe, "--resume")
        print(f"tether run: a step failed; fix it and continue with {resume}", file=sys.stderr)
        return 1
    if result.stop is not None:
        _print_stop(args, result.stop)
        return EXIT_APPROVAL_NEEDED
    return BATCH_EXIT_DEFERRED if result.deferred else 0


@dataclasses.dataclass(frozen=True)
class _Route:
    """The command-line route of one service command."""

    add_parser: Callable[[argparse._SubParsersAction, str], None]
    to_request: Callable[[argparse.Namespace], Request]
    invoke: Callable[[argparse.Namespace], int]


_ROUTES: dict[str, _Route] = {
    "extract": _Route(_add_extract_parser, _extract_request, _run_extract),
    "batch": _Route(_add_batch_parser, _batch_request, _run_batch),
    "idealize": _Route(_add_idealize_parser, _idealize_request, _run_idealize),
    "map": _Route(_add_map_parser, _map_request, _run_map),
    "approve": _Route(_add_approve_parser, _approve_request, _run_approve),
}


def unrouted_commands() -> list[str]:
    """Name each registered service command that has no command-line route."""
    return [entry.name for entry in COMMANDS if entry.name not in _ROUTES]


def _add_info_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``info`` subcommand (a read-only project summary, #655)."""
    info = subparsers.add_parser(
        "info",
        help="summarize what a .tether project holds, without opening it for writing",
        description=(
            "Print, for each project: the schema version, the movies, the conditions, the "
            "molecule, curation and category counts, the correction state, the "
            "idealization models, the ranker model reference and the lock holder. The "
            "project is opened read-only and no lock is taken, so this works on a project "
            "another user holds. Movie files are never read, and trace arrays are read "
            "only for --stale."
        ),
    )
    info.add_argument("projects", nargs="+", help="one or more .tether projects")
    info.add_argument(
        "--json",
        action="store_true",
        help="print one JSON document (a documented, versioned layout) instead of text",
    )
    info.add_argument(
        "--stale",
        action="store_true",
        help=(
            "also count each idealization's stale molecules; this reads the trace arrays "
            "the models were fit on, so it is slower on a large project"
        ),
    )


def _format_curation(counts: CurationCounts) -> str:
    """``3 accepted, 1 rejected, 1 undecided`` for a [`CurationCounts`][]."""
    return f"{counts.accepted} accepted, {counts.rejected} rejected, {counts.undecided} undecided"


def _format_summary(s: ProjectSummary) -> str:
    """The text form of one [`ProjectSummary`][tether.project.summary.ProjectSummary]."""
    lines = [s.path]
    written = f", written by Tether {s.written_by}" if s.written_by else ""
    lines.append(f"  schema version {s.schema_version}{written}")
    lines.append(f"  movies: {len(s.movies)}")
    for m in s.movies:
        frame_time = f"{m.frame_time_s:g} s/frame" if m.frame_time_s else "frame time not recorded"
        lines.append(f"    {m.name}: {m.n_frames} frames, {frame_time}, {m.n_molecules} molecules")
        lines.append(f"      curation: {_format_curation(m.curation)}")
    lines.append(f"  conditions: {len(s.conditions)}")
    for c in s.conditions:
        note = "" if c.materialized else " (not yet in /conditions)"
        lines.append(f"    {c.condition_id}: {c.n_molecules} molecules{note}")
    lines.append(f"  molecules: {s.n_molecules}")
    lines.append(f"  curation: {_format_curation(s.curation)}")
    if s.categories:
        parts = [f"{name} {n}" for name, n in s.categories.items()]
        lines.append(f"  categories: {', '.join(parts)}; {s.uncategorized} uncategorized")
    else:
        lines.append(f"  categories: none set ({s.uncategorized} uncategorized)")
    corrections = s.corrections
    if corrections.ran:
        parts = [f"{method or 'none'} {n}" for method, n in corrections.methods.items()]
        lines.append(f"  corrections: ran ({', '.join(parts)})")
    else:
        lines.append("  corrections: not run")
    lines.append(f"  idealizations: {len(s.idealizations)}")
    for i in s.idealizations:
        states = "? states" if i.n_states is None else f"{i.n_states} states"
        stale = "stale not checked (use --stale)" if i.stale is None else f"{i.stale} stale"
        lines.append(
            f"    {i.name}: method {i.method or 'unknown'}, {states}, "
            f"{i.n_molecules} molecules, {stale}"
        )
    models = ", ".join(s.models) if s.models else "none"
    lines.append(f"  ranker model: {models}")
    lock = s.lock
    if not lock.held:
        lines.append("  lock: not held")
    elif lock.corrupt:
        lines.append("  lock: present but unreadable (owner unknown)")
    else:
        state = "stale" if lock.stale else "active"
        lines.append(
            f"  lock: held by {lock.user}@{lock.host} (pid {lock.pid}) since "
            f"{lock.acquired_utc}, {state}"
        )
    return "\n".join(lines)


def _run_info(args: argparse.Namespace) -> int:
    """Handle ``tether info``; exit 1 if any project could not be read."""
    # Lazy: keep the HDF5 stack off the ``--version`` path.
    import json

    from tether.project.summary import (
        SUMMARY_FORMAT_VERSION,
        ProjectSummaryError,
        read_project_summary,
    )

    failed = False
    documents: list[dict[str, object]] = []
    texts: list[str] = []
    for project in args.projects:
        try:
            summary = read_project_summary(project, check_stale=args.stale)
        except ProjectSummaryError as exc:
            failed = True
            print(f"tether info: {exc}", file=sys.stderr)
            documents.append({"path": project, "error": str(exc)})
            continue
        documents.append(summary.to_dict())
        texts.append(_format_summary(summary))
        # Detect-and-surface (§5.4, #560): stderr, so a --json document stays clean.
        _warn_conflict_copies(project)
    if args.json:
        print(
            json.dumps(
                {
                    "format": "tether-info",
                    "format_version": SUMMARY_FORMAT_VERSION,
                    "tether_version": __version__,
                    "projects": documents,
                },
                indent=2,
            )
        )
    elif texts:
        print("\n\n".join(texts))
    return 1 if failed else 0


def _add_metadata_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``metadata`` subcommand (show or give movie metadata, #911)."""
    meta = subparsers.add_parser(
        "metadata",
        help="show or give a project's frame time, pixel size and channel roles",
        description=(
            "Print each movie's frame time, pixel size and channel roles with their "
            "origin (file, user or unknown) and whether the frame time is verified. "
            "--frame-time and --pixel-size give a value (origin user); a given frame "
            "time is verified only by a separate --confirm-frame-time call."
        ),
    )
    meta.add_argument("project", help="path to the .tether project")
    meta.add_argument(
        "--movie",
        default=None,
        metavar="MOVIE_ID",
        help="the movie to change (default: the only movie of the project)",
    )
    meta.add_argument(
        "--frame-time", type=float, default=None, metavar="SECONDS", help="give the frame time"
    )
    meta.add_argument(
        "--pixel-size", type=float, default=None, metavar="UM", help="give the pixel size in um"
    )
    meta.add_argument(
        "--confirm-frame-time",
        action="store_true",
        help="mark the movie's frame time as verified (a separate call from --frame-time)",
    )
    meta.add_argument("--json", action="store_true", help="print the records as JSON")


def _run_metadata(args: argparse.Namespace) -> int:
    """Handle ``tether metadata``; exit 1 on a refused change, 2 on bad arguments."""
    import json

    from tether.project.core import Project
    from tether.project.lock import LockedError

    if args.confirm_frame_time and args.frame_time is not None:
        print(
            "tether metadata: --confirm-frame-time is a separate action; "
            "give --frame-time first, then confirm it in a second call",
            file=sys.stderr,
        )
        return 2
    try:
        project = Project.open(args.project)
        records = project.movie_metadata()
        changes = args.frame_time is not None or args.pixel_size is not None
        if changes or args.confirm_frame_time:
            movie_id = args.movie
            if movie_id is None:
                if len(records) != 1:
                    raise ValueError(
                        f"the project has {len(records)} movies; name one with --movie"
                    )
                (movie_id,) = records
            # One all-or-nothing write: a refused command (exit 1) leaves the
            # project as it was.
            project.update_movie_metadata(
                movie_id,
                frame_time=args.frame_time,
                pixel_size_um=args.pixel_size,
                confirm_frame_time=args.confirm_frame_time,
            )
            records = project.movie_metadata()
    except (KeyError, ValueError, OSError, LockedError) as exc:
        print(f"tether metadata: {exc}", file=sys.stderr)
        return 1
    _warn_conflict_copies(args.project)
    if args.json:
        print(json.dumps([r.to_dict() for r in records.values()], indent=2))
        return 0
    for r in records.values():
        state = "verified" if r.frame_time_verified else "not verified"
        print(f"{r.movie_id}:")
        print(f"  frame time: {r.frame_time:g} s ({r.frame_time_origin}, {state})")
        print(f"  pixel size: {r.pixel_size_um:g} um ({r.pixel_size_origin})")
        print(f"  channel roles: {r.channel_roles_origin}")
    return 0


#: The stages ``tether status --require`` accepts; ``tether.project.readiness.STAGES`` keys.
#: A literal, so ``build_parser`` stays off the HDF5 stack; a test pins the two equal.
_STATUS_STAGES = (
    "sources",
    "map",
    "traces",
    "curation",
    "corrections",
    "analysis_sets",
    "fits",
    "results",
    "deposit",
)

#: ``tether status`` exits with this code when a ``--require`` stage is not ready.
STATUS_NOT_READY_EXIT = 3


def _add_status_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``status`` subcommand (the readiness of each stage, #896)."""
    status = subparsers.add_parser(
        "status",
        help="print the readiness of each stage of a .tether project and the next step",
        description=(
            "Print each stage of the project in workflow order with its state, each stale "
            "item with the reason, the stored jobs, and the next step. The project is opened "
            "read-only and no lock is taken, as for tether info. Exit codes: 0 read, 1 the "
            f"project could not be read, 2 bad arguments, {STATUS_NOT_READY_EXIT} a --require "
            "stage is not ready."
        ),
    )
    status.add_argument("project", help="the .tether project")
    status.add_argument(
        "--json",
        action="store_true",
        help="print one JSON document (a documented, versioned layout) instead of text",
    )
    status.add_argument(
        "--require",
        action="append",
        choices=_STATUS_STAGES,
        metavar="STAGE",
        help=(
            f"exit {STATUS_NOT_READY_EXIT} unless STAGE is ready (or not needed); give it "
            f"again for more stages. One of: {', '.join(_STATUS_STAGES)}"
        ),
    )


def _format_readiness(r: ProjectReadiness) -> str:
    """The text form of one [`ProjectReadiness`][tether.project.readiness.ProjectReadiness]."""
    from tether.project.readiness import SECTIONS  # noqa: PLC0415

    lines = [r.path]
    for section, title in SECTIONS:
        lines.append(f"  {title}")
        for stage in (s for s in r.stages if s.section == section):
            detail = f" ({stage.detail})" if stage.detail else ""
            lines.append(f"    {stage.name}: {stage.state.text}{detail}")
    if r.stale:
        lines.append(f"  stale: {len(r.stale)}")
        lines.extend(f"    {s.item}: {s.reason}" for s in r.stale)
    else:
        lines.append("  stale: none")
    if r.jobs:
        lines.append(f"  jobs: {len(r.jobs)}")
        for j in r.jobs:
            error = f"; {j.error}" if j.error else ""
            lines.append(
                f"    {j.job_id[:8]} {j.command}: {j.state}, "
                f"{j.n_completed} of {j.n_parts} parts complete{error}"
            )
    else:
        lines.append("  jobs: none")
    lines.append(f"  next step: {r.next_step.action}")
    return "\n".join(lines)


def _run_status(args: argparse.Namespace) -> int:
    """Handle ``tether status``: 0 read, 1 unreadable, 3 a ``--require`` stage not ready."""
    import json  # noqa: PLC0415

    from tether.project.readiness import (  # noqa: PLC0415
        READINESS_FORMAT_VERSION,
        ProjectReadinessError,
    )
    from tether.services.status import project_status  # noqa: PLC0415

    try:
        readiness = project_status(args.project)
    except ProjectReadinessError as exc:
        print(f"tether status: {exc}", file=sys.stderr)
        return 1
    if args.json:
        document = {
            "format": "tether-status",
            "format_version": READINESS_FORMAT_VERSION,
            "tether_version": __version__,
            **readiness.to_dict(),
        }
        print(json.dumps(document, indent=2))
    else:
        print(_format_readiness(readiness))
    _warn_conflict_copies(args.project)
    code = 0
    for key in args.require or ():
        stage = readiness.stage(key)
        if not stage.state.done:
            print(f"tether status: {key} is {stage.state.text}", file=sys.stderr)
            code = STATUS_NOT_READY_EXIT
    return code


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
    if route is not None:
        return route.invoke(args)
    if command == "run":
        return _run_recipe(args)
    if command == "info":
        return _run_info(args)
    if command == "metadata":
        return _run_metadata(args)
    if command == "export":
        return run_export(args)
    if command == "status":
        return _run_status(args)
    parser.error(f"unknown command: {command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
