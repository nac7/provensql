"""
Stage A of the Calcite rule-ingestion pilot (docs/rule_ingestion_scope.md, I0).

Harvests the declarative (input, expected) simplification pairs that Calcite's
own test suite asserts, from RexProgramTest.java's checkSimplify / checkSimplify2
/ checkSimplify3 calls. Each such call is a real intended simplification: the
first argument is the input expression built with the test DSL (plus/mul/div/
case_/eq/...), the remaining string argument(s) are the RexNode dump(s) of the
expected simplified form.

We do NOT parse Calcite's imperative rule code; we harvest the pairs the project
maintains as tests. This module only *extracts and classifies* -- translation to
our SQL fragment is Stage B (translate.py).

Input is the raw RexProgramTest.java (kept out of the repo; pass its path).
Output is a JSON list of pair records, cached so re-runs are cheap.

    python mining/calcite_ingest/extract.py <path-to-RexProgramTest.java> [out.json]
"""

import json
import re
import sys
from pathlib import Path

CHECK_RE = re.compile(r"\bcheckSimplify(3|2)?\s*\(")
# arithmetic builders in the test DSL (word-boundary + open paren). `add(` is the
# struct-type builder, NOT arithmetic -- deliberately excluded.
ARITH_RE = re.compile(r"\b(plus|minus|mul|div|sub|abs|mod|power)\s*\(")
# the error/null-relevant wrappers we already model or plan to
NULLISH_RE = re.compile(r"\b(isNull|isNotNull|coalesce|nullif|case_)\s*\(")


def _split_args(s: str):
    """Split a top-level argument list on commas, respecting parens/brackets and
    Java string/char literals."""
    args, depth, buf, i, in_str, quote = [], 0, [], 0, False, ""
    while i < len(s):
        c = s[i]
        if in_str:
            buf.append(c)
            if c == "\\":
                buf.append(s[i + 1]); i += 2; continue
            if c == quote:
                in_str = False
            i += 1; continue
        if c in "\"'":
            in_str, quote = True, c; buf.append(c); i += 1; continue
        if c in "([{":
            depth += 1; buf.append(c)
        elif c in ")]}":
            depth -= 1; buf.append(c)
        elif c == "," and depth == 0:
            args.append("".join(buf).strip()); buf = []
        else:
            buf.append(c)
        i += 1
    if buf:
        args.append("".join(buf).strip())
    return args


def _extract_call(text: str, open_paren_idx: int):
    """Given the index of the '(' opening a checkSimplify call, return the raw
    argument substring (inside the outermost parens), string-aware."""
    depth, i, in_str, quote = 0, open_paren_idx, False, ""
    start = open_paren_idx + 1
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2; continue
            if c == quote:
                in_str = False
        elif c in "\"'":
            in_str, quote = True, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return None


def _unquote_java_string(s: str):
    """If s is a single Java string literal, return its content; else None."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].encode().decode("unicode_escape")
    return None


def extract(java_path: Path):
    text = java_path.read_text(encoding="utf-8")
    # line lookup for provenance
    line_starts = [0]
    for m in re.finditer("\n", text):
        line_starts.append(m.end())

    def line_of(idx):
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= idx:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    pairs = []
    for m in CHECK_RE.finditer(text):
        kind = "checkSimplify" + (m.group(1) or "")
        open_idx = m.end() - 1
        raw = _extract_call(text, open_idx)
        if raw is None:
            continue
        args = _split_args(raw)
        if len(args) < 2:
            continue
        input_dsl = args[0]
        expected = [(_unquote_java_string(a) or a) for a in args[1:]]
        pairs.append({
            "id": f"L{line_of(m.start())}",
            "kind": kind,
            "line": line_of(m.start()),
            "input_dsl": " ".join(input_dsl.split()),
            "expected": expected,
            "arithmetic": bool(ARITH_RE.search(input_dsl)),
            "nullish": bool(NULLISH_RE.search(input_dsl)),
        })
    return pairs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    java_path = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else java_path.with_suffix(".pairs.json")
    pairs = extract(java_path)
    out.write_text(json.dumps(pairs, indent=2), encoding="utf-8")

    n = len(pairs)
    arith = [p for p in pairs if p["arithmetic"]]
    nullish = [p for p in pairs if p["nullish"]]
    arith_or_null = [p for p in pairs if p["arithmetic"] or p["nullish"]]
    print(f"extracted {n} checkSimplify* pairs")
    print(f"  arithmetic (plus/minus/mul/div/sub/...):   {len(arith)}")
    print(f"  nullish (isNull/coalesce/nullif/case_):    {len(nullish)}")
    print(f"  arithmetic OR nullish (candidate audit set): {len(arith_or_null)}")
    print(f"wrote {out}")
    print("\nsample arithmetic pairs:")
    for p in arith[:12]:
        print(f"  [{p['id']}] {p['input_dsl'][:70]}  ->  {p['expected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
