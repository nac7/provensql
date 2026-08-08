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


def test_is_null_over_divzero_is_not_false():
    # CALCITE-7145: RexSimplify folds IS NULL(10/0) to false; a /0 raises (or is
    # NULL), never a definite false. Our lattice reports ERROR vs false.
    res, w = error_equivalent("(10/0) IS NULL", "FALSE")
    assert res == "DIFFERENT", f"expected DIFFERENT, got {res}"
    assert w["lhs"] == "ERROR" and w["rhs"] == "0"


def test_is_not_null_over_divzero_is_not_true():
    res, w = error_equivalent("(10/0) IS NOT NULL", "TRUE")
    assert res == "DIFFERENT"
    assert w["lhs"] == "ERROR" and w["rhs"] == "1"


def test_null_plus_safe_division_folds_to_null():
    # CALCITE-7295: null + a/4 -> null is sound; /4 can never raise.
    res, _ = error_equivalent("NULL + a/4", "NULL")
    assert res == "EQUIVALENT_ERR"


def test_null_plus_unsafe_division_does_not_fold():
    # ...but null + a/b -> null drops a division-by-zero ERROR at b=0.
    res, w = error_equivalent("NULL + a/b", "NULL")
    assert res == "DIFFERENT"
    assert w["lhs"] == "ERROR" and w["rhs"] == "NULL"
    assert w["inputs"]["b"] == "0"
