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

from tether.io.deeplasi import (
    DeepLasiExport,
    DeepLasiTraces,
    read_deeplasi_mat,
    read_deeplasi_txt,
    write_deeplasi_txt,
)
from tether.io.filename import ConditionKey, ParsedFilename, parse_filename
from tether.io.intake import (
    AcquisitionFileSet,
    DiscoveryResult,
    FileRole,
    MovieRefCheck,
    MovieReference,
    classify_file,
    discover_acquisitions,
    read_mat_movie_reference,
    read_tdat_movie_reference,
    verify_movie_reference,
)
from tether.io.recover import (
    RecoveredCoordinates,
    SmdCoordinateMatch,
    match_smd_to_coordinates,
    recover_coordinates,
)
from tether.io.tdat import (
    Tdat,
    TdatColocalization,
    TdatCorrections,
    TdatDetectionSettings,
    TdatMovieReference,
    read_detection_settings,
    read_movie_reference,
    read_tdat,
    remap_correction_factors,
)

__all__ = [
    "AcquisitionFileSet",
    "ConditionKey",
    "DeepLasiExport",
    "DeepLasiTraces",
    "DiscoveryResult",
    "FileRole",
    "MovieRefCheck",
    "MovieReference",
    "ParsedFilename",
    "RecoveredCoordinates",
    "SmdCoordinateMatch",
    "Tdat",
    "TdatColocalization",
    "TdatCorrections",
    "TdatDetectionSettings",
    "TdatMovieReference",
    "classify_file",
    "discover_acquisitions",
    "match_smd_to_coordinates",
    "parse_filename",
    "read_deeplasi_mat",
    "read_deeplasi_txt",
    "read_detection_settings",
    "read_mat_movie_reference",
    "read_movie_reference",
    "read_tdat",
    "read_tdat_movie_reference",
    "recover_coordinates",
    "remap_correction_factors",
    "verify_movie_reference",
    "write_deeplasi_txt",
]
