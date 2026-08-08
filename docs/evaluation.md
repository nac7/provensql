# Evaluating provensql: soundness, coverage, and honest limits

provensql decides whether an edit to a SQL query can change its result. This
document explains what it guarantees, how that guarantee is enforced, and —
the part most tools skip — how it is *measured*, including where it falls
short. Every number here is reproducible from the scripts in `mining/` and
`eval/`.

## 1. The claim, and why the claim is falsifiable

provensql returns one of four verdicts for a `(before, after)` pair:

| Verdict | Meaning | Backed by |
|---|---|---|
| `EQUIVALENT` | Same output for **every** database instance | A proof (canonical-form equality, or SMT) |
| `SCHEMA_CHANGE` | Output columns differ in name/order/type | Static comparison |
| `DIFFERENT` | Outputs diverge on some instance | A concrete witness, executed in DuckDB |
| `UNKNOWN` | Outside what provensql can currently decide | Explicit refusal, with a reason code |

The one property that matters more than any feature: **provensql must never
return `EQUIVALENT` unless it has proven it.** An equivalence checker that is
occasionally wrong in the "yes, these are the same" direction is worse than
useless — it launders a behavior change as safe. So the entire tool is
organized around making that specific error impossible, and the entire
evaluation is organized around trying to elicit it.

This is enforced structurally, not by convention. `Verdict.different()`
raises unless given a witness; no stage below the counterexample search can
construct a `DIFFERENT`; and `EQUIVALENT` is only ever produced by a stage
that emits a proof. Undercoverage (`UNKNOWN`) is the designed-for failure
mode.

## 2. The pipeline

```
parse (Stage 0) → canonicalize (Stage 1) → canonical match?  → EQUIVALENT
                                          → schema differs?   → SCHEMA_CHANGE
                                          → SMT proof (Stage 3) → EQUIVALENT
                                          → counterexample (Stage 4) → DIFFERENT
                                          → otherwise          → UNKNOWN
```

Stage 0 refuses anything outside a supported fragment (window functions,
recursive CTEs, nested types, nondeterministic functions) with a reason
code. Stage 3 proves equivalence of the conjunctive fragment: scalar
expressions under three-valued NULL logic, `GROUP BY` reordering,
`LEFT`↔`INNER` substitution justified by catalog constraints, inner-join
reordering, `WHERE`↔`ON` pushdown, and `DISTINCT` elimination. Stage 4
disproves equivalence by finding a database instance where the two queries
diverge, and hands back that instance as a replayable witness.

## 3. Three complementary evaluations

No single evaluation answers every question, so provensql uses three.

### 3a. Hand-labeled real-world corpus (precision & soundness vs. ground truth)

We mined 1,242 real `(before, after)` SQL pairs from the commit history of
`mozilla/bigquery-etl`, `GoogleCloudPlatform/bigquery-utils`, and
`dbt-labs/jaffle-shop-classic` — every commit that modified a `.sql` file.
A 213-pair stratified sample was hand-labeled `EQUIVALENT` / `DIFFERENT` /
`SCHEMA_CHANGE` / `UNKNOWN`, independently of the tool's own output.

A finding worth stating on its own: **76% of real SQL edits are
semantically consequential** (`DIFFERENT` or `SCHEMA_CHANGE`), and in the
sample every touched `WHERE`/`HAVING` predicate changed behavior — not one
was cosmetic. Query edits are rarely "just refactors."

Tool results on those 213 pairs (`python eval/run_eval.py --catalog mining/output/udf_catalog.yml`):

| | |
|---|---|
| Coverage (definitive verdict) | 10.8% |
| **False `EQUIVALENT`** | **0** |
| Precision on decided cases | 87.0% |

### 3b. Full corpus (where the coverage ceiling actually is)

Running the same pipeline over all 1,242 pairs
(`python mining/full_corpus_eval.py`) gives 10.1% coverage — nearly
identical to the sample's 10.8%. That near-match is itself the finding: the
sample was representative, so **sample size is not the bottleneck**, and
neither is Stage 3's capability — its join/predicate/pushdown reasoning
fired on just 3 of 1,242 pairs. The `join_change` and `predicate_change`
buckets yielded *zero* proven-equivalent pairs, because those changes are
genuine semantic edits, not equivalence-preserving refactors.

The ceiling is **corpus composition**: ~46%+ of pairs are refused at Stage 0
(Jinja templating, BigQuery scripting, window functions), and most of the
rest are real behavior changes. bigquery-etl is production ETL, not a
refactoring playground — so it exercises soundness heavily and the
refactor-proving machinery barely at all. That motivates the third
evaluation.

### 3c. Mutation testing (does the refactor-proving machinery work?)

To measure the capabilities the natural corpus doesn't exercise, we apply
transformations whose ground-truth answer is known by construction to 421
real single-SELECT queries (`python mining/mutation_eval.py`).

**Equivalence-preserving rewrites — recall (want `EQUIVALENT`):**

| Rewrite | Recall | Resolved by |
|---|---|---|
| Reorder `WHERE` conjuncts | 97% | Stage 2 |
| Swap `=` operands | 88% | Stage 2 |
| Double negation | 94% | Stage 2 + 3 |
| `WHERE`→`ON` pushdown (inner joins) | **100%** | Stage 3 |
| Reorder inner-join chain | **75%** | Stage 3 |
| Redundant `DISTINCT` elimination | **88%** | Stage 3 |

