"""
Tier 3: the runtime EQUIVALENT backstop.

`test_cross_validation.py` asserts, at test time, that Stage 3 and Stage 4
never contradict each other. This test covers the *shipped* enforcement of
that invariant inside compare(): when Stage 3 proves EQUIVALENT, compare()
re-runs Stage 4's counterexample search on the catalog-free path, and if
Stage 4 finds a diverging instance the EQUIVALENT is withheld -- degrading to
UNKNOWN rather than emitting a false EQUIVALENT.

To prove the backstop actually fires we inject an *unsound* Stage 3 (one that
certifies a pair that genuinely differs) and confirm compare() catches the
contradiction instead of trusting it. That is exactly the failure mode --
an encoder or solver bug producing a wrong proof -- the backstop exists for.
"""

import sys

from provensql.compare import compare
from provensql.verdict import Verdict, VerdictType

# provensql/__init__ re-exports `compare`, which shadows the submodule name, so
# `import provensql.compare` yields the function. Reach the real module object
# (where prove_equivalent lives as a global compare() looks up) via sys.modules.
compare_mod = sys.modules["provensql.compare"]


def test_backstop_downgrades_a_bogus_equivalent(monkeypatch):
    # `a > 1` vs `a > 2` genuinely differ (a = 2 diverges), so a Stage 3 that
    # returns EQUIVALENT here is unsound -- the backstop must catch it.
    monkeypatch.setattr(
        compare_mod,
        "prove_equivalent",
        lambda base, head, catalog=None: Verdict.equivalent("bogus proof (injected)"),
    )
    v = compare("SELECT x FROM t WHERE a > 1", "SELECT x FROM t WHERE a > 2")
    assert v.type == VerdictType.UNKNOWN, f"backstop should have withheld EQUIVALENT, got {v.type}"
    assert v.reason_code == "stage3_stage4_contradiction"


def test_backstop_leaves_a_genuine_equivalent_intact(monkeypatch):
    # Same injected "always EQUIVALENT" Stage 3, but on a genuinely equivalent
    # pair: Stage 4 finds no counterexample, so the backstop must NOT downgrade.
    monkeypatch.setattr(
        compare_mod,
        "prove_equivalent",
        lambda base, head, catalog=None: Verdict.equivalent("proof (injected)"),
    )
    v = compare("SELECT x FROM t WHERE a > 1", "SELECT x FROM t WHERE NOT (a <= 1)")
    assert v.type == VerdictType.EQUIVALENT


def test_backstop_does_not_disturb_real_stage3_equivalences():
    # End-to-end, no injection: a real Stage 3 proof that the backstop passes.
    v = compare(
        "SELECT x FROM t WHERE a > 1 AND b > 2",
        "SELECT x FROM t WHERE b > 2 AND a > 1",
    )
    assert v.type == VerdictType.EQUIVALENT
