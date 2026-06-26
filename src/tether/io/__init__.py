# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.io — readers, the HDF5 project store, and exporters (PRD §4.2).

Readers for the lazy big-endian TIFF movie, the Deep-LASI ``.tdat`` / ``.tmap``
/ ``.txt`` / ``.mat`` artifacts, and the tMAVEN SMD-HDF5 container; the
``.tether`` HDF5 project store; the filename-to-metadata parser; and exporters
(CSV, Deep-LASI-style ``.txt``, subset ``.tether``, SMD-HDF5). Applies the
Deep-LASI correction-triplet remap on import (PRD Appendix B).
"""

from __future__ import annotations

from tether.io.filename import ConditionKey, ParsedFilename, parse_filename
from tether.io.tdat import (
    Tdat,
    TdatColocalization,
    TdatCorrections,
    read_tdat,
    remap_correction_factors,
)

__all__ = [
    "ConditionKey",
    "ParsedFilename",
    "Tdat",
    "TdatColocalization",
    "TdatCorrections",
    "parse_filename",
    "read_tdat",
    "remap_correction_factors",
]
