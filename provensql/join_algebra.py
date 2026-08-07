"""
Join-structure comparison for Stage 3, one level past skeleton.py's exact
string match.

Same base table required; the joined tables can differ in two ways and
still be proven equivalent:

  1. Positional match (same join sequence): an ON condition that's
     logically-but-not-syntactically the same (reuses
     smt.expressions_equivalent -- e.g. `a.id = b.id` vs `b.id = a.id`),
     and/or a join's type changing between LEFT and INNER, if the catalog
     proves the NULL-padding case can never actually happen: the outer
     row's join key is NOT NULL and has a foreign key to a column the
     catalog declares UNIQUE. Under that guarantee every outer row finds
     exactly one match, so LEFT JOIN and INNER JOIN produce byte-identical
     results -- not just "usually the same," provably identical for any
     data. That's also why it's safe regardless of what other joins come
     after this one: the one behavioral difference between LEFT and INNER
     (unmatched rows) never triggers, so there's nothing for later joins to
     see differently.

  2. Set match (reordered join sequence), tried only when every join on
     BOTH sides is a plain INNER join: a chain of INNER joins is
     associative and commutative under bag semantics, so any permutation
     with the same set of (table, on-condition) pairs is guaranteed to
     produce the same result -- no catalog needed, this is a pure
     relational-algebra fact. This never applies when a LEFT/RIGHT/FULL
     join is present anywhere in either chain: an outer join is not
     associative with whatever follows it, so reordering across one can
     change results even with identical (table, type, on) triples in each
     position.

RIGHT/FULL join-type substitution, self-joins (the same table joined
twice), and reordering that mixes outer joins are all out of scope for v1;
each causes this module to report no match, which is always the safe (if
less powerful) answer -- Stage 3 just falls through to Stage 4.
"""

from dataclasses import dataclass

from sqlglot import exp

from provensql import catalog as catalog_module
from provensql.smt import expressions_equivalent


class NotAnalyzable(Exception):
    """This query's FROM/JOIN shape is outside what this module reasons
    about (a joined relation isn't a plain table, a join has no ON
    condition, etc). Callers must treat this as "no match found," not an
    error."""


@dataclass
class JoinEdge:
    side: str  # "" (inner), "LEFT", "RIGHT", "FULL"
    table: str
    on: exp.Expression


def _table_name(node: exp.Expression) -> str:
    if not isinstance(node, exp.Table):
        raise NotAnalyzable("joined relation is not a plain table (subquery/derived table)")
    return node.name


def extract(tree: exp.Expression) -> tuple[str, list[JoinEdge]]:
    if not isinstance(tree, exp.Select):
        raise NotAnalyzable("not a plain SELECT")
    from_ = tree.args.get("from_")
    if from_ is None:
        raise NotAnalyzable("no FROM clause")
    base = _table_name(from_.this)

    edges = []
    for j in tree.args.get("joins") or []:
        table = _table_name(j.this)
        kind = (j.kind or "").upper()
        if kind and kind != "INNER":
            raise NotAnalyzable(f"unsupported join kind: {kind}")  # e.g. CROSS
        on = j.args.get("on")
        if on is None:
            raise NotAnalyzable("join without a plain ON condition (USING/NATURAL/CROSS)")
        side = (j.side or "").upper()
        if side not in ("", "LEFT", "RIGHT", "FULL"):
            raise NotAnalyzable(f"unsupported join side: {side}")
        edges.append(JoinEdge(side=side, table=table, on=on))
    return base, edges


def _simple_eq_columns(on: exp.Expression):
    """If `on` is exactly `a.col = b.col`, returns ((table_a, col_a), (table_b, col_b));
    else None -- anything more complex than a single equality is out of scope
    for the join-type-substitution check (the ON-condition-equivalence check
    elsewhere handles arbitrary predicates fine; this one specifically needs
    to identify "the" join key to look up in the catalog)."""
    if not isinstance(on, exp.EQ):
        return None
    left, right = on.this, on.expression
    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return None
    return (left.table, left.name), (right.table, right.name)


