"""
M2 tests: error-semantics equivalence (provensql/error_semantics.py).

The point of the value/NULL/ERROR lattice is that turning a runtime ERROR into
a NULL is a real, detectable behavior change -- the exact distinction the
frontier's total-arithmetic models cannot make. Over exact reals the proving
direction is tractable here (unlike the float case in M1), so EQUIVALENT_ERR is
a usable positive result too.
"""

from provensql.error_semantics import error_equivalent


def test_div_vs_safe_divide_diverges_on_error_vs_null():
    res, w = error_equivalent("a / b", "SAFE_DIVIDE(a, b)")
    assert res == "DIFFERENT", f"expected DIFFERENT, got {res}"
    # the divergence must be exactly ERROR vs NULL, at a zero divisor
    assert {w["lhs"], w["rhs"]} == {"ERROR", "NULL"}
    assert w["inputs"]["b"] == "0"


def test_nullif_guard_matches_safe_divide():
    # a / NULLIF(b, 0) returns NULL when b = 0, exactly like SAFE_DIVIDE
    res, _ = error_equivalent("a / NULLIF(b, 0)", "SAFE_DIVIDE(a, b)")
    assert res == "EQUIVALENT_ERR", f"expected EQUIVALENT_ERR, got {res}"


def test_commutativity_is_error_equivalent():
    res, _ = error_equivalent("a + b", "b + a")
    assert res == "EQUIVALENT_ERR"


def test_division_is_not_commutative():
    # a/b vs b/a differ: at a=0, b=1 -> 0 vs ERROR (1/0)
    res, w = error_equivalent("a / b", "b / a")
    assert res == "DIFFERENT"


def test_identity_is_error_equivalent():
    res, _ = error_equivalent("a / b", "a / b")
    assert res == "EQUIVALENT_ERR"


def test_string_expression_is_unknown():
    res, _ = error_equivalent("'x'", "'x'")
    assert res == "UNKNOWN"
