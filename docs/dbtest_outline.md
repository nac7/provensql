# DBTest submission outline — provensql

**Target venue:** DBTest (workshop on Testing Database Systems, co-located with SIGMOD).
**Format:** short paper, ~6 pages. Check the current CFP for the exact page limit and deadline before writing.

**Why this venue:** DBTest is about *practical techniques for testing database systems and their components*. provensql is exactly that — a tool that tests the soundness of SQL rewrites, with a mutation-based methodology and a precision/error-aware axis that production optimizers do not test for. This reframes the Paper 2 material (which is written as a research contribution) for a testing-methods audience.

**Positioning vs. the two preprints:** this is NOT a third research result — it is a *tool/methods* retelling. Paper 1 = the tool + LLM-judge finding; Paper 2 = the precision/error-aware research contribution; DBTest paper = "here is a reusable testing methodology and an open tool, and here is what it found when pointed at real optimizers." Cite both preprints; contribute the *testing* framing and the artifact.

---

## Proposed title
"Testing SQL Rewrite Soundness Under Rounding and Runtime Errors: A Mutation- and Differential-Testing Toolkit"

## Abstract (½ column)
Database systems and their users constantly rewrite SQL — optimizers apply rules, engineers refactor, and increasingly LLMs generate edits. Testing whether a rewrite preserves behavior is hard: existing equivalence checkers model numbers as exact reals under total arithmetic, so they cannot test the two axes that break in production — IEEE-754 rounding and runtime errors (division by zero, overflow, CAST failure). We present provensql, an open tool that tests rewrite soundness three ways: (i) a sound decision layer that never reports a false EQUIVALENT (0 across 511 equivalence-breaking mutations); (ii) mutation-based recall testing; and (iii) precision/error-aware auditing that disproves real-number-valid rewrites with concrete, re-executed counterexamples. Pointed at production optimizers, it re-derives by machine the integral-type guard Spark applies by hand and flags the exact case Calcite had to patch (CALCITE-7145). We describe the methodology, the harness, and results, and release everything for reuse.

## 1. Motivation (1 col)
- The rewrite-testing problem, three sources of rewrites (optimizer rules, human refactors, LLM-generated SQL).
- The gap: equivalence checkers assume exact-real, total arithmetic → silent on rounding and errors, the two things that actually diverge in production. (condense Paper 2 §2)
- Contribution as a *testing* contribution: a reusable methodology + open artifact, plus a validation study against real optimizers.

## 2. The soundness contract as a test oracle (1 col)
- Four verdicts (EQUIVALENT / DIFFERENT / SCHEMA_CHANGE / UNKNOWN); UNKNOWN = honest abstention.
- The key test property: **never a false EQUIVALENT**. Frame this as an oracle guarantee for downstream test harnesses.
- Runtime EQUIVALENT backstop (Stage-3 proof re-checked by Stage-4 counterexample search) — a testing-in-depth design. (from Paper 1 / Tier 3)

## 3. Mutation-based recall testing (1 col)
- Mutation operators that break equivalence; 0/511 false EQUIVALENT; recall by class.
- Clopper-Pearson upper bound on the false-EQUIVALENT rate (0/511 → ≤0.58%). This is the quantitative testing result DBTest readers want.

## 4. Precision- and error-aware auditing (1.5 col — the novel testing axis)
- FP axis: SMT theory of floating point, Float32 default, disproof with numpy-re-executed witnesses. (Paper 2 §3)
- Error axis: ERROR/NULL/value lattice; a/b vs SAFE_DIVIDE separation. (Paper 2 §4)
- The curated audit as a *test suite over common rewrites*: 7/12 FP-unsound, 4/5 error-unsound. (Paper 2 §5)

## 5. Validation against production optimizers (1 col)
- Re-derives Spark ReorderAssociativeOperator's IntegralType guard; flags CALCITE-7145 / 7295. (Paper 2 §6)
- Automatic ingestion of Calcite's own checkSimplify assertions: 391→117→26 audited, 0 contradictions; FP-yield ≈ 0 (measurement finding: production suites barely test the FP axis). (Paper 2 §7)
- Frame the 0-contradiction result as *external validation of the oracle on independently-authored tests*.

## 6. The artifact (½ col)
- CLI `provensql diff base head [--catalog] [--json]`; CI exit codes (0/1/2); JSON audit certificate.
- Reproducibility: harness in provensql/, mining/, scripts/; pip install; Apache-2.0. Apply for the DBTest/ACM artifact badge.

## 7. Limitations (½ col)
- Proving precision-equivalence is intractable even for commutativity → the value is disproof/bug-finding. Scalar fragment scope. (Paper 2 §9, honest.)

## 8. Related work (½ col)
- Cosette, EQUITAS, SPES, WeTune, QED, VeriEQL, Polygon — all exact-real/total-arithmetic. (condense Paper 2 §10)

## Reuse checklist (what to cut from Paper 2 to fit ~6 pages)
- Drop the full reference survey; keep the one-paragraph frontier contrast.
- Compress §3/§4 formalism to the procedure + one witness each; move detail to an appendix or cite Paper 2.
- Lead with §3–§5 here (the testing methodology + results); the DBTest audience wants technique and numbers over motivation.
- Keep every concrete number verifiable from the released harness (DBTest values reproducibility).
