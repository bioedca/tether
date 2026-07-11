# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Deep-LASI re-analysis wizard controller (PRD §7.8, M7).

Exercises the Qt-free planning state machine over directly-constructed
:class:`~tether.io.intake.DiscoveryResult` objects (the discovery grouping itself is
covered by ``test_intake.py``), plus one ``from_directory`` integration smoke over
real Deep-LASI filenames.

Reconstruction requires the ``.mat`` (its pre-integrated traces) — a movie + ``.tdat``
alone has coordinates but no traces — so "reconstruct-capable" fixtures carry ``.mat``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tether.gui.deeplasi_wizard import (
    DeepLasiWizard,
    WizardError,
    WizardMode,
    WizardPlan,
    plan_discovery,
)
from tether.io.intake import AcquisitionFileSet, DiscoveryResult, MovieRefCheck

# Real UCKOPSB filenames (PRD Appendix A) — known to group, for the integration smoke.
MOVIE = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
TDAT = "DeepLASI_DATA_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif2025-07-21_00-00.tdat"
MAT = "DeepLASI_MAT_export_Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.mat"
ACQ_STEM = "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010"


def _fs(
    key: str = "acq_010",
    *,
    movie: bool = False,
    tdat: bool = False,
    mat: bool = False,
    txt: bool = False,
    smd: bool = False,
    warnings: tuple[str, ...] = (),
) -> AcquisitionFileSet:
    """An :class:`AcquisitionFileSet` with dummy paths for the requested roles."""

    def p(ext: str) -> Path:
        return Path("/data") / f"{key}.{ext}"

    return AcquisitionFileSet(
        key=key,
        condition_id="cond-x",
        video_index="010",
        movie=p("tif") if movie else None,
        tdat=p("tdat") if tdat else None,
        mat=p("mat") if mat else None,
        txt=p("txt") if txt else None,
        smd=p("hdf5") if smd else None,
        warnings=warnings,
    )


def _discovery(
    *filesets: AcquisitionFileSet,
    unpaired: tuple[Path, ...] = (),
    ignored: tuple[Path, ...] = (),
    shared_maps: tuple[Path, ...] = (),
) -> DiscoveryResult:
    return DiscoveryResult(
        acquisitions=filesets,
        shared_maps=shared_maps,
        ignored=ignored,
        unpaired=unpaired,
    )


def _wizard(*filesets: AcquisitionFileSet, **kw: object) -> DeepLasiWizard:
    return DeepLasiWizard(_discovery(*filesets, **kw))  # type: ignore[arg-type]


# --- default plan proposal ---------------------------------------------------


def test_round_trip_with_mat_only_reconstructs_from_mat() -> None:
    (plan,) = plan_discovery(_discovery(_fs(movie=True, mat=True)))
    assert plan.mode is WizardMode.RECONSTRUCT
    assert plan.coordinate_source == "mat"
    assert plan.output_name == "acq_010.tether"


def test_tdat_preferred_over_mat_when_both_present() -> None:
    (plan,) = plan_discovery(_discovery(_fs(movie=True, tdat=True, mat=True)))
    assert plan.mode is WizardMode.RECONSTRUCT
    assert plan.coordinate_source == "tdat"


def test_movie_and_tdat_without_mat_cannot_reconstruct() -> None:
    # The .tdat has coordinates but no traces; reconstruct needs the .mat. With no
    # SMD/.txt to fall back on, the set is blocked → skipped (the contract fix).
    (plan,) = plan_discovery(_discovery(_fs(movie=True, tdat=True)))
    assert plan.mode is WizardMode.SKIP
    assert ".mat" in plan.rationale


def test_movie_tdat_smd_without_mat_falls_back_to_analysis_only() -> None:
    # A failing reconstruct must not be preferred over a workable analysis-only path.
    (plan,) = plan_discovery(_discovery(_fs(movie=True, tdat=True, smd=True)))
    assert plan.mode is WizardMode.ANALYSIS_ONLY


@pytest.mark.parametrize(
    "fileset",
    [
        _fs(movie=True, smd=True),  # movie + intensity, but no coordinate source
        _fs(txt=True),
        _fs(smd=True),
    ],
)
def test_no_coordinate_source_defaults_to_analysis_only(fileset: AcquisitionFileSet) -> None:
    (plan,) = plan_discovery(_discovery(fileset))
    assert plan.mode is WizardMode.ANALYSIS_ONLY
    assert plan.coordinate_source == ""


