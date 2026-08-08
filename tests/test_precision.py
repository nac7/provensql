"""
M1 tests for precision-aware equivalence (provensql/precision.py).

The sound, valuable direction is *disproving* float-equivalence: a DIVERGENT
result must come with a witness on which the two expressions genuinely differ
under IEEE-754. We validate that here the same way the main tool validates its
Stage-4 witnesses -- by re-executing at the witness, using numpy.float32 to
match the modeled Float32 semantics. We also check the disprove direction is
sound: a genuinely FP-equivalent rewrite (commutativity) is never reported
DIVERGENT.
"""

import numpy as np
import pytest

from provensql.precision import fp_equivalent


def _f32(s):
    # z3 renders a Float32 value as e.g. '-1.984...*(2**32)', which is valid
    # Python arithmetic; the value is exactly representable in float32, so
    # round-tripping through float64 and back is exact.
    return np.float32(eval(str(s)))


def test_associativity_diverges_with_a_valid_witness():
    res, w = fp_equivalent("(a + b) + c", "a + (b + c)")
    assert res == "DIVERGENT", f"expected DIVERGENT, got {res}"
    a, b, c = _f32(w["a"]), _f32(w["b"]), _f32(w["c"])
    lhs = np.float32(np.float32(a + b) + c)
    rhs = np.float32(a + np.float32(b + c))
    assert lhs != rhs, "witness must actually make (a+b)+c differ from a+(b+c) in float32"


def test_distributivity_diverges_with_a_valid_witness():
    res, w = fp_equivalent("a * (b + c)", "a * b + a * c")
    assert res == "DIVERGENT", f"expected DIVERGENT, got {res}"
    a, b, c = _f32(w["a"]), _f32(w["b"]), _f32(w["c"])
    lhs = np.float32(a * np.float32(b + c))
    rhs = np.float32(np.float32(a * b) + np.float32(a * c))
    assert lhs != rhs


def test_multiply_by_one_is_fp_equivalent():
    res, _ = fp_equivalent("a * 1.0", "a")
    assert res == "EQUIVALENT_FP", f"expected EQUIVALENT_FP, got {res}"


def test_commutativity_is_never_reported_divergent():
    # a + b == b + a holds in IEEE-754, so the disprove direction must NOT
    # claim DIVERGENT -- it may prove EQUIVALENT_FP or (if the solver can't
    # prove UNSAT in time) abstain to UNKNOWN, but never DIVERGENT.
    res, _ = fp_equivalent("a + b", "b + a", timeout_ms=2000)
    assert res in ("EQUIVALENT_FP", "UNKNOWN"), f"unsound: reported {res} for a commutative rewrite"


def test_non_arithmetic_is_unknown():
    res, _ = fp_equivalent("a > 1", "1 < a")
    assert res == "UNKNOWN"


def test_precision_catches_what_exact_real_stage3_misses():
    # The headline of the precision direction: the shipped Stage 3 models
    # numerics as exact reals and proves reassociation EQUIVALENT (a caveat it
    # prints); the precision prototype flags the same rewrite DIVERGENT under
    # float. This is the gap M1 exists to close.
    from provensql.compare import compare
    from provensql.verdict import VerdictType

    base = "SELECT (a + b) + c AS x FROM t"
    head = "SELECT a + (b + c) AS x FROM t"
    assert compare(base, head).type == VerdictType.EQUIVALENT  # exact-real proof
    assert fp_equivalent("(a + b) + c", "a + (b + c)")[0] == "DIVERGENT"  # fp reality
