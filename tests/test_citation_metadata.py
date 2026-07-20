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


def _citation() -> dict:
    return yaml.safe_load(CITATION.read_text(encoding="utf-8"))


def _authors() -> list[dict]:
    return _citation().get("authors") or []


def test_citation_parses_and_has_authors() -> None:
    """The file is valid YAML and declares a non-empty author list."""
    assert _authors(), "CITATION.cff declares no authors"


def test_at_least_one_named_person() -> None:
    """At least one author is a person with both name parts.

    An author list containing only the ``The Tether Authors`` entity produces a
    citation that credits no identifiable person, which is the state this guard exists
    to prevent recurring. Zenodo and JOSS both reject it.
    """
    people = [a for a in _authors() if "given-names" in a and "family-names" in a]
    assert people, (
        "CITATION.cff names no human author — every entry is an entity. GitHub's "
        "'Cite this repository' output, Zenodo and JOSS all require named people."
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


def test_release_fields_are_consistent() -> None:
    """``version``/``date-released``/``doi`` are either all absent or coherently present.

    They are added together in the release commit. A ``doi`` without a ``version`` would
    make the archived record ambiguous about *what* it identifies.
    """
    cff = _citation()
    if "doi" in cff:
        assert "version" in cff and "date-released" in cff, (
            "CITATION.cff carries a doi but is missing version and/or date-released — "
            "a version DOI must identify a specific released version"
        )
