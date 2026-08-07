"""
Predicate pushdown/pullup equivalence for Stage 3, valid only when every
join in the query is a plain INNER join.

For INNER joins, WHERE and each join's ON clause are just different places
to write conjuncts of the same overall filter -- `A JOIN B ON p1 WHERE p2`,
`A JOIN B ON (p1 AND p2)`, and `A JOIN B ON p2 WHERE p1` all produce
identical results. So instead of requiring "the WHERE clauses match" and
"the ON conditions match" separately (what join_algebra.py and stage3.py's
WHERE check do), this folds every ON condition plus WHERE into one big
conjunction per query and asks whether *that* is logically equivalent.

This is deliberately NOT extended to outer joins: `A LEFT JOIN B ON p1
WHERE b.x = 5` filters out exactly the unmatched (NULL-padded) rows that
the LEFT JOIN exists to keep -- moving `b.x = 5` into the ON clause changes
the result (unmatched A rows would then survive). Silently allowing
pushdown across an outer join is precisely the classic accidental-INNER-JOIN
bug; this module refuses to reason about it at all rather than get it wrong.
"""

from functools import reduce

from sqlglot import exp

from provensql import join_algebra
from provensql.smt import expressions_equivalent


def _conjunction(parts: list[exp.Expression]) -> exp.Expression:
    parts = [p for p in parts if p is not None]
    if not parts:
        return exp.true()
    return reduce(lambda acc, p: exp.And(this=acc, expression=p), parts[1:], parts[0])


def _extract_inner_only(tree: exp.Expression):
    """Returns (base_table, edges, all_tables) if every join is a plain
    INNER join; None if not analyzable or any join is LEFT/RIGHT/FULL."""
    try:
        base, edges = join_algebra.extract(tree)
    except join_algebra.NotAnalyzable:
        return None
    if any(e.side != "" for e in edges):
        return None
    return base, edges, {base} | {e.table for e in edges}


def combined_predicate_equivalent(base_tree: exp.Expression, head_tree: exp.Expression, column_vars: dict) -> bool:
    if not (isinstance(base_tree, exp.Select) and isinstance(head_tree, exp.Select)):
        return False

    base_extracted = _extract_inner_only(base_tree)
    head_extracted = _extract_inner_only(head_tree)
    if base_extracted is None or head_extracted is None:
        return False
    _, base_edges, base_tables = base_extracted
    _, head_edges, head_tables = head_extracted
    if base_tables != head_tables:
        return False

    base_where = base_tree.args.get("where")
    head_where = head_tree.args.get("where")
    base_combined = _conjunction([e.on for e in base_edges] + ([base_where.this] if base_where else []))
    head_combined = _conjunction([e.on for e in head_edges] + ([head_where.this] if head_where else []))

    return expressions_equivalent(base_combined, head_combined, column_vars)
