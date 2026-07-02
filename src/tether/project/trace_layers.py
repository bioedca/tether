# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical ``intensity_quantity`` -> ``/traces`` layer mapping (PRD §5).

The single source of truth for which ``/traces`` datasets a given
``intensity_quantity`` selects, shared by idealization (:mod:`tether.project.idealize`),
the tMAVEN hand-off (:mod:`tether.project.handoff`), and analysis
(:mod:`tether.analysis`). Kept dependency-light (a plain dict, no imports) so any
consumer can reference it without pulling a heavy import chain — and so a schema
update happens in exactly one place, never drifting between copies.
"""

from __future__ import annotations

__all__ = ["INTENSITY_QUANTITY_LAYERS"]

#: ``(donor_layer, acceptor_layer)`` ``/traces`` dataset names by ``intensity_quantity``
#: key. ``"corrected"`` = background-subtracted disk intensity (the apparent-E input at
#: M2; photophysical α/γ corrections are M3). ``"raw"`` = pre-background-subtraction.
INTENSITY_QUANTITY_LAYERS: dict[str, tuple[str, str]] = {
    "corrected": ("donor_corrected", "acceptor_corrected"),
    "raw": ("donor_raw", "acceptor_raw"),
}
