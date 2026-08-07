"""
Three-valued-logic SQL scalar expression compiler, targeting Z3.

Every SQL scalar value is either NULL or a value of some type, and NULL
propagates through most operators (Kleene/3-valued logic: `NULL AND FALSE`
is `FALSE`, but `NULL AND TRUE` is `NULL`). Each compiled expression is
represented as an (is_null, value) pair of Z3 terms; `value` is only
meaningful when `is_null` is false, and every operator below is built to
respect that -- garbage-when-null values never leak into the final
equivalence check, only the is_null flags and value-when-not-null do.

This is intentionally a small, refusal-heavy fragment: unsupported node
shapes raise Undecidable rather than silently mis-encoding something, since
a wrong encoding here could produce a false EQUIVALENT -- the one outcome
this whole project exists to prevent. See stage3.py for how callers use
this (an SMT proof is only trusted end-to-end, never partially).
"""

from dataclasses import dataclass

import z3
from sqlglot import exp

from sqlsense import schema_infer as si


class Undecidable(Exception):
    """Raised whenever compile_expr can't confidently encode a node.
    Callers must treat this as an abstention, not an error to work around."""


@dataclass
class SqlExpr:
    is_null: "z3.BoolRef"
    value: "z3.ExprRef"
    sort: str | None  # si.NUMERIC / si.VARCHAR / si.BOOLEAN, or None for a bare NULL literal


def _dummy(sort: str):
    if sort == si.NUMERIC:
        return z3.RealVal(0)
    if sort == si.BOOLEAN:
        return z3.BoolVal(False)
    return z3.StringVal("")


def _coerce(e: SqlExpr, target_sort: str) -> SqlExpr:
    """A bare NULL literal has no sort of its own -- it unifies with whatever
    sort context requires. Anything else must already match."""
    if e.sort is None:
        return SqlExpr(is_null=z3.BoolVal(True), value=_dummy(target_sort), sort=target_sort)
    if e.sort != target_sort:
        raise Undecidable(f"sort mismatch: {e.sort} vs {target_sort}")
    return e


def build_column_vars(table_schemas: dict) -> dict[tuple[str, str], SqlExpr]:
    variables = {}
    for table, cols in table_schemas.items():
        for col, info in cols.items():
            sort = si.VARCHAR if info["type"] == si.DATE else info["type"]
            is_null = z3.Bool(f"{table}__{col}__isnull")
            if sort == si.NUMERIC:
                val = z3.Real(f"{table}__{col}__val")
            elif sort == si.BOOLEAN:
                val = z3.Bool(f"{table}__{col}__val")
            else:
                val = z3.String(f"{table}__{col}__val")
            variables[(table, col)] = SqlExpr(is_null=is_null, value=val, sort=sort)
    return variables


def _literal(node: exp.Literal) -> SqlExpr:
    if node.is_string:
        return SqlExpr(is_null=z3.BoolVal(False), value=z3.StringVal(node.this), sort=si.VARCHAR)
    text = node.this
    try:
        num = float(text) if "." in text else int(text)
    except (TypeError, ValueError):
        raise Undecidable(f"unparseable literal: {text}")
    return SqlExpr(is_null=z3.BoolVal(False), value=z3.RealVal(num), sort=si.NUMERIC)


def _cmp(op, a: SqlExpr, b: SqlExpr) -> SqlExpr:
    if a.sort is None and b.sort is None:
        raise Undecidable("comparison between two untyped NULLs")
    target = a.sort or b.sort
    a, b = _coerce(a, target), _coerce(b, target)
    return SqlExpr(is_null=z3.Or(a.is_null, b.is_null), value=op(a.value, b.value), sort=si.BOOLEAN)


def _ordered_cmp(op, a: SqlExpr, b: SqlExpr) -> SqlExpr:
    # z3 doesn't give total ordering over SeqRef/String out of the box in a
    # way we want to rely on for correctness -- restrict ordering comparisons
    # to NUMERIC, which covers the vast majority of range predicates anyway.
    target = a.sort or b.sort
    if target != si.NUMERIC:
        raise Undecidable(f"ordered comparison on non-numeric sort: {target}")
    return _cmp(op, a, b)


def _and(a: SqlExpr, b: SqlExpr) -> SqlExpr:
    if a.sort != si.BOOLEAN or b.sort != si.BOOLEAN:
        raise Undecidable("AND on non-boolean operand")
    a_false = z3.And(z3.Not(a.is_null), z3.Not(a.value))
    b_false = z3.And(z3.Not(b.is_null), z3.Not(b.value))
    short_circuit = z3.Or(a_false, b_false)
    return SqlExpr(
        is_null=z3.If(short_circuit, z3.BoolVal(False), z3.Or(a.is_null, b.is_null)),
        value=z3.If(short_circuit, z3.BoolVal(False), z3.And(a.value, b.value)),
        sort=si.BOOLEAN,
    )


