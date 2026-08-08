"""
Validates the soundness property Stage 2 actually depends on: that
canonicalize() is semantics-preserving, so a canonical-string match really
does imply equivalence.

This test is why canonicalize() no longer calls sqlglot's simplify(). The
fuzzer below originally checked simplify() directly and found it collapsing
`CASE WHEN flag THEN b WHEN TRUE THEN 2 ELSE 0 END` to `2` -- dropping the
earlier branch -- which through canonicalize() produced a live false
EQUIVALENT at Stage 2 (`compare(CASE..., 2)` returned EQUIVALENT). simplify()
was removed from the pipeline; canonicalize() now applies only qualification
(structural) plus the renderer's normalization, both semantics-preserving.

So the fuzzer now checks canonicalize() itself: generate random expressions,
canonicalize the query that projects them, and confirm the projected
expression evaluates identically before and after in DuckDB across random
rows including NULLs. If anyone re-introduces an unsound transform, the
CASE-family case is in the generator's range and this fails.
"""

import random
import sys
from pathlib import Path

import duckdb
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).parent))

from test_smt_differential import (  # noqa: E402  (shared fuzzer + DuckDB harness)
    _random_assignment,
    _values_agree,
    eval_duckdb,
    random_expr,
)

from provensql import schema_infer as si  # noqa: E402
from provensql.canonicalize import UnsupportedConstruct, canonicalize, parse  # noqa: E402
from provensql.compare import compare  # noqa: E402
from provensql.verdict import VerdictType  # noqa: E402


def _unalias(node: exp.Expression) -> exp.Expression:
    return node.this if isinstance(node, exp.Alias) else node


def run_canonicalize_check(n_expr, n_assign, seed, max_depth=4):
    rng = random.Random(seed)
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (a DOUBLE, b DOUBLE, s VARCHAR, u VARCHAR, flag BOOLEAN)")
    mismatches, checked = [], 0

    for _ in range(n_expr):
        sort = rng.choice([si.BOOLEAN, si.NUMERIC, si.VARCHAR])
        expr_sql = random_expr(sort, rng.randint(1, max_depth), rng).sql(dialect="bigquery")
        try:
            parsed = parse(f"SELECT ({expr_sql}) AS c FROM t")
        except UnsupportedConstruct:
            continue
        if not isinstance(parsed, exp.Select) or not parsed.selects:
            continue
        canon = canonicalize(parsed)
        orig_e, canon_e = _unalias(parsed.selects[0]), _unalias(canon.selects[0])
        for _ in range(n_assign):
            assignment = _random_assignment(rng)
            try:
                ov = eval_duckdb(con, orig_e, assignment)
                cv = eval_duckdb(con, canon_e, assignment)
            except Exception:
                continue
            checked += 1
            if not _values_agree(ov, cv, sort):
                mismatches.append({
                    "original": orig_e.sql(dialect="duckdb"),
                    "canonicalized": canon_e.sql(dialect="duckdb"),
                    "assignment": {f"{t}.{c}": v for (t, c), v in assignment.items()},
                    "original_val": ov, "canonicalized_val": cv,
                })
    con.close()
    return mismatches, checked


def test_canonicalize_preserves_semantics():
    mismatches, checked = run_canonicalize_check(n_expr=300, n_assign=15, seed=555)
    assert checked > 1000, f"too few cases actually checked ({checked})"
    assert not mismatches, (
        f"canonicalize() changed query meaning in {len(mismatches)} cases -- Stage 2 "
        f"asserts EQUIVALENT on a canonical match, so this must never happen. "
        f"e.g. {mismatches[:3]}"
    )


def test_sqlglot_simplify_bug_is_not_a_false_equivalent():
    # The exact expression the fuzzer caught sqlglot's simplify() mangling.
    # It must never be certified equivalent to the bare `2` it wrongly folds to.
    v = compare(
        "SELECT CASE WHEN flag THEN b WHEN TRUE THEN 2 ELSE 0 END AS y FROM t",
        "SELECT 2 AS y FROM t",
    )
    assert v.type != VerdictType.EQUIVALENT


if __name__ == "__main__":
    import sys as _sys

    seed = int(_sys.argv[1]) if len(_sys.argv) > 1 else 0
    ms, n = run_canonicalize_check(n_expr=5000, n_assign=40, seed=seed)
    print(f"checked {n} (expression, row) evaluations")
    if ms:
        print(f"!!! {len(ms)} canonicalize() semantics changes !!!")
        for m in ms[:20]:
            print(" ", m)
        _sys.exit(1)
    print("canonicalize() preserved semantics on every checked case")
