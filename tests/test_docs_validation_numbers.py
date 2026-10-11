# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every number ``docs/validation.md`` transcribes still matches its source artifact.

``docs/validation.md`` is a *transcription* page: it restates the four ``$.tolerance``
bounds, the four ``$.tolerance_by_method.ebhmm`` bounds, both ``pooled_worst`` blocks,
the kinSoftChallenge rate matrices and roughly a dozen module constants from ``src/``.
Nothing pinned any of them. ``mkdocs build --strict`` is a link-and-nav check, not a
value check, so a freeze that moved a bound — or a constant edited in ``src/`` — left
the published validation page silently wrong while every gate stayed green. That is the
exact failure mode #158 was written to eliminate, so this module closes it (#214).

**Structural selection, not literal grepping.** A pin never searches the page for its
own number; it *locates a region first* and then asks that region what it states:

* :class:`Cell` selects the table in a named ``##`` section whose header carries a given
  cell, then the row whose first cell names the pin, then the column by its header text.
  Every step asserts a unique match, so a page edit that duplicates or renames a table,
  row or column fails loudly here instead of silently widening what counts.
* :class:`Prose` selects the one non-table paragraph of a section that names an anchor
  token, then attributes each literal in it to the nearest *preceding* name token. So
  ``` `viterbi_agreement` 0.9355949176941853 ``` binds that number to that key, and
  moving the number next to a different key breaks the pin rather than passing because
  the digits are still somewhere on the page. *Every* literal attributed to a key has to
  agree, not just one, so a key stated twice in one paragraph cannot keep a stale
  contradictory restatement alive behind a correct one.
* :class:`Quote` renders a live artifact value into a fixed phrase and requires the phrase
  verbatim in the selected paragraph, whitespace-normalised so a re-wrap cannot break it.
  The phrase carries the words that say *which* field the value is, so swapping the paper
  DOI with the data DOI fails even though both remain present. It also pins the
  measurement counts, the licence, and the two figures the page prints as percentages.

Section, table, row, column and paragraph selection each assert a **unique** match, and a
repeated ``##`` heading is an error rather than a last-one-wins overwrite — a stale
duplicate section inserted above the canonical one would otherwise be read by nothing here
and published anyway.

The artifacts are read with :mod:`json` and the module constants with :mod:`ast`, so the
guard is dependency-free: no ``tether`` import, no SciPy/h5py, and it runs unmarked on
the plain 3-OS ``test`` matrix.

**Scope.** The pinned set is the one #214's accepted criteria enumerate: both frozen
tolerance blocks, both ``pooled_worst`` blocks, the kinSoft matrices, the measurement
counts, and every module constant the page prints a value for. Within that set,
:data:`EXCLUSIONS` records what is deliberately left unpinned and why, because on a
validation page an unexplained omission and an oversight look identical.

**Restatements (#435).** A value restated outside the region its first pin selects is
unguarded by that pin, so #435 adds a pin per restatement where a committed artifact is the
source: both tMAVEN commits in (b)'s build table, (b)'s live-pin paragraph, (b)'s "Gap,
stated plainly" blockquote and the third *Scope and known limitations* bullet; both
``sidecar_python_version`` values beside them; the blockquote's rounded ``$.pooled_worst``
figures, ceilings and run count; and the two prose copies of ebFRET's ``0.5249`` floor.
Each such pin carries a ``source`` label naming its *location* as well as its pointer,
because the mutation tests collect every pin sharing a label and require one edited page
to break all of them.

This is still **not** a page-wide numerical-coverage manifest. What stays unpinned on
purpose — including ``RMS ≤ 0.5 px`` in (a)'s enforcing-tests sentence, which is a test's
own literal rather than a transcription — is in :data:`EXCLUSIONS` with its reason.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "validation.md"
SRC = ROOT / "src"
PARITY_PATH = ROOT / "schema" / "parity_tolerance.json"
KINSOFT_PATH = ROOT / "schema" / "kinsoft_reference.json"

PAGE_TEXT = PAGE.read_text(encoding="utf-8")
PARITY = json.loads(PARITY_PATH.read_text(encoding="utf-8"))
KINSOFT = json.loads(KINSOFT_PATH.read_text(encoding="utf-8"))


# --- Reading the source artifacts ---------------------------------------------


def at(doc: dict, pointer: str) -> object:
    """The value of a dotted ``$.a.b.c`` pointer, spelled the way the page spells it."""
    node: object = doc
    for key in pointer.removeprefix("$.").split("."):
        assert isinstance(node, dict), f"{pointer}: {key!r} is not under a mapping"
        assert key in node, f"{pointer}: no key {key!r}"
        node = node[key]
    return node


def module_constant(dotted: str, name: str) -> object:
    """The module-level value of ``name`` in ``tether.x.y``, read from disk with ``ast``.

    Parsed rather than imported so the guard has no runtime dependency and executes no
    module-import side effects — the same reason ``test_docs_parameter_defaults`` reads
    its defaults this way.

    The **last** top-level binding is the one returned, because it is the one the runtime
    uses: a module that rebinds ``X = 5.0`` and then ``X = 8.0`` is read as ``8.0``.
    Returning the first would pin the page to a value the code never uses.
    """
    path = SRC / Path(*dotted.split(".")).with_suffix(".py")
    bound: ast.expr | None = None
    for stmt in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(stmt, ast.AnnAssign):
            # An annotation with no value (`X: float`) binds nothing at runtime, so the
            # assignment before it stays the effective one.
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name and stmt.value:
                bound = stmt.value
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    bound = stmt.value
    if bound is None:
        raise AssertionError(f"{dotted} has no module-level constant {name!r}")
    return ast.literal_eval(bound)


# --- Reading the page ----------------------------------------------------------

#: A level-2 heading only. The negative lookahead keeps a future ``### Sub-heading`` from
#: parsing as a section literally named ``# Sub-heading``.
_HEADING_RE = re.compile(r"^## (?!#)(.+)$")
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
_BACKTICKED_RE = re.compile(r"`([^`]+)`")
_QUOTED_RE = re.compile(r'"([^"]*)"')
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
#: A constant as the page spells one. Leading-underscore test helpers (``_EXPECTED_
#: EVIDENCE``) are deliberately out: the page names them, it never states their value.
#: Paired with the backtick filter in
#: :func:`test_every_constant_the_page_states_a_value_for_is_pinned`, because prose
#: acronyms (``PRD §11.2``, ``PEP 610``, ``RMS ≤ 0.5 px``) are shaped like constants and
#: sit next to a number without being one; the page only ever writes real code in code
#: spans.
_CONSTANT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: The identifier a code span opens with, however it continues (`X = 1`, `X=1`, `X`).
_LEADING_IDENT_RE = re.compile(r"^[A-Za-z_]\w*")
#: The name a code span opens with, in the :data:`_TOKEN_RE` name shape — so unlike
#: :data:`_LEADING_IDENT_RE` it admits a leading ``$`` and a dotted pointer. Used by
#: :func:`paragraph`'s second tier, so `$.band.rate_rel_deviation_max = 0.12` is reachable
#: by the bare pointer `$.band.rate_rel_deviation_max`.
_LEADING_TOKEN_RE = re.compile(r"^[A-Za-z_$][\w.$/-]*")
#: Python-Markdown's fenced-code rule, copied verbatim from
#: ``markdown.extensions.fenced_code.FencedBlockPreprocessor.FENCED_BLOCK_RE`` in
#: Python-Markdown 3.11, the version the docs toolchain resolves here.
#: ``requirements-docs.txt`` pins ``mkdocs==1.6.1``, which enables ``fenced_code`` by
#: default, and leaves Markdown itself unpinned. It is copied rather than imported so the
#: guard stays dependency-free; :func:`test_the_fence_rule_is_python_markdowns` compares
#: it with the installed one wherever Markdown is importable. What it encodes: the
#: opening delimiter starts at column zero; a block exists only once a closing line
#: repeating the opener exactly is found; an opener with no closer is ordinary text.
_FENCED_BLOCK_RE = re.compile(
    r"""
(?P<fence>^(?:~{3,}|`{3,}))[ ]*                          # opening fence
((\{(?P<attrs>[^\n]*)\})|                                # (optional {attrs} or
(\.?(?P<lang>[\w#.+-]*)[ ]*)?                            # optional (.)lang
(hl_lines=(?P<quot>"|')(?P<hl_lines>.*?)(?P=quot)[ ]*)?) # optional hl_lines)
\n                                                       # newline (end of opening fence)
(?P<code>.*?)(?<=\n)                                     # the code block
(?P=fence)[ ]*$                                          # closing fence
""",
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)


def _fenced(lines: list[str]) -> list[bool]:
    """For each line, is it part of a fenced code block, opener and closer included?

    Blocks are found the way Python-Markdown finds them: complete opener/body/closer
    matches of :data:`_FENCED_BLOCK_RE`, leftmost first and non-overlapping. One edge is
    not reproduced: Python-Markdown also rejects an ``{attrs}`` opener whose braces do
    not balance, and this treats it as a fence.
    """
    text = "\n".join(lines)
    spans = [m.span() for m in _FENCED_BLOCK_RE.finditer(text)]
    out: list[bool] = []
    offset = 0
    for line in lines:
        out.append(any(start <= offset < end for start, end in spans))
        offset += len(line) + 1
    return out


def _section_spans(text: str) -> list[tuple[str, int, int]]:
    """Each ``##`` section of ``text`` as ``(name, body_start, body_end)`` offsets.

    The one place a section boundary is decided, so :func:`_sections` and
    :func:`_mutate` cannot disagree about where a body ends. A ``## `` line inside a
    fenced code block is code, not a heading.
    """
    out: list[tuple[str, int, int]] = []
    current: tuple[str, int] | None = None
    lines = text.split("\n")
    offset = 0
    for line, fenced in zip(lines, _fenced(lines), strict=True):
        heading = None if fenced else _HEADING_RE.match(line)
        if heading:
            if current is not None:
                out.append((current[0], current[1], max(current[1], offset - 1)))
            current = (heading.group(1).strip(), offset + len(line) + 1)
        offset += len(line) + 1
    if current is not None:
        out.append((current[0], current[1], max(current[1], len(text))))
    return out


#: Name / string / number, tried in that order so ``smd_281mol`` and ``k12`` stay whole
#: names rather than decomposing into a name and a stray number. Slashes and hyphens are
#: part of a name so ``src/tether/fret/gamma.py`` and ``ADR-0043`` do not leak digits.
_TOKEN_RE = re.compile(
    r"""(?P<name>[A-Za-z_$][\w.$/-]*)
      | (?P<string>"[^"]*")
      | (?P<number>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)""",
    re.VERBOSE,
)


class Table(NamedTuple):
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _sections(text: str) -> dict[str, str]:
    """The page split on its ``##`` headings. Prose above the first one is dropped.

    A repeated heading is an error, not a last-one-wins overwrite. A stale duplicate of
    a validation section inserted *before* the canonical one would otherwise be invisible
    to every pin here — they would all read the later copy — while the contradictory
    earlier copy stayed published and the suite stayed green.

    A ``## `` line inside a fenced code block is code, not a heading, and is kept as body;
    the boundary itself comes from :func:`_section_spans`.
    """
    out: dict[str, str] = {}
    for name, start, end in _section_spans(text):
        assert name not in out, (
            f"docs/validation.md has two '## {name}' sections. Every pin would read only "
            "the later one while the earlier stayed published; de-duplicate the page."
        )
        out[name] = text[start:end]
    return out


def _tables(body: str) -> list[Table]:
    """Every Markdown table in ``body``, header row first.

    A ``|`` line inside a fenced code block is code, not a table row.
    """
    out: list[Table] = []
    header: list[str] | None = None
    rows: list[tuple[str, ...]] = []
    lines = body.split("\n")
    for line, fenced in zip(lines, _fenced(lines), strict=True):
        stripped = line.strip()
        if not fenced and stripped.startswith("|"):
            if _SEPARATOR_RE.match(stripped):
                continue
            if header is None:
                header = _cells(stripped)
            else:
                rows.append(tuple(_cells(stripped)))
        elif header is not None:
            out.append(Table(tuple(header), tuple(rows)))
            header, rows = None, []
    if header is not None:
        out.append(Table(tuple(header), tuple(rows)))
    return out


def _prose_paragraphs(body: str) -> list[str]:
    """Blank-line-separated blocks that are not tables.

    Tables are excluded because several anchors (``$.tolerance_by_method.ebhmm``) name
    both a sentence and a column header; a :class:`Prose` pin must never resolve to the
    table a :class:`Cell` pin already owns.
    """
    blocks = [b for b in re.split(r"\n\s*\n", body) if b.strip()]
    return [
        b
        for b in blocks
        if not all(line.strip().startswith("|") for line in b.split("\n") if line.strip())
    ]


def _owned_literals(paragraph: str) -> dict[str, set[object]]:
    """Each name token in ``paragraph`` mapped to the literals that follow it.

    A literal belongs to the nearest name *before* it, which is how this page writes
    every one of them — ``` `k32` 0.680 ``` , ``` `measured_utc` `"2026-07-08"` ``` .
    Backticks are stripped first so a whole backticked clause (``DEFAULT_SHIP_BAR_PTS =
    10.0``) tokenizes like the prose around it.
    """
    owner: str | None = None
    out: dict[str, set[object]] = {}
    for match in _TOKEN_RE.finditer(paragraph.replace("`", " ")):
        kind = match.lastgroup
        text = match.group()
        if kind == "name":
            owner = text
        elif owner is not None:
            with contextlib.suppress(SyntaxError, ValueError):
                out.setdefault(owner, set()).add(ast.literal_eval(text))
    return out


def _states(cell: str, value: object) -> bool:
    """Does the table cell ``cell`` state ``value``?

    A string is compared against the whole cell and against each backticked or quoted
    token in it, because the page writes ``2026-06-26`` bare in a table and
    ``` `"2026-06-26"` ``` in prose. A number is compared against every numeric literal
    in the cell — which is what lets ``≤ 0.12`` state ``0.12`` — with the parsed type
    required to match, so ``0`` never satisfies ``0.0``.
    """
    if isinstance(value, str):
        # Explicit transcriptions first -- code spans and quoted strings -- and the bare
        # cell only when it carries neither, because the page writes `2026-06-26` bare in
        # a table and ``"2026-06-26"`` in prose. The cell must state exactly ONE of them:
        # keeping the right date and adding a stale one beside it is a contradiction, the
        # same way it is for a number. A cell that legitimately needs a second code span
        # fails here loudly rather than quietly widening what counts.
        tokens = set(_BACKTICKED_RE.findall(cell)) | set(_QUOTED_RE.findall(cell))
        return (tokens or {cell.strip()}) == {value}
    numbers: list[object] = []
    for text in _NUMBER_RE.findall(cell):
        with contextlib.suppress(SyntaxError, ValueError):
            numbers.append(ast.literal_eval(text))
    # Every number in the cell must agree, not merely one of them: `0.9 (also 0.8)` keeps
    # the right value while publishing a contradiction, which is the same defect the
    # Prose branch rejects. Every pinned Value cell states exactly one number today.
    comparable = [x for x in numbers if _same_kind(x, value)]
    return bool(comparable) and all(type(x) is type(value) and x == value for x in comparable)


# --- The three pin kinds -------------------------------------------------------


class Cell(NamedTuple):
    """One value read out of a named table row and column."""

    section: str
    #: A header cell that identifies the table uniquely inside ``section``.
    table: str
    #: The row, matched against its first cell's backticked tokens or as a prefix.
    row: str
    #: The header cell of the column holding the value.
    column: str
    #: Where the live value comes from, for the failure message.
    source: str
    value: object

    @property
    def label(self) -> str:
        return f"{self.row}|{self.column}"


class Prose(NamedTuple):
    """One value stated next to its own key in a sentence."""

    section: str
    #: A backticked token naming the one non-table paragraph this pin reads.
    anchor: str
    #: The name token the value must sit behind.
    key: str
    source: str
    value: object

    @property
    def label(self) -> str:
        return f"{self.anchor}|{self.key}"


class Quote(NamedTuple):
    """A live value rendered into a fixed phrase that must appear verbatim."""

    section: str
    anchor: str
    #: ``str.format`` template; ``{}`` takes the rendered value.
    template: str
    source: str
    value: object
    #: Multiplier applied before rendering — the page prints two rates as percentages.
    scale: float = 1.0
    #: A *declared* rendering of the live value: :func:`short_commit` or :func:`sci3` where
    #: the page abbreviates or rounds it, :func:`shortest` where it states a float exactly.
    #: The default ``:g`` below keeps only six significant digits, so it is not an exact
    #: rendering of an arbitrary float; a float the page claims to state exactly takes
    #: :func:`shortest`, and a deliberate rounding is declared rather than left to ``:g``.
    #: The page's printed text is never transformed; only the live value is.
    render: Callable[[object], str] | None = None

    @property
    def label(self) -> str:
        return f"{self.anchor}|{self.template}"

    def value_text(self) -> str:
        """The live value as the page spells it."""
        if self.render is not None:
            return self.render(self.value)
        if isinstance(self.value, str):
            return self.value
        scaled = self.value * self.scale
        text = f"{scaled:g}"
        # `:g` keeps six significant digits. A longer value would compare a truncated
        # artifact value against a truncated page value and accept a rounding, so refuse
        # to render one rather than let a pin quietly compare less than it claims to.
        assert float(text) == scaled, (
            f"{scaled!r} does not round-trip through ':g' (renders {text!r}); this Quote "
            "would compare a rounded value"
        )
        return text

    def rendered(self) -> str:
        return self.template.format(self.value_text())


Pin = Cell | Prose | Quote


# --- Locating a pin's region ---------------------------------------------------


def _section(text: str, name: str) -> str:
    sections = _sections(text)
    assert name in sections, f"docs/validation.md has no '## {name}' section"
    return sections[name]


def _table(text: str, pin: Cell) -> Table:
    candidates = [t for t in _tables(_section(text, pin.section)) if pin.table in t.header]
    assert len(candidates) == 1, (
        f"'{pin.section}' must hold exactly one table with a {pin.table!r} header cell; "
        f"found {len(candidates)}"
    )
    return candidates[0]


def _row_matches(cell: str, label: str) -> bool:
    """Is ``cell`` the row labelled ``label``?

    A backticked token has to match exactly. A bare label may be a prefix — the deferred
    levels are written ``level2 (Fig. 3)`` — but only up to a boundary, so ``level2`` no
    longer answers for a row renamed or mistyped as ``level20``, which would quietly
    validate that row's numbers under the wrong label instead of failing selection.
    """
    if label in _BACKTICKED_RE.findall(cell):
        return True
    if not cell.startswith(label):
        return False
    rest = cell[len(label) :]
    return not rest or not (rest[0].isalnum() or rest[0] == "_")


def cell_text(text: str, pin: Cell) -> str:
    """The single cell a :class:`Cell` pin addresses."""
    table = _table(text, pin)
    # Exactly one column, not merely at least one: a stale duplicate `$.tolerance` column
    # would bind every pin to the first and leave the contradicting one published.
    found = table.header.count(pin.column)
    assert found == 1, (
        f"the {pin.table!r} table in '{pin.section}' must have exactly one {pin.column!r} "
        f"column; found {found}. Its header is {list(table.header)}"
    )
    column = table.header.index(pin.column)
    matches = [row for row in table.rows if _row_matches(row[0], pin.row)]
    assert len(matches) == 1, (
        f"the {pin.table!r} table in '{pin.section}' must hold exactly one row for "
        f"{pin.row!r}; found {len(matches)}"
    )
    row = matches[0]
    assert column < len(row), f"row {pin.row!r} has no cell under {pin.column!r}"
    return row[column]


def paragraph(text: str, section: str, anchor: str) -> str:
    """The one non-table paragraph of ``section`` selected by ``anchor``.

    Three tiers, each consulted only when the one above it selected nothing:

    1. **Exact code span** — a paragraph carrying a code span whose whole text is
       ``anchor``.
    2. **Leading token** — a paragraph carrying a code span that *opens* with ``anchor``
       as its leading name token, so the bare pointer `$.band.rate_rel_deviation_max`
       reaches the span `$.band.rate_rel_deviation_max = 0.12`. An anchor therefore never
       has to repeat the value its pin reads, and a page updated correctly to a new value
       stays selectable.
    3. **Lead-in** — a paragraph that starts with ``anchor``, for the few the page never
       labels with a pointer (``**Measured result.**``).

    The exact tier comes first so that inserting a tier cannot change what an anchor
    already selects by exact code-span text; that is the same reason the lead-in tier is
    consulted only when every tier above it matched nothing.
    """
    paragraphs = _prose_paragraphs(_section(text, section))
    matches = [p for p in paragraphs if anchor in _BACKTICKED_RE.findall(p)]
    if not matches:
        matches = [
            p
            for p in paragraphs
            if any(
                (lead := _LEADING_TOKEN_RE.match(span.strip())) and lead.group() == anchor
                for span in _BACKTICKED_RE.findall(p)
            )
        ]
    if not matches:
        matches = [p for p in paragraphs if p.lstrip().startswith(anchor)]
    assert len(matches) == 1, (
        f"'{section}' must hold exactly one non-table paragraph selected by {anchor!r}; "
        f"found {len(matches)} — re-anchor the pin rather than relaxing it"
    )
    return matches[0]


def _template_regex(template: str) -> re.Pattern[str]:
    """``"Paper doi `{}`"`` as a pattern that captures whatever value is printed there.

    Used so a :class:`Quote` can check *every* place its claim is made rather than
    stopping at the first. The placeholder captures a run of non-space characters, which
    is what every pinned value is; the surrounding words are matched literally, so the
    pattern only fires where the claim is actually being made.
    """
    head, placeholder, tail = template.partition("{}")
    assert placeholder, f"a Quote template needs a {{}} placeholder: {template!r}"
    return re.compile(re.escape(head) + r"(\S+?)" + re.escape(tail))


#: The ``>`` that opens every line of a Markdown blockquote, with the one space after it.
_BLOCKQUOTE_MARKER_RE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)


