"""Split to_label.jsonl into size-balanced chunks for parallel agent labeling."""
import json
from pathlib import Path

SRC = Path("output/to_label.jsonl")
OUT_DIR = Path("output/chunks")
OUT_DIR.mkdir(exist_ok=True)
N_CHUNKS = 7

recs = []
with open(SRC, encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        r["_size"] = len(r["base"]) + len(r["head"])
        recs.append(r)

recs.sort(key=lambda r: -r["_size"])
buckets = [[] for _ in range(N_CHUNKS)]
bucket_sizes = [0] * N_CHUNKS

for r in recs:
    idx = bucket_sizes.index(min(bucket_sizes))
    buckets[idx].append(r)
    bucket_sizes[idx] += r["_size"]

for i, bucket in enumerate(buckets):
    path = OUT_DIR / f"chunk_{i}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in bucket:
            r.pop("_size", None)
            f.write(json.dumps(r) + "\n")
    print(f"chunk_{i}.jsonl: {len(bucket)} pairs, {bucket_sizes[i]} chars")