@pytest.mark.parametrize(
    "fileset",
    [
        _fs(movie=True),  # movie alone: nothing to extract or analyze
        _fs(mat=True),  # .mat but no movie to link pixels
        _fs(tdat=True),  # coordinate source but no movie and no traces
        _fs(movie=True, tdat=True),  # coordinates but no .mat traces, no SMD/.txt
    ],
)
def test_unsupported_set_is_skipped_and_explained(fileset: AcquisitionFileSet) -> None:
    (plan,) = plan_discovery(_discovery(fileset))
    assert plan.mode is WizardMode.SKIP
    assert not plan.runnable
    assert plan.warnings  # the blocking reason is surfaced as a warning
    assert "no" in plan.rationale


def test_warnings_carried_from_the_fileset() -> None:
    (plan,) = plan_discovery(_discovery(_fs(movie=True, mat=True, warnings=("dup .tdat",))))
    assert "dup .tdat" in plan.warnings


# --- controller construction -------------------------------------------------


def test_wizard_plans_match_plan_discovery() -> None:
    discovery = _discovery(_fs("a", movie=True, mat=True), _fs("b", txt=True))
    assert DeepLasiWizard(discovery).plans == plan_discovery(discovery)


def test_from_directory_empty_is_not_ready(tmp_path: Path) -> None:
    wizard = DeepLasiWizard.from_directory(tmp_path)
    assert wizard.plans == ()
    assert not wizard.is_ready
    with pytest.raises(WizardError, match="not ready"):
        wizard.finalize()


def test_from_directory_groups_and_plans_real_names(tmp_path: Path) -> None:
    for name in (MOVIE, TDAT, MAT):
        (tmp_path / name).touch()
    wizard = DeepLasiWizard.from_directory(tmp_path)
    (plan,) = wizard.plans
    assert plan.key == ACQ_STEM
    assert plan.mode is WizardMode.RECONSTRUCT  # movie + .mat traces + .tdat coordinates
    assert plan.coordinate_source == "tdat"
    assert wizard.is_ready


# --- mode edits --------------------------------------------------------------


def test_set_mode_to_analysis_only_retains_coordinate_source() -> None:
    wizard = _wizard(_fs(movie=True, mat=True, txt=True))  # reconstruct-capable + txt
    updated = wizard.set_mode("acq_010", WizardMode.ANALYSIS_ONLY)
    assert updated.mode is WizardMode.ANALYSIS_ONLY
    assert updated.coordinate_source == "mat"  # retained, not cleared


def test_set_mode_analysis_only_rejected_without_intensity_source() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))  # reconstruct-capable, no smd/.txt
    with pytest.raises(WizardError, match="no SMD or .txt"):
        wizard.set_mode("acq_010", WizardMode.ANALYSIS_ONLY)


def test_set_mode_reconstruct_rejected_without_mat() -> None:
    wizard = _wizard(_fs(movie=True, tdat=True, smd=True))  # coords but no .mat traces
    with pytest.raises(WizardError, match=r"cannot reconstruct.*\.mat"):
        wizard.set_mode("acq_010", WizardMode.RECONSTRUCT)


def test_set_mode_accepts_plain_string() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    assert wizard.set_mode("acq_010", "skip").mode is WizardMode.SKIP


# --- coordinate-source edits -------------------------------------------------


def test_set_coordinate_source_switches_between_available() -> None:
    wizard = _wizard(_fs(movie=True, tdat=True, mat=True))
    assert wizard.set_coordinate_source("acq_010", "mat").coordinate_source == "mat"


def test_set_coordinate_source_rejects_absent_source() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))  # no .tdat
    with pytest.raises(WizardError, match="no 'tdat' coordinate source"):
        wizard.set_coordinate_source("acq_010", "tdat")


def test_set_coordinate_source_rejected_when_not_reconstruct_capable() -> None:
    wizard = _wizard(_fs(txt=True))  # analysis-only, never reconstructs
    with pytest.raises(WizardError, match="not reconstruct-capable"):
        wizard.set_coordinate_source("acq_010", "tdat")


