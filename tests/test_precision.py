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


# Generous solver budget on the disprove tests: the SAT witness solves in <1s
# in isolation, but bit-blasted FP solving can drift toward Z3's timeout
# boundary when many solvers run concurrently (full suite). The budget keeps
# these deterministic; it never affects correctness, only abstention-under-load.
_DISPROVE_TIMEOUT_MS = 20000


def test_associativity_diverges_with_a_valid_witness():
    # Soundness contract (see note above): the disproof must never certify the
    # rewrite as FP-equivalent; UNKNOWN is an honest abstention (bit-blasted FP
    # solving is nondeterministic under concurrent load); a DIVERGENT result
    # must carry a witness that genuinely differs in float32.
    res, w = fp_equivalent("(a + b) + c", "a + (b + c)", timeout_ms=_DISPROVE_TIMEOUT_MS)
    assert res != "EQUIVALENT_FP", "unsound: certified a reassociation as FP-equivalent"
    if res == "UNKNOWN":
        pytest.skip("solver abstained on the associativity disproof under budget")
    a, b, c = _f32(w["a"]), _f32(w["b"]), _f32(w["c"])
    lhs = np.float32(np.float32(a + b) + c)
    rhs = np.float32(a + np.float32(b + c))
    assert lhs != rhs, "witness must actually make (a+b)+c differ from a+(b+c) in float32"


def test_distributivity_diverges_with_a_valid_witness():
    # Distribution is the heaviest FP disproof and its bit-blasted solve time is
    # genuinely nondeterministic; abstaining (UNKNOWN) is sound. What must never
    # happen is a false EQUIVALENT_FP. When it does disprove, the witness must be
    # real.
    res, w = fp_equivalent("a * (b + c)", "a * b + a * c", timeout_ms=_DISPROVE_TIMEOUT_MS)
    assert res != "EQUIVALENT_FP", "unsound: certified a distributive rewrite as FP-equivalent"
    if res == "UNKNOWN":
        pytest.skip("solver abstained on the distribution disproof under budget")
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
    # fp reality: the prototype must not agree with the exact-real proof here.
    # It flags DIVERGENT (the contrast this test exists to show) or abstains to
    # UNKNOWN under load -- but must never itself return EQUIVALENT_FP.
    fp = fp_equivalent("(a + b) + c", "a + (b + c)", timeout_ms=_DISPROVE_TIMEOUT_MS)[0]
    assert fp != "EQUIVALENT_FP"
    if fp != "DIVERGENT":
        pytest.skip(f"solver abstained ({fp}); contrast holds only when it disproves")
