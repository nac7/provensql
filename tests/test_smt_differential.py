"""
Differential validation of the SMT encoding against a real SQL engine.

Stage 3's entire soundness rests on smt.py's hand-written (is_null, value)
encoding being a *faithful* model of SQL scalar semantics -- the
three-valued AND/OR truth tables, COALESCE, CASE NULL fall-through,
comparison NULL propagation. That faithfulness is otherwise backed only by
a handful of hand-written unit tests.

This generates random in-fragment expressions and, for many random input
rows (NULLs included), evaluates each one BOTH through the Z3 encoding and
through DuckDB, and asserts they agree (same value, or both NULL). A
disagreement means the encoding mismodels SQL semantics -- which would make
every proof built on it unsound. Run as a pytest for a bounded, seeded
check in CI; run as `python tests/test_smt_differential.py` for a large
sweep.

Division is deliberately excluded from generation: the encoding models
NUMERIC as Z3 Real (exact rationals) while DuckDB uses floating point, so
`1/3` would differ for reasons unrelated to the 3-valued-logic encoding
this test exists to validate (that gap is documented separately).
"""

import random

import duckdb
import sqlglot
import z3
from sqlglot import exp

from provensql import schema_infer as si
from provensql.smt import Undecidable, compile_expr

# One synthetic table; every column nullable, covering each modeled sort.
COLUMNS = {
    ("t", "a"): si.NUMERIC,
    ("t", "b"): si.NUMERIC,
    ("t", "s"): si.VARCHAR,
    ("t", "u"): si.VARCHAR,
    ("t", "flag"): si.BOOLEAN,
}
_TABLE_SCHEMAS = {"t": {col: {"type": typ, "literals": set()} for (tbl, col), typ in COLUMNS.items()}}

_NUM_DOMAIN = [-2, -1, 0, 1, 2, 3]
_STR_DOMAIN = ["x", "y", "z", ""]


def _cols_of(sort):
    return [name for (tbl, name), typ in COLUMNS.items() if typ == sort]


def _literal(sort, rng):
    if sort == si.NUMERIC:
        return exp.Literal(this=str(rng.choice(_NUM_DOMAIN)), is_string=False)
    if sort == si.VARCHAR:
        return exp.Literal(this=rng.choice(_STR_DOMAIN), is_string=True)
    return exp.Boolean(this=rng.choice([True, False]))


def _col(sort, rng):
    return exp.column(rng.choice(_cols_of(sort)), table="t")


