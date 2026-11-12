# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``CITATION.cff`` stays a *usable* citation, not merely a present file (PRD §12.7).

GitHub's "Cite this repository" button, Zenodo and JOSS all read this file, and all
three need identifiable human authors — an entity-only author list credits nobody, and
the credit is unrecoverable once papers start citing a published archive.

These are structural guards runnable on the base 3-OS ``test`` matrix. They are not a
full CFF schema validation (``cffconvert`` is not in the base conda-lock); they encode
the specific ways this file has actually been wrong:

* it carried no named person at all, only the ``The Tether Authors`` entity; and
* that entity carried ``affiliation``, which CFF 1.2.0 allows on a *person* but not on
  an *entity* — so the file failed schema validation while looking perfectly reasonable.

A key that is present but empty is as unusable as a missing one, so the guards check
values, not keys (#751).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml  # provided by the base conda-lock (a mkdocs dependency)

CITATION = Path(__file__).resolve().parents[1] / "CITATION.cff"

# CFF 1.2.0 "entity" accepts these keys; notably NOT `affiliation`, which is person-only.
_ENTITY_KEYS = {
    "address",
    "alias",
    "city",
    "country",
    "date-end",
    "date-start",
    "email",
    "fax",
    "location",
    "name",
    "orcid",
    "post-code",
    "region",
    "tel",
    "website",
}

_ORCID_RE = re.compile(r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")

#: Tether is archived on Zenodo, so its DOI is always a Zenodo one (ADR and file comments).
_ZENODO_DOI_RE = re.compile(r"10\.5281/zenodo\.\d+")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The pages the file's own comment says "carry the same value" as its ``doi``.
_DOI_MIRRORS = ("README.md", "docs/citing.md")


def _citation() -> dict:
    return yaml.safe_load(CITATION.read_text(encoding="utf-8"))


def _authors() -> list[dict]:
    return _citation().get("authors") or []


def _filled(value: object) -> bool:
    """A scalar that holds text after stripping. ``""``, ``"  "`` and ``None`` do not."""
    return value is not None and bool(str(value).strip())


def test_citation_parses_and_has_authors() -> None:
    """The file is valid YAML and declares a non-empty author list."""
    assert _authors(), "CITATION.cff declares no authors"


def test_at_least_one_named_person() -> None:
    """At least one author is a person with both name parts.

    An author list containing only the ``The Tether Authors`` entity produces a
    citation that credits no identifiable person, which is the state this guard exists
    to prevent recurring. Zenodo and JOSS both reject it.
    """
    people = [
        a for a in _authors() if _filled(a.get("given-names")) and _filled(a.get("family-names"))
    ]
    assert people, (
        "CITATION.cff names no human author with both name parts filled in — every entry "
        "is an entity or has an empty name. GitHub's 'Cite this repository' output, Zenodo "
        "and JOSS all require named people."
    )


def test_entity_authors_carry_no_person_only_fields() -> None:
    """Entity authors use only entity-legal keys.

    ``affiliation`` on an entity is the exact defect that made this file fail CFF
    schema validation: it matches neither the person schema (which forbids ``name``)
    nor the entity schema (which forbids ``affiliation``).
    """
    offenders = {
        a.get("name"): sorted(set(a) - _ENTITY_KEYS)
        for a in _authors()
        if "name" in a and set(a) - _ENTITY_KEYS
    }
    assert not offenders, (
        "these entity authors carry person-only CFF fields and will fail schema "
        f"validation — (entity, illegal keys): {offenders}"
    )


def test_orcids_are_well_formed() -> None:
    """Every ORCID present is a full https://orcid.org/ URI with a valid checksum slot.

    CFF requires the URI form, not the bare 0000-0000-0000-0000 identifier.
    """
    bad = [
        (a.get("family-names") or a.get("name"), a["orcid"])
        for a in _authors()
        if "orcid" in a and not _ORCID_RE.match(str(a["orcid"]))
    ]
    assert not bad, f"malformed ORCID values (must be https://orcid.org/XXXX-...): {bad}"


def test_release_fields_are_all_or_nothing() -> None:
    """``version``/``date-released``/``doi`` are either all absent or all present.

    They are added together in the release commit, so the check has to run in **both**
    directions. A ``doi`` without a ``version`` leaves the archived record ambiguous
    about what it identifies; a ``version`` without a ``doi`` is the likelier mistake —
    it ships a release whose citation cannot be resolved, silently, because a
    one-directional guard has nothing to fire on.

    Before the first release all three are absent, which is the valid pre-release state.
    """
    cff = _citation()
    release_fields = ("version", "date-released", "doi")
    present = [k for k in release_fields if k in cff]
    assert len(present) in (0, len(release_fields)), (
        "CITATION.cff release fields must be added together — present: "
        f"{present}, missing: {[k for k in release_fields if k not in cff]}. "
        "See the ordered release procedure in the file's own comments."
    )


def test_release_fields_hold_usable_values() -> None:
    """When the release fields are present, each one holds a value a reader can use.

    The all-or-nothing guard above checks only that the keys exist, so ``version: ""`` or
    ``doi: ""`` would pass it and still ship a citation that resolves to nothing.
    """
    cff = _citation()
    if "doi" not in cff:
        return  # the pre-release state; the all-or-nothing guard owns that case
    assert _filled(cff.get("version")), f"CITATION.cff `version` is empty: {cff.get('version')!r}"
    released = str(cff.get("date-released") or "").strip()
    assert _ISO_DATE_RE.match(released), (
        f"CITATION.cff `date-released` is not an ISO 8601 date (YYYY-MM-DD): {released!r}"
    )
    doi = str(cff.get("doi") or "").strip()
    assert _ZENODO_DOI_RE.fullmatch(doi), (
        f"CITATION.cff `doi` is not a Zenodo DOI (10.5281/zenodo.N): {doi!r}"
    )


def test_the_doi_matches_the_pages_that_repeat_it() -> None:
    """README.md and docs/citing.md cite the same concept DOI as ``CITATION.cff``.

    The file's own comment says these pages "carry the same value". Nothing else reads
    the file, so a changed DOI in one place and not the others would publish two
    different citations for one archive.
    """
    cff = _citation()
    if "doi" not in cff:
        return
    doi = str(cff["doi"]).strip()
    root = CITATION.parent
    mismatched = {}
    for rel in _DOI_MIRRORS:
        found = set(_ZENODO_DOI_RE.findall((root / rel).read_text(encoding="utf-8")))
        if found != {doi}:
            mismatched[rel] = sorted(found)
    assert not mismatched, (
        f"CITATION.cff cites {doi}, but these pages cite other or no Zenodo DOIs "
        f"(page: DOIs found): {mismatched}"
    )
