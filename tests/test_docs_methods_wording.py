# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The published pages never say that Tether corrected traces or efficiencies (#586).

Tether stores the leakage factor α and the γ ratio with each molecule, but every value it
shows or exports, and the hidden Markov model it fits, uses apparent E
(``docs/reproducibility.md``, "A sample Methods paragraph"). ``docs/citing.md`` once offered
a sample Methods sentence that began "Traces were corrected for donor leakage (α) …", so a
reader who copied it published a step the software did not perform.

This module guards three things:

* no published page says that traces, trajectories, intensities or efficiencies *were* (or
  *are*) corrected;
* ``docs/citing.md`` keeps no second copy of the stamp list or the ``h5py`` read-back script,
  which ``docs/reproducibility.md`` owns, so the two pages cannot drift apart again;
* the ``#reporting-a-tether-analysis`` anchor that ``docs/reproducibility.md`` links to still
  exists, and the sample paragraph there still says the fit ran on apparent efficiency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CITING = DOCS / "citing.md"
REPRODUCIBILITY = DOCS / "reproducibility.md"

# mkdocs.yml `exclude_docs` keeps the PRD off the site.
_EXCLUDED = {DOCS / "PRD.md"}

# A subject naming the measured quantity, then up to 40 characters inside the same sentence,
# then a passive "were/was/are/is/been corrected". The window lets "Traces were corrected"
# and "FRET efficiencies of each molecule were corrected" both match, while "Correction
# factors were estimated" does not.
_CORRECTED_CLAIM = re.compile(
    r"\b(?:traces?|trajector(?:y|ies)|intensit(?:y|ies)|efficienc(?:y|ies))\b"
    r"[^.;:]{0,40}?\b(?:were|was|are|is|been)\s+corrected\b",
    re.IGNORECASE,
)


def _published_pages() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p not in _EXCLUDED)


def _flat(text: str) -> str:
    """Join wrapped lines and drop blockquote markers, so a sentence reads as one string."""
    lines = (re.sub(r"^\s*>\s?", "", line) for line in text.splitlines())
    return re.sub(r"\s+", " ", " ".join(lines))


def _section(text: str, heading: str) -> str:
    """The body of the level-2 section named ``heading``, up to the next level-2 heading."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"no '## {heading}' section"
    return match.group(1)


def test_pattern_catches_the_old_sentence() -> None:
    old = (
        "> (https://doi.org/10.5281/zenodo.23306397). Traces were corrected for donor "
        "leakage (α)\n> and for the detection-efficiency and quantum-yield ratio (γ)"
    )
    assert _CORRECTED_CLAIM.search(_flat(old))


@pytest.mark.parametrize(
    "sentence",
    [
        "Correction factors were estimated as described by McCann et al. (2010).",
        "Trajectories were idealized on the apparent FRET efficiency.",
        "the state means are therefore apparent efficiencies.",
    ],
)
def test_pattern_spares_the_true_wording(sentence: str) -> None:
    assert not _CORRECTED_CLAIM.search(sentence)


@pytest.mark.parametrize("page", _published_pages(), ids=lambda p: p.relative_to(DOCS).as_posix())
def test_no_page_says_tether_corrected_the_data(page: Path) -> None:
    hits = [m.group(0) for m in _CORRECTED_CLAIM.finditer(_flat(page.read_text("utf-8")))]
    assert not hits, (
        f"{page.relative_to(ROOT)} says data were corrected, which Tether does not do: {hits}. "
        "Say the correction factors were estimated, and that values are apparent E."
    )


def test_citing_keeps_the_anchor_reproducibility_links_to() -> None:
    assert re.search(r"^## Reporting a Tether analysis$", CITING.read_text("utf-8"), re.M)
    assert "citing.md#reporting-a-tether-analysis" in REPRODUCIBILITY.read_text("utf-8")


def test_citing_carries_no_second_copy_of_the_stamps() -> None:
    section = _section(CITING.read_text("utf-8"), "Reporting a Tether analysis")
    assert "```" not in section, "the read-back script belongs to reproducibility.md"
    assert "h5py" not in section
    for stamp in ("/settings/", "app_version", ".provenance.json", "nstates_selected_by"):
        assert stamp not in section, f"citing.md repeats the stamp list ({stamp!r})"
    assert "reproducibility.md#a-sample-methods-paragraph" in section


def test_sample_paragraph_names_apparent_efficiency() -> None:
    section = _flat(_section(REPRODUCIBILITY.read_text("utf-8"), "A sample Methods paragraph"))
    assert "idealized on the apparent FRET efficiency" in section
    assert "without the α and γ corrections" in section
