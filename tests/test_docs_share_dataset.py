# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The dataset-sharing guide (``docs/how-to/share-a-dataset.md``, #598) runs and stays true.

The page tells a user which files to deposit, how to see the curator name and the movie paths
that a project file carries, and how to describe and cite the deposit. Its signatures are
checked by ``tests/test_docs_how_to.py`` and its imports by ``tests/test_docs_examples.py``.
This module pins the rest of #598's acceptance criteria:

* every Python block on the page runs, in order and in one namespace, against the tutorial
  project that ``docs/tutorial.md`` builds and curates, and prints what the page says it
  prints: the full project shows the movie path and the curator, the subset shows only the
  curator;
* the page keeps the parts the issue asks for: the raw-movie recommendation and its fallback,
  Zenodo and CC BY 4.0 as examples, a README template, a data availability statement and the
  verified Lerner et al. 2021 reference;
* the tutorial states the origin and license of its sample movie;
* the three pages the issue names link to the guide.

Default tier only: no ``large``, ``sidecar`` or ``deep`` marker.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PAGE = DOCS / "how-to" / "share-a-dataset.md"
MOVIE = DOCS / "tutorial" / "data" / "tutorial_movie.tif"

# Python-Markdown's fenced_code matches fences at column zero only, so this is what renders.
_FENCE_RE = re.compile(
    r"^(?P<fence>`{3,})(?P<lang>[\w+-]*)[ ]*\n(?P<code>.*?)^(?P=fence)[ ]*$", re.M | re.S
)
#: The tutorial's curation (``tests/test_docs_tutorial.py``): mol-1 rejected, the others kept.
REJECTED_ID = "b4d11875527ee175c9c0e414d154db7a81a9bca669d3573f66cb1184a511a09d"
ACCEPTED_IDS = (
    "463104a0a9ee0dca67d4eb2981655e11d79ac500d70fbb3cdc5c388de7a77392",
    "7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057",
)
CURATOR = "curator-598"


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.)\s+", " ".join(text.split()))


def _python_blocks() -> list[str]:
    return [m.group("code") for m in _FENCE_RE.finditer(_text()) if m.group("lang") == "python"]


def test_every_python_block_runs_on_the_tutorial_project(tmp_path, monkeypatch, capsys) -> None:
    for module in ("numpy", "scipy", "skimage", "h5py", "tifffile"):
        pytest.importorskip(module)
    from tether.cli import main  # noqa: PLC0415
    from tether.project.labels import accept, reject  # noqa: PLC0415

    (tmp_path / MOVIE.name).write_bytes(MOVIE.read_bytes())
    monkeypatch.chdir(tmp_path)
    assert main(["extract", MOVIE.name, "-o", "tutorial.tether"]) == 0
    capsys.readouterr()
    reject("tutorial.tether", REJECTED_ID, labeler=CURATOR)
    for key in ACCEPTED_IDS:
        accept("tutorial.tether", key, labeler=CURATOR)

    blocks = _python_blocks()
    assert len(blocks) == 2, "the page's Python blocks changed; update this test with them"
    namespace: dict[str, object] = {"__name__": "share_a_dataset"}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for code in blocks:
            exec(compile(code, str(PAGE), "exec"), namespace)  # noqa: S102
    lines = out.getvalue().splitlines()

    assert lines[0] == "2 molecules in the subset, 2 rows in the table", lines
    curators = f"curators: ['{CURATOR}']"
    # The full project: one movie line with its relative path and checksum, then the curator.
    assert re.fullmatch(r"movie path: tutorial_movie\.tif sha256: [0-9a-f]{64}", lines[1]), lines
    assert lines[2] == curators, lines
    # The subset: no movie row, so no movie line, and the curator names are kept.
    assert lines[3:] == [curators], lines
    for name in ("accepted.tether", "accepted.csv"):
        assert (tmp_path / "deposit" / name).is_file(), name
        assert (tmp_path / "deposit" / f"{name}.provenance.json").is_file(), name


def test_the_page_lists_the_deposit_files() -> None:
    text = _text()
    for item in (
        "**The project file, or a subset of it.**",
        "**A provenance file beside each export.**",
        "**The molecule table.**",
        "**The figures**",
        "**The raw movies.**",
        "**A `README.md` file**",
    ):
        assert item in text, item


def test_the_page_recommends_depositing_the_movies_with_a_fallback() -> None:
    text = _text()
    assert "Deposit the raw movies with the project." in _sentences(text)
    assert "\n> The raw movies (" in text, "no fallback statement"


def test_zenodo_and_cc_by_are_examples_not_requirements() -> None:
    sentences = _sentences(_text())
    assert "Zenodo is one example." in sentences
    assert "Creative Commons Attribution 4.0 (CC BY 4.0) is one example." in sentences
    assert "Any repository that issues a DOI and any explicit open license will do." in sentences


def test_the_page_has_a_readme_template_and_an_availability_statement() -> None:
    text = _text()
    markdown = [m.group("code") for m in _FENCE_RE.finditer(text) if m.group("lang") == "markdown"]
    assert len(markdown) == 1, "the README template is one markdown block"
    for heading in ("## Sample", "## Instrument and acquisition", "## Analysis", "## Files"):
        assert heading in markdown[0], heading
    assert "Tether version DOI" in markdown[0]
    assert "## 7. Write the data availability statement" in text
    assert "> The single-molecule FRET movies," in text


def test_the_page_names_fair_and_cites_the_verified_reference() -> None:
    text = _text()
    assert "FAIR" in text
    assert "(Lerner et al. 2021)" in text
    assert "https://doi.org/10.7554/eLife.60416" in text


def test_the_tutorial_states_the_origin_and_license_of_the_sample_movie() -> None:
    text = " ".join((DOCS / "tutorial.md").read_text(encoding="utf-8").split())
    assert "scripts/make_tutorial_movie.py" in text
    assert "under the same license as Tether, GPL-3.0-or-later" in text


@pytest.mark.parametrize(
    ("page", "target"),
    [
        ("how-to/export-results.md", "share-a-dataset.md"),
        ("reproducibility.md", "how-to/share-a-dataset.md"),
        ("privacy.md", "how-to/share-a-dataset.md#3-check-what-the-files-reveal"),
    ],
)
def test_the_named_pages_link_to_the_guide(page: str, target: str) -> None:
    assert f"]({target})" in (DOCS / page).read_text(encoding="utf-8"), page
