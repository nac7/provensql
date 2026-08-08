# Benchmark scoping: what provensql can and can't be compared on

Goal: decide which standard SQL-equivalence benchmarks provensql can be
evaluated against *honestly* — no cherry-picking — before writing a paper's
comparison section. This memo records a measured triage, not an estimate.

## The fragment, stated precisely

provensql **parses** a wide fragment (Stage 0): `SELECT`, `UNION`/`INTERSECT`/
`EXCEPT`, subqueries, non-recursive CTEs, `GROUP BY`/`HAVING`, `ORDER BY`/
`LIMIT`, flat scalar types. It **proves equivalence** (Stage 2 canonical match
or Stage 3 SMT) over a much narrower one: the **constraint-aware conjunctive
fragment** — scalar expressions under 3-valued NULL logic, `GROUP BY`
reordering, `LEFT`↔`INNER` substitution (catalog-justified), inner-join
reordering, `WHERE`↔`ON` pushdown, and `DISTINCT` elimination. Crucially,
**aggregates compile to opaque atoms** (`smt.py`): `COUNT(*)`≡`COUNT(1)`,
aggregate pushdown, and any aggregation algebra are *not modeled* — they match
only if syntactically identical. There is **no subquery unnesting** and **no
set-operation reasoning** in the proof engine.

On an equivalence benchmark (all pairs labeled `EQUIVALENT`), only Stage 2/3
count as "solved." Stage 4 can only ever return `DIFFERENT`, never prove
equivalence.

## Candidate benchmarks

