# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The dependency graph table agrees with ADR-0103, and the stale rules hold on it (#893).

ADR-0103 gives the graph of a project as two Markdown tables, and
:mod:`tether.project.dependency_graph` holds the same graph as data that code reads. The first
group of tests reads the two tables of the ADR and requires them to equal the module, cell by
cell, so the record and the code cannot drift apart. The second group checks the graph itself:
no cycle, every edge between known nodes, and each rule of the workflow design (G05 note 03,
G03, G04, G02 note 04) as a statement about the transitive closure. The third group checks that
the idealization stale rule of today (ADR-0024, ADR-0029) is three edges into ``fit`` and no more.

The module is Qt-free and numpy-free, so this runs in the default matrix on all three systems.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tether.project import dependency_graph as g

REPO = Path(__file__).resolve().parents[1]
ADR = REPO / "docs" / "adr" / "0103-the-project-dependency-graph-and-the-stale-rules.md"

# An unescaped pipe is a cell boundary (the rule of scripts/gen_adr_index.py).
_CELL_RE = re.compile(r"(?<!\\)\|")


def _table(tag: str) -> list[list[str]]:
    """The body rows of the table between the ``graph:<tag>`` markers, as normalized cells."""
    text = ADR.read_text(encoding="utf-8")
    match = re.search(rf"<!-- graph:{tag} start -->\n(.*?)<!-- graph:{tag} end -->", text, re.S)
    assert match, f"ADR-0103 has no graph:{tag} block"
    lines = [ln for ln in match.group(1).splitlines() if ln.strip()]
    assert len(lines) >= 2, f"graph:{tag} block holds no table"
    rows = []
    for line in lines[2:]:  # skip the header and the separator
        cells = _CELL_RE.split(line.strip())[1:-1]
        rows.append([_norm(c) for c in cells])
    return rows


def _norm(cell: str) -> str:
    """One table cell as plain text: no code spans, no escaped pipes, a dash for empty."""
    text = cell.strip().replace("\\|", "|").replace("`", "")
    return "" if text == "—" else text


def _plain(text: str) -> str:
    return text.replace("`", "")


# --- the ADR and the module agree --------------------------------------------


def test_adr_node_table_equals_the_module() -> None:
    expected = [
        [
            n.key,
            n.name,
            _plain(n.diagram_block),
            n.identity_kind,
            _plain(n.identity),
            _plain(n.existing_stamp),
            _plain(n.when_stale),
        ]
        for n in g.NODES
    ]
    assert _table("nodes") == expected


def test_adr_edge_table_equals_the_module() -> None:
    expected = [
        [
            str(e.number),
            e.upstream,
            e.downstream,
            _plain(e.event),
            _plain(e.guard),
            _plain(e.diagram_block),
        ]
        for e in g.EDGES
    ]
    assert _table("edges") == expected


def test_adr_counts_match_the_prose() -> None:
    text = ADR.read_text(encoding="utf-8")
    words = {15: "fifteen", 29: "twenty-nine"}
    assert len(g.NODES) == 15 and len(g.EDGES) == 29
    assert f"{words[len(g.NODES)]} nodes and {words[len(g.EDGES)]} edges" in text


# --- the shape of the graph ---------------------------------------------------


def test_every_node_has_a_known_identity_kind_and_a_diagram_block() -> None:
    for n in g.NODES:
        assert n.identity_kind in g.IDENTITY_KINDS, n.key
        assert n.diagram_block.startswith("G0"), n.key
        assert n.identity and n.existing_stamp and n.when_stale, n.key


def test_edges_join_known_nodes_and_are_numbered_in_order() -> None:
    assert [e.number for e in g.EDGES] == list(range(1, len(g.EDGES) + 1))
    pairs = [(e.upstream, e.downstream) for e in g.EDGES]
    assert len(pairs) == len(set(pairs)), "two edges join the same pair of nodes"
    for e in g.EDGES:
        assert e.upstream in g.NODE_BY_KEY and e.downstream in g.NODE_BY_KEY, e
        assert e.upstream != e.downstream, e
        assert e.event and e.diagram_block.startswith("G0"), e


def test_the_graph_has_no_cycle() -> None:
    for n in g.NODES:
        assert n.key not in g.descendants(n.key), f"{n.key} is its own descendant"


def test_root_nodes_are_the_nodes_with_no_upstream() -> None:
    downstreams = {e.downstream for e in g.EDGES}
    roots = {n.key for n in g.NODES if n.key not in downstreams}
    assert roots == {"source_movie", "geometry_recipe", "calibration_windows"}
    for key in roots:
        assert g.node(key).when_stale.startswith("Root:"), key


def test_unknown_keys_fail_loudly() -> None:
    with pytest.raises(KeyError, match="no node"):
        g.node("trace")
    with pytest.raises(KeyError):
        g.descendants("trace")
    with pytest.raises(KeyError):
        g.edges_from("trace")


def test_the_tables_are_read_only() -> None:
    with pytest.raises(TypeError):
        g.NODE_BY_KEY["x"] = g.NODES[0]  # type: ignore[index]
    with pytest.raises(AttributeError):
        g.NODES[0].key = "x"  # type: ignore[misc]


def test_every_stale_record_permits_the_three_things() -> None:
    text = " ".join(g.STALE_PERMITS)
    assert "stays readable" in text and "keeps its stale mark" in text
    assert "never supplies a current result" in text
    assert "Nothing is deleted" in text


# --- the rules of the design on the closure -----------------------------------

