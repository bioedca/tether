# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The docs site keeps maintainer-only pages out of the reader's nav (#181).

The nav has two top-level groups, decided on #181: ``Using Tether`` for everything a lab scientist
acts on, and ``Project & maintainers`` for what only the project acts on. These tests pin the
properties that decision exists for, so a later nav edit cannot quietly undo them:

* commands that need write access to ``bioedca/tether`` live only on maintainer pages;
* every nav page except the ``Home`` router opens with a "Who this page is for" statement (#587),
  and every maintainer page says, in its first paragraph, what access it needs;
* the standalone-tMAVEN cross-OS acceptance record keeps its three ``10f4230`` rows with no
  ``pending`` row left, on a maintainer page rather than a user one (it is ADR-0010's manual GUI
  leg, not tracker state);
* the deep-classifier page leads with its setup command and keeps its rationale last, and the
  ``#gpu-cuda-setup`` anchor that ``troubleshooting.md`` links still exists.
* a ``Using Tether`` page cites no section of the product specification and no milestone label,
  and names a test or a private name only inside a link to its file (#590, writing-style
  Rule 10); ``validation.md`` shows evidence, so it may name tests in its own text.

Stdlib plus PyYAML (a mkdocs dependency in the base lock); it reads Markdown as text.
"""

from __future__ import annotations

import fnmatch
import html
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
USER_GROUP = "Using Tether"
MAINTAINER_GROUP = "Project & maintainers"
# The router: it sends each reader to a page, so it needs no reader statement of its own (#587).
_ROUTER = "index.md"
_READER_STATEMENT = "Who this page is for"
_STATEMENT_WINDOW = 5

# Each needs write or admin rights on the repository; a user page must never instruct them.
_MAINTAINER_COMMANDS = ("git tag -s", "gh workflow run", "gh api repos/bioedca")


def _config() -> dict:
    return yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))


def _pages(entries: object) -> list[str]:
    found: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            found += _pages(entry)
    elif isinstance(entries, dict):
        for value in entries.values():
            found += _pages(value)
    elif isinstance(entries, str):
        found.append(entries)
    return found


def _groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for entry in _config()["nav"]:
        assert isinstance(entry, dict) and len(entry) == 1, f"top-level nav entry {entry!r}"
        ((title, children),) = entry.items()
        groups[html.unescape(title)] = _pages(children)
    return groups


def _not_in_nav_patterns() -> list[str]:
    return (_config().get("not_in_nav") or "").split()


def _first_paragraph(page: str) -> str:
    text = (DOCS / page).read_text(encoding="utf-8")
    body = text.split("\n", 1)[1] if text.startswith("# ") else text
    paragraphs = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    return " ".join(paragraphs[0].split()) if paragraphs else ""


def _opening_lines(page: str) -> list[str]:
    """The first non-blank lines after the page's H1, skipping a leading SPDX comment block."""
    lines = (DOCS / page).read_text(encoding="utf-8").splitlines()
    title = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    assert title is not None, f"{page} has no H1 title"
    return [line for line in lines[title + 1 :] if line.strip()][:_STATEMENT_WINDOW]


def test_the_nav_has_exactly_the_two_decided_groups() -> None:
    assert list(_groups()) == [USER_GROUP, MAINTAINER_GROUP]


def test_maintainer_commands_appear_only_on_maintainer_pages() -> None:
    maintainer_pages = set(_groups()[MAINTAINER_GROUP])
    patterns = _not_in_nav_patterns()
    offenders = []
    for path in sorted(DOCS.rglob("*.md")):
        page = path.relative_to(DOCS).as_posix()
        text = path.read_text(encoding="utf-8")
        if not any(command in text for command in _MAINTAINER_COMMANDS):
            continue
        if page in maintainer_pages or any(fnmatch.fnmatch(page, p) for p in patterns):
            continue
        offenders.append(page)
    assert not offenders, f"write-access commands on pages a user reaches: {offenders}"


def test_every_nav_page_except_the_router_opens_with_a_reader_statement() -> None:
    # #587: a reader decides from the opening lines whether a page is for them. The bold lead-in
    # and the how-to guides' ``## Who this page is for`` heading both carry the phrase.
    pages = [page for group in _groups().values() for page in group if page != _ROUTER]
    assert pages, "the nav lists no pages"
    missing = {
        page: _opening_lines(page)[:1]
        for page in pages
        if not any(_READER_STATEMENT in line for line in _opening_lines(page))
    }
    assert not missing, f"nav pages with no reader statement near the top: {missing}"


def test_every_maintainer_page_opens_with_a_reader_statement_naming_its_access() -> None:
    missing = {}
    for page in _groups()[MAINTAINER_GROUP]:
        first = _first_paragraph(page)
        if not first.startswith("**Who this page is for.**") or "access" not in first:
            missing[page] = first[:120]
    assert not missing, f"maintainer pages without a reader statement naming access: {missing}"


_CROSS_OS_RECORD = "maintainers/standalone-tmaven-cross-os-record.md"


def test_the_cross_os_record_keeps_its_rows_on_a_maintainer_page() -> None:
    # The two manual GUI legs ADR-0010 deferred were run and recorded under #543. Only the record
    # itself is read for a ``pending`` row: another page's table may use that word freely (#572).
    record = (DOCS / _CROSS_OS_RECORD).read_text(encoding="utf-8")
    assert "| pending |" not in record, _CROSS_OS_RECORD
    rows = [
        (path.relative_to(DOCS).as_posix(), line)
        for path in sorted(DOCS.rglob("*.md"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ") and "| `10f4230` |" in line
    ]
    assert [line.split("|")[1].strip() for _, line in rows] == [
        "Linux (CI)",
        "Windows 11",
        "Ubuntu on WSL2",
    ], rows
    maintainer_pages = set(_groups()[MAINTAINER_GROUP])
    assert {page for page, _ in rows} <= maintainer_pages, rows


def test_the_deep_classifier_page_leads_with_setup_and_ends_with_rationale() -> None:
    text = (DOCS / "ml" / "deep-classifier.md").read_text(encoding="utf-8")
    first_fence = re.search(r"^```[^\n]*\n(.*?)^```", text, flags=re.MULTILINE | re.DOTALL)
    assert first_fence is not None
    assert first_fence.group(1).startswith("conda-lock install"), first_fence.group(1)

    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    sections = ["Setup", "Using the classifier", "Why a third isolated environment"]
    positions = [headings.index(name) for name in sections]
    assert positions == sorted(positions), headings
    # troubleshooting.md links this anchor; `--strict` fails if the heading moves away from it.
    assert re.search(r"^### GPU \(CUDA\) setup$", text, flags=re.MULTILINE)
    assert "ml/deep-classifier.md#gpu-cuda-setup" in (DOCS / "troubleshooting.md").read_text(
        encoding="utf-8"
    )


# --- References a user can open (#590) ------------------------------------------------
# A "Using Tether" page refers only to what its reader can open (writing-style Rule 10). The
# product specification is kept out of the site, a milestone label means nothing outside the
# tracker, and a test or private name tells a user nothing unless it links to its file.
# Code spans are exempt from the first two checks: they quote code and program output, such
# as an error message that names a specification section. Link targets are exempt too, so a
# link to docs/PRD.md on GitHub is allowed.

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_LINK_TEXT_RE = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)\s]*)\)")
_SPEC_SECTION_RE = re.compile(r"\bPRD\b|§\s*\d")
_MILESTONE_RE = re.compile(r"\bM\d{1,2}(?:\.\d+)?\b")
_TEST_OR_PRIVATE_RE = re.compile(
    r"tests/|test_\w|_[A-Za-z]|tether(?:\.\w+)*\._[A-Za-z]"  # tether.mod._private
)
#: The page that shows evidence: it may name tests in its own text (#590's decision).
_EVIDENCE_PAGE = "validation.md"
#: (page, fragment) pairs a milestone check reads past. Each fragment must still be on its
#: page, so an entry cannot outlive the text it excuses.
_MILESTONE_ALLOWED = (
    # Apple's chip, not a milestone.
    ("install.md", "Apple silicon (M1 or later)"),
    # The value of `$.frozen_at_milestone` in schema/parity_tolerance.json: a label that
    # names one recorded measurement, not a time.
    ("validation.md", "(M0.5, vbconhmm)"),
    ("validation.md", "the M0.5 worst cases"),
    # The D1 promises that #365 settles; rewriting them here would decide #365 for it.
    ("analysis/parity-gallery.md", "switching to corrected E at M3"),
    ("analysis/parity-gallery.md", "(photobleach segmentation at M3)"),
    ("analysis/parity-gallery.md", "in the M2 dock (photobleach segmentation lands at M3)"),
)


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """Each line outside a fenced block, numbered from 1."""
    out: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.split("\n"), start=1):
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append((number, line))
    return out


