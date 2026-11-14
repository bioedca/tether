# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tests/fixtures/PROVENANCE.md`` checksum-locks its sources, and its generator keeps it (#552).

The inventory used to give most sources only a 16-hex prefix of their SHA-256 and an ellipsis. A
prefix names a source but cannot checksum-lock it, which AGENTS.md requires of every accepted
reference. ``shortened_digests`` finds such a digest anywhere in the file, and in every table
column headed ``SHA-256`` it also refuses a cell that is not a full 64-hex digest.

``scripts/make_fixtures.py`` used to rewrite the whole file from its own five rows, which erased
every entry added after it. The last tests run the script on synthetic sources against a copy of
the committed inventory and check that only the lines it owns change.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
PROVENANCE = REPO / "tests" / "fixtures" / "PROVENANCE.md"
SCRIPT = REPO / "scripts" / "make_fixtures.py"

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_FULL_SHA256 = re.compile(r"[0-9a-f]{64}")
# A hex run cut short by an ellipsis on either side, e.g. `c4293f00ed2ac72d…` or `0f4c8902…a58839`.
_ELLIPSIS = r"(?:…|\.\.\.)"
_ABBREVIATED = re.compile(rf"(?:[0-9a-fA-F]{{4,}}{_ELLIPSIS}|{_ELLIPSIS}[0-9a-fA-F]{{4,}})")
_HEX_AND_ELLIPSES = re.compile(r"[0-9a-fA-F….]+")

# The rows of the inventory table that scripts/make_fixtures.py writes.
_SCRIPT_ROWS = (
    "tests/fixtures/movie_be_64x64x50.tif",
    "tests/fixtures/smd_4mol.hdf5",
    "tests/fixtures/smd_2mol.hdf5",
    "tests/fixtures/large/smd_281mol.hdf5",
    "tests/fixtures/large/model_281mol.hdf5",
)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _sha256_cells(text: str) -> list[tuple[int, str]]:
    """``(line number, cell)`` for every cell of every table column headed ``SHA-256``."""
    lines = text.split("\n")
    found: list[tuple[int, str]] = []
    i = 0
    while i < len(lines) - 1:
        header, rule = lines[i], lines[i + 1]
        if not (header.startswith("|") and re.fullmatch(r"\|(?:\s*:?-+:?\s*\|)+", rule.strip())):
            i += 1
            continue
        names = _cells(header)
        columns = [k for k, name in enumerate(names) if "SHA-256" in name]
        i += 2
        while i < len(lines) and lines[i].startswith("|"):
            row = _cells(lines[i])
            if len(row) != len(names):
                raise AssertionError(
                    f"PROVENANCE.md line {i + 1}: {len(row)} cells, header has {len(names)}"
                )
            found.extend((i + 1, row[k]) for k in columns)
            i += 1
    return found


def shortened_digests(text: str) -> list[tuple[int, str]]:
    """Every checksum in ``text`` that is not a full 64-hex SHA-256, as ``(line, text)``."""
    bad: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for span in _CODE_SPAN.findall(line):
            if _HEX_AND_ELLIPSES.fullmatch(span) and _ABBREVIATED.search(span):
                bad.append((lineno, span))
    for lineno, cell in _sha256_cells(text):
        spans = _CODE_SPAN.findall(cell)
        if not spans:
            bad.append((lineno, cell))
        bad.extend((lineno, span) for span in spans if not _FULL_SHA256.fullmatch(span))
    return sorted(set(bad))


def test_every_source_checksum_is_a_full_sha256() -> None:
    text = PROVENANCE.read_text(encoding="utf-8")
    assert shortened_digests(text) == []


def test_the_check_reads_the_inventory_tables() -> None:
    """The column parse finds the main table, so a passing check is not a vacuous one."""
    cells = _sha256_cells(PROVENANCE.read_text(encoding="utf-8"))
    assert len(cells) > 100
    rows = {lineno for lineno, _ in cells}
    text = PROVENANCE.read_text(encoding="utf-8").split("\n")
    for fixture in (*_SCRIPT_ROWS, "tests/fixtures/docs_site/gh_pages_index.html"):
        assert any(text[n - 1].startswith(f"| `{fixture}` |") for n in rows), fixture


@pytest.mark.parametrize(
    "bad",
    [
        "| `a.tif` | `c4293f00ed2ac72d…` |",  # a 16-hex prefix in a SHA-256 column
        "| `a.tif` | `c4293f00ed2ac72d` |",  # a prefix with no ellipsis
        "| `a.tif` | `" + "a" * 63 + "` |",  # one hex digit short
        "| `a.tif` | not recorded |",  # no digest at all
    ],
)
def test_the_check_refuses_a_short_digest_in_a_sha256_column(bad: str) -> None:
    text = "| Fixture | Source SHA-256 |\n|---|---|\n" + bad + "\n"
    assert shortened_digests(text) != []


@pytest.mark.parametrize(
    "prose", ["(source sha256 `a993036a…`)", "(both `0f4c8902…a58839`)", "`81d06b5b...3320`"]
)
def test_the_check_refuses_an_abbreviated_digest_in_prose(prose: str) -> None:
    assert shortened_digests(f"Some text {prose} here.\n") != []


