# Brand kit generator

These two scripts make every file of the Tether brand kit from mark 42a (#671). The rules for
using the files are on the documentation page [Brand](../../docs/brand.md).

- `brandkit.py` holds the geometry of the mark (a 200 × 200 drawing), the color tokens, and
  the conversion of the word TETHER to outlines.
- `build.py` writes the full kit (129 files) to `build/brand-kit/` and then copies the files
  that the repository keeps into place. `--out DIR` sets a different kit folder.
  `--no-install` writes the kit and changes no repository file.

CI does not run these scripts. Run them by hand when the mark changes, and commit the result.

## Run

The base environment has PySide6, Pillow and fontTools. Add `uharfbuzz` in a separate
folder, so that the base environment does not change:

```sh
python -m pip install --target /tmp/brand-deps uharfbuzz
PYTHONPATH=/tmp/brand-deps python scripts/brand/build.py
```

These versions made the committed files (Linux, 2026-11-10): Python 3.12.13, PySide6
6.11.1, Pillow 12.3.0, fontTools 4.63.0, uharfbuzz 0.57.0. A different Qt or Pillow
version can make different PNG bytes from the same drawing.

## Where the files go

| Folder | Contents |
|---|---|
| `src/tether/gui/assets/brand/` | The application icon (dark tile: PNG 16 to 1024, `tether.ico`, `tether.icns`, the Linux hicolor set), the `.tether` document icon, the twelve interface icons for each theme, the start-page art, the splash art, the two tiles, `tokens.json` and the fonts. The wheel contains this folder. |
| `packaging/images/` | The Windows welcome image (164 × 314), the Windows header image (150 × 57), the installer icon (256 × 256) and the macOS background (1227 × 600). |
| `docs/assets/brand/` | The site logo, the favicon (SVG and ICO), the touch icon, the two lockups, the social card (1200 × 630), `brand.css`, and the marks and tiles that `docs/brand.md` shows. |

The full kit also has files that the repository does not keep: the light application icon
set, the GitHub social preview (1280 × 640), the larger installer images and the token CSS.
`INSTALL` in `build.py` is the complete list of kept files.

## Source and checks

The maintainer approved the kit as the archive `tether-brand-kit.zip` (SHA-256
`3e5f778351bae83831397d8d22d53b0d651193067788d6f2ff1d86858f4f8409`, 2026-11-10). With the
versions above, `build.py` makes the 129 files of that archive again, byte for byte, with three
deliberate changes:

1. **The 16 px icon is fitted to the pixel grid.** At 16 px the curves of the tiny mark fall
   between pixels, so the dots and arcs became grey smears. `glyph_16px` in `brandkit.py`
   puts the dots and the two arcs on whole pixels and keeps the molecule a circle. This
   changes seven files of the kit: the 16 px PNG and the 16 px hicolor PNG of each theme, the
   two `.ico` files, and the site `favicon.ico`. `icon-16px-before-after.png` shows the 16 px
   icon at 8× zoom, before (left) and after (right), dark tile above and light tile below.
   `build.py` writes that image again on each run.
2. **The font license file is the upstream file.** The archive's `OFL-1.1.txt` had a
   rewritten copyright line. The repository keeps the upstream `OFL.txt` instead.
3. **The 1200 × 630 social card fits its canvas** (#683). `social()` draws the mark, the word
   and the tagline for the 1280 × 640 GitHub social preview. At 1200 px the tagline ran past
   the right edge. `social()` now scales that group by the smaller of the two ratios of the
   canvas to 1280 × 640 (never above 1) and keeps it centred, so the social card has a right
   margin in proportion to the preview. This changes one file, `social-card-1200x630.png`.
   The 1280 × 640 preview does not change.

One more difference is in the copy step only, not in the kit: the SVGs in
`docs/assets/brand/` that `docs/brand.md` shows get a `width` and a `height` (`PAGE_PX` in
`build.py`). Without them a browser stretches an SVG that has only a `viewBox` to the full
page width. The drawing does not change.

## Fonts

Red Hat Display, version 1.030, from <https://github.com/RedHatOfficial/RedHatFont> at commit
`6bb1048`, folder `fonts/Proportional/RedHatDisplay/ttf/`. License: SIL Open Font License
1.1. The upstream README states a Reserved Font Name "Red Hat"; the font files are therefore
kept unchanged, with no subset. The SHA-256 of each TTF file in
`src/tether/gui/assets/brand/fonts/` equals that of the upstream file (read from
`raw.githubusercontent.com` on 2026-11-10):

| File | SHA-256 |
|---|---|
| `RedHatDisplay-Regular.ttf` | `ab007b2a5b1590027cb049c3946de67870f6de9fa2420f8ef6e968392219aecd` |
| `RedHatDisplay-Medium.ttf` | `83c3626b8abaa09d8df2d0dc7ead26c4cfe06e3e8c9d0a34afa2910cd92fc501` |
| `RedHatDisplay-Bold.ttf` | `bac4990e2845c796ad0cfc47c3121b09df9285858ba841f7eab8bcdea87b74f1` |

`tests/test_brand_kit.py` checks these values, so a changed or subset font fails the test
suite. `OFL.txt` is the upstream file (SHA-256 `4f1417e2…dff6`) with one trailing space
removed by the `trailing-whitespace` pre-commit hook; its words are unchanged.