def _readable(line: str) -> str:
    """``line`` with its code spans and link targets blanked: the words a reader reads."""
    line = _LINK_TEXT_RE.sub(lambda m: f"[{m.group(1)}]()", line)
    return _CODE_SPAN_RE.sub("``", line)


def spec_citations(text: str) -> list[str]:
    """Each line of ``text`` that cites a section of the product specification."""
    return [
        f"{n}: {line.strip()}"
        for n, line in _prose_lines(text)
        if _SPEC_SECTION_RE.search(_readable(line))
    ]


def milestone_labels(text: str, allowed: tuple[str, ...] = ()) -> list[str]:
    """Each line of ``text`` that uses a milestone label, past the ``allowed`` fragments."""
    found = []
    for n, line in _prose_lines(text):
        readable = _readable(line)
        for fragment in allowed:
            readable = readable.replace(_readable(fragment), "")
        if _MILESTONE_RE.search(readable):
            found.append(f"{n}: {line.strip()}")
    return found


def unlinked_names(text: str) -> list[str]:
    """Each code span in ``text`` that names a test or a private name outside a link."""
    found = []
    for n, line in _prose_lines(text):
        linked = [(m.start(1), m.end(1)) for m in _LINK_TEXT_RE.finditer(line)]
        for span in _CODE_SPAN_RE.finditer(line):
            inside = any(a <= span.start() and span.end() <= b for a, b in linked)
            if not inside and _TEST_OR_PRIVATE_RE.match(span.group(1).strip()):
                found.append(f"{n}: {span.group(0)}")
    return found


