# SPDX-FileCopyrightText: 2026 The Tether Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The glossary is a list of short definitions that a reader can scan (#595).

``docs/glossary.md`` is a reference page in the Diátaxis sense: it states, it does not explain.
These tests pin the acceptance criteria of #595:

* every entry opens with a definition of at most two sentences, at most 25 words each, that holds
  no code identifier (no code span);
* no entry is longer than 12 source lines, from its heading to its last line;
* the "All terms, A to Z" list links every entry, and lists its terms in alphabetical order;
* every anchor that the page served before #595 still resolves, because other pages and old
  links point at entries;
* each passage that #595 moved off the glossary is on the page that received it, and is no
  longer on the glossary.

The sentence splitter and the word count are the ones of ``tests/test_docs_style_budget.py``,
and the heading slugs are the ones of ``tests/test_docs_links.py``, so the three tests agree.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from _docs_source import split_front_matter
from test_docs_links import _anchors, _heading_text, _slugify
from test_docs_style_budget import sentences, words

DOCS = Path(__file__).resolve().parents[1] / "docs"
GLOSSARY = DOCS / "glossary.md"
INDEX_HEADING = "## All terms, A to Z"
MAX_ENTRY_LINES = 12
MAX_DEFINITION_SENTENCES = 2
MAX_DEFINITION_WORDS = 25

#: The heading ids that ``docs/glossary.md`` served before #595 (``main`` at 712e4e0, read from
#: ``mkdocs build``). A rewrite of an entry keeps its heading, so each of these still resolves.
ANCHORS_BEFORE_595 = (
    "proximity-ratio",
    "apparent-e",
    "corrected-e-accurate-fret",
    "correction-order",
    "leakage-bleedthrough",
    "gamma-detection-correction",
    "delta-direct-excitation",
    "correction_method",
    "withheld-factor-nan-sentinel",
    "photobleach-frames-first-bleach-frame",
    "analysis-window",
    "window-always-with-a-qualifier",
    "curation-label-category-quality-class",
    "tirf",
    "alex-pie",
    "dual-view",
    "aperture",
    "background-subtracted-intensity-corrected",
    "donor-anchored-colocalization",
    "partner-in-both-colocalization",
    "registration-tmap",
    "rms-gate-low-confidence-registration",
    "a-trous-starlet-wavelet-mad",
    "idealization",
    "vbfret-ebfret-consensus-vb-hmm",
    "elbo",
    "state",
    "viterbi-path-state-path",
    "no_state",
    "dwell-time-dwell-time-survival",
    "tdp-transition-density-plot",
    "post-synchronization",
    "staleness-stale-fresh-live",
    "fresh-idealization-gating-curation-filter",
    "precisionk-never-auto-drop",
    "kinsoftchallenge",
    "nd-normalized-nd-raw",
    "molecule-vs-trace",
    "condition",
    "tirfdata-mcos-refs",
    "tmap-tdat",
    "smd",
    "tether-project",
    "trace-generation",
    "provenance-file",
    "analysis-only-import",
    "sidecar",
    "transient-vs-deterministic-sidecar-failure",
    "stage-status-batch",
    "write-lock-stale-lock",
    "schema_version",
)

#: A phrase of each passage that #595 moved, and the page that now holds it. The last five
#: passages were already on their page, so the glossary only dropped its copy and links there.
MOVED = (
    ("hiding it would be a silent distortion", "pipeline.md"),
    ("Raw traces keep a large offset and never look bleached", "pipeline.md"),
    ("Deep-LASI offers only a visual check of the registration", "pipeline.md"),
    ("The movie is never resampled", "pipeline.md"),
    ("α is project-wide in Tether", "reference/parameters.md"),
    ("it is a curator-editable field", "reference/parameters.md"),
    ("its frame gate is stricter and two-sided", "reference/parameters.md"),
    ("per-molecule with a population-median fallback", "reference/parameters.md"),
    ("A NO_STATE frame is never itself a dwell and never a transition", "reference/exports.md"),
    ("What a merged dwell's length means", "reference/exports.md"),
    ("A merge is reported only when the merged run is retained", "reference/exports.md"),
    ("Which dwell surfaces see a gap is decided by their callers", "reference/exports.md"),
    ("Why the first and last dwells are dropped", "reference/exports.md"),
    (
        "whose donor and acceptor only cancel can have a finite corrected E",
        "reference/tether-format.md",
    ),
    ("tether.project.merge appends a contributor's human rows", "reference/tether-format.md"),
    ("two provisional keys can agree for two different conditions", "reference/tether-format.md"),
    ("is the one row that never shows up on an exit-0 run", "cli.md"),
    ("The naming is inverted", "io/legacy-import.md"),
    ("it raises no error", "cli.md"),
    ("whenever the control points exactly determine the fit", "troubleshooting.md"),
    ("called out per plot as re-derivations", "analysis/parity-gallery.md"),
    ("expect Tether's to run about (1 + α) higher", "pipeline.md"),
)

