"""
provensql showcase: what a sound checker does that an LLM judge can't.

Runs provensql live on a handful of curated SQL edits -- safe refactors it
proves, classic traps it catches with a witness, a catalog-dependent edit that
flips verdict with the schema, and an out-of-fragment case it honestly refuses.
Each block prints the verdict, the reason, and (for DIFFERENT) the replayable
witness instance.

    python demo/showcase.py                 # provensql only
    python demo/showcase.py --llm           # also query an OpenAI judge
                                            #   (needs OPENAI_API_KEY; billed)

The --llm column is the point: an LLM judge is confident and often right, but
it says EQUIVALENT on traps like COUNT(x) vs COUNT(*) -- the one error a sound
checker never makes. See docs/evaluation.md for the measured rate.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from provensql import catalog as catalog_module  # noqa: E402
from provensql.compare import compare  # noqa: E402

CATALOG = ROOT / "demo" / "orders_catalog.yml"

# (title, what an LLM/human tends to say, base, head, catalog-path-or-None)
CASES = [
    ("Conjunct reordering (safe)",
     "LLM: equivalent (correct)",
     "SELECT x FROM t WHERE a > 1 AND b < 5",
     "SELECT x FROM t WHERE b < 5 AND a > 1", None),
    ("COALESCE rewritten as CASE (safe)",
     "LLM: equivalent (correct)",
     "SELECT COALESCE(name, 'n/a') AS nm FROM users",
     "SELECT CASE WHEN name IS NULL THEN 'n/a' ELSE name END AS nm FROM users", None),
    ("COUNT(col) -> COUNT(*)  [classic trap]",
     "LLM: usually 'equivalent' -- WRONG (COUNT(col) skips NULLs)",
     "SELECT COUNT(dept) AS c FROM emp",
     "SELECT COUNT(*) AS c FROM emp", None),
    ("Off-by-one: > vs >=",
     "LLM: sometimes 'equivalent' -- WRONG",
     "SELECT x FROM t WHERE a > 1",
     "SELECT x FROM t WHERE a >= 1", None),
    ("Added DISTINCT (dedup)",
     "LLM: sometimes 'equivalent' -- WRONG when duplicates exist",
     "SELECT dept FROM emp",
     "SELECT DISTINCT dept FROM emp", None),
    ("LEFT JOIN -> JOIN, no catalog  [classic trap]",
     "LLM: usually 'equivalent' -- WRONG without an FK guarantee",
     "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id",
     "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id", None),
    ("LEFT JOIN -> JOIN, WITH catalog (same pair)",
     "provensql now PROVES it, given NOT NULL + FK + UNIQUE",
     "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id",
     "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id", str(CATALOG)),
    ("Window-function change (out of fragment)",
     "provensql refuses rather than guess",
     "SELECT ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t",
     "SELECT RANK() OVER (ORDER BY a) AS rn FROM t", None),
]


def make_llm():
    try:
        sys.path.insert(0, str(ROOT / "eval"))
        from baselines import make_openai_judge  # type: ignore
        return make_openai_judge("gpt-5")
    except Exception as e:  # noqa: BLE001
        print(f"(LLM judge unavailable: {e})\n")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also query an OpenAI judge (needs OPENAI_API_KEY)")
    args = ap.parse_args()
    judge = make_llm() if args.llm else None

    for title, note, base, head, cat_path in CASES:
        cat = catalog_module.load(cat_path) if cat_path else None
        v = compare(base, head, catalog=cat)
        print("=" * 78)
        print(title)
        print(f"  before: {base}")
        print(f"  after : {head}")
        if cat_path:
            print(f"  catalog: {Path(cat_path).name}")
        print(f"  note  : {note}")
        print(f"  --> provensql: {v.type.value}  ({(v.reason or v.reason_code)})")
        for a in v.assumptions:
            print(f"        assuming: {a}")
        if v.witness:
            print("        witness:")
            for line in v.witness.splitlines():
                print(f"          {line}")
        if judge is not None:
            try:
                lv = judge(base, head)
            except Exception as e:  # noqa: BLE001
                lv = f"error: {e}"
            print(f"  --> LLM judge: {lv}")
    print("=" * 78)


if __name__ == "__main__":
    main()