def _flat(text: str) -> str:
    """One line, single-spaced — so a pinned phrase survives Markdown re-wrapping.

    The page hard-wraps at 90-odd columns, so ``Paper doi`` and the DOI that belongs to it
    sit on different lines. Without this a label-bound phrase would be unmatchable purely
    because of where the paragraph happened to break.

    A blockquote's per-line ``>`` marker is stripped first. Left inline, it lands in the
    middle of any phrase that wraps — ``are 1.0 on > both floors`` — so a pin inside a
    blockquote would hold or fail depending on where the line broke, which is the
    fragility this function exists to remove.
    """
    return " ".join(_BLOCKQUOTE_MARKER_RE.sub("", text).split())


def _same_kind(literal: object, value: object) -> bool:
    """Could ``literal`` be a *restatement* of ``value``, right or wrong?

    Any two numbers are candidates for the same claim even when their Python types
    differ, so ``11`` counts as a contradiction of ``10.0`` rather than being waved
    through as a different kind of thing. Equality itself stays type-exact.
    """
    numeric = (int, float)
    if isinstance(literal, bool) or isinstance(value, bool):
        return type(literal) is type(value)
    if isinstance(literal, numeric) and isinstance(value, numeric):
        return True
    return type(literal) is type(value)


def satisfied(text: str, pin: Pin) -> bool:
    """Does ``text`` still state this pin's live value where the pin says it does?"""
    if isinstance(pin, Cell):
        return _states(cell_text(text, pin), pin.value)
    if isinstance(pin, Prose):
        owned = _owned_literals(paragraph(text, pin.section, pin.anchor)).get(pin.key, set())
        # EVERY comparable literal must agree, not merely one of them. A key stated twice
        # in a paragraph -- `DEFAULT_SHIP_BAR_PTS` is -- would otherwise keep a stale
        # contradictory restatement published as long as one occurrence stayed right.
        # Scoped by kind because the nearest-name rule legitimately sweeps up a trailing
        # count of another kind: "`measured_utc` `"2026-07-08"`, 19 cross-seed comparisons"
        # attributes both to `measured_utc`, and only the date is a restatement of it.
        comparable = [x for x in owned if _same_kind(x, pin.value)]
        return bool(comparable) and all(
            type(x) is type(pin.value) and x == pin.value for x in comparable
        )
    # Every occurrence of the claim must carry the live value. A substring test would
    # accept a paragraph that keeps `(5 % average)` and adds `(6 % average)` beside it.
    stated = _template_regex(pin.template).findall(_flat(paragraph(text, pin.section, pin.anchor)))
    return bool(stated) and all(found == pin.value_text() for found in stated)


# --- The registry --------------------------------------------------------------
# Every entry reads its value live, so a bound re-frozen in schema/, a rate re-measured
# in schema/, or a constant edited in src/ fails here until the page follows.

_PARITY_SECTION = "(b) Idealization parity vs tMAVEN"
_KINSOFT_SECTION = "External benchmark"
_BOUNDS = (
    "state_count_min_fraction",
    "state_mean_abs_delta_max",
    "viterbi_min_agreement",
    "relative_elbo_max",
)
_METRICS = (
    "state_count_fraction",
    "state_mean_abs_delta",
    "viterbi_agreement",
    "relative_elbo",
)
#: The kinSoft level-3 rate matrix, in the order the page prints it.
_LEVEL3_RATES = ("k12", "k14", "k21", "k23", "k32", "k41")
_LEVEL3_RATES_POINTER = "$.levels.level3.ground_truth.rates_s_inv"
_LEVEL3_NOTE_POINTER = "$.levels.level3.ground_truth.note"

#: The clause that turns six numbers into a 4x4 matrix, on either side. The artifact
#: writes "are 0" and the page writes "zero", so it is matched by shape and the captured
#: word is then required to *mean* zero — which "0.01" does not.
#: A transition the page enumerates: ``k12``, never ``kbright``.
_TRANSITION_RE = re.compile(r"^k\d+$")
_ALL_OTHER_RE = re.compile(r"all other off-diagonal rates (?:are )?([A-Za-z0-9.]+)")
_ZERO_WORDS = frozenset({"0", "0.0", "zero"})


