"""
Relational skeleton matching for Stage 3.

Stage 3 doesn't attempt general query-containment reasoning -- that's a much
larger project (homomorphism search over arbitrary conjunctive queries).
Instead it targets the case the eval corpus showed is actually common:
the FROM/JOIN/GROUP BY/ORDER BY/LIMIT structure is untouched, and only a
WHERE predicate, a HAVING predicate, or a SELECT-list expression changed.
The skeleton signature captures everything except those three things, so
an exact skeleton match tells you it's safe to reason about the changed
expressions in isolation.
"""

from sqlglot import exp


def _sort_group_by(skeleton: exp.Select) -> None:
    # GROUP BY a, b and GROUP BY b, a produce the same grouping/aggregation
    # result -- there's no default row order after grouping for the column
    # order to affect, so sorting it into a canonical order is a strict,
    # unconditional improvement (no join-structure caveats apply here).
    group = skeleton.args.get("group")
    if group and group.expressions:
        sorted_exprs = sorted(group.expressions, key=lambda e: e.sql(dialect="bigquery", normalize=True))
        group.set("expressions", sorted_exprs)


def skeleton_signature(tree: exp.Expression) -> str | None:
    """None means "not a plain single SELECT" -- Stage 3 v1 doesn't reason
    about UNION/INTERSECT/EXCEPT or bare subqueries at the top level."""
    if not isinstance(tree, exp.Select):
        return None

    skeleton = tree.copy()
    skeleton.set("where", None)
    if skeleton.args.get("having"):
        skeleton.set("having", None)
    _sort_group_by(skeleton)

    # Blank out each projection's expression body but keep its output name
    # and position -- the actual bodies are what Stage 3 proves equivalent
    # separately; the skeleton only needs to pin down that the same number
    # of columns, in the same order, under the same names, are being
    # produced from the same FROM/JOIN/GROUP BY structure.
    placeholders = [exp.column(e.alias_or_name) for e in skeleton.expressions]
    skeleton.set("expressions", placeholders)

    return skeleton.sql(dialect="bigquery", normalize=True)


def skeleton_signature_sans_joins(tree: exp.Expression) -> str | None:
    """Same as skeleton_signature, but also blanks FROM/JOIN entirely --
    used as the second half of a two-part check when join_algebra.py is
    doing the join-structure comparison separately (with catalog-aware
    LEFT/INNER substitution and SMT-checked ON conditions instead of an
    exact string match)."""
    if not isinstance(tree, exp.Select):
        return None

    skeleton = tree.copy()
    skeleton.set("where", None)
    if skeleton.args.get("having"):
        skeleton.set("having", None)
    skeleton.set("from_", None)
    skeleton.set("joins", None)
    _sort_group_by(skeleton)

    placeholders = [exp.column(e.alias_or_name) for e in skeleton.expressions]
    skeleton.set("expressions", placeholders)

    return skeleton.sql(dialect="bigquery", normalize=True)
