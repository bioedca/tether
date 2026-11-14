# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The dependency graph of a project and the rules that make a result stale (ADR-0103).

This module is the **one Qt-free table** of the graph that ADR-0103 decides (PRD §7.13
FR-WORKFLOW-6). It holds data and two read-only walks, nothing else: it writes no store
data and it sets no stale mark. The job and dependency coordinator of #895 and the stage
readiness of #896 read it, so that two features cannot implement two different rules for
the same edge.

The words of the graph:

* A **node** is a *kind* of project record (a trace generation, a fit, a result snapshot).
  An *instance* is one such record, and its **identity** (:attr:`Node.identity_kind`) is a
  version counter, a content hash, or an external id.
* An **edge** says that a downstream record is made from an upstream one. Each edge names
  the **event** that changes the upstream identity in a way that makes the downstream record
  stale, and optionally a **guard**: the edge applies only when the guard holds.
* A downstream record is **stale** when an upstream identity that it recorded at creation
  differs from the current one. Staleness is transitive: the descendants of a stale record
  are stale too. A stale record stays readable and keeps its mark; it never supplies a
  current result; nothing is deleted (:data:`STALE_PERMITS`).

The scope of a mark is the instances that recorded the changed instance, not every instance
of the node kind: an α change stales the corrections of its condition only, and a membership
edit stales the fits of that one analysis set only.

``tests/test_dependency_graph.py`` asserts that :data:`NODES` and :data:`EDGES` agree, cell by
cell, with the two tables of ``docs/adr/0103-the-project-dependency-graph-and-the-stale-rules.md``.
A change to one is a change to the other, in the same pull request.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The identity of an instance is a content hash: SHA-256 over a canonical encoding.
IDENTITY_CONTENT_HASH = "content hash"
#: The identity of an instance is a version counter that is never reused.
IDENTITY_VERSION_COUNTER = "version counter"
#: The identity of an instance is an id that an external system assigns.
IDENTITY_EXTERNAL_ID = "external id"

IDENTITY_KINDS = frozenset({IDENTITY_CONTENT_HASH, IDENTITY_VERSION_COUNTER, IDENTITY_EXTERNAL_ID})

#: What every stale record permits, whatever its kind (ADR-0103, decision 3).
STALE_PERMITS = (
    "A stale record stays readable and keeps its stale mark.",
    "A stale record never supplies a current result.",
    "Nothing is deleted: a stale record is kept until the user removes it.",
)


@dataclass(frozen=True)
class Node:
    """One kind of project record in the dependency graph.

    Attributes
    ----------
    key
        The stable machine name, for example ``"trace_generation"``.
    name
        The name that the ADR and the user interface show.
    diagram_block
        The block of the workflow design (``docs/design/``) that the node comes from.
    identity_kind
        One of :data:`IDENTITY_KINDS`.
    identity
        How the identity of an instance is calculated.
    existing_stamp
        The provenance stamp that exists today and that the identity agrees with, or
        ``"none"`` when the node has no stored record yet.
    when_stale
        What a stale instance of this node still permits, beyond :data:`STALE_PERMITS`.
    """

    key: str
    name: str
    diagram_block: str
    identity_kind: str
    identity: str
    existing_stamp: str
    when_stale: str


@dataclass(frozen=True)
class Edge:
    """A downstream record is made from an upstream one.

    Attributes
    ----------
    number
        The row number of the edge in the ADR table (1-based, stable).
    upstream, downstream
        Node keys.
    event
        The event that changes the upstream identity and makes the downstream record stale.
    guard
        The condition under which the edge applies, or ``""`` when it always applies.
    diagram_block
        The block, note or transition of the workflow design that the edge comes from.
    """

    number: int
    upstream: str
    downstream: str
    event: str
    guard: str
    diagram_block: str

    @property
    def guarded(self) -> bool:
        """Whether the edge applies only when its :attr:`guard` holds."""
        return bool(self.guard)


_ROOT = "Root: no upstream event makes it stale."

