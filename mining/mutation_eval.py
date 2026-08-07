"""
Mutation-based evaluation of Stage 3, the rigorous way to measure an
equivalence checker: apply transformations whose ground-truth answer we
know by construction.

  - Equivalence-PRESERVING mutations (predicate reordering, operand swap,
    double negation, WHERE->ON pushdown for inner joins, inner-join
    reordering) SHOULD come back EQUIVALENT. The rate is recall.
  - Equivalence-BREAKING mutations (flip a comparison operator, bump a
    literal, drop a conjunct) must NEVER come back EQUIVALENT. Any that do
    are dumped for inspection -- either a real soundness bug, or a mutation
    that happened to be a no-op (e.g. dropping a redundant conjunct), which
    the reviewer distinguishes by hand.

This exercises exactly the refactor classes Stage 3 targets, which the
natural bigquery-etl corpus barely contains -- see full_corpus_eval.py for
that finding. Source queries are real (extracted from the mined corpus),
only the transformations are synthetic.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlglot import exp

from sqlsense.canonicalize import UnsupportedConstruct, parse
from sqlsense.compare import compare
from sqlsense.verdict import VerdictType

CLASSIFIED = Path(__file__).parent / "output" / "classified.jsonl"


# ---- mutators: each takes an exp.Select (already parsed) and returns a
# ---- mutated *SQL string*, or None if the mutation doesn't apply here. ----

def _where_pred(tree):
    w = tree.args.get("where")
    return w.this if w is not None else None


def m_reorder_where_conjuncts(tree):
    pred = _where_pred(tree)
    if not isinstance(pred, exp.And):
        return None
    t = tree.copy()
    w = t.args["where"].this
    # swap the two immediate operands of the top-level AND
    new = exp.And(this=w.expression.copy(), expression=w.this.copy())
    t.args["where"].set("this", new)
    return t.sql(dialect="bigquery")


def m_swap_eq_operands(tree):
    t = tree.copy()
    changed = False
    for eq in t.find_all(exp.EQ):
        left, right = eq.this.copy(), eq.expression.copy()
        eq.set("this", right)
        eq.set("expression", left)
        changed = True
        break
    return t.sql(dialect="bigquery") if changed else None


def m_double_negate(tree):
    pred = _where_pred(tree)
    if pred is None:
        return None
    t = tree.copy()
    inner = t.args["where"].this.copy()
    t.args["where"].set("this", exp.Not(this=exp.Paren(this=exp.Not(this=exp.Paren(this=inner)))))
    return t.sql(dialect="bigquery")


def _inner_joins(tree):
    joins = tree.args.get("joins") or []
    if joins and all((j.side or "") == "" and (j.kind or "").upper() in ("", "INNER") for j in joins):
        return joins
    return []


def m_push_where_to_on(tree):
    pred = _where_pred(tree)
    joins = _inner_joins(tree)
    if pred is None or not joins:
        return None
    t = tree.copy()
    tjoins = t.args["joins"]
    last = tjoins[-1]
    on = last.args.get("on")
    if on is None:
        return None
    combined = exp.And(this=on.copy(), expression=t.args["where"].this.copy())
    last.set("on", combined)
    t.set("where", None)
    return t.sql(dialect="bigquery")


def m_reorder_inner_joins(tree):
    joins = _inner_joins(tree)
    if len(joins) < 2:
        return None
    t = tree.copy()
    tjoins = list(t.args["joins"])
    tjoins[0], tjoins[1] = tjoins[1], tjoins[0]
    t.set("joins", tjoins)
    return t.sql(dialect="bigquery")


def m_flip_comparison(tree):
    pred = _where_pred(tree)
    if pred is None:
        return None
    t = tree.copy()
    for node in t.args["where"].find_all(exp.GT):
        node.replace(exp.GTE(this=node.this.copy(), expression=node.expression.copy()))
        return t.sql(dialect="bigquery")
    for node in t.args["where"].find_all(exp.LT):
        node.replace(exp.LTE(this=node.this.copy(), expression=node.expression.copy()))
        return t.sql(dialect="bigquery")
    for node in t.args["where"].find_all(exp.EQ):
        node.replace(exp.NEQ(this=node.this.copy(), expression=node.expression.copy()))
        return t.sql(dialect="bigquery")
    return None


def m_bump_literal(tree):
    pred = _where_pred(tree)
    if pred is None:
        return None
    t = tree.copy()
    for lit in t.args["where"].find_all(exp.Literal):
        if not lit.is_string:
            try:
                val = int(lit.this)
            except ValueError:
                continue
            lit.replace(exp.Literal(this=str(val + 1), is_string=False))
            return t.sql(dialect="bigquery")
    return None


def m_add_redundant_distinct(tree):
    # Adding DISTINCT to a query that GROUP BYs and projects every group key
    # is a no-op -- the rows are already distinct.
    group = tree.args.get("group")
    if tree.args.get("distinct") is not None or not (group and group.expressions):
        return None
    projected = {(_unalias_e(e)).sql(dialect="bigquery", normalize=True) for e in tree.expressions}
    group_keys = [g.sql(dialect="bigquery", normalize=True) for g in group.expressions]
    if not all(g in projected for g in group_keys):
        return None
    t = tree.copy()
    t.set("distinct", exp.Distinct())
    return t.sql(dialect="bigquery")


def m_add_deduplicating_distinct(tree):
    # Adding DISTINCT to a plain SELECT with no GROUP BY genuinely changes
    # results (it removes duplicate rows) -- must never be called EQUIVALENT.
    if tree.args.get("distinct") is not None or tree.args.get("group") is not None:
        return None
    if not tree.expressions:
        return None
    t = tree.copy()
    t.set("distinct", exp.Distinct())
    return t.sql(dialect="bigquery")


def _unalias_e(node):
    return node.this if isinstance(node, exp.Alias) else node


def m_drop_conjunct(tree):
    pred = _where_pred(tree)
    if not isinstance(pred, exp.And):
        return None
    t = tree.copy()
    t.args["where"].set("this", t.args["where"].this.this.copy())  # keep only left conjunct
    return t.sql(dialect="bigquery")


PRESERVING = [
    ("reorder_where_conjuncts", m_reorder_where_conjuncts),
    ("swap_eq_operands", m_swap_eq_operands),
    ("double_negate", m_double_negate),
    ("push_where_to_on", m_push_where_to_on),
    ("reorder_inner_joins", m_reorder_inner_joins),
    ("add_redundant_distinct", m_add_redundant_distinct),
]
BREAKING = [
    ("flip_comparison", m_flip_comparison),
    ("bump_literal", m_bump_literal),
    ("drop_conjunct", m_drop_conjunct),
    ("add_deduplicating_distinct", m_add_deduplicating_distinct),
]


def _stage(v):
    if v.type == VerdictType.EQUIVALENT:
        return "stage3" if "SMT-proved" in v.reason else "stage2"
    return v.type.value


def main():
    seen, queries = set(), []
    for line in open(CLASSIFIED, encoding="utf-8"):
        r = json.loads(line)
        for sql in (r["base"], r["head"]):
            h = hash(sql)
            if h in seen:
                continue
            seen.add(h)
            try:
                t = parse(sql)
            except UnsupportedConstruct:
                continue
            if isinstance(t, exp.Select):
                queries.append(sql)

    print(f"{len(queries)} real single-SELECT source queries\n")

    # recall on preserving mutations
    print("=== EQUIVALENCE-PRESERVING (want EQUIVALENT; recall) ===")
    for name, mut in PRESERVING:
        applied = eq = 0
        by_stage = defaultdict(int)
        for sql in queries:
            try:
                mutated = mut(parse(sql))
            except Exception:
                mutated = None
            if mutated is None or mutated.strip() == sql.strip():
                continue
            applied += 1
            try:
                v = compare(sql, mutated)
            except Exception:
                continue
            if v.type == VerdictType.EQUIVALENT:
                eq += 1
                by_stage[_stage(v)] += 1
        rate = f"{100*eq/applied:.0f}%" if applied else "n/a"
        stages = ", ".join(f"{k}:{n}" for k, n in sorted(by_stage.items()))
        print(f"  {name:24s} recall {eq:3d}/{applied:3d} ({rate:>4})  [{stages}]")

    # soundness on breaking mutations
    print("\n=== EQUIVALENCE-BREAKING (must NEVER be EQUIVALENT; soundness) ===")
    violations = []
    for name, mut in BREAKING:
        applied = false_eq = 0
        verdicts = defaultdict(int)
        for sql in queries:
            try:
                mutated = mut(parse(sql))
            except Exception:
                mutated = None
            if mutated is None or mutated.strip() == sql.strip():
                continue
            applied += 1
            try:
                v = compare(sql, mutated)
            except Exception:
                continue
            verdicts[v.type.value] += 1
            if v.type == VerdictType.EQUIVALENT:
                false_eq += 1
                violations.append((name, sql, mutated))
        dist = ", ".join(f"{k}:{n}" for k, n in sorted(verdicts.items()))
        flag = " *** FALSE EQUIVALENT ***" if false_eq else ""
        print(f"  {name:24s} false_eq {false_eq}/{applied}{flag}  [{dist}]")

    if violations:
        out = Path(__file__).parent / "output" / "mutation_violations.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for name, sql, mutated in violations:
                f.write(json.dumps({"mutator": name, "base": sql, "head": mutated}) + "\n")
        print(f"\nwrote {len(violations)} false-EQUIVALENT cases to {out} for inspection")


if __name__ == "__main__":
    main()
