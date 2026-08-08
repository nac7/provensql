import argparse
import json
import sys
from pathlib import Path

from provensql import catalog as catalog_module
from provensql.compare import compare
from provensql.verdict import Verdict, VerdictType

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
    diff.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-checkable audit certificate (JSON) instead of the text summary",
    )

    args = ap.parse_args()

    if args.cmd == "diff":
        catalog = catalog_module.load(args.catalog) if args.catalog else None
        base_sql = args.base.read_text(encoding="utf-8")
        head_sql = args.head.read_text(encoding="utf-8")
        verdict = compare(base_sql, head_sql, catalog=catalog)
        if args.json:
            print(json.dumps(_certificate(verdict, args.base, args.head, args.catalog), indent=2))
        else:
            print(f"{verdict.type.value}: {verdict.reason}")
            for a in verdict.assumptions:
                print(f"  assuming: {a}")
        sys.exit(EXIT_CODES[verdict.type])


def _certificate(verdict: Verdict, base: Path, head: Path, catalog: Path | None) -> dict:
    """A structured, archivable record of one comparison -- the artifact an
    auditor keeps. A DIFFERENT verdict carries its replayable witness instance;
    an EQUIVALENT verdict records that it survived the runtime counterexample
    backstop (see compare._equivalent_backstop). Everything here is derived
    from the verdict, so re-running the same inputs reproduces it exactly."""
    cert = {
        "tool": "provensql",
        "verdict": verdict.type.value,
        "reason": verdict.reason,
        "assumptions": list(verdict.assumptions),
        "inputs": {"base": str(base), "head": str(head), "catalog": str(catalog) if catalog else None},
    }
    if verdict.type == VerdictType.UNKNOWN:
        cert["reason_code"] = verdict.reason_code
    if verdict.type == VerdictType.DIFFERENT:
        cert["witness"] = verdict.witness
    if verdict.type == VerdictType.EQUIVALENT:
        # The catalog-free backstop actively tried to refute this and failed;
        # with a catalog it can't run soundly (see _equivalent_backstop).
        cert["counterexample_backstop"] = "no_counterexample_found" if catalog is None else "not_run_catalog_present"
    return cert


if __name__ == "__main__":
    main()