def test_the_check_accepts_full_digests_and_other_hex() -> None:
    full = "c4293f00ed2ac72d0aa6c55f0c3fbb4910e928eed8f2c4c73a3087cf109e8448"
    text = (
        "| Fixture | Source SHA-256 |\n|---|---|\n"
        f"| `a.tif` | `{full}` |\n| `b.tif` | `{full}` + `{full}` |\n\n"
        "An MD5 `49ef3a860a2e05d01233466ccbd06169` and commit `10f4230` are not SHA-256 cells.\n"
    )
    assert shortened_digests(text) == []


def test_docs_site_snapshot_row_records_its_bytes() -> None:
    """The verbatim ``gh-pages`` root (#509) has a row with its real size and digest."""
    path = REPO / "tests" / "fixtures" / "docs_site" / "gh_pages_index.html"
    data = path.read_bytes().replace(b"\r\n", b"\n")
    row = next(
        line
        for line in PROVENANCE.read_text(encoding="utf-8").split("\n")
        if line.startswith("| `tests/fixtures/docs_site/gh_pages_index.html` |")
    )
    assert f"| {len(data):,} B |" in row
    assert f"`{hashlib.sha256(data).hexdigest()}`" in row


def _load_script() -> ModuleType:
    pytest.importorskip("numpy")
    pytest.importorskip("tifffile")
    spec = importlib.util.spec_from_file_location("make_fixtures_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_script_rows_are_in_the_script_format() -> None:
    """Rebuilding each of the five rows from its own values reproduces the committed line."""
    mf = _load_script()
    lines = PROVENANCE.read_text(encoding="utf-8").split("\n")
    for fixture in _SCRIPT_ROWS:
        line = next(x for x in lines if mf.row_key(x) == fixture)
        _, label, src, src_size, dst_size, digest, note = _cells(line)
        rebuilt = mf.format_row(
            fixture,
            label,
            src.strip("`"),
            int(src_size.removesuffix(" B").replace(",", "")),
            int(dst_size.removesuffix(" B").replace(",", "")),
            digest.strip("`"),
            note,
        )
        assert rebuilt == line


def test_make_fixtures_keeps_rows_it_did_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the generator changes its own five rows and crop paragraph, and nothing else."""
    import numpy as np
    import tifffile

    mf = _load_script()
    root = tmp_path / "repo"
    fixtures = root / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    shutil.copyfile(PROVENANCE, fixtures / "PROVENANCE.md")
    monkeypatch.setattr(mf, "REPO_ROOT", root)
    monkeypatch.setattr(mf, "FIXTURES", fixtures)
    monkeypatch.setattr(mf, "LARGE", fixtures / "large")

    src = tmp_path / "example-data"
    rng = np.random.default_rng(552)
    movie = rng.integers(0, 100, size=(mf.CROP_FRAMES, 80, 96), dtype=np.uint16)
    movie[:, 5:15, 7:17] += 1000  # the brightest window starts near (row=0..5, col=0..7)
    (src / "bla-uckopsb-tbox-video10").mkdir(parents=True)
    tifffile.imwrite(
        src / "bla-uckopsb-tbox-video10" / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif",
        movie.astype(">u2"),
        byteorder=">",
    )
    for rel in (
        "bla-uckopsb-tbox-video10/video10.hdf5",
        "uckopsb-01ab-smd-video25-28/video 25,26,27,28.hdf5",
        "tmaven-model/model-source-smd-281mol.hdf5",
        "tmaven-model/model.hdf5",
    ):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(rel.encode())

    assert mf.main(["--source-root", str(src)]) == 0

    before = PROVENANCE.read_text(encoding="utf-8").split("\n")
    after = (fixtures / "PROVENANCE.md").read_text(encoding="utf-8").split("\n")
    assert len(after) == len(before)
    begin = before.index(mf.CROP_BEGIN)
    end = before.index(mf.CROP_END)
    owned = {i for i, line in enumerate(before) if mf.row_key(line) in _SCRIPT_ROWS}
    owned |= set(range(begin + 1, end))
    changed = {i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b}
    assert changed <= owned, [before[i] for i in sorted(changed - owned)]
    assert {i for i in owned if mf.row_key(before[i])} <= changed  # its rows were rewritten
    crop = "\n".join(after[begin + 1 : end])
    assert "(row=375, col=432)" not in crop and "top-left pixel `(row=" in crop
    assert shortened_digests("\n".join(after)) == []


def test_update_provenance_refuses_to_guess() -> None:
    mf = _load_script()
    text = PROVENANCE.read_text(encoding="utf-8")
    row = mf.format_row("tests/fixtures/not_in_the_inventory.tif", "x", "y", 1, 1, "0" * 64, "z")
    meta = {"crop_origin": (0, 0), "shape": (50, 64, 64)}
    with pytest.raises(ValueError, match="no row"):
        mf.update_provenance(text, [row], meta)
    with pytest.raises(ValueError, match="markers"):
        mf.update_provenance(text.replace(mf.CROP_BEGIN, ""), [], meta)
