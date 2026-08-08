"""
M2 of precision-aware equivalence (research prototype).

Models a scalar expression's outcome as a three-way lattice -- ERROR, NULL, or
a value -- so runtime errors become first-class. Two expressions are
error-equivalent only if, on every input, they produce the *same outcome*: an
edit that turns a division-by-zero ERROR into a NULL (the classic
`a/b` -> `SAFE_DIVIDE(a,b)` refactor) is therefore DIFFERENT, not equivalent.

The frontier (QED, VeriEQL, ...) models arithmetic as total and cannot see
this distinction; provensql's shipped Stage 3 abstains on division for exactly
this reason. This prototype makes the outcome observable.

Scope/soundness: STANDALONE (does not touch compare()/Stage 3). Values are
modeled over exact reals -- this milestone is about ERROR/NULL *outcomes*, not
floating-point rounding (that is M1, precision.py). A DIFFERENT result carries
a concrete witness; EQUIVALENT_ERR means equal outcomes under this model.
See docs/precision_research_scope.md.
"""

import z3
from sqlglot import exp, parse_one

DIALECT = "bigquery"


class NotModeled(Exception):
    """Expression uses something outside the modeled fragment."""


class Outcome:
    """(err, null, val): booleans for ERROR / NULL, and a real value that is
    only meaningful when neither flag holds."""

    def __init__(self, err, null, val):
        self.err, self.null, self.val = err, null, val


def _parse_scalar(sql_expr: str) -> exp.Expression:
    node = parse_one(f"SELECT {sql_expr}", dialect=DIALECT).selects[0]
    return node.this if isinstance(node, exp.Alias) else node


def _propagate(a: Outcome, b: Outcome, val):
    """Standard 2-ary propagation: ERROR dominates, then NULL, else the value.
    Used by +, -, * (which introduce no new errors)."""
    err = z3.Or(a.err, b.err)
    null = z3.And(z3.Not(err), z3.Or(a.null, b.null))
    return Outcome(err, null, val)


def _func_name(node) -> str:
    if isinstance(node, exp.Anonymous):
        return (node.name or "").upper()
    return type(node).__name__.upper()


def compile_outcome(node: exp.Expression, variables: dict) -> Outcome:
    if isinstance(node, exp.Paren):
        return compile_outcome(node.this, variables)
    if isinstance(node, exp.Column):
        name = node.name
        if name not in variables:
            # Nullability convention: a column named `notNull...` is NOT NULL, so
            # its null flag is fixed false. This mirrors Calcite's own test column
            # naming (vIntNotNull -> notNullInt0, dumped as ?0.notNullInt0), which
            # lets the rule-ingestion pilot audit nullability-sensitive rules
            # faithfully. Ordinary columns stay possibly-null.
            null = z3.BoolVal(False) if name.startswith("notNull") else z3.Bool(f"{name}__null")
            variables[name] = Outcome(z3.BoolVal(False), null, z3.Real(name))
        return variables[name]
    if isinstance(node, exp.Null):
        return Outcome(z3.BoolVal(False), z3.BoolVal(True), z3.RealVal(0))
    if isinstance(node, exp.Boolean):
        return Outcome(z3.BoolVal(False), z3.BoolVal(False),
                       z3.RealVal(1) if node.this else z3.RealVal(0))
    if isinstance(node, exp.Literal):
        if node.is_string:
            raise NotModeled("string literal")
        return Outcome(z3.BoolVal(False), z3.BoolVal(False), z3.RealVal(float(node.this)))
    if isinstance(node, exp.Neg):
        x = compile_outcome(node.this, variables)
        return Outcome(x.err, x.null, -x.val)
    if isinstance(node, exp.Cast):
        # Passthrough: we model the outcome (ERROR/NULL/value), and a cast that
        # neither overflows nor fails preserves it. Overflow/CAST-failure as a
        # first-class ERROR is a named engine extension (see rule_ingestion_scope
        # docs); until then a cast is transparent to the outcome lattice.
        return compile_outcome(node.this, variables)

    if isinstance(node, (exp.Add, exp.Sub, exp.Mul)):
        a = compile_outcome(node.this, variables)
        b = compile_outcome(node.expression, variables)
        if isinstance(node, exp.Add):
            v = a.val + b.val
        elif isinstance(node, exp.Sub):
            v = a.val - b.val
        else:
            v = a.val * b.val
        return _propagate(a, b, v)

    if isinstance(node, exp.Div):
        a = compile_outcome(node.this, variables)
        b = compile_outcome(node.expression, variables)
        divzero = z3.And(z3.Not(a.err), z3.Not(b.err), z3.Not(a.null), z3.Not(b.null), b.val == 0)
        err = z3.Or(a.err, b.err, divzero)
        null = z3.And(z3.Not(err), z3.Or(a.null, b.null))
        return Outcome(err, null, a.val / b.val)

    if isinstance(node, exp.Nullif):
        x = compile_outcome(node.this, variables)
        y = compile_outcome(node.expression, variables)
        err = z3.Or(x.err, y.err)
        eq_true = z3.And(z3.Not(x.null), z3.Not(y.null), x.val == y.val)
        null = z3.And(z3.Not(err), z3.Or(x.null, eq_true))
        return Outcome(err, null, x.val)

    if isinstance(node, exp.SafeDivide):
        a = compile_outcome(node.this, variables)
        b = compile_outcome(node.expression, variables)
        err = z3.Or(a.err, b.err)  # SAFE_DIVIDE returns NULL on /0, never raises
        null = z3.And(z3.Not(err), z3.Or(a.null, b.null, b.val == 0))
        return Outcome(err, null, a.val / b.val)

    if isinstance(node, exp.Is):
        # IS NULL: a runtime ERROR in the operand still propagates (engines that
        # raise on the inner expression never reach the null test); otherwise the
        # result is the boolean 1/0 of "operand is NULL". This is the exact shape
        # of CALCITE-7145, where RexSimplify wrongly folds IS NULL(10/0) to false.
        if isinstance(node.expression, exp.Null):
            x = compile_outcome(node.this, variables)
            return Outcome(x.err, z3.BoolVal(False), z3.If(x.null, z3.RealVal(1), z3.RealVal(0)))
        raise NotModeled("IS <non-null predicate>")
    if isinstance(node, exp.Not):
        # boolean negation over the 0/1 lattice; ERROR and NULL propagate.
        x = compile_outcome(node.this, variables)
        return Outcome(x.err, x.null, z3.RealVal(1) - x.val)

    raise NotModeled(_func_name(node))