**Equivalence-breaking mutations — soundness (must NEVER be `EQUIVALENT`):**
across **511** mutations (flip a comparison operator, bump a literal, drop a
conjunct, add a deduplicating `DISTINCT`), **zero** were wrongly certified
equivalent. Every one landed on `DIFFERENT` (with a witness) or `UNKNOWN`.

This is the shape an equivalence checker's evaluation should have: high
recall on the rewrite classes it targets, and a hard zero on adversarial
cases.

### The comparison that motivates all of this

Asked whether two queries are equivalent, an LLM will confidently say "yes"
to classic traps: `LEFT JOIN`→`JOIN` on a nullable key, `COUNT(x)`→`COUNT(*)`,
`NOT IN` vs `NOT EXISTS` with NULLs, a `CASE` whose branches were flipped
without accounting for NULL. provensql either proves the divergence with a
concrete witness or says `UNKNOWN`. It never guesses "equivalent."

### Baselines (`python eval/baselines.py`)

Three obvious alternatives, scored on the same 213 pairs, reporting the one
metric that matters — false-`EQUIVALENT` count (said equivalent when the
human label was `DIFFERENT`/`SCHEMA_CHANGE`), against provensql's **0**:

| Baseline | False `EQUIVALENT` | Note |
|---|---|---|
| String equality | 0 | Calls *every* pair `DIFFERENT` (they're real diffs) — never recognizes a true equivalence; accuracy 40.8% |
| `sqlglot`-normalized string compare | 0 | Same — naive normalization catches none of the 213, so its clean soundness is vacuous |
| LLM judge (Claude/GPT) | *run it* | Needs an API key + billed calls; this is the case that produces false positives |

The two trivial baselines score a clean zero only because they never claim
equivalence at all — their soundness is an artifact of refusing to play, not
a real result. The LLM judge is the one that *does* claim equivalence
liberally and gets some wrong; the harness runs it (`--model`, `--limit`)
so the false-`EQUIVALENT` rate can be measured directly against provensql's
zero. The point isn't that provensql is more cautious than an LLM — the
trivial baselines are trivially cautious too — it's that provensql claims
equivalence on real cases *and* is never wrong when it does.

## 4. What the process caught

The evaluation harness is not decoration — it has repeatedly caught real
defects before they shipped, which is the strongest evidence it measures
something real:

- **A live false `EQUIVALENT` via a dependency bug.** The trust-boundary
  test (`test_simplify_faithful.py`) differentially checks the one external
  transform Stage 1 trusted — sqlglot's `simplify()` — against DuckDB, and
  found it collapsing `CASE WHEN flag THEN b WHEN TRUE THEN 2 ELSE 0 END` to
  `2` (silently dropping the earlier branch). Because `canonicalize()` ran
  `simplify()` and Stage 2 asserts `EQUIVALENT` on a canonical-string match,
  this produced a real false `EQUIVALENT` in the pipeline (`compare(CASE…, 2)`
  returned `EQUIVALENT`). Fixed by removing `simplify()` from the pipeline
  entirely — canonicalization now applies only qualification plus rendering,
  both semantics-preserving; the equivalences `simplify()` used to catch move
  to the SMT-validated Stage 3. Zero coverage cost on the real corpus.
- **Identical-expression abstention.** Stage 3 abstained whenever *any*
  expression used a function outside the SMT fragment, even when that
  expression was unchanged and only some other part of the query differed.
  Real queries almost always contain such a function, so this silently
  blocked Stage 3 across the board. The mutation eval surfaced it
  (`WHERE`→`ON` pushdown recall was 0/6); a sound "structurally identical
  expressions are trivially equivalent" fast path fixed it (0/6 → 6/6).
- **A backwards `DISTINCT` rule.** The first draft of `DISTINCT` elimination
  would have accepted `SELECT DISTINCT a … GROUP BY a, b` as redundant,
  which is false (one `a` spans many `b` groups). Caught while writing the
  adversarial test; the case is now a permanent regression test.
- **Execution-layer bugs** (catalog-qualified table names, query
  parameters, column-domain collisions) surfaced only by running against
  real queries, never by unit tests alone.

## 5. Honest limitations

- **Coverage on production ETL is low (~10%)** and bounded by Stage 0's
  refusal of templated/scripted SQL, not by proof power. A refactor-heavy
  or plain-SQL corpus would show materially higher coverage.
- **Catalog-dependent proofs** (`LEFT`↔`INNER`, `DISTINCT` via uniqueness)
  need FK/`NOT NULL`/`UNIQUE` declarations the tool can't infer without a
  catalog; every such verdict prints the assumption it relied on.
- **Out of scope in v1:** `RIGHT`/`FULL` join substitution, self-joins,
  redundant-join elimination, subquery unnesting, nested (`ARRAY`/`STRUCT`)
  data. Each currently yields a clean `UNKNOWN`, never a guess.
- **The mined corpus is not redistributed** (`mozilla/bigquery-etl` is
  MPL-2.0); results reproduce from public history via the harness.

## 6. Reproducing everything

```
pip install -e ".[dev]"
python -m pytest tests/ -q                                   # 39 unit tests
python eval/run_eval.py --catalog mining/output/udf_catalog.yml   # hand-labeled
python mining/full_corpus_eval.py                            # full-corpus ceiling
python mining/mutation_eval.py                               # recall + soundness
```

The single number to check across all of them: false `EQUIVALENT` count.
It is zero, and it is meant to stay zero.
