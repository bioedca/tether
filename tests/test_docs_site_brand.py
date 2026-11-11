# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The documentation site shows the Tether brand and loads nothing from another host (#674).

``docs/privacy.md`` says the site makes **no third-party asset requests**. The brand adds a
heading font and new images, so these tests keep that sentence true:

* ``mkdocs.yml`` uses the kit's logo and favicon, the ``custom`` palette in both schemes (no
  indigo), the kit's ``brand.css`` and the heading-font stylesheet, and keeps ``font: false``
  so that Material adds no Google Fonts link;
* the site's font files are the upstream Red Hat Display files unchanged (the same bytes as
  the copies that ``tests/test_brand_kit.py`` pins), with ``OFL.txt`` beside them, and every
  ``@font-face`` URL resolves to one of them;
* the home page shows the lockup once for each scheme, with alternative text;
* no stylesheet under ``docs/`` and no theme override under ``overrides/`` loads a font, an
  image, a stylesheet or a script from another host;
* the **built** site does the same, page by page. That test reads the folder that
  ``TETHER_BUILT_SITE`` names, and the required ``docs-build`` job sets it after
  ``mkdocs build --strict``. Without the variable the test skips, because the 3-OS matrix
  does not install MkDocs. With the variable set, a missing folder fails.

What a static scan sees is the HTML and CSS that a browser fetches without running code.
Requests that the theme's JavaScript makes are outside it.

Stdlib plus PyYAML (base lock and the docs toolchain both carry it).
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
DOCS = _REPO / "docs"
OVERRIDES = _REPO / "overrides"
FONTS = DOCS / "assets" / "fonts"
GUI_FONTS = _REPO / "src" / "tether" / "gui" / "assets" / "brand" / "fonts"
FONT_FILES = ("RedHatDisplay-Medium.ttf", "RedHatDisplay-Bold.ttf")

#: ``<link rel>`` values that make the browser fetch or connect to the ``href``.
FETCHING_RELS = frozenset(
    {
        "stylesheet",
        "icon",
        "shortcut",
        "apple-touch-icon",
        "apple-touch-icon-precomposed",
        "mask-icon",
        "manifest",
        "preload",
        "prefetch",
        "preconnect",
        "dns-prefetch",
        "modulepreload",
        "prerender",
    }
)
#: Attributes that the browser fetches, by element.
FETCHING_ATTRS = {
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "script": ("src",),
    "iframe": ("src",),
    "embed": ("src",),
    "audio": ("src",),
    "video": ("src", "poster"),
    "track": ("src",),
    "input": ("src",),
    "object": ("data",),
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
    "feimage": ("href", "xlink:href"),
}
_CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.IGNORECASE | re.DOTALL)
_CSS_IMPORT = re.compile(r"""@import\s+(['"])(.*?)\1""", re.IGNORECASE)


def _config() -> dict:
    return yaml.safe_load((_REPO / "mkdocs.yml").read_text(encoding="utf-8"))


SITE_HOST = urlsplit(_config()["site_url"]).hostname


def css_loads(text: str) -> list[str]:
    """Every URL that a stylesheet (or a ``style`` value) makes the browser fetch."""
    return [m.group(2).strip() for m in (*_CSS_URL.finditer(text), *_CSS_IMPORT.finditer(text))]


