# M3 headline audit: real optimizer rules under FP and ERROR/NULL semantics

M3's curated audit (`docs/fp_rule_audit.md`) showed the M1/M2 engines flag
textbook algebraic rewrites that are valid over the reals but unsound under
floating point or across a runtime error. This audit takes the same engines to
**rules that real, production SQL optimizers actually apply**, and checks the
engine's verdict against how the optimizer itself handles each rule.

Corpus: `mining/optimizer_rules.py` (cited, with source URLs).
Reproduce: `python scripts/optimizer_rule_audit.py`.

## What "reconcile" means

Every rule in the corpus is valid over the mathematical reals with total,
error-free arithmetic — the model every SQL-equivalence prover
(Cosette/HoTTSQL, EQUITAS, SPES, QED, VeriEQL, LIA) assumes. Against that
baseline we record, per rule, the *ground truth* from the optimizer:

- **guard-confirmed** — the optimizer restricts the rule (e.g. Spark applies
  reassociation only to integral types). Our engine should independently find
  the **unguarded** form unsound. Re-deriving a hand-written guard is the
  evidence the checker is trustworthy.
- **known-defect** — a case the optimizer's own issue tracker records as wrong
  (Apache Calcite JIRA). Our engine should **flag** it — validation against
  ground truth, not a novel-bug claim.

We make no claim to have discovered new bugs in mature optimizers: the obvious
unsound rewrites are already guarded (Spark) or already tracked (Calcite). The
result is that **a single checker re-derives both the guards and the tracked
defects automatically**, on an axis the proving frontier does not model.

## Results

**No rule ever contradicts its recorded status** (7 guard-confirmed,
2 known-defect), across every run. The engine's contract is *soundness, not
completeness*: it must never certify an unsound rule as equivalent nor flag a
sound one as divergent. An `UNKNOWN` is an honest abstention, never a false
certification.

Typically **8–9 of the 9 resolve** with a concrete witness. The lone variable
is the distribution disproof `a*(b+c) = a*b+a*c`: bit-blasted FP solving makes
its runtime genuinely nondeterministic — often under a second, occasionally past
any fixed budget — so it flags UNSOUND on most runs and abstains to UNKNOWN on
some. The two Spark reassociation disproofs and all error-axis rules resolve
deterministically. This is the intended behavior: the tool would rather abstain
than guess, and it never once mislabels a rule.

### Floating-point axis (M1 `precision.py`)

| Rule | Optimizer & guard | Engine verdict |
|---|---|---|
| `(a + b) + c` = `a + (b + c)` | Spark `ReorderAssociativeOperator` — *IntegralType only* | **UNSOUND** (Float32 witness) |
| `(a * b) * c` = `a * (b * c)` | Spark `ReorderAssociativeOperator` — *IntegralType only* | **UNSOUND** (Float32 witness) |
| `a * (b + c)` = `a*b + a*c` | common simplification — unsound on FLOAT | **UNSOUND** (witness; abstains on some runs) |
| `(a + b) - b` = `a` | common simplification — unsound on FLOAT | **UNSOUND** (witness) |

Spark's `ReorderAssociativeOperator` gates its body on
`a.dataType.isInstanceOf[IntegralType]` — the authors excluded floating point
by hand because reassociation is not value-preserving there. Our FP engine
reaches the same conclusion mechanically, producing the finite counterexample
that justifies the guard.

### ERROR / NULL axis (M2 `error_semantics.py`)

| Rule | Optimizer & status | Engine verdict |
|---|---|---|
| `(10/0) IS NULL` = `FALSE` | Calcite **CALCITE-7145** (defect) | **UNSOUND** — ERROR vs `false` |
| `(10/0) IS NOT NULL` = `TRUE` | Calcite **CALCITE-7145** (defect) | **UNSOUND** — ERROR vs `true` |
| `NULL + a/4` = `NULL` | Calcite **CALCITE-7295** — constant nonzero divisor | sound |
| `NULL + a/b` = `NULL` | Calcite **CALCITE-7295** — needs non-raising divisor | **UNSOUND** — ERROR vs NULL at `b=0` |
| `(a + b) - b` = `a` | common simplification | **UNSOUND** — NULL vs value at `b=NULL` |

**CALCITE-7145** is the sharpest result. RexSimplify folds `IS NULL(10/0)` to a
definite `false` (and `IS NOT NULL(10/0)` to `true`), treating a
division-by-zero as an ordinary non-null value — but a `/0` either raises
(Oracle, PostgreSQL) or yields NULL (MySQL, SQLite), so a definite `false` is
wrong under *every* engine. The M2 lattice, modeling the raising semantics,
reports ERROR ≠ `false` immediately. A checker wired into RexSimplify would
have caught this before it shipped.

**CALCITE-7295** shows the engine tracking a *subtle* boundary rather than a
blunt one: null-propagation through `a/4` is genuinely safe (a constant nonzero
divisor can never raise), so the engine proves `NULL + a/4 = NULL`; the same
fold with a variable divisor `a/b` drops a division-by-zero at `b=0`, and the
engine refutes it with the witness `{a:0, b:0}`. This is exactly the
distinction that makes "improve simplification of division" hard to get right.

## Why this is the contribution

The equivalence-proving frontier (QED, VeriEQL, …) would certify **all nine**
of these rewrites as equivalent, because each is valid over exact, total reals.
Real optimizers know better — but only through guards their authors reasoned
out by hand, and, where the reasoning slips, through bugs filed after the fact.
This audit shows one checker that:

1. re-derives the guards mature optimizers already apply (Spark integral-only
   reassociation), and
2. flags the cases they got wrong and had to patch (Calcite CALCITE-7145),

on the FP-rounding and ERROR/NULL axes no existing SQL-equivalence prover
models. That is the precise, defensible claim for the top-tier write-up (M4).

## Scope and honesty

This is a curated, cited corpus, not an automatic scrape of optimizer source,
and it validates against known ground truth rather than claiming new
discoveries. Scaling to an automatic ingest of a full rule base (Calcite
`RexSimplify`, Spark's optimizer, WeTune's discovered rules) — where a
real-valid-but-FP/error-unsound rule with no adequate guard would be a genuine,
reportable defect — is the natural next step, and the machinery
(engines + reconciliation harness) is now in place for it.
