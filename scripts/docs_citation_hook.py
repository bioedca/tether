# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""MkDocs hook: the citation metadata of ``CITATION.cff`` in the head of each page (#599).

``mkdocs.yml`` loads this file through its ``hooks:`` setting. At ``on_config`` it reads
``CITATION.cff`` beside ``mkdocs.yml`` and puts two values in ``config.extra.citation``. The
theme override ``overrides/main.html`` writes them into the HTML head:

* ``json_ld``: a schema.org ``SoftwareSourceCode`` record (name, description, license, code
  repository, the concept DOI as ``identifier``, keywords and authors). Every page carries it.
* ``meta``: the ``citation_*`` meta tags that reference managers and scholarly search services
  read (title, one tag for each author, DOI, publication date). Only the pages in ``pages``
  carry them: the landing page and the citing page.

``CITATION.cff`` is the one source. A changed DOI, license or author list goes into the next
build without an edit here or in the template. A ``CITATION.cff`` without a title, a license,
a DOI or an author stops the build, because a head without them would describe nothing.

The values are static text. The head loads no script, font or image from another host, so
the "no third-party asset requests" statement of ``docs/privacy.md`` stays true.

Only PyYAML is imported at module level (MkDocs depends on it, and the base lock carries it),
so ``tests/test_docs_citation_head.py`` can import the builders on the 3-OS matrix, where MkDocs
is not installed.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import yaml

#: The file this hook reads, beside ``mkdocs.yml``.
CFF_NAME = "CITATION.cff"

#: The pages, by source path under ``docs/``, that carry the ``citation_*`` meta tags.
CITATION_PAGES = ("index.md", "citing.md")

#: The fields without which the head would describe nothing.
_REQUIRED = ("title", "license", "doi", "authors")


def load_cff(path: Path) -> dict[str, Any]:
    """``CITATION.cff`` parsed, after a check that the fields the head needs are present."""
    cff = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cff, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    missing = [key for key in _REQUIRED if not cff.get(key)]
    if missing:
        raise ValueError(f"{path} has no {', '.join(missing)}; the page head needs them")
    return cff


def _text(value: object) -> str:
    """A string with each run of whitespace made one space (a folded YAML block keeps one)."""
    return " ".join(str(value).split())


def _person_name(author: dict[str, Any]) -> str:
    return " ".join(
        _text(author[key]) for key in ("given-names", "family-names") if author.get(key)
    )


def _is_person(author: dict[str, Any]) -> bool:
    # CFF 1.2.0: a person has `family-names` or `given-names`; an entity has `name`.
    return bool(author.get("family-names") or author.get("given-names"))


def _json_ld_author(author: dict[str, Any]) -> dict[str, Any]:
    if not _is_person(author):
        return {"@type": "Organization", "name": _text(author["name"])}
    person: dict[str, Any] = {"@type": "Person", "name": _person_name(author)}
    if author.get("orcid"):
        person["@id"] = _text(author["orcid"])
    if author.get("given-names"):
        person["givenName"] = _text(author["given-names"])
    if author.get("family-names"):
        person["familyName"] = _text(author["family-names"])
    if author.get("affiliation"):
        person["affiliation"] = {"@type": "Organization", "name": _text(author["affiliation"])}
    return person


def json_ld(cff: dict[str, Any]) -> dict[str, Any]:
    """The schema.org ``SoftwareSourceCode`` record that ``cff`` describes.

    E-mail addresses are left out on purpose: the page head is published on every page, and
    ``CITATION.cff`` already gives them to anyone who asks for the citation.
    """
    record: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "SoftwareSourceCode",
        "name": _text(cff["title"]),
    }
    if cff.get("abstract"):
        record["description"] = _text(cff["abstract"])
    record["license"] = f"https://spdx.org/licenses/{_text(cff['license'])}"
    if cff.get("repository-code"):
        record["codeRepository"] = _text(cff["repository-code"])
    record["identifier"] = f"https://doi.org/{_text(cff['doi'])}"
    if cff.get("keywords"):
        record["keywords"] = [_text(word) for word in cff["keywords"]]
    record["author"] = [_json_ld_author(author) for author in cff["authors"]]
    return record


def _publication_date(value: object) -> str:
    """``date-released`` in the ``YYYY/MM/DD`` form the ``citation_*`` tags ask for."""
    if isinstance(value, datetime.date):
        return value.strftime("%Y/%m/%d")
    return datetime.date.fromisoformat(_text(value)).strftime("%Y/%m/%d")


def citation_meta(cff: dict[str, Any]) -> list[tuple[str, str]]:
    """The ``citation_*`` meta tags for ``cff``, as ``(name, content)`` pairs in page order."""
    tags = [("citation_title", _text(cff["title"]))]
    for author in cff["authors"]:
        if _is_person(author):
            # "Family, Given" is the form these tags use for a person.
            parts = [author.get("family-names"), author.get("given-names")]
            tags.append(("citation_author", ", ".join(_text(p) for p in parts if p)))
        else:
            tags.append(("citation_author", _text(author["name"])))
    tags.append(("citation_doi", _text(cff["doi"])))
    if cff.get("date-released"):
        tags.append(("citation_publication_date", _publication_date(cff["date-released"])))
    return tags


def script_json(record: dict[str, Any]) -> str:
    """``record`` as JSON that is safe inside a ``<script>`` element.

    ``<``, ``>`` and ``&`` are written as ``\\u`` escapes, so no value can close the element
    or open a comment. A JSON parser reads the escapes back as the same characters.
    """
    text = json.dumps(record, ensure_ascii=False, indent=2)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def citation_context(cff: dict[str, Any]) -> dict[str, Any]:
    """What the theme override reads from ``config.extra.citation``."""
    return {
        "json_ld": script_json(json_ld(cff)),
        "meta": citation_meta(cff),
        "pages": list(CITATION_PAGES),
    }


def on_config(config: Any, **kwargs: Any) -> Any:
    """MkDocs event: read ``CITATION.cff`` and give the theme its citation values."""
    path = Path(config.config_file_path).parent / CFF_NAME
    try:
        cff = load_cff(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # Imported here, so the module imports where MkDocs is not installed.
        from mkdocs.exceptions import PluginError

        raise PluginError(f"citation hook (#599): {exc}") from exc
    config.extra["citation"] = citation_context(cff)
    return config
