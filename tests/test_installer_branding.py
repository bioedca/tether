# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installers show the Tether images and texts, not constructor's defaults (issue #672).

``packaging/scripts/check_installer_branding.py`` reads a built installer on the runner that
built it (``install_smoke.sh``). These tests drive it against small stand-ins for an expanded
macOS package and an unpacked NSIS installer, and pin that the smoke runs it on both.
"""

from __future__ import annotations

import importlib.util
import io
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "packaging" / "scripts" / "check_installer_branding.py"
_IMAGES = _ROOT / "packaging" / "images"
_spec = importlib.util.spec_from_file_location("tether_check_installer_branding", _SCRIPT)
branding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(branding)

TEXTS = {
    "welcome": "This program installs Tether on your computer.",
    "readme": "The installer is not signed.",
    "conclusion": "Tether is installed.",
}


def _distribution(build: str, welcome: bool = True) -> str:
    """A Distribution file in the shape constructor's ``osxpkg.modify_xml`` writes."""
    parts = [
        "<title>Tether 1.0.0</title>",
        f'<license file="{build}/LICENSE.txt"/>',
        f'<background file="{build}/welcome.png" scaling="proportional" alignment="center"/>',
        f'<background-darkAqua file="{build}/welcome.png" scaling="proportional"/>',
        f'<conclusion file="{build}/conclusion.txt" mime-type="text/plain"/>',
        f'<readme file="{build}/readme.txt" mime-type="text/plain"/>',
        '<choice id="x.prepare_installation" title="Install Tether" description=""/>',
    ]
    if welcome:
        parts.append(f'<welcome file="{build}/welcome.txt" mime-type="text/plain"/>')
    return '<installer-gui-script minSpecVersion="1">' + "".join(parts) + "</installer-gui-script>"


def _tether_pkg(root: Path) -> Path:
    resources = root / "Resources"
    resources.mkdir(parents=True)
    shutil.copy(_IMAGES / "macos-welcome-1227x600.png", resources / "welcome.png")
    for key, text in TEXTS.items():
        (resources / f"{key}.txt").write_text(text, encoding="utf-8")
    # The GPL text is shown too; "secondarily" contains "conda" but is not the word.
    (resources / "LICENSE.txt").write_text("directly or secondarily liable", encoding="utf-8")
    (root / "Distribution").write_text(_distribution("/tmp/built_pkgs"), encoding="utf-8")
    return root


def _check_pkg(root: Path) -> list[str]:
    return branding.check_pkg(root, TEXTS, _IMAGES / "macos-welcome-1227x600.png")


def test_a_package_with_the_tether_background_and_texts_passes(tmp_path: Path) -> None:
    assert _check_pkg(_tether_pkg(tmp_path)) == []


def test_constructor_defaults_on_macos_fail_by_name(tmp_path: Path) -> None:
    """No keys set: a generated background, Anaconda's read-me and conclusion, no welcome."""
    root = _tether_pkg(tmp_path)
    resources = root / "Resources"
    Image.new("RGBA", (1227, 600), (40, 160, 70, 255)).save(resources / "welcome.png")
    (resources / "readme.txt").write_text(
        "Anaconda is the most popular Python data science platform.", encoding="utf-8"
    )
    (resources / "conclusion.txt").write_text("Share your packages on Anaconda Cloud!")
    (root / "Distribution").write_text(_distribution("/b", welcome=False), encoding="utf-8")
    problems = "\n".join(_check_pkg(root))
    assert "<background> is not macos-welcome-1227x600.png" in problems
    assert "<background-darkAqua> is not" in problems
    assert "<welcome> does not show" in problems
    assert "<readme> does not show" in problems
    assert "shows 'Anaconda'" in problems


@pytest.mark.parametrize("text", ["Run conda init first.", "CONDA is here", "the anaconda prompt"])
def test_the_words_conda_and_anaconda_fail_wherever_they_are_shown(
    tmp_path: Path, text: str
) -> None:
    root = _tether_pkg(tmp_path)
    (root / "Resources" / "LICENSE.txt").write_text(text, encoding="utf-8")
    assert any("the installer shows" in p for p in _check_pkg(root))


def test_texts_are_read_from_the_osx_lines_of_construct_yaml(tmp_path: Path) -> None:
    recipe = tmp_path / "construct.yaml"
    recipe.write_text(
        'welcome_text: "Hello, Tether."    # [osx]\n'
        'readme_text: "Not for Windows."   # [win]\n'
        "conclusion_text: unquoted          # [osx]\n",
        encoding="utf-8",
    )
    assert branding.expected_texts(recipe) == {"welcome": "Hello, Tether."}


