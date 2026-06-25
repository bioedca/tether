# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Filename-to-metadata parser (PRD §5.1, §7.6).

Lab acquisition filenames encode the experimental condition. This module
extracts a **provisional** :class:`ConditionKey` — the chemistry/optics identity
tuple **(construct/variant, dye, ligand + concentration, buffer, temperature,
laser power)** that PRD §5.1 makes the condition identity — plus the
*within-condition* provenance fields (``date``, ``replicate``, source video
index, immobilized-sample concentration) that deliberately vary inside a single
condition.

The parse is **provisional by design**: PRD §7.6 requires filename auto-fill to
be *human-validated* (at M4), so this parser is best-effort. It recovers what the
filename actually carries and leaves everything absent (``""`` / ``None``) for the
M4 validation step to fill — it never fabricates a value it cannot read. Two
movies belong to the same condition iff their :class:`ConditionKey` fields match
(PRD §5.1 referential validation); :meth:`ConditionKey.condition_id` is the stable
provisional id written into ``/molecules.condition_id_provisional`` and, once a
human confirms it, ``/conditions.condition_id`` (PRD §5.1).

Examples
--------
The example-data filenames this targets (PRD Appendix A, ``example-data/``):

* ``Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif`` — a FRET acquisition: construct
  ``Bla UCKOPSB T-box``, ligand ``tRNA`` @ 600 nM, immobilized at 35 pM, video 010.
* ``DeepLASI_DATA_Cy3_only_WCBN_2ndreplicate_15pM_001_2026-06-22_15-33.tdat`` — a
  donor-only (Cy3) leakage-calibration sample (PRD Appendix B), construct ``WCBN``,
  2nd replicate, immobilized at 15 pM, video 001.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePath

__all__ = [
    "ConditionKey",
    "ParsedFilename",
    "parse_filename",
]

# --- Lexical patterns --------------------------------------------------------

#: Deep-LASI / export filename prefixes, stripped before parsing (longest first
#: so ``DeepLASI_MAT_export_`` wins over ``DeepLASI_MAT_``). PRD Appendix A/D.
_PREFIXES: tuple[str, ...] = (
    "DeepLASI_MAT_export_",
    "DeepLASI_DATA_",
    "DeepLASI_MAP_",
    "DeepLASI_MAT_",
)

#: File extensions of the acquisition set (PRD Appendix A/D). Removed wherever
#: they appear because Deep-LASI glues a source ``.tif`` into ``.tdat`` names
#: (e.g. ``..._010.tif2025-07-21_00-00.tdat``).
_EXTENSIONS: tuple[str, ...] = (
    ".tiff",
    ".tif",
    ".tdat",
    ".tmap",
    ".hdf5",
    ".txt",
    ".mat",
)

#: Corrected-intensity export markers (``-donc-accc-w`` = donor-corrected /
#: acceptor-corrected, PRD Appendix A) and other trailing decorations.
_SUFFIXES: tuple[str, ...] = (
    "-donc-accc-w",
    "-donc-accc",
)

#: Trailing wall-clock stamp ``_YYYY-MM-DD_HH-MM`` (optionally ``-SS``).
_TIMESTAMP_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}(?:-\d{2})?$")

#: An embedded compact acquisition date ``YYYYMMDD`` (8 digits as its own token).
_DATE8_RE = re.compile(r"(?:^|_)(\d{8})(?=_|$)")

#: A ``<value><unit>`` concentration token, e.g. ``600nM`` / ``35pM`` / ``1.5uM``.
#: Matched per ``_``-delimited token, so the closing ``\b`` is a real boundary.
_CONC_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>pM|nM|uM|µM|mM|M)$", re.IGNORECASE)

#: ``2ndreplicate`` / ``3rd_replicate`` style replicate markers.
_REPLICATE_RE = re.compile(r"^(?P<n>\d+)(?:st|nd|rd|th)?[-_]?replicate$", re.IGNORECASE)

#: A trailing source video index, 2–4 digits (``010``, ``001``).
_VIDEO_RE = re.compile(r"^\d{2,4}$")

#: Known soluble-ligand tokens: a concentration whose preceding token is one of
#: these is a *ligand* concentration (part of the condition key); any other bare
#: concentration is the immobilized-sample concentration (provenance, not key).
#: Provisional — an unknown ligand defaults to sample concentration and is
#: corrected at the mandatory M4 validation step (PRD §7.6).
_LIGANDS: frozenset[str] = frozenset(
    {"trna", "mrna", "rrna", "trna2", "rna", "dna", "atp", "gtp", "mg", "mgcl2", "ligand"}
)

