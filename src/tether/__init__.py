# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether — a cross-platform single-molecule FRET desktop suite.

Tether is the Mondragón Lab (Northwestern) smFRET analysis application: a
PySide6 shell with an embedded napari movie panel and pyqtgraph trace docks,
backed by a single self-describing HDF5 (``.tether``) project store that carries
full provenance for every datum. See ``docs/PRD.md`` for the product spec.

The package is organized into the eight modules described in PRD §4.2:
[`tether.io`][], [`tether.imaging`][], [`tether.fret`][],
[`tether.idealize`][], [`tether.ml`][], [`tether.analysis`][],
[`tether.gui`][], and [`tether.project`][].
"""

from __future__ import annotations

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - _version.py is generated at build time
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
