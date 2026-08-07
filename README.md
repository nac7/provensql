# provensql

[![CI](https://github.com/nac7/provensql/actions/workflows/ci.yml/badge.svg)](https://github.com/nac7/provensql/actions/workflows/ci.yml)

**Sound-by-construction semantic diff for SQL.** Given two versions of a query, provensql tells you whether the edit could change the answer — and it is built so that it can never wrongly tell you "no change" when there was one.

```
$ provensql diff before.sql after.sql
DIFFERENT: counterexample found (instance 'with_null_row'): base returned 4 rows, head returned 5 rows
  assuming: no catalog supplied -- witness assumes no NOT NULL/UNIQUE/FK constraints
  beyond what the query text itself implies; verify against your actual schema
```

## Why

Every team that ships SQL has had the "we changed the query and something broke" incident. `sqlglot` and `sqlfluff` give you a parser and a linter; dbt gives you tests you have to write yourself; an LLM judge will confidently tell you two queries are equivalent when they aren't (see [Evaluation](#evaluation) — this is not hypothetical). Nothing answers the actual question a reviewer has: **will this change the output, for some input the tests didn't cover?**

provensql answers that question the way a formal tool should: it returns one of four verdicts, and each one means exactly what it says.

## Verdicts

| Verdict | Meaning | Backed by |
|---|---|---|
| `EQUIVALENT` | Proven identical output for **any** database instance | Canonical-form equality (Stage 2) or an SMT proof (Stage 3) |
| `SCHEMA_CHANGE` | Output columns differ in name, order, or type | Static schema comparison |
| `DIFFERENT` | A concrete database instance exists where the outputs diverge | An actual counterexample, executed and shown to you (Stage 4) |
| `UNKNOWN` | Outside what provensql can currently decide | Refusal, with a machine-readable reason code |

**The one rule that matters more than any feature:** provensql must never return `EQUIVALENT` unless it has actually proven it. This isn't a design goal stated in a doc somewhere — it's enforced in the type system. `Verdict.different()` raises `ValueError` if you call it without a witness, and there is no code path anywhere in the pipeline that can construct a false `EQUIVALENT` verdict. Undercoverage (`UNKNOWN`) is the correct, honest failure mode; a wrong answer is not.

## How it works

```
parse (Stage 0) → canonicalize (Stage 1) → canonical-form match? → EQUIVALENT
                                          → schema differs?       → SCHEMA_CHANGE
                                          → SMT proof (Stage 3)   → EQUIVALENT
                                          → counterexample search → DIFFERENT (Stage 4)
                                          → nothing found          → UNKNOWN
```

- **Stage 0 — Parse & fragment check.** Parses with `sqlglot` (BigQuery dialect) and refuses (with a reason code) anything outside the supported fragment: window functions, recursive CTEs, nested ARRAY/STRUCT types, nondeterministic functions.
- **Stage 1 — Canonicalize.** Identifier qualification + constant folding/boolean simplification, each step independently best-effort so a partial failure fails toward `UNKNOWN`, never toward a false match.
- **Stage 2 — Canonical equality.** If both queries render identically after Stage 1, they're `EQUIVALENT`.
- **Schema check.** Static comparison of the output column list.
- **Stage 3 — SMT proof.** Requires the relational skeleton (tables/joins/group-by/order/limit) to match exactly, then compiles each `WHERE`/`HAVING`/`SELECT`-list expression into Z3 terms and proves logical equivalence under SQL's three-valued NULL logic — catching rewrites like a `CASE`/`COALESCE` change or a `NOT (a <= 1)` → `a > 1` predicate flip that Stage 2's exact-match can't see. All-or-nothing: if the skeleton doesn't match, or even one expression can't be proven equivalent, it abstains entirely rather than claim a partial result. Aggregates (`COUNT`, `SUM`, ...) aren't modeled semantically but are recognized as identical when textually identical, so reordering a `HAVING` clause around an aggregate still proves out.
- **Stage 4 — Counterexample search.** Generates small adversarial database instances (NULL rows, duplicate rows, empty tables, disjoint join keys) and executes both queries against them in DuckDB. A divergence is a proof of `DIFFERENT`, complete with a replayable witness instance. Tried after Stage 3 since it's a weaker claim (an absence of a counterexample doesn't prove equivalence) and more expensive (actual execution vs. symbolic reasoning).

### Optional catalog

Without a catalog, provensql infers column types heuristically from how each column is used in the query (a literal comparison, a cast) and refuses to execute any call to a function it doesn't recognize. A `--catalog schema.yml` overrides this with ground truth:

```yaml
tables:
  orders:
    columns:
      id: INT64
      status: STRING
udfs:
  - mozfun.norm.diff_months
```

Catalog-declared UDFs get a deterministic stand-in registered in DuckDB (see `provensql/udf_rewrite.py` for why a stub is sound here even though it doesn't reproduce the UDF's real logic). Columns declared `ARRAY`/`STRUCT` cause a clean `UNKNOWN` rather than fabricated flat data.

## Install

```
pip install -e .
provensql diff base.sql head.sql [--catalog schema.yml]
```

Exit codes are CI-friendly: `0` = proven safe, `1` = needs human review, `2` = proven or flagged as a behavior change.

## Evaluation

> Full methodology, results, and honest limitations: **[docs/evaluation.md](docs/evaluation.md)**.

provensql is evaluated against real commit history, not hand-picked examples. The methodology (see `mining/`):

1. Mined 1,242 real `(before, after)` SQL pairs from commits that modified a `.sql` file across `mozilla/bigquery-etl`, `GoogleCloudPlatform/bigquery-utils`, and `dbt-labs/jaffle-shop-classic`.
2. Auto-bucketed each pair by AST diff, then drew a 213-pair stratified sample.
3. Hand-labeled all 213 pairs (`EQUIVALENT` / `DIFFERENT` / `SCHEMA_CHANGE` / `UNKNOWN`) — independently, without seeing provensql's own verdict.

**Corpus finding:** 76% of real SQL edits are semantically consequential (`DIFFERENT` + `SCHEMA_CHANGE`), and every real `WHERE`/`HAVING` touch in the sample was substantive — a touched predicate was never just cosmetic. Full breakdown in `mining/`.

**Tool results on the same 213 pairs (current state, M1+M2+M3):**

| | |
|---|---|
| Coverage (definitive verdict reached) | 10.3% |
| **False `EQUIVALENT` count** | **0** |
| Precision on decided cases | 86.4% |

Coverage is low, and that's the honest, expected shape of where this stands: most of the corpus never reaches Stage 3 or 4 at all, rejected at Stage 0 for constructs genuinely out of v0's scope (Jinja templating, BigQuery scripting, window functions). Of what does get through, Stage 3 adds real but modest coverage on top of Stage 2/4 — one additional real production query (a reserved-word backtick-quoting fix) proven equivalent by SMT where exact canonical matching didn't catch it. Small movement, honestly reported. Soundness — the number that actually matters — is clean throughout.

### Mutation testing: does Stage 3 actually work?

The natural corpus barely exercises Stage 3 (see below), so its refactor-handling is measured the rigorous way instead — apply transformations whose ground-truth answer is known by construction to 421 real single-SELECT queries, via `python mining/mutation_eval.py`:

**Equivalence-preserving rewrites (recall — want `EQUIVALENT`):**

| Rewrite | Recall | Resolved by |
|---|---|---|
| Reorder `WHERE` conjuncts | 97% | Stage 2 |
| Swap `=` operands | 88% | Stage 2 |
| Double negation | 94% | Stage 2 + 3 |
| `WHERE`→`ON` pushdown (inner joins) | **100%** | Stage 3 |
| Reorder inner-join chain | **75%** | Stage 3 |
| Redundant `DISTINCT` elimination | **88%** | Stage 3 |

**Equivalence-breaking mutations (soundness — must NEVER be `EQUIVALENT`):** across 511 mutations (flip a comparison operator, bump a literal, drop a conjunct, add a deduplicating `DISTINCT`), **zero** were wrongly certified equivalent — they land on `DIFFERENT` (with a witness) or `UNKNOWN`.

This is what an equivalence checker's evaluation should look like: high recall on the rewrite classes it targets, and a hard zero on the adversarial cases. It also caught a real limitation — Stage 3 was abstaining whenever any *unchanged* expression used a function outside the SMT fragment (`TIMESTAMP_DIFF` etc.), fixed by a sound "structurally identical expressions are trivially equivalent" fast path.

### What the full corpus says about where the ceiling is

Running the same pipeline across all **1,242** mined pairs (not just the labeled 213) is nearly identical: 10.1% coverage, still zero false `EQUIVALENT`. That near-match is itself a finding — the small sample was representative, so *sample size was never the bottleneck*. Nor is Stage 3's capability: its join-type-substitution, join-reordering, and `WHERE`/`ON` pushdown reasoning fired on **3 of 1,242 pairs**, all reserved-word quoting fixes; the join- and predicate-change buckets yielded **zero** proven-equivalent pairs, because those changes are genuine semantic edits, not the equivalence-preserving refactors Stage 3 proves. The ceiling is corpus composition: bigquery-etl is dominated by Jinja/scripting (Stage-0-rejected) and by real behavior changes. Exercising Stage 3's full range needs a refactor-heavy corpus (a dbt project's history, a SQL-formatter migration) — the reasoning is verified by the test suite regardless; this corpus just doesn't contain many cases that trigger it. Reproduce with `python mining/full_corpus_eval.py`.

Two `DIFFERENT` verdicts in this run disagree with the human label of `EQUIVALENT`. Both are the same pattern: an upstream source column was renamed and re-aliased back to the same output name, and the human labeler knew from external context that the rename preserved the data. provensql, reasoning only from the query text, cannot assume two differently-named columns hold identical data — that's a defensible default, not unsoundness, but it's a real limitation worth knowing: provensql currently has no way to declare "these two column names are known-equivalent under a rename," which would be a reasonable catalog extension.

### The comparison that motivated this project

An LLM judge given the same pairs will confidently call classic traps equivalent: `LEFT JOIN` → `JOIN` with a nullable key, `COUNT(x)` → `COUNT(*)`, `NOT IN` vs `NOT EXISTS` on a nullable column. provensql either proves the divergence with a witness or honestly says it doesn't know. It never does the first thing.

## Corpus & licensing

The mining/labeling *tooling* in this repo (`mining/*.py`) is original code under this project's Apache-2.0 license, with no encumbrance from the repos it targets — safe to point at any codebase. The mined *data itself* is not published here: `mozilla/bigquery-etl` is MPL-2.0, and redistributing verbatim source snippets extracted from it sits in a genuine gray area this project isn't going to resolve by assumption. If you want to reproduce the evaluation, `mining/repos.txt` lists the exact source repos — clone them and re-run the harness; the numbers above are reproducible from public history.

## Roadmap

- Extend Stage 3 beyond scalar-expression equivalence toward join reassociation and predicate pushdown under catalog-declared FK/uniqueness constraints (the harder, join-structure half of "conjunctive query equivalence" that v1 doesn't attempt).
- Column-rename lineage hints in the catalog (see the two-false-`DIFFERENT` finding above).
- Additional dialects beyond BigQuery (the fragment is largely dialect-agnostic via `sqlglot`; BigQuery was chosen for v0 because it has a free, credential-less sandbox that makes the eval corpus reproducible by anyone).

## Contributing

Issues and PRs welcome. If you're extending Stage 0's supported fragment or Stage 1's canonicalization rules, the one hard requirement is in `provensql/verdict.py`: no change should make it possible to emit `EQUIVALENT` without an accompanying proof. `tests/test_compare.py` and `tests/test_catalog.py` have the current soundness invariants under test — add to them, don't relax them.

## License

Apache 2.0 — see [LICENSE](LICENSE).