def _or(a: SqlExpr, b: SqlExpr) -> SqlExpr:
    if a.sort != si.BOOLEAN or b.sort != si.BOOLEAN:
        raise Undecidable("OR on non-boolean operand")
    a_true = z3.And(z3.Not(a.is_null), a.value)
    b_true = z3.And(z3.Not(b.is_null), b.value)
    short_circuit = z3.Or(a_true, b_true)
    return SqlExpr(
        is_null=z3.If(short_circuit, z3.BoolVal(False), z3.Or(a.is_null, b.is_null)),
        value=z3.If(short_circuit, z3.BoolVal(True), z3.Or(a.value, b.value)),
        sort=si.BOOLEAN,
    )


def _not(a: SqlExpr) -> SqlExpr:
    if a.sort != si.BOOLEAN:
        raise Undecidable("NOT on non-boolean operand")
    return SqlExpr(is_null=a.is_null, value=z3.Not(a.value), sort=si.BOOLEAN)


def _arith(op, a: SqlExpr, b: SqlExpr) -> SqlExpr:
    if a.sort != si.NUMERIC or b.sort != si.NUMERIC:
        raise Undecidable("arithmetic on non-numeric operand")
    return SqlExpr(is_null=z3.Or(a.is_null, b.is_null), value=op(a.value, b.value), sort=si.NUMERIC)


def _opaque_atom(node: exp.Expression, opaque: dict) -> SqlExpr:
    """Aggregates (COUNT/SUM/...) aren't modeled -- but HAVING clauses very
    commonly reorder/reuse the exact same aggregate expression, so treating
    each distinct one as an opaque symbolic value (keyed by its own
    canonical SQL text, shared between the base and head compile calls)
    proves that case without needing to know what the aggregate computes.
    If base and head use textually different aggregate expressions, they
    get different unconstrained variables and the proof correctly fails to
    show equivalence rather than guessing.

    This is only sound because Stage 0 already refuses any query containing
    a known nondeterministic function (see canonicalize.NONDETERMINISTIC_FUNCS)
    -- otherwise "same text -> same value" wouldn't hold even within one
    query, let alone across base and head."""
    key = node.sql(dialect="bigquery", normalize=True)
    if key not in opaque:
        idx = len(opaque)
        opaque[key] = SqlExpr(
            is_null=z3.Bool(f"__opaque_{idx}__isnull"),
            value=z3.Real(f"__opaque_{idx}__val"),
            sort=si.NUMERIC,
        )
    return opaque[key]