def zero_clauses(text: str) -> tuple[list[str], list[str]]:
    """Every ``all other off-diagonal rates X`` clause in ``text``, split by whether X is zero.

    The guard *and* the tests that prove the guard bites both call this, so a regression
    here fails both. Written as a helper for exactly that reason: a mutation test that
    re-implements the check it is meant to protect keeps passing after the real check
    regresses, which is a test that tests itself.
    """
    stated = [m.group(1).rstrip(".,;)") for m in _ALL_OTHER_RE.finditer(text)]
    return [c for c in stated if c in _ZERO_WORDS], [c for c in stated if c not in _ZERO_WORDS]


def quotation_sides(fragment: str, artifact: dict, pointer: str, region: str) -> tuple[bool, bool]:
    """Is ``fragment`` still in the artifact, and still in the page region that quotes it?

    Shared by the live check and by the test that proves a dropped quotation fails, so
    neither can drift away from the other. Returned as two flags so the caller can say
    *which* side went stale.
    """
    held = at(artifact, pointer)
    source = held if isinstance(held, str) else "\n".join(str(item) for item in held)  # type: ignore[union-attr]
    return fragment in source, fragment in _flat(region)


#: ``kbright=7 s^-1`` inside the level-3 ``note``, keyed on the name. The literal is
#: matched whole — sign, decimals and exponent — with a trailing boundary, because
#: ``[0-9.]+`` would read ``kbright=7e-1`` as ``7`` and let the page keep saying 7 while
#: the artifact had come to mean 0.7.
_BLINKING_RE = r"\b{name}\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![\w.])"


def _blinking_rate(name: str) -> object:
    """``kbright`` / ``kdark`` as the level-3 ``note`` records them.

    These two are the one pair the artifact keeps inside free text — *"blinking kbright=7
    s^-1, kdark=0.007 s^-1"* — rather than as fields of ``rates_s_inv``, so they are read
    with a regex bounded to the key name instead of a pointer. Restating the literal here
    would make the pin assert the test's own copy of the number, and a pin that reads
    nothing is prose.
    """
    note = str(at(KINSOFT, "$.levels.level3.ground_truth.note"))
    stated = {m.group(1) for m in re.finditer(_BLINKING_RE.format(name=name), note)}
    assert stated, f"the level-3 note no longer records `{name}=`; re-read it before pinning"
    assert len(stated) == 1, (
        f"the level-3 note states {name} as {sorted(stated)} — the artifact contradicts "
        "itself, so there is no single value to pin the page to"
    )
    return ast.literal_eval(stated.pop())


# --- Derived renderings ----------------------------------------------------------
# The page abbreviates or rounds some live values. Each rendering below is applied to the
# LIVE artifact value only; the page's printed text is compared as printed, never itself
# rounded or truncated, so a page printing `8.3472e-09` or `8.3e-09` still fails.

_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def short_commit(commit: object) -> str:
    """A full 40-hex tMAVEN commit as the page abbreviates it: its first 8 hex.

    The truncation is the page's typography, not the pin's tolerance — the full value is
    pinned exactly in (b)'s live-pin paragraph. Refuses anything but a full commit, so a
    pointer that came to hold an abbreviation cannot silently compare prefix-to-prefix.
    """
    assert isinstance(commit, str), f"{commit!r} is not a commit string"
    assert _FULL_COMMIT_RE.match(commit), f"{commit!r} is not a full 40-hex commit"
    return commit[:8]


def sci3(value: object) -> str:
    """The page's rounding rule: three significant figures in scientific notation.

    ``format(value, ".2e")`` — so ``8.34719832143449e-09`` renders ``8.35e-09`` and
    ``1.0024658100238614e-09`` renders ``1.00e-09``. ``.3g`` is not the same rule: it
    drops the trailing zero and yields ``1e-09``.
    """
    assert isinstance(value, float), f"{value!r} is not a float"
    return format(value, ".2e")


def shortest(value: object) -> str:
    """The shortest string that round-trips to ``value`` — ``repr``, so ``1.0`` stays ``1.0``.

    ``f"{value:g}"`` would print ``1``, which is not what the page writes.
    """
    assert isinstance(value, float), f"{value!r} is not a float"
    text = repr(value)
    assert float(text) == value
    return text


def _common_value(*pointers: str) -> object:
    """The one value every pointer holds — for a page clause like "1.0 on both floors".

    If the fields ever diverge there is no single value the clause can state, so this
    fails rather than pinning the page to whichever pointer came first.
    """
    values = {pointer: at(PARITY, pointer) for pointer in pointers}
    assert len(set(values.values())) == 1, (
        f"{values} differ, so there is no single value the page's shared clause can state"
    )
    return values[pointers[0]]


#: The ``(… fail; … pass)`` illustration in (b)'s ship-floor bullet.
_STRADDLE_RE = re.compile(r"\(([^;()]*) fail; ([^;()]*) pass\)")


def straddle_violations(text: str, floor: float) -> list[str]:
    """Literals in the ``(… fail; … pass)`` clause that are on the wrong side of ``floor``.

    The sentence says the default state-count floor *fails* the per-method ordering test
    once it is dropped to ebFRET's floor or below, and *passes* above it, then illustrates
    with two literals each side. Transcribing those would only check the page against
    itself; this checks the claim: every fail-side literal must be ``<= floor`` and every
    pass-side literal ``> floor``. ``floor`` is a parameter so a re-freeze that moved it
    past the illustration is testable without editing the artifact.
    """
    clauses = _STRADDLE_RE.findall(_flat(text))
    assert len(clauses) == 1, f"expected one '(… fail; … pass)' clause, found {len(clauses)}"
    fail_side, pass_side = ([float(n) for n in _NUMBER_RE.findall(side)] for side in clauses[0])
    assert fail_side and pass_side, f"the clause lost one of its sides: {clauses[0]}"
    return [f"{x} fails but is above {floor}" for x in fail_side if not x <= floor] + [
        f"{x} passes but is not above {floor}" for x in pass_side if not x > floor
    ]


#: Where the restatement pins sit. Each one's ``source`` names its location as well as
#: its pointer: the mutation tests collect every pin sharing a ``source`` and require one
#: edited page to break all of them, which two different page locations never do.
_AT_BUILD_TABLE = " @ (b) build table"
_AT_LIVE_PIN = " @ (b) live-pin paragraph"
_AT_GAP_QUOTE = " @ (b) gap blockquote"
_AT_SCOPE = " @ Scope third bullet"
_SCOPE_SECTION = "Scope and known limitations"
#: The paragraph naming the pin every live sidecar assertion uses.
_LIVE_PIN_ANCHOR = ".github/workflows/sidecar.yml"
#: (b)'s "Gap, stated plainly" blockquote that restates `$.pooled_worst`. §(b) has two
#: blockquotes with that lead-in, so the anchor carries enough of it to name this one.
#: Its code spans name script flags and paths rather than its subject, so this uses the
#: lead-in fallback.
_GAP_QUOTE_ANCHOR = "> Gap, stated plainly. The **default**"
#: The bullet list under "What the suite does still enforce against loosening".
_SHIP_FLOOR_ANCHOR = "$.tolerance.state_count_min_fraction"
#: The paragraph after it. Its only code spans are the value it restates and
#: `$.freeze_policy`, which an earlier paragraph also names, so it is selected by lead-in.
_WIDENING_ANCHOR = "Widening any of the eight bounds"
#: §Scope's third bullet carries no code span but the two hashes it restates, so the pins
#: select the Validation-coverage list through another bullet's pointer, and
#: :func:`test_the_scope_anchor_still_selects_the_third_bullet` asserts the third bullet
#: is still inside what that selects.
_SCOPE_ANCHOR = "$.tolerance"
_SCOPE_BULLET_LEAD_IN = "- The frozen **default** parity tolerance for (b)"
_M05_COLUMN = "`$.method` (M0.5, vbconhmm)"
_EBHMM_COLUMN = "`$.measured_by_method.ebhmm.method`"
_M05_COMMIT = "$.method.tmaven_commit"
_EBHMM_COMMIT = "$.measured_by_method.ebhmm.method.tmaven_commit"
_M05_PYTHON = "$.method.sidecar_python_version"
_EBHMM_PYTHON = "$.measured_by_method.ebhmm.method.sidecar_python_version"
_EBHMM_FLOOR = "$.tolerance_by_method.ebhmm.state_count_min_fraction"


def commit_cell(column: str, pointer: str, commit: object) -> Cell:
    """A build-table ``tmaven_commit`` cell, which the page prints as 8 hex plus ``…``.

    Built by one factory so the registry and the hypothetical-value test construct the
    same pin, and a derivation bug cannot hide in either copy.
    """
    return Cell(
        _PARITY_SECTION,
        _M05_COLUMN,
        "tmaven_commit",
        column,
        f"{pointer}[:8]…{_AT_BUILD_TABLE}",
        short_commit(commit) + "…",
    )


def _restatement_pins() -> list[Pin]:
    """Second and later statements of an artifact value, each at its own location (#435)."""
    pins: list[Pin] = []

    # (b) build table -- both commits, abbreviated the way the page abbreviates them.
    pins += [
        commit_cell(_M05_COLUMN, _M05_COMMIT, at(PARITY, _M05_COMMIT)),
        commit_cell(_EBHMM_COLUMN, _EBHMM_COMMIT, at(PARITY, _EBHMM_COMMIT)),
    ]

    # (b) "The pin every live assertion uses is ..." -- the full commit, exactly, and both
    # copies of its interpreter version.
    pins += [
        Quote(
            _PARITY_SECTION,
            _LIVE_PIN_ANCHOR,
            "live assertion uses is `{}`",
            f"{_EBHMM_COMMIT}{_AT_LIVE_PIN}",
            at(PARITY, _EBHMM_COMMIT),
        ),
        Quote(
            _PARITY_SECTION,
            _LIVE_PIN_ANCHOR,
            "on CPython {} — `TMAVEN_SPEC`",
            f"{_EBHMM_PYTHON}{_AT_LIVE_PIN}, CPython clause",
            at(PARITY, _EBHMM_PYTHON),
        ),
        Quote(
            _PARITY_SECTION,
            _LIVE_PIN_ANCHOR,
            "resolving to {} on all four platforms",
            f"{_EBHMM_PYTHON}{_AT_LIVE_PIN}, lock clause",
            at(PARITY, _EBHMM_PYTHON),
        ),
    ]

    # (b) "Gap, stated plainly" blockquote. The two pooled-worst figures are ROUNDED on the
    # page, so they are rendered with `sci3` -- three significant figures in scientific
    # notation, format(value, ".2e") -- applied to the live value only. The floors clause
    # and both ceilings are NOT rounded, so they render shortest-round-trip: `:g` would
    # keep only six significant digits and pass a stale page past a seventh-digit change.
    floors = (
        "$.pooled_worst.state_count_fraction",
        "$.pooled_worst.viterbi_agreement",
    )
    pins += [
        Quote(
            _PARITY_SECTION,
            _GAP_QUOTE_ANCHOR,
            template,
            f"{source}{_AT_GAP_QUOTE}",
            value,
            render=render,
        )
        for template, source, value, render in (
            (
                "measured against tMAVEN `{}`",
                f"{_M05_COMMIT}[:8]",
                at(PARITY, _M05_COMMIT),
                short_commit,
            ),
            (
                "the pinned `{}`",
                f"{_EBHMM_COMMIT}[:8]",
                at(PARITY, _EBHMM_COMMIT),
                short_commit,
            ),
            ("on CPython {}. That is", _M05_PYTHON, at(PARITY, _M05_PYTHON), None),
            (
                "are {} on both floors",
                " == ".join(floors),
                _common_value(*floors),
                shortest,
            ),
            (
                "both floors and {} / ",
                "$.pooled_worst.state_mean_abs_delta, 3 s.f.",
                at(PARITY, "$.pooled_worst.state_mean_abs_delta"),
                sci3,
            ),
            (
                " / {} against ceilings",
                "$.pooled_worst.relative_elbo, 3 s.f.",
                at(PARITY, "$.pooled_worst.relative_elbo"),
                sci3,
            ),
            (
                "against ceilings of {} and ",
                "$.tolerance.state_mean_abs_delta_max",
                at(PARITY, "$.tolerance.state_mean_abs_delta_max"),
                shortest,
            ),
            (
                " and {}, and the freeze",
                "$.tolerance.relative_elbo_max",
                at(PARITY, "$.tolerance.relative_elbo_max"),
                shortest,
            ),
            # The closing backtick is inside the template: the value ends a code span, and
            # a template ending in its placeholder would capture a single character.
            (
                "--n-runs {}`",
                "$.method.n_runs_per_fixture",
                at(PARITY, "$.method.n_runs_per_fixture"),
                None,
            ),
        )
    ]

    # (b) the two prose copies of ebFRET's state-count floor, rendered shortest-round-trip
    # like the ceilings above so a change past the sixth significant digit still fails.
    # The third copy -- the first fail-side literal of "(0.5249 and 0.40 fail; 0.5250 and
    # 0.60 pass)" -- is checked structurally by `straddle_violations`, which asserts `<=`
    # rather than equality.
    floor = at(PARITY, _EBHMM_FLOOR)
    pins += [
        Quote(
            _PARITY_SECTION,
            _SHIP_FLOOR_ANCHOR,
            "frozen `{}` or below",
            f"{_EBHMM_FLOOR} @ (b) ship-floor bullet",
            floor,
            render=shortest,
        ),
        Quote(
            _PARITY_SECTION,
            _WIDENING_ANCHOR,
            "stays above `{}`",
            f"{_EBHMM_FLOOR} @ (b) widening paragraph",
            floor,
            render=shortest,
        ),
    ]

    # Scope and known limitations, third bullet -- both commits and both interpreters.
    pins += [
        Quote(_SCOPE_SECTION, _SCOPE_ANCHOR, template, f"{source}{_AT_SCOPE}", value, render=render)
        for template, source, value, render in (
            (
                "measured against tMAVEN `{}`",
                f"{_M05_COMMIT}[:8]",
                at(PARITY, _M05_COMMIT),
                short_commit,
            ),
            (
                "behind the pinned `{}`",
                f"{_EBHMM_COMMIT}[:8]",
                at(PARITY, _EBHMM_COMMIT),
                short_commit,
            ),
            ("on CPython {} — 13 upstream", _M05_PYTHON, at(PARITY, _M05_PYTHON), None),
            ("on CPython {} that CI", _EBHMM_PYTHON, at(PARITY, _EBHMM_PYTHON), None),
        )
    ]
    return pins


