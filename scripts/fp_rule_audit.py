"""
M3: audit common algebraic SQL rewrite rules for soundness under real numeric
semantics -- the kind of scalar simplifications query optimizers and humans
apply freely. Each rule is a rewrite that is valid over the mathematical reals;
we ask two separate questions the frontier's exact-real models never do:

  * FP audit (precision.py): is it still valid under IEEE-754 rounding?
  * ERROR audit (error_semantics.py): does it preserve the ERROR/NULL/value
    outcome?

A rule that is real-valid but FP- or error-INVALID is a latent bug for any
engine/optimizer that applies it to floating-point columns or across a possible
runtime error. `DIVERGENT`/`DIFFERENT` results carry a concrete witness.

This audits a curated rule set; ingesting a full optimizer rule base
(Calcite RexSimplify, Spark, WeTune) is the same procedure at scale -- see
docs/precision_research_scope.md (M3).

    python scripts/fp_rule_audit.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from provensql.error_semantics import error_equivalent  # noqa: E402
from provensql.precision import fp_equivalent  # noqa: E402

# (name, lhs, rhs): each is real-valid; we test FP validity.
FP_RULES = [
    ("add reassociation", "(a + b) + c", "a + (b + c)"),
    ("mul reassociation", "(a * b) * c", "a * (b * c)"),
    ("distribute mul over add", "a * (b + c)", "a * b + a * c"),
    ("factor common term", "a * b + a * c", "a * (b + c)"),
    ("cancel add then sub", "(a + b) - b", "a"),
    ("cancel mul then div", "(a * b) / b", "a"),
    ("chain div to mul", "a / b / c", "a / (b * c)"),
    ("reorder mul/div", "a * c / b", "a / b * c"),
    ("double negation", "-(-a)", "a"),
    ("add zero identity", "a + 0.0", "a"),
    ("mul one identity", "a * 1.0", "a"),
    ("self add is times two", "a + a", "a * 2.0"),
]

# (name, lhs, rhs): tested for ERROR/NULL outcome preservation.
ERR_RULES = [
    ("div vs SAFE_DIVIDE", "a / b", "SAFE_DIVIDE(a, b)"),
    ("nullif-guarded div vs SAFE_DIVIDE", "a / NULLIF(b, 0)", "SAFE_DIVIDE(a, b)"),
    ("chain div to mul (errors)", "a / b / c", "a / (b * c)"),
    ("cancel add then sub (errors)", "(a + b) - b", "a"),
    ("mul then div (errors)", "(a * b) / b", "a"),
]

FP_LABEL = {"DIVERGENT": "FP-UNSOUND", "EQUIVALENT_FP": "fp-safe", "UNKNOWN": "unknown"}
ERR_LABEL = {"DIFFERENT": "ERROR-UNSOUND", "EQUIVALENT_ERR": "error-safe", "UNKNOWN": "unknown"}


def main():
    findings = {"fp": [], "error": []}

    print("=== FP audit (valid over reals -> valid under IEEE-754?) ===\n")
    for name, lhs, rhs in FP_RULES:
        res, w = fp_equivalent(lhs, rhs, timeout_ms=8000)
        label = FP_LABEL.get(res, res)
        print(f"  [{label:11}] {name}:  {lhs}  ==  {rhs}")
        if res == "DIVERGENT":
            print(f"                witness: {w}")
        findings["fp"].append({"rule": name, "lhs": lhs, "rhs": rhs, "result": res, "witness": w})

    print("\n=== ERROR audit (preserves ERROR/NULL/value outcome?) ===\n")
    for name, lhs, rhs in ERR_RULES:
        res, w = error_equivalent(lhs, rhs)
        label = ERR_LABEL.get(res, res)
        print(f"  [{label:13}] {name}:  {lhs}  ==  {rhs}")
        if res == "DIFFERENT":
            print(f"                witness: {w}")
        findings["error"].append({"rule": name, "lhs": lhs, "rhs": rhs, "result": res, "witness": w})

    fp_unsound = sum(1 for f in findings["fp"] if f["result"] == "DIVERGENT")
    err_unsound = sum(1 for f in findings["error"] if f["result"] == "DIFFERENT")
    print(f"\nsummary: {fp_unsound}/{len(FP_RULES)} rules FP-UNSOUND, "
          f"{err_unsound}/{len(ERR_RULES)} rules ERROR-UNSOUND")

    out = Path(__file__).resolve().parent.parent / "docs" / "fp_rule_audit_result.json"
    out.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