def _nsis(root: Path, welcome: Path, header: Path, icon: Path) -> tuple[Path, Path]:
    """An NSIS installer as 7-Zip unpacks it, with the bitmaps constructor writes."""
    plugins = root / "extracted" / "$PLUGINSDIR"
    plugins.mkdir(parents=True)
    for name, source, size in (
        ("modern-wizard.bmp", welcome, (164, 314)),
        ("modern-header.bmp", header, (150, 57)),
    ):
        with Image.open(source) as image:
            image.resize(size).save(plugins / name)
    frames = branding.icon_frames(icon)
    exe = root / "Tether-1.0.0-Windows-x86_64.exe"
    exe.write_bytes(b"MZ" + bytes(64) + frames[256] + bytes(64))
    return exe, root / "extracted"


def test_a_windows_installer_with_the_tether_images_passes(tmp_path: Path) -> None:
    exe, extracted = _nsis(
        tmp_path,
        _IMAGES / "windows-welcome-164x314.png",
        _IMAGES / "windows-header-150x57.png",
        _IMAGES / "windows-icon-256.png",
    )
    assert branding.check_exe(exe, extracted, _IMAGES / "windows-icon-256.png") == []


def test_constructor_generated_windows_images_fail(tmp_path: Path) -> None:
    blue = tmp_path / "blue.png"
    Image.new("RGB", (256, 314), (0x33, 0x66, 0x99)).save(blue)
    (tmp_path / "pkg").mkdir()
    exe, extracted = _nsis(tmp_path / "pkg", blue, blue, blue)
    problems = "\n".join(branding.check_exe(exe, extracted, _IMAGES / "windows-icon-256.png"))
    assert "modern-wizard.bmp is not windows-welcome-164x314.png" in problems
    assert "modern-header.bmp is not windows-header-150x57.png" in problems
    assert "does not hold the 256 px frame of windows-icon-256.png" in problems


def test_icon_frames_are_the_ico_that_pillow_writes() -> None:
    frames = branding.icon_frames(_IMAGES / "windows-icon-256.png")
    assert 256 in frames
    with Image.open(io.BytesIO(frames[256])) as frame:
        assert frame.size == (256, 256)


def test_main_reports_a_missing_package_and_fails(tmp_path: Path, capsys) -> None:
    assert branding.main(["pkg", str(tmp_path)]) == 1
    assert "::error::installer branding: no Distribution file" in capsys.readouterr().out


def test_install_smoke_checks_the_branding_on_windows_and_macos() -> None:
    code = "\n".join(
        line
        for line in (_ROOT / "packaging" / "scripts" / "install_smoke.sh")
        .read_text(encoding="utf-8")
        .splitlines()
        if not line.lstrip().startswith("#")
    )
    check = "python packaging/scripts/check_installer_branding.py"
    assert re.search(r'7z x .*"\*\.bmp"', code)
    assert f'{check} exe "$(cygpath -w "$exe")" "$brand_dir"' in code
    assert 'pkgutil --expand "$pkg"' in code
    assert f'{check} pkg "$RUNNER_TEMP/installer-branding"' in code
    # Both run before the installer is installed, so a failure names its cause.
    assert code.index(f"{check} exe") < code.index("/S /D=$prefix")
    assert code.index(f"{check} pkg") < code.index("installer -pkg")


#: The three macOS texts as the maintainer approved them on issue #672.
APPROVED = {
    "welcome": (
        "This program installs Tether on your computer. Tether is a desktop application for"
        " single-molecule FRET analysis. The installer contains all necessary files and"
        " downloads nothing."
    ),
    "readme": (
        "The installer is not signed. To make sure that your file is correct, compare its"
        " SHA-256 value with the value on the release page."
    ),
    "conclusion": (
        "Tether is installed. To start Tether, obey the instructions in the install guide at"
        " https://bioedca.github.io/tether/latest/install/."
    ),
}


def test_construct_yaml_sets_the_approved_macos_texts() -> None:
    texts = branding.expected_texts()
    assert texts == APPROVED
    assert not any(branding.FORBIDDEN.search(text) for text in texts.values())
    # The read-me names the check that docs/install.md and ADR-0059 give for a download.
    install = (_ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    assert "SHA-256 value the release lists" in install


@pytest.mark.parametrize(
    ("key", "selector", "name", "size"),
    [
        ("welcome_image", "win", "windows-welcome-164x314.png", (164, 314)),
        ("welcome_image", "osx", "macos-welcome-1227x600.png", (1227, 600)),
        ("header_image", "win", "windows-header-150x57.png", (150, 57)),
        ("icon_image", "win", "windows-icon-256.png", (256, 256)),
    ],
)
def test_construct_yaml_names_each_image_at_the_size_constructor_uses(
    key: str, selector: str, name: str, size: tuple[int, int]
) -> None:
    """The size constructor resizes to, so the brand file reaches the installer unchanged."""
    recipe = (_ROOT / "packaging" / "construct.yaml").read_text(encoding="utf-8")
    assert re.search(rf"^{key}: images/{re.escape(name)}\s+# \[{selector}\]$", recipe, re.M)
    with Image.open(_IMAGES / name) as image:
        assert image.size == size
