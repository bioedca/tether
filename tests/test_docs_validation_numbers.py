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

**What is deliberately not pinned** is enumerated in :data:`EXCLUSIONS` with a reason
for each, because an unexplained omission on a validation page is indistinguishable from
an oversight. In short: figures whose source is upstream git history, a historical
Actions run, or a prose record of a local run that produced no machine-readable artifact.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
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
    """
    path = SRC / Path(*dotted.split(".")).with_suffix(".py")
    for stmt in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name:
                assert stmt.value is not None
                return ast.literal_eval(stmt.value)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(stmt.value)
    raise AssertionError(f"{dotted} has no module-level constant {name!r}")


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
    """
    out: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []

    def close() -> None:
        assert name is not None
        assert name not in out, (
            f"docs/validation.md has two '## {name}' sections. Every pin would read only "
            "the later one while the earlier stayed published; de-duplicate the page."
        )
        out[name] = "\n".join(buf)

    for line in text.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading:
            if name is not None:
                close()
            name, buf = heading.group(1).strip(), []
        else:
            buf.append(line)
    if name is not None:
        close()
    return out


def _tables(body: str) -> list[Table]:
    """Every Markdown table in ``body``, header row first."""
    out: list[Table] = []
    header: list[str] | None = None
    rows: list[tuple[str, ...]] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):
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
        candidates = {cell.strip(), cell.strip().strip("`").strip('"')}
        candidates |= set(_BACKTICKED_RE.findall(cell)) | set(_QUOTED_RE.findall(cell))
        return value in candidates
    for text in _NUMBER_RE.findall(cell):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):  # pragma: no cover - defensive
            continue
        if type(parsed) is type(value) and parsed == value:
            return True
    return False


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

    @property
    def label(self) -> str:
        return f"{self.anchor}|{self.template}"

    def rendered(self) -> str:
        if isinstance(self.value, str):
            return self.template.format(self.value)
        return self.template.format(f"{self.value * self.scale:g}")


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


def cell_text(text: str, pin: Cell) -> str:
    """The single cell a :class:`Cell` pin addresses."""
    table = _table(text, pin)
    assert pin.column in table.header, (
        f"the {pin.table!r} table in '{pin.section}' has no {pin.column!r} column; "
        f"its header is {list(table.header)}"
    )
    column = table.header.index(pin.column)
    matches = [
        row
        for row in table.rows
        if pin.row in _BACKTICKED_RE.findall(row[0]) or row[0].startswith(pin.row)
    ]
    assert len(matches) == 1, (
        f"the {pin.table!r} table in '{pin.section}' must hold exactly one row for "
        f"{pin.row!r}; found {len(matches)}"
    )
    row = matches[0]
    assert column < len(row), f"row {pin.row!r} has no cell under {pin.column!r}"
    return row[column]


def paragraph(text: str, section: str, anchor: str) -> str:
    """The one non-table paragraph of ``section`` selected by ``anchor``.

    ``anchor`` is normally a backticked token, matched by equality so that
    ``$.levels.level1`` never resolves through ``$.levels.level1.ground_truth``. A few
    paragraphs the page never labels with a pointer — ``**Measured result.**`` — are
    reachable by their bold lead-in instead, and that fallback is only consulted when the
    token form matched nothing, so no existing anchor changes meaning.
    """
    paragraphs = _prose_paragraphs(_section(text, section))
    matches = [p for p in paragraphs if anchor in _BACKTICKED_RE.findall(p)]
    if not matches:
        matches = [p for p in paragraphs if p.lstrip().startswith(anchor)]
    assert len(matches) == 1, (
        f"'{section}' must hold exactly one non-table paragraph selected by {anchor!r}; "
        f"found {len(matches)} — re-anchor the pin rather than relaxing it"
    )
    return matches[0]


def _flat(text: str) -> str:
    """One line, single-spaced — so a pinned phrase survives Markdown re-wrapping.

    The page hard-wraps at 90-odd columns, so ``Paper doi`` and the DOI that belongs to it
    sit on different lines. Without this a label-bound phrase would be unmatchable purely
    because of where the paragraph happened to break.
    """
    return " ".join(text.split())


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
    return _flat(pin.rendered()) in _flat(paragraph(text, pin.section, pin.anchor))


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
_ALL_OTHER_RE = re.compile(r"all other off-diagonal rates (?:are )?([A-Za-z0-9.]+)")
_ZERO_WORDS = frozenset({"0", "0.0", "zero"})

#: ``kbright=7 s^-1`` inside the level-3 ``note``, keyed on the name.
_BLINKING_RE = r"\b{name}\s*=\s*([0-9.]+)"


