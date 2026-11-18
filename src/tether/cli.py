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
pipeline over many movies — see [`tether.services.batch`][]. Their subcommands are made
from [`tether.services.registry.COMMANDS`][], and [`unrouted_commands`][] names any
registered command that has no route here. ``tether info`` prints a read-only summary of
what a project holds — see [`tether.project.summary`][]. ``tether metadata`` shows and
gives a project's movie metadata with its origin — see
[`tether.project.movie_metadata`][].

The CLI deliberately uses the standard-library [`argparse`][] rather than a
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
from typing import TYPE_CHECKING

from tether import __version__
from tether.extract_choices import DETECTION_MODES, DONOR_SIDES, PREALIGN_MODES
from tether.services import (
    COMMANDS,
    BatchRequest,
    BatchResult,
    CommandFailed,
    ExtractRequest,
    ExtractResult,
    Request,
    RequestError,
    RequestRefused,
)
from tether.services import run as run_service
from tether.services.profile import read_profile

if TYPE_CHECKING:
    # Annotation-only: the summary module pulls in h5py, which ``--version`` must not.
    from tether.project.batch import BatchSummary
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
    _add_info_parser(subparsers)
    _add_metadata_parser(subparsers)
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
            "'intensity'/'bandpass' also honor --detection-threshold. "
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
            "(intensity/bandpass modes only; default: each mode's own — intensity 0.5, "
            "bandpass 0.98; ignored by wavelet). Mutually exclusive with --tdat"
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
            "default (wavelet 8, intensity/bandpass 3)"
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
            "rms_gate": args.rms_gate,
        }
    return ExtractRequest(
        movie=args.movie,
        output=args.output,
        overwrite=args.overwrite,
        tmap=args.tmap,
        tdat=args.tdat,
        **values,  # type: ignore[arg-type]
    )


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
            ".tether under --out-dir, isolating failures (continue-on-error) and\n"
            "checkpointing per stage so a re-run resumes only the incomplete stages.\n"
            "Prints an end-of-run summary and writes a JSONL structured log."
        ),
        epilog=_batch_exit_code_epilog(),
    )
    batch.add_argument(
        "movies",
        nargs="+",
        help=(
            "dual-channel TIFF movies: files, folders (the movies directly inside) or "
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
            "no movie is read and nothing is written"
        ),
    )
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
        help=f"a shared Deep-LASI .tdat detection config applied to every movie; {_TDAT_RULE}",
    )
    batch.add_argument(
        "--profile",
        default=None,
        metavar="PATH",
        help=(
            "extract every movie with the extraction options of an extraction "
            "profile (a JSON file; see docs/cli.md); an option the file leaves out keeps "
            "its default, and without a profile every option is at its default"
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
    values = _profile_values(args) or {}
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
    print(f"  structured log: {result.log_path}", file=sys.stderr)
    return code


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
    if command == "info":
        return _run_info(args)
    if command == "metadata":
        return _run_metadata(args)
    parser.error(f"unknown command: {command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