_GREEK = {"α": "alpha", "γ": "gamma", "δ": "delta"}
_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])+)\]\(#([^)]+)\)")
_LETTER = re.compile(r"^- \*\*([A-Z]):\*\* ")


def _body() -> str:
    return split_front_matter(GLOSSARY.read_text(encoding="utf-8"))[1]


def _flat(text: str) -> str:
    """Markdown reduced to plain words: no emphasis, no code ticks, one space between words."""
    return " ".join(re.sub(r"[*`]", "", text).split())


def _entries() -> list[tuple[str, list[str]]]:
    """Each ``###`` entry as its heading and its lines, up to the next heading or rule."""
    lines = _body().splitlines()
    found: list[tuple[str, list[str]]] = []
    for i, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        end = i + 1
        while end < len(lines) and not re.match(r"^(#{1,3} |---\s*$)", lines[end]):
            end += 1
        block = lines[i:end]
        while block and not block[-1].strip():
            block.pop()
        found.append((line[4:], block))
    return found


def _definition(block: list[str]) -> str:
    """The first paragraph after the heading."""
    i = 1
    while i < len(block) and not block[i].strip():
        i += 1
    para: list[str] = []
    while i < len(block) and block[i].strip():
        para.append(block[i].strip())
        i += 1
    return " ".join(para)


def _index_lines() -> list[str]:
    text = _body()
    start = text.index(INDEX_HEADING)
    end = text.index("\n---", start)
    items: list[str] = []
    for line in text[start:end].splitlines():
        if line.startswith("- "):
            items.append(line)
        elif items and line.startswith("  "):
            items[-1] += " " + line.strip()
    return items


def _sort_key(term: str) -> str:
    term = "".join(_GREEK.get(ch, ch) for ch in term)
    term = unicodedata.normalize("NFKD", term)
    term = "".join(ch for ch in term if not unicodedata.combining(ch))
    return term.replace("`", "").lstrip(".#").lower()


def test_the_glossary_has_entries() -> None:
    assert len(_entries()) >= len(ANCHORS_BEFORE_595)


@pytest.mark.parametrize("heading,block", _entries(), ids=[h for h, _ in _entries()])
def test_each_entry_opens_with_a_short_plain_definition(heading: str, block: list[str]) -> None:
    definition = _definition(block)
    assert definition, f"{heading!r} has no definition paragraph"
    assert "`" not in definition, f"{heading!r} opens with a code identifier: {definition!r}"
    said = sentences(definition)
    assert len(said) <= MAX_DEFINITION_SENTENCES, f"{heading!r} definition: {said}"
    for sentence in said:
        count = len(words(sentence))
        assert count <= MAX_DEFINITION_WORDS, f"{heading!r}: {count} words in {sentence!r}"


@pytest.mark.parametrize("heading,block", _entries(), ids=[h for h, _ in _entries()])
def test_no_entry_is_longer_than_twelve_lines(heading: str, block: list[str]) -> None:
    assert len(block) <= MAX_ENTRY_LINES, f"{heading!r} is {len(block)} lines"


def _entry_anchors() -> list[str]:
    """The heading id of each ``###`` entry, as ``tests/test_docs_links.py`` computes it."""
    return [_slugify(_heading_text(heading, {})) for heading, _ in _entries()]


def test_the_a_to_z_list_links_every_entry() -> None:
    linked = {anchor for line in _index_lines() for _, anchor in _LINK.findall(line)}
    served = _anchors(GLOSSARY)
    assert linked <= served, f"the A to Z list links missing anchors: {sorted(linked - served)}"
    entries = _entry_anchors()
    assert set(entries) <= served, sorted(set(entries) - served)
    missing = [anchor for anchor in entries if anchor not in linked]
    assert not missing, f"entries the A to Z list does not reach: {missing}"


def test_the_a_to_z_list_is_in_alphabetical_order() -> None:
    letters: list[str] = []
    for line in _index_lines():
        m = _LETTER.match(line)
        assert m, f"an A to Z line does not start with its letter: {line!r}"
        letters.append(m.group(1))
        terms = [text for text, _ in _LINK.findall(line)]
        keys = [_sort_key(t) for t in terms]
        assert keys == sorted(keys), f"the {m.group(1)} line is out of order: {terms}"
        assert all(k[0].upper() == m.group(1) for k in keys), (
            f"a term under the wrong letter: {terms}"
        )
    assert letters == sorted(letters) and len(letters) == len(set(letters))


@pytest.mark.parametrize("anchor", ANCHORS_BEFORE_595)
def test_every_anchor_from_before_595_still_resolves(anchor: str) -> None:
    assert anchor in _anchors(GLOSSARY)


@pytest.mark.parametrize("phrase,page", MOVED, ids=[p for p, _ in MOVED])
def test_each_moved_passage_is_on_its_new_page(phrase: str, page: str) -> None:
    target = _flat((DOCS / page).read_text(encoding="utf-8"))
    assert _flat(phrase) in target, f"{page} does not hold {phrase!r}"
    assert _flat(phrase) not in _flat(_body()), f"the glossary still holds {phrase!r}"
