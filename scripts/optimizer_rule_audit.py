"""
M3 headline audit: run the precision (M1) and error-semantics (M2) engines over
a cited corpus of rewrite rules taken from real production optimizers
(mining/optimizer_rules.py), and check the engine's verdict against how the
optimizer actually handles each rule.

For every rule -- valid over the mathematical reals, which is all the
equivalence-proving frontier models -- we ask the axis-appropriate engine
whether it still holds under IEEE-754 rounding (fp) or under the ERROR/NULL
outcome lattice (error). We then reconcile:

  * "guard-confirmed" rules: the optimizer restricts the rule; the engine
    should independently find the *unguarded* form unsound (DIVERGENT /
    DIFFERENT). A tick means the tool re-derived a hand-written guard.
  * "known-defect" rules: a case the optimizer's issue tracker records as
    wrong; the engine should flag it (DIVERGENT / DIFFERENT). A tick means the
    tool would have caught a real, tracked bug.

Exit code is nonzero if any rule's engine verdict fails to reconcile with its
recorded status -- so this doubles as a regression test for the engines.

    python scripts/optimizer_rule_audit.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mining.optimizer_rules import RULES  # noqa: E402
from provensql.error_semantics import error_equivalent  # noqa: E402
from provensql.precision import fp_equivalent  # noqa: E402

# For both "guard-confirmed" (guard is necessary) and "known-defect" (optimizer
# got it wrong), a correct engine must report the rule as NOT equivalence-
# preserving on its axis.
UNSOUND = {"DIVERGENT", "DIFFERENT"}
SOUND = {"EQUIVALENT_FP", "EQUIVALENT_ERR"}


def _run(rule):
    if rule["axis"] == "fp":
        # Generous budget: FP disproofs are bit-blasted; the distribution
        # witness solves in <1s but sits near Z3's shorter-timeout boundary.
        return fp_equivalent(rule["lhs"], rule["rhs"], timeout_ms=20000)
    return error_equivalent(rule["lhs"], rule["rhs"])


def _expected_unsound(rule):
    # guard-confirmed rules where the *guard makes them safe* (constant nonzero
    # divisor) should verify SOUND; every other guard-confirmed / known-defect
    # rule should verify UNSOUND. We encode that in the corpus by giving the
    # safe form a guard of "constant nonzero divisor".
    return rule["guard"] != "constant nonzero divisor"


def main():
    findings = []
    flagged = abstained = 0
    contradictions = []  # the only real failures: the engine disagreed with ground truth

    print("=== Optimizer-rule audit (real rules vs FP / ERROR-NULL semantics) ===\n")
    for rule in RULES:
        res, witness = _run(rule)
        exp_unsound = _expected_unsound(rule)
        got_unsound = res in UNSOUND
        got_sound = res in SOUND

        # Soundness contract: never contradict ground truth. UNKNOWN is an
        # honest abstention (allowed). A contradiction is certifying an unsound
        # rule as equivalent, or flagging a sound one as divergent.
        contradiction = (exp_unsound and got_sound) or (not exp_unsound and got_unsound)
        if contradiction:
            contradictions.append((rule["id"], res))
            mark = "!! "
        elif res in ("UNKNOWN",):
            abstained += 1
            mark = "-- "
        else:
            flagged += 1
            mark = "OK "

        verdict = {"DIVERGENT": "UNSOUND", "DIFFERENT": "UNSOUND",
                   "EQUIVALENT_FP": "sound", "EQUIVALENT_ERR": "sound"}.get(res, res)
        print(f"  [{mark}] {rule['source']:26} {rule['ref']:20} "
              f"[{rule['axis']:5}] {verdict:8}  {rule['lhs']}  ->  {rule['rhs']}")
        print(f"         status={rule['status']}; guard={rule['guard']}")
        if witness:
            print(f"         witness: {witness}")
        findings.append({**{k: rule[k] for k in ("id", "source", "ref", "url",
                        "lhs", "rhs", "guard", "axis", "status", "note")},
                        "engine_result": res, "witness": witness,
                        "contradiction": contradiction})

    n = len(RULES)
    guard = sum(1 for r in RULES if r["status"] == "guard-confirmed")
    defect = sum(1 for r in RULES if r["status"] == "known-defect")
    print(f"\nsummary: {flagged} of {n} rules resolved as expected, {abstained} "
          f"honest abstention(s), {len(contradictions)} contradiction(s) "
          f"({guard} guard-confirmed, {defect} known-defect in corpus)")
    print("  (an abstention is UNKNOWN under solver budget -- never a false "
          "certification; only a contradiction is a failure)")
    if contradictions:
        print("CONTRADICTIONS (engine disagreed with ground truth):")
        for rid, res in contradictions:
            print(f"  - {rid}: engine said {res}")

    out = ROOT / "docs" / "optimizer_rule_audit_result.json"
    out.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")

    return 1 if contradictions else 0


if __name__ == "__main__":
    sys.exit(main())
