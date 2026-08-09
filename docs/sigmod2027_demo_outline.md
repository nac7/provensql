# SIGMOD 2027 Demonstration — provensql (outline)

**Target:** SIGMOD 2027 Demonstration track. **Deadline: Fri Jan 15, 2027** (single round, 11:59pm AoE). **Length: max 4 US-letter pages.** Single PDF. Every author needs a **CMT** account and an **ORCID** (Nachiket: 0009-0000-7932-0952) or the submission is desk-rejected.

**What a demo paper is (vs. the DBTest outline):** the demo track rewards a *working system and a compelling live experience*, not a research delta. So the center of gravity moves to §4 (the demonstration scenario — what attendees see, touch, and are surprised by). Methodology and formalism compress to a paragraph each and cite the two preprints. Reuse numbers from Paper 1 / Paper 2; do not re-derive.

**One-line pitch:** an interactive station where attendees try SQL rewrites and watch a sound checker either *prove* them safe, *disprove* them with a concrete counterexample row, or *honestly abstain* — including the precision/error-aware cases every other checker silently gets wrong.

---

## Title (candidate)
"provensql: Sound, Precision-Aware SQL Equivalence Checking, Live"

## Abstract (≈120 words)
Engineers, optimizers, and now LLMs constantly rewrite SQL, and deciding whether a rewrite preserves behavior is deceptively hard — especially under floating-point rounding and runtime errors, which every published equivalence checker ignores by modeling numbers as exact reals. We demonstrate provensql, an open, sound-by-construction checker that returns one of four honest verdicts (EQUIVALENT / DIFFERENT / SCHEMA_CHANGE / UNKNOWN), never a false EQUIVALENT, and produces a replayable counterexample when a rewrite diverges. Attendees drive the system live: they pick or edit rewrites and watch it prove, disprove-with-witness, or abstain; toggle the precision/error-aware engine to see reassociation break under IEEE-754; watch it re-derive Spark's integral-type guard and flag the exact Calcite bug (CALCITE-7145); and race it against an LLM judge that confidently gets equivalence wrong.

## 1. Introduction (≈¾ page)
- The rewrite-safety problem; three sources (optimizers, human refactors, LLM-generated SQL).
- The blind spot: exact-real / total-arithmetic models miss rounding and runtime errors — the two axes that actually diverge in production (one-paragraph condense of Paper 2 §2).
- What the demo contributes: the first *interactive, sound* experience of equivalence checking, with witnesses and honest abstention, on a laptop. Point to the tool (pip, Apache-2.0) and CI Action.

## 2. System overview (≈¾ page, with an architecture figure)
- Pipeline: Stage 0 parse/normalize → Stage 3 SMT proof (conjunctive fragment, 3-valued NULL) → Stage 4 DuckDB counterexample search → precision/error-aware engines (Z3 QF_FP; ERROR/NULL/value lattice).
- The soundness contract: UNKNOWN over a false EQUIVALENT; the runtime EQUIVALENT backstop (Stage-3 proof re-checked by Stage-4 search).
- Outputs: text verdict, JSON audit certificate (with replayable witness), CI exit codes.
- **Figure 1:** architecture / verdict-flow diagram.

## 3. Key techniques (≈½ page — brief, cite preprints)
- Precision axis: SMT theory of floating point, Float32 default, disproof with re-executed witnesses (Paper 2 §3).
- Error axis: ERROR/NULL/value lattice; a/b vs SAFE_DIVIDE separation (Paper 2 §4).
- Validation: re-derives Spark's IntegralType guard; flags CALCITE-7145/7295; 26/26 agreement on ingested Calcite assertions, 0 contradictions (Paper 2 §6–§7).

## 4. Demonstration scenario (≈1.25 pages — THE CORE)
A single laptop station with a guided UI + terminal. Four short acts, each ~2 minutes, attendee-driven:

- **Act 1 — Prove & disprove, live.** Attendee edits a rewrite (e.g. join reordering, predicate pushdown, DISTINCT elimination). provensql returns EQUIVALENT (with the proof reason) or DIFFERENT with a **concrete counterexample row** they can copy and run. Includes a SCHEMA_CHANGE and an honest UNKNOWN so abstention is visible.
- **Act 2 — Turn on precision.** The same reassociation `a+b+c → (a+b)+c` is EQUIVALENT over integers but the attendee flips column types to `DOUBLE`; provensql now disproves it and shows a Float32 witness, re-executed on the spot to confirm the values actually differ.
- **Act 3 — Catch a real optimizer bug.** Load the CALCITE-7145 pattern; provensql flags the error-semantics divergence Calcite had to patch, and re-derives Spark's hand-written integral-type guard — "your tool rediscovered a guard a human wrote."
- **Act 4 — Beat the LLM.** Attendee submits a tricky pair to both an LLM judge and provensql; the LLM confidently answers EQUIVALENT on a pair that isn't (the gpt-5 finding from Paper 1), while provensql refuses or disproves. Drives home *why soundness matters*.
- **Bring-your-own-SQL:** a free-play mode where attendees paste their own rewrites.
- **Figure 2:** screenshot of the verdict + witness UI.

## 5. Related work (≈¼ page)
Cosette, EQUITAS, SPES, WeTune, QED, VeriEQL, Polygon — all exact-real/total-arithmetic; none offer a sound, precision/error-aware, witness-producing interactive experience (condense Paper 2 §10).

## Captured figures (in `docs/figures/`)
- `demo-divergent-witness.png` — the precision engine returning DIVERGENT on `(a+b)+c` vs `a+(b+c)` with a Float32 witness (paper Fig. showing the IEEE-754 disproof + witness UI).
- `demo-llm-race.png` — provensql DIFFERENT vs an LLM judge's wrong EQUIVALENT on `COUNT(dept)` vs `COUNT(*)` (paper Fig. for §4 Act 4; also the Show HN reply image).

## Build notes / checklist
- Register CMT + confirm ORCID on all authors early (desk-reject risk).
- 4 pages HARD; put formalism in the preprints, keep §4 the largest section.
- Need a lightweight **UI** for the demo (even a TUI or a small web front-end over the CLI/JSON certificate) — scope this as the main new build for the demo; the engine is done.
- Prepare a canned offline dataset so the demo works without network (esp. the LLM act — pre-record or cache responses).
- Screenshots for Figures 1–2; a 1-page teaser/poster if the track asks for one.
- Reproducibility: same harness as the preprints; mention pip install + the GitHub Action.
