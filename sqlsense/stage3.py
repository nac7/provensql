"""
Stage 3: SMT proof of equivalence for the conjunctive fragment.

All-or-nothing by design: if the relational skeleton doesn't match, or any
single WHERE/HAVING/SELECT-list expression can't be proven equivalent, this
abstains entirely (returns None) rather than claim a partial result. A
proof that "9 of 10 SELECT columns match" is not a proof the queries are
equivalent -- it's not a proof of anything.

Everything except FROM/JOIN/WHERE must match via skeleton_signature_sans_joins
(GROUP BY compared as a set, output aliases/positions fixed, ORDER BY/LIMIT
exact) -- that's a hard precondition for all three strategies below, tried
cheapest first:

  1. Exact skeleton match (skeleton_signature, joins included as written).
     Catches the common case: only WHERE/HAVING/SELECT bodies changed.
  2. join_algebra.equivalent -- allows a LEFT<->INNER type substitution
     (catalog-justified) or reordering a chain of plain INNER joins, then
     still requires WHERE to match separately.
  3. pushdown.combined_predicate_equivalent -- when every join on both
     sides is a plain INNER join, folds all ON conditions and WHERE into
     one conjunction per side and proves those equivalent, so a condition
     is free to have moved between an ON clause and WHERE. This subsumes
     (2)'s ON-matching for the fully-inner case but doesn't attempt
     LEFT/RIGHT/FULL reasoning at all -- see pushdown.py for why moving a
     predicate across an outer join is a real semantic change, not a
     refactor.
"""

from sqlglot import exp

from sqlsense import catalog as catalog_module
from sqlsense import join_algebra
from sqlsense import pushdown
from sqlsense import schema_infer as si
from sqlsense import skeleton
from sqlsense.smt import build_column_vars, expressions_equivalent
from sqlsense.verdict import Verdict

SMT_ASSUMPTION = (
    "SMT proof covers WHERE/HAVING/SELECT-list scalar expressions only; column types "
    "are inferred (or catalog-provided) and division-by-zero is not modeled"
)


def _unalias(node: exp.Expression) -> exp.Expression:
    return node.this if isinstance(node, exp.Alias) else node


def _predicate(where_or_having) -> exp.Expression:
    return where_or_having.this if where_or_having is not None else exp.true()


def _distinct_redundant(tree: exp.Select, catalog) -> tuple[bool, str | None]:
    """Is this SELECT's DISTINCT provably a no-op (the rows are already
    distinct without it)? Returns (redundant, assumption). Only ever called
    on the side that HAS a DISTINCT."""
    projected = [_unalias(e) for e in tree.expressions]
    if any(isinstance(e, exp.Star) or getattr(e, "is_star", False) for e in projected):
        return False, None  # SELECT DISTINCT * -- can't reason without the full column list

    # Rule A (no catalog needed): a GROUP BY produces exactly one row per
    # distinct group-key tuple. If every group key is present in the
    # projection, then distinct groups yield distinct output rows, so
    # DISTINCT adds nothing. (The reverse subset does NOT hold: projecting a
    # strict subset of the group keys -- SELECT DISTINCT a ... GROUP BY a, b
    # -- genuinely deduplicates, since one `a` can span many `b` groups.)
    group = tree.args.get("group")
    if group and group.expressions:
        projected_keys = {e.sql(dialect="bigquery", normalize=True) for e in projected}
        group_keys = [g.sql(dialect="bigquery", normalize=True) for g in group.expressions]
        if all(g in projected_keys for g in group_keys):
            return True, None

    # Rule B (catalog): a projected column declared UNIQUE means every row is
    # already distinct.
    if catalog is not None:
        for e in projected:
            if isinstance(e, exp.Column) and catalog.is_unique(e.table, e.name):
                return True, f"{e.table}.{e.name} is UNIQUE per catalog, so DISTINCT is redundant"

    return False, None


def _strip_distinct(tree: exp.Select) -> exp.Select:
    t = tree.copy()
    t.set("distinct", None)
    return t


def _where_matches(base_tree, head_tree, column_vars) -> bool:
    return expressions_equivalent(
        _predicate(base_tree.args.get("where")), _predicate(head_tree.args.get("where")), column_vars
    )


def _joins_and_where_match(base_tree, head_tree, column_vars, catalog) -> tuple[bool, list[str]]:
    base_skel = skeleton.skeleton_signature(base_tree)
    head_skel = skeleton.skeleton_signature(head_tree)
    if base_skel is not None and base_skel == head_skel:
        return _where_matches(base_tree, head_tree, column_vars), []

    join_ok, join_assumptions = join_algebra.equivalent(base_tree, head_tree, column_vars, catalog)
    if join_ok and _where_matches(base_tree, head_tree, column_vars):
        return True, join_assumptions

    if pushdown.combined_predicate_equivalent(base_tree, head_tree, column_vars):
        return True, []

    return False, []


def prove_equivalent(
    base_tree: exp.Expression,
    head_tree: exp.Expression,
    catalog: catalog_module.Catalog | None = None,
) -> Verdict | None:
    if not (isinstance(base_tree, exp.Select) and isinstance(head_tree, exp.Select)):
        return None

    # If one side has DISTINCT and the other doesn't, that's only a no-op
    # refactor when the DISTINCT is provably redundant; otherwise it changes
    # results (deduplication) and we must not treat the two as equivalent.
    distinct_assumptions: list[str] = []
    base_distinct = base_tree.args.get("distinct") is not None
    head_distinct = head_tree.args.get("distinct") is not None
    if base_distinct != head_distinct:
        bearer = base_tree if base_distinct else head_tree
        redundant, assumption = _distinct_redundant(bearer, catalog)
        if not redundant:
            return None
        if assumption:
            distinct_assumptions.append(assumption)
        base_tree = _strip_distinct(base_tree)
        head_tree = _strip_distinct(head_tree)

    base_skel_sans_joins = skeleton.skeleton_signature_sans_joins(base_tree)
    head_skel_sans_joins = skeleton.skeleton_signature_sans_joins(head_tree)
    if base_skel_sans_joins is None or base_skel_sans_joins != head_skel_sans_joins:
        return None

    base_schema = si.infer([base_tree])
    head_schema = si.infer([head_tree])
    table_schemas = si.merge_table_schemas(base_schema, head_schema)
    try:
        table_schemas = catalog_module.apply(table_schemas, catalog)
    except catalog_module.CatalogTypeUnsupported:
        return None

    try:
        column_vars = build_column_vars(table_schemas)

        joins_ok, join_assumptions = _joins_and_where_match(base_tree, head_tree, column_vars, catalog)
        if not joins_ok:
            return None

        if not expressions_equivalent(
            _predicate(base_tree.args.get("having")), _predicate(head_tree.args.get("having")), column_vars
        ):
            return None

        base_exprs = base_tree.expressions
        head_exprs = head_tree.expressions
        if len(base_exprs) != len(head_exprs):
            return None  # schema check upstream should already have caught this
        for be, he in zip(base_exprs, head_exprs):
            if not expressions_equivalent(_unalias(be), _unalias(he), column_vars):
                return None
    except Exception:
        return None  # any internal failure abstains -- never guess

    assumptions = (SMT_ASSUMPTION, *join_assumptions, *distinct_assumptions)
    return Verdict.equivalent(
        "SMT-proved: relational skeleton matched (exactly, via a justified join-type "
        "substitution, or via WHERE/ON predicate pushdown for an all-INNER join), all "
        "HAVING/SELECT expressions proven logically equivalent under 3-valued NULL semantics",
        assumptions=assumptions,
    )