def compile_expr(node: exp.Expression, column_vars: dict, opaque: dict) -> SqlExpr:
    if isinstance(node, exp.Paren):
        return compile_expr(node.this, column_vars, opaque)

    if isinstance(node, exp.Column):
        key = (node.table, node.name)
        if key not in column_vars:
            raise Undecidable(f"unresolved column: {key}")
        return column_vars[key]

    if isinstance(node, exp.AggFunc):
        return _opaque_atom(node, opaque)

    if isinstance(node, exp.Null):
        return SqlExpr(is_null=z3.BoolVal(True), value=None, sort=None)

    if isinstance(node, exp.Boolean):
        return SqlExpr(is_null=z3.BoolVal(False), value=z3.BoolVal(bool(node.this)), sort=si.BOOLEAN)

    if isinstance(node, exp.Literal):
        return _literal(node)

    if isinstance(node, exp.Is):
        left = compile_expr(node.this, column_vars, opaque)
        is_null_check = isinstance(node.expression, exp.Null)
        if not is_null_check:
            raise Undecidable("IS <non-null-literal> not supported")
        return SqlExpr(is_null=z3.BoolVal(False), value=left.is_null, sort=si.BOOLEAN)

    if isinstance(node, exp.Not):
        return _not(compile_expr(node.this, column_vars, opaque))

    if isinstance(node, exp.And):
        return _and(compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))

    if isinstance(node, exp.Or):
        return _or(compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))

    if isinstance(node, exp.EQ):
        return _cmp(lambda a, b: a == b, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))
    if isinstance(node, exp.NEQ):
        return _not(_cmp(lambda a, b: a == b, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque)))
    if isinstance(node, exp.GT):
        return _ordered_cmp(lambda a, b: a > b, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))
    if isinstance(node, exp.GTE):
        return _ordered_cmp(lambda a, b: a >= b, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))
    if isinstance(node, exp.LT):
        return _ordered_cmp(lambda a, b: a < b, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))
    if isinstance(node, exp.LTE):
        return _ordered_cmp(lambda a, b: a <= b, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))

    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        op = {exp.Add: lambda a, b: a + b, exp.Sub: lambda a, b: a - b,
              exp.Mul: lambda a, b: a * b, exp.Div: lambda a, b: a / b}[type(node)]
        return _arith(op, compile_expr(node.this, column_vars, opaque), compile_expr(node.expression, column_vars, opaque))

    if isinstance(node, exp.Between):
        x = compile_expr(node.this, column_vars, opaque)
        low = compile_expr(node.args["low"], column_vars, opaque)
        high = compile_expr(node.args["high"], column_vars, opaque)
        return _and(
            _ordered_cmp(lambda a, b: a >= b, x, low),
            _ordered_cmp(lambda a, b: a <= b, x, high),
        )

    if isinstance(node, exp.In):
        candidates = node.expressions
        if not candidates or not all(isinstance(c, exp.Literal) for c in candidates):
            raise Undecidable("IN with a non-literal list (e.g. subquery)")
        x = compile_expr(node.this, column_vars, opaque)
        result = _cmp(lambda a, b: a == b, x, compile_expr(candidates[0], column_vars, opaque))
        for c in candidates[1:]:
            result = _or(result, _cmp(lambda a, b: a == b, x, compile_expr(c, column_vars, opaque)))
        return result

    if isinstance(node, exp.Case):
        ifs = node.args.get("ifs") or []
        if not ifs:
            raise Undecidable("CASE with no branches")
        branches = [compile_expr(i.args["true"], column_vars, opaque) for i in ifs]
        sort = next((b.sort for b in branches if b.sort is not None), None)
        if sort is None:
            raise Undecidable("CASE where every branch is a bare NULL")
        branches = [_coerce(b, sort) for b in branches]
        default_node = node.args.get("default")
        default = _coerce(compile_expr(default_node, column_vars, opaque), sort) if default_node is not None else \
            SqlExpr(is_null=z3.BoolVal(True), value=_dummy(sort), sort=sort)

        result = default
        for if_, branch in zip(reversed(ifs), reversed(branches)):
            cond = compile_expr(if_.this, column_vars, opaque)
            if cond.sort != si.BOOLEAN:
                raise Undecidable("CASE WHEN condition is not boolean")
            cond_true = z3.And(z3.Not(cond.is_null), cond.value)
            result = SqlExpr(
                is_null=z3.If(cond_true, branch.is_null, result.is_null),
                value=z3.If(cond_true, branch.value, result.value),
                sort=sort,
            )
        return result

    if isinstance(node, exp.Coalesce):
        parts = [compile_expr(node.this, column_vars, opaque)] + [compile_expr(e, column_vars, opaque) for e in node.expressions]
        sort = next((p.sort for p in parts if p.sort is not None), None)
        if sort is None:
            raise Undecidable("COALESCE where every argument is a bare NULL")
        parts = [_coerce(p, sort) for p in parts]
        result = parts[-1]
        for p in reversed(parts[:-1]):
            result = SqlExpr(
                is_null=z3.And(p.is_null, result.is_null),
                value=z3.If(z3.Not(p.is_null), p.value, result.value),
                sort=sort,
            )
        return result

    raise Undecidable(f"unsupported node type: {type(node).__name__}")


def expressions_equivalent(base_node: exp.Expression, head_node: exp.Expression, column_vars: dict) -> bool:
    """True only if Z3 proves the two expressions always agree (same value,
    or both NULL) for every possible assignment. False covers both "found a
    counterexample" and "gave up (Undecidable/timeout)" -- Stage 3 only ever
    acts on the True case, so collapsing those is safe."""
    # Fast path: structurally identical expressions are trivially equivalent.
    # This is sound because Stage 0 already rejects any query containing a
    # nondeterministic function, so "same text" really does mean "same value"
    # here. It matters a lot in practice: it lets Stage 3 succeed when the
    # *changed* part of a query is in the SMT fragment even though some
    # *unchanged* expression uses a function the encoder doesn't model
    # (TIMESTAMP_DIFF, DATE_TRUNC, ...) -- without it, one unsupported
    # function anywhere in the SELECT list blocks the whole proof.
    if base_node.sql(dialect="bigquery", normalize=True) == head_node.sql(dialect="bigquery", normalize=True):
        return True

    opaque: dict = {}
    try:
        b = compile_expr(base_node, column_vars, opaque)
        h = compile_expr(head_node, column_vars, opaque)
    except Undecidable:
        return False

    if b.sort is None or h.sort is None:
        target = b.sort or h.sort
        if target is None:
            return False  # both sides are bare NULL literals -- trivially equal, but not interesting
        b, h = _coerce(b, target), _coerce(h, target)
    if b.sort != h.sort:
        return False

    solver = z3.Solver()
    solver.set("timeout", 2000)
    same = z3.And(b.is_null == h.is_null, z3.Or(b.is_null, b.value == h.value))
    solver.add(z3.Not(same))
    try:
        return solver.check() == z3.unsat
    except z3.Z3Exception:
        return False
