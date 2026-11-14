# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The folder the install guide puts on ``PATH`` holds the two launchers only (#492, ADR-0094).

Up to rc3 the installer wrote ``tether`` and ``tether-gui`` into ``<prefix>/bin``. On Linux and
macOS that is the ``bin`` of constructor's base env, the python + conda bootstrap, so a user
who put it on ``PATH`` also got that env's ``python``, ``pip``, ``conda`` and about 120 other
programs. The launchers now go into ``<prefix>/launchers``, which nothing else writes to.

* ``post_install.sh``, run against a stub prefix, leaves exactly the two launchers in that
  folder, and the folder on ``PATH`` starts the env's own entry points.
* ``post_install.bat`` writes exactly ``tether.bat`` and ``tether-gui.bat`` there.
* ``install_smoke.sh`` lists the folder on each system and fails on anything extra, and its
  launch checks go through the new folder, so both ``packaging.yml`` and ``release.yml`` catch
  a regression on a real install.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "packaging" / "scripts"
POST_INSTALL_SH = _SCRIPTS / "post_install.sh"
POST_INSTALL_BAT = _SCRIPTS / "post_install.bat"
INSTALL_SMOKE = _SCRIPTS / "install_smoke.sh"

UNIX_LAUNCHERS = ["tether", "tether-gui"]
WINDOWS_LAUNCHERS = ["tether-gui.bat", "tether.bat"]


def _code_lines(path: Path, comment: str) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().lower().startswith(comment)
    ]


def _stub_prefix(tmp_path: Path) -> Path:
    """A prefix with no base env, whose env interpreters skip pip.

    ``envs/tether/bin`` also gets the two entry points as small scripts that print their
    own name and arguments, so a launcher can be run end to end.
    """
    prefix = tmp_path / "prefix with space"
    for env in ("tether", "sidecar"):
        bin_d = prefix / "envs" / env / "bin"
        bin_d.mkdir(parents=True)
        stub = bin_d / "python"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
    for name in UNIX_LAUNCHERS:
        entry = prefix / "envs" / "tether" / "bin" / name
        entry.write_text(f'#!/bin/sh\necho "{name} $*"\n', encoding="utf-8")
        entry.chmod(0o755)
    (prefix / "wheelhouse").mkdir()
    return prefix


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None,
    reason="post_install.sh is the Linux and macOS post-install script",
)
def test_post_install_sh_writes_only_the_two_launchers(tmp_path: Path) -> None:
    prefix = _stub_prefix(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "PREFIX": str(prefix), "HOME": str(home)}
    subprocess.run(["sh", str(POST_INSTALL_SH)], check=True, env=env, capture_output=True)

    launch_d = prefix / "launchers"
    assert sorted(p.name for p in launch_d.iterdir()) == UNIX_LAUNCHERS
    assert not (prefix / "bin").exists(), "nothing may be written into the base env's bin"
    for name in UNIX_LAUNCHERS:
        assert os.access(launch_d / name, os.X_OK), name
        run_env = {**os.environ, "PATH": f"{launch_d}{os.pathsep}{os.environ['PATH']}"}
        out = subprocess.run(
            [name, "--version"], check=True, env=run_env, capture_output=True, text=True
        )
        assert out.stdout.strip() == f"{name} --version"


def test_post_install_bat_writes_only_the_two_launchers() -> None:
    code = _code_lines(POST_INSTALL_BAT, "rem")
    text = "\n".join(code)
    assert re.search(r'(?m)^set "LAUNCH_D=%PREFIX%\\launchers"$', text)
    written = {m.group(1) for ln in code if (m := re.match(r'>>?\s*"%LAUNCH_D%\\([^"]+)"', ln))}
    assert sorted(written) == WINDOWS_LAUNCHERS
    assert "%PREFIX%\\bin" not in text, "the launchers no longer go into %PREFIX%\\bin"


def test_post_install_sh_names_the_launchers_folder() -> None:
    text = "\n".join(_code_lines(POST_INSTALL_SH, "#"))
    assert 'LAUNCH_D="$PREFIX/launchers"' in text
    assert '"$PREFIX/bin' not in text, "the launchers no longer go into $PREFIX/bin"


def test_install_smoke_asserts_the_folder_holds_only_the_launchers() -> None:
    text = "\n".join(_code_lines(INSTALL_SMOKE, "#"))
    assert 'ls -A "$prefix_u/launchers" | LC_ALL=C sort' in text
    assert 'test "$launchers" = "tether-gui.bat tether.bat "' in text
    assert 'ls -A "$prefix/launchers" | LC_ALL=C sort' in text
    assert 'test "$launchers" = "tether tether-gui "' in text


def test_install_smoke_launches_through_the_launchers_folder() -> None:
    text = "\n".join(_code_lines(INSTALL_SMOKE, "#"))
    assert 'PATH="$prefix/launchers:$PATH" tether --version' in text
    assert 'PATH="$prefix/launchers:$PATH" tether-gui --version' in text
    assert 'cmd /c "$prefix\\\\launchers\\\\tether.bat" --version' in text
    assert '"$prefix/bin' not in text and "$prefix_u/bin" not in text
    # The one bin\ path left is the seed of an rc3-format shortcut, which rc3 aimed there.
    assert [ln for ln in text.splitlines() if "\\\\bin\\\\" in ln] == [
        ln for ln in text.splitlines() if "OLD_BAT_W=" in ln
    ]
