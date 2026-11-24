# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether export``: the three project exports from the command line (#658, FR-EXPORT).

Each form calls one function of ``tether.project.export`` and nothing else, so a file
written here is the file that function writes for the same arguments:

* ``tether export table`` calls ``export_molecule_table_csv``;
* ``tether export traces`` calls ``export_deeplasi_txt``;
* ``tether export subset`` calls ``export_subset_tether``.

The flags map one-to-one to the function's parameters. What this module adds is only
what a command line needs and a Python call does not: output naming for several
projects, a refusal to replace an existing output without ``--overwrite``, a refusal to
write over the source project, and exit codes (0 written, 1 failed, 2 usage).

An export only reads the source project. It takes no writer lock and never touches the
project's ``<file>.lock``, so it works on a project another user holds open.

Only `argparse` is imported at module level: ``tether.cli.build_parser`` registers
these parsers on the ``tether --version`` path, which must stay free of the HDF5 stack.
The export functions are imported inside [`run_export`][tether.cli_export.run_export].
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = [
    "EXPORT_FORMS",
    "INTENSITY_QUANTITIES",
    "TRACES_SUFFIX",
    "add_export_parser",
    "run_export",
]

#: The three forms, in the order the help lists them.
EXPORT_FORMS: tuple[str, ...] = ("table", "traces", "subset")

#: The ``intensity_quantity`` values the two table exports accept. A literal here, so
#: that building the parser imports nothing; a test pins it to the keys of
#: ``tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS``.
INTENSITY_QUANTITIES: tuple[str, ...] = ("corrected", "raw")

#: The Deep-LASI trace-matrix file ending that ``tether export traces`` names its files with.
TRACES_SUFFIX = "-donc-accc-w.txt"

#: The provenance sidecar every export writes beside its file.
_SIDECAR_SUFFIX = ".provenance.json"


#: The ``-o`` help of the two forms whose single-project output is a file.
_FILE_OR_FOLDER = (
    "the {kind} file to write; with several projects, or when it is an existing "
    "folder, the folder (created if absent) that gets one export per project, named "
    "after it"
)


class _UsageError(Exception):
    """A command line that parses but cannot run as given (exit 2, nothing written)."""