def test_coordinate_source_choice_survives_exclude_include() -> None:
    # Regression: the user's 'mat' pick must not silently revert to the preferred
    # 'tdat' across a skip round-trip (a both-sources set is required to catch it).
    wizard = _wizard(_fs(movie=True, tdat=True, mat=True))
    wizard.set_coordinate_source("acq_010", "mat")
    wizard.exclude("acq_010")
    restored = wizard.include("acq_010")
    assert restored.mode is WizardMode.RECONSTRUCT
    assert restored.coordinate_source == "mat"


# --- output-name + category edits --------------------------------------------


def test_set_output_name_enforces_tether_suffix() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    assert wizard.set_output_name("acq_010", "custom").output_name == "custom.tether"
    assert wizard.set_output_name("acq_010", "  keep.tether  ").output_name == "keep.tether"


def test_set_output_name_rejects_empty() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    with pytest.raises(WizardError, match="cannot be empty"):
        wizard.set_output_name("acq_010", "   ")


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "..\\escape",
        "sub/dir",
        "sub\\dir",
        "/abs.tether",
        "\\abs.tether",
        "C:\\abs.tether",
        "C:rel.tether",
        ".",
        "..",
    ],
)
def test_set_output_name_rejects_path_like_names(name) -> None:
    # The executor writes output_dir / output_name, so a path-like name could escape
    # the destination (traversal / absolute / drive). The controller rejects them.
    wizard = _wizard(_fs(movie=True, mat=True))
    with pytest.raises(WizardError, match="bare filename"):
        wizard.set_output_name("acq_010", name)
    # The plan is unchanged after a rejected edit.
    assert wizard.plans[0].output_name == "acq_010.tether"


def test_set_categories() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    assert wizard.set_categories("acq_010", ["dynamic", "static"]).categories == (
        "dynamic",
        "static",
    )


# --- exclude / include -------------------------------------------------------


def test_exclude_then_include_round_trips_to_default_mode() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    assert wizard.exclude("acq_010").mode is WizardMode.SKIP
    assert wizard.include("acq_010").mode is WizardMode.RECONSTRUCT


def test_include_preserves_user_state() -> None:
    wizard = _wizard(_fs(movie=True, tdat=True, mat=True))
    wizard.set_output_name("acq_010", "renamed")
    wizard.set_categories("acq_010", ["a"])
    wizard.set_coordinate_source("acq_010", "mat")
    check = MovieRefCheck(status="mismatch", expected="x.tif", found="y.tif", message="suspect")
    wizard.annotate_movie_ref("acq_010", check)
    wizard.exclude("acq_010")
    restored = wizard.include("acq_010")
    assert restored.output_name == "renamed.tether"
    assert restored.categories == ("a",)
    assert restored.coordinate_source == "mat"
    assert restored.movie_ref is check
    assert "suspect" in restored.warnings


def test_include_rejects_an_already_included_plan() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))  # default RECONSTRUCT (runnable)
    with pytest.raises(WizardError, match="already included"):
        wizard.include("acq_010")


def test_include_rejects_a_blocked_set() -> None:
    wizard = _wizard(_fs(movie=True))  # blocked: movie only → default SKIP
    with pytest.raises(WizardError, match="cannot be included"):
        wizard.include("acq_010")


def test_unknown_key_raises() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    with pytest.raises(WizardError, match="no acquisition with key"):
        wizard.set_mode("nope", WizardMode.SKIP)


# --- summary + readiness -----------------------------------------------------


def test_summary_counts_modes() -> None:
    wizard = _wizard(
        _fs("a", movie=True, mat=True),  # reconstruct
        _fs("b", txt=True),  # analysis-only
        _fs("c", movie=True),  # blocked → skip
    )
    summary = wizard.summary()
    assert (summary.n_total, summary.n_reconstruct, summary.n_analysis_only, summary.n_skipped) == (
        3,
        1,
        1,
        1,
    )
    assert summary.n_runnable == 2
    assert summary.is_ready


