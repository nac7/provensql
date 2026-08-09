# Precision- and Error-Aware SQL Equivalence: Deciding Rewrites Under IEEE-754 Rounding and Runtime Errors

**Nachiket Lele**, Independent Researcher · ORCID [0009-0000-7932-0952](https://orcid.org/0009-0000-7932-0952)

**Preprint, August 2026.** DOI
[10.5281/zenodo.21862537](https://doi.org/10.5281/zenodo.21862537). All
numbers below are reproducible from the harness in `provensql/`, `mining/`, and
`scripts/`; see §8. This is the top-tier research track (Paper 2) that extends
the tool described in the companion preprint (DOI
[10.5281/zenodo.21853966](https://doi.org/10.5281/zenodo.21853966)).

---

## Abstract

Every published SQL equivalence checker — Cosette/HoTTSQL, EQUITAS, SPES,
WeTune, and the current frontier QED, VeriEQL, and Polygon — decides whether two
queries are equivalent by modeling numeric columns as *exact mathematical
integers or reals* under *total, error-free* arithmetic. That model is
convenient and, for its purpose, sound; but it is silent on two behaviors that
are observable in every production engine. First, **floating-point rounding**:
rewrites that hold over the reals — reassociation, distribution, cancellation —
can change the result of an `IEEE-754 DOUBLE` computation, and an exact-real
prover accepts them anyway. Second, **runtime errors**: division by zero,
overflow, and `CAST` failure are outcomes distinct from a value or `NULL`, and a
total-arithmetic model silently equates an edit that *errors* with one that
returns `NULL` (the archetypal `a/b` → `SAFE_DIVIDE(a,b)` refactor). We present
the first **precision- and error-aware** SQL equivalence checker. It decides
scalar rewrites under (i) an IEEE-754 floating-point model via the SMT theory of
floating point, and (ii) a three-outcome ERROR/NULL/value lattice. Its primary,
sound use is **disproof**: producing a concrete witness on which a
real-number-valid rewrite diverges under rounding or error semantics. We show
the prover *disproves* reassociation, distribution, and cancellation with
`Float32` counterexamples validated by re-execution, and separates
error-preserving from error-changing edits with witnesses. We then run the
checker against a production optimizer: it **re-derives, mechanically, the
`IntegralType` guard** Apache Spark's `ReorderAssociativeOperator` applies by
hand, and **flags the exact case** Apache Calcite's `RexSimplify` got wrong and
had to patch (CALCITE-7145). Finally, we automatically ingest Calcite's own
`checkSimplify` test assertions (391 pairs) and audit the 26 that fall in our
modeled fragment: the checker agrees with all 26 and contradicts none —
external validation on independently-authored pairs — while measuring that the
floating-point axis is essentially absent from that suite, which explains *why*
the axis has remained unchecked. We are explicit about the limitation that gives
the contribution its shape: **proving** precision-equivalence is intractable
even for commutativity, so the value is in disproof and bug-finding, exactly
where the axis is open.

---

## 1. Introduction

A query optimizer, a data engineer refactoring a pipeline, and a
migration-conformance tool all ask the same question: *are these two SQL
expressions equivalent?* The last decade has produced increasingly powerful
automated answers. Cosette and HoTTSQL [1,2] introduced semantic proofs of
equivalence; EQUITAS [3] and SPES [4] scaled SMT-based checking to set and bag
semantics; WeTune [5] used equivalence checking to *discover* rewrite rules
automatically; and the current frontier — QED [6], VeriEQL [7], and Polygon [8]
— handles integrity constraints, three-valued `NULL` logic, and bounded
counterexample search across tens of thousands of benchmarks.

All of them share a modeling choice: **numbers are exact and arithmetic is
total.** Integers are mathematical integers; `DECIMAL`/`DOUBLE` are reals;
`a + b`, `a * b`, and `a / b` always denote a real value. This is a reasonable
abstraction — and for the relational reasoning these tools target (projection,
join, aggregation, subquery), it is the right one. But two behaviors of real
engines fall outside it:

1. **Floating-point rounding is invisible.** Under `IEEE-754`, addition and
   multiplication are commutative but *not associative or distributive*. A
   rewrite an exact-real prover certifies as equivalent — `(a+b)+c = a+(b+c)`,
   `a*(b+c) = a*b+a*c`, `(a+b)-b = a` — can produce a different `DOUBLE` result.
   Optimizers know this: Spark's `ReorderAssociativeOperator` reassociates *only
   for integral types*, a guard its authors reasoned out by hand.

2. **Runtime errors are invisible.** Division by zero, arithmetic overflow, and
   `CAST` failure are real, observable outcomes. Modeling arithmetic as total
   equates an edit that raises with one that returns `NULL` or a value. This is
   not hypothetical: Apache Calcite's `RexSimplify` folded `IS NULL(10/0)` to a
   definite `false` — treating a division-by-zero as an ordinary non-null value
   — a bug tracked and patched as CALCITE-7145.

This paper closes both gaps. Our contributions:

- **A precision-aware decision procedure** (§3) that compiles scalar SQL
  arithmetic to the SMT theory of floating point and decides equivalence under
  `IEEE-754` round-to-nearest-even, producing a finite counterexample when a
  real-valid rewrite diverges under rounding.
- **An error-aware decision procedure** (§4) over a three-outcome
  ERROR/NULL/value lattice, so an edit that changes a division-by-zero error
  into a `NULL` (or a `NULL` into a value) is `DIFFERENT`, with a witness.
- **A curated soundness audit** (§5) of twelve common algebraic rewrites,
  finding 7/12 FP-unsound and 4/5 error-unsound, each with a witness — including
  two rules unsound under *both* lenses for different reasons.
- **Validation against a production optimizer** (§6): the checker re-derives
  Spark's `IntegralType` guard mechanically and flags Calcite's CALCITE-7145 and
  the subtle CALCITE-7295 divisor boundary, with no contradiction against ground
  truth.
- **An automatic ingestion pilot** (§7): harvesting Calcite's own
  `checkSimplify` assertions, we audit 26 in-fragment pairs with zero
  contradictions, and *measure* that the FP axis is nearly absent from the
  suite — the empirical reason the axis is open.

We are equally explicit about scope (§9): **proving** precision-equivalence
(UNSAT over floating point) is intractable even for commutativity, so the
contribution is *disproof* — which is precisely the form that finds bugs.

## 2. Background and the gap

**Floating point.** `IEEE-754` [9] arithmetic rounds every operation to the
nearest representable value. Addition and multiplication are commutative but not
associative: there exist `a,b,c` for which `(a+b)+c ≠ a+(b+c)` because the
intermediate rounding of `a+b` discards low-order bits that a different grouping
would retain [10]. Distribution and cancellation fail for the same reason. None
of this is modeled by a prover that treats a `DOUBLE` column as a real.

**Runtime errors.** SQL engines differ in error semantics — PostgreSQL and
Oracle raise on `x/0`; MySQL and SQLite yield `NULL` — but in *no* engine is
`x/0` an ordinary value. A rewrite that removes or introduces a possible error
(guarded division, `SAFE_DIVIDE`, `NULLIF`, overflow-prone widening) changes
observable behavior. An exact-real, total-arithmetic model cannot represent the
distinction.

**The gap, confirmed.** We surveyed the limitations of the frontier tools. QED
[6] models bag semantics with integrity constraints and `NULL`s over
*uninterpreted/integer* values; VeriEQL [7] does bounded verification with rich
constraints and counterexamples over an integer/UF theory; the linear-integer
approach [11] is by construction integer-exact. None models `IEEE-754` rounding
or a first-class error outcome. The companion tool [12] already sits on this
boundary: it abstains on division and documents "numerics modeled as exact
reals" as a soundness caveat. That caveat is the seed of this contribution.

## 3. Precision-aware equivalence

We compile a scalar arithmetic expression over columns `x₁…xₙ` to a term in the
SMT theory of floating point (`QF_FP`): each column becomes a floating-point
variable of a chosen sort, literals become `FPVal`, and `+ − × ÷` become the
rounded operations `fpAdd`, `fpSub`, `fpMul`, `fpDiv` under round-to-nearest-even
(`RNE`). Two expressions `A`, `B` are **precision-equivalent** iff
`A = B` for all finite inputs, encoded by asserting `¬(A ≈ B)` and checking
satisfiability, where `≈` treats `+0.0 = −0.0` and `NaN = NaN` to match SQL
value comparison.

- **SAT** → the model is a concrete assignment on which `A` and `B` differ:
  a **DIVERGENT** witness (a genuine counterexample).
- **UNSAT** → `EQUIVALENT_FP` under the modeled semantics.
- **timeout / unknown** → `UNKNOWN` (honest abstention).

We default to `Float32`. SMT-FP is bit-blasted, and 64-bit multiplication is
frequently intractable, whereas 32-bit keeps the reassociation and distribution
*disproofs* fast; the rounding phenomena are identical in both widths, and
`Float64` is available behind the timeout. Crucially, **the sound and tractable
direction is disproof (SAT)**: finding a divergence is fast, while *proving*
equivalence (UNSAT) is hard — see §9. Every `DIVERGENT` witness is validated
out-of-band by re-executing both expressions at the witness with `numpy.float32`
and confirming the results differ, mirroring how the companion tool validates
its counterexamples.

## 4. Error-aware equivalence

We model a scalar expression's outcome as a three-element lattice
`Outcome(err, null, val)`: booleans for ERROR and NULL, and a real value that is
meaningful only when neither flag holds. Propagation is standard — ERROR
dominates, then NULL, else the value — with division introducing an error
exactly when the divisor is a non-null, non-error zero. `NULLIF` and
`SAFE_DIVIDE` are modeled directly (the latter returns `NULL`, never raises, on
a zero divisor), as are `NULL`/boolean literals, `IS [NOT] NULL`, and (as an
outcome-transparent passthrough) `CAST`. Two expressions are
**error-equivalent** iff on every input they agree on the *outcome*: same ERROR
flag, and where not error the same NULL flag, and where neither the same value.
Values are modeled over exact reals here — this procedure is about *outcomes*,
not rounding (that is §3).

Because the value theory is linear real arithmetic, **both directions are
tractable**: `EQUIVALENT_ERR` is a usable positive result, not only a
refutation. For example:

- `a / b` vs `SAFE_DIVIDE(a, b)` → **DIFFERENT**, witness `b = 0` (ERROR vs NULL).
- `a / NULLIF(b, 0)` vs `SAFE_DIVIDE(a, b)` → **EQUIVALENT_ERR** (both NULL at
  `b = 0`, proven).
- `a / b` vs `b / a` → **DIFFERENT** (division is not commutative across the
  error boundary: at `a = 0, b = 1` the first is `0`, the second raises).

An engine's raise-vs-`NULL` choice on `x/0` is a calibration parameter of the
model; we adopt the raising semantics (matching PostgreSQL/Oracle and the
companion tool's `SAFE_DIVIDE` contrast) and note where a conclusion depends on
it.

## 5. A soundness audit of common rewrites

We ran both procedures over twelve textbook algebraic rewrites — the scalar
simplifications optimizers and hand-refactors apply freely, each valid over the
reals. Results (reproducible via `scripts/fp_rule_audit.py`):

- **FP axis: 7/12 unsound**, each with a `Float32` witness — add/mul
  reassociation, distribution, factoring, `(a+b)−b`, chained division,
  mul/div reorder. The four "fp-safe" rules are exactly those with no rounding
  step that can lose information (identities, exact doubling, negation); one is
  an honest solver timeout.
- **Error axis: 4/5 unsound** — `a/b` vs `SAFE_DIVIDE`, chained division,
  `(a+b)−b` (NULL vs value at `b = NULL`), `(a*b)/b` (ERROR vs value at
  `b = 0`); only the `NULLIF`-guarded form is error-safe.

The sharpest observation: `(a+b)−b = a` and `(a*b)/b = a` are unsound under
*both* lenses — for different reasons and with different witnesses. An
exact-real, total-arithmetic prover certifies all of these as safe.

## 6. Validation against a production optimizer

A checker on this axis is only credible if it agrees with what mature optimizers
already do by hand and flags what they got wrong. We built a cited corpus
(`mining/optimizer_rules.py`) of rules real optimizers apply, each with its
real-world guard/status, and reconciled the engine's verdict against ground
truth (`scripts/optimizer_rule_audit.py`). The soundness contract is that the
engine must **never contradict** ground truth; `UNKNOWN` is honest abstention.

- **Spark `ReorderAssociativeOperator`** reassociates `Add`/`Multiply` only when
  `a.dataType.isInstanceOf[IntegralType]` — floating point is excluded by hand.
  Our FP engine **independently produces the divergence witness** that justifies
  the guard: it re-derives, mechanically, a hand-written soundness condition.
- **Calcite CALCITE-7145** (tracked, patched): `RexSimplify` folded
  `IS NULL(10/0)` to `false`. Our error engine reports ERROR ≠ `false`
  immediately — it would have flagged the defect before it shipped.
- **Calcite CALCITE-7295**: null-propagation through division is sound with a
  constant nonzero divisor (`NULL + a/4` → `NULL`, proven) but unsound with a
  variable divisor (`NULL + a/b` → `NULL` drops a division-by-zero at `b = 0`,
  refuted with a witness) — the engine tracks the subtle boundary, not just the
  blunt one.

Across the corpus there are **no contradictions with ground truth**. We claim
this as *validation*, not novel-bug discovery: the obvious unsound rewrites are
already guarded or already tracked. The result is that a single checker
re-derives the guards and flags the tracked defects automatically, on axes no
equivalence prover models.

## 7. Automatic ingestion: validation and yield measurement

To test whether the approach scales beyond a curated corpus, we built an
ingestion pipeline (`mining/calcite_ingest/`) that harvests a real optimizer's
*declarative* simplification pairs rather than parsing its imperative rule code.
Apache Calcite encodes its intended simplifications as test assertions
`checkSimplify(input, "expected")`; each is an `(input, expected)` pair — the
project's own claim that the two are equivalent. We extract these
(balanced-paren, multiline-aware), translate both the test DSL and the RexNode
dump notation into SQL (type and nullability ride along in Calcite's column
names), keep the pairs inside our modeled fragment, and audit each. The run is
checkpointed and resumable.

From `RexProgramTest.java` (Calcite `master`, August 2026):

| Stage | Count |
|---|---|
| `checkSimplify*` pairs extracted | 391 |
| candidate set (arithmetic ∨ NULL-ish) | 117 |
| translated into the modeled fragment & audited | 26 |
| audited → `EQUIVALENT_ERR` (contradictions: **0**) | **26 / 0** |

Two findings. First, **the checker agrees with every Calcite simplification it
can model and contradicts none** — external validation on 26 independently
authored pairs, including null-propagation through arithmetic, division by a
nonzero constant, and the *post-fix* CALCITE-7145 shape (Calcite now keeps
`IS NULL(CAST(x/0))` symbolic; the engine agrees it must not be folded). Second,
**the floating-point axis is nearly absent from the suite**: of 41 arithmetic
pairs, the column types are 12 integer, 1 decimal, and 0 float/double. Calcite
does not test FP-unsound rewrites *because it does not perform them* — which is
the empirical explanation for why this axis has gone unchecked, and why finding
a novel FP defect requires a source that applies rewrites to float columns (a
machine-discovered rule set such as WeTune's, or direct application to typed
columns) rather than a battle-tested integer-typed test suite. The binding
constraint on coverage is the modeled fragment (91 of 117 candidates use
`AND`/`OR`/`CASE`/`COALESCE`/comparisons or unresolved local references), which
prioritizes future engine extensions.

## 8. Reproducibility

All results are reproducible from the repository. §3 procedure: `provensql/
precision.py`, tests in `tests/test_precision.py`. §4 procedure: `provensql/
error_semantics.py`, tests in `tests/test_error_semantics.py`. §5 audit:
`scripts/fp_rule_audit.py` → `docs/fp_rule_audit.md`. §6 audit:
`scripts/optimizer_rule_audit.py` + `mining/optimizer_rules.py` →
`docs/optimizer_rule_audit.md`, tests in `tests/test_optimizer_rule_audit.py`.
§7 pilot: `mining/calcite_ingest/{extract,translate,run_audit}.py` →
`docs/calcite_ingest_pilot.md`, tests in `tests/test_calcite_ingest.py`. FP
witnesses are validated by re-execution with `numpy.float32`; the full test
suite passes (88 tests). Raw third-party optimizer source is not redistributed;
the harness takes its path.

## 9. Limitations and scope

- **Proving is intractable; disproof is the contribution.** Deciding
  precision-*equivalence* (UNSAT over `QF_FP`) is expensive enough that even
  commutativity may not prove within a practical budget at `Float64`, and the
  distribution disproof's runtime is nondeterministic under load. We therefore
  center the contribution on *disproof* — producing divergence witnesses — which
  is sound, fast, and is exactly the operation that finds bugs. `EQUIVALENT_FP`
  is a prototype-level positive result under the modeled semantics, not a
  general certificate.
- **Fragment.** The procedures model scalar arithmetic, `NULL`/error outcomes,
  `IS [NOT] NULL`, `NULLIF`, `SAFE_DIVIDE`, and outcome-transparent `CAST`. They
  do not yet model `CASE`/`COALESCE`, comparisons as three-valued booleans,
  `DECIMAL(p,s)` scaled-integer rounding, or overflow/`CAST`-failure as
  first-class errors. Each is a named, independently-testable extension; §7
  quantifies the coverage they would unlock.
- **Model calibration.** The raise-vs-`NULL` semantics of `x/0` is
  engine-dependent; error-axis conclusions state the assumed semantics.
- **Standalone by design.** These procedures are research prototypes that do not
  touch the companion tool's shipped, sound pipeline; its guarantee is unchanged.

## 10. Related work

Cosette/HoTTSQL [1,2], EQUITAS [3], and SPES [4] established SMT- and
proof-based equivalence over set/bag semantics; WeTune [5] applied it to rule
discovery. The current frontier — QED [6] (bag normal forms, integrity
constraints, `NULL`s), VeriEQL [7] (bounded proving *and* disproving with rich
constraints and counterexamples), and Polygon [8] (conflict-driven
under-approximation for disproof and input generation) — advances both proving
and disproving substantially, and this work does not compete there. Our axis is
orthogonal: all of these model numbers as exact and arithmetic as total, and
none reasons about `IEEE-754` rounding or a first-class error outcome. The
floating-point verification literature [10] and SMT-FP [13] supply the
machinery we build on; our contribution is the SQL semantics on top, the
error/NULL lattice, and the empirical audit against a production optimizer.

## References

*(Verified against publisher records, August 2026.)*

[1] S. Chu, K. Weitz, A. Cheung, D. Suciu. "HoTTSQL: Proving Query Rewrites with
Univalent SQL Semantics." PLDI 2017, pp. 510–524.
[2] S. Chu, C. Wang, K. Weitz, A. Cheung. "Cosette: An Automated Prover for
SQL." CIDR 2017.
[3] Q. Zhou, J. Arulraj, S. B. Navathe, W. Harris, D. Xu. "Automated
Verification of Query Equivalence Using Satisfiability Modulo Theories." PVLDB
12(11):1276–1288, 2019. DOI 10.14778/3342263.3342267. (EQUITAS)
[4] Q. Zhou, J. Arulraj, S. B. Navathe, W. Harris, J. Wu. "SPES: A Two-Stage
Query Equivalence Verifier." ICDE 2022.
[5] Z. Wang, Z. Zhou, Y. Yang, H. Ding, G. Hu, D. Ding, C. Tang, H. Chen,
J. Li. "WeTune: Automatic Discovery and Verification of Query Rewrite Rules."
SIGMOD 2022.
[6] S. Wang, S. Pan, A. Cheung. "QED: A Powerful Query Equivalence Decider for
SQL." PVLDB 17(11):3602–3614, 2024. DOI 10.14778/3681954.3682024.
[7] Y. He, P. Zhao, X. Wang, Y. Wang. "VeriEQL: Bounded Equivalence
Verification for Complex SQL Queries with Integrity Constraints." Proc. ACM
Program. Lang. 8(OOPSLA1), Article 132, 2024, 29 pp. DOI 10.1145/3649849.
(Distinguished Paper)
[8] P. Zhao, Y. Wang, X. Wang. "Polygon: Symbolic Reasoning for SQL using
Conflict-Driven Under-Approximation Search." PLDI 2025. DOI 10.1145/3729303.
[9] IEEE Standard for Floating-Point Arithmetic, IEEE 754-2019.
[10] D. Goldberg. "What Every Computer Scientist Should Know About
Floating-Point Arithmetic." ACM Computing Surveys 23(1):5–48, 1991.
[11] H. Ding, Z. Wang, Y. Yang, D. Zhang, Z. Xu, H. Chen, R. Piskac, J. Li.
"Proving Query Equivalence Using Linear Integer Arithmetic." Proc. ACM Manag.
Data 1(4), Article 227, 2023. DOI 10.1145/3626768. (SQLSolver)
[12] N. Lele. "provensql: Sound, Catalog-Aware Detection of Behavior-Changing
SQL Edits." Preprint, 2026. DOI 10.5281/zenodo.21853966.
[13] L. de Moura, N. Bjørner. "Z3: An Efficient SMT Solver." TACAS 2008, LNCS
4963, pp. 337–340.
[14] Apache Calcite issue tracker, CALCITE-7145 and CALCITE-7295.
[15] Apache Spark, Catalyst `ReorderAssociativeOperator`
(`sql/catalyst/.../optimizer/expressions.scala`).
