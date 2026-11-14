# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``codemeta.json`` describes the same release as ``CITATION.cff`` (#597).

``CITATION.cff`` is the single source of the citation; ``codemeta.json`` repeats part of it
for the readers that want CodeMeta (Software Heritage, research-software registries, FAIR
assessment tools). A field that both files carry and that differs would publish two
descriptions of one archive, so every shared field is compared here. ``yaml`` and ``json``
only, so the guard adds no dependency.
"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path

import pytest
import yaml  # provided by the base conda-lock (a mkdocs dependency)

_ROOT = Path(__file__).resolve().parents[1]

#: The terms of the CodeMeta 3.0 context, copied from
#: https://raw.githubusercontent.com/codemeta/codemeta/3.0/codemeta.jsonld
#: (SHA-256 ecf6cfff1f701f5b6b188b9925707c44e6c86134f47aecc6663baf7f6757473f, read
#: 2026-11-14). A key outside this set is not defined by the context and would expand to
#: nothing a CodeMeta reader understands. The 83 entries include the ``type``/``id`` aliases
#: and the ``schema``/``codemeta`` prefixes.
_TERMS = (
    "Organization Person Review Role SoftwareApplication SoftwareSourceCode Text URL address "
    "affiliation applicationCategory applicationSubCategory author buildInstructions "
    "citation codeRepository codemeta continuousIntegration contributor copyrightHolder "
    "copyrightYear dateCreated dateModified datePublished description developmentStatus "
    "downloadUrl editor email embargoEndDate encoding endDate familyName fileFormat fileSize "
    "funder funding givenName hasPart hasSourceCode id identifier installUrl "
    "isAccessibleForFree isPartOf isSourceCodeOf issueTracker keywords license maintainer "
    "memoryRequirements name operatingSystem permissions position processorRequirements "
    "producer programmingLanguage provider publisher readme referencePublication relatedLink "
    "releaseNotes review reviewAspect reviewBody roleName runtimePlatform sameAs schema "
    "softwareHelp softwareRequirements softwareSuggestions softwareVersion sponsor startDate "
    "storageRequirements supportingData targetProduct type url version "
)
_CODEMETA_3_TERMS = frozenset(_TERMS.split())
_JSONLD_KEYWORDS = frozenset({"@context", "@type", "@id"})


def _cff() -> dict:
    return yaml.safe_load((_ROOT / "CITATION.cff").read_text(encoding="utf-8"))


def _codemeta() -> dict:
    return json.loads((_ROOT / "codemeta.json").read_text(encoding="utf-8"))


def _cff_authors(cff: dict) -> list[tuple]:
    return [
        (a["name"],) if "name" in a else (a["given-names"], a["family-names"], a.get("orcid"))
        for a in cff.get("authors") or []
    ]


def _codemeta_authors(cm: dict) -> list[tuple]:
    return [
        (a["name"],)
        if a.get("@type") == "Organization"
        else (a.get("givenName"), a.get("familyName"), a.get("@id"))
        for a in cm.get("author") or []
    ]


def _mismatches(cff: dict, cm: dict) -> list[str]:
    """The shared fields whose values differ, by their CITATION.cff name."""
    released = cff.get("date-released")
    pairs = {
        "title": (cff.get("title"), cm.get("name")),
        "abstract": (" ".join(str(cff.get("abstract")).split()), cm.get("description")),
        "version": (cff.get("version"), cm.get("version")),
        "date-released": (str(released) if released else None, cm.get("datePublished")),
        "license": (f"https://spdx.org/licenses/{cff.get('license')}", cm.get("license")),
        "doi": (f"https://doi.org/{cff.get('doi')}", cm.get("identifier")),
        "authors": (_cff_authors(cff), _codemeta_authors(cm)),
        "repository-code": (cff.get("repository-code"), cm.get("codeRepository")),
        "url": (cff.get("url"), cm.get("url")),
        "keywords": (cff.get("keywords"), cm.get("keywords")),
    }
    return [name for name, (left, right) in pairs.items() if left != right]


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node).union(*(_keys(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_keys(v) for v in node))
    return set()


def test_codemeta_uses_only_codemeta_3_terms() -> None:
    assert len(_CODEMETA_3_TERMS) == 83  # the copied context, complete
    cm = _codemeta()
    assert cm["@context"] == "https://w3id.org/codemeta/3.0"
    assert cm["@type"] == "SoftwareSourceCode"
    unknown = _keys(cm) - _CODEMETA_3_TERMS - _JSONLD_KEYWORDS
    assert not unknown, f"codemeta.json keys the CodeMeta 3.0 context does not define: {unknown}"


def test_codemeta_matches_citation_cff() -> None:
    assert _mismatches(_cff(), _codemeta()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Tetherr"),
        ("abstract", "Another tool."),
        ("version", "9.9.9"),
        ("date-released", "1999-01-01"),
        ("license", "MIT"),
        ("doi", "10.5281/zenodo.1"),
        ("repository-code", "https://example.org/fork"),
        ("url", "https://example.org/"),
        ("keywords", ["smFRET"]),
    ],
)
def test_a_changed_shared_field_is_caught(field: str, value: object) -> None:
    cff = _cff()
    cff[field] = value
    assert _mismatches(cff, _codemeta()) == [field]


def test_a_changed_orcid_is_caught() -> None:
    cff = copy.deepcopy(_cff())
    person = next(a for a in cff["authors"] if "orcid" in a)
    person["orcid"] = "https://orcid.org/0000-0000-0000-0000"
    assert _mismatches(cff, _codemeta()) == ["authors"]


def test_python_requirement_and_package_urls() -> None:
    """codemeta's Python requirement is pyproject's; the package URLs name docs, changelog, DOI."""
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    python = next(r for r in _codemeta()["softwareRequirements"] if r["name"] == "Python")
    assert python["version"] == project["requires-python"]
    urls = project["urls"]
    assert urls["Documentation"] == _cff()["url"]
    assert urls["DOI"] == f"https://doi.org/{_cff()['doi']}"
    assert urls["Changelog"] == _codemeta()["releaseNotes"]


def test_no_zenodo_json() -> None:
    """Zenodo reads CITATION.cff; a .zenodo.json would override it and become a second source."""
    assert not (_ROOT / ".zenodo.json").exists()
