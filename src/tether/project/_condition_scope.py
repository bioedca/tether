# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Group the molecules of a correction pass by their condition (#624, ADR-0068).

The leakage α and the γ of PRD §7.2 are estimated **per condition inside the project**: each
group of ``/molecules`` rows sharing one ``condition_id`` gets the estimate the same code gives
when that condition is alone in a project. This module holds only the grouping and the shape of
the per-condition provenance, so the three passes
([`tether.project.leakage`][], [`tether.project.gamma`][], [`tether.project.correct`][])
group the rows the same way.

The per-condition provenance is one additive attribute, ``conditions``, on each pass's
``/settings`` group: a JSON list with one object for each condition, in the order the condition
first appears in ``/molecules``. Each object names its ``condition_id``; later records (the
factor source and version of #926) add keys to these objects rather than new attributes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence

import numpy as np

__all__ = [
    "CONDITIONS_ATTR",
    "condition_ids",
    "finite_or_none",
    "group_by_condition",
    "read_conditions",
    "write_conditions",
]

#: The per-condition provenance attribute on ``/settings/leakage``, ``/settings/gamma`` and
#: ``/settings/correction``.
CONDITIONS_ATTR = "conditions"


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def condition_ids(table: np.ndarray) -> list[str]:
    """Return each ``/molecules`` row's ``condition_id`` as text (``""`` when unassigned)."""
    return [_text(v) for v in table["condition_id"]]


def group_by_condition(ids: Sequence[str], rows: Iterable[int]) -> dict[str, list[int]]:
    """Return ``rows`` grouped by ``ids[row]``, each group and the groups in first-seen order.

    An unassigned ``condition_id`` (``""``) is a group of its own, so a project whose molecules
    carry no condition gives one group, the same rows the pass read before #624.
    """
    groups: dict[str, list[int]] = {}
    for row in rows:
        groups.setdefault(ids[row], []).append(row)
    return groups


def write_conditions(group: object, entries: Sequence[Mapping[str, object]]) -> None:
    """Stamp the per-condition provenance on an open ``/settings`` child group."""
    attrs = group.attrs  # type: ignore[attr-defined]
    attrs[CONDITIONS_ATTR] = json.dumps([dict(e) for e in entries], allow_nan=False)
    attrs["n_conditions"] = len(entries)


def read_conditions(group: object | None) -> list[dict[str, object]] | None:
    """Return the per-condition provenance of an open group, or ``None`` when it has none."""
    if group is None:
        return None
    raw = group.attrs.get(CONDITIONS_ATTR)  # type: ignore[attr-defined]
    if raw is None:
        return None
    return list(json.loads(_text(raw)))


def finite_or_none(value: float | None) -> float | None:
    """Return ``value`` as a JSON-safe float, or ``None`` when it is missing or not finite."""
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None