def _registry() -> list[Pin]:
    pins: list[Pin] = []

    # (a) — the M1 extraction gates, module constants rather than a JSON artifact.
    pins += [
        Cell("(a) Extraction vs Deep-LASI", "Role", name, "Value", f"oracle.{name}", value)
        for name, value in (
            (n, module_constant("tether.project.oracle", n))
            for n in ("RECALL_THRESHOLD", "MATCH_TOL_PX", "PEARSON_THRESHOLD", "RMS_THRESHOLD_PX")
        )
    ]

    # (b) — both frozen tolerance blocks, side by side in one table.
    for bound in _BOUNDS:
        pins.append(
            Cell(
                _PARITY_SECTION,
                "Bound",
                bound,
                "`$.tolerance` (default)",
                f"$.tolerance.{bound}",
                at(PARITY, f"$.tolerance.{bound}"),
            )
        )
        pins.append(
            Cell(
                _PARITY_SECTION,
                "Bound",
                bound,
                "`$.tolerance_by_method.ebhmm`",
                f"$.tolerance_by_method.ebhmm.{bound}",
                at(PARITY, f"$.tolerance_by_method.ebhmm.{bound}"),
            )
        )

    # (b) — the vbconhmm pooled-worst block, printed at full precision.
    pins += [
        Cell(
            _PARITY_SECTION,
            "Pooled worst case",
            metric,
            "Pooled worst case",
            f"$.pooled_worst.{metric}",
            at(PARITY, f"$.pooled_worst.{metric}"),
        )
        for metric in _METRICS
    ]

    # (b) — the ebFRET pooled-worst block, printed in a sentence instead of a table.
    ebhmm = "$.measured_by_method.ebhmm"
    pins += [
        Prose(
            _PARITY_SECTION,
            ebhmm,
            metric,
            f"{ebhmm}.pooled_worst.{metric}",
            at(PARITY, f"{ebhmm}.pooled_worst.{metric}"),
        )
        for metric in _METRICS
    ]
    pins.append(
        Prose(
            _PARITY_SECTION,
            ebhmm,
            "measured_utc",
            f"{ebhmm}.measured_utc",
            at(PARITY, f"{ebhmm}.measured_utc"),
        )
    )

    # (b) — how much measurement stands behind those blocks. These are machine-readable
    # fields, so leaving them neither pinned nor excluded would let a re-measure with a
    # different run count keep the page's "20 fits / 39 comparisons" sentence green.
    vbconhmm_spread = at(PARITY, "$.spread_by_fixture")
    assert isinstance(vbconhmm_spread, dict)
    pins += [
        Quote(
            _PARITY_SECTION,
            "**Measured result.**",
            "{} self-reseeded fits per fixture",
            "$.method.n_runs_per_fixture",
            at(PARITY, "$.method.n_runs_per_fixture"),
        ),
        Quote(
            _PARITY_SECTION,
            "**Measured result.**",
            "{} recorded comparisons pooled",
            "sum of $.spread_by_fixture.*.n_comparisons",
            sum(fixture["n_comparisons"] for fixture in vbconhmm_spread.values()),
        ),
        Quote(
            _PARITY_SECTION,
            ebhmm,
            "{} cross-seed comparisons",
            f"{ebhmm}.spread_by_fixture.smd_281mol.n_comparisons",
            at(PARITY, f"{ebhmm}.spread_by_fixture.smd_281mol.n_comparisons"),
        ),
    ]

    # (b) — the artifact's own provenance, both as prose and in the build table.
    pins += [
        Prose(
            _PARITY_SECTION,
            "$.tolerance_by_method.ebhmm",
            key,
            f"$.{key}",
            at(PARITY, f"$.{key}"),
        )
        for key in ("schema_version", "frozen_at_milestone", "measured_utc")
    ]
    # The build table sets the two blocks side by side. Note the asymmetry it encodes:
    # the M0.5 date is the artifact's *top-level* `measured_utc` (the vbconhmm block has
    # none of its own), while the ebFRET one hangs off `measured_by_method`.
    m05_column = _M05_COLUMN
    ebhmm_column = _EBHMM_COLUMN
    pins += [
        Cell(_PARITY_SECTION, m05_column, row, column, source, value)
        for row, column, source, value in (
            ("measured_utc", m05_column, "$.measured_utc", PARITY["measured_utc"]),
            (
                "measured_utc",
                ebhmm_column,
                f"{ebhmm}.measured_utc",
                at(PARITY, f"{ebhmm}.measured_utc"),
            ),
            (
                "sidecar_python_version",
                m05_column,
                "$.method.sidecar_python_version",
                at(PARITY, "$.method.sidecar_python_version"),
            ),
            (
                "sidecar_python_version",
                ebhmm_column,
                f"{ebhmm}.method.sidecar_python_version",
                at(PARITY, f"{ebhmm}.method.sidecar_python_version"),
            ),
        )
    ]

    # (d) and (e) and (g) — the remaining module constants the page prints a value for.
    pins.append(
        Prose(
            "(d) Ranker held-out cross-validation",
            "DEFAULT_SHIP_BAR_PTS",
            "DEFAULT_SHIP_BAR_PTS",
            "ml.prequential.DEFAULT_SHIP_BAR_PTS",
            module_constant("tether.ml.prequential", "DEFAULT_SHIP_BAR_PTS"),
        )
    )
    pins += [
        Cell(
            "(e) α / γ estimator edge cases",
            "Module",
            name,
            "Value",
            f"{module.rsplit('.', 1)[-1]}.{name}",
            module_constant(module, name),
        )
        for module, name in (
            ("tether.fret.leakage", "LEAKAGE_CEILING"),
            ("tether.fret.leakage", "DEFAULT_MIN_WINDOW_FRAMES"),
            ("tether.fret.leakage", "DEFAULT_MIN_QUALIFYING_TRACES"),
            ("tether.fret.gamma", "GAMMA_CEILING"),
            ("tether.fret.gamma", "DEFAULT_GAMMA_HALF_WINDOW"),
        )
    ]
    pins += [
        Prose(
            "(g) Photobleaching detector",
            "PB_PRIOR_MU",
            name,
            f"photobleach.{name}",
            module_constant("tether.fret.photobleach", name),
        )
        for name in ("PB_PRIOR_A", "PB_PRIOR_B", "PB_PRIOR_BETA", "PB_PRIOR_MU")
    ]

    # External benchmark — the kinSoft artifact's own provenance.
    pins += [
        Prose(_KINSOFT_SECTION, "schema/kinsoft_reference.json", key, f"$.{key}", KINSOFT[key])
        for key in ("schema_version", "frozen_at_milestone", "measured_utc")
    ]
    # Each citation carries the words that identify *which* field it is. A bare `{}` would
    # let the paper doi and the data doi swap places on the page and still pass, because
    # both values would still be somewhere in the same paragraph.
    pins += [
        Quote(_KINSOFT_SECTION, "schema/kinsoft_reference.json", template, f"$.source.{key}", value)
        for key, template, value in (
            ("doi", "Paper doi `{}`", at(KINSOFT, "$.source.doi")),
            ("data_doi", "data doi `{}`", at(KINSOFT, "$.source.data_doi")),
            # The licence follows the data DOI's closing backtick. A bare "({})" would
            # also fire on the "(2022)" of the citation two clauses earlier, so the
            # template keeps just enough context to name the one place it belongs.
            ("license", "` ({})", at(KINSOFT, "$.source.license")),
        )
    ]

    # External benchmark — the level-1 acquisition parameters.
    level1 = "$.levels.level1"
    pins.append(
        Prose(
            _KINSOFT_SECTION,
            level1,
            level1,
            f"{level1}.n_traces",
            at(KINSOFT, f"{level1}.n_traces"),
        )
    )
    pins += [
        Prose(_KINSOFT_SECTION, level1, key, f"{level1}.{key}", at(KINSOFT, f"{level1}.{key}"))
        for key in ("dt_s", "sampling_rate_hz", "snr_estimate")
    ]

    # External benchmark — the 2-state ground truth / Tether / band matrix.
    for rate in ("k12_low_high", "k21_high_low"):
        pins.append(
            Cell(
                _KINSOFT_SECTION,
                "Advisory band",
                rate,
                "Ground truth (s⁻¹)",
                f"{level1}.ground_truth.rates_s_inv.{rate}",
                at(KINSOFT, f"{level1}.ground_truth.rates_s_inv.{rate}"),
            )
        )
        pins.append(
            Cell(
                _KINSOFT_SECTION,
                "Advisory band",
                rate,
                "Tether (s⁻¹)",
                f"{level1}.tether_measured.{rate}",
                at(KINSOFT, f"{level1}.tether_measured.{rate}"),
            )
        )
        pins.append(
            Cell(
                _KINSOFT_SECTION,
                "Advisory band",
                rate,
                "Relative deviation",
                f"{level1}.tether_measured.{rate.split('_')[0]}_rel_dev",
                at(KINSOFT, f"{level1}.tether_measured.{rate.split('_')[0]}_rel_dev"),
            )
        )
        pins.append(
            Cell(
                _KINSOFT_SECTION,
                "Advisory band",
                rate,
                "Advisory band",
                "$.band.rate_rel_deviation_max",
                at(KINSOFT, "$.band.rate_rel_deviation_max"),
            )
        )
    band = at(KINSOFT, "$.band.rate_rel_deviation_max")
    spread = f"{level1}.reported_inter_tool_spread"
    pins.append(
        Prose(
            _KINSOFT_SECTION,
            "$.band.rate_rel_deviation_max",
            "$.band.rate_rel_deviation_max",
            "$.band.rate_rel_deviation_max",
            band,
        )
    )
    pins.append(
        Quote(
            _KINSOFT_SECTION,
            "$.band.rate_rel_deviation_max",
            "({} % average)",
            f"{spread}.rate_mean_rel_dev_from_gt",
            at(KINSOFT, f"{spread}.rate_mean_rel_dev_from_gt"),
            scale=100,
        )
    )
    pins.append(
        Quote(
            _KINSOFT_SECTION,
            "$.band.rate_rel_deviation_max",
            "≥ {} % (1σ)",
            f"{spread}.finite_size_uncertainty_1sigma",
            at(KINSOFT, f"{spread}.finite_size_uncertainty_1sigma"),
            scale=100,
        )
    )

    # External benchmark — the deferred levels and level 3's full ground-truth matrix.
    for level in ("level2", "level3"):
        pins.append(
            Cell(
                _KINSOFT_SECTION,
                "Why deferred (`$.levels.*.deferred_reason`)",
                level,
                "States",
                f"$.levels.{level}.nstates",
                at(KINSOFT, f"$.levels.{level}.nstates"),
            )
        )
        pins.append(
            Cell(
                _KINSOFT_SECTION,
                "Why deferred (`$.levels.*.deferred_reason`)",
                level,
                "Traces",
                f"$.levels.{level}.n_traces",
                at(KINSOFT, f"$.levels.{level}.n_traces"),
            )
        )
    rates3 = _LEVEL3_RATES_POINTER
    pins += [
        Prose(_KINSOFT_SECTION, rates3, rate, f"{rates3}.{rate}", at(KINSOFT, f"{rates3}.{rate}"))
        for rate in _LEVEL3_RATES
    ]
    note = "$.levels.level3.ground_truth.note"
    pins += [
        Quote(
            _KINSOFT_SECTION, rates3, f"`{name}` {{}} s⁻¹", f"{note} {name}", _blinking_rate(name)
        )
        for name in ("kbright", "kdark")
    ]

    pins += _restatement_pins()
    return pins


REGISTRY = _registry()


# --- What is deliberately left unpinned ----------------------------------------

