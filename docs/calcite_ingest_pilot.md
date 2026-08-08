# I0 pilot: automatic ingestion of Calcite's simplification pairs

This is the pilot from `rule_ingestion_scope.md` (I0): the cheap experiment
whose only job is to **measure the yield** of automatically ingesting a real
optimizer's rules before committing to the full effort. It builds the extract →
translate → filter → audit pipeline against **Apache Calcite**'s own
`checkSimplify` test assertions and reports what came out.

Code: `mining/calcite_ingest/` (`extract.py`, `translate.py`, `run_audit.py`).
Source: `RexProgramTest.java`, Calcite `master`, fetched 2026-08-08 (kept out of
this repo; the harness takes its path). The audit run is **checkpointed and
resumable** — killing it mid-run and re-running continues from the last pair.

## What the pipeline does

Calcite encodes its intended simplifications as test assertions
`checkSimplify(input, "expected")`, where `input` is built with the test DSL
(`plus`, `div`, `isNull`, `case_`, …) and `expected` is the RexNode dump of the
simplified form. We harvest those `(input, expected)` pairs (no imperative
rule-code parsing), translate both sides into SQL, keep the ones inside the
modeled arithmetic/NULL fragment, and ask the error-semantics engine whether it
agrees the two are equivalent. Type and nullability ride along in Calcite's own
column names (`vIntNotNull` → `notNullInt0`, dumped as `?0.notNullInt0`).

## Yield (the number the pilot exists to produce)

| Stage | Count |
|---|---|
| `checkSimplify*` pairs extracted | **391** |
| …arithmetic (`plus/minus/mul/div/sub`) | 41 |
| …NULL-ish (`isNull/coalesce/nullif/case_`) | 100 |
| **candidate set** (arithmetic ∨ NULL-ish) | **117** |
| translated into the modeled fragment & audited | **26** |
| skipped (out of fragment after translation) | 91 |

Two facts dominate:

1. **Floating-point yield from Calcite is ≈ zero.** Of the 41 arithmetic pairs,
   the column types are 12 integer, 1 decimal, **0 float/double**. Calcite does
   not test FP-unsound rewrites *because it does not perform them* — so its test
   suite carries almost no signal on the IEEE-754 axis. Mining Calcite is the
   wrong route to an FP finding; that axis needs a source that actually applies
   rewrites to `FLOAT`/`DOUBLE` (Spark's arithmetic suites, DataFusion, or
   applying rules to float columns directly).
2. **The binding constraint is fragment coverage, not extraction.** 91 of 117
   candidates were skipped because a side uses `AND`/`OR`/`CASE`/`COALESCE`/
   comparisons or a locally-declared ref we don't resolve — exactly the engine
   extensions the scope names (gaps #3, #5) plus a symbol resolver.

## Validation result (the audit)

Of the 26 pairs we could translate and audit, **all 26 are `EQUIVALENT_ERR` and
there are zero contradictions**: the error-semantics engine agrees with every
Calcite simplification it can model. This is real external validation of the M2
engine against 26 independently-authored test pairs, including:

- `(notNullInt0 + notNullInt1) IS NULL` → `FALSE`, and the `IS NOT NULL` / mixed
  nullability variants — null-propagation reasoning the engine reproduces.
- `int0 * NULL` → `NULL` (and `+`, `-`, `/`) — NULL propagation through
  arithmetic.
- `(notNullInt0 / 2) IS NULL` → `FALSE` and `(notNullDecimal0 / 2.5) IS NULL` →
  `FALSE` — division by a nonzero constant never raises, so the outcome is
  non-null; the engine agrees.
- The **post-CALCITE-7145-fix** shape `IS NULL(CAST(div(notNullInt0, 0)…))`,
  which current Calcite keeps *symbolic* (no longer folds to `false`); the engine
  agrees it must not be folded.

No contradiction means no error-axis defect surfaced in Calcite's current
simplifier — the expected outcome for a mature, already-patched optimizer, and a
clean demonstration that the pipeline neither fabricates nor misses on 26 real
cases.

## Go / no-go

The pilot answers the gate question cheaply and honestly:

- **Novel-defect hunting in Calcite: no-go.** The obvious unsound rewrites are
  guarded or already tracked (we independently re-confirmed this), the FP-axis
  yield is ≈ 0, and the error-axis audit is clean. Grinding Calcite further is
  unlikely to produce a new bug.
- **The pipeline works and validates the engine: go, as a measurement study.**
  The honest, publishable result is *"an automatic cross-checker that reproduces
  a mature optimizer's simplification soundness on the axes provers ignore, and
  measures where the checker's fragment must grow to cover more"* — the
  guard-coverage / validation framing from the scope's success criterion (2).
- **If a novel FP/error defect is the goal**, redirect to the sources where a
  guard is likelier to be missing: **WeTune's machine-discovered rules**, and
  **applying rewrites to `FLOAT`/`DECIMAL` columns directly** rather than reading
  an integer-typed test suite. That is a deliberate next-target decision, not a
  continuation of the Calcite grind.

## To continue (if the gate is passed)

Priced by the 91 skips, in order: (1) `CASE`/`COALESCE` and comparison outcomes
in the error engine; (2) a per-method symbol resolver for local refs
(`a`, `zero`, `one`); (3) `AND`/`OR` under Kleene logic. Each unlocks a
measurable slice of the 91 and is independently testable. The extraction and
checkpointed audit harness carry over unchanged.