def _same_outcome(a: Outcome, b: Outcome):
    return z3.And(
        a.err == b.err,
        z3.Implies(z3.Not(a.err), a.null == b.null),
        z3.Implies(z3.And(z3.Not(a.err), z3.Not(a.null)), a.val == b.val),
    )


def _describe(o: Outcome, m) -> str:
    if z3.is_true(m.eval(o.err, model_completion=True)):
        return "ERROR"
    if z3.is_true(m.eval(o.null, model_completion=True)):
        return "NULL"
    return str(m.eval(o.val, model_completion=True))


def error_equivalent(sql_a: str, sql_b: str):
    """Returns (result, witness): 'EQUIVALENT_ERR', 'DIFFERENT', or 'UNKNOWN'.
    For 'DIFFERENT', witness maps each column to its value/NULL and reports the
    two diverging outcomes."""
    variables: dict = {}
    try:
        a = compile_outcome(_parse_scalar(sql_a), variables)
        b = compile_outcome(_parse_scalar(sql_b), variables)
    except NotModeled:
        return ("UNKNOWN", None)

    s = z3.Solver()
    s.add(z3.Not(_same_outcome(a, b)))
    r = s.check()
    if r == z3.unsat:
        return ("EQUIVALENT_ERR", None)
    if r != z3.sat:
        return ("UNKNOWN", None)

    m = s.model()
    inputs = {}
    for name, o in variables.items():
        inputs[name] = "NULL" if z3.is_true(m.eval(o.null, model_completion=True)) \
            else str(m.eval(o.val, model_completion=True))
    return ("DIFFERENT", {"inputs": inputs, "lhs": _describe(a, m), "rhs": _describe(b, m)})


if __name__ == "__main__":
    trials = [
        ("a / b", "SAFE_DIVIDE(a, b)"),            # DIFFERENT: ERROR vs NULL at b=0
        ("a / NULLIF(b, 0)", "SAFE_DIVIDE(a, b)"), # EQUIVALENT_ERR: both NULL at b=0
        ("a + b", "b + a"),                         # EQUIVALENT_ERR
        ("a / b", "a / b"),                         # EQUIVALENT_ERR
    ]
    for x, y in trials:
        res, w = error_equivalent(x, y)
        print(f"{res:15} {x!r} vs {y!r}" + (f"   {w}" if w else ""), flush=True)