#: Characters reserved by the canonical-string encoding; scrubbed from values so
#: the round-trip is unambiguous. Provisional values come from filenames and
#: never contain these in practice.
_RESERVED = str.maketrans({"|": "/", "=": "-"})


def _norm(text: str) -> str:
    """Collapse whitespace and strip the canonical-encoding reserved characters."""
    return " ".join(text.split()).translate(_RESERVED).strip()


def _fmt_num(value: float | None) -> str:
    """Round-trip-stable number rendering: ``None`` → ``""``; drop trailing zeros."""
    if value is None:
        return ""
    if value == int(value):
        return str(int(value))
    return repr(value)


def _parse_num(text: str) -> float | None:
    """Inverse of :func:`_fmt_num` (``""`` → ``None``)."""
    return float(text) if text else None


# --- The condition identity key ----------------------------------------------


@dataclass(frozen=True)
class ConditionKey:
    """The condition identity tuple (PRD §5.1).

    These six chemistry/optics fields define condition identity; ``date``,
    ``replicate``, and source file deliberately vary *within* a condition.
    Laser power is part of the key because it scales the intensities that feed
    the leakage-α and γ corrections (PRD §5.1). Fields absent from a filename
    stay ``""`` / ``None`` (provisional; filled at the M4 validation step).
    """

    construct_variant: str = ""
    dye: str = ""
    ligand: str = ""
    ligand_concentration: float | None = None
    ligand_concentration_unit: str = ""
    buffer: str = ""
    temperature_c: float | None = None
    laser_power: float | None = None

    def to_canonical(self) -> str:
        """Serialize to a deterministic, exactly-reversible string.

        Inverse of :meth:`from_canonical`; the pair is a true round-trip
        (:func:`parse_filename` "round-trips a known condition string", PRD §9 M0).
        """
        fields = (
            ("construct", self.construct_variant),
            ("dye", self.dye),
            ("ligand", self.ligand),
            ("ligand_conc", _fmt_num(self.ligand_concentration)),
            ("ligand_unit", self.ligand_concentration_unit),
            ("buffer", self.buffer),
            ("temp_c", _fmt_num(self.temperature_c)),
            ("laser", _fmt_num(self.laser_power)),
        )
        return " | ".join(f"{name}={value}" for name, value in fields)

    @classmethod
    def from_canonical(cls, text: str) -> ConditionKey:
        """Reconstruct a key from :meth:`to_canonical` output."""
        parts: dict[str, str] = {}
        for chunk in text.split(" | "):
            name, _, value = chunk.partition("=")
            parts[name] = value
        return cls(
            construct_variant=parts.get("construct", ""),
            dye=parts.get("dye", ""),
            ligand=parts.get("ligand", ""),
            ligand_concentration=_parse_num(parts.get("ligand_conc", "")),
            ligand_concentration_unit=parts.get("ligand_unit", ""),
            buffer=parts.get("buffer", ""),
            temperature_c=_parse_num(parts.get("temp_c", "")),
            laser_power=_parse_num(parts.get("laser", "")),
        )

    def condition_id(self) -> str:
        """A stable provisional condition id (``cond-<12 hex>`` of the key).

        Two filenames that parse to equal key fields yield the same id, so it
        joins ``/molecules.condition_id_provisional`` to a ``/conditions`` row
        (PRD §5.1). Deterministic across runs/platforms (content hash, no salt).
        """
        digest = sha256(self.to_canonical().encode("utf-8")).hexdigest()
        return f"cond-{digest[:12]}"


@dataclass(frozen=True)
class ParsedFilename:
    """The full provisional parse of one acquisition filename."""

    key: ConditionKey
    sample_concentration: float | None = None
    sample_concentration_unit: str = ""
    date: str = ""
    replicate: str = ""
    video_index: str = ""
    source_filename: str = ""
    stem: str = ""
    unparsed_tokens: tuple[str, ...] = field(default_factory=tuple)

    @property
    def condition_id(self) -> str:
        """Convenience alias for :meth:`ConditionKey.condition_id`."""
        return self.key.condition_id()


# --- The parser --------------------------------------------------------------