NODES: tuple[Node, ...] = (
    Node(
        key="source_movie",
        name="Source movie",
        diagram_block="G02 “Immutable source registry”; G05 “Raw / prepared movies”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity="SHA-256 of the file bytes",
        existing_stamp="`/movies.sha256`; `molecule_key` is built on it",
        when_stale=(
            _ROOT + " A file whose bytes change is a different source: the registry shows"
            " the registered one as missing."
        ),
    ),
    Node(
        key="geometry_recipe",
        name="Geometry recipe",
        diagram_block="G02 “Pair channels; assign D/A”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity=(
            "SHA-256 of the canonical recipe: channel roles, the per-channel convert, crop,"
            " rotate, flip and translate steps, and the adapter with its version"
        ),
        existing_stamp="the crop, rotation and flip fields of `/movies`",
        when_stale=_ROOT + " An edit makes a new recipe, and the old recipe stays in the registry.",
    ),
    Node(
        key="map",
        name="Map",
        diagram_block="G02 “Separate reusable map file”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity=(
            "SHA-256 of the map file: source ids, geometry, transform, projection settings"
            " and quality record"
        ),
        existing_stamp="`/movies.calibration_id` and `/calibration/<calibration_id>`",
        when_stale=(
            "Readable with its quality record. It is not applied to a new extraction until"
            " the reuse check passes again."
        ),
    ),
    Node(
        key="trace_generation",
        name="Trace generation",
        diagram_block="G02 “New trace generation”",
        identity_kind=IDENTITY_VERSION_COUNTER,
        identity=(
            "A counter per project; the generation records the identities of the sources,"
            " the recipe and the map that made it"
        ),
        existing_stamp="`input_trace_hash` of each molecule (ADR-0024)",
        when_stale=(
            "The old generation stays readable. Its labels and analysis windows are carried"
            " to the new generation; no new fit or correction uses it as current."
        ),
    ),
    Node(
        key="curation_labels",
        name="Curation labels",
        diagram_block="G03 “APPARENT_REVIEW”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity="SHA-256 of the curation label and the category of each molecule",
        existing_stamp="`/molecules.curation_label` and the `/labels` log (ADR-0023)",
        when_stale=(
            "Readable and editable with the mark “carried” on the new generation, until"
            " the user reconciles them."
        ),
    ),
    Node(
        key="analysis_windows",
        name="Analysis windows",
        diagram_block="G03 note 03; G03 “CORRECTED_REVIEW”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity="The (pre, post) frame bounds of each molecule",
        existing_stamp=(
            "`/molecules.analysis_window`, folded into `input_provenance_hash` (ADR-0029)"
        ),
        when_stale=(
            "Readable and editable with the mark “carried” on the new generation, until"
            " the user reconciles them."
        ),
    ),
    Node(
        key="calibration_windows",
        name="Calibration windows",
        diagram_block="G03 note 03",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity="SHA-256 of the step window of each γ contributor",
        existing_stamp="none; `/molecules.bleach_frames` is the detected boundary, not a window",
        when_stale=_ROOT,
    ),
    Node(
        key="factor_version",
        name="Bleedthrough factor and gamma version",
        diagram_block="G03 “CALIBRATION_APPROVED”; G05 “Factor versions + contributor evidence”",
        identity_kind=IDENTITY_VERSION_COUNTER,
        identity=(
            "A counter per condition; the version also records the SHA-256 of the factors"
            " and of the contributor evidence frozen at approval"
        ),
        existing_stamp=(
            "the effective applied `/molecules.alpha`, `/molecules.gamma` and"
            " `/molecules.correction_method` (ADR-0029)"
        ),
        when_stale=(
            "Readable with its evidence (CALIBRATION_STALE). It is not applied as a new"
            " correction version."
        ),
    ),
    Node(
        key="correction_version",
        name="Correction version",
        diagram_block="G03 “apply correction version”",
        identity_kind=IDENTITY_VERSION_COUNTER,
        identity=(
            "A counter per project; the version records its trace generation and its factor version"
        ),
        existing_stamp="the effective α and γ folded into `input_provenance_hash` (ADR-0029)",
        when_stale=(
            "Corrected data stays readable with the mark. Corrected analysis on it is"
            " disabled; apparent E stays available."
        ),
    ),
    Node(
        key="review_snapshot",
        name="Review snapshot",
        diagram_block="G03 “ANALYSIS_READY”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity=(
            "SHA-256 of the correction version and of the label, analysis window and bleach"
            " frames of each molecule"
        ),
        existing_stamp="none",
        when_stale=(
            "Readable. The project leaves ANALYSIS_READY; no corrected analysis set is frozen on "
            "it."
        ),
    ),
    Node(
        key="analysis_set_version",
        name="Analysis-set version",
        diagram_block="G04 “Frozen analysis-set version”",
        identity_kind=IDENTITY_VERSION_COUNTER,
        identity=(
            "A counter per analysis set; the version records the SHA-256 of its frozen definition"
        ),
        existing_stamp="none",
        when_stale="Readable and frozen. A new version is frozen for new fits.",
    ),
    Node(
        key="fit",
        name="Fit",
        diagram_block="G04 “Run model jobs in background”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity=(
            "SHA-256 of its input, family, K and effective settings, plus the"
            " `input_provenance_hash` of each molecule"
        ),
        existing_stamp=(
            "the per-molecule `input_hashes` of `/idealization/{model}` (ADR-0024, ADR-0029)"
        ),
        when_stale=(
            "Viewable with the mark. It is left out of the TDP, dwell and plot results"
            " unless the user asks to include stale fits; it is never the current result"
            " of an active fit."
        ),
    ),
    Node(
        key="result_snapshot",
        name="Result snapshot",
        diagram_block="G04 “Versioned result snapshot”",
        identity_kind=IDENTITY_VERSION_COUNTER,
        identity=(
            "A counter per project; the snapshot records the SHA-256 of its lineage: the"
            " analysis-set version, the active fit and the plot recipe"
        ),
        existing_stamp="the export provenance sidecar: `app_version`, `created_utc`, `parameters`",
        when_stale=(
            "A historical result: readable with its original inputs, never shown as current."
        ),
    ),
    Node(
        key="package_manifest",
        name="Package manifest",
        diagram_block="G06 “Portable package manifest”",
        identity_kind=IDENTITY_CONTENT_HASH,
        identity=(
            "SHA-256 of the manifest: relative paths, the SHA-256 of each file, and the lineage"
        ),
        existing_stamp="none",
        when_stale="Readable. It is not uploaded until it is built again.",
    ),
    Node(
        key="deposit_record",
        name="Deposit record",
        diagram_block="G06 “Save DOI + record version”",
        identity_kind=IDENTITY_EXTERNAL_ID,
        identity=(
            "The Zenodo deposit id, record id, record version and DOI, bound to the manifest hash"
        ),
        existing_stamp="none",
        when_stale=(
            "A published record stays a valid record of its version. A draft is bound to the"
            " new manifest before upload; a new publication is a new record version."
        ),
    ),
)


