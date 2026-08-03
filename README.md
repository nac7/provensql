# sqlsense

**Sound-by-construction semantic diff for SQL.** Given two versions of a query, sqlsense tells you whether the edit could change the answer — and it is built so that it can never wrongly tell you "no change" when there was one.

```
$ sqlsense diff before.sql after.sql
DIFFERENT: counterexample found (instance 'with_null_row'): base returned 4 rows, head returned 5 rows
  assuming: no catalog supplied -- witness assumes no NOT NULL/UNIQUE/FK constraints
  beyond what the query text itself implies; verify against your actual schema
```

## Why

Every team that ships SQL has had the "we changed the query and something broke" incident. `sqlglot` and `sqlfluff` give you a parser and a linter; dbt gives you tests you have to write yourself; an LLM judge will confidently tell you two queries are equivalent when they aren't (see [Evaluation](#evaluation) — this is not hypothetical). Nothing answers the actual question a reviewer has: **will this change the output, for some input the tests didn't cover?**

sqlsense answers that question the way a formal tool should: it returns one of four verdicts, and each one means exactly what it says.

## Verdicts

| Verdict | Meaning | Backed by |
|---|---|---|
| `EQUIVALENT` | Proven identical output for **any** database instance | Canonical-form equality after safe rewrites (Stage 2) |
| `SCHEMA_CHANGE` | Output columns differ in name, order, or type | Static schema comparison |
| `DIFFERENT` | A concrete database instance exists where the outputs diverge | An actual counterexample, executed and shown to you (Stage 4) |
| `UNKNOWN` | Outside what sqlsense can currently decide | Refusal, with a machine-readable reason code |

**The one rule that matters more than any feature:** sqlsense must never return `EQUIVALENT` unless it has actually proven it. This isn't a design goal stated in a doc somewhere — it's enforced in the type system. `Verdict.different()` raises `ValueError` if you call it without a witness, and there is no code path from Stage 0–3 that can construct a `DIFFERENT` or false `EQUIVALENT` verdict. Undercoverage (`UNKNOWN`) is the correct, honest failure mode; a wrong answer is not.

## How it works

```
parse (Stage 0) → canonicalize (Stage 1) → canonical-form match? → EQUIVALENT
                                          → schema differs?       → SCHEMA_CHANGE
                                          → counterexample search → DIFFERENT (Stage 4)
                                          → nothing found          → UNKNOWN
```

- **Stage 0 — Parse & fragment check.** Parses with `sqlglot` (BigQuery dialect) and refuses (with a reason code) anything outside the supported fragment: window functions, recursive CTEs, nested ARRAY/STRUCT types, nondeterministic functions.
- **Stage 1 — Canonicalize.** Identifier qualification + constant folding/boolean simplification, each step independently best-effort so a partial failure fails toward `UNKNOWN`, never toward a false match.
- **Stage 2 — Canonical equality.** If both queries render identically after Stage 1, they're `EQUIVALENT`.
- **Schema check.** Static comparison of the output column list.
- **Stage 4 — Counterexample search.** Generates small adversarial database instances (NULL rows, duplicate rows, empty tables, disjoint join keys) and executes both queries against them in DuckDB. A divergence is a proof of `DIFFERENT`, complete with a replayable witness instance.
- **Stage 3 — SMT proof for the conjunctive fragment.** Not built yet. This is the current roadmap priority (see below) — it's what will let sqlsense prove equivalence of expression-level rewrites (a `CASE`/`COALESCE` change, a predicate rewrite) that Stage 2's exact-match can't see and Stage 4 can't always find a counterexample for.

### Optional catalog

Without a catalog, sqlsense infers column types heuristically from how each column is used in the query (a literal comparison, a cast) and refuses to execute any call to a function it doesn't recognize. A `--catalog schema.yml` overrides this with ground truth:

```yaml
tables:
  orders:
    columns:
      id: INT64
      status: STRING
udfs:
  - mozfun.norm.diff_months
```

Catalog-declared UDFs get a deterministic stand-in registered in DuckDB (see `sqlsense/udf_rewrite.py` for why a stub is sound here even though it doesn't reproduce the UDF's real logic). Columns declared `ARRAY`/`STRUCT` cause a clean `UNKNOWN` rather than fabricated flat data.

