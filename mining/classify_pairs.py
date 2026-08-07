"""
provensql mining harness -- Stage B: parse + auto-bucket (base, head) pairs.

Reads the jsonl produced by extract_pairs.py, parses both sides with
sqlglot(dialect="bigquery"), and buckets each pair into a coarse change-type
category using sqlglot's AST diff (sqlglot.diff). This is a heuristic
discovery tool, not the real Stage-1/2/3 canonicalizer -- its only job is to
tell us the frequency distribution of change types in real SQL history, so
we know what the v0 fragment should prioritize.

Buckets (checked in this priority order -- first match wins, since a single
commit often touches multiple things and we want the "riskiest" label):
  parse_error         -- either side failed to parse under BigQuery dialect
  formatting_only      -- normalized/pretty-printed ASTs are textually identical
  join_change          -- a Join node was inserted/removed/updated
  predicate_change      -- Where/Having node touched
  set_op_change         -- Union/Intersect/Except touched
  aggregation_change    -- Group node touched, or an AggFunc inserted/removed
  window_function_change -- a Window node touched
  cte_change            -- With/CTE node touched
  projection_change     -- Select's expressions (column list) touched
  order_limit_change    -- only Order/Limit/Offset touched (usually benign)
  other_structural_change -- something changed but doesn't fit above

Output:
  - output/classified.jsonl  (one row per input pair, with bucket + diff summary)
  - prints a frequency table to stdout
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.diff import diff as sql_diff

DIALECT = "bigquery"

# node-type -> bucket, checked in this order
BUCKET_RULES = [
    ("join_change", (exp.Join,)),
    ("predicate_change", (exp.Where, exp.Having)),
    ("set_op_change", (exp.Union, exp.Intersect, exp.Except)),
    ("aggregation_change", (exp.Group, exp.AggFunc)),
    ("window_function_change", (exp.Window,)),
    ("cte_change", (exp.With, exp.CTE)),
    ("projection_change", (exp.Select,)),  # catches column-list edits on the Select node itself
    ("order_limit_change", (exp.Order, exp.Limit, exp.Offset)),
]


def normalize_str(tree: exp.Expression) -> str:
    """Cheap canonical string: re-render, then collapse whitespace and case
    on identifiers/keywords via sqlglot's own generator normalization."""
    try:
        return tree.sql(dialect=DIALECT, normalize=True, pretty=False)
    except Exception:
        return tree.sql(dialect=DIALECT, pretty=False)


def classify_diff(base_ast: exp.Expression, head_ast: exp.Expression) -> tuple[str, dict]:
    edits = sql_diff(base_ast, head_ast, delta_only=True)
    touched_types = Counter()
    for edit in edits:
        # edit is one of Insert/Remove/Move/Update/Keep (Keep filtered by delta_only)
        node = getattr(edit, "expression", None) or getattr(edit, "source", None)
        if node is None:
            continue
        touched_types[type(node).__name__] += 1

    for bucket_name, node_classes in BUCKET_RULES:
        names = {c.__name__ for c in node_classes}
        if touched_types.keys() & names:
            return bucket_name, dict(touched_types)

    if not edits:
        return "formatting_only", {}
    return "other_structural_change", dict(touched_types)


def process_file(path: Path, out_f, counter: Counter):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            base_sql, head_sql = rec["base"], rec["head"]

            try:
                base_ast = sqlglot.parse_one(base_sql, dialect=DIALECT)
                head_ast = sqlglot.parse_one(head_sql, dialect=DIALECT)
            except Exception as e:
                counter["parse_error"] += 1
                out_f.write(json.dumps({**rec, "bucket": "parse_error",
                                         "error": str(e)[:200]}) + "\n")
                continue

            if normalize_str(base_ast) == normalize_str(head_ast):
                bucket, touched = "formatting_only", {}
            else:
                try:
                    bucket, touched = classify_diff(base_ast, head_ast)
                except Exception as e:
                    bucket, touched = "other_structural_change", {"diff_error": str(e)[:120]}

            counter[bucket] += 1
            out_f.write(json.dumps({
                "repo": rec["repo"], "commit": rec["commit"], "path": rec["path"],
                "bucket": bucket, "touched_node_types": touched,
                "base": base_sql, "head": head_sql,
            }) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    counter = Counter()
    with open(args.out, "w", encoding="utf-8") as out_f:
        for p in args.inputs:
            process_file(p, out_f, counter)

    total = sum(counter.values())
    print(f"\n=== Frequency table ({total} pairs total) ===", file=sys.stderr)
    for bucket, n in counter.most_common():
        pct = 100 * n / total if total else 0
        print(f"  {bucket:28s} {n:5d}  ({pct:5.1f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()
