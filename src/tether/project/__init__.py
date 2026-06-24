# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.project — the experiment/session model and headless core (PRD §4.2).

The experiment/session data model plus the batch runner and the headless API.
The GUI (:mod:`tether.gui`) is a thin layer over this core, so every operation
is scriptable without a display.
"""

from __future__ import annotations
