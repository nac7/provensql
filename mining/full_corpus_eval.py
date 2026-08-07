"""
Run the current provensql pipeline across the ENTIRE mined corpus (not just
the hand-labeled 213-pair sample) to answer one question with evidence:
do Stage 3's capabilities actually fire more broadly than the small sample
showed, or is the low coverage a real ceiling?

No hand labels for most of these pairs, so this does NOT compute precision
against ground truth. What it CAN establish soundly:
  - overall + per-bucket verdict distribution at full corpus scale
  - which stage produced each EQUIVALENT/DIFFERENT (by reason string), so
    we can see if Stage 3 is contributing at all beyond Stage 2
  - every EQUIVALENT verdict dumped for manual soundness spot-check
    (a false EQUIVALENT is the only truly unacceptable outcome)
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from provensql import catalog as catalog_module
from provensql.compare import compare
from provensql.verdict import VerdictType

CLASSIFIED = Path(__file__).parent / "output" / "classified.jsonl"
CATALOG = Path(__file__).parent / "output" / "udf_catalog.yml"


def stage_of(verdict) -> str:
    r = verdict.reason
    if verdict.type == VerdictType.EQUIVALENT:
        if "SMT-proved" in r:
            return "stage3_smt"
        if "canonical forms identical" in r:
            return "stage2_canonical"
        return "equivalent_other"
    if verdict.type == VerdictType.DIFFERENT:
        return "stage4_counterexample"
    if verdict.type == VerdictType.SCHEMA_CHANGE:
        return "schema_check"
    return "unknown"


def main():
    catalog = catalog_module.load(CATALOG)
    records = [json.loads(l) for l in open(CLASSIFIED, encoding="utf-8")]

    verdicts = Counter()
    stages = Counter()
    per_bucket = defaultdict(Counter)
    equivalents = []

    for rec in records:
        try:
            v = compare(rec["base"], rec["head"], catalog=catalog)
        except Exception:
            verdicts["ERROR"] += 1
            continue
        verdicts[v.type.value] += 1
        stages[stage_of(v)] += 1
        per_bucket[rec["bucket"]][v.type.value] += 1
        if v.type == VerdictType.EQUIVALENT:
            equivalents.append((rec, v))

    n = len(records)
    print(f"=== full corpus ({n} pairs) ===\n")
    print("Verdict distribution:")
    for k, c in verdicts.most_common():
        print(f"  {k:16s} {c:5d} ({100*c/n:.1f}%)")

    print("\nStage attribution:")
    for k, c in stages.most_common():
        print(f"  {k:22s} {c:5d}")

    print("\nEQUIVALENT + DIFFERENT by bucket:")
    for bucket in sorted(per_bucket):
        eq = per_bucket[bucket].get("EQUIVALENT", 0)
        diff = per_bucket[bucket].get("DIFFERENT", 0)
        sc = per_bucket[bucket].get("SCHEMA_CHANGE", 0)
        total = sum(per_bucket[bucket].values())
        print(f"  {bucket:26s} EQ={eq:3d} DIFF={diff:3d} SCHEMA={sc:3d}  / {total}")

    print(f"\n=== all {len(equivalents)} EQUIVALENT verdicts (for soundness spot-check) ===")
    stage3_eqs = [(r, v) for r, v in equivalents if "SMT-proved" in v.reason]
    print(f"(of which {len(stage3_eqs)} are Stage 3 SMT proofs)\n")
    out = Path(__file__).parent / "output" / "full_corpus_equivalents.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for rec, v in equivalents:
            f.write(json.dumps({
                "repo": rec["repo"], "path": rec["path"], "bucket": rec["bucket"],
                "stage": stage_of(v), "base": rec["base"], "head": rec["head"],
            }) + "\n")
    print(f"wrote {out} for manual review")


if __name__ == "__main__":
    main()
