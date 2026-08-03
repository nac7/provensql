"""
Stage 3: SMT proof of equivalence for the conjunctive fragment.

All-or-nothing by design: if the relational skeleton (tables/joins/group-by)
doesn't match exactly, or any single WHERE/HAVING/SELECT-list expression
can't be proven equivalent, this abstains entirely (returns None) rather
than claim a partial result. A proof that "9 of 10 SELECT columns match" is
not a proof the queries are equivalent -- it's not a proof of anything.
"""

from sqlglot import exp

from sqlsense import catalog as catalog_module
from sqlsense import schema_infer as si
from sqlsense import skeleton
from sqlsense.smt import build_column_vars, expressions_equivalent
from sqlsense.verdict import Verdict

SMT_ASSUMPTION = (
    "SMT proof covers WHERE/HAVING/SELECT-list scalar expressions only, under an "
    "identical relational skeleton (same tables/joins/group-by); column types are "
    "inferred (or catalog-provided) and division-by-zero is not modeled"
)


def _unalias(node: exp.Expression) -> exp.Expression:
    return node.this if isinstance(node, exp.Alias) else node


def _predicate(where_or_having) -> exp.Expression:
    return where_or_having.this if where_or_having is not None else exp.true()


def prove_equivalent(
    base_tree: exp.Expression,
    head_tree: exp.Expression,
    catalog: catalog_module.Catalog | None = None,
) -> Verdict | None:
    base_skel = skeleton.skeleton_signature(base_tree)
    head_skel = skeleton.skeleton_signature(head_tree)
    if base_skel is None or head_skel is None or base_skel != head_skel:
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

    return Verdict.equivalent(
        "SMT-proved: identical relational skeleton, all WHERE/HAVING/SELECT expressions "
        "proven logically equivalent under 3-valued NULL semantics",
        assumptions=(SMT_ASSUMPTION,),
    )
