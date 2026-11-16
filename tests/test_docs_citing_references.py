# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``docs/citing.md`` cites only references it lists, and ``CITATION.cff`` holds every one (#534).

The page tells a citing user which papers to cite for each step, by short keys such as
``Lee 2005``. Two statements on the page are checkable, and both broke silently before:

* every key in the two "Cite" columns names an entry of the page's own References list, so a
  reader never meets a citation they cannot resolve; and
* the page says the ``references:`` list in ``CITATION.cff`` "holds them all", so every DOI in
  that References list is also a ``CITATION.cff`` reference.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import yaml  # provided by the base conda-lock (a mkdocs dependency)

ROOT = Path(__file__).resolve().parents[1]
CITING = ROOT / "docs" / "citing.md"
CITATION = ROOT / "CITATION.cff"

#: One References entry: ``- **Key Year.** ... [https://doi.org/DOI](https://doi.org/DOI)``.
_ENTRY_RE = re.compile(r"^- \*\*(?P<key>[^*]+?)\.\*\*(?P<body>.*?)(?=^- \*\*|\Z)", re.M | re.S)
_DOI_LINK_RE = re.compile(r"\]\(https://doi\.org/(?P<doi>[^)\s]+)\)")
#: A short citation key as the tables write it: a name, then a four-digit year. The name may
#: start in lower case (``van de Meent 2014``), so a key is anything that ends in a year.
_KEY_RE = re.compile(r"\S.*? \d{4}")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = re.search(r"^#{2,3} ", text[start + len(heading) :], re.M)
    return text[start : start + len(heading) + nxt.start()] if nxt else text[start:]


def _references() -> dict[str, str]:
    """Map each References key (``"Lee 2005"``) to its DOI."""
    section = _section(CITING.read_text(encoding="utf-8"), "### References")
    out: dict[str, str] = {}
    for match in _ENTRY_RE.finditer(section):
        link = _DOI_LINK_RE.search(match["body"])
        assert link, f"citing.md reference {match['key']!r} has no doi.org link"
        out[match["key"].strip()] = unquote(link["doi"]).lower()
    return out


def _cited_keys() -> set[str]:
    """Every key in the last ("Cite") column of the page's tables."""
    keys: set[str] = set()
    for line in CITING.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[-1] in {"Cite", "---"} or not line.startswith("|"):
            continue
        keys.update(k.strip() for k in cells[-1].split(";") if _KEY_RE.fullmatch(k.strip()))
    return keys


def test_tables_cite_something() -> None:
    """The parse finds the tables; an empty set would make the next test vacuous."""
    assert {"Verma 2024", "Lee 2005", "Crocker 1996", "Zhang 2025", "van de Meent 2014"} <= (
        _cited_keys()
    )


def test_every_cited_key_is_a_listed_reference() -> None:
    missing = sorted(_cited_keys() - set(_references()))
    assert not missing, f"citing.md tables cite keys its References list lacks: {missing}"


def test_every_listed_reference_is_in_citation_cff() -> None:
    cff = yaml.safe_load(CITATION.read_text(encoding="utf-8"))
    cff_dois = {str(r.get("doi", "")).lower() for r in cff.get("references") or []}
    missing = {key: doi for key, doi in _references().items() if doi not in cff_dois}
    assert not missing, (
        f"citing.md says CITATION.cff `references:` holds every paper it lists; these are not "
        f"there (key: DOI): {missing}"
    )
