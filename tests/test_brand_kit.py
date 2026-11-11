# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The brand kit from mark 42a is complete, offline and correctly licensed (#671).

``scripts/brand/build.py`` writes the files; CI does not run it, so these tests guard what is
committed:

* each of the three folders holds the files the later brand issues (#672-#677) use, with the
  pixel sizes their names state;
* no SVG or CSS file of the kit loads a font or a remote file, so the application, the
  installers and the site make no third-party request (``docs/privacy.md``);
* the Red Hat Display files are the upstream files, byte for byte (no subset: OFL-1.1 with a
  Reserved Font Name);
* the wheel can carry ``tether/gui/assets/brand/`` (nothing ignores it, nothing excludes it);
* the five brand colors agree between ``tokens.json`` and ``docs/brand.md``;
* the README shows the lockup for both GitHub themes and keeps its ``# Tether`` heading.

Stdlib plus Pillow (base lock) for the one test that decodes the 16 px icons.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
GUI = _REPO / "src" / "tether" / "gui" / "assets" / "brand"
PKG = _REPO / "packaging" / "images"
DOCS = _REPO / "docs" / "assets" / "brand"
FOLDERS = (GUI, PKG, DOCS)

THEMES = ("light", "dark")
ICON_NAMES = (
    "analyze",
    "busy",
    "conditions",
    "correct",
    "curate",
    "export",
    "extract",
    "hand-off",
    "idealize",
    "import",
    "movie",
    "project",
)
APP_PNG = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
HICOLOR = (16, 24, 32, 48, 64, 128, 256, 512)

# Upstream RedHatOfficial/RedHatFont at 6bb1048, fonts/Proportional/RedHatDisplay/ttf/.
FONT_SHA256 = {
    "RedHatDisplay-Regular.ttf": "ab007b2a5b1590027cb049c3946de67870f6de9fa2420f8ef6e968392219aecd",
    "RedHatDisplay-Medium.ttf": "83c3626b8abaa09d8df2d0dc7ead26c4cfe06e3e8c9d0a34afa2910cd92fc501",
    "RedHatDisplay-Bold.ttf": "bac4990e2845c796ad0cfc47c3121b09df9285858ba841f7eab8bcdea87b74f1",
}

BRAND_COLORS = {
    "ink": "#121417",
    "paper": "#fbfbfa",
    "edge": "#d5dadd",
    "ground": "#0d0f12",
    "chalk": "#f2f4f5",
}

SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", path
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _expected_png_sizes() -> dict[Path, tuple[int, int]]:
    sizes: dict[Path, tuple[int, int]] = {}
    for s in APP_PNG:
        sizes[GUI / "app-icon" / f"tether-{s}.png"] = (s, s)
    for s in HICOLOR:
        sizes[GUI / "app-icon" / "hicolor" / f"{s}x{s}" / "apps" / "tether.png"] = (s, s)
    for s in (32, 64, 256):
        sizes[GUI / "document" / f"tether-document-{s}.png"] = (s, s)
    for t in THEMES:
        sizes[GUI / f"splash-{t}-640x360.png"] = (640, 360)
        sizes[GUI / f"splash-{t}-1280x720.png"] = (1280, 720)
    sizes[PKG / "windows-welcome-164x314.png"] = (164, 314)
    sizes[PKG / "windows-header-150x57.png"] = (150, 57)
    sizes[PKG / "windows-icon-256.png"] = (256, 256)
    sizes[PKG / "macos-welcome-1227x600.png"] = (1227, 600)
    sizes[DOCS / "apple-touch-icon-180.png"] = (180, 180)
    sizes[DOCS / "social-card-1200x630.png"] = (1200, 630)
    return sizes


def _expected_other_files() -> list[Path]:
    files = [
        GUI / "app-icon" / "tether.ico",
        GUI / "app-icon" / "tether.icns",
        GUI / "app-icon" / "hicolor" / "scalable" / "apps" / "tether.svg",
        GUI / "document" / "tether-document.svg",
        GUI / "document" / "tether-document-small.svg",
        GUI / "document" / "tether-document.ico",
        GUI / "tokens.json",
        GUI / "fonts" / "OFL.txt",
        *(GUI / "fonts" / name for name in FONT_SHA256),
    ]
    for t in THEMES:
        files += [GUI / "icons" / t / f"{name}.svg" for name in ICON_NAMES]
        files += [GUI / f"start-art-{t}.svg", GUI / f"splash-{t}.svg", GUI / f"tile-{t}.svg"]
        files += [DOCS / f"{kind}-{t}.svg" for kind in ("tile", "icon-small", "icon-tiny")]
        files.append(DOCS / f"lockup-{t}.svg")
    files += [DOCS / name for name in ("logo.svg", "favicon.svg", "favicon.ico", "brand.css")]
    files += [DOCS / "mark-black.svg", DOCS / "mark-white.svg", DOCS / "icon-dark.svg"]
    return files


def test_each_folder_holds_the_kit_files_at_their_stated_sizes() -> None:
    missing = [p for p in _expected_other_files() if not p.is_file()]
    wrong = {}
    for path, size in _expected_png_sizes().items():
        if not path.is_file():
            missing.append(path)
        elif _png_size(path) != size:
            wrong[path.relative_to(_REPO).as_posix()] = _png_size(path)
    assert not missing, [p.relative_to(_REPO).as_posix() for p in missing]
    assert not wrong, wrong


def test_the_icon_containers_are_what_their_names_say() -> None:
    for ico in (GUI / "app-icon" / "tether.ico", GUI / "document" / "tether-document.ico"):
        reserved, kind, count = struct.unpack("<HHH", ico.read_bytes()[:6])
        assert (reserved, kind) == (0, 1) and count >= 6, ico
    icns = (GUI / "app-icon" / "tether.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)


def _kit_text_files() -> list[Path]:
    files = [p for folder in FOLDERS for p in folder.rglob("*.svg")]
    files += [p for folder in FOLDERS for p in folder.rglob("*.css")]
    return sorted(files)


def test_no_kit_file_loads_a_font_or_a_remote_file() -> None:
    files = _kit_text_files()
    assert len(files) >= 40, len(files)  # 48 when the kit landed (#671)
    offenders = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        found = []
        if "@import" in text or "@font-face" in text:
            found.append("loads a stylesheet or a font")
        if re.search(r"<text[\s>]", text):
            found.append("has live text, not outlines")
        if re.search(r"<image[\s>]|xlink:href|url\(\s*['\"]?(?!#)", text):
            found.append("references another file")
        found += sorted(set(re.findall(r"https?://[^\s\"'<>)]+", text)) - {SVG_NAMESPACE})
        if found:
            offenders[path.relative_to(_REPO).as_posix()] = found
    assert not offenders, offenders


def test_the_fonts_are_the_upstream_files_unchanged() -> None:
    fonts = GUI / "fonts"
    assert sorted(p.name for p in fonts.glob("*.ttf")) == sorted(FONT_SHA256)
    for name, digest in FONT_SHA256.items():
        assert hashlib.sha256((fonts / name).read_bytes()).hexdigest() == digest, name
    ofl = (fonts / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in ofl
    assert "The Red Hat Project Authors" in ofl


def test_reuse_licenses_the_brand_files_cc_by_and_the_fonts_ofl() -> None:
    config = tomllib.loads((_REPO / "REUSE.toml").read_text(encoding="utf-8"))
    annotations = config["annotations"]

    def index_of(path: str) -> int:
        hits = [
            i
            for i, a in enumerate(annotations)
            if path in ([a["path"]] if isinstance(a["path"], str) else a["path"])
        ]
        assert len(hits) == 1, (path, hits)
        return hits[0]

    brand = index_of("src/tether/gui/assets/brand/**")
    fonts = index_of("src/tether/gui/assets/brand/fonts/**")
    assert annotations[brand]["SPDX-License-Identifier"] == "CC-BY-4.0"
    assert annotations[fonts]["SPDX-License-Identifier"] == "OFL-1.1"
    for i in (brand, fonts):
        assert annotations[i]["precedence"] == "override"
    # REUSE applies the last matching annotation, so the font entry must follow the brand one.
    assert fonts > brand
    for folder in ("packaging/images/**", "docs/assets/brand/**"):
        assert index_of(folder) == brand, folder
    assert (_REPO / "LICENSES" / "OFL-1.1.txt").is_file()
    notice = (_REPO / "NOTICE").read_text(encoding="utf-8")
    assert "Red Hat Display" in notice and "OFL-1.1" in notice and "6bb1048" in notice


def test_the_wheel_can_carry_the_brand_folder() -> None:
    config = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/tether"]
    assert not {"exclude", "only-include", "include"} & set(wheel), wheel
    assert "exclude" not in config["tool"]["hatch"]["build"]
    git = shutil.which("git")
    if git is None:  # pragma: no cover - every CI runner has git
        pytest.skip("git is not on PATH")
    paths = [p.relative_to(_REPO).as_posix() for p in GUI.rglob("*") if p.is_file()]
    result = subprocess.run(
        [git, "check-ignore", "--no-index", "--stdin"],
        cwd=_REPO,
        input="\n".join(paths),
        capture_output=True,
        text=True,
        check=False,
    )
    # Exit 1 means that no path is ignored; hatchling leaves ignored files out of the wheel.
    assert result.returncode == 1, result.stdout or result.stderr


def test_the_16px_icon_is_the_same_image_in_every_container() -> None:
    Image = pytest.importorskip("PIL.Image")
    png = Image.open(GUI / "app-icon" / "tether-16.png").convert("RGBA")
    hicolor = Image.open(GUI / "app-icon" / "hicolor" / "16x16" / "apps" / "tether.png")
    ico = Image.open(GUI / "app-icon" / "tether.ico")
    ico.size = (16, 16)
    for other in (hicolor.convert("RGBA"), ico.convert("RGBA")):
        assert other.size == (16, 16)
        assert other.tobytes() == png.tobytes()


def test_the_brand_colors_agree_between_tokens_and_the_brand_page() -> None:
    tokens = json.loads((GUI / "tokens.json").read_text(encoding="utf-8"))
    assert tokens["brand"] == BRAND_COLORS
    page = (_REPO / "docs" / "brand.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| (\w+) \| `(#[0-9a-f]{6})` \|", page, flags=re.MULTILINE))
    assert {name.lower(): value for name, value in rows.items()} == BRAND_COLORS


def test_the_readme_shows_the_lockup_for_both_themes_and_keeps_its_heading() -> None:
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    head, sep, _ = readme.partition("\n# Tether\n")
    assert sep, "README lost its '# Tether' heading"
    picture = re.search(r"<picture>(.*?)</picture>", head, flags=re.DOTALL)
    assert picture is not None, "no <picture> above the heading"
    dark = re.search(
        r'<source media="\(prefers-color-scheme: dark\)" srcset="([^"]+)"', picture.group(1)
    )
    light = re.search(r'<img src="([^"]+)" alt="Tether"', picture.group(1))
    assert dark and light
    assert dark.group(1) == "docs/assets/brand/lockup-dark.svg"
    assert light.group(1) == "docs/assets/brand/lockup-light.svg"
    badges = re.findall(r"https://img\.shields\.io/badge/[^)]*-([0-9a-f]{6})\.svg", readme)
    assert badges == ["121417", "121417"], badges


def test_the_wiki_lockup_points_at_a_committed_file() -> None:
    home = (_REPO / "wiki" / "Home.md").read_text(encoding="utf-8")
    prefix = "https://raw.githubusercontent.com/bioedca/tether/main/"
    urls = re.findall(re.escape(prefix) + r"([^)\s]+)", home)
    assert urls == ["docs/assets/brand/lockup-light.svg"], urls
    assert (_REPO / urls[0]).is_file()