#: Figures the page prints that this module deliberately leaves unpinned. Most are held
#: by no committed artifact, so a pin would assert nothing (or that two transcriptions of
#: the same prose agree); the rest say exactly why the artifact that does hold them is not
#: read. Recorded rather than dropped: on a validation page an unexplained omission and an
#: oversight look identical, and #214 asks for the reason in the test.
EXCLUSIONS: dict[str, str] = {
    "the tMAVEN upstream commit dates 2025-05-06 / 2025-10-05": (
        "When each commit was made upstream is a fact of the tMAVEN repository that no "
        "committed artifact records. The commits themselves are recorded, under "
        "`$.method.tmaven_commit` and `$.measured_by_method.ebhmm.method.tmaven_commit`, "
        "and are pinned wherever the page prints them (#435): the build table, (b)'s "
        "live-pin paragraph and gap blockquote, and the third Scope bullet."
    ),
    "'13 upstream commits behind' and the 256 / 42 changed-line counts": (
        "Derived by diffing two upstream tMAVEN commits. Nothing in this repository "
        "records them, and re-deriving them in a test would need network access."
    ),
    "the weekly large-fixtures run of 2026-07-20 and its '7 passed, 4 skipped' summary": (
        "A historical GitHub Actions run. It is evidence that the gated assertion skipped "
        "on that date, not a value any artifact carries."
    ),
    "sidecar-measure dispatch run 28963324581": (
        "A historical GitHub Actions run id. It appears in the artifact only inside the "
        "free-text `build_provenance` narrative, not as a machine-readable field."
    ),
    "the ADR-0022 extraction numbers (0.928, 0.984, 0.982, 0.854, 0.34, ~0.66, ~0.9 GB)": (
        "Measured on a local run on the maintainer's workstation and recorded in "
        "docs/adr/0022 as prose. There is no machine-readable artifact, so a pin would "
        "only assert that two transcriptions of the same paragraph agree."
    ),
    "(a)'s enforcing-tests sentence: 'RMS <= 0.5 px', '>= 95 %' and 'within 1 px'": (
        "All three are the hard-coded literals of the test that sentence describes, "
        "tests/test_register.py::test_native_fit_reproduces_tmap_within_tolerance "
        "(`point_rms(...) <= 0.5` and `np.mean(agreement <= 1.0) >= 0.95`), whose "
        "docstring says they measure registration faithfulness, not the colocalization-"
        "recall criterion. No artifact or module constant is their source, so there is "
        "nothing to pin them to. Two coincide numerically with M1 extraction-oracle "
        "constants -- oracle.RMS_THRESHOLD_PX is 0.5 and oracle.RECALL_THRESHOLD is 0.95 "
        "-- by accident; both are already pinned where they belong, to (a)'s Frozen "
        "tolerance table, and binding this sentence to them would attribute a "
        "registration gate to the extraction oracle. Nothing holds 'within 1 px' at all "
        "(oracle.MATCH_TOL_PX is 2.0)."
    ),
    "'≈1e-9' in the third Scope bullet": (
        "An order-of-magnitude summary of the two non-unit `$.pooled_worst` metrics, not "
        "a transcription of either. Both are pinned at full precision in (b)'s table and "
        "at three significant figures in (b)'s gap blockquote; an approximate sign has no "
        "exact rendering to compare against."
    ),
    "(b) build table, 'How the values were obtained' row: `python 3.12.13`, `@10f4230`, "
    "2026-06-26": (
        "Quoted fragments of two free-text fields: the ebFRET cell quotes "
        "`$.measured_by_method.ebhmm.method.build_provenance`'s 'python 3.12.13 "
        "hd63d673_0_cpython conda-forge' and its resolved '...tmaven.git@10f4230' (7 hex, "
        "so no commit[:8] derivation covers it), and the M0.5 cell quotes the date out of "
        "`$.method.build_provenance`. Both fields hold those strings verbatim, so this is "
        "not a missing source; it is mechanically unreachable here. `_states` requires a "
        "cell's whole code-span-or-quoted token set to EQUAL one live value, and the "
        "ebFRET cell carries five code spans while the M0.5 cell quotes one phrase lifted "
        "out of a paragraph-long field; `_prose_paragraphs` drops table blocks, so no "
        "Quote can reach a table cell either. Pinning a fragment inside a cell is "
        "comparator work, out of #435's scope."
    ),
    "the bare `model_281mol.hdf5` (and `smd_281mol`) in (b)'s gap blockquote": (
        "`model_281mol.hdf5` is the basename of `$.spread_by_fixture.smd_281mol.reference`, "
        "and no pin reads that field anywhere on the page -- (b)'s Reference-data and "
        "enforcing-tests paragraphs name the same file unpinned. Covering it would be a "
        "page-wide coverage extension, not a restatement of a pinned value."
    ),
    "the 'M0.5' of 'the M0.5 worst cases' in (b)'s gap blockquote": (
        "`$.frozen_at_milestone`, already pinned as a Prose entry in (b)'s Frozen-tolerance "
        "paragraph. Here it is a label naming which block the sentence means."
    ),
    'the script-output claims in (b)\'s gap blockquote: `["vbconhmm"]`, `"ebFRET (M6)"`, '
    "'exactly ten top-level keys' and its key list, 'all three sub-keys of `$.coverage`'": (
        "They describe what the literal `out = {...}` dict at the end of `main()` in "
        "scripts/measure_parity.py would write -- the authority the paragraph itself names "
        "-- not the committed artifact. Verified: 'ebFRET (M6)' appears nowhere in "
        "schema/parity_tolerance.json, whose `$.coverage.applied_to` is "
        "['vbFRET (per-trace, M2)', 'consensus VB-HMM (M6)']. The committed value they are "
        'contrasted with, `"vbconhmm (vb Consensus HMM)"`, IS pinned, as a verbatim quote.'
    ),
    "'`applied_to` per-trace vbFRET' in (b)'s gap blockquote": (
        "A paraphrase of `$.coverage.applied_to`'s 'vbFRET (per-trace, M2)', not a "
        "quotation of it, so there is no literal to compare."
    ),
    "the paper's '14 published analyses', '17 %' and '9-14 %' figures": (
        "Facts of Gotz et al. 2022. kinsoft_reference.json records them only inside the "
        "free-text `spread_source` and `deferred_reason` strings, which the page "
        "paraphrases rather than quotes, so there is no literal to pin against."
    ),
}


# --- The guard ------------------------------------------------------------------


def test_page_exists_and_still_has_its_sections() -> None:
    sections = _sections(PAGE_TEXT)
    for name in ("(a) Extraction vs Deep-LASI", _PARITY_SECTION, _KINSOFT_SECTION):
        assert name in sections, f"docs/validation.md lost its '{name}' section"


@pytest.mark.parametrize("pin", REGISTRY, ids=[p.source + "@" + p.label for p in REGISTRY])
def test_documented_value_matches_its_source_artifact(pin: Pin) -> None:
    assert satisfied(PAGE_TEXT, pin), (
        f"docs/validation.md no longer states {pin.value!r} for {pin.source} "
        f"at {pin.label!r} in '{pin.section}'. The artifact is the authority: update the "
        f"page in the same pull request that moved the value."
    )


def test_every_frozen_bound_in_both_blocks_is_pinned() -> None:
    """A bound added to either tolerance block may not go undocumented."""
    for pointer in ("$.tolerance", "$.tolerance_by_method.ebhmm"):
        block = at(PARITY, pointer)
        assert isinstance(block, dict)
        pinned = {p.source for p in REGISTRY}
        missing = sorted(k for k in block if f"{pointer}.{k}" not in pinned)
        assert not missing, f"{pointer} carries unpinned bounds {missing}"


def test_every_pooled_worst_metric_in_both_blocks_is_pinned() -> None:
    pinned = {p.source for p in REGISTRY}
    for pointer in ("$.pooled_worst", "$.measured_by_method.ebhmm.pooled_worst"):
        block = at(PARITY, pointer)
        assert isinstance(block, dict)
        missing = sorted(k for k in block if f"{pointer}.{k}" not in pinned)
        assert not missing, f"{pointer} carries unpinned metrics {missing}"


def test_every_kinsoft_ground_truth_rate_is_pinned() -> None:
    pinned = {p.source for p in REGISTRY}
    for pointer in (
        "$.levels.level1.ground_truth.rates_s_inv",
        "$.levels.level3.ground_truth.rates_s_inv",
    ):
        block = at(KINSOFT, pointer)
        assert isinstance(block, dict)
        missing = sorted(k for k in block if f"{pointer}.{k}" not in pinned)
        assert not missing, f"{pointer} carries unpinned rates {missing}"


def test_every_constant_the_page_states_a_value_for_is_pinned() -> None:
    """The registry may not lag the page.

    Any ``SCREAMING_CASE`` constant the page writes *in a code span* and prints a number
    beside has to have an entry, so a new frozen constant added to the page cannot drift
    unguarded. Table rows are joined before the scan because a constant sits in the
    ``Constant`` cell and its value in the next one.

    The code-span requirement is what separates a constant from an acronym: ``PRD §11.2``,
    ``PEP 610`` and ``RMS ≤ 0.5 px`` all read as a capitalised name followed by a number,
    and none of them is a symbol anything could be pinned to.
    """
    pinned = {p.key for p in REGISTRY if isinstance(p, Prose)}
    pinned |= {p.row for p in REGISTRY if isinstance(p, Cell)}
    # The leading *identifier* of each code span, so `DEFAULT_SHIP_BAR_PTS = 10.0` counts
    # and so does `NEW_LIMIT=5`, which splitting on whitespace would have left as the
    # unmatchable token "NEW_LIMIT=5" while `_owned_literals` called it "NEW_LIMIT".
    code_spans = {
        match.group()
        for span in _BACKTICKED_RE.findall(PAGE_TEXT)
        if (match := _LEADING_IDENT_RE.match(span.strip()))
    }
    regions: list[str] = []
    for body in _sections(PAGE_TEXT).values():
        regions += _prose_paragraphs(body)
        regions += [" ".join(row) for table in _tables(body) for row in table.rows]
    documented = {
        name
        for region in regions
        for name, values in _owned_literals(region).items()
        if values and _CONSTANT_RE.match(name) and name in code_spans
    }
    missing = sorted(documented - pinned)
    assert not missing, (
        "docs/validation.md prints a value for these constants but the registry does not "
        f"pin them to the code: {missing}"
    )


#: Claims the page presents as *quotations* of an artifact, as
#: ``(fragment, artifact, pointer, section, anchor)``. Each is checked in **both**
#: directions. A one-sided check — the fragment is still in the artifact — passes a page
#: that stopped quoting it, or quotes its opposite; the page saying `$.description`
#: "never gates main" is only true while *both* halves hold.
_VERBATIM_QUOTES = (
    ("never gates main", KINSOFT, "$.description", _KINSOFT_SECTION, "$.description"),
    (
        "vbconhmm (vb Consensus HMM)",
        PARITY,
        "$.coverage.measured_methods",
        _PARITY_SECTION,
        "$.coverage.measured_methods",
    ),
    # The same quotation restated in (b)'s gap blockquote ("the committed `"vbconhmm (vb
    # Consensus HMM)"` quoted below"). Not a Quote pin: the fragment contains spaces, and
    # a Quote placeholder captures one run of non-space characters.
    (
        "vbconhmm (vb Consensus HMM)",
        PARITY,
        "$.coverage.measured_methods",
        _PARITY_SECTION,
        _GAP_QUOTE_ANCHOR,
    ),
)


@pytest.mark.parametrize(
    ("fragment", "artifact", "pointer", "section", "anchor"),
    _VERBATIM_QUOTES,
    ids=[
        pointer if anchor == pointer else f"{pointer}@{anchor}"
        for _, _, pointer, _, anchor in _VERBATIM_QUOTES
    ],
)
def test_the_page_quotes_the_artifact_verbatim(
    fragment: str, artifact: dict, pointer: str, section: str, anchor: str
) -> None:
    """A quotation has two sides, and the page owns one of them."""
    in_artifact, on_page = quotation_sides(
        fragment, artifact, pointer, paragraph(PAGE_TEXT, section, anchor)
    )
    assert in_artifact, (
        f"docs/validation.md presents {fragment!r} as a verbatim quote of {pointer}, "
        "but the artifact no longer says it"
    )
    assert on_page, (
        f"{pointer} still says {fragment!r} but docs/validation.md no longer quotes it "
        "where it claims to"
    )


def test_the_deferred_matrix_still_says_every_unlisted_rate_is_zero() -> None:
    """The clause that makes six numbers a 4x4 matrix, on both sides.

    ``rates_s_inv`` lists only the six non-zero transitions; *"all other off-diagonal
    rates zero"* is what completes the accepted ground truth. Pinning the six and not the
    clause would let the page say the unlisted transitions are 0.01 with every other
    guard green — and the deferred level-3 matrix is exactly what a future dwell-CDF
    oracle would be built against.
    """
    page = _flat(paragraph(PAGE_TEXT, _KINSOFT_SECTION, _LEVEL3_RATES_POINTER))
    note = str(at(KINSOFT, _LEVEL3_NOTE_POINTER))
    for label, text in (("the artifact note", note), ("docs/validation.md", page)):
        zero, nonzero = zero_clauses(text)
        assert zero or nonzero, f"{label} no longer states what the unlisted off-diagonal rates are"
        assert not nonzero, f"{label} says the unlisted off-diagonal rates are {nonzero}, not zero"
    listed = at(KINSOFT, _LEVEL3_RATES_POINTER)
    assert isinstance(listed, dict)
    assert set(listed) == set(_LEVEL3_RATES), (
        "the artifact records a different rate set than this module enumerates; re-read "
        "the level-3 ground truth before pinning it"
    )
    # And the same set has to be what the PAGE prints. Reading only the artifact here
    # would let the page add `k13 0.01` beside the six correct rates while still saying
    # every other transition is zero -- a contradiction with itself, not with the JSON.
    displayed = {
        name
        for name, values in _owned_literals(
            paragraph(PAGE_TEXT, _KINSOFT_SECTION, _LEVEL3_RATES_POINTER)
        ).items()
        if _TRANSITION_RE.match(name) and values
    }
    assert displayed == set(listed), (
        f"docs/validation.md enumerates transitions {sorted(displayed)} but the artifact "
        f'records {sorted(listed)}, so its "all other ... zero" clause no longer covers '
        "what it claims to"
    )


