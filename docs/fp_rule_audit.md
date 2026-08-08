# M3: auditing common rewrite rules for FP- and error-unsoundness

Every rule below is valid over the mathematical **reals** — the domain every
existing SQL-equivalence prover assumes. Using the M1/M2 prototypes we ask two
questions those provers never do: is the rule still valid under **IEEE-754
rounding**, and does it preserve the **ERROR/NULL/value outcome**? A rule that
is real-valid but fails either test is a latent bug for any engine or optimizer
that applies it to floating-point columns or across a possible runtime error.

Reproduce: `python scripts/fp_rule_audit.py`.

## FP audit — valid over reals → valid under IEEE-754?

| Rewrite | Under IEEE-754 |
|---|---|
| `(a + b) + c` = `a + (b + c)` (add reassociation) | **FP-UNSOUND** |
| `(a * b) * c` = `a * (b * c)` (mul reassociation) | **FP-UNSOUND** |
| `a * (b + c)` = `a*b + a*c` (distribute) | **FP-UNSOUND** |
| `a*b + a*c` = `a * (b + c)` (factor) | **FP-UNSOUND** |
| `(a + b) - b` = `a` (cancel) | **FP-UNSOUND** |
| `a / b / c` = `a / (b * c)` (chain div) | **FP-UNSOUND** |
| `a * c / b` = `a / b * c` (reorder mul/div) | **FP-UNSOUND** |
| `(a * b) / b` = `a` (cancel mul/div) | unknown (solver timeout) |
| `-(-a)` = `a` (double negation) | fp-safe |
| `a + 0.0` = `a` (add identity) | fp-safe |
| `a * 1.0` = `a` (mul identity) | fp-safe |
| `a + a` = `a * 2.0` (doubling) | fp-safe |

**7 of 12 are FP-unsound**, each with a concrete Float32 witness. Example
(`(a + b) - b` ≠ `a`): with `a ≈ 2^-62`, `b ≈ 2^-68`, the small `a` is lost in
the rounding of `a + b` and does not come back on subtraction. The four
"fp-safe" rules are exactly the ones with no rounding step that can lose
information (identities, exact doubling, negation). The single "unknown" is an
honest solver timeout — finding a divergence for division-heavy expressions is
expensive to bit-blast, so the prototype abstains rather than guess.

## Error audit — preserves the ERROR / NULL / value outcome?

| Rewrite | Outcome |
|---|---|
| `a / b` = `SAFE_DIVIDE(a, b)` | **ERROR-UNSOUND** — `b=0`: ERROR vs NULL |
| `a / b / c` = `a / (b * c)` | **ERROR-UNSOUND** — `b=0, c=NULL`: ERROR vs NULL |
| `(a + b) - b` = `a` | **ERROR-UNSOUND** — `b=NULL`: NULL vs value |
| `(a * b) / b` = `a` | **ERROR-UNSOUND** — `b=0`: ERROR vs value |
| `a / NULLIF(b, 0)` = `SAFE_DIVIDE(a, b)` | error-safe |

**4 of 5 are error-unsound.** These are not FP subtleties — they are outcome
changes: a rewrite that turns a division-by-zero *error* into a NULL (or a NULL
into a concrete value) changes what the query does, and only a model with a
first-class ERROR/NULL lattice can see it. Only the `NULLIF`-guarded form
matches `SAFE_DIVIDE`, and the prototype proves it.

## Why this matters

The same two rules — `(a + b) - b = a` and `(a * b) / b = a` — are unsound under
*both* lenses (rounding **and** error/NULL), for different reasons and with
different witnesses. Optimizers and hand-refactors apply exactly these
simplifications; an equivalence checker that models numbers as exact, total
reals certifies them as safe. Making rounding and error semantics observable
turns "obviously fine" rewrites into checkable — and here, refutable — claims.

## Scope and next step

This audits a curated rule set to establish the capability. The identical
procedure scales to a full optimizer rule base — Calcite `RexSimplify`, Spark's
optimizer, WeTune's discovered rules — where any real-valid-but-FP/error-unsound
rule applied without a `FLOAT`/nullability guard is a concrete, reportable
defect. That ingestion is the headline experiment for the top-tier write-up
(see docs/precision_research_scope.md, M3).
