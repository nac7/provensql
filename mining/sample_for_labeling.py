"""
provensql mining harness -- Stage C: stratified sample for hand-labeling.

Takes the classified.jsonl (output of classify_pairs.py) and pulls a
stratified sample across buckets so the ~150-200 hand-labeled pairs cover
every change-type category proportionally-but-not-starved (small buckets
still get a minimum count so rare-but-important categories like
join_change aren't invisible in the labeled set).

Writes output/to_label.jsonl with an empty "human_label" field for
manual annotation. Labels to use when hand-labeling:
  EQUIVALENT       -- same result set for any database instance
  DIFFERENT        -- can construct/can point to a DB instance where results differ
  SCHEMA_CHANGE    -- output column set/order/types differ
  UNKNOWN          -- can't tell without more context (rare bucket for -- discovery
                      pass; if you're using this a lot, that's itself a finding)
"""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("classified", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target-total", type=int, default=180)
    ap.add_argument("--min-per-bucket", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--exclude-buckets", nargs="*", default=["parse_error", "formatting_only"])
    args = ap.parse_args()

    rng = random.Random(args.seed)

    by_bucket = defaultdict(list)
    with open(args.classified, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["bucket"] in args.exclude_buckets:
                continue
            by_bucket[rec["bucket"]].append(rec)

    buckets = list(by_bucket.keys())
    n_buckets = len(buckets)
    if n_buckets == 0:
        print("No non-excluded pairs found.")
        return

    # proportional allocation with a floor per bucket
    total_available = sum(len(v) for v in by_bucket.values())
    alloc = {}
    remaining = args.target_total
    for b in buckets:
        share = max(args.min_per_bucket, round(args.target_total * len(by_bucket[b]) / total_available))
        alloc[b] = min(share, len(by_bucket[b]))

    sample = []
    for b in buckets:
        rng.shuffle(by_bucket[b])
        picked = by_bucket[b][:alloc[b]]
        for rec in picked:
            rec["human_label"] = None
            rec["notes"] = ""
            sample.append(rec)

    rng.shuffle(sample)  # so labeling isn't done bucket-by-bucket (avoids anchoring bias)

    with open(args.out, "w", encoding="utf-8") as f:
        for rec in sample:
            f.write(json.dumps(rec) + "\n")

    print(f"Sampled {len(sample)} pairs across {n_buckets} buckets -> {args.out}")
    for b in buckets:
        print(f"  {b:28s} available={len(by_bucket[b]):5d}  sampled={alloc[b]:4d}")


if __name__ == "__main__":
    main()
