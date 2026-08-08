# Scope: automatic ingestion of a full optimizer rule base

This scopes the true headline experiment flagged in
`precision_research_scope.md` (M3) and `optimizer_rule_audit.md`: move from a
*curated, cited* rule corpus (which we validate against known ground truth) to
an *automatic* ingest of a production optimizer's rules, so that a rewrite which
is valid over the reals but **FP- or error-unsound and not adequately guarded**
becomes a genuine, previously-unreported defect.

It is a plan, not a result. It states the core difficulty, the design pivot
that makes it tractable, the sources, the pipeline, the engine gaps, the triage
protocol that turns a raw refutation into a defensible defect report, and a
staged plan with a go/no-go gate after a small pilot.

## Success criterion

One of these, in decreasing order of ambition:

1. **A confirmed novel defect**: a rewrite the optimizer applies to
   FLOAT/DECIMAL or nullable-across-error inputs without a guard, that our
   engine refutes with a witness, that reproduces on the live engine, and that
   is not already tracked. (Report it upstream; cite it.)
2. **A guard-coverage measurement**: an automatic audit of N real rules
   reporting how many are FP/error-sensitive and, of those, how many are
   guarded — a "how sound is this optimizer under the axes provers ignore?"
   measurement study. Publishable even with zero novel bugs.
3. **A negative result done rigorously**: "the mature optimizers guard or track
   every FP/error-sensitive rule we could extract" — still a contribution,
   because no one has measured it on this axis.

All three require the same pipeline; only the triage depth differs. We should
commit to (2) as the floor and treat (1) as upside — that keeps the effort
honest and de-risked.

## The core difficulty, and the pivot around it

Optimizer rules are **imperative code**, not declarative `lhs → rhs` pairs.
Calcite's `RexSimplify` and Spark's Catalyst rules pattern-match a node tree and
*construct* a replacement in Java/Scala; there is no rule literal to parse.
Building a faithful extractor for that imperative logic is a compiler project in
its own right and is the reason "just parse the rule base" is deceptively large.

**Pivot: harvest the declarative pairs the projects already maintain.** Every
mature optimizer encodes its intended simplifications as *test assertions* and
golden files, which are exactly `(input, expected-output)` expression pairs:

- **Calcite** — `RexProgramTest` / `RexSimplifyTest` call
  `checkSimplify(expr, "expected")` (and `checkSimplify2`, `checkSimplify3`)
  hundreds of times; each is a real intended simplification as an
  input→output pair. Confirmed to exist and be the primary simplification
  test surface. This is the highest-yield, lowest-friction source.
- **Calcite** — `.iq` (Quidem) files and `RelOptRulesTest` before/after plans
  for relational rewrites (lower arithmetic yield, structural).
- **Spark** — Catalyst optimizer suites (`*OptimizeSuite`,
  `SimplifyBinaryComparisonSuite`, arithmetic-simplification suites) compare
  `Optimize.execute(plan)` to a `correctAnswer` plan; the expression subtrees
  are the pairs.
- **WeTune** — publishes discovered rules as data files
  (`wtune_data/prepared/rules.txt`), line-delimited. These are **relational
  plan substitutions**, so arithmetic yield is low, but the subset with
  predicates/expressions over numeric columns is worth filtering for — and,
  being machine-discovered rather than battle-tested, is the likeliest place a
  guard is *missing*.

Harvesting `(input, expected)` pairs from tests/golden files sidesteps the
imperative-parsing problem entirely: the project itself asserts each pair is an
equivalence, so a pair our engine refutes under FP/error semantics is, by
construction, a claim the optimizer's own test suite makes and that is unsound
on an axis the test does not check.

## Pipeline

```
[source files]
   |  (A) extract raw (input, expected) pairs
   v
[raw pairs in the source's own expr notation: Calcite RexNode dump / Spark
 Catalyst / WeTune plan algebra]
   |  (B) translate notation -> common AST (sqlglot)
   v
[normalized SQL scalar expressions]
   |  (C) filter to the modeled fragment + require arithmetic/nullable content
   v
[auditable pairs]
   |  (D) run M1 (fp_equivalent) and M2 (error_equivalent)
   v
[refutations with witnesses]
   |  (E) triage -> confirmed defect | already-guarded | already-tracked | out-of-scope
   v
[report]
```

Stage (A) is source-specific and mostly regex/AST-over-Java-test-source; the
harness from `scripts/optimizer_rule_audit.py` already implements (D) and the
reconciliation/soundness contract. (B), (C), (E) are the new work.

## Translation layer (Stage B) — the fidelity risk

Calcite `checkSimplify` inputs are built with test DSL helpers
(`mul(a,b)`, `case(...)`, `vInt()`, `vDecimal()`) and expected outputs are
RexNode `toString()` dumps (`+(?0.a, ?0.b)`, `CAST(...):DOUBLE`). We need a
parser from that notation into sqlglot expressions, preserving **operator,
operands, and — critically — declared type** (the `vInt`/`vDecimal`/`vDouble`
helper tells us the column type, which decides whether the FP axis even
applies). Fidelity here is the make-or-break: a wrong type annotation turns a
sound integer rule into a false FP lead, or hides a real float rule. Build (B)
with a round-trip test (parse → re-emit → compare) on a labeled sample before
trusting any downstream refutation.

## Engine gaps to close (fragment coverage)