def test_advisories_report_unpaired_and_ignored() -> None:
    wizard = _wizard(
        _fs(movie=True, mat=True),
        unpaired=(Path("/data/orphan.hdf5"),),
        ignored=(Path("/data/notes.log"),),
    )
    advisories = wizard.summary().advisories
    assert any("SMD" in a for a in advisories)
    assert any("unrecognized" in a for a in advisories)


def test_output_name_collision_blocks_the_run() -> None:
    wizard = _wizard(_fs("a", movie=True, mat=True), _fs("b", movie=True, mat=True))
    wizard.set_output_name("a", "same")
    wizard.set_output_name("b", "same")
    summary = wizard.summary()
    assert not summary.is_ready
    assert any("output name" in b for b in summary.blocking)
    with pytest.raises(WizardError, match="not ready"):
        wizard.finalize()


def test_all_skipped_is_not_ready() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    wizard.exclude("acq_010")
    summary = wizard.summary()
    assert not summary.is_ready
    assert any("no acquisition is selected" in b for b in summary.blocking)


# --- finalize ----------------------------------------------------------------


def test_finalize_partitions_runnable_and_skipped_and_carries_context() -> None:
    unpaired = (Path("/data/orphan.hdf5"),)
    ignored = (Path("/data/notes.log"),)
    shared_maps = (Path("/data/map.tmap"),)
    wizard = _wizard(
        _fs("a", movie=True, mat=True),  # runnable
        _fs("b", movie=True),  # blocked → skipped
        unpaired=unpaired,
        ignored=ignored,
        shared_maps=shared_maps,
    )
    plan = wizard.finalize()
    assert isinstance(plan, WizardPlan)
    assert [p.key for p in plan.acquisitions] == ["a"]
    assert [p.key for p in plan.skipped] == ["b"]
    assert plan.unpaired == unpaired
    assert plan.ignored == ignored
    assert plan.shared_maps == shared_maps


# --- movie-reference annotation ----------------------------------------------


def test_annotate_movie_ref_mismatch_adds_warning() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    check = MovieRefCheck(
        status="mismatch", expected="other.tif", found="acq.tif", message="pairing suspect"
    )
    updated = wizard.annotate_movie_ref("acq_010", check)
    assert updated.movie_ref is check
    assert "pairing suspect" in updated.warnings


def test_annotate_movie_ref_confirmed_adds_no_warning() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    check = MovieRefCheck(status="confirmed", expected="acq.tif", found="acq.tif", message="ok")
    updated = wizard.annotate_movie_ref("acq_010", check)
    assert updated.movie_ref is check
    assert updated.warnings == ()


def test_annotate_movie_ref_resolved_mismatch_clears_stale_warning() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    wizard.annotate_movie_ref(
        "acq_010",
        MovieRefCheck(status="mismatch", expected="x.tif", found="y.tif", message="suspect"),
    )
    resolved = wizard.annotate_movie_ref(
        "acq_010",
        MovieRefCheck(status="confirmed", expected="y.tif", found="y.tif", message="ok"),
    )
    assert "suspect" not in resolved.warnings
    assert resolved.warnings == ()


def test_annotate_movie_ref_replaces_prior_mismatch_warning() -> None:
    wizard = _wizard(_fs(movie=True, mat=True))
    wizard.annotate_movie_ref(
        "acq_010",
        MovieRefCheck(status="mismatch", expected="a.tif", found="c.tif", message="first"),
    )
    updated = wizard.annotate_movie_ref(
        "acq_010",
        MovieRefCheck(status="mismatch", expected="b.tif", found="c.tif", message="second"),
    )
    assert updated.warnings == ("second",)


def test_annotate_movie_ref_movie_absent_adds_warning() -> None:
    wizard = _wizard(_fs(mat=True, smd=True))  # analysis-only; movie absent per the ref
    check = MovieRefCheck(
        status="movie_absent", expected="missing.tif", found="", message="locate it"
    )
    updated = wizard.annotate_movie_ref("acq_010", check)
    assert "locate it" in updated.warnings


# --- mode value round-trips --------------------------------------------------


def test_mode_is_str_enum() -> None:
    # a StrEnum so a mode round-trips through logs/JSON as its value
    assert WizardMode.RECONSTRUCT == "reconstruct"
    assert WizardMode("analysis_only") is WizardMode.ANALYSIS_ONLY
