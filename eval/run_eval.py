"""
Score provensql's compare() against the 213-pair hand-labeled ground truth
(mining/output/labeled.jsonl).

The number that matters most is soundness: how many times did the tool say
EQUIVALENT when a human said DIFFERENT or SCHEMA_CHANGE? That number must
be zero, forever -- see provensql/verdict.py for why. Coverage (how often
the tool reaches a definitive verdict at all) is expected to be low in M1,
since Stage 3 (proof) and Stage 4 (counterexample search) aren't built yet;
every case that isn't a Stage-2 canonical match or a schema change falls
through to UNKNOWN by design.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from provensql import catalog as catalog_module
from provensql.compare import compare
from provensql.verdict import VerdictType

LABELED = Path(__file__).parent.parent / "mining" / "output" / "labeled.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", type=Path, default=None)
    args = ap.parse_args()
    catalog = catalog_module.load(args.catalog) if args.catalog else None

    records = [json.loads(l) for l in open(LABELED, encoding="utf-8")]

    confusion = Counter()  # (tool_verdict, human_label) -> count
    unsound_examples = []
    tool_verdicts = Counter()

    for rec in records:
        human_label = rec["human_label"]
        verdict = compare(rec["base"], rec["head"], catalog=catalog)
        tool_verdicts[verdict.type.value] += 1
        confusion[(verdict.type.value, human_label)] += 1

        if verdict.type == VerdictType.EQUIVALENT and human_label in ("DIFFERENT", "SCHEMA_CHANGE"):
            unsound_examples.append((rec, verdict))

    n = len(records)
    n_unknown = tool_verdicts.get("UNKNOWN", 0)
    n_decided = n - n_unknown
    n_unsound = len(unsound_examples)

    print(f"=== provensql M1 eval ({n} pairs) ===\n")

    print("Tool verdict distribution:")
    for v, c in tool_verdicts.most_common():
        print(f"  {v:16s} {c:4d}  ({100*c/n:.1f}%)")

    print(f"\nCoverage (tool reached a definitive verdict): {n_decided}/{n} = {100*n_decided/n:.1f}%")
    print(f"SOUNDNESS -- false EQUIVALENT count: {n_unsound}  {'*** FAIL ***' if n_unsound else '(clean)'}")

    print("\nConfusion matrix (tool_verdict x human_label):")
    labels = ["EQUIVALENT", "DIFFERENT", "SCHEMA_CHANGE", "UNKNOWN"]
    header = f'{"tool \\ human":16s}' + "".join(f"{l:15s}" for l in labels)
    print(header)
    for tv in labels:
        row = "".join(f"{confusion.get((tv, hl), 0):15d}" for hl in labels)
        print(f"{tv:16s}{row}")

    # accuracy among decided cases (EQUIVALENT / SCHEMA_CHANGE tool verdicts only,
    # since DIFFERENT isn't constructible yet and UNKNOWN isn't a "decision")
    n_correct_decided = confusion[("EQUIVALENT", "EQUIVALENT")] + confusion[("SCHEMA_CHANGE", "SCHEMA_CHANGE")]
    if n_decided:
        print(f"\nPrecision on decided cases: {n_correct_decided}/{n_decided} = {100*n_correct_decided/n_decided:.1f}%")

    if unsound_examples:
        print("\n!!! UNSOUND CASES (tool said EQUIVALENT, human said otherwise) !!!")
        for rec, verdict in unsound_examples:
            print(f"  {rec['repo']} {rec['path']} human={rec['human_label']} notes={rec.get('notes','')}")


if __name__ == "__main__":
    main()
