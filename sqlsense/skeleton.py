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


def skeleton_signature(tree: exp.Expression) -> str | None:
    """None means "not a plain single SELECT" -- Stage 3 v1 doesn't reason
    about UNION/INTERSECT/EXCEPT or bare subqueries at the top level."""
    if not isinstance(tree, exp.Select):
        return None

    skeleton = tree.copy()
    skeleton.set("where", None)
    if skeleton.args.get("having"):
        skeleton.set("having", None)

    # Blank out each projection's expression body but keep its output name
    # and position -- the actual bodies are what Stage 3 proves equivalent
    # separately; the skeleton only needs to pin down that the same number
    # of columns, in the same order, under the same names, are being
    # produced from the same FROM/JOIN/GROUP BY structure.
    placeholders = [exp.column(e.alias_or_name) for e in skeleton.expressions]
    skeleton.set("expressions", placeholders)

    return skeleton.sql(dialect="bigquery", normalize=True)
