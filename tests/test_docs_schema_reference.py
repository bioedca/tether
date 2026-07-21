# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The store-schema reference page matches the frozen manifest (issue #173).

``docs/reference/tether-format.md`` is the published description of the ``.tether``
HDF5 store. ``schema/schema_frozen.json`` is the machine-checked golden that
``schema-guard`` diffs the real writer against. If those two drift, the page becomes
the thing CONTRIBUTING.md warns about — "a plausible-sounding docstring that
misstates behaviour is worse than none, because it is believed" — so this module
pins the page to the golden: every frozen group gets its own ``##`` section, every
compound table's field rows reproduce the golden's field **names, canonical dtypes
and sub-array shapes in the frozen order**, and the root attributes are stated with
their pinned values.

Field *order* is part of the freeze (``_diff_compound`` in ``tether.io.schema``
requires the golden field sequence to stay an exact prefix of the current one), so
the field assertions compare ordered lists, not sets: a reordered or renamed row
fails, and so does a deleted one.

Stdlib only (``json``, ``pathlib``, ``re``) and no Tether import: ``python-markdown``
and ``h5py`` are both out of scope for what this needs, so the guard runs everywhere
the base 3-OS ``test`` matrix runs, not only in the Linux-only ``docs-build`` job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
GOLDEN = _REPO / "schema" / "schema_frozen.json"
PAGE = _REPO / "docs" / "reference" / "tether-format.md"

#: A ``## `<token>` — heading`` line; the first backticked token identifies the group.
_H2_RE = re.compile(r"^##\s+(.*)$")
_FIRST_CODE_RE = re.compile(r"`([^`]+)`")

#: A field-table row: ``| `name` | `dtype` | shape | meaning |``, where the shape cell
#: is ``scalar`` or ``` `(2,)` ```. Requiring a backticked identifier *and* a backticked
#: type cell is what tells a field row apart from an ordinary prose table row.
_FIELD_ROW_RE = re.compile(
    r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|"
)


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _sections() -> dict[str, list[str]]:
    """Map the first backticked token of each ``##`` heading to that section's lines.

    Headings without a backticked token (prose sections) are keyed by their full
    heading text, so they can never be mistaken for a group section.
    """
    out: dict[str, list[str]] = {}
    current: list[str] = []
    for line in _page().splitlines():
        m = _H2_RE.match(line)
        if m:
            code = _FIRST_CODE_RE.search(m.group(1))
            key = code.group(1) if code else m.group(1).strip()
            current = out.setdefault(key, [])
            continue
        current.append(line)
    return out


def _first_field_block(lines: list[str]) -> list[tuple[str, str, str]]:
    """Return the first contiguous run of field rows in ``lines``.

    Taking only the first run keeps a later, unrelated table in the same section
    (e.g. the ``/settings/extraction`` attribute table) from being mistaken for
    more fields of the frozen compound dtype.
    """
    block: list[tuple[str, str, str]] = []
    for line in lines:
        m = _FIELD_ROW_RE.match(line)
        if m:
            block.append((m.group(1), m.group(2), m.group(3).strip().strip("`")))
        elif block:
            break
    return block


def _expected_shape(shape: list[int]) -> str:
    """Render a golden field shape the way the page's Shape column spells it."""
    return "scalar" if not shape else f"({shape[0]},)"


def test_page_and_golden_exist() -> None:
    """Sanity: both inputs are on disk (guards a moved path silently passing)."""
    assert GOLDEN.is_file(), f"missing the golden manifest at {GOLDEN}"
    assert PAGE.is_file(), f"missing the schema reference page at {PAGE}"


def test_every_frozen_group_has_its_own_section() -> None:
    """Each of the 12 frozen group paths gets a ``##``-level section on the page."""
    sections = _sections()
    missing = [g for g in _golden()["groups"] if g not in sections]
    assert not missing, (
        "these frozen groups from schema/schema_frozen.json have no '## `<path>`' "
        f"section in docs/reference/tether-format.md: {missing}"
    )


def test_every_frozen_dataset_path_is_named() -> None:
    """Each frozen dataset path appears literally on the page."""
    text = _page()
    missing = [p for p in _golden()["datasets"] if p not in text]
    assert not missing, (
        f"these frozen dataset paths are not named in docs/reference/tether-format.md: {missing}"
    )


def test_field_tables_match_the_golden_exactly() -> None:
    """Every compound field is documented, with the golden dtype/shape, in order.

    The page documents a table's fields inside the section for its *parent group*
    (``/movies/table`` -> the ``## `/movies``` section), which is also where a reader
    looks for them.
    """
    problems: list[str] = []
    sections = _sections()
    for path, spec in _golden()["datasets"].items():
        group = path.rsplit("/", 1)[0] or "/"
        lines = sections.get(group)
        if lines is None:
            problems.append(f"{path}: no '## `{group}`' section to document it in")
            continue
        documented = _first_field_block(lines)
        expected = [
            (f["name"], f["dtype"], _expected_shape(f["shape"])) for f in spec["dtype"]["fields"]
        ]
        if documented != expected:
            documented_names = [row[0] for row in documented]
            expected_names = [row[0] for row in expected]
            if documented_names != expected_names:
                problems.append(
                    f"{path}: field rows disagree with the golden (order is part of the "
                    f"freeze): page {documented_names} != golden {expected_names}"
                )
            else:
                bad = [
                    f"{d[0]}: page ({d[1]}, {d[2]}) != golden ({e[1]}, {e[2]})"
                    for d, e in zip(documented, expected, strict=True)
                    if d != e
                ]
                problems.append(f"{path}: dtype/shape cells disagree with the golden: {bad}")
    assert not problems, (
        "docs/reference/tether-format.md has drifted from schema/schema_frozen.json "
        f"(the manifest wins — fix the page): {problems}"
    )


def test_root_attributes_and_pinned_values_are_stated() -> None:
    """Every root attribute has a row in the ``/`` section stating its type and value.

    Scoped to that one row rather than to the whole page: a bare ``\\`1\\``` search
    would be satisfied by any unrelated backticked ``1`` elsewhere on the page (the
    feature-cache version, for one), which is exactly the kind of accidentally-passing
    assertion this guard exists to avoid.
    """
    rows = {
        m.group(1): line
        for line in _sections().get("/", [])
        if (m := re.match(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|", line))
    }
    golden_attrs = _golden()["attrs"]["/"]

    missing = [name for name in golden_attrs if name not in rows]
    assert not missing, (
        f"these frozen root attributes have no row in the '## `/`' section: {missing}"
    )

    problems = [
        f"{name}: row does not state {token}"
        for name, spec in golden_attrs.items()
        for token in (f"`{spec['dtype']}`", *([f"`{spec['value']}`"] if "value" in spec else []))
        if token not in rows[name]
    ]
    assert not problems, (
        "the '## `/`' root-attribute rows must state the dtype schema-guard freezes, "
        f"and the value for the two value-frozen attributes: {problems}"
    )


def test_page_states_the_additive_only_rule_and_the_bump_requirement() -> None:
    """The policy facts a downstream reader depends on are actually written down.

    Compares against whitespace-normalized, emphasis-stripped text so re-wrapping a
    paragraph or bolding a phrase cannot break the check.
    """
    text = re.sub(r"\s+", " ", _page().lower().replace("*", ""))
    for phrase in ("additive-only", "schema-guard", "in the same pull request"):
        assert phrase in text, (
            f"docs/reference/tether-format.md must state {phrase!r}: a reader writing "
            "their own parser needs the freeze rules, not just the field list"
        )