EDGES: tuple[Edge, ...] = (
    Edge(
        1,
        "source_movie",
        "trace_generation",
        "Source change: the bytes no longer match the registered SHA-256",
        "",
        "G02 “Immutable source registry”; G05 “Read source pixels”",
    ),
    Edge(
        2,
        "geometry_recipe",
        "map",
        "Geometry edit",
        "only when the map no longer matches the channel geometry",
        "G02 “Saved map matches channel geometry?”; G02 note 02",
    ),
    Edge(
        3,
        "geometry_recipe",
        "trace_generation",
        "Geometry edit",
        "",
        "G05 note 03; G02 note 04",
    ),
    Edge(
        4,
        "map",
        "trace_generation",
        "Map edit",
        "",
        "G05 note 03; G02 note 04",
    ),
    Edge(
        5,
        "trace_generation",
        "curation_labels",
        "New trace generation: labels are carried by `molecule_key`",
        "",
        "G02 note 04",
    ),
    Edge(
        6,
        "trace_generation",
        "analysis_windows",
        "New trace generation: windows are carried by `molecule_key`",
        "",
        "G02 note 04",
    ),
    Edge(
        7,
        "trace_generation",
        "factor_version",
        "New trace generation: calibration evidence changes",
        "only when the intensities of an α or γ contributor change",
        "G03 “calibration evidence changes”",
    ),
    Edge(
        8,
        "trace_generation",
        "correction_version",
        "New trace generation",
        "",
        "G02 note 04",
    ),
    Edge(
        9,
        "trace_generation",
        "analysis_set_version",
        "New trace generation of a member movie",
        "",
        "G04 “Frozen analysis-set version”",
    ),
    Edge(
        10,
        "trace_generation",
        "fit",
        "New trace generation: the input-trace identity changes",
        "",
        "G02 note 04",
    ),
    Edge(
        11,
        "curation_labels",
        "factor_version",
        "Curation edit: factor / contributor evidence changes",
        "only when the label of a γ contributor changes",
        "G03 “factor / contributor evidence changes”",
    ),
    Edge(
        12,
        "curation_labels",
        "review_snapshot",
        "Curation edit: edit analysis windows / curation",
        "",
        "G03 “edit analysis windows / curation”",
    ),
    Edge(
        13,
        "curation_labels",
        "analysis_set_version",
        "Curation edit of a member molecule",
        "only when it changes the result of the molecule filter of the set",
        "G04 “Corrections + review current for selection?”",
    ),
    Edge(
        14,
        "calibration_windows",
        "factor_version",
        "Calibration-window edit: calibration evidence changes",
        "",
        "G05 note 03; G03 note 03",
    ),
    Edge(
        15,
        "analysis_windows",
        "factor_version",
        "Analysis-window edit",
        "only when the inputs of the γ estimator change",
        "G03 note 03",
    ),
    Edge(
        16,
        "analysis_windows",
        "review_snapshot",
        "Analysis-window edit: edit analysis windows / curation",
        "",
        "G03 “edit analysis windows / curation”",
    ),
    Edge(
        17,
        "analysis_windows",
        "analysis_set_version",
        "Analysis-window edit of a member molecule",
        "",
        "G05 note 03",
    ),
    Edge(
        18,
        "analysis_windows",
        "fit",
        "Analysis-window edit",
        "",
        "G05 note 03; G03 note 03",
    ),
    Edge(
        19,
        "factor_version",
        "correction_version",
        "Factor edit: factor / contributor evidence changes",
        "",
        "G05 note 03; G03 “factor / contributor evidence changes”",
    ),
    Edge(
        20,
        "correction_version",
        "review_snapshot",
        "New correction version: calibration evidence changes",
        "",
        "G03 “calibration evidence changes”; G03 “finish review [all dependencies current]”",
    ),
    Edge(
        21,
        "correction_version",
        "fit",
        "New correction version: the effective α or γ of a molecule changes",
        "",
        "G05 note 03",
    ),
    Edge(
        22,
        "review_snapshot",
        "analysis_set_version",
        "New review snapshot of a member project",
        "only for an analysis set in corrected E",
        "G04 “Corrections + review current for selection?”",
    ),
    Edge(
        23,
        "analysis_set_version",
        "fit",
        "Membership edit: Membership changed",
        "",
        "G04 “Membership changed”; G05 note 03; G03 note 04",
    ),
    Edge(
        24,
        "analysis_set_version",
        "result_snapshot",
        "New analysis-set version",
        "",
        "G04 “Lineage to exact data and active fit”; G03 note 04",
    ),
    Edge(
        25,
        "fit",
        "result_snapshot",
        "The active fit changes or goes stale",
        "",
        "G04 note 04",
    ),
    Edge(
        26,
        "source_movie",
        "package_manifest",
        "Source change or a missing source",
        "",
        "G06 “All sources available and verified?”; G06 note 01",
    ),
    Edge(
        27,
        "map",
        "package_manifest",
        "Map edit",
        "",
        "G06 note 01",
    ),
    Edge(
        28,
        "result_snapshot",
        "package_manifest",
        "New result snapshot",
        "",
        "G06 note 04",
    ),
    Edge(
        29,
        "package_manifest",
        "deposit_record",
        "Manifest change",
        "",
        "G06 “Bind deposit ID to manifest”; G06 note 04",
    ),
)