## Install

```
pip install -e .
sqlsense diff base.sql head.sql [--catalog schema.yml]
```

Exit codes are CI-friendly: `0` = proven safe, `1` = needs human review, `2` = proven or flagged as a behavior change.

## Evaluation

sqlsense is evaluated against real commit history, not hand-picked examples. The methodology (see `mining/`):

1. Mined 1,242 real `(before, after)` SQL pairs from commits that modified a `.sql` file across `mozilla/bigquery-etl`, `GoogleCloudPlatform/bigquery-utils`, and `dbt-labs/jaffle-shop-classic`.
2. Auto-bucketed each pair by AST diff, then drew a 213-pair stratified sample.
3. Hand-labeled all 213 pairs (`EQUIVALENT` / `DIFFERENT` / `SCHEMA_CHANGE` / `UNKNOWN`) — independently, without seeing sqlsense's own verdict.

**Corpus finding:** 76% of real SQL edits are semantically consequential (`DIFFERENT` + `SCHEMA_CHANGE`), and every real `WHERE`/`HAVING` touch in the sample was substantive — a touched predicate was never just cosmetic. Full breakdown in `mining/`.

**Tool results on the same 213 pairs (current state, M1+M2):**

| | |
|---|---|
| Coverage (definitive verdict reached) | 9.9% |
| **False `EQUIVALENT` count** | **0** |
| Precision on decided cases | 85.7% |

Coverage is low, and that's the honest, expected shape of this milestone: Stage 3 doesn't exist yet, so most real-world "this predicate got rewritten" or "this UDF's arguments changed" cases correctly fall through to `UNKNOWN` rather than get guessed. Soundness — the number that actually matters — is clean.

Two `DIFFERENT` verdicts in this run disagree with the human label of `EQUIVALENT`. Both are the same pattern: an upstream source column was renamed and re-aliased back to the same output name, and the human labeler knew from external context that the rename preserved the data. sqlsense, reasoning only from the query text, cannot assume two differently-named columns hold identical data — that's a defensible default, not unsoundness, but it's a real limitation worth knowing: sqlsense currently has no way to declare "these two column names are known-equivalent under a rename," which would be a reasonable catalog extension.

### The comparison that motivated this project

An LLM judge given the same pairs will confidently call classic traps equivalent: `LEFT JOIN` → `JOIN` with a nullable key, `COUNT(x)` → `COUNT(*)`, `NOT IN` vs `NOT EXISTS` on a nullable column. sqlsense either proves the divergence with a witness or honestly says it doesn't know. It never does the first thing.

## Corpus & licensing

The mining/labeling *tooling* in this repo (`mining/*.py`) is original code under this project's Apache-2.0 license, with no encumbrance from the repos it targets — safe to point at any codebase. The mined *data itself* is not published here: `mozilla/bigquery-etl` is MPL-2.0, and redistributing verbatim source snippets extracted from it sits in a genuine gray area this project isn't going to resolve by assumption. If you want to reproduce the evaluation, `mining/repos.txt` lists the exact source repos — clone them and re-run the harness; the numbers above are reproducible from public history.

## Roadmap

- **Stage 3**: SMT-based equivalence proof for the conjunctive-query fragment (joins, equality/comparison predicates, set semantics) — targets the largest unresolved category in the eval corpus (expression-level rewrites: `CASE`/`COALESCE`/`CAST` changes).
- Column-rename lineage hints in the catalog (see the two-false-`DIFFERENT` finding above).
- Additional dialects beyond BigQuery (the fragment is largely dialect-agnostic via `sqlglot`; BigQuery was chosen for v0 because it has a free, credential-less sandbox that makes the eval corpus reproducible by anyone).

## Contributing

Issues and PRs welcome. If you're extending Stage 0's supported fragment or Stage 1's canonicalization rules, the one hard requirement is in `sqlsense/verdict.py`: no change should make it possible to emit `EQUIVALENT` without an accompanying proof. `tests/test_compare.py` and `tests/test_catalog.py` have the current soundness invariants under test — add to them, don't relax them.

## License

Apache 2.0 — see [LICENSE](LICENSE).
