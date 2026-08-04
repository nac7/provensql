"""
Stage 3: SMT proof of equivalence for the conjunctive fragment.

All-or-nothing by design: if the relational skeleton doesn't match, or any
single WHERE/HAVING/SELECT-list expression can't be proven equivalent, this
abstains entirely (returns None) rather than claim a partial result. A
proof that "9 of 10 SELECT columns match" is not a proof the queries are
equivalent -- it's not a proof of anything.

Skeleton matching is two-tiered:
  1. skeleton_signature: exact string match of tables/joins/group-by/order/
     limit. Cheap, catches the common case (only WHERE/HAVING/SELECT
     bodies changed).
  2. If that fails: skeleton_signature_sans_joins (same, but ignoring FROM/
     JOIN) plus join_algebra.equivalent, which allows a join's ON condition
     to be logically-but-not-syntactically equivalent, and a LEFT<->INNER
     type change when the catalog proves it's a no-op. See join_algebra.py
     for why that's sound.
"""

from sqlglot import exp

from sqlsense import catalog as catalog_module
from sqlsense import join_algebra
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


def _skeletons_match(base_tree, head_tree, column_vars, catalog) -> tuple[bool, list[str]]:
    base_skel = skeleton.skeleton_signature(base_tree)
    head_skel = skeleton.skeleton_signature(head_tree)
    if base_skel is not None and base_skel == head_skel:
        return True, []

    base_skel2 = skeleton.skeleton_signature_sans_joins(base_tree)
    head_skel2 = skeleton.skeleton_signature_sans_joins(head_tree)
    if base_skel2 is None or base_skel2 != head_skel2:
        return False, []

    return join_algebra.equivalent(base_tree, head_tree, column_vars, catalog)


def prove_equivalent(
    base_tree: exp.Expression,
    head_tree: exp.Expression,
    catalog: catalog_module.Catalog | None = None,
) -> Verdict | None:
    if not (isinstance(base_tree, exp.Select) and isinstance(head_tree, exp.Select)):
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

        skeleton_ok, join_assumptions = _skeletons_match(base_tree, head_tree, column_vars, catalog)
        if not skeleton_ok:
            return None

        if not expressions_equivalent(
            _predicate(base_tree.args.get("where")), _predicate(head_tree.args.get("where")), column_vars
        ):
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

    assumptions = (SMT_ASSUMPTION, *join_assumptions)
    return Verdict.equivalent(
        "SMT-proved: relational skeleton matched (exactly, or via a justified join-type "
        "substitution), all WHERE/HAVING/SELECT expressions proven logically equivalent "
        "under 3-valued NULL semantics",
        assumptions=assumptions,
    )
