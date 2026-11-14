# Workflow design diagrams

These files are the workflow design of the application that the maintainer made on 2026-11-13 and
supplied on 2026-11-14. They show a **future state**: the design is not a statement that the screens,
the store fields or the adapters exist today. The product requirements that come from them are
FR-WORKFLOW (§7.13) and FR-DEPOSIT (§7.14) of the
[product requirements document](https://github.com/bioedca/tether/blob/main/docs/PRD.md), and the
changes of §2.2 (N5), §7.1, §7.2, §7.4, §7.7, §7.9, §7.11, §9.6 and Appendix A that issue
[#891](https://github.com/bioedca/tether/issues/891) made. The tracking issue of the plan is
[#890](https://github.com/bioedca/tether/issues/890).

**Baseline.** The diagrams were drawn against `main` at commit `64ae5bc`
(`64ae5bc62c9197047d26b8d60ae696ee78e8a8f4`). The capability tags in a diagram describe that commit.

**What is here.** One Graphviz source (`.dot`) and the drawing made from it (`.svg`) for each diagram.
The files are as the maintainer made them. Git stores the `.dot` files with LF line endings and a final
newline; that is the only change. This directory does not redraw a diagram.

| Diagram | Subject | Source | Drawing |
|---|---|---|---|
| 01 | End-to-end flow in four sections: Prepare, Curate and correct, Analyze, Deposit | [`01-workflow.dot`](01-workflow.dot) | [`01-workflow.svg`](01-workflow.svg) |
| G02 | Import, mapping and trace extraction | [`02-preparation.dot`](02-preparation.dot) | [`02-preparation.svg`](02-preparation.svg) |
| G03 | Correction and review state machine | [`03-correction-state-machine.dot`](03-correction-state-machine.dot) | [`03-correction-state-machine.svg`](03-correction-state-machine.svg) |
| G04 | Analysis and model capability flow | [`04-analysis.dot`](04-analysis.dot) | [`04-analysis.svg`](04-analysis.svg) |
| G05 | Components and data ownership | [`05-components.dot`](05-components.dot) | [`05-components.svg`](05-components.svg) |
| G06 | Package, draft upload and publication | [`06-deposition.dot`](06-deposition.dot) | [`06-deposition.svg`](06-deposition.svg) |

To draw a diagram again from its source, use Graphviz 14.1.3, the version that made these drawings,
for example `dot -Tsvg 01-workflow.dot -o 01-workflow.svg`. Each source names its layout engine in
its `graph` attributes (`layout=neato` with fixed node positions for five diagrams, `layout=dot` for
G03), and Graphviz uses that engine.

## How to read the diagrams

These rules are page 7 of the maintainer's PDF, which has no DOT source.

- The activity diagrams show decisions, alternatives, validation gates and retry paths. Lanes group
  modules; they are not separate users or execution threads. A crossing without a node is not a
  junction.
- G03 is a state machine. Its labels use `event [guard] / action`. G05 is a logical component and
  dependency view: arrows describe calls and dependencies, not deployment locations or message
  protocols.
- Folded-corner documents are input artifacts. Dark ovals are entry or hand-off points. Cylinders are
  persisted logical assets, not a requirement to use a database. Rectangles are software
  responsibilities.
- Solid arrows are a flow or a call. Blue dashed arrows are a data dependency. Amber dashed arrows are
  a revisit or a retry.
- The capability tags are: **A** a current desktop capability; **C** a core, command-line or Python
  capability; **P** described on the published roadmap; **N** proposed in this design. Combined tags
  separate a reusable capability from the requested new behaviour. They are not completion
  percentages.
- Scope: two-color, single-laser smFRET. "FRET-state lifetime" means the dwell time of a FRET state.
  Fluorescence lifetimes, FLIM, TCSPC, ALEX, PIE and three-color acquisitions are outside this scope.

## Source register

The register of page 7, as the PDF lists it. Sources pinned or reviewed on 2026-11-13.

| Key | Source |
|---|---|
| T1 | Tether README: implemented and planned capabilities |
| T2 | Tether product requirements: project model, corrections, analysis and plot inventory |
| T3 | Tether compatibility: inputs, geometry, current limits and source references |
| T4 | Tether ADR-0015: explicit difference from Deep-LASI colocalization |
| T5 | Tether gamma implementation decision |
| T6 | Tether kinetic inference and model-dependent output context |
| D1 | Deep-LASI documentation and GUI workflow |
| D2 | Deep-LASI dynamic two-color worked example |
| M1 | MASH-FRET video-processing workflow |
| G1 | Requested Graphviz documentation source |
| G2 | Graphviz DOT grammar, subgraphs and HTML-like labels |
| Z1 | Zenodo deposition API: draft uploads and explicit publication |
| Z2 | Zenodo file preparation, size and count limits, and file visibility |
| Z3 | Zenodo DOI reservation and registration |
| S1 | User-supplied `tmaven_state_scan.py`, inspected without execution; written 2026-10-16; targets tMAVEN 0.3.0 |
| U | User requirements and question-card decisions of 2026-11-13 |

The maintainer said on 2026-11-14 not to mind source U: the diagrams and the decisions recorded on
[#890](https://github.com/bioedca/tether/issues/890) are the record. The script of source S1 is not in
this directory; the pull request of [#937](https://github.com/bioedca/tether/issues/937) asks whether
it can be committed.
