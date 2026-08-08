# Research scope: precision- and error-aware SQL equivalence

This is the scoping memo for the **top-tier (Paper 2)** direction identified in
`paper_positioning.md`. It states the open problem, shows it is feasible and
genuinely uncovered by the frontier, and lays out an approach, evaluation, and
milestones. It is a plan, not a result.

## The gap (confirmed by the 2026-08 prior-art pass)

Every SQL-equivalence prover — Cosette/HoTTSQL, EQUITAS, SPES, QED (PVLDB'24),
VeriEQL (OOPSLA'24), and the linear-integer-arithmetic approach (PACMMOD'23) —
models numbers as **exact mathematical integers/reals** and assumes **total,
error-free arithmetic**. Two consequences, both unaddressed in the literature:

1. **Floating-point / decimal rounding is invisible to them.** They will certify
   as "equivalent" rewrites that only hold over the reals but *diverge* under
   IEEE-754 `DOUBLE` or fixed-scale `NUMERIC` — reassociation, distribution,
   sum reordering, `x*1.0`, constant folding of decimals.
2. **Runtime errors are invisible.** Division-by-zero, arithmetic overflow, and
   `CAST` failure are real, observable outcomes in every engine; treating
   arithmetic as total silently equates an edit that *errors* with one that
   returns a value or `NULL` (e.g. `a/b` vs a guarded `SAFE_DIVIDE(a,b)`).

provensql already sits exactly on this boundary — it *abstains* on division and
documents "numerics modeled as exact reals" as a soundness caveat. That caveat
is the seed of a contribution.

## Feasibility (a concrete check, not a hope)

Z3's floating-point theory (FPA) expresses IEEE-754 semantics directly. A
10-line probe (reproducible) already delivers the core result:

- `(a+b)+c == a+(b+c)` over IEEE-754 `DOUBLE` is **disproven** — Z3 returns a
  finite counterexample `a,b,c`. Associativity, a rewrite every real-number
  prover accepts, is *not* equivalence-preserving under floating point.
- `0.1 + 0.2 == 0.3` in `DOUBLE` is **disproven**.

So the machinery to decide precision-equivalence exists; the research is in the
encoding, the SQL semantics on top, scalability, and the empirical payoff.

## Contribution framing

**The first precision- and error-aware SQL equivalence checker.** Its primary,
splashy use is *disproving*: finding rewrites that are real-number-equivalent
but **FP- or error-divergent** — including unsound rules in production query
optimizers. (VeriEQL's headline was finding bugs in MySQL/Calcite via
counterexamples; "which optimizer rewrite rules are unsound under floating
point?" is the same shape of result, on an axis no one has checked.) The
secondary contribution is *proving* precision-equivalence where it does hold
(monotone rewrites, no reassociation), and **error-equivalence** (edits that
preserve the value/NULL/ERROR outcome).

## Technical approach

1. **Typed numeric model.** Replace the exact-real encoding in `smt.py` with a
   type-directed one: `FLOAT`/`DOUBLE` → Z3 FPA with SQL's rounding (RNE);
   `NUMERIC`/`DECIMAL(p,s)` → scaled integers with defined rounding and
   overflow bounds; integers stay exact. Column types come from the catalog
   (or are conservatively unknown → abstain).
2. **Three-outcome value lattice.** Extend the current `(is_null, value)` pair
   to `(is_error, is_null, value)` with Kleene-plus-error propagation, so
   division-by-zero, overflow, and `CAST` failure are first-class. Equivalence
   requires matching the *outcome*, so turning an error into a NULL is a
   detectable `DIFFERENT`.
3. **Two decision procedures.** (a) *precision-equivalence*: equal under the
   typed rounding model (often UNSAT-to-prove → produce the divergence witness
   instead); (b) *error-equivalence*: identical error behavior on all inputs.
4. **Aggregation caveat as a feature.** `SUM`/`AVG` over a *bag* have no
   engine-defined summation order, so their FP result is genuinely
   order-dependent — a principled place to return "nondeterministic under FP,"
   which is itself a finding worth formalizing.

## Evaluation plan

- **A precision-divergence benchmark (built by construction).** Rewrites that
  are real-equivalent but FP-divergent — reassociation, distribution, sum
  reordering, `x+0.0`, `x*1.0`, decimal constant folding, `DECIMAL`↔`FLOAT`
  casts. Show the tool flags them and that exact-real provers (QED/VeriEQL)
  wrongly accept them.
- **Optimizer-rule audit (the headline experiment).** Run the checker over the
  rewrite rules of Calcite/Spark (and WeTune's discovered rules) under the FP
  model; any rule that is unsound under floating point is a concrete, citable
  finding.
- **Error-semantics pairs.** `a/b` vs `SAFE_DIVIDE`, guarded vs unguarded
  `CAST`, overflow-prone widening — verify the tool separates error-preserving
  from error-changing edits.

## Milestones

- **M1 — DONE (prototype).** Scalar FP equivalence/divergence in
  `provensql/precision.py` (standalone; does not touch the shipped pipeline).
  Disproves reassociation and distributivity under IEEE-754 with witnesses
  validated by `numpy.float32`; proves simple identities (`x*1.0`); abstains to
  `UNKNOWN` on solver timeout. Confirmed at the tool level that proving
  FP-equivalence (UNSAT) is hard even for commutativity while disproving (SAT)
  is fast — so the contribution centers on disproof/bug-finding. Tests in
  `tests/test_precision.py`, incl. the headline contrast: the exact-real Stage 3
  proves `(a+b)+c == a+(b+c)` EQUIVALENT while the prototype flags it DIVERGENT.
  Default sort is Float32 for tractability (Float64 behind the timeout).
- **M2 — DONE (prototype).** The value/NULL/ERROR lattice in
  `provensql/error_semantics.py` (standalone; values over exact reals since the
  milestone is about *outcomes*, not rounding). Div-by-zero, NULL propagation,
  `SAFE_DIVIDE`, and `NULLIF` are modeled; `a/b` vs `SAFE_DIVIDE(a,b)` is
  DIFFERENT (ERROR vs NULL at a zero divisor, with witness), while
  `a/NULLIF(b,0)` vs `SAFE_DIVIDE(a,b)` proves EQUIVALENT_ERR. Proving is
  tractable here (real arithmetic), unlike the float case. Tests in
  `tests/test_error_semantics.py`.
- **M3 — DONE (curated prototype).** `scripts/fp_rule_audit.py` runs the M1/M2
  engines over a curated set of common algebraic rewrites. Result
  (docs/fp_rule_audit.md): **7/12 FP-unsound** and **4/5 error-unsound**, each
  with a witness — including `(a+b)-b = a` and `(a*b)/b = a`, which are unsound
  under *both* rounding and error/NULL semantics. Establishes the capability.
- **M3-headline — DONE (real optimizer rules).** `scripts/optimizer_rule_audit.py`
  runs the engines over a *cited* corpus of rules real optimizers apply
  (`mining/optimizer_rules.py`) and reconciles each verdict with the optimizer's
  own handling. Result (docs/optimizer_rule_audit.md): **no rule ever
  contradicts ground truth** (soundness contract; UNKNOWN is honest abstention),
  and 8–9 of 9 resolve with a witness. The FP engine re-derives Spark
  `ReorderAssociativeOperator`'s IntegralType guard (reassociation UNSOUND on
  floats, with witness), and the ERROR/NULL engine flags Calcite's tracked
  defect **CALCITE-7145** (`IS NULL(10/0) → false`) plus the subtle CALCITE-7295
  safe/unsafe-divisor boundary. Framed honestly as validation against ground
  truth (guards + JIRAs), not novel-bug discovery.
  Required extending `error_semantics.py` with NULL/boolean literals and the
  `IS [NOT] NULL` predicate. The remaining step is an *automatic* ingest of a
  full rule base, where a real-valid-but-FP/error-unsound rule with no adequate
  guard would be a genuine, reportable defect.
- **M4** — formal write-up: the typed encoding, per-rule soundness statements,
  and the empirical audit.

## Risks and open questions

- **SMT cost.** FPA solving is far heavier than linear arithmetic; scalability
  to realistic expressions is the main technical risk. Mitigations: bounded
  bit-width, staged solving (real-first, FP only on candidates), caching.
- **Scope of numeric types.** Engine-specific `DECIMAL` rules and coercion
  differ; the model must pick a target semantics (start with one engine's).
- **Cross-dialect angle.** Rounding/coercion differences *between* engines make
  this overlap [[project_migration_conformance]] — a possible second paper.
- **Novelty delta vs. the frontier** must stay sharp: the claim is precisely
  "sound under FP/decimal/error semantics," which QED/VeriEQL are not.

## Venue fit

The FP/error-semantics angle is a natural PL/verification contribution
(PLDI/OOPSLA) and also lands at DB venues (SIGMOD/VLDB/ICDE) via the
optimizer-rule audit. Either way it requires the M1–M3 work above; this repo's
provensql is the prototype the encoding extends.

## Before committing months

Read the VeriEQL, QED, and LIA *limitations* sections verbatim to confirm none
quietly handle FP/errors (the encoding evidence is strong but should be
confirmed at the source), and skim the FP-verification literature (e.g. tools
reasoning about IEEE-754 in programs) for reusable encodings.