def test_only_one_method_was_measured_as_the_page_says() -> None:
    """The page says `$.coverage.measured_methods` records *only* that one method.

    The verbatim check above proves the string is present on both sides; "only" is the
    separate claim that nothing joined it, which a second measured method would falsify
    while every quotation still matched.
    """
    sole = next(q[0] for q in _VERBATIM_QUOTES if q[2] == "$.coverage.measured_methods")
    assert at(PARITY, "$.coverage.measured_methods") == [sole]


def test_provisional_still_equals_the_frozen_tolerance() -> None:
    """The page's central mitigating claim about the M0.5 build gap.

    ``docs/validation.md`` argues the stale-build gap is tolerable because the freeze
    *confirmed* the provisional PRD §11.2 defaults rather than widening them — "`$.tolerance`
    holds exactly `$.provisional`". If that stops being true the argument is void, so it
    is asserted here rather than left as prose.
    """
    assert at(PARITY, "$.tolerance") == at(PARITY, "$.provisional")


def test_every_exclusion_records_why() -> None:
    """#214's third acceptance criterion: no silent omissions."""
    assert EXCLUSIONS, "the exclusion list may not be emptied without saying so"
    for figure, reason in EXCLUSIONS.items():
        assert reason.strip(), f"exclusion {figure!r} carries no reason"


# --- The guard would notice ----------------------------------------------------
# Everything above asserts what the page says today. These assert that a drifted value
# would actually FAIL, which is the whole of #214 -- a pin that cannot fail is prose.


def _mutate(section: str, old: str, new: str, text: str | None = None) -> str:
    """Replace ``old`` with ``new`` inside one ``##`` section of ``text`` (default: the page).

    Asserts ``old`` occurs exactly once in that section, so a mutation that silently did
    nothing fails here, naming the real cause, rather than further down. ``text`` lets
    one mutation chain after another.
    """
    page = PAGE_TEXT if text is None else text
    # The same boundary :func:`_sections` uses, so a fenced `## ` line cannot cut it short.
    spans = [(start, end) for name, start, end in _section_spans(page) if name == section]
    assert len(spans) == 1, f"'{section}' is not exactly one section; found {len(spans)}"
    start, end = spans[0]
    body = page[start:end]
    assert body.count(old) == 1, f"{old!r} is not unique inside '{section}'"
    return page[:start] + body.replace(old, new) + page[end:]


@pytest.mark.parametrize(
    ("section", "old", "new", "source"),
    [
        # A tightened ebFRET state-count floor: one digit, in one table cell.
        (
            _PARITY_SECTION,
            "| `state_count_min_fraction` | floor | 0.9 | 0.5249 |",
            "| `state_count_min_fraction` | floor | 0.9 | 0.5250 |",
            "$.tolerance_by_method.ebhmm.state_count_min_fraction",
        ),
        # A pooled-worst value rounded rather than transcribed.
        (
            _PARITY_SECTION,
            "8.34719832143449e-09",
            "8.34719832143e-09",
            "$.pooled_worst.state_mean_abs_delta",
        ),
        # An ebFRET number truncated rather than transcribed. The mutation has to change
        # the *double*, not just the digits: a last-place edit inside one ULP parses back
        # to the same float, and the same float is the same number.
        (
            _PARITY_SECTION,
            "`viterbi_agreement` 0.9355949176941853",
            "`viterbi_agreement` 0.93559492",
            "$.measured_by_method.ebhmm.pooled_worst.viterbi_agreement",
        ),
        # A module constant the page prints but src/ no longer holds.
        (
            "(e) α / γ estimator edge cases",
            "| `GAMMA_CEILING` | 5.0 |",
            "| `GAMMA_CEILING` | 5.5 |",
            "gamma.GAMMA_CEILING",
        ),
        # A kinSoft ground-truth rate.
        (
            _KINSOFT_SECTION,
            "| `k21_high_low` | 0.22 |",
            "| `k21_high_low` | 0.23 |",
            "$.levels.level1.ground_truth.rates_s_inv.k21_high_low",
        ),
        # One entry of the deferred level-3 matrix.
        (
            _KINSOFT_SECTION,
            "k32 0.680",
            "k32 0.690",
            "$.levels.level3.ground_truth.rates_s_inv.k32",
        ),
        # A blinking rate, which is the one pair read out of the artifact's free text.
        (
            _KINSOFT_SECTION,
            "`kbright` 7 s⁻¹",
            "`kbright` 8 s⁻¹",
            "$.levels.level3.ground_truth.note kbright",
        ),
        # The SECOND of two statements of one constant in a single paragraph. The first
        # stays correct, so this fails only because every occurrence has to agree.
        (
            "(d) Ranker held-out cross-validation",
            "`DEFAULT_SHIP_BAR_PTS == 10.0`",
            "`DEFAULT_SHIP_BAR_PTS == 11.0`",
            "ml.prequential.DEFAULT_SHIP_BAR_PTS",
        ),
        # --- #435: restatements, one case per pinned location ------------------------
        # (b) build table, both commits, abbreviated.
        (
            _PARITY_SECTION,
            "| `71cfa1af…` (2025-05-06) |",
            "| `81cfa1af…` (2025-05-06) |",
            f"{_M05_COMMIT}[:8]…{_AT_BUILD_TABLE}",
        ),
        (
            _PARITY_SECTION,
            "| `10f4230b…` (2025-10-05) |",
            "| `20f4230b…` (2025-10-05) |",
            f"{_EBHMM_COMMIT}[:8]…{_AT_BUILD_TABLE}",
        ),
        # (b) live-pin paragraph: the full commit, and both copies of its interpreter.
        (
            _PARITY_SECTION,
            "`10f4230b6d13c6d2ad67b05d801696b4a40eff4a`",
            "`10f4230b6d13c6d2ad67b05d801696b4a40eff4b`",
            f"{_EBHMM_COMMIT}{_AT_LIVE_PIN}",
        ),
        (
            _PARITY_SECTION,
            "3.12.13 — `TMAVEN_SPEC`",
            "3.12.14 — `TMAVEN_SPEC`",
            f"{_EBHMM_PYTHON}{_AT_LIVE_PIN}, CPython clause",
        ),
        (
            _PARITY_SECTION,
            "resolving to 3.12.13",
            "resolving to 3.12.14",
            f"{_EBHMM_PYTHON}{_AT_LIVE_PIN}, lock clause",
        ),
        # (b) gap blockquote: every artifact value it restates.
        (
            _PARITY_SECTION,
            "tMAVEN `71cfa1af` (2025-05-06) on CPython 3.9.23. That is",
            "tMAVEN `81cfa1af` (2025-05-06) on CPython 3.9.23. That is",
            f"{_M05_COMMIT}[:8]{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "the pinned `10f4230b` (2025-10-05)",
            "the pinned `20f4230b` (2025-10-05)",
            f"{_EBHMM_COMMIT}[:8]{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "on CPython 3.9.23. That is",
            "on CPython 3.9.24. That is",
            f"{_M05_PYTHON}{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "are 1.0 on",
            "are 1.00 on",
            "$.pooled_worst.state_count_fraction == $.pooled_worst.viterbi_agreement"
            + _AT_GAP_QUOTE,
        ),
        (
            _PARITY_SECTION,
            "8.35e-09 / 1.00e-09",
            "8.36e-09 / 1.00e-09",
            f"$.pooled_worst.state_mean_abs_delta, 3 s.f.{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "8.35e-09 / 1.00e-09",
            "8.35e-09 / 1.01e-09",
            f"$.pooled_worst.relative_elbo, 3 s.f.{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "ceilings of 0.02 and 0.01",
            "ceilings of 0.03 and 0.01",
            f"$.tolerance.state_mean_abs_delta_max{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "ceilings of 0.02 and 0.01",
            "ceilings of 0.02 and 0.02",
            f"$.tolerance.relative_elbo_max{_AT_GAP_QUOTE}",
        ),
        (
            _PARITY_SECTION,
            "--n-runs 20",
            "--n-runs 30",
            f"$.method.n_runs_per_fixture{_AT_GAP_QUOTE}",
        ),
        # (b) the two prose copies of ebFRET's floor. A bare `0.5249` is not unique.
        (
            _PARITY_SECTION,
            "frozen `0.5249` or below",
            "frozen `0.5250` or below",
            f"{_EBHMM_FLOOR} @ (b) ship-floor bullet",
        ),
        (
            _PARITY_SECTION,
            "stays above `0.5249`",
            "stays above `0.5250`",
            f"{_EBHMM_FLOOR} @ (b) widening paragraph",
        ),
        # Scope and known limitations, third bullet.
        (
            _SCOPE_SECTION,
            "measured against tMAVEN `71cfa1af`",
            "measured against tMAVEN `81cfa1af`",
            f"{_M05_COMMIT}[:8]{_AT_SCOPE}",
        ),
        (
            _SCOPE_SECTION,
            "behind the pinned `10f4230b`",
            "behind the pinned `20f4230b`",
            f"{_EBHMM_COMMIT}[:8]{_AT_SCOPE}",
        ),
        (
            _SCOPE_SECTION,
            "on CPython 3.9.23 — 13 upstream",
            "on CPython 3.9.24 — 13 upstream",
            f"{_M05_PYTHON}{_AT_SCOPE}",
        ),
        (
            _SCOPE_SECTION,
            "on CPython 3.12.13 that CI",
            "on CPython 3.12.14 that CI",
            f"{_EBHMM_PYTHON}{_AT_SCOPE}",
        ),
    ],
)
def test_a_deliberate_one_value_mismatch_fails(
    section: str, old: str, new: str, source: str
) -> None:
    """Change exactly one number and the pin that owns it must reject the page."""
    mutated = _mutate(section, old, new)
    pins = [p for p in REGISTRY if p.source == source]
    assert pins, f"no registry entry sources {source}"
    for pin in pins:
        assert satisfied(PAGE_TEXT, pin), f"{source} does not hold on the unmutated page"
        assert not satisfied(mutated, pin), (
            f"the guard accepted a page that states the wrong value for {source} — "
            "the pin is decorative"
        )


def test_a_number_moved_to_a_neighbouring_key_fails() -> None:
    """Presence is not the test; ownership is.

    Swapping two ebFRET pooled-worst values leaves both literals on the page and both
    distinct, so a check that merely looked for the digits would stay green while the
    page told a reader ebFRET's Viterbi floor was its state-count worst case.
    """
    swapped = _mutate(
        _PARITY_SECTION,
        "`state_count_fraction` 0.6832740213523132",
        "`state_count_fraction` 0.9355949176941853",
    )
    swapped_text = _mutate(
        _PARITY_SECTION,
        "`viterbi_agreement` 0.9355949176941853",
        "`viterbi_agreement` 0.6832740213523132",
        swapped,
    )
    for metric in ("state_count_fraction", "viterbi_agreement"):
        pin = next(
            p for p in REGISTRY if p.source == f"$.measured_by_method.ebhmm.pooled_worst.{metric}"
        )
        assert satisfied(PAGE_TEXT, pin)
        assert not satisfied(swapped_text, pin), (
            f"{metric} was accepted next to another metric's value"
        )


def test_swapping_the_two_dois_fails() -> None:
    """A citation value must carry the words saying *which* field it is.

    Both DOIs stay present and distinct through the swap, so a pin that only asked
    whether each value appears somewhere in the paragraph would call the page correct
    while it attributed the Zenodo data deposit to the paper.
    """
    paper = at(KINSOFT, "$.source.doi")
    data = at(KINSOFT, "$.source.data_doi")
    swapped = PAGE_TEXT.replace(f"`{paper}`", "@@PAPER@@").replace(f"`{data}`", f"`{paper}`")
    swapped = swapped.replace("@@PAPER@@", f"`{data}`")
    assert str(paper) in swapped and str(data) in swapped  # nothing was lost, only moved
    for pin in (p for p in REGISTRY if p.source in ("$.source.doi", "$.source.data_doi")):
        assert satisfied(PAGE_TEXT, pin)
        assert not satisfied(swapped, pin), f"{pin.source} was accepted under the wrong label"


