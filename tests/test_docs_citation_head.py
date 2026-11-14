# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The docs site tells readers and machines how to cite Tether (#599).

FAIR4RS F2 and F4 ask for rich metadata where people search. These tests keep four things true:

* every nav page has its own ``description`` in its front matter, of at most 160 characters,
  so search results show what each page gives the reader and not one site-wide summary;
* the landing page links the citing page; ``tests/test_citation_metadata.py`` checks that the
  DOI it shows is the DOI of ``CITATION.cff``;
* ``scripts/docs_citation_hook.py`` turns ``CITATION.cff`` into a schema.org
  ``SoftwareSourceCode`` record and the ``citation_*`` meta tags, and nothing in the record can
  close its ``<script>`` element;
* the **built** site carries that record on every page and the meta tags on the landing and
  citing pages, with the DOI, license and authors of ``CITATION.cff``. Those tests read the
  folder that ``TETHER_BUILT_SITE`` names, which the required ``docs-build`` job sets after
  ``mkdocs build --strict``; without the variable they skip, because the 3-OS matrix does not
  install MkDocs.

``tests/test_docs_site_brand.py`` checks that the built head loads nothing from another host.

Stdlib plus PyYAML (base lock and the docs toolchain both carry it).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from _docs_source import split_front_matter

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
CFF = _REPO / "CITATION.cff"
HOOK = _REPO / "scripts" / "docs_citation_hook.py"
TEMPLATE = _REPO / "overrides" / "main.html"

#: Search engines cut a longer snippet (the limit the issue names).
DESCRIPTION_MAX = 160

_spec = importlib.util.spec_from_file_location("docs_citation_hook", HOOK)
assert _spec is not None and _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hook
_spec.loader.exec_module(hook)


def _config() -> dict:
    return yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))


def _nav_pages(entries: object) -> list[str]:
    if isinstance(entries, str):
        return [entries]
    if isinstance(entries, list):
        return [page for entry in entries for page in _nav_pages(entry)]
    if isinstance(entries, dict):
        return [page for value in entries.values() for page in _nav_pages(value)]
    return []


NAV_PAGES = _nav_pages(_config()["nav"])


def _front_matter(page: str) -> dict:
    raw, _ = split_front_matter((DOCS / page).read_text(encoding="utf-8"))
    return (yaml.safe_load(raw) or {}) if raw else {}


def _cff() -> dict:
    return hook.load_cff(CFF)


# --- Page descriptions -----------------------------------------------------------------------


def test_the_nav_is_read() -> None:
    assert len(NAV_PAGES) > 30, NAV_PAGES
    assert "index.md" in NAV_PAGES and "citing.md" in NAV_PAGES


@pytest.mark.parametrize("page", NAV_PAGES)
def test_every_nav_page_has_a_short_plain_description(page: str) -> None:
    description = _front_matter(page).get("description")
    assert isinstance(description, str) and description.strip(), f"{page} has no description"
    assert len(description) <= DESCRIPTION_MAX, (page, len(description))
    # Material writes the value into a quoted attribute without escaping it.
    assert not set(description) & set('"<>&'), page
    assert description != _config()["site_description"], page


def test_no_two_nav_pages_share_a_description() -> None:
    seen: dict[str, list[str]] = {}
    for page in NAV_PAGES:
        seen.setdefault(_front_matter(page).get("description", ""), []).append(page)
    assert {text: pages for text, pages in seen.items() if len(pages) > 1} == {}


def test_front_matter_is_split_as_mkdocs_splits_it() -> None:
    meta, body = split_front_matter('---\ndescription: "A: b."\n---\n\n# Title\n')
    assert yaml.safe_load(meta) == {"description": "A: b."}
    assert body == "# Title\n"
    assert split_front_matter("# Title\n\n---\n") == ("", "# Title\n\n---\n")


# --- The landing page ------------------------------------------------------------------------


def test_the_landing_page_links_the_citing_page() -> None:
    _, body = split_front_matter((DOCS / "index.md").read_text(encoding="utf-8"))
    section = body.split("## Cite Tether", 1)
    assert len(section) == 2, "docs/index.md has no 'Cite Tether' section"
    text = section[1].split("\n## ", 1)[0]
    assert "](citing.md)" in text
    assert f"https://doi.org/{_cff()['doi']}" in text


# --- The hook --------------------------------------------------------------------------------


def _person_names(cff: dict) -> list[str]:
    names = []
    for author in cff["authors"]:
        if author.get("family-names") or author.get("given-names"):
            names.append(f"{author['given-names']} {author['family-names']}")
        else:
            names.append(author["name"])
    return names


def test_the_record_carries_the_doi_license_and_authors_of_the_citation_file() -> None:
    cff = _cff()
    record = hook.json_ld(cff)
    assert record["@context"] == "https://schema.org"
    assert record["@type"] == "SoftwareSourceCode"
    assert record["name"] == cff["title"]
    assert record["identifier"] == f"https://doi.org/{cff['doi']}"
    assert record["license"] == f"https://spdx.org/licenses/{cff['license']}"
    assert record["codeRepository"] == cff["repository-code"]
    assert [a["name"] for a in record["author"]] == _person_names(cff)
    orcids = [a.get("orcid") for a in cff["authors"]]
    assert [a.get("@id") for a in record["author"]] == orcids
    # The head is published on every page; the e-mail addresses stay in CITATION.cff.
    emails = {a["email"] for a in cff["authors"] if a.get("email")}
    assert emails and not any(email in json.dumps(record) for email in emails)


