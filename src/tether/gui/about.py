# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Help menu's content: the About box, the documentation link and the log folder (#629).

A bug report needs the exact build, so the About box shows the version, the commit
and one line for each of Tether's three isolated dependency stacks (ADR-0004,
ADR-0047): the base GUI stack this process runs, the tMAVEN sidecar it launches, and
the optional deep stack. Its **Copy** button puts the same text on the clipboard.

Each stack line is read without starting a process, so opening the box is instant:

* **Base** — versions read from the running interpreter.
* **Sidecar** — the interpreter idealization would use (``TETHER_SIDECAR_PYTHON``,
  else the installer's sibling env), read from that env's ``conda-meta`` records and
  its ``tmaven`` package metadata. No path is shown, so a pasted report carries no
  user name.
* **Deep** — PyTorch, read from the running interpreter; the deep stack is a separate
  optional environment (ADR-0047), so the line usually reads "not in this
  environment".

The documentation link points at the installed version's docs: a final release
``X.Y.Z`` opens the ``X.Y`` docs that ``docs.yml`` publishes with mike, and every other
build (a release candidate, a development build) opens ``latest``.

Like the other ``tether.gui`` dialogs, :class:`AboutDialog` *holds* a ``QDialog``
rather than subclassing one, and Qt is imported lazily.
"""

from __future__ import annotations

import os
import platform
import re
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Final

from tether import __version__

if TYPE_CHECKING:
    from pyqtgraph.Qt import QtWidgets

__all__ = [
    "DOCS_ROOT",
    "AboutDialog",
    "about_rows",
    "about_text",
    "build_commit",
    "docs_url",
    "log_folder",
    "open_external",
]

#: The root of the published documentation site (mkdocs ``site_url``).
DOCS_ROOT: Final = "https://bioedca.github.io/tether/"

_FINAL_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_LOCAL_COMMIT = re.compile(r"\+g([0-9a-f]{7,40})(?:\.|$)")
_NOT_INSTALLED = "not installed"


def docs_url(version: str = __version__, page: str = "") -> str:
    """The documentation URL for ``version``, optionally for one ``page`` (e.g. ``"tutorial/"``).

    A final release ``X.Y.Z`` maps to the ``X.Y`` docs label; anything else maps to
    ``latest``, because ``docs.yml`` publishes no label for a pre-release.
    """
    match = _FINAL_RELEASE.match(version)
    label = f"{match.group(1)}.{match.group(2)}" if match else "latest"
    return f"{DOCS_ROOT}{label}/{page}"


def build_commit(version: str = __version__, commit_id: str | None = None) -> str | None:
    """The commit this build came from, or ``None`` when the build does not record one.

    Prefers the ``commit_id`` that hatch-vcs can write into ``_version.py``; otherwise
    reads the ``+g<sha>`` part of a development version. A build made from a tag has
    neither, and the tag that the version names is then the commit.
    """
    if commit_id:
        return commit_id
    match = _LOCAL_COMMIT.search(version)
    return match.group(1) if match else None


def _recorded_commit_id() -> str | None:
    try:
        from tether import _version  # noqa: PLC0415 - generated at build time
    except ImportError:  # pragma: no cover - an uninstalled checkout
        return None
    return getattr(_version, "commit_id", None)


def _dist_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return _NOT_INSTALLED


def _base_stack() -> str:
    parts = [f"Python {platform.python_version()}"]
    for label, dist in (("PySide6", "PySide6"), ("NumPy", "numpy"), ("napari", "napari")):
        parts.append(f"{label} {_dist_version(dist)}")
    return ", ".join(parts)


def _sidecar_prefix() -> Path | None:
    from tether.idealize.driver import SIDECAR_ENV_VAR, bundled_sidecar_python

    configured = os.environ.get(SIDECAR_ENV_VAR)
    interpreter = Path(configured) if configured else bundled_sidecar_python()
    if interpreter is None or not interpreter.exists():
        return None
    # python.exe sits in the env root on Windows and in <prefix>/bin elsewhere.
    for prefix in (interpreter.parent, interpreter.parent.parent):
        if (prefix / "conda-meta").is_dir():
            return prefix
    return interpreter.parent


def _conda_meta_versions(prefix: Path, names: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    meta = prefix / "conda-meta"
    if not meta.is_dir():
        return found
    for record in meta.glob("*.json"):
        # <name>-<version>-<build>.json; a name may itself contain hyphens.
        pieces = record.stem.rsplit("-", 2)
        if len(pieces) == 3 and pieces[0] in names:
            found[pieces[0]] = pieces[1]
    return found


def _tmaven_version(prefix: Path) -> str | None:
    patterns = (
        "lib/python*/site-packages/tmaven-*.dist-info",
        "Lib/site-packages/tmaven-*.dist-info",
    )
    for pattern in patterns:
        for info in sorted(prefix.glob(pattern)):
            return info.name[len("tmaven-") : -len(".dist-info")]
    return None


def _sidecar_stack() -> str:
    try:
        prefix = _sidecar_prefix()
        if prefix is None:
            return "not found (idealization is unavailable)"
        versions = _conda_meta_versions(prefix, ("python", "numpy", "pyqt"))
        tmaven = _tmaven_version(prefix)
    except OSError:
        return "found, versions not readable"
    parts = [
        f"{label} {versions[key]}"
        for label, key in (("Python", "python"), ("NumPy", "numpy"), ("PyQt5", "pyqt"))
        if key in versions
    ]
    if tmaven is not None:
        parts.append(f"tMAVEN {tmaven}")
    return ", ".join(parts) if parts else "found, versions not recorded"


def _deep_stack() -> str:
    torch = _dist_version("torch")
    if torch == _NOT_INSTALLED:
        return "not in this environment (optional)"
    return f"PyTorch {torch}"


def about_rows() -> list[tuple[str, str]]:
    """The ``(label, value)`` rows the About box shows, in display order."""
    commit = build_commit(__version__, _recorded_commit_id())
    return [
        ("Version", __version__),
        ("Commit", commit if commit is not None else "not recorded in this build"),
        ("Base stack", _base_stack()),
        ("Sidecar stack", _sidecar_stack()),
        ("Deep stack", _deep_stack()),
    ]


def about_text(rows: list[tuple[str, str]] | None = None) -> str:
    """The plain text the **Copy** button puts on the clipboard (one row per line)."""
    lines = ["Tether"] + [f"{label}: {value}" for label, value in (rows or about_rows())]
    return "\n".join(lines)


def log_folder() -> Path | None:
    """The folder that holds Tether's log files, or ``None`` when it does not exist yet.

    The location is the per-user application data folder plus ``logs``. Nothing in
    Tether writes a log file yet (the message log is separate work), so the Help menu
    hides **Open log folder** until this folder exists.
    """
    from pyqtgraph.Qt import QtCore

    location = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    if not location:
        return None
    folder = Path(location) / "logs"
    return folder if folder.is_dir() else None


def open_external(url: str) -> bool:
    """Open ``url`` (a web page or a ``file:`` folder) in the system's default handler."""
    from pyqtgraph.Qt import QtCore, QtGui

    return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)))


