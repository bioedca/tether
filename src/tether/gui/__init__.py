# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.gui — the PySide6 shell and dockable analysis surfaces (PRD §4.2).

The PySide6 application shell with an embedded napari movie panel, the
multi-movie round-trip browser, curation/labeling, annotation, and the
pyqtgraph plot docks. This layer is a thin presentation shell over
:mod:`tether.project`.
"""

from __future__ import annotations