def _user_page_texts() -> dict[str, str]:
    return {page: (DOCS / page).read_text(encoding="utf-8") for page in _groups()[USER_GROUP]}


def test_no_user_page_cites_a_section_of_the_product_specification() -> None:
    found = {
        page: hits for page, text in _user_page_texts().items() if (hits := spec_citations(text))
    }
    assert not found, f"user pages that cite the product specification by section: {found}"


def test_no_user_page_uses_a_milestone_label() -> None:
    found = {}
    for page, text in _user_page_texts().items():
        allowed = tuple(fragment for where, fragment in _MILESTONE_ALLOWED if where == page)
        if hits := milestone_labels(text, allowed):
            found[page] = hits
    assert not found, f"user pages that use a milestone label: {found}"


def test_every_milestone_exception_is_still_on_its_page() -> None:
    texts = _user_page_texts()
    stale = [
        (page, fragment) for page, fragment in _MILESTONE_ALLOWED if fragment not in texts[page]
    ]
    assert not stale, f"milestone exceptions whose text is gone; delete them: {stale}"


def test_user_pages_name_tests_and_private_names_only_in_links() -> None:
    found = {
        page: hits
        for page, text in _user_page_texts().items()
        if page != _EVIDENCE_PAGE and (hits := unlinked_names(text))
    }
    assert not found, f"test or private names on user pages without a link to their file: {found}"


def test_the_reference_checks_bite() -> None:
    """Each check fails on the text it exists to refuse, and passes its exemptions."""
    assert spec_citations("This page is the reference (PRD §7.8).")
    assert spec_citations("the literal §11.2 numbers")
    assert not spec_citations("The [specification](https://example.org/docs/PRD.md) says so.")
    assert not spec_citations("The error reads `refusing to open (PRD section 5.4).`")
    assert not spec_citations("```\n# PRD §8\n```")
    assert milestone_labels("Extraction writes NaN until M3 estimates it.")
    assert milestone_labels("the M0.5 deferral")
    assert not milestone_labels("Apple silicon (M1 or later)", ("Apple silicon (M1 or later)",))
    assert not milestone_labels('`frozen_at_milestone` `"M8"`')
    assert unlinked_names("asserted by `test_x` in the suite")
    assert unlinked_names("run `tests/test_y.py`")
    assert unlinked_names("built by `_private_helper`")
    assert unlinked_names("see `tether.io.schema._str`")
    assert not unlinked_names("see [`_str()`](https://example.org/src/tether/io/schema.py)")
    assert not unlinked_names("see [`tests/test_y.py`](https://example.org/tests/test_y.py)")
    assert not unlinked_names("the package's `__init__.py`")  # a dunder is not private
