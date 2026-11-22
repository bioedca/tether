# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The record of each approval of a step that needs a person (ADR-0102 §4, #899).

Four steps of the workflow need a person: ``map.accept``, ``alpha.confirm``,
``gamma.approve`` and ``fit.select`` ([`APPROVAL_STEPS`][]). A person approves one in the
application or with ``tether approve``; a recipe run approves one only with a policy that
the recipe names. Either way one row of ``/settings/approvals`` records it:

* ``step`` and ``subject`` (the map, the condition, the analysis set);
* ``by`` (``person`` or ``policy``) and ``actor``: the person's name, read as the
  ``labeler`` of a curation label is, or the policy text exactly as the recipe gives it;
* ``recipe_sha256`` and ``recipe_version``: the identity of the recipe that asked for the
  approval, empty for an approval that no recipe run asked for;
* ``report``: the evidence, a JSON object of file name to SHA-256 for the report's JSON
  and each of its PNG images. An approval with no report is refused;
* ``timestamp`` (UTC, ISO 8601) and ``app_version``.

``/settings/approvals`` is a lazily created, append-only compound dataset under the frozen
``/settings`` container, as ``/settings/jobs`` is (ADR-0126), so ``SCHEMA_VERSION`` does not
change and a project from before it reads as having no approval. Only an approval is
recorded: a policy that does not approve (``apparent``, ``fail``, ``stop``) writes no row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tether.project.core import Project

__all__ = [
    "APPROVAL_STEPS",
    "Approval",
    "find_approval",
    "read_approvals",
    "record_approval",
    "report_files",
]

#: The four approval steps of ADR-0102 §1, and no other.
APPROVAL_STEPS: tuple[str, ...] = ("map.accept", "alpha.confirm", "gamma.approve", "fit.select")

_SETTINGS = "settings"
_DATASET = "approvals"
_FIELDS = (
    "step",
    "subject",
    "by",
    "actor",
    "recipe_sha256",
    "recipe_version",
    "report",
    "timestamp",
    "app_version",
)


@dataclass(frozen=True)
class Approval:
    """One stored approval, as [`read_approvals`][] returns it.

    ``recipe_version`` is ``0`` and ``recipe_sha256`` is ``""`` when no recipe run asked
    for the approval. ``report`` maps each evidence file name to its SHA-256.
    """

    step: str
    subject: str
    by: str
    actor: str
    recipe_sha256: str
    recipe_version: int
    report: Mapping[str, str]
    timestamp: str
    app_version: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def report_files(report: str | Path) -> dict[str, str]:
    """Return ``{file name: SHA-256}`` for a report's JSON and its PNG images.

    The images are the PNG files beside the JSON whose names start with its stem
    (``gamma.png``, ``gamma-steps.png`` for ``gamma.json``). Raise ``ValueError`` when
    the report is not readable JSON or when an evidence file cannot be read.
    """
    path = Path(report)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError(f"the report must be an existing .json file: {report}")
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"the report {report} is not readable JSON: {exc}") from exc
    evidence = [path]
    for image in sorted(path.parent.glob("*.png")):
        if image.stem == path.stem or image.stem.startswith(f"{path.stem}-"):
            evidence.append(image)
    files = {}
    for item in evidence:
        try:
            files[item.name] = _sha256(item)
        except OSError as exc:
            raise ValueError(f"cannot read approval evidence {item}: {exc}") from exc
    return files


def _app_version() -> str:
    from tether.io.schema import _app_version as version  # noqa: PLC0415

    return version()


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def record_approval(
    project: Project,
    *,
    step: str,
    subject: str,
    by: str,
    actor: str,
    report: str | Path,
    recipe_sha256: str = "",
    recipe_version: int = 0,
) -> Approval:
    """Append one approval to ``/settings/approvals`` and return it.

    Needs a single-writer lock retained by ``project`` until the approval is stored.
    Accepts a step in [`APPROVAL_STEPS`][], a nonempty subject and actor, ``person`` or
    ``policy`` as ``by``, and readable evidence from [`report_files`][]. Invalid values
    raise ``ValueError``.
    """
    import h5py  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from tether.project import lock  # noqa: PLC0415

    if step not in APPROVAL_STEPS:
        raise ValueError(f"unknown approval step {step!r} (known: {', '.join(APPROVAL_STEPS)})")
    if not subject.strip():
        raise ValueError("an approval needs a subject")
    if by not in ("person", "policy"):
        raise ValueError(f"an approval is by a 'person' or a 'policy', not {by!r}")
    if not actor.strip():
        raise ValueError(f"a {by} approval needs a name or a policy text")
    nonce = project._assert_held_lock()
    held = project._held_lock
    assert held is not None  # established by _assert_held_lock
    files = report_files(report)
    approval = Approval(
        step=step,
        subject=subject,
        by=by,
        actor=actor,
        recipe_sha256=recipe_sha256,
        recipe_version=int(recipe_version),
        report=files,
        timestamp=datetime.now(UTC).isoformat(timespec="microseconds"),
        app_version=_app_version(),
    )
    row = {
        **{k: str(getattr(approval, k)) for k in _FIELDS},
        "report": json.dumps(files, sort_keys=True),
    }
    string = h5py.string_dtype(encoding="utf-8")
    dtype = np.dtype([(name, string) for name in _FIELDS])
    block = np.zeros(1, dtype=dtype)
    for name in _FIELDS:
        block[name][0] = row[name]
    with lock.holding(project.path, held):
        project._assert_writable()
        with h5py.File(project.path, "r+") as f:
            project._assert_held_lock(expected_nonce=nonce)
            settings = f[_SETTINGS]
            if _DATASET not in settings:
                settings.create_dataset(_DATASET, shape=(0,), maxshape=(None,), dtype=dtype)
            ds = settings[_DATASET]
            n0 = ds.shape[0]
            ds.resize((n0 + 1,))
            try:
                ds[n0:] = block
            except BaseException:
                ds.resize((n0,))
                raise
    return approval


def read_approvals(path: str | Path) -> list[Approval]:
    """Return every approval of the project at ``path``, oldest first (a read)."""
    import h5py  # noqa: PLC0415

    with h5py.File(path, "r") as f:
        settings = f.get(_SETTINGS)
        if settings is None or _DATASET not in settings:
            return []
        data = settings[_DATASET][()]
    out = []
    for raw in data:
        row = {name: _text(raw[name]) for name in _FIELDS}
        out.append(
            Approval(
                **{
                    **row,
                    "recipe_version": int(row["recipe_version"] or 0),
                    "report": json.loads(row["report"] or "{}"),
                }
            )
        )
    return out


def find_approval(path: str | Path, step: str, subject: str) -> Approval | None:
    """Return the latest approval of ``step`` for ``subject``, or ``None``."""
    for approval in reversed(read_approvals(path)):
        if approval.step == step and approval.subject == subject:
            return approval
    return None
