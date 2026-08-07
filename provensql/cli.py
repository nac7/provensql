import argparse
import sys
from pathlib import Path

from provensql import catalog as catalog_module
from provensql.compare import compare
from provensql.verdict import VerdictType

# CI-friendly exit codes: 0 = proven safe, 1 = couldn't decide (needs human
# review), 2 = proven/flagged as a behavior change.
EXIT_CODES = {
    VerdictType.EQUIVALENT: 0,
    VerdictType.UNKNOWN: 1,
    VerdictType.SCHEMA_CHANGE: 2,
    VerdictType.DIFFERENT: 2,
}


def main():
    ap = argparse.ArgumentParser(prog="provensql")
    sub = ap.add_subparsers(dest="cmd", required=True)

    diff = sub.add_parser("diff", help="compare two SQL files")
    diff.add_argument("base", type=Path)
    diff.add_argument("head", type=Path)
    diff.add_argument("--catalog", type=Path, default=None, help="YAML file with table/column types and known UDFs")

    args = ap.parse_args()

    if args.cmd == "diff":
        catalog = catalog_module.load(args.catalog) if args.catalog else None
        base_sql = args.base.read_text(encoding="utf-8")
        head_sql = args.head.read_text(encoding="utf-8")
        verdict = compare(base_sql, head_sql, catalog=catalog)
        print(f"{verdict.type.value}: {verdict.reason}")
        for a in verdict.assumptions:
            print(f"  assuming: {a}")
        sys.exit(EXIT_CODES[verdict.type])


if __name__ == "__main__":
    main()