class AboutDialog:
    """The **Help → About Tether** box: the build rows and a **Copy** button."""

    def __init__(self, *, parent: QtWidgets.QWidget | None = None) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui import theme

        self._rows = about_rows()
        self._dialog = QtWidgets.QDialog(parent)
        self._dialog.setWindowTitle("About Tether")
        layout = QtWidgets.QVBoxLayout(self._dialog)
        title = QtWidgets.QLabel("Tether")
        title.setFont(theme.heading_font())
        layout.addWidget(title)
        form = QtWidgets.QFormLayout()
        for label, value in self._rows:
            field = QtWidgets.QLabel(value)
            field.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            field.setWordWrap(True)
            form.addRow(f"{label}:", field)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        self._copy_button = buttons.addButton(
            "&Copy", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        self._copy_button.setToolTip("Copy these lines for a bug report")
        self._copy_button.clicked.connect(self.copy)
        buttons.rejected.connect(self._dialog.reject)
        layout.addWidget(buttons)

    @property
    def dialog(self) -> QtWidgets.QDialog:
        """The wrapped ``QDialog``."""
        return self._dialog

    @property
    def rows(self) -> list[tuple[str, str]]:
        """The ``(label, value)`` rows shown."""
        return list(self._rows)

    @property
    def copy_button(self) -> QtWidgets.QPushButton:
        """The **Copy** button."""
        return self._copy_button

    def text(self) -> str:
        """The text **Copy** puts on the clipboard."""
        return about_text(self._rows)

    def copy(self) -> str:
        """Put :meth:`text` on the system clipboard and return it."""
        from pyqtgraph.Qt import QtWidgets

        text = self.text()
        QtWidgets.QApplication.clipboard().setText(text)
        return text

    def show(self) -> None:
        self._dialog.show()
