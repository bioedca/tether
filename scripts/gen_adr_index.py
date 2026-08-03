# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate / verify the ADR index table in ``docs/adr/README.md`` (PRD §12.7).

**The defect this exists for is invisible to git.** #251 adds an ADR numbered 0057 while `main`
already carries a *different* record numbered 0057. The two have different filenames, so git reports
no conflict on either record — the only file that conflicts is this index, and a careless resolution
ships both. Every assertion in ``tests/test_adr_index.py`` still passes in that state: both records
are indexed, both links resolve, both titles match their own H1.

``claim.py reserve-adr`` prevents this between concurrent claimants. It does nothing for a branch
cut made before the reservation namespace existed, which is exactly #251's shape.

Follows ``scripts/dump_schema.py`` as the generate-plus-verify precedent:

* ``--write`` (default): regenerate the table block between the ``gen:adr-index`` markers.
* ``--check``: exit non-zero when the committed block differs from what the records imply.

**What is generated and what is preserved is a deliberate split.** The *mechanical* cells — number,
link target, and the Title cell, which ``tests/test_adr_index.py`` already requires to equal the
record's own H1 — come from disk. The *curated* cells do not: the index's Status column is not a
copy of the record's ``**Status:**`` field. ADR-0052's record runs to four lines of prose while its
index cell reads ``superseded by 0057 (fully retired)``, and ADR-0006's record carries a two-line
resolution note. A generator that overwrote those would degrade the index into something nobody
would read, so Status and PRD anchor are carried across from the committed index and only *derived*
for a record that has never been indexed — a first draft the author is expected to edit.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = _REPO_ROOT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"

TEMPLATE = "0000-template.md"

START = "<!-- gen:adr-index start (scripts/gen_adr_index.py --write) -->"
END = "<!-- gen:adr-index end -->"

# ADRs 0001-0008 were seeded together at M0 as the foundational set; everything after is homed by
# the PR that implements it. The split is editorial, it has been stable since the index was
# created, and it is a constant here rather than per-record metadata because no record carries it.
FOUNDATIONAL_MAX = 8
SECTIONS = (
    (0, FOUNDATIONAL_MAX, "### Foundational, cross-cutting (M0 seed — PLAN M0 S1)"),
    (FOUNDATIONAL_MAX + 1, 9999, "### Homed incrementally by implementing PRs"),
)

HEADER = ("| ADR | Title | Status | PRD anchor |", "|----:|-------|--------|------------|")

_ROW_RE = re.compile(r"^\|\s*\[(\d{4})\]\(")
# A cell boundary is an UNESCAPED pipe. `\|` is a literal pipe inside a Markdown cell, and splitting
# on it reads `accepted \| experimental` back as `experimental` - which `--write` would then commit
# as the curated value while `--check` went green on the corruption.
_CELL_RE = re.compile(r"(?<!\\)\|")
_H1_RE = re.compile(r"^#\s*\d{4}\s*[—–-]\s*(.+?)\s*$")
_FIELD_RE = r"^-\s+\*\*{name}:\*\*\s*(.+?)\s*$"

# A record's own Status/PRD-anchor bullet, used only for a record the index has never seen. Markdown
# emphasis is stripped so a derived cell does not arrive pre-bolded; links are kept, since an index
# cell like "superseded by 0057" is more useful pointing at the record it names.
_EMPHASIS_RE = re.compile(r"\*\*|\*|_(?=\S)|(?<=\S)_")


class IndexError_(RuntimeError):
    """A precondition failed. The message names the files involved."""


def _rel(path: Path) -> str:
    """A path for a message. Falls back to the bare name rather than raising.

    ``Path.relative_to`` raises when the target is outside the repository, which happens whenever
    the module's ``INDEX`` is pointed elsewhere - so a reporting call could crash a run that had
    otherwise succeeded.
    """
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:
        return path.name