def random_expr(sort, depth, rng):
    """Generate a random, type-correct, in-fragment expression of `sort`."""
    if depth <= 0 or rng.random() < 0.35:
        r = rng.random()
        if r < 0.55:
            return _col(sort, rng)
        if r < 0.9:
            return _literal(sort, rng)
        return exp.Null()  # bare NULL occasionally

    if sort == si.BOOLEAN:
        kind = rng.choice(
            ["eq_num", "cmp_num", "eq_str", "and", "or", "not", "isnull", "in", "between", "col", "lit"]
        )
        if kind == "eq_num":
            op = rng.choice([exp.EQ, exp.NEQ])
            return op(this=random_expr(si.NUMERIC, depth - 1, rng), expression=random_expr(si.NUMERIC, depth - 1, rng))
        if kind == "cmp_num":
            op = rng.choice([exp.GT, exp.GTE, exp.LT, exp.LTE])
            return op(this=random_expr(si.NUMERIC, depth - 1, rng), expression=random_expr(si.NUMERIC, depth - 1, rng))
        if kind == "eq_str":
            op = rng.choice([exp.EQ, exp.NEQ])
            return op(this=random_expr(si.VARCHAR, depth - 1, rng), expression=random_expr(si.VARCHAR, depth - 1, rng))
        if kind == "and":
            return exp.And(this=random_expr(si.BOOLEAN, depth - 1, rng), expression=random_expr(si.BOOLEAN, depth - 1, rng))
        if kind == "or":
            return exp.Or(this=random_expr(si.BOOLEAN, depth - 1, rng), expression=random_expr(si.BOOLEAN, depth - 1, rng))
        if kind == "not":
            return exp.Not(this=random_expr(si.BOOLEAN, depth - 1, rng))
        if kind == "isnull":
            operand_sort = rng.choice([si.NUMERIC, si.VARCHAR, si.BOOLEAN])
            node = exp.Is(this=random_expr(operand_sort, depth - 1, rng), expression=exp.Null())
            return exp.Not(this=node) if rng.random() < 0.5 else node
        if kind == "in":
            lits = [_literal(si.NUMERIC, rng) for _ in range(rng.randint(1, 3))]
            return exp.In(this=random_expr(si.NUMERIC, depth - 1, rng), expressions=lits)
        if kind == "between":
            lo, hi = sorted(rng.sample(_NUM_DOMAIN, 2))
            return exp.Between(
                this=random_expr(si.NUMERIC, depth - 1, rng),
                low=exp.Literal(this=str(lo), is_string=False),
                high=exp.Literal(this=str(hi), is_string=False),
            )
        return _col(sort, rng) if kind == "col" else _literal(sort, rng)

    # NUMERIC or VARCHAR
    kind = rng.choice(["case", "coalesce", "arith", "col", "lit"] if sort == si.NUMERIC else ["case", "coalesce", "col", "lit"])
    if kind == "arith":
        op = rng.choice([exp.Add, exp.Sub, exp.Mul])  # no Div (Real vs float)
        return op(this=random_expr(si.NUMERIC, depth - 1, rng), expression=random_expr(si.NUMERIC, depth - 1, rng))
    if kind == "case":
        n = rng.randint(1, 2)
        ifs = [exp.If(this=random_expr(si.BOOLEAN, depth - 1, rng), true=random_expr(sort, depth - 1, rng)) for _ in range(n)]
        return exp.Case(ifs=ifs, default=random_expr(sort, depth - 1, rng))
    if kind == "coalesce":
        parts = [random_expr(sort, depth - 1, rng) for _ in range(rng.randint(2, 3))]
        return exp.Coalesce(this=parts[0], expressions=parts[1:])
    return _col(sort, rng) if kind == "col" else _literal(sort, rng)


def _random_assignment(rng):
    a = {}
    for (tbl, col), typ in COLUMNS.items():
        if rng.random() < 0.3:
            a[(tbl, col)] = None
        elif typ == si.NUMERIC:
            a[(tbl, col)] = rng.choice(_NUM_DOMAIN)
        elif typ == si.VARCHAR:
            a[(tbl, col)] = rng.choice(_STR_DOMAIN)
        else:
            a[(tbl, col)] = rng.choice([True, False])
    return a


def _dummy(sort):
    return {si.NUMERIC: z3.RealVal(0), si.VARCHAR: z3.StringVal(""), si.BOOLEAN: z3.BoolVal(False)}[sort]


def _lit(sort, value):
    if sort == si.NUMERIC:
        return z3.RealVal(value)
    if sort == si.VARCHAR:
        return z3.StringVal(value)
    return z3.BoolVal(value)


def eval_z3(compiled, column_vars, assignment):
    """Returns None (SQL NULL) or a concrete Python value."""
    subs = []
    for key, sqlv in column_vars.items():
        v = assignment[key]
        subs.append((sqlv.is_null, z3.BoolVal(v is None)))
        subs.append((sqlv.value, _dummy(sqlv.sort) if v is None else _lit(sqlv.sort, v)))
    is_null = z3.simplify(z3.substitute(compiled.is_null, *subs))
    if z3.is_true(is_null):
        return None
    val = z3.simplify(z3.substitute(compiled.value, *subs))
    if compiled.sort == si.BOOLEAN:
        return z3.is_true(val)
    if compiled.sort == si.VARCHAR:
        return val.as_string()
    return float(val.as_fraction())  # NUMERIC


