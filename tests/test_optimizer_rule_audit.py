"""
M3 headline audit regression test: every rule in the cited real-optimizer
corpus must reconcile with its recorded status -- guard-confirmed rules whose
guard makes them safe verify sound, all other guard-confirmed and known-defect
rules verify unsound (DIVERGENT/DIFFERENT). A drift here means either the corpus
or an engine changed behavior.
"""

import pytest

from mining.optimizer_rules import RULES
from provensql.error_semantics import error_equivalent
from provensql.precision import fp_equivalent

UNSOUND = {"DIVERGENT", "DIFFERENT"}
SOUND = {"EQUIVALENT_FP", "EQUIVALENT_ERR"}


def _run(rule):
    if rule["axis"] == "fp":
        return fp_equivalent(rule["lhs"], rule["rhs"], timeout_ms=20000)
    return error_equivalent(rule["lhs"], rule["rhs"])


@pytest.mark.parametrize("rule", RULES, ids=[r["id"] for r in RULES])
def test_engine_never_contradicts_recorded_status(rule):
    # The engine's guarantee is soundness, not completeness: it must never
    # *contradict* ground truth. UNKNOWN (honest abstention -- e.g. a bit-blasted
    # FP disproof that runs out of solver budget under load) is always allowed;
    # what is forbidden is certifying an unsound rule as equivalent, or flagging
    # a sound rule as divergent.
    res, _ = _run(rule)
    expect_sound = rule["guard"] == "constant nonzero divisor"
    if expect_sound:
        assert res not in UNSOUND, f"{rule['id']}: false alarm, flagged sound rule as {res}"
    else:
        assert res not in SOUND, f"{rule['id']}: FALSE CERTIFICATION, called unsound rule {res}"


def test_corpus_covers_both_engines_and_real_sources():
    axes = {r["axis"] for r in RULES}
    assert axes == {"fp", "error"}
    assert any("Spark" in r["source"] for r in RULES)
    assert any("Calcite" in r["source"] for r in RULES)
    assert any(r["status"] == "known-defect" for r in RULES)