class _Loads(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "link" and FETCHING_RELS & set(values.get("rel", "").lower().split()):
            self.urls.append(values.get("href", ""))
        for name in FETCHING_ATTRS.get(tag, ()):
            if name not in values:
                continue
            if name == "srcset":
                self.urls += [part.split()[0] for part in values[name].split(",") if part.split()]
            else:
                self.urls.append(values[name])
        if "style" in values:
            self.urls += css_loads(values["style"])
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.urls += css_loads(data)


def html_loads(text: str) -> list[str]:
    """Every URL that an HTML page makes the browser fetch without running a script."""
    parser = _Loads()
    parser.feed(text)
    parser.close()
    return parser.urls


def is_foreign(url: str) -> bool:
    """True when ``url`` names a host other than the site's own (relative and data: are not)."""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.scheme in ("", "data"):
        return False
    return parts.scheme not in ("http", "https") or parts.hostname != SITE_HOST


def _foreign(files: list[Path], root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        urls = css_loads(text) if path.suffix == ".css" else html_loads(text)
        bad = sorted({u for u in urls if is_foreign(u)})
        if bad:
            found[path.relative_to(root).as_posix()] = bad
    return found


# --- The scanner itself ------------------------------------------------------------------------


def test_the_scanner_finds_each_kind_of_remote_load() -> None:
    page = """
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Roboto">
      <link rel="icon" href="//cdn.example.org/favicon.ico">
      <script src="https://cdn.example.org/a.js"></script>
      <img src="https://img.shields.io/badge/x.svg" alt="">
      <img srcset="a.png 1x, https://cdn.example.org/b.png 2x" alt="">
      <svg><use href="https://cdn.example.org/s.svg#i"/></svg>
      <div style="background: url('https://cdn.example.org/bg.png')"></div>
      <style>@import "https://cdn.example.org/x.css";</style>
    """
    css = '@font-face { src: url(https://fonts.gstatic.com/x.woff2) format("woff2"); }'
    assert sorted(u for u in html_loads(page) if is_foreign(u)) == sorted(
        [
            "https://fonts.gstatic.com",
            "https://fonts.googleapis.com/css?family=Roboto",
            "//cdn.example.org/favicon.ico",
            "https://cdn.example.org/a.js",
            "https://img.shields.io/badge/x.svg",
            "https://cdn.example.org/b.png",
            "https://cdn.example.org/s.svg#i",
            "https://cdn.example.org/bg.png",
            "https://cdn.example.org/x.css",
        ]
    )
    assert [u for u in css_loads(css) if is_foreign(u)] == ["https://fonts.gstatic.com/x.woff2"]


def test_the_scanner_lets_links_and_own_files_through() -> None:
    page = f"""
      <a href="https://github.com/bioedca/tether">repository</a>
      <link rel="canonical" href="https://elsewhere.example.org/">
      <link rel="icon" href="https://{SITE_HOST}/tether/assets/brand/favicon.svg">
      <link rel="stylesheet" href="assets/brand/brand.css">
      <img src="data:image/svg+xml;base64,AAAA" alt="">
      <img src="../assets/brand/logo.svg" alt="">
    """
    css = 'a { background: url("#mask"); } @font-face { src: url(../assets/fonts/x.ttf); }'
    assert [u for u in html_loads(page) if is_foreign(u)] == []
    assert [u for u in css_loads(css) if is_foreign(u)] == []


# --- The sources -------------------------------------------------------------------------------


def test_the_theme_uses_the_brand_logo_favicon_palette_and_stylesheets() -> None:
    config = _config()
    theme = config["theme"]
    assert theme["font"] is False  # no Google Fonts link (docs/privacy.md)
    assert theme["logo"] == "assets/brand/logo.svg"
    assert theme["favicon"] == "assets/brand/favicon.svg"
    schemes = {entry["scheme"]: entry for entry in theme["palette"]}
    assert set(schemes) == {"default", "slate"}
    for name, entry in schemes.items():
        assert (entry["primary"], entry["accent"]) == ("custom", "custom"), name
    assert "indigo" not in (_REPO / "mkdocs.yml").read_text(encoding="utf-8")
    assert config["extra_css"] == ["assets/brand/brand.css", "stylesheets/site.css"]
    for path in (theme["logo"], theme["favicon"], *config["extra_css"]):
        assert not is_foreign(path) and (DOCS / path).is_file(), path


def test_the_site_fonts_are_the_upstream_files_with_their_license() -> None:
    assert sorted(p.name for p in FONTS.iterdir()) == sorted([*FONT_FILES, "OFL.txt"])
    # tests/test_brand_kit.py pins the GUI copies to the upstream SHA-256 values.
    for name in (*FONT_FILES, "OFL.txt"):
        assert (FONTS / name).read_bytes() == (GUI_FONTS / name).read_bytes(), name


def test_each_font_face_names_a_font_file_of_the_site() -> None:
    stylesheet = DOCS / "stylesheets" / "site.css"
    text = stylesheet.read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{(.*?)\}", text, re.DOTALL)
    assert len(faces) == len(FONT_FILES)
    targets = []
    for face in faces:
        assert '"Red Hat Display"' in face
        (url,) = css_loads(face)
        assert not is_foreign(url), url
        targets.append((stylesheet.parent / url).resolve())
    assert sorted(targets) == sorted((FONTS / name).resolve() for name in FONT_FILES)
    # Headings only: the body text keeps the system font.
    assert "--md-text-font:" not in text


def test_the_home_page_shows_the_lockup_for_each_scheme_with_alt_text() -> None:
    images = re.findall(r"<img\b[^>]*>", (DOCS / "index.md").read_text(encoding="utf-8"))
    by_class = {}
    for tag in images:
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        by_class[attrs.get("class")] = attrs
    assert by_class["on-light"]["src"] == "assets/brand/lockup-light.svg"
    assert by_class["on-dark"]["src"] == "assets/brand/lockup-dark.svg"
    for attrs in by_class.values():
        assert attrs["alt"].strip(), attrs
        assert (DOCS / attrs["src"]).is_file(), attrs
    # brand.css hides the image for the other scheme by these two class names.
    css = (DOCS / "assets" / "brand" / "brand.css").read_text(encoding="utf-8")
    assert '[data-md-color-scheme="default"] .tether-hero .on-dark' in css
    assert '[data-md-color-scheme="slate"] .tether-hero .on-light' in css


def test_no_source_stylesheet_or_override_loads_from_another_host() -> None:
    files = sorted(DOCS.rglob("*.css")) + sorted(OVERRIDES.rglob("*.html"))
    assert {p.name for p in files} >= {"brand.css", "site.css", "copyright.html"}
    assert _foreign(files, _REPO) == {}


# --- The built site ----------------------------------------------------------------------------


@pytest.fixture
def built_site() -> Path:
    named = os.environ.get("TETHER_BUILT_SITE")
    if not named:
        pytest.skip("TETHER_BUILT_SITE is not set; the docs-build job sets it after the build")
    site = Path(named)
    assert (site / "index.html").is_file(), f"no built site at {site}"
    return site


def test_the_built_site_loads_nothing_from_another_host(built_site: Path) -> None:
    files = sorted(built_site.rglob("*.html")) + sorted(built_site.rglob("*.css"))
    assert len(files) > 50, len(files)
    assert _foreign(files, built_site) == {}


def test_the_built_site_uses_the_brand_on_every_page(built_site: Path) -> None:
    pages = sorted(built_site.rglob("*.html"))
    wrong = {}
    for page in pages:
        text = page.read_text(encoding="utf-8")
        colors = set(re.findall(r'data-md-color-(?:primary|accent)="([^"]*)"', text))
        if text.lstrip().lower().startswith("<!doctype") and colors != {"custom"}:
            wrong[page.relative_to(built_site).as_posix()] = sorted(colors)
    assert not wrong, wrong
    home = (built_site / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<link rel="icon" href="assets/brand/favicon\.svg"', home)
    assert re.search(
        r'class="md-header__button md-logo"[^>]*>\s*<img src="assets/brand/logo\.svg"', home
    )
    for name in ("assets/brand/brand.css", "stylesheets/site.css"):
        assert f'<link rel="stylesheet" href="{name}">' in home, name
    built_css = built_site / "stylesheets" / "site.css"
    for url in css_loads(built_css.read_text(encoding="utf-8")):
        assert (built_css.parent / url).resolve().is_file(), url
