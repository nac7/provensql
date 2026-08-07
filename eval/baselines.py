"""
Baseline comparison for the evaluation writeup: how do the obvious
alternatives to provensql do on the same 213-pair hand-labeled corpus?

The headline claim provensql makes is "an LLM judge will confidently call
non-equivalent SQL equivalent." This script turns that from rhetoric into a
measured number by running three baselines and reporting, for each, the one
metric that matters: false-EQUIVALENT count (said EQUIVALENT when the human
label was DIFFERENT or SCHEMA_CHANGE) -- against provensql's 0.

  1. string_equal      -- trivial: identical text after strip()
  2. sqlglot_normalized -- parse+normalize both, compare canonical strings
                           (a proxy for a formatter/linter-based check)
  3. llm_judge         -- ask Claude "are these equivalent?" (needs an API
                           key + billed calls; skipped if unavailable)

Run: python eval/baselines.py [--model claude-opus-5] [--limit N] [--no-llm]
The labeled corpus is local-only (see README "Corpus & licensing").
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import sqlglot

LABELED = Path(__file__).parent.parent / "mining" / "output" / "labeled.jsonl"
NOT_EQUIVALENT = {"DIFFERENT", "SCHEMA_CHANGE"}

VERDICTS = ["EQUIVALENT", "DIFFERENT", "SCHEMA_CHANGE", "UNKNOWN"]

JUDGE_SYSTEM = (
    "You are a careful SQL reviewer. You are given two versions of a SQL query, "
    "BEFORE and AFTER. Decide the relationship between their results for every "
    "possible database instance, and answer with exactly one label:\n"
    "- EQUIVALENT: identical result set (same rows, same columns, same values) "
    "for ANY database contents.\n"
    "- DIFFERENT: there exists some database instance where the results differ.\n"
    "- SCHEMA_CHANGE: the output columns differ in name, order, or type.\n"
    "- UNKNOWN: you cannot determine the relationship.\n"
    "Consider NULL handling, JOIN semantics, duplicates, and empty tables."
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": VERDICTS}},
    "required": ["verdict"],
    "additionalProperties": False,
}


def string_equal(base: str, head: str) -> str:
    return "EQUIVALENT" if base.strip() == head.strip() else "DIFFERENT"


def sqlglot_normalized(base: str, head: str) -> str:
    try:
        b = sqlglot.parse_one(base, dialect="bigquery").sql(dialect="bigquery", normalize=True)
        h = sqlglot.parse_one(head, dialect="bigquery").sql(dialect="bigquery", normalize=True)
    except Exception:
        return "UNKNOWN"
    return "EQUIVALENT" if b == h else "DIFFERENT"


def make_llm_judge(model: str):
    import anthropic

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY or an `ant` profile

    def judge(base: str, head: str) -> str:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,  # room for default-on thinking + the short answer
                system=JUDGE_SYSTEM,
                messages=[{"role": "user", "content": f"BEFORE:\n{base}\n\nAFTER:\n{head}"}],
                output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            )
        except Exception as e:
            return f"ERROR:{type(e).__name__}"
        if resp.stop_reason == "refusal":
            return "UNKNOWN"  # a refusal to judge is not a false EQUIVALENT
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)["verdict"]
        except Exception:
            return "UNKNOWN"

    return judge


def score(name: str, verdicts: list[str], labels: list[str]):
    false_eq = [
        i for i, (v, l) in enumerate(zip(verdicts, labels))
        if v == "EQUIVALENT" and l in NOT_EQUIVALENT
    ]
    decided = sum(1 for v in verdicts if v in ("EQUIVALENT", "DIFFERENT", "SCHEMA_CHANGE"))
    correct = sum(1 for v, l in zip(verdicts, labels) if v == l)
    n = len(labels)
    print(f"\n=== {name} (n={n}) ===")
    dist = Counter(verdicts)
    print("  verdicts:", ", ".join(f"{k}:{dist[k]}" for k in sorted(dist)))
    print(f"  coverage (decided): {decided}/{n} = {100*decided/n:.1f}%")
    print(f"  exact-match accuracy: {correct}/{n} = {100*correct/n:.1f}%")
    print(f"  *** FALSE EQUIVALENT: {len(false_eq)} ***  (provensql: 0)")
    return len(false_eq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM judge (no API calls)")
    args = ap.parse_args()

    records = [json.loads(l) for l in open(LABELED, encoding="utf-8")]
    if args.limit:
        records = records[: args.limit]
    labels = [r["human_label"] for r in records]

    score("string_equal", [string_equal(r["base"], r["head"]) for r in records], labels)
    score("sqlglot_normalized", [sqlglot_normalized(r["base"], r["head"]) for r in records], labels)

    if args.no_llm:
        print("\n(LLM judge skipped: --no-llm)")
        return
    try:
        judge = make_llm_judge(args.model)
    except Exception as e:
        print(f"\n(LLM judge unavailable: {type(e).__name__}: {e}). Re-run with credentials, or --no-llm.")
        return

    print(f"\nrunning LLM judge ({args.model}) over {len(records)} pairs... (billed)")
    verdicts = []
    for i, r in enumerate(records, 1):
        verdicts.append(judge(r["base"], r["head"]))
        if i % 25 == 0:
            print(f"  {i}/{len(records)}")
    errors = [v for v in verdicts if v.startswith("ERROR:")]
    if errors:
        print(f"  ({len(errors)} calls errored; counted as non-decided)")
    score(f"llm_judge:{args.model}", verdicts, labels)


if __name__ == "__main__":
    main()
