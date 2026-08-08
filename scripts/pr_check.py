"""
provensql PR gate: check every SQL file changed in a git range for
behavior-changing edits, and fail the build if any is found.

For each `.sql` file that differs between a base ref and the current tree, this
compares the two versions with provensql and reports the verdict. It exits
non-zero if any edit is a proven/likely behavior change (`DIFFERENT` or
`SCHEMA_CHANGE`); `UNKNOWN` is a warning by default (use --fail-on-unknown to
gate on it too), and `EQUIVALENT` passes.

Usage:
    python scripts/pr_check.py --base origin/main [--catalog schema.yml]
    python scripts/pr_check.py --base HEAD~1
    python scripts/pr_check.py --pair before.sql after.sql   # no git; direct

Designed to run from `action.yml` (this repo's composite GitHub Action) or a
pre-commit `repo: local` hook -- see docs/ci_integration.md.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from provensql import catalog as catalog_module  # noqa: E402
from provensql.compare import compare  # noqa: E402
from provensql.verdict import VerdictType  # noqa: E402

BEHAVIOR_CHANGE = {VerdictType.DIFFERENT, VerdictType.SCHEMA_CHANGE}

ICON = {
    VerdictType.EQUIVALENT: "OK  ",
    VerdictType.UNKNOWN: "?   ",
    VerdictType.DIFFERENT: "FAIL",
    VerdictType.SCHEMA_CHANGE: "FAIL",
}


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def changed_sql_files(base):
    r = _git("diff", "--name-only", f"{base}...HEAD", "--", "*.sql")
    if r.returncode != 0:
        r = _git("diff", "--name-only", base, "--", "*.sql")  # fallback: base..worktree
    return [line for line in r.stdout.splitlines() if line.strip()]


def version_at(ref, path):
    r = _git("show", f"{ref}:{path}")
    return r.stdout if r.returncode == 0 else None


def current_version(path):
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else None


def check_pair(base_sql, head_sql, catalog):
    if base_sql is None or head_sql is None:
        return None  # added or deleted file -- nothing to compare
    return compare(base_sql, head_sql, catalog=catalog)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main", help="git ref to diff against")
    ap.add_argument("--catalog", default=None, help="catalog YAML for constraint-aware proofs")
    ap.add_argument("--fail-on-unknown", action="store_true", help="also fail when a verdict is UNKNOWN")
    ap.add_argument("--pair", nargs=2, metavar=("BEFORE", "AFTER"), help="compare two files directly (no git)")
    args = ap.parse_args()

    catalog = catalog_module.load(args.catalog) if args.catalog else None

    if args.pair:
        v = compare(Path(args.pair[0]).read_text(encoding="utf-8"),
                    Path(args.pair[1]).read_text(encoding="utf-8"), catalog=catalog)
        print(f"[{ICON[v.type]}] {args.pair[0]} -> {args.pair[1]}: {v.type.value} ({v.reason or v.reason_code})")
        return 1 if v.type in BEHAVIOR_CHANGE or (args.fail_on_unknown and v.type == VerdictType.UNKNOWN) else 0

    files = changed_sql_files(args.base)
    if not files:
        print(f"provensql: no changed .sql files vs {args.base}")
        return 0

    failed = warned = 0
    print(f"provensql: checking {len(files)} changed .sql file(s) vs {args.base}\n")
    for path in files:
        v = check_pair(version_at(args.base, path), current_version(path), catalog)
        if v is None:
            print(f"[--  ] {path}: added or deleted (skipped)")
            continue
        print(f"[{ICON[v.type]}] {path}: {v.type.value}  ({(v.reason or v.reason_code)[:70]})")
        if v.type in BEHAVIOR_CHANGE:
            failed += 1
            if v.witness:
                for line in v.witness.splitlines()[:4]:
                    print(f"         {line}")
        elif v.type == VerdictType.UNKNOWN:
            warned += 1

    print()
    gate_unknown = args.fail_on_unknown and warned
    if failed or gate_unknown:
        print(f"provensql: {failed} behavior-changing edit(s)"
              + (f", {warned} UNKNOWN" if gate_unknown else "") + " -- review required")
        return 1
    print(f"provensql: no behavior-changing edits ({warned} UNKNOWN, not gated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
