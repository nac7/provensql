"""
Stage C+D of the Calcite rule-ingestion pilot (docs/rule_ingestion_scope.md, I0).

Take the extracted checkSimplify pairs (Stage A), translate both sides to SQL
(Stage B), keep those that land in the modeled arithmetic/NULL fragment
(Stage C), and audit each with the error-semantics engine (Stage D): does our
ERROR/NULL model agree that Calcite's asserted `input == expected` holds?

  * EQUIVALENT_ERR  -> we agree with Calcite (engine validated on a real pair)
  * DIFFERENT       -> we DISAGREE: either a real error-axis defect in the rule,
                       or a place our model and Calcite's dialect differ (e.g.
                       div-by-zero as ERROR vs NULL). Every one is a triage lead.
  * UNKNOWN         -> honest abstention (out of fragment after translation)

CHECKPOINTING (resumable): each pair's result is appended to a JSONL checkpoint
and flushed immediately. On restart we load the ids already done and skip them,
so a paused/killed run resumes exactly where it stopped. Use --restart to force
a clean run.

    python mining/calcite_ingest/run_audit.py <pairs.json> [--out DIR] [--restart]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from translate import translate_dsl, translate_dump  # noqa: E402
from provensql.error_semantics import error_equivalent  # noqa: E402


def _load_done(ckpt: Path):
    done = {}
    if ckpt.exists():
        for line in ckpt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rec = json.loads(line)
                done[rec["id"]] = rec
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs")
    ap.add_argument("--out", default=None, help="output dir (default: alongside pairs.json)")
    ap.add_argument("--restart", action="store_true", help="ignore & overwrite any checkpoint")
    args = ap.parse_args()

    pairs = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
    candidates = [p for p in pairs if p["arithmetic"] or p["nullish"]]

    out_dir = Path(args.out) if args.out else Path(args.pairs).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = out_dir / "audit_checkpoint.jsonl"
    if args.restart and ckpt.exists():
        ckpt.unlink()

    done = _load_done(ckpt)
    print(f"{len(candidates)} candidate pairs; {len(done)} already in checkpoint "
          f"-> resuming, {len(candidates) - len(done)} to go")

    # append mode; flush after every write so a kill mid-run loses nothing
    with ckpt.open("a", encoding="utf-8") as fh:
        for i, p in enumerate(candidates, 1):
            if p["id"] in done:
                continue
            lhs = translate_dsl(p["input_dsl"])
            rhs = translate_dump(p["expected"][0]) if p["expected"] else None
            rec = {"id": p["id"], "line": p["line"], "kind": p["kind"],
                   "input_dsl": p["input_dsl"], "expected": p["expected"],
                   "lhs_sql": lhs, "rhs_sql": rhs}
            if lhs is None or rhs is None:
                rec["status"] = "skipped"
                rec["reason"] = ("untranslatable-input" if lhs is None else "") + \
                                (";untranslatable-expected" if rhs is None else "")
            else:
                res, witness = error_equivalent(lhs, rhs)
                rec["status"] = res
                rec["witness"] = witness
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            done[p["id"]] = rec
            if i % 20 == 0:
                print(f"  ...{i}/{len(candidates)}")

    # summary from the full checkpoint
    recs = list(done.values())
    from collections import Counter
    c = Counter(r["status"] for r in recs)
    audited = [r for r in recs if r["status"] in ("EQUIVALENT_ERR", "DIFFERENT", "UNKNOWN")]
    contradictions = [r for r in recs if r["status"] == "DIFFERENT"]
    print("\n=== pilot audit summary ===")
    print(f"  candidates:            {len(candidates)}")
    print(f"  translated & audited:  {len(audited)}")
    for k in ("EQUIVALENT_ERR", "DIFFERENT", "UNKNOWN", "skipped"):
        print(f"    {k:16} {c.get(k, 0)}")
    if contradictions:
        print("\n  CONTRADICTIONS (Calcite asserts ==, our error model says DIFFERENT):")
        for r in contradictions:
            print(f"    [{r['id']}] {r['input_dsl'][:60]}  ->  {r['expected']}")
            print(f"           lhs={r['lhs_sql']}  rhs={r['rhs_sql']}  witness={r.get('witness')}")
    else:
        print("\n  no contradictions: our error model agrees with every audited "
              "Calcite simplification (engine validated on real pairs).")

    summary_path = out_dir / "audit_summary.json"
    summary_path.write_text(json.dumps(
        {"candidates": len(candidates), "counts": dict(c),
         "contradictions": contradictions}, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {ckpt}\nwrote {summary_path}")


if __name__ == "__main__":
    main()