@pytest.mark.parametrize(
    ("section", "old", "new", "source"),
    [
        # A table cell that keeps the right value and publishes a stale one beside it.
        (
            _PARITY_SECTION,
            "| `viterbi_min_agreement` | floor | 0.95 |",
            "| `viterbi_min_agreement` | floor | 0.95 (was 0.93) |",
            "$.tolerance.viterbi_min_agreement",
        ),
        # The same shape in a Quote's paragraph rather than a cell.
        (
            _KINSOFT_SECTION,
            "(5 % average)",
            "(5 % average), or (6 % average)",
            "$.levels.level1.reported_inter_tool_spread.rate_mean_rel_dev_from_gt",
        ),
    ],
)
def test_a_contradiction_beside_a_correct_value_fails(
    section: str, old: str, new: str, source: str
) -> None:
    """Keeping the right number is not enough if a wrong one is published beside it.

    Cell, Prose and Quote all have to agree about this. They did not: only the Prose
    branch rejected a contradiction, so the same stale restatement passed or failed
    depending on whether the page happened to print it in a table.
    """
    mutated = _mutate(section, old, new)
    pins = [p for p in REGISTRY if p.source == source]
    assert pins, f"no registry entry sources {source}"
    for pin in pins:
        assert satisfied(PAGE_TEXT, pin), f"{source} does not hold on the unmutated page"
        assert not satisfied(mutated, pin), (
            f"{source} was accepted while the page also stated a contradicting value"
        )


@pytest.mark.parametrize(
    ("label", "replacement", "expected_nonzero"),
    [
        # The clause changed outright.
        ("changed", "all other off-diagonal rates 0.01", ["0.01"]),
        # A correct clause kept, a contradicting one added after it. Reading only the
        # first occurrence would call this page clean.
        (
            "contradicted",
            "all other off-diagonal rates zero, and all other off-diagonal rates 0.01",
            ["0.01"],
        ),
    ],
)
def test_a_bad_zero_clause_fails(label: str, replacement: str, expected_nonzero: list[str]) -> None:
    """Both failures go through :func:`zero_clauses`, the function the guard itself calls.

    Re-implementing the scan here would make this test pass even after the guard regressed
    to reading one occurrence — a mutation test that only proves the mutation happened.
    """
    mutated = _mutate(_KINSOFT_SECTION, "all other off-diagonal rates zero", replacement)
    page = _flat(paragraph(mutated, _KINSOFT_SECTION, _LEVEL3_RATES_POINTER))
    assert zero_clauses(page)[1] == expected_nonzero, label
    # And the unmutated page is clean by the same function.
    assert not zero_clauses(_flat(paragraph(PAGE_TEXT, _KINSOFT_SECTION, _LEVEL3_RATES_POINTER)))[1]


def test_a_duplicate_column_fails_rather_than_binding_to_the_first() -> None:
    """A stale second column must not hide behind the canonical one.

    ``header.index()`` always returns the first, so without a count the pins would keep
    reading the good column while the contradicting one stayed published.
    """
    pin = next(p for p in REGISTRY if isinstance(p, Cell) and p.column == "`$.tolerance` (default)")
    duplicated = PAGE_TEXT.replace(
        "| Bound | Direction | `$.tolerance` (default) | `$.tolerance_by_method.ebhmm` |",
        "| Bound | Direction | `$.tolerance` (default) | `$.tolerance` (default) |"
        " `$.tolerance_by_method.ebhmm` |",
    )
    with pytest.raises(AssertionError, match="exactly one"):
        cell_text(duplicated, pin)


def test_a_row_label_only_matches_at_a_boundary() -> None:
    """`level2` must not answer for a row renamed `level20`.

    The uniqueness assertion cannot catch this on its own — only one row would still
    start with `level2` — so the pin would validate that row's numbers under a label the
    page no longer uses.
    """
    assert _row_matches("level2 (Fig. 3)", "level2")
    assert _row_matches("level2", "level2")
    assert not _row_matches("level20 (Fig. 3)", "level2")
    assert not _row_matches("level2_old", "level2")
    # A backticked label still has to match the whole token.
    assert _row_matches("`k12_low_high`", "k12_low_high")
    assert not _row_matches("`k12_low_high_stale`", "k12_low_high")


def test_an_extra_transition_on_the_page_fails() -> None:
    """A seventh rate printed beside the six contradicts the "all other zero" clause."""
    extra = _mutate(_KINSOFT_SECTION, "k32 0.680", "k13 0.010, k32 0.680")
    displayed = {
        name
        for name, values in _owned_literals(
            paragraph(extra, _KINSOFT_SECTION, _LEVEL3_RATES_POINTER)
        ).items()
        if _TRANSITION_RE.match(name) and values
    }
    assert "k13" in displayed
    assert displayed != set(at(KINSOFT, _LEVEL3_RATES_POINTER))


def test_a_stale_duplicate_section_fails_rather_than_being_shadowed() -> None:
    """A second copy of a section must not hide behind the canonical one.

    Dictionary assignment used to be last-one-wins, so a stale duplicate inserted *above*
    the real section was read by nothing and published anyway.
    """
    heading = f"## {_KINSOFT_SECTION}"
    duplicated = PAGE_TEXT.replace(
        heading,
        f"{heading}\n\nA stale copy with `$.band.rate_rel_deviation_max = 0.99`.\n\n{heading}",
        1,
    )
    with pytest.raises(AssertionError, match="two '## External benchmark' sections"):
        _sections(duplicated)


def test_a_dropped_quotation_fails_even_though_the_artifact_still_says_it() -> None:
    """The page owns one side of every quotation it presents as verbatim.

    Goes through :func:`quotation_sides`, the same function the live check calls, so a
    regression that stopped consulting the page would fail here too.
    """
    fragment, artifact, pointer, section, anchor = _VERBATIM_QUOTES[0]
    reworded = PAGE_TEXT.replace(f'it "{fragment}"', "it says otherwise")
    before = quotation_sides(fragment, artifact, pointer, paragraph(PAGE_TEXT, section, anchor))
    after = quotation_sides(fragment, artifact, pointer, paragraph(reworded, section, anchor))
    assert before == (True, True)
    # The artifact side is untouched; only the page stopped quoting it.
    assert after == (True, False)


def test_an_ambiguous_anchor_fails_loudly_rather_than_picking_one() -> None:
    """Region selection asserts uniqueness, so a page edit cannot silently widen it."""
    duplicated = _mutate(
        "(g) Photobleaching detector",
        "**Frozen constants.** The ±2-frame tolerance",
        "`PB_PRIOR_MU` is also mentioned here.\n\n**Frozen constants.** The ±2-frame tolerance",
    )
    with pytest.raises(AssertionError, match="exactly one non-table paragraph"):
        paragraph(duplicated, "(g) Photobleaching detector", "PB_PRIOR_MU")


# --- #440: hardening the guard --------------------------------------------------

#: The anchors #440 moved off a value-embedding code span onto the bare pointer.
_REANCHORED = (
    (_KINSOFT_SECTION, "$.band.rate_rel_deviation_max"),
    ("(d) Ranker held-out cross-validation", "DEFAULT_SHIP_BAR_PTS"),
)


@pytest.mark.parametrize(("section", "anchor"), _REANCHORED, ids=[a for _, a in _REANCHORED])
def test_a_bare_pointer_anchor_selects_exactly_one_paragraph(section: str, anchor: str) -> None:
    """Neither bare pointer is a whole code span on the page; the leading-token tier finds it."""
    assert anchor not in _BACKTICKED_RE.findall(_section(PAGE_TEXT, section))
    assert f"`{anchor} " in paragraph(PAGE_TEXT, section, anchor)


def test_no_anchor_embeds_a_value() -> None:
    """An anchor that repeats the value its pin reads breaks selection on a correct update.

    Deliberately narrow — an anchor written ``rate 0.12`` would still pass — because it
    fences the one shape this module has actually carried.
    """
    embedding = sorted(
        {p.anchor for p in REGISTRY if isinstance(p, Prose | Quote) and " = " in p.anchor}
    )
    assert not embedding, f"these anchors embed a value: {embedding}"


@pytest.mark.parametrize(
    ("old", "new", "source"),
    [
        (
            "`$.band.rate_rel_deviation_max = 0.12`",
            "`$.band.rate_rel_deviation_max = 0.13`",
            "$.band.rate_rel_deviation_max",
        ),
        (
            "(5 % average)",
            "(6 % average)",
            "$.levels.level1.reported_inter_tool_spread.rate_mean_rel_dev_from_gt",
        ),
    ],
)
def test_a_re_anchored_pin_still_rejects_a_stale_page(old: str, new: str, source: str) -> None:
    """Re-anchoring changed how the paragraph is found, not what the pin demands of it.

    Kept out of :func:`test_a_deliberate_one_value_mismatch_fails` on purpose: that test
    requires every pin sharing a ``source`` to reject the edit, and the two `Advisory band`
    table cells also carry `$.band.rate_rel_deviation_max` while reading a table this
    prose-only edit does not touch.
    """
    mutated = _mutate(_KINSOFT_SECTION, old, new)
    pins = [
        p
        for p in REGISTRY
        if isinstance(p, Prose | Quote) and p.source == source and p.anchor == _REANCHORED[0][1]
    ]
    assert pins, f"no re-anchored prose pin sources {source}"
    for pin in pins:
        assert satisfied(PAGE_TEXT, pin)
        assert not satisfied(mutated, pin), f"{source} accepted a stale page"


def test_a_correctly_updated_span_is_still_selectable() -> None:
    """The defect the re-anchor removes: a right page must not fail *selection*.

    A page that moved its span to a new band value is still found by the bare pointer,
    and is then rejected for the value — the artifact still says 0.12 — rather than with
    a selection error that names the wrong problem.
    """
    updated = _mutate(
        _KINSOFT_SECTION,
        "`$.band.rate_rel_deviation_max = 0.12`",
        "`$.band.rate_rel_deviation_max = 0.15`",
    )
    pin = next(
        p for p in REGISTRY if isinstance(p, Prose) and p.source == "$.band.rate_rel_deviation_max"
    )
    assert "0.15" in paragraph(updated, pin.section, pin.anchor)
    assert not satisfied(updated, pin)


def test_the_exact_tier_wins_over_the_leading_token_tier() -> None:
    """Adding the leading-token tier cannot change what an exact anchor selects."""
    text = "## S\n\nFirst names `X = 1`.\n\nSecond names `X`.\n"
    assert paragraph(text, "S", "X").strip() == "Second names `X`."
    assert paragraph("## S\n\nOnly `X = 1` here.\n", "S", "X").strip() == "Only `X = 1` here."


@pytest.mark.parametrize(
    "value",
    [
        # Real artifact values, neither of which survives ':g'.
        PARITY["pooled_worst"]["state_mean_abs_delta"],
        PARITY["measured_by_method"]["ebhmm"]["pooled_worst"]["viterbi_agreement"],
    ],
)
def test_a_quote_refuses_a_value_g_would_round(value: float) -> None:
    """A Quote on a long float must fail loudly rather than compare a rounding."""
    pin = Quote(_PARITY_SECTION, "**Measured result.**", "{}", "hypothetical", value)
    with pytest.raises(AssertionError, match="round-trip"):
        pin.value_text()


def test_module_constant_reads_the_last_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``X = 5.0`` then ``X = 8.0`` is ``8.0`` at runtime, so it is ``8.0`` here."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_text("X = 5.0\nY: float = 1.0\nX = 8.0\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    assert module_constant("pkg.mod", "X") == 8.0
    assert module_constant("pkg.mod", "Y") == 1.0
    with pytest.raises(AssertionError, match="no module-level constant 'Z'"):
        module_constant("pkg.mod", "Z")


def test_mutate_chains_on_a_text_it_is_given() -> None:
    """The optional ``text`` lets a second mutation build on the first, still checked."""
    first = _mutate(_KINSOFT_SECTION, "k32 0.680", "k32 0.690")
    second = _mutate(_KINSOFT_SECTION, "k32 0.690", "k32 0.700", first)
    assert "k32 0.700" in second
    assert "k32 0.680" not in second
    with pytest.raises(AssertionError, match="not unique"):
        _mutate(_KINSOFT_SECTION, "k32 0.680", "k32 0.710", second)


#: The four mutation sites a review found replacing text without proving it applied.
_ROUTED_THROUGH_MUTATE = (
    "test_a_number_moved_to_a_neighbouring_key_fails",
    "test_a_bad_zero_clause_fails",
    "test_an_extra_transition_on_the_page_fails",
    "test_an_ambiguous_anchor_fails_loudly_rather_than_picking_one",
)