def _strip_decorations(name: str) -> tuple[str, str]:
    """Strip extension/prefix/suffix/timestamp; return ``(stem, iso_date)``."""
    stem = PurePath(name).name
    # Replace every acquisition extension (wherever it occurs) with a separator,
    # longest first so ``.tiff`` beats ``.tif``: Deep-LASI glues a source ``.tif``
    # mid-name (``..._010.tif2025-07-21...``), so a bare removal would fuse the
    # video index onto the timestamp. A separator keeps the tokens distinct.
    for ext in _EXTENSIONS:
        stem = re.sub(re.escape(ext), "_", stem, flags=re.IGNORECASE)
    stem = stem.strip("_ ")
    for prefix in _PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    for suffix in _SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    iso_date = ""
    ts = _TIMESTAMP_RE.search(stem)
    if ts:
        iso_date = ts.group(1)
        stem = stem[: ts.start()]
    d8 = _DATE8_RE.search(stem)
    if d8:
        raw = d8.group(1)
        iso_date = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        stem = (stem[: d8.start()] + "_" + stem[d8.end() :]).strip("_")
    return stem.strip("_ "), iso_date


def _detect_dye(tokens: list[str]) -> tuple[str, set[int]]:
    """Recognize a donor-only ``Cy3 only`` marker; return ``(dye, consumed)``."""
    lowered = [t.lower() for t in tokens]
    consumed: set[int] = set()
    for i, tok in enumerate(lowered):
        if tok in {"cy3", "cy5"} and i + 1 < len(lowered) and lowered[i + 1] == "only":
            consumed.update({i, i + 1})
            return tok.title(), consumed
        if tok in {"cy3only", "cy5only"}:
            consumed.add(i)
            return tok[:3].title(), consumed
    return "", consumed


def parse_filename(name: str) -> ParsedFilename:
    """Parse an acquisition filename into a provisional :class:`ParsedFilename`.

    Best-effort and provisional (PRD §7.6): it extracts what the filename carries
    and leaves the rest empty for human validation at M4. Never raises on an
    unrecognized name — an unparseable stem yields an empty key with the raw
    tokens preserved in :attr:`ParsedFilename.unparsed_tokens` for provenance.
    """
    stem, iso_date = _strip_decorations(name)
    tokens = [t for t in re.split(r"[_\s]+", stem) if t]

    consumed: set[int] = set()
    dye, dye_consumed = _detect_dye(tokens)
    consumed |= dye_consumed

    replicate = ""
    video_index = ""
    ligand = ""
    ligand_conc: float | None = None
    ligand_unit = ""
    sample_conc: float | None = None
    sample_unit = ""

    for i, tok in enumerate(tokens):
        if i in consumed:
            continue
        rep = _REPLICATE_RE.match(tok)
        if rep:
            replicate = rep.group("n")
            consumed.add(i)
            continue
        conc = _CONC_RE.match(tok)
        if conc:
            value = float(conc.group("value"))
            unit = conc.group("unit")
            prev = tokens[i - 1].lower() if i > 0 else ""
            if prev in _LIGANDS and (i - 1) not in consumed:
                ligand = tokens[i - 1]
                ligand_conc, ligand_unit = value, unit
                consumed.update({i - 1, i})
            elif sample_conc is None:
                sample_conc, sample_unit = value, unit
                consumed.add(i)
            else:
                consumed.add(i)
            continue

    # A trailing pure-digit token is the source video index (consume the *last*
    # such unconsumed token so a numeric construct token earlier is preserved).
    for i in range(len(tokens) - 1, -1, -1):
        if i not in consumed and _VIDEO_RE.match(tokens[i]):
            video_index = tokens[i]
            consumed.add(i)
            break

    remaining = [tok for i, tok in enumerate(tokens) if i not in consumed]
    construct_variant = _norm(" ".join(remaining))

    key = ConditionKey(
        construct_variant=construct_variant,
        dye=_norm(dye),
        ligand=_norm(ligand),
        ligand_concentration=ligand_conc,
        ligand_concentration_unit=ligand_unit,
    )
    return ParsedFilename(
        key=key,
        sample_concentration=sample_conc,
        sample_concentration_unit=sample_unit,
        date=iso_date,
        replicate=replicate,
        video_index=video_index,
        source_filename=PurePath(name).name,
        stem=stem,
        unparsed_tokens=tuple(remaining),
    )
