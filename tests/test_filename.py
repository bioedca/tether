# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the filename→metadata parser (PRD §5.1, §7.6, §9 M0).

Exercises the real ``example-data/`` acquisition filenames (PRD Appendix A): the
UCKOPSB T-box FRET set and the Cy3-only leakage-calibration sample. The M0
done-criterion is "the parser round-trips a known condition string".
"""

from __future__ import annotations

import pytest

from tether.io import ConditionKey, ParsedFilename, parse_filename

# Real filenames from example-data/ (PRD Appendix A).
UCKOPSB_TIF = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
UCKOPSB_TDAT = "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif2025-07-21_00-00.tdat"
UCKOPSB_MAT = "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"
UCKOPSB_TXT = "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010-donc-accc-w.txt"
UCKOPSB_MAP = "DeepLASI_MAP_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_20250718_2025-07-18_13-40.tmap"
CY3_TDAT = "DeepLASI_DATA_Cy3_only_WCBN_2ndreplicate_15pM_001_2026-06-22_15-33.tdat"


def test_parse_uckopsb_movie() -> None:
    parsed = parse_filename(UCKOPSB_TIF)
    assert parsed.key.construct_variant == "Bla UCKOPSB T-box"
    assert parsed.key.ligand == "tRNA"
    assert parsed.key.ligand_concentration == 600.0
    assert parsed.key.ligand_concentration_unit == "nM"
    assert parsed.key.dye == ""  # no dye token in the FRET filename (filled at M4)
    assert parsed.sample_concentration == 35.0
    assert parsed.sample_concentration_unit == "pM"
    assert parsed.video_index == "010"
    assert parsed.source_filename == UCKOPSB_TIF


def test_parse_cy3_donor_only() -> None:
    parsed = parse_filename(CY3_TDAT)
    assert parsed.key.dye == "Cy3"
    assert parsed.key.construct_variant == "WCBN"
    assert parsed.key.ligand == ""  # donor-only calibration, no ligand
    assert parsed.replicate == "2"
    assert parsed.sample_concentration == 15.0
    assert parsed.sample_concentration_unit == "pM"
    assert parsed.video_index == "001"
    assert parsed.date == "2026-06-22"


def test_deeplasi_prefixes_and_extensions_stripped() -> None:
    # The embedded source ``.tif`` and the trailing wall-clock stamp are removed.
    parsed = parse_filename(UCKOPSB_TDAT)
    assert parsed.key.construct_variant == "Bla UCKOPSB T-box"
    assert parsed.date == "2025-07-21"
    assert parsed.video_index == "010"


def test_corrected_intensity_suffix_stripped() -> None:
    parsed = parse_filename(UCKOPSB_TXT)
    assert "donc" not in parsed.key.construct_variant
    assert parsed.key.ligand == "tRNA"
    assert parsed.video_index == "010"


def test_compact_date_token_parsed() -> None:
    parsed = parse_filename(UCKOPSB_MAP)
    assert parsed.date == "2025-07-18"
    assert parsed.key.construct_variant == "Bla UCKOPSB T-box"


def test_same_condition_shares_id_across_files() -> None:
    # The .tif, .tdat, and .mat of one acquisition differ in date/video but share
    # the chemistry/optics key, so they resolve to the same condition (PRD §5.1).
    ids = {parse_filename(n).condition_id for n in (UCKOPSB_TIF, UCKOPSB_TDAT, UCKOPSB_MAT)}
    assert len(ids) == 1
    only = next(iter(ids))
    assert only.startswith("cond-")


def test_different_condition_distinct_id() -> None:
    assert parse_filename(UCKOPSB_TIF).condition_id != parse_filename(CY3_TDAT).condition_id


def test_condition_id_is_deterministic() -> None:
    # Content hash, no salt → stable across runs/platforms (PRD §5.1 join key).
    assert parse_filename(UCKOPSB_TIF).condition_id == parse_filename(UCKOPSB_TIF).condition_id


@pytest.mark.parametrize(
    "key",
    [
        ConditionKey(),  # all-empty
        ConditionKey(
            construct_variant="Bla UCKOPSB T-box",
            ligand="tRNA",
            ligand_concentration=600.0,
            ligand_concentration_unit="nM",
        ),
        ConditionKey(construct_variant="WCBN", dye="Cy3"),
        ConditionKey(buffer="T50", temperature_c=23.5, laser_power=2.0),
    ],
)
def test_condition_key_canonical_round_trip(key: ConditionKey) -> None:
    # The defining round-trip: from_canonical ∘ to_canonical is the identity.
    assert ConditionKey.from_canonical(key.to_canonical()) == key


def test_unrecognized_name_never_raises() -> None:
    parsed = parse_filename("random_unstructured_name.dat")
    assert isinstance(parsed, ParsedFilename)
    assert parsed.condition_id.startswith("cond-")
    # Tokens it could not classify are preserved for provenance.
    assert parsed.unparsed_tokens