def eval_duckdb(con, expr, assignment):
    con.execute("DELETE FROM t")
    cols = list(COLUMNS.keys())
    con.execute(
        "INSERT INTO t VALUES (?, ?, ?, ?, ?)",
        [assignment[c] for c in cols],
    )
    sql = expr.sql(dialect="duckdb")
    result = con.execute(f"SELECT {sql} FROM t").fetchone()[0]
    return result


def _values_agree(z3_val, duck_val, sort):
    if z3_val is None or duck_val is None:
        return z3_val is None and duck_val is None
    if sort == si.NUMERIC:
        return abs(float(z3_val) - float(duck_val)) < 1e-9
    if sort == si.BOOLEAN:
        return bool(z3_val) == bool(duck_val)
    return str(z3_val) == str(duck_val)


def run_differential(n_expr, n_assign, seed, max_depth=4):
    """Returns a list of mismatch dicts (empty == encoding matches DuckDB)."""
    rng = random.Random(seed)
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (a DOUBLE, b DOUBLE, s VARCHAR, u VARCHAR, flag BOOLEAN)")
    column_vars = None
    from provensql.smt import build_column_vars

    column_vars = build_column_vars(_TABLE_SCHEMAS)
    mismatches = []
    checked = 0

    for _ in range(n_expr):
        sort = rng.choice([si.BOOLEAN, si.NUMERIC, si.VARCHAR])
        generated = random_expr(sort, rng.randint(1, max_depth), rng)
        # Round-trip through SQL text and re-parse. The generator builds ASTs
        # directly (with no explicit Paren nodes), and rendering such an AST
        # can drop precedence parentheses -- so the raw AST and its rendered
        # SQL can mean different things. The real pipeline only ever compiles
        # parsed-from-text trees, so we do the same here: compile and execute
        # whatever the SQL TEXT actually means, keeping both engines honest.
        sql_text = generated.sql(dialect="bigquery")
        try:
            expr = sqlglot.parse_one(sql_text, dialect="bigquery")
        except Exception:
            continue
        if expr is None:
            continue
        try:
            compiled = compile_expr(expr, column_vars, {})
        except Undecidable:
            continue
        if compiled.sort is None:  # bare NULL top-level
            continue
        for _ in range(n_assign):
            assignment = _random_assignment(rng)
            try:
                zv = eval_z3(compiled, column_vars, assignment)
                dv = eval_duckdb(con, expr, assignment)
            except Exception as e:
                mismatches.append({"expr": expr.sql(dialect="duckdb"), "error": repr(e)[:200], "assignment": dict(assignment)})
                continue
            checked += 1
            if not _values_agree(zv, dv, compiled.sort):
                mismatches.append({
                    "expr": expr.sql(dialect="duckdb"),
                    "assignment": {f"{t}.{c}": v for (t, c), v in assignment.items()},
                    "z3": zv, "duckdb": dv, "sort": compiled.sort,
                })
    con.close()
    return mismatches, checked


def test_smt_encoding_matches_duckdb():
    """Bounded, seeded run for CI. Any mismatch is a soundness-relevant bug."""
    mismatches, checked = run_differential(n_expr=300, n_assign=15, seed=1234)
    assert checked > 1000, f"too few cases actually checked ({checked})"
    assert not mismatches, f"{len(mismatches)} SMT/DuckDB disagreements, e.g. {mismatches[:3]}"


if __name__ == "__main__":
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    mismatches, checked = run_differential(n_expr=5000, n_assign=40, seed=seed)
    print(f"checked {checked} (expression, row) evaluations")
    if mismatches:
        print(f"!!! {len(mismatches)} MISMATCHES !!!")
        for m in mismatches[:20]:
            print(" ", m)
        sys.exit(1)
    print("all SMT-encoding evaluations agree with DuckDB")