def test_the_meta_tags_name_each_author_family_name_first() -> None:
    cff = _cff()
    tags = hook.citation_meta(cff)
    assert tags[0] == ("citation_title", cff["title"])
    authors = [content for name, content in tags if name == "citation_author"]
    assert authors[0] == f"{cff['authors'][0]['family-names']}, {cff['authors'][0]['given-names']}"
    assert len(authors) == len(cff["authors"])
    assert ("citation_doi", cff["doi"]) in tags
    year, month, day = str(cff["date-released"]).split("-")
    assert ("citation_publication_date", f"{year}/{month}/{day}") in tags


def test_no_value_can_close_the_script_element() -> None:
    record = {"name": "</script><!-- & -->"}
    text = hook.script_json(record)
    assert not set(text) & set("<>&")
    assert json.loads(text) == record


def test_a_citation_file_without_a_doi_stops_the_build(tmp_path: Path) -> None:
    cff = tmp_path / "CITATION.cff"
    cff.write_text('title: "X"\nlicense: MIT\nauthors:\n  - name: "Y"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="doi"):
        hook.load_cff(cff)


def test_on_config_reads_the_citation_file_beside_mkdocs_yml() -> None:
    config = SimpleNamespace(config_file_path=str(_REPO / "mkdocs.yml"), extra={})
    assert hook.on_config(config) is config
    citation = config.extra["citation"]
    assert json.loads(citation["json_ld"]) == hook.json_ld(_cff())
    assert citation["pages"] == ["index.md", "citing.md"]


def test_mkdocs_loads_the_hook_and_the_template_extends_the_head() -> None:
    config = _config()
    assert "scripts/docs_citation_hook.py" in config["hooks"]
    assert (_REPO / config["theme"]["custom_dir"] / "main.html") == TEMPLATE
    template = TEMPLATE.read_text(encoding="utf-8")
    assert '{% extends "base.html" %}' in template
    assert "{% block extrahead %}" in template and "{{ super() }}" in template


# --- The built site --------------------------------------------------------------------------


@pytest.fixture
def built_site() -> Path:
    named = os.environ.get("TETHER_BUILT_SITE")
    if not named:
        pytest.skip("TETHER_BUILT_SITE is not set; the docs-build job sets it after the build")
    site = Path(named)
    assert (site / "index.html").is_file(), f"no built site at {site}"
    return site


class _Head(HTMLParser):
    """The meta tags and the JSON-LD scripts of a page's ``<head>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[tuple[str, str]] = []
        self.json_ld: list[str] = []
        self._in_head = False
        self._in_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "head":
            self._in_head = True
        elif self._in_head and tag == "meta" and values.get("name"):
            self.meta.append((values["name"] or "", values.get("content") or ""))
        elif self._in_head and tag == "script" and values.get("type") == "application/ld+json":
            self._in_ld = True
            self.json_ld.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False
        elif tag == "script":
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self.json_ld[-1] += data


def _built(site: Path, page: str) -> Path:
    stem = page.removesuffix(".md")
    parent, _, name = stem.rpartition("/")
    folder = Path(parent) if name in ("index", "README") else Path(stem)
    return site / folder / "index.html"


def _head(site: Path, page: str) -> _Head:
    parser = _Head()
    parser.feed(_built(site, page).read_text(encoding="utf-8"))
    return parser


def test_the_built_heads_carry_the_descriptions_and_the_citation_metadata(
    built_site: Path,
) -> None:
    # One test for every page, not one for each: on the 3-OS legs, where no site is built,
    # it is one entry in tests/skip_budget.toml and one skip, not one skip per nav page.
    cff = _cff()
    record = json.loads(_head(built_site, "index.md").json_ld[0])
    assert record["@type"] == "SoftwareSourceCode"
    assert record["identifier"] == f"https://doi.org/{cff['doi']}"
    assert record["license"] == f"https://spdx.org/licenses/{cff['license']}"
    assert [a["name"] for a in record["author"]] == _person_names(cff)

    wrong: dict[str, list[str]] = {}
    for page in NAV_PAGES:
        head = _head(built_site, page)
        problems = []
        descriptions = [content for name, content in head.meta if name == "description"]
        if descriptions != [_front_matter(page)["description"]]:
            problems.append(f"description {descriptions}")
        if [json.loads(text) for text in head.json_ld] != [hook.json_ld(cff)]:
            problems.append(f"{len(head.json_ld)} JSON-LD records, or not the CITATION.cff one")
        tags = [(name, content) for name, content in head.meta if name.startswith("citation_")]
        if tags != (hook.citation_meta(cff) if page in hook.CITATION_PAGES else []):
            problems.append(f"citation_* tags {tags}")
        if problems:
            wrong[page] = problems
    assert wrong == {}