_DOWNSTREAM_OF_EXTRACTION = {
    "trace_generation",
    "curation_labels",
    "analysis_windows",
    "correction_version",
    "review_snapshot",
    "analysis_set_version",
    "fit",
    "result_snapshot",
    "package_manifest",
    "deposit_record",
}
_CALIBRATION = {"calibration_windows", "factor_version", "correction_version"}


@pytest.mark.parametrize("edit", ["map", "geometry_recipe"])
def test_g05_map_or_geometry_edit_stales_extraction_and_descendants(edit: str) -> None:
    always = g.descendants(edit, follow_guarded=False)
    assert "trace_generation" in always
    assert always >= _DOWNSTREAM_OF_EXTRACTION
    assert g.descendants("trace_generation") <= g.descendants(edit)


@pytest.mark.parametrize("edit", ["factor_version", "calibration_windows"])
def test_g05_factor_or_calibration_window_edit_stales_corrected_data_and_descendants(
    edit: str,
) -> None:
    always = g.descendants(edit, follow_guarded=False)
    assert "correction_version" in always
    assert g.descendants("correction_version", follow_guarded=False) <= always
    # never upstream of the calibration: extraction and labels stay current
    assert not always & {"trace_generation", "curation_labels", "analysis_windows", "map"}


def test_g05_analysis_window_edit_stales_dependent_fits_and_plots() -> None:
    always = g.descendants("analysis_windows", follow_guarded=False)
    assert {"fit", "result_snapshot"} <= always
    # G03 note 03: it stales gamma only if the estimator's inputs changed - a guarded edge
    assert "factor_version" not in always
    assert "factor_version" in g.descendants("analysis_windows")
    (edge,) = [
        e for e in g.EDGES if (e.upstream, e.downstream) == ("analysis_windows", "factor_version")
    ]
    assert "inputs of the γ estimator" in edge.guard


def test_g05_membership_edit_stales_only_that_sets_results_never_the_calibration() -> None:
    stale = g.descendants("analysis_set_version")
    assert stale == {"fit", "result_snapshot", "package_manifest", "deposit_record"}
    assert not stale & _CALIBRATION
    assert "review_snapshot" not in stale


def test_new_trace_generation_stales_the_fits_and_corrections_of_the_old_one() -> None:
    always = g.descendants("trace_generation", follow_guarded=False)
    assert {"fit", "correction_version"} <= always
    # G02 note 04: labels and windows are carried and need reconciliation
    assert {"curation_labels", "analysis_windows"} <= always


@pytest.mark.parametrize(
    "phrase",
    [
        "calibration evidence changes",
        "factor / contributor evidence changes",
        "edit analysis windows / curation",
        "Membership changed",
    ],
)
def test_the_g03_and_g04_events_are_in_the_edge_list(phrase: str) -> None:
    assert any(phrase in e.event for e in g.EDGES), phrase
    assert any(phrase in e.diagram_block for e in g.EDGES), phrase


@pytest.mark.parametrize(
    "event", ["Map edit", "Geometry edit", "Factor edit", "Calibration-window edit"]
)
def test_the_g05_note_03_edits_are_events(event: str) -> None:
    assert any(e.event.startswith(event) for e in g.EDGES), event


def test_a_stale_fit_never_supplies_a_current_result() -> None:
    assert "result_snapshot" in {e.downstream for e in g.edges_from("fit")}
    assert "never" in g.node("fit").when_stale


def test_guarded_edges_drop_out_with_follow_guarded_false() -> None:
    for e in g.EDGES:
        unguarded = g.edges_from(e.upstream, follow_guarded=False)
        assert (e in unguarded) is (not e.guarded), e


# --- the idealization rule of today is three edges ----------------------------


def test_the_idealization_rule_is_three_edges_into_fit() -> None:
    project_level = {e.upstream for e in g.EDGES if e.downstream == "fit"} - {
        "analysis_set_version"
    }
    # input_trace_hash, the window bounds, the effective alpha and gamma (ADR-0029)
    assert project_level == {"trace_generation", "analysis_windows", "correction_version"}
    # no label edge: a label edit does not stale a project-level idealization, as today
    assert "curation_labels" not in project_level


def test_the_rule_names_the_hash_inputs_that_exist() -> None:
    from tether.project import idealize

    fit = g.node("fit")
    assert "input_provenance_hash" in fit.identity
    assert hasattr(idealize, "input_provenance_hash")
    assert hasattr(idealize, "input_trace_hash")
    assert hasattr(idealize, "stale_molecule_keys")
    assert "input_trace_hash" in g.node("trace_generation").existing_stamp


# --- Qt-free ------------------------------------------------------------------


def test_the_module_imports_without_qt_or_numpy() -> None:
    # tether.project/__init__ imports the store core, so load the module file alone
    path = Path(g.__file__)
    code = (
        "import importlib.util, sys; "
        f"spec = importlib.util.spec_from_file_location('dg', {str(path)!r}); "
        "m = importlib.util.module_from_spec(spec); sys.modules['dg'] = m; "
        "spec.loader.exec_module(m); "
        "bad = [n for n in sys.modules if n.split('.')[0] in "
        "('PySide6', 'PyQt5', 'qtpy', 'numpy', 'h5py')]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_a_new_generation_stales_the_factor_version_on_alpha_or_gamma_evidence() -> None:
    # alpha comes from post-acceptor-bleach tails of the traces (ADR-0027) and gamma from
    # bleach steps (ADR-0028); the contributor sets differ, so the guard names both.
    (edge,) = [
        e for e in g.EDGES if (e.upstream, e.downstream) == ("trace_generation", "factor_version")
    ]
    assert "α" in edge.guard and "γ" in edge.guard
    assert "factor_version" in g.descendants("trace_generation")
