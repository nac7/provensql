"""
Benchmark triage: measure how much of a standard equivalence benchmark falls
inside provensql's provable fragment, honestly and reproducibly.

Motivation: provensql is a *sound, fragment-restricted* checker. The academic
sound checkers it should be compared to -- SPES (ICDE'21), EQUITAS,
Cosette/HoTTSQL -- report on the "Calcite 232" set of equivalent query pairs.
This script runs provensql over such a benchmark and buckets each pair, so the
comparison is grounded in a real coverage number instead of a guess.

Every pair in these benchmarks is labeled EQUIVALENT, so:
  - EQUIVALENT (Stage 2 canonical match or Stage 3 SMT proof) = SOLVED (correct)
  - UNKNOWN                                                    = abstained (sound, not covered)
  - DIFFERENT                                                  = FALSE DIFFERENT (a bug to inspect:
        Stage 4 found a counterexample under catalog-free schema inference that
        the benchmark's intended schema would forbid, or a genuine bag/set
        subtlety). The runtime backstop guards EQUIVALENT, not DIFFERENT, so
        these must be counted and eyeballed.
  - SCHEMA_CHANGE                                              = output-shape mismatch (usually a
        parse/alias artifact on these single-table rename pairs)

Usage:
  python eval/benchmark_triage.py eval/benchmarks/spes_calcite_tests.json
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from provensql.canonicalize import UnsupportedConstruct, parse  # noqa: E402
from provensql.compare import compare  # noqa: E402
from provensql.verdict import VerdictType  # noqa: E402


def load_pairs(path: Path):
    """Accepts the SPES calcite_tests.json shape [{name,q1,q2}] and the
    generic [{name?,base,head}] shape."""
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = []
    for i, row in enumerate(data):
        name = row.get("name", f"pair_{i}")
        base = row.get("q1", row.get("base"))
        head = row.get("q2", row.get("head"))
        if base is not None and head is not None:
            pairs.append((name, base, head))
    return pairs


def stage0_outcome(sql: str) -> str:
    """Why (if at all) Stage 0 refuses this query -- the reason_code, or 'ok'."""
    try:
        parse(sql)
        return "ok"
    except UnsupportedConstruct as e:
        return e.reason_code


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval/benchmarks/spes_calcite_tests.json")
    pairs = load_pairs(path)
    print(f"{len(pairs)} pairs from {path.name} (all labeled EQUIVALENT)\n")

    verdicts = Counter()
    parse_reasons = Counter()   # why either side was refused at Stage 0
    both_parse = 0
    solved_by = Counter()       # stage2 vs stage3 among the SOLVED
    false_diff = []             # DIFFERENT on an equivalent pair -- inspect

    for name, base, head in pairs:
        b0, h0 = stage0_outcome(base), stage0_outcome(head)
        if b0 != "ok" or h0 != "ok":
            for r in (b0, h0):
                if r != "ok":
                    parse_reasons[r] += 1
        else:
            both_parse += 1

        try:
            v = compare(base, head)
        except Exception as e:
            verdicts["ERROR"] += 1
            continue
        verdicts[v.type.value] += 1

        if v.type == VerdictType.EQUIVALENT:
            solved_by["stage3_smt" if "SMT-proved" in v.reason else "stage2_canonical"] += 1
        elif v.type == VerdictType.DIFFERENT:
            false_diff.append((name, base, head, v.reason))

    n = len(pairs)
    solved = verdicts["EQUIVALENT"]
    print("=== final verdicts (ground truth = EQUIVALENT for all) ===")
    for k in ("EQUIVALENT", "UNKNOWN", "DIFFERENT", "SCHEMA_CHANGE", "ERROR"):
        if verdicts[k]:
            print(f"  {k:14s} {verdicts[k]:4d}  ({100*verdicts[k]/n:.1f}%)")

    print(f"\n=== coverage ===")
    print(f"  both sides parse (in Stage-0 fragment): {both_parse}/{n} ({100*both_parse/n:.1f}%)")
    print(f"  PROVEN EQUIVALENT (coverage):           {solved}/{n} ({100*solved/n:.1f}%)")
    if both_parse:
        print(f"  proven among the parseable:             {solved}/{both_parse} ({100*solved/both_parse:.1f}%)")
    if solved:
        print("  proven by:", ", ".join(f"{k}={c}" for k, c in solved_by.items()))

    print(f"\n=== why pairs fall outside the fragment (Stage-0 refusals, by side) ===")
    for r, c in parse_reasons.most_common():
        print(f"  {r:34s} {c}")

    print(f"\n=== FALSE DIFFERENT (must be 0 for soundness; inspect any) ===")
    print(f"  count: {len(false_diff)}")
    for name, base, head, reason in false_diff[:8]:
        print(f"  - {name}: {reason[:80]}")

    # machine-readable dump for the paper's appendix / reproducibility
    out = path.with_name(path.stem + "_triage.json")
    out.write_text(json.dumps({
        "benchmark": path.name,
        "n": n,
        "verdicts": dict(verdicts),
        "both_parse": both_parse,
        "proven_equivalent": solved,
        "solved_by": dict(solved_by),
        "stage0_refusal_reasons": dict(parse_reasons),
        "false_different": [{"name": nm, "reason": rs} for nm, _, _, rs in false_diff],
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out.name}")


if __name__ == "__main__":
    main()