def test_the_named_mutation_sites_go_through_mutate() -> None:
    """Scoped to the four named functions; the other direct-replace sites are deliberate."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in _ROUTED_THROUGH_MUTATE
    }
    assert set(functions) == set(_ROUTED_THROUGH_MUTATE)
    for name, function in functions.items():
        replaces = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
        ]
        assert not replaces, f"{name} still calls .replace() directly"


@pytest.mark.parametrize(
    "text",
    [
        "```\n## Fake heading\n```\n## Real section\nbody\n",
        "## Real section\nbody\n```\n## Fake heading\n```\nmore body\n",
    ],
    ids=["fence-before-heading", "fence-in-body"],
)
def test_a_heading_inside_a_fence_is_not_a_section(text: str) -> None:
    assert list(_sections(text)) == ["Real section"]


def test_a_table_inside_a_fence_is_not_a_table() -> None:
    assert _tables("prose\n\n```\n| a | b |\n| --- | --- |\n| 1 | 2 |\n```\n") == []
    # A real table after the fence is still found.
    assert _tables("```\n| a | b |\n```\n| c | d |\n| --- | --- |\n| 3 | 4 |\n") == [
        Table(("c", "d"), (("3", "4"),))
    ]


def test_module_constant_skips_an_annotation_without_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``X = 5.0`` then a bare ``X: float`` is still ``5.0``; the annotation binds nothing."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_text("X = 5.0\nX: float\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    assert module_constant("pkg.mod", "X") == 5.0


@pytest.mark.parametrize(
    "text",
    [
        # A longer fence showing a shorter fenced example stays one block.
        "## Real section\nbody\n````\n```\n## Fake heading\n```\n````\n",
        # A backtick fence does not close on tildes.
        "## Real section\nbody\n```\n~~~\n## Fake heading\n~~~\n```\n",
    ],
    ids=["longer-outer-fence", "mismatched-fence-char"],
)
def test_a_fence_closes_only_on_its_own_delimiter(text: str) -> None:
    assert list(_sections(text)) == ["Real section"]
    assert _tables(text.replace("## Fake heading", "| a | b |")) == []


def test_the_fence_rule_is_python_markdowns() -> None:
    """The copied rule still matches the Python-Markdown the docs build would use."""
    fenced_code = pytest.importorskip("markdown.extensions.fenced_code")
    installed = fenced_code.FencedBlockPreprocessor.FENCED_BLOCK_RE
    assert _FENCED_BLOCK_RE.pattern == installed.pattern
    assert _FENCED_BLOCK_RE.flags == installed.flags


@pytest.mark.parametrize(
    "text",
    [
        # An indented delimiter is not a fence to Python-Markdown's fenced_code.
        "## Real section\nbody\n   ```\n## Second section\n| a | b |\n   ```\n",
        # An opener that never closes is ordinary text, not a fence over the rest.
        "## Real section\nbody\n```\n## Second section\n| a | b |\n",
    ],
    ids=["indented-delimiter", "unclosed-opener"],
)
def test_a_pseudo_fence_does_not_hide_live_content(text: str) -> None:
    sections = _sections(text)
    assert list(sections) == ["Real section", "Second section"]
    assert _tables(sections["Second section"]) == [Table(("a", "b"), ())]


def test_mutate_takes_its_boundary_from_the_fence_aware_parser() -> None:
    """A fenced `## ` line before the value must not end the section early."""
    page = "intro\n## S\n```\n## not a heading\n```\nvalue 1\n## T\nvalue 1\n"
    mutated = _mutate("S", "value 1", "value 2", page)
    assert mutated == page.replace("```\nvalue 1\n", "```\nvalue 2\n")
    assert _sections(mutated)["T"] == "value 1\n"


# --- #435: restatements --------------------------------------------------------


def test_every_restatement_pin_has_a_source_label_of_its_own() -> None:
    """A restatement pin's ``source`` names its location, and no other pin shares it.

    The two mutation tests above collect every pin with a given ``source`` and require
    one edited page to break *all* of them. A restatement pin labelled with a bare pointer
    would be collected by a mutation of a different location that it rightly survives,
    and a landed case would go red. The bare pointers stay on the pins that carried them
    before, which is what the frozen-bound and pooled-worst coverage tests read.
    """
    restatements = _restatement_pins()
    counts: dict[str, int] = {}
    for pin in REGISTRY:
        counts[pin.source] = counts.get(pin.source, 0) + 1
    for pin in restatements:
        assert " @ " in pin.source, f"{pin.source!r} does not name its location"
        assert counts[pin.source] == 1, f"{pin.source!r} labels {counts[pin.source]} pins"


def test_a_full_commit_in_a_build_table_cell_is_rejected() -> None:
    """The build table abbreviates, so a pin holding the full 40 hex must not match it.

    Proves the comparison is the exact set equality of :func:`_states` against the
    *derived* spelling, not a prefix test that would accept either form.
    """
    for column, pointer in ((_M05_COLUMN, _M05_COMMIT), (_EBHMM_COLUMN, _EBHMM_COMMIT)):
        commit = at(PARITY, pointer)
        assert satisfied(PAGE_TEXT, commit_cell(column, pointer, commit))
        full = commit_cell(column, pointer, commit)._replace(value=commit)
        assert not satisfied(PAGE_TEXT, full), f"{pointer}: the full hash matched an abbreviation"


def _other_commit(commit: object) -> str:
    """A hypothetical re-measured commit: same length, a different first hex digit."""
    assert isinstance(commit, str)
    return ("0" if commit[0] != "0" else "1") + commit[1:]


def test_a_re_measured_commit_is_rejected_by_the_unchanged_page() -> None:
    """The 8-hex derivation reads the live value; it cannot accept what the page says."""
    for column, pointer in ((_M05_COLUMN, _M05_COMMIT), (_EBHMM_COLUMN, _EBHMM_COMMIT)):
        moved = commit_cell(column, pointer, _other_commit(at(PARITY, pointer)))
        assert not satisfied(PAGE_TEXT, moved), f"{pointer} accepted a commit it does not print"
    abbreviated = [
        p for p in _restatement_pins() if isinstance(p, Quote) and p.render is short_commit
    ]
    assert len(abbreviated) == 4
    for pin in abbreviated:
        assert satisfied(PAGE_TEXT, pin)
        moved_quote = pin._replace(value=_other_commit(pin.value))
        assert not satisfied(PAGE_TEXT, moved_quote), f"{pin.source} accepted a moved commit"


def test_short_commit_refuses_anything_but_a_full_commit() -> None:
    with pytest.raises(AssertionError, match="full 40-hex"):
        short_commit("71cfa1af")


def test_a_re_measured_pooled_worst_is_rejected_by_the_unchanged_page() -> None:
    """The three-significant-figure rounding reads the live value.

    A hypothetical re-measure that moves the third significant figure must fail against
    the unchanged page, so a derivation bug cannot accept whatever the page prints.
    """
    rounded = [p for p in _restatement_pins() if isinstance(p, Quote) and p.render is sci3]
    assert len(rounded) == 2
    for pin in rounded:
        assert satisfied(PAGE_TEXT, pin)
        assert isinstance(pin.value, float)
        moved = pin._replace(value=pin.value * 1.02)
        assert sci3(moved.value) != sci3(pin.value)
        assert not satisfied(PAGE_TEXT, moved), f"{pin.source} accepted a re-measured value"


def test_the_rounded_and_the_exact_rendering_stay_different_paths() -> None:
    """Rounding is a declared rendering, never the exact one in disguise.

    For `$.pooled_worst.state_mean_abs_delta` the page's table prints the exact value and
    the blockquote the rounded one; if the two renderings ever agreed for that value, the
    blockquote pin would no longer be a rounding pin at all.
    """
    value = at(PARITY, "$.pooled_worst.state_mean_abs_delta")
    assert isinstance(value, float)
    assert sci3(value) != shortest(value)
    assert float(shortest(value)) == value
    assert float(sci3(value)) != value


@pytest.mark.parametrize("printed", ["8.3472e-09", "8.3e-09", "8.347e-09"])
def test_the_page_text_is_never_rounded_before_comparison(printed: str) -> None:
    """Only the live value is rounded. A page printing more or fewer figures fails."""
    mutated = _mutate(_PARITY_SECTION, "8.35e-09 / 1.00e-09", f"{printed} / 1.00e-09")
    pin = next(
        p
        for p in REGISTRY
        if p.source == f"$.pooled_worst.state_mean_abs_delta, 3 s.f.{_AT_GAP_QUOTE}"
    )
    assert satisfied(PAGE_TEXT, pin)
    assert not satisfied(mutated, pin)


def test_both_floors_must_hold_one_common_value() -> None:
    """ "1.0 on both floors" only has a value to state while the two floors agree."""
    with pytest.raises(AssertionError, match="no single value"):
        _common_value("$.pooled_worst.state_count_fraction", "$.pooled_worst.relative_elbo")


def test_a_blockquote_pin_survives_a_rewrap_across_a_line_break() -> None:
    """The blockquote's ``>`` markers are not part of the sentence.

    Re-wraps the gap blockquote so pinned phrases straddle a line break, and every pin
    reading that blockquote still holds. The naive flattening — whitespace only, no
    marker strip — is shown failing on the same re-wrap, which is what the strip fixes.
    """
    rewrapped = _mutate(
        _PARITY_SECTION,
        "8.35e-09 / 1.00e-09 against ceilings of 0.02 and 0.01, and",
        "8.35e-09 /\n> 1.00e-09 against\n> ceilings of 0.02 and\n> 0.01, and",
    )
    pins = [p for p in REGISTRY if isinstance(p, Quote) and p.anchor == _GAP_QUOTE_ANCHOR]
    assert len(pins) == 9
    for pin in pins:
        assert satisfied(rewrapped, pin), f"{pin.source} broke on a re-wrap"
    region = paragraph(rewrapped, _PARITY_SECTION, _GAP_QUOTE_ANCHOR)
    naive = " ".join(region.split())
    assert "8.35e-09 / 1.00e-09 against ceilings of 0.02 and 0.01" not in naive
    assert "8.35e-09 / 1.00e-09 against ceilings of 0.02 and 0.01" in _flat(region)


def test_the_blockquote_copy_of_the_measured_methods_quotation_is_checked() -> None:
    """The blockquote's own copy of the quotation fails on its own, not via the other."""
    entry = next(q for q in _VERBATIM_QUOTES if q[4] == _GAP_QUOTE_ANCHOR)
    fragment, artifact, pointer, section, anchor = entry
    mutated = _mutate(
        _PARITY_SECTION,
        '`"vbconhmm (vb Consensus HMM)"` quoted below',
        '`"vbconhmm (VB Consensus HMM)"` quoted below',
    )
    before = quotation_sides(fragment, artifact, pointer, paragraph(PAGE_TEXT, section, anchor))
    after = quotation_sides(fragment, artifact, pointer, paragraph(mutated, section, anchor))
    assert before == (True, True)
    assert after == (True, False)


def test_the_ship_floor_illustration_straddles_the_ebfret_floor() -> None:
    """ "(0.5249 and 0.40 fail; 0.5250 and 0.60 pass)" is checked as a claim, not a copy."""
    floor = at(PARITY, _EBHMM_FLOOR)
    assert isinstance(floor, float)
    region = paragraph(PAGE_TEXT, _PARITY_SECTION, _SHIP_FLOOR_ANCHOR)
    assert straddle_violations(region, floor) == []


def test_a_floor_moved_past_the_illustration_rejects_it() -> None:
    """Driven with a hypothetical re-frozen floor of 0.60, the same sentence is wrong."""
    region = paragraph(PAGE_TEXT, _PARITY_SECTION, _SHIP_FLOOR_ANCHOR)
    assert straddle_violations(region, 0.60) == [
        "0.525 passes but is not above 0.6",
        "0.6 passes but is not above 0.6",
    ]
    # And a fail-side literal above the live floor is caught in the other direction.
    floor = at(PARITY, _EBHMM_FLOOR)
    assert isinstance(floor, float)
    mutated = _mutate(_PARITY_SECTION, "(0.5249 and 0.40 fail;", "(0.5249 and 0.55 fail;")
    assert straddle_violations(paragraph(mutated, _PARITY_SECTION, _SHIP_FLOOR_ANCHOR), floor)


def test_the_scope_anchor_still_selects_the_third_bullet() -> None:
    """The Scope pins depend on a selection fact; assert it so it fails as itself.

    The third bullet has no code span of its own except two of the values it restates,
    so the pins select the Validation-coverage list through `$.tolerance` in a later
    bullet. That works only while the list is one blank-line-free block. A blank line
    between bullets is a routine edit that would move the restated values out of the
    selection and make the pins fail as though the page stated a wrong value.
    """
    region = paragraph(PAGE_TEXT, _SCOPE_SECTION, _SCOPE_ANCHOR)
    assert _SCOPE_BULLET_LEAD_IN in region, (
        f"the '{_SCOPE_SECTION}' paragraph selected by {_SCOPE_ANCHOR!r} no longer holds "
        f"{_SCOPE_BULLET_LEAD_IN!r}. The values may still be right: re-anchor the Scope "
        "pins on the paragraph that holds that bullet rather than editing a value."
    )


def test_an_exact_float_restatement_catches_a_change_past_six_digits() -> None:
    """The ceilings and floor the page restates exactly use the full-precision renderer.

    ``:g`` keeps six significant digits, so a bound re-frozen at ``0.020000001`` would
    render ``0.02`` and the unchanged page would pass. ``shortest`` renders every bit.
    """
    exact = [
        p
        for p in _restatement_pins()
        if isinstance(p, Quote) and isinstance(p.value, float) and p.render is not sci3
    ]
    assert len(exact) == 5  # both ceilings, the 1.0 floors clause, both 0.5249 copies
    for pin in exact:
        assert pin.render is shortest, f"{pin.source} renders a float with ':g'"
        assert isinstance(pin.value, float)
        nudged = pin._replace(value=float(repr(pin.value)) + pin.value * 1e-9)
        assert nudged.value != pin.value
        assert not satisfied(PAGE_TEXT, nudged), f"{pin.source} missed a 1e-9 relative change"