def _text(path: Path) -> str:
    """Read with line endings normalised.

    The working tree is CRLF on this Windows-primary repository (`core.autocrlf=true` against
    `* text=auto`) while the repository stores LF. Comparing raw bytes would make ``--check`` report
    drift that does not exist, on one platform only.
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _records() -> dict[int, Path]:
    """Every numbered record, keyed by number.

    A shared number is fatal: it is the #251 defect, and nothing else in the repository sees it.
    """
    seen: dict[int, Path] = {}
    collisions: dict[int, list[str]] = {}
    for path in sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")):
        if path.name == TEMPLATE:
            continue
        number = int(path.name[:4])
        if number in seen:
            collisions.setdefault(number, [seen[number].name]).append(path.name)
        else:
            seen[number] = path
    if collisions:
        detail = "; ".join(
            f"ADR-{n:04d} is claimed by {', '.join(names)}"
            for n, names in sorted(collisions.items())
        )
        raise IndexError_(
            f"two records share one ADR number: {detail}. Git reports no conflict on this - the "
            "records have different filenames - so it has to be caught here. Renumber one with "
            "`python3 .agents/bin/claim.py reserve-adr` and move its inbound cross-links with it."
        )
    return seen


def _h1(path: Path) -> str:
    """The record's own H1, minus the ``NNNN — `` prefix.

    Takes the first ``# `` line rather than line 1: several records open with an SPDX HTML comment.
    """
    for line in _text(path).splitlines():
        if line.startswith("# "):
            match = _H1_RE.match(line)
            return match.group(1) if match else line[2:].strip()
    raise IndexError_(f"{path.name} has no H1 heading")


def _field(path: Path, name: str) -> str:
    """The first line of a record's ``**<name>:**`` bullet, de-emphasised. Empty when absent."""
    for line in _text(path).splitlines():
        match = re.match(_FIELD_RE.format(name=name), line)
        if match:
            return _EMPHASIS_RE.sub("", match.group(1)).strip()
    return ""


def _curated() -> dict[str, tuple[str, str]]:
    """``{NNNN: (status, prd_anchor)}`` from the committed index.

    Cells are taken from the end, never by position from the start: a Title containing a pipe would
    shift a left-anchored parse, and Status and PRD anchor are always the last two.
    """
    out: dict[str, tuple[str, str]] = {}
    for line in _text(INDEX).split("\n"):
        match = _ROW_RE.match(line)
        if match:
            cells = _CELL_RE.split(line)
            out[match.group(1)] = (cells[-3].strip(), cells[-2].strip())
    return out


def _escape(cell: str) -> str:
    """Escape a pipe so it stays table *content*.

    A raw `|` in a generated cell is a delimiter, so an ADR whose H1 contains one would shift or
    truncate the Title, Status and anchor cells - and neither `--check` nor the structural tests
    would see it, because `_curated` reads cells from the right and `_indexed_titles` rejoins the
    middle. The index would simply render wrong.
    """
    return cell.replace("|", r"\|")


def _derived(path: Path, name: str) -> str:
    """A curated cell's first draft for a record the index has never seen.

    Refuses rather than defaulting. Publishing `accepted` for a record whose Status bullet is
    missing or malformed FABRICATES the value, and once written it becomes curated data: later
    correcting the record does not update the index, and `--check` keeps passing over the invention.
    """
    value = _field(path, name)
    if not value:
        raise IndexError_(
            f"{path.name} has no `**{name}:**` bullet, so there is nothing to index it with. Add "
            "one to the record rather than letting the index state something the record does not."
        )
    return value


def _rows(
    numbers: list[int], records: dict[int, Path], curated: dict[str, tuple[str, str]]
) -> list[str]:
    rows = []
    for number in numbers:
        path = records[number]
        key = f"{number:04d}"
        # Curated cells are carried across as stored - they already contain whatever escaping their
        # author wrote - while a derived cell is raw record text and has to be escaped here.
        cells = curated.get(key)
        if cells is None:
            cells = (_escape(_derived(path, "Status")), _escape(_derived(path, "PRD anchor")))
        status, anchor = cells
        rows.append(f"| [{key}]({path.name}) | {_escape(_h1(path))} | {status} | {anchor} |")
    return rows


def render() -> str:
    """The generated block, markers included."""
    records = _records()
    curated = _curated()
    lines = [START, ""]
    for low, high, heading in SECTIONS:
        numbers = sorted(n for n in records if low <= n <= high)
        if not numbers:
            continue
        # Header, delimiter and rows are emitted as one uninterrupted run. A single blank line
        # legally terminates a Markdown table, and a split table is how ADRs 0039-0050 reached the
        # published site as one run-on paragraph of literal pipes.
        lines += [heading, "", *HEADER, *_rows(numbers, records, curated), ""]
    lines.append(END)
    return "\n".join(lines)


def _splice(current: str, block: str) -> str:
    start, end = current.find(START), current.find(END)
    if start == -1 or end == -1:
        raise IndexError_(
            f"{INDEX.name} is missing the gen:adr-index markers; add {START} and {END} around the "
            "index tables"
        )
    return current[:start] + block + current[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="verify the committed index instead of rewriting it"
    )
    args = parser.parse_args(argv)
    try:
        wanted = _splice(_text(INDEX), render())
    except IndexError_ as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if _text(INDEX) == wanted:
        print(f"{_rel(INDEX)} is up to date ({len(_records())} records).")
        return 0
    if args.check:
        print(
            f"::error::{_rel(INDEX)} does not match the records on disk; "
            "run `python scripts/gen_adr_index.py` and commit the result",
            file=sys.stderr,
        )
        return 1
    INDEX.write_text(wanted, encoding="utf-8", newline="")
    print(f"Wrote {_rel(INDEX)} ({len(_records())} records).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