def _left_inner_justified(on: exp.Expression, joined_table: str, catalog: catalog_module.Catalog | None):
    """Returns an assumption string if catalog proves LEFT and INNER are
    interchangeable for this join, else None."""
    if catalog is None:
        return None
    cols = _simple_eq_columns(on)
    if cols is None:
        return None
    (t1, c1), (t2, c2) = cols
    if t2 == joined_table:
        outer_table, outer_col, inner_table, inner_col = t1, c1, t2, c2
    elif t1 == joined_table:
        outer_table, outer_col, inner_table, inner_col = t2, c2, t1, c1
    else:
        return None  # neither side of the equality is the newly-joined table

    if not catalog.is_not_null(outer_table, outer_col):
        return None
    if catalog.fk_target(outer_table, outer_col) != (inner_table, inner_col):
        return None
    if not catalog.is_unique(inner_table, inner_col):
        return None

    return (
        f"{outer_table}.{outer_col} is NOT NULL with a foreign key to "
        f"{inner_table}.{inner_col} (declared UNIQUE) per catalog -- every row always "
        f"finds exactly one match, so LEFT JOIN and INNER JOIN are identical here"
    )


def equivalent(
    base_tree: exp.Expression,
    head_tree: exp.Expression,
    column_vars: dict,
    catalog: catalog_module.Catalog | None,
) -> tuple[bool, list[str]]:
    """Returns (matched, assumptions_used). Never raises -- any internal
    NotAnalyzable is caught and reported as no match."""
    try:
        base_base, base_edges = extract(base_tree)
        head_base, head_edges = extract(head_tree)
    except NotAnalyzable:
        return False, []

    if base_base != head_base or len(base_edges) != len(head_edges):
        return False, []

    ok, assumptions = _positional_match(base_edges, head_edges, column_vars, catalog)
    if ok:
        return True, assumptions

    # Positional matching failed -- if every join on both sides is a plain
    # INNER join, try matching by table identity instead of position. A
    # chain of INNER joins is associative and commutative (bag semantics),
    # so any permutation with the same set of (table, on-condition) pairs
    # produces the same result -- no catalog needed, this is a pure
    # relational-algebra fact. This does NOT extend to LEFT/RIGHT/FULL: an
    # outer join is not associative with whatever follows it, so reordering
    # across one can change results even with identical (table, type, on)
    # triples in each position.
    if all(e.side == "" for e in base_edges) and all(e.side == "" for e in head_edges):
        return _set_match(base_edges, head_edges, column_vars)

    return False, []


def _positional_match(
    base_edges: list[JoinEdge],
    head_edges: list[JoinEdge],
    column_vars: dict,
    catalog: catalog_module.Catalog | None,
) -> tuple[bool, list[str]]:
    assumptions = []
    for be, he in zip(base_edges, head_edges):
        if be.table != he.table:
            return False, []

        if not expressions_equivalent(be.on, he.on, column_vars):
            return False, []

        if be.side == he.side:
            continue
        if {be.side, he.side} != {"", "LEFT"}:
            return False, []  # only LEFT<->INNER substitution is supported in v1
        justification = _left_inner_justified(be.on, be.table, catalog)
        if justification is None:
            return False, []
        assumptions.append(justification)

    return True, assumptions


def _set_match(base_edges: list[JoinEdge], head_edges: list[JoinEdge], column_vars: dict) -> tuple[bool, list[str]]:
    base_by_table: dict[str, JoinEdge] = {}
    for e in base_edges:
        if e.table in base_by_table:
            return False, []  # same table joined twice (self-join/alias) -- out of scope for v1
        base_by_table[e.table] = e

    head_by_table: dict[str, JoinEdge] = {}
    for e in head_edges:
        if e.table in head_by_table:
            return False, []
        head_by_table[e.table] = e

    if set(base_by_table) != set(head_by_table):
        return False, []

    for table, be in base_by_table.items():
        he = head_by_table[table]
        if not expressions_equivalent(be.on, he.on, column_vars):
            return False, []

    return True, []