| Benchmark | Source | License | Format | Reported on by | Ingestion effort |
|---|---|---|---|---|---|
| **Calcite 232** | `georgia-tech-db/spes` → `testData/calcite_tests.json` | Apache-2.0 | 232 × `{name,q1,q2}`, one JSON file | **SPES (ICDE'21), EQUITAS** | **Low** (done) |
| Cosette suite | `uwdb/Cosette` → `examples/` | BSD-ish | many small files (`calcite/`, `sqlrewrites/`, `inequal/`) | Cosette/HoTTSQL | Medium |
| WeTune rules | `WeTune/WeTune-code` | Apache-2.0 | rule *templates* + constraints (not concrete SQL) | WeTune (SIGMOD'22) | High (must instantiate) |

The Calcite 232 is the de-facto standard: **both** SPES and EQUITAS report
coverage on exactly this set, so it's the only one offering a direct
number-to-number comparison. It's also the cheapest to ingest (one file).

## Measured triage on Calcite 232 (`eval/benchmark_triage.py`)

Running provensql over all 232 pairs, catalog-free:

| Outcome | Count | % |
|---|---|---|
| Both sides parse (in Stage-0 fragment) | 195 | 84.1% |
| **Proven `EQUIVALENT` (coverage)** | **1** | **0.4%** |
| `UNKNOWN` (abstained — sound, not covered) | 211 | 90.9% |
| `DIFFERENT` (see below) | 9 | 3.9% |
| `SCHEMA_CHANGE` (alias/parse artifacts) | 11 | 4.7% |

Of the 195 parseable pairs, **107 contain aggregation/`GROUP BY` and 87
contain a subquery — none are pure in-fragment SPJ rewrites.** That is the
entire story: the Calcite 232 is a benchmark of *aggregation and subquery*
rewrites, which is a different fragment from the one provensql proves.

### The 9 `DIFFERENT`s are correct, not soundness bugs

They are all constraint-conditional Calcite rules — `PushAggregateThroughJoin`,
`AddRedundantSemiJoin`, `DistinctCount`, `PushSemiJoinPastJoin`. Example:

```
q1: SELECT EMP.DEPTNO FROM EMP JOIN DEPT ON EMP.DEPTNO = DEPT.DEPTNO GROUP BY EMP.DEPTNO
q2: SELECT t0.DEPTNO FROM (SELECT DEPTNO FROM EMP GROUP BY DEPTNO) t0 JOIN DEPT ON t0.DEPTNO = DEPT.DEPTNO
```

These are equivalent **only if** `DEPT.DEPTNO` is a key (otherwise the join
changes multiplicities). Calcite fires the rule knowing the schema's keys;
provensql, given no catalog, correctly finds a counterexample and returns
`DIFFERENT` **with a witness** — exactly as its stated no-catalog assumption
promises. Soundness (never a false `EQUIVALENT`) is fully intact: 0 false
`EQUIVALENT` on all 232.

## Cross-check: Cosette (confirms the pattern, and sharpens the roadmap)

Two Cosette sets, both all-`EQUIVALENT`:

- **`examples/calcite/calcite_tests.json`** is the *same* Calcite set as SPES's
  (229/232 shared names; 46 differ only in SQL rendering). Triage: **1/232
  (0.4%) proven**, 82.8% parse — identical pattern to SPES, from an
  independently maintained source.
- **`examples/sqlrewrites/` (22 SPJ-oriented pairs, extracted from the `.cos`
  DSL)** — the set that *looked* most in-fragment (joinCommute, pushdownSelect,
  commutativeSelect, havingToWhere, …). Triage: **0/22 proven**, 90.9% parse,
  **0 false `EQUIVALENT`, 0 false `DIFFERENT`**.

The 0/22 is the useful part, because the *reasons* are specific and nameable —
not a predicate-reasoning weakness (the mutation eval shows provensql reorders
conjuncts fine on flat queries) but a **form** mismatch:

| Cosette pair | Why it abstains |
|---|---|
| commutativeSelect, conjunctSelect, idempotentSelect | rewrite is expressed through **nested subqueries**; provensql does no subquery unnesting, so it never sees the conjuncts |
| pushdownSelect, joinCommute | **comma-joins** (`FROM r1 x, r2 y`), not explicit `JOIN … ON` |
| havingToWhere | aggregation (opaque) **+** subquery unnesting |
| several | Cosette's `b(x)` tuple-predicate DSL notation isn't real SQL |

So even the "simple SQL rewrites" academic set is written in a
subquery-nested, comma-join, relational-algebra style that doesn't match
provensql's flat-SPJ-with-explicit-joins fragment. This directly identifies
the two highest-ROI fragment extensions: **subquery unnesting** and
**comma-join → explicit-join normalization**. Adding those would unlock a
chunk of both Cosette and Calcite without touching aggregation.

## Recommendation

**Do not run a head-to-head coverage bake-off against SPES/EQUITAS on the
Calcite 232.** provensql proves ~0.4% where they report ~95%+, not because it
is weaker but because the benchmark targets aggregation/subquery equivalences
that are *outside its fragment by design*. Presented as competitive, this
number is misleading and actively damages the paper. Worse, supplying the
Calcite `EMP`/`DEPT` catalog would **not** materially raise proof coverage:
provensql has no aggregate-pushdown or subquery-unnesting rule, so a catalog
would mostly convert the 9 `DIFFERENT`s to `UNKNOWN`, not to `EQUIVALENT`.

Instead:

1. **Use the triage as a scope-delineation figure, not a competition.** State
   plainly: SPES/EQUITAS/Cosette target aggregation- and subquery-rewrite
   equivalence; provensql targets *constraint-aware SPJ change-safety with
   executable witnesses*. The 84% parse / 0.4% prove / 3.9% correct-`DIFFERENT`
   split **is** the evidence for that positioning.

2. **Turn the constraint-conditional finding into a contribution.** Quantify
   how many of the 232 are equivalences that hold *only* under integrity
   constraints, and show provensql's witnesses expose exactly those hidden
   assumptions. "A catalog-free checker correctly declines constraint-dependent
   rewrites, and names the missing constraint" is novel and favorable.

3. **Build a fragment-matched benchmark for the capability claim.** provensql's
   real strengths (pushdown, join reorder, `LEFT`↔`INNER`, `DISTINCT`) need a
   SPJ+constraints benchmark. Best option: the existing **mutation eval** is
   already this, by construction — lean on it and formalize it, rather than
   borrowing an ill-fitting academic set. Optionally curate the small SPJ
   subset of WeTune's rules for external validity.

4. **If a named-benchmark comparison is required for the venue,** the honest
   framing is a *fragment-restricted* table: "on the N Calcite pairs within the
   SPJ fragment, provensql proves M; on the aggregation/subquery majority it
   abstains, never wrong" — with N measured, not asserted.

## Reproduce

```
curl -sL https://raw.githubusercontent.com/georgia-tech-db/spes/master/testData/calcite_tests.json \
  -o eval/benchmarks/spes_calcite_tests.json
python eval/benchmark_triage.py eval/benchmarks/spes_calcite_tests.json
```

Benchmark data is git-ignored (not redistributed); the triage harness and this
memo are the committed artifacts.