def add_export_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``tether export`` and its three forms on the top-level subparsers."""
    export = subparsers.add_parser(
        "export",
        help="write the molecule table, the trace text file or a subset project",
        description=(
            "Export from one or more .tether projects. The project is only read: no "
            "lock is taken, so this works on a project another user holds. Each export "
            "writes a <file>.provenance.json beside its file."
        ),
    )
    forms = export.add_subparsers(dest="export_form", metavar="<form>", required=True)

    table = forms.add_parser(
        "table",
        help="one CSV row per molecule (export_molecule_table_csv)",
        description=(
            "Write one CSV row per molecule: the stored per-molecule fields and the "
            "apparent-E summary. Rejected molecules are included unless "
            "--exclude-rejected is given."
        ),
    )
    _add_common(table, output_help=_FILE_OR_FOLDER.format(kind=".csv"))
    _add_intensity_quantity(table)
    table.add_argument(
        "--exclude-rejected",
        action="store_true",
        help="leave rejected molecules out of the table (include_rejected=False)",
    )

    traces = forms.add_parser(
        "traces",
        help="the Deep-LASI donor/acceptor trace matrix (export_deeplasi_txt)",
        description=(
            "Write the per-frame donor and acceptor traces as a Deep-LASI trace matrix, "
            f"<project>{TRACES_SUFFIX}, in the output folder. Rejected molecules are "
            "left out unless --include-rejected is given."
        ),
    )
    _add_common(
        traces,
        output_help=(
            f"the folder (created if absent) to write one <project>{TRACES_SUFFIX} in per project"
        ),
    )
    _add_intensity_quantity(traces)
    _add_include_rejected(traces)
    _add_molecule_key(traces)

    subset = forms.add_parser(
        "subset",
        help="a movie-less subset .tether project (export_subset_tether)",
        description=(
            "Write a self-contained .tether of the selected molecules, without the "
            "movie. Raw traces are left out unless --include-raw is given, and rejected "
            "molecules unless --include-rejected is given."
        ),
    )
    _add_common(subset, output_help=_FILE_OR_FOLDER.format(kind=".tether"))
    _add_include_rejected(subset)
    subset.add_argument(
        "--include-raw",
        action="store_true",
        help="also embed the raw and background traces (include_raw=True)",
    )
    _add_molecule_key(subset)
    subset.add_argument(
        "--curator-alias",
        default=None,
        metavar="PREFIX",
        help=(
            "replace each stored user name with PREFIX-1, PREFIX-2, ... "
            "(curator_alias; default: names are kept)"
        ),
    )


def _add_common(parser: argparse.ArgumentParser, *, output_help: str) -> None:
    parser.add_argument("projects", nargs="+", help="one or more .tether projects to read")
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help=output_help,
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing output file and its provenance file",
    )


def _add_intensity_quantity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--intensity-quantity",
        default="corrected",
        choices=INTENSITY_QUANTITIES,
        help="which trace layer to read (intensity_quantity; default: corrected)",
    )


def _add_include_rejected(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include-rejected",
        action="store_true",
        help="keep rejected molecules (include_rejected=True)",
    )


def _add_molecule_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--molecule-key",
        action="append",
        default=None,
        metavar="KEY",
        help=(
            "export only this molecule; repeat for more (molecule_keys; default: every molecule)"
        ),
    )


def _file_name(form: str, project: Path) -> str:
    """Return the name an export of ``project`` gets inside an output folder."""
    if form == "table":
        return f"{project.stem}.csv"
    if form == "traces":
        return f"{project.stem}{TRACES_SUFFIX}"
    return f"{project.stem}-subset.tether"


def _targets(
    form: str, projects: list[Path], output: Path
) -> tuple[list[tuple[Path, Path]], Path | None]:
    """Pair each source project with its output path; also return the folder to make.

    ``traces`` always writes into a folder. ``table`` and ``subset`` write the file
    ``output`` names, unless ``output`` is an existing folder or several projects are
    given; then each export goes into that folder, named after its project. The folder
    is returned rather than made, so that nothing is created before every check passed.
    """
    folder = form == "traces" or len(projects) > 1 or output.is_dir()
    if not folder:
        return [(projects[0], output)], None
    if output.exists() and not output.is_dir():
        why = "for traces" if form == "traces" else "with several projects"
        raise _UsageError(f"-o {output} is a file; {why}, -o names a folder")
    if form == "traces" and not output.exists() and output.suffix.lower() == ".txt":
        raise _UsageError(
            f"-o {output} looks like a file name; for traces, -o names a folder and the "
            f"file in it is named <project>{TRACES_SUFFIX}"
        )
    # Case-folded: on Windows and macOS, Exp.csv and exp.csv are one file.
    seen: dict[str, Path] = {}
    pairs = []
    for project in projects:
        name = _file_name(form, project)
        if name.casefold() in seen:
            raise _UsageError(
                f"projects {str(seen[name.casefold()])!r} and {str(project)!r} both map to "
                f"{(output / name).as_posix()!r}; rename one or export them separately"
            )
        seen[name.casefold()] = project
        pairs.append((project, output / name))
    return pairs, output


def _is_source(source: Path, out: Path) -> bool:
    """``out`` is ``source``: the same path, or a hard-linked alias of it."""
    if out.resolve() == source.resolve():
        return True
    return out.exists() and source.exists() and out.samefile(source)


def _input_overwritten(form: str, targets: list[tuple[Path, Path]]) -> str | None:
    """Return the refusal if an export or its provenance file is an input project or its lock.

    Every destination is checked against **every** input, not only its own source,
    before the first export starts: with ``--overwrite``, the subset of ``movie.tether``
    could otherwise replace ``movie-subset.tether`` given as a second input, which would
    destroy that input and then export the replacement.
    """
    from tether.project.lock import lock_path

    inputs = [source for source, _ in targets]
    for source, out in targets:
        for path in (out, out.with_name(out.name + _SIDECAR_SUFFIX)):
            for project in inputs:
                if _is_source(lock_path(project), path):
                    # An export is read-only: it must not replace another user's lock record.
                    return (
                        f"{form} output {path} is the lock file of the input project "
                        f"{project}; refusing to overwrite it"
                    )
                if not _is_source(project, path):
                    continue
                if project == source and path == out:
                    # The subset function's own refusal, worded the same way for the two
                    # table exports, which write straight to their path and do not check.
                    return f"{form} out_path {out} is the source project; refusing to overwrite"
                return (
                    f"{form} output {path} is the input project {project}; refusing to overwrite it"
                )
    return None


def _shared_destination(form: str, targets: list[tuple[Path, Path]]) -> str | None:
    """Return the refusal if two planned destinations are one existing file (#1217).

    ``table`` and ``traces`` open each output and provenance file in mode ``"w"``, so
    when two of those paths are hard links to one file the later export truncates it
    and replaces what the earlier one wrote, and the command still reports success.
    The names are distinct (``_targets`` refused case-folded clashes), so only the
    identity of the files behind them is compared, with ``Path.samefile`` as in
    ``_is_source``. ``subset`` writes its data file through a temporary file and a
    rename, which breaks the link, so only its provenance files, which are written in
    place like every other form's, are checked.
    """
    paths = []
    for _, out in targets:
        if form != "subset":
            paths.append(out)
        paths.append(out.with_name(out.name + _SIDECAR_SUFFIX))
    existing = [path for path in paths if path.exists()]
    for i, first in enumerate(existing):
        for second in existing[i + 1 :]:
            if first.samefile(second):
                return (
                    f"{form} outputs {first} and {second} are the same file (hard links); "
                    "refusing to write one over the other"
                )
    return None


def _export_one(form: str, source: Path, out: Path, args: argparse.Namespace):
    """Run one export; raise on any failure, return the function's ``ExportResult``."""
    from tether.io.schema import assert_is_compatible_project
    from tether.project.export import (
        export_deeplasi_txt,
        export_molecule_table_csv,
        export_subset_tether,
    )

    # A clear error for a missing or non-project file, before anything is written.
    assert_is_compatible_project(source)
    if not args.overwrite:
        for path in (out, out.with_name(out.name + _SIDECAR_SUFFIX)):
            if path.exists():
                raise FileExistsError(f"{path} already exists; pass --overwrite to replace it")

    if form == "table":
        return export_molecule_table_csv(
            source,
            out,
            intensity_quantity=args.intensity_quantity,
            include_rejected=not args.exclude_rejected,
        )
    if form == "traces":
        return export_deeplasi_txt(
            source,
            out,
            molecule_keys=args.molecule_key,
            intensity_quantity=args.intensity_quantity,
            include_rejected=args.include_rejected,
        )
    return export_subset_tether(
        source,
        out,
        molecule_keys=args.molecule_key,
        include_rejected=args.include_rejected,
        include_raw=args.include_raw,
        overwrite=args.overwrite,
        curator_alias=args.curator_alias,
    )


def run_export(args: argparse.Namespace) -> int:
    """Handle ``tether export <form>``: 0 all written, 1 any export failed, 2 usage.

    Projects are exported one after the other. A failure is reported on stderr and the
    remaining projects are still exported, as ``tether batch`` does with movies.
    """
    form = args.export_form
    prog = f"tether export {form}"
    projects = [Path(p) for p in args.projects]
    try:
        targets, folder = _targets(form, projects, Path(args.output))
        refusal = _input_overwritten(form, targets) or _shared_destination(form, targets)
    except _UsageError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # A path the checks cannot stat (a name too long, a folder it may not read).
        print(f"{prog}: {exc}", file=sys.stderr)
        return 1
    if refusal is not None:
        print(f"{prog}: {refusal}", file=sys.stderr)
        return 1
    if folder is not None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"{prog}: {exc}", file=sys.stderr)
            return 1

    from tether.services.notices import conflict_copy_warning

    failed = 0
    for source, out in targets:
        try:
            result = _export_one(form, source, out, args)
        except (OSError, ValueError, KeyError) as exc:
            # str(KeyError) adds quotes around the message; print the message itself.
            message = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
            where = f"{source}: " if len(targets) > 1 else ""
            print(f"{prog}: {where}{message}", file=sys.stderr)
            failed += 1
            continue
        print(f"Exported {result.n_molecules} molecule(s) -> {result.path}")
        # The same advisory stderr line as tether info: a sync-conflict copy beside the
        # source is named, never read or changed (PRD §5.4).
        warning = conflict_copy_warning(source)
        if warning is not None:
            print(f"  warning: {warning}.", file=sys.stderr)
    return 1 if failed else 0