def _blinking_rate(name: str) -> object:
    """``kbright`` / ``kdark`` as the level-3 ``note`` records them.

    These two are the one pair the artifact keeps inside free text — *"blinking kbright=7
    s^-1, kdark=0.007 s^-1"* — rather than as fields of ``rates_s_inv``, so they are read
    with a regex bounded to the key name instead of a pointer. Restating the literal here
    would make the pin assert the test's own copy of the number, and a pin that reads
    nothing is prose.
    """
    note = str(at(KINSOFT, "$.levels.level3.ground_truth.note"))
    match = re.search(_BLINKING_RE.format(name=name), note)
    assert match, f"the level-3 note no longer records `{name}=`; re-read it before pinning"
    return ast.literal_eval(match.group(1))


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
    m05_column = "`$.method` (M0.5, vbconhmm)"
    ebhmm_column = "`$.measured_by_method.ebhmm.method`"
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
            "DEFAULT_SHIP_BAR_PTS = 10.0",
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
            ("license", "({})", at(KINSOFT, "$.source.license")),
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
            "$.band.rate_rel_deviation_max = 0.12",
            "$.band.rate_rel_deviation_max",
            "$.band.rate_rel_deviation_max",
            band,
        )
    )
    pins.append(
        Quote(
            _KINSOFT_SECTION,
            "$.band.rate_rel_deviation_max = 0.12",
            "({} % average)",
            f"{spread}.rate_mean_rel_dev_from_gt",
            at(KINSOFT, f"{spread}.rate_mean_rel_dev_from_gt"),
            scale=100,
        )
    )
    pins.append(
        Quote(
            _KINSOFT_SECTION,
            "$.band.rate_rel_deviation_max = 0.12",
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

    return pins


REGISTRY = _registry()


# --- What is deliberately left unpinned ----------------------------------------

#: Figures the page prints that no committed artifact holds, so a pin here would assert
#: nothing (or would assert that two transcriptions of the same prose agree). Recorded
#: rather than dropped: on a validation page an unexplained omission and an oversight
#: look identical, and #214 asks for the reason in the test.
EXCLUSIONS: dict[str, str] = {
    "tMAVEN upstream commit hashes and their 2025-05-06 / 2025-10-05 dates": (
        "The dates are upstream-repository facts held in no committed artifact. #214 puts "
        "the hashes beside them out of scope; the artifact does in fact record both under "
        "`$.method.tmaven_commit` and `$.measured_by_method.ebhmm.method.tmaven_commit`, "
        "so pinning them is a separate, filed follow-up rather than a silent widening here."
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
    "the paper's '14 published analyses', '17 %' and '9-14 %' figures": (
        "Facts of Gotz et al. 2022. kinsoft_reference.json records them only inside the "
        "free-text `spread_source` and `deferred_reason` strings, which the page "
        "paraphrases rather than quotes, so there is no literal to pin against."
    ),
}


# --- The guard ------------------------------------------------------------------


def test_page_exists_and_still_has_its_sections() -> None:
    assert PAGE.is_file(), f"{PAGE} is missing"
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
    # The leading token of each code span, so `DEFAULT_SHIP_BAR_PTS = 10.0` counts.
    code_spans = {span.split()[0] for span in _BACKTICKED_RE.findall(PAGE_TEXT) if span.split()}
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
)


@pytest.mark.parametrize(
    ("fragment", "artifact", "pointer", "section", "anchor"),
    _VERBATIM_QUOTES,
    ids=[pointer for _, _, pointer, _, _ in _VERBATIM_QUOTES],
)
def test_the_page_quotes_the_artifact_verbatim(
    fragment: str, artifact: dict, pointer: str, section: str, anchor: str
) -> None:
    """A quotation has two sides, and the page owns one of them."""
    held = at(artifact, pointer)
    source = held if isinstance(held, str) else "\n".join(str(item) for item in held)  # type: ignore[union-attr]
    assert fragment in source, (
        f"docs/validation.md presents {fragment!r} as a verbatim quote of {pointer}, "
        "but the artifact no longer says it"
    )
    assert fragment in _flat(paragraph(PAGE_TEXT, section, anchor)), (
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
        match = _ALL_OTHER_RE.search(text)
        assert match, f"{label} no longer states what the unlisted off-diagonal rates are"
        stated = match.group(1).rstrip(".,;)")
        assert stated in _ZERO_WORDS, (
            f"{label} says the unlisted off-diagonal rates are {stated!r}, not zero"
        )
    listed = at(KINSOFT, _LEVEL3_RATES_POINTER)
    assert isinstance(listed, dict)
    assert set(listed) == set(_LEVEL3_RATES), (
        "the page enumerates a different rate set than the artifact records, so its "
        '"all other" clause no longer covers what it claims to'
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


def _mutate(section: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` inside one ``##`` section of the page."""
    heading = f"\n## {section}\n"
    start = PAGE_TEXT.index(heading) + len(heading)
    end = PAGE_TEXT.find("\n## ", start)
    end = len(PAGE_TEXT) if end == -1 else end
    body = PAGE_TEXT[start:end]
    assert body.count(old) == 1, f"{old!r} is not unique inside '{section}'"
    return PAGE_TEXT[:start] + body.replace(old, new) + PAGE_TEXT[end:]


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
    swapped_text = swapped.replace(
        "`viterbi_agreement` 0.9355949176941853", "`viterbi_agreement` 0.6832740213523132"
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


def test_a_nonzero_all_other_clause_fails() -> None:
    """The zero clause is a claim, so changing it has to break something."""
    nonzero = PAGE_TEXT.replace(
        "all other off-diagonal rates zero", "all other off-diagonal rates 0.01"
    )
    match = _ALL_OTHER_RE.search(_flat(paragraph(nonzero, _KINSOFT_SECTION, _LEVEL3_RATES_POINTER)))
    assert match is not None
    assert match.group(1).rstrip(".,;)") not in _ZERO_WORDS


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
    """The page owns one side of every quotation it presents as verbatim."""
    fragment, artifact, pointer, section, anchor = _VERBATIM_QUOTES[0]
    assert fragment in str(at(artifact, pointer))  # the artifact side is untouched
    reworded = PAGE_TEXT.replace(f'it "{fragment}"', "it says otherwise")
    assert fragment not in _flat(paragraph(reworded, section, anchor))


def test_an_ambiguous_anchor_fails_loudly_rather_than_picking_one() -> None:
    """Region selection asserts uniqueness, so a page edit cannot silently widen it."""
    duplicated = PAGE_TEXT.replace(
        "**Frozen constants.** The ±2-frame tolerance",
        "`PB_PRIOR_MU` is also mentioned here.\n\n**Frozen constants.** The ±2-frame tolerance",
    )
    with pytest.raises(AssertionError, match="exactly one non-table paragraph"):
        paragraph(duplicated, "(g) Photobleaching detector", "PB_PRIOR_MU")
