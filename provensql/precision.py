"""
M1 of precision-aware equivalence (research prototype).

Decides whether two scalar SQL arithmetic expressions are equivalent under
IEEE-754 double semantics -- catching rewrites (reassociation, distribution)
that hold over the reals but DIVERGE under floating point. The shipped Stage 3
models numerics as exact reals and would wrongly accept such rewrites for FLOAT
columns (a caveat it prints); this module is the prototype that will eventually
let smt.py reason about the real numeric semantics instead.

Scope and soundness (read before relying on it):
  - This is STANDALONE. It does not touch compare()/Stage 3; the shipped
    soundness guarantee is unchanged.
  - The valuable, sound direction is DISPROVING: a `DIVERGENT` result comes
    with a concrete assignment on which the two expressions differ under
    Float64 round-to-nearest-even -- a genuine counterexample.
  - `EQUIVALENT_FP` means "equal under the modeled Float64/RNE semantics for
    finite inputs," NOT a general equivalence claim. It is a prototype result,
    not a shipped verdict.

See docs/precision_research_scope.md for the full plan (typed decimal model,
value/NULL/ERROR lattice, optimizer-rule audit) that M2+ builds on.
"""

import z3
from sqlglot import exp, parse_one

DIALECT = "bigquery"
# Default to Float32 for tractability: SMT over IEEE-754 is bit-blasted, and
# 64-bit multiplication is often intractable, whereas 32-bit keeps the
# associativity/distributivity disproofs fast. The rounding phenomena this
# module detects occur identically in both widths. Pass sort=z3.Float64() for
# double, guarded by the solver timeout below.
FSORT = z3.Float32()
RM = z3.RNE()
DEFAULT_TIMEOUT_MS = 5000


class NotArithmetic(Exception):
    """The expression uses something outside the modeled scalar-arithmetic
    fragment (strings, functions, comparisons, ...)."""


def _finite(x):
    return z3.And(z3.Not(z3.fpIsNaN(x)), z3.Not(z3.fpIsInf(x)))


def _parse_scalar(sql_expr: str) -> exp.Expression:
    tree = parse_one(f"SELECT {sql_expr}", dialect=DIALECT)
    node = tree.selects[0]
    return node.this if isinstance(node, exp.Alias) else node


_BINARY = {
    exp.Add: z3.fpAdd,
    exp.Sub: z3.fpSub,
    exp.Mul: z3.fpMul,
    exp.Div: z3.fpDiv,
}


def compile_fp(node: exp.Expression, variables: dict, sort=FSORT):
    """Compile a scalar arithmetic expression to a Z3 floating-point term,
    allocating one FP variable per column name (shared across both sides)."""
    if isinstance(node, exp.Paren):
        return compile_fp(node.this, variables, sort)
    if isinstance(node, exp.Column):
        name = node.name
        if name not in variables:
            variables[name] = z3.FP(name, sort)
        return variables[name]
    if isinstance(node, exp.Literal):
        if node.is_string:
            raise NotArithmetic("string literal")
        return z3.FPVal(float(node.this), sort)
    if isinstance(node, exp.Neg):
        return z3.fpNeg(compile_fp(node.this, variables, sort))
    for cls, op in _BINARY.items():
        if isinstance(node, cls):
            return op(RM, compile_fp(node.this, variables, sort), compile_fp(node.expression, variables, sort))
    raise NotArithmetic(type(node).__name__)


def _same(a, b):
    # equivalence under value semantics: equal, or both NaN (fpEQ treats
    # +0.0 == -0.0, which matches SQL value comparison).
    return z3.Or(z3.fpEQ(a, b), z3.And(z3.fpIsNaN(a), z3.fpIsNaN(b)))


def fp_equivalent(sql_a: str, sql_b: str, sort=FSORT, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                  assume_finite_inputs: bool = True):
    """Returns (result, witness) where result is 'EQUIVALENT_FP', 'DIVERGENT',
    or 'UNKNOWN'. For 'DIVERGENT', witness is a dict {column: value} on which the
    two expressions produce different floating-point results. A solver timeout
    yields 'UNKNOWN' -- honest abstention, never a false claim either way."""
    variables: dict = {}
    try:
        a = compile_fp(_parse_scalar(sql_a), variables, sort)
        b = compile_fp(_parse_scalar(sql_b), variables, sort)
    except NotArithmetic:
        return ("UNKNOWN", None)

    s = z3.Solver()
    s.set("timeout", timeout_ms)
    if assume_finite_inputs:
        for v in variables.values():
            s.add(_finite(v))
    s.add(z3.Not(_same(a, b)))

    r = s.check()
    if r == z3.unsat:
        return ("EQUIVALENT_FP", None)
    if r != z3.sat:
        return ("UNKNOWN", None)  # z3 'unknown' (incl. timeout) -> abstain

    m = s.model()
    witness = {}
    for name, v in variables.items():
        val = m.eval(v, model_completion=True)
        try:
            witness[name] = float(val.as_string()) if hasattr(val, "as_string") else str(val)
        except (ValueError, AttributeError):
            witness[name] = str(val)
    return ("DIVERGENT", witness)


if __name__ == "__main__":
    trials = [
        ("(a + b) + c", "a + (b + c)"),   # associativity: DIVERGENT
        ("a * (b + c)", "a * b + a * c"), # distributivity: DIVERGENT
        ("a + b", "b + a"),               # commutativity: EQUIVALENT_FP
        ("a * 1.0", "a"),                 # identity: EQUIVALENT_FP
    ]
    for x, y in trials:
        res, w = fp_equivalent(x, y)
        print(f"{res:14} {x!r} vs {y!r}" + (f"   witness={w}" if w else ""), flush=True)