def _index_nodes(nodes: tuple[Node, ...]) -> Mapping[str, Node]:
    by_key: dict[str, Node] = {}
    for n in nodes:
        if n.key in by_key:
            raise ValueError(f"duplicate node key {n.key!r}")
        by_key[n.key] = n
    return MappingProxyType(by_key)


#: The nodes by key (read-only).
NODE_BY_KEY: Mapping[str, Node] = _index_nodes(NODES)


def node(key: str) -> Node:
    """Return the node with this key.

    Raises
    ------
    KeyError
        If no node has the key (a typo fails loudly; there is no default).
    """
    try:
        return NODE_BY_KEY[key]
    except KeyError:
        raise KeyError(f"no node {key!r} in the dependency graph") from None


def edges_from(key: str, *, follow_guarded: bool = True) -> tuple[Edge, ...]:
    """The edges whose upstream is ``key``, in table order.

    With ``follow_guarded=False`` the guarded edges are left out: the result is the edges that
    apply whatever the guard says.
    """
    node(key)
    return tuple(e for e in EDGES if e.upstream == key and (follow_guarded or not e.guarded))


def descendants(key: str, *, follow_guarded: bool = True) -> frozenset[str]:
    """Every node that a change of ``key`` can make stale (the node itself is not included).

    This is the transitive closure over :data:`EDGES`. With ``follow_guarded=False`` it follows
    only the edges that apply whatever their guard says, so the result is what a change of
    ``key`` *always* makes stale; with the default it is what it *can* make stale.
    """
    seen: set[str] = set()
    queue = deque([key])
    node(key)
    while queue:
        current = queue.popleft()
        for e in edges_from(current, follow_guarded=follow_guarded):
            if e.downstream not in seen:
                seen.add(e.downstream)
                queue.append(e.downstream)
    return frozenset(seen)


__all__ = [
    "EDGES",
    "IDENTITY_CONTENT_HASH",
    "IDENTITY_EXTERNAL_ID",
    "IDENTITY_KINDS",
    "IDENTITY_VERSION_COUNTER",
    "NODES",
    "NODE_BY_KEY",
    "STALE_PERMITS",
    "Edge",
    "Node",
    "descendants",
    "edges_from",
    "node",
]
