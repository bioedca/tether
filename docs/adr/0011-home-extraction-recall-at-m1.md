# 0011 — Home the M0.5 ≥95% extraction-recall acceptance at M1

- **Status:** accepted
- **Date:** 2026-06-27
- **Deciders:** bioedca
- **PRD anchor:** §9 M0.5(b), §9 M1, §11.2 (detection/extraction rows), §8 NFR-VALID (a)
- **Milestone:** M0.5 (close decision); the binding recall+intensity gate lands at M1

## Context and problem statement

PRD §9 **M0.5(b)** lists three acceptance clauses: (1) "≥ 95% of Deep-LASI
molecules matched within 1 px"; (2) the `TIRFdata` decode recovers coordinates +
α/β/γ; (3) native registration RMS residual ≤ 0.5 px vs the `.tmap`. Clauses (2)
and (3) are met and merged — the MCOS decode (#25), the β→α/α→δ/γ→γ remap (#25,
ADR-0008), and native polynomial registration at **RMS = 0.434 px** vs the `.tmap`
(#27). Clause (1), the matched-molecule recall, is **not** met by the M0.5
detection work: the M0.5 S5 "thin preview" detector (`detect_spots`, #22) reaches
only ~20% recall @ 1 px on the full donor field, and even the underlying detection
image caps near ~80% local-max recall (#28). Closing the gap to ≥ 95% requires the
full M1 detector (à trous threshold/scale tuning, centroid merge, colocalization
pairing across the registered channels) — multi-session M1 work, not a preview.

The same recall bar appears **verbatim in PRD §9 M1**: "matched-molecule recall
≥ 95% within 1 px; per-frame integrated-intensity Pearson r ≥ 0.99 on matched
molecules; registration RMS ≤ 0.5 px." So the question is not whether the gate
holds, but **which milestone owns it as a binding gate** — and therefore whether
M0.5 may close with clause (1) demonstrated-as-a-preview rather than passed at the
95% bar.

## Decision drivers

- The recall+intensity extraction acceptance is a single coherent oracle (PRD §8
  NFR-VALID (a): recall / intensity-Pearson / RMS), and the PLAN already homes that
  oracle at **M1**: §1.1 names it "M1 extraction tolerance (recall ≥ 95% @ 1px,
  Pearson r ≥ 0.99, RMS ≤ 0.5px)"; §2.3 oracle (a) is milestone **M1**; §4 S5
  scoped M0.5 detection explicitly as "a thin M1 preview … loose threshold here;
  tightened at M1."
- M0.5's de-risking purpose is to retire the two highest risks **before** building
  on them: that the tMAVEN sidecar can be driven headlessly with a *frozen* parity
  tolerance (done — #20, #29, #21), and that the decode/registration/detection
  **path** works on real data (done — decode #25, registration RMS 0.434 px #27,
  detection+aperture demonstrated #22/#23/#28). Both risks are retired.
- Do not silently relax an acceptance gate — the homing must be explicit, recorded,
  and reversible; the recall bar must still bind somewhere (it binds at M1, which
  cannot sign off without it).
- The frozen PRD text must not be edited to make a milestone close. The recall
  clause stays in M1 (where it binds); this ADR records the *reading* of M0.5(b)
  clause (1) as the preview leg of that same M1 oracle.

## Considered options

- **A. Home the binding recall gate at M1; close M0.5 on its non-recall
  de-risking deliverables.** The ≥ 95% recall bar is **not** treated as satisfied
  at M0.5 — it remains the binding M1 extraction-tolerance gate (PRD §9 M1, PLAN
  §1.1/§2.3/§4 S5), which M1 cannot close without. The M0.5(b) clause-(1) checklist
  item is **annotated as homed at M1 (this ADR) when the milestone is closed** — so
  the close and the checklist stay aligned. No PRD edit (recall stays in M1).
- **B. Keep M0.5 open until the M1 detector reaches ≥ 95% recall.** Circular against
  the milestone DAG (M0.5 → M1): M0.5 would only close partway through M1, while M1
  work proceeds anyway. The recall gate is captured by M1's own acceptance regardless.
- **C. Weaken the M0.5(b) recall bar in place** (e.g. to 80%). Rejected — fabricates
  a non-PRD threshold and weakens a gate to match what's missing (forbidden by the
  working agreement); the real bar belongs at M1 unchanged.

## Decision outcome

Chosen option: **A**. The binding ≥ 95% matched-molecule recall (with intensity
Pearson r ≥ 0.99) is the **M1** extraction-tolerance gate and is **not** treated as
satisfied at M0.5. **M0.5 closes** on its **non-recall** de-risking deliverables —
headless vbFRET sidecar driver + the frozen §11.2 parity tolerance (the hard gate) +
the validated `.tdat` decode / factor remap / native registration — and the close is
enacted together with **annotating the GitHub M0.5(b) clause-(1) checklist item as
homed at M1 (this ADR)**, so the milestone state and its checklist stay aligned.
**No SemVer tag** (M0.5 is a fractional de-risking gate, not a release — PRD §12.7's
SemVer track runs M0–M9 and omits M0.5). The frozen PRD §9 text is unchanged; the
recall criterion continues to bind at M1.

### Consequences

- Good: M0.5 carries no remaining blocker; autonomous development proceeds into M1.
  Both M0.5 hard gates (schema freeze ADR-0005, parity-tol freeze ADR-0009) remain
  closed and bind every later milestone.
- The recall gate is **not dropped** — it binds at M1 and **M1 cannot sign off
  without** recall ≥ 95% @ 1px, intensity Pearson r ≥ 0.99 on matched molecules, and
  RMS ≤ 0.5 px on the UCKOPSB pair (PRD §9 M1; oracle wired into `large-fixtures.yml`
  per PLAN §5 S9). The M0.5 registration-RMS evidence (0.434 px) and the decode carry
  forward as M1 substrate.
- Trade-off: M0.5 closes **without** the ≥ 95% recall having been met — that gate is
  deferred-in-place to M1, not passed at M0.5. Reversible — if the maintainer judges
  the bar should bind at M0.5, reopening the milestone and #17 is a one-step revert;
  the recall gate is unchanged either way.
- Follow-up: #17 is closed (its decode/remap/registration code is merged; this ADR
  resolves its remaining close-decision). M0.5 milestone closed; its acceptance
  checklist annotated (clause (1) → M1 (this ADR); GUI 2-OS leg → M9 (ADR-0010)).

## More information

PRD §9 M0.5(b), §9 M1, §11.2, §8 NFR-VALID (a); PLAN §1.1, §2.3 oracle (a), §4 S5/S6,
§5 S9; issues #17, #22, #23, #25, #27, #28; ADR-0005, ADR-0006, ADR-0008, ADR-0009,
ADR-0010.
