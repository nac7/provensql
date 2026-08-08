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

Running the same judge across two providers (Anthropic + OpenAI) is stronger
evidence than either alone: it shows the false-EQUIVALENT failure is a
property of LLM judging in general, not one vendor's model.

Run:
  python eval/baselines.py --no-llm                       # free baselines only
  python eval/baselines.py --anthropic-model claude-opus-5
  python eval/baselines.py --openai-model gpt-5           # needs OPENAI_API_KEY
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


def make_openai_judge(model: str):
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY from the environment
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "sql_verdict", "strict": True, "schema": JUDGE_SCHEMA},
    }

    def judge(base: str, head: str) -> str:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": f"BEFORE:\n{base}\n\nAFTER:\n{head}"},
                ],
                response_format=response_format,
            )
        except Exception as e:
            return f"ERROR:{type(e).__name__}"
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            return "UNKNOWN"
        content = msg.content
        if not content:
            return "UNKNOWN"
        try:
            return json.loads(content)["verdict"]
        except Exception:
            return "UNKNOWN"

    return judge


def run_judge(name: str, judge, records: list, labels: list[str]):
    print(f"\nrunning {name} over {len(records)} pairs... (billed)")
    verdicts = []
    for i, r in enumerate(records, 1):
        verdicts.append(judge(r["base"], r["head"]))
        if i % 25 == 0:
            print(f"  {i}/{len(records)}")
    errors = [v for v in verdicts if v.startswith("ERROR:")]
    if errors:
        print(f"  ({len(errors)} calls errored; counted as non-decided) e.g. {errors[0]}")
    score(name, verdicts, labels)


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
    ap.add_argument("--anthropic-model", default=None, help="e.g. claude-opus-5 (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--openai-model", default=None, help="e.g. gpt-5 (needs OPENAI_API_KEY)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true", help="skip all LLM judges (no API calls)")
    args = ap.parse_args()

    records = [json.loads(l) for l in open(LABELED, encoding="utf-8")]
    if args.limit:
        records = records[: args.limit]
    labels = [r["human_label"] for r in records]

    score("string_equal", [string_equal(r["base"], r["head"]) for r in records], labels)
    score("sqlglot_normalized", [sqlglot_normalized(r["base"], r["head"]) for r in records], labels)

    if args.no_llm or not (args.anthropic_model or args.openai_model):
        print("\n(LLM judges skipped -- pass --anthropic-model and/or --openai-model to run them)")
        return

    if args.anthropic_model:
        try:
            run_judge(f"anthropic:{args.anthropic_model}", make_llm_judge(args.anthropic_model), records, labels)
        except Exception as e:
            print(f"\n(Anthropic judge unavailable: {type(e).__name__}: {e})")

    if args.openai_model:
        try:
            run_judge(f"openai:{args.openai_model}", make_openai_judge(args.openai_model), records, labels)
        except Exception as e:
            print(f"\n(OpenAI judge unavailable: {type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