The current engines model `+ - * /`, `NULLIF`, `SAFE_DIVIDE`, `IS [NOT] NULL`,
NULL/boolean literals, over Float32 (M1) / exact reals (M2). Real simplification
pairs will exercise more. Prioritized by expected frequency in the pairs:

1. **Typed columns from context** — carry the `vInt/vDecimal/vDouble` type onto
   each column so the FP axis runs only on FLOAT/DOUBLE and the DECIMAL axis on
   NUMERIC. Without this, every refutation needs manual type-checking. *(High.)*
2. **CAST + overflow** — `CAST(x AS INT)`, widening/narrowing, and overflow as a
   first-class ERROR outcome (M2). Many simplifications move or drop casts.
   *(High.)*
3. **CASE / COALESCE** — extremely common in simplification tests; needed just
   to not discard most pairs at Stage C. *(High.)*
4. **DECIMAL(p,s) scaled-integer model** — the second numeric axis named in the
   research scope; distinct rounding/overflow from FLOAT. *(Medium.)*
5. **Comparisons returning three-valued boolean** — to audit predicate
   simplifications (`x > y` folding), not just value expressions. *(Medium.)*

Each is a milestone-sized, independently testable extension to `precision.py` /
`error_semantics.py`, and each stays standalone (never touches shipped
`compare()`).

## Triage protocol (Stage E) — where the claim's soundness lives

A raw refutation is a *lead*, not a defect. The tool is sound about "these two
expressions differ under FP/error semantics"; it is **not** by itself evidence
the optimizer has a bug. Promoting a lead to a reportable defect requires, in
order (stop at the first that disqualifies it):

1. **Type applicability** — does the rule actually apply to FLOAT/DECIMAL or
   nullable-across-error inputs in the real optimizer, or only to the integer
   type the test used? (Read the rule's guard.) This is exactly the wall we hit
   manually with Spark's IntegralType guard — most leads die here, correctly.
2. **Guard check** — is there a type/nullability precondition in the rule that
   our extracted pair dropped? If guarded, it's a "guard-confirmed" validation,
   not a bug.
3. **Dedup against the issue tracker** — search JIRA/GitHub for the expression
   shape (this is how we found CALCITE-7145/7295 already exist). Known → cite as
   validation, not discovery.
4. **Live repro** — construct a minimal query on the actual engine with
   FLOAT/DECIMAL columns and the witness values; confirm the observable result
   differs from what the rewrite would produce. No repro → not reportable.

Only a lead surviving all four is a novel defect. Budget for the fact that the
**mature optimizers will kill most leads at steps 1–3** — that is the honest
expectation and the reason (2)/(3) above are the realistic deliverables.

## Milestones and the go/no-go gate

- **I0 — pilot (small, do first).** Stage-A extractor for Calcite
  `checkSimplify*` only; Stage-B translator for the RexNode subset those pairs
  use; Stage-C filter; run the existing audit. **Measure:** how many pairs
  extracted, how many survive the fragment filter, how many are FP/error-
  sensitive, how many refute. This yields the one number that decides the whole
  effort: *the arithmetic/nullable yield of a real rule test suite.*
- **GATE.** If the pilot yields a healthy population of FP/error-sensitive pairs
  and any survive early triage, proceed. If yield is near-zero (the fragment is
  too small, or Calcite's tests are overwhelmingly structural/boolean),
  reprioritize the engine extensions (I1–I3) *before* scaling sources, or
  reframe to the measurement-study deliverable.
- **I1–I3 — engine extensions** (typed columns, CAST/overflow, CASE/COALESCE),
  each with tests; re-run the audit after each and watch the surviving
  population grow.
- **I4 — second source** (Spark suites) once the Calcite path is proven, to
  cross-check and widen coverage.
- **I5 — triage + write-up**: run the full protocol on all leads; report either
  a confirmed defect, the guard-coverage measurement, or the rigorous negative
  result.

## Risks (named, with mitigations)

- **Low arithmetic yield** — most simplification tests are boolean/structural.
  *Mitigation:* the pilot measures this before any large investment; the
  measurement-study framing survives low yield.
- **Translation infidelity** — wrong type/operator mapping fabricates or hides
  leads. *Mitigation:* round-trip test on a labeled sample; type is mandatory,
  not inferred.
- **The "already guarded/tracked" wall** — we already hit it manually; at scale
  most leads die in triage. *Mitigation:* commit to the measurement deliverable;
  treat novel bugs as upside.
- **Maintenance / version drift** — test files change across releases. *Pin a
  release tag; the audit is a snapshot, not a live CI against upstream.*
- **Licensing** — Calcite/Spark are Apache-2.0, WeTune Apache-2.0; extracted
  expression *pairs* are facts about their behavior, but keep any verbatim
  source snippets out of this repo (same policy as `mining/`), storing only the
  normalized pairs and results, and gitignore the raw dumps.

## Honest bottom line

The pivot (harvest declarative test pairs instead of parsing imperative rules)
makes this **tractable** — I0 is a few days, not months, and the existing audit
harness + soundness contract carry over unchanged. The uncertainty is not "can
we build it" but "is there anything left to find in a mature optimizer on this
axis" — which is precisely why I0 exists: it measures the yield cheaply before
committing to the engine extensions. The measurement study is a real result
regardless, and it is the honest thing to promise; a novel defect is plausible
upside, concentrated in machine-discovered rule sets (WeTune) rather than
battle-tested ones (Calcite/Spark).
