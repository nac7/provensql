"""
Stage 4: counterexample search.

Tries a handful of adversarial small instances (see data_gen.py) against
both queries in DuckDB and looks for a database instance where the result
multisets differ. The first divergence found is returned as a witness; if
none of the instances trigger a divergence, this returns None and the
caller falls through to UNKNOWN -- absence of a counterexample in this
search is not a proof of equivalence, only a failure to find one.
"""

from collections import Counter

import duckdb
from sqlglot import exp

from sqlsense import catalog as catalog_module
from sqlsense import data_gen, execute
from sqlsense import schema_infer as si
from sqlsense import udf_rewrite

MAX_INSTANCES = 20


def search(
    base_tree: exp.Expression,
    head_tree: exp.Expression,
    catalog: catalog_module.Catalog | None = None,
) -> dict | None:
    all_tables = si.base_tables(base_tree) | si.base_tables(head_tree)
    if not all_tables:
        return None  # nothing to instantiate against (e.g. constant-only query)

    base_schema = si.infer([base_tree])
    head_schema = si.infer([head_tree])
    table_schemas = si.merge_table_schemas(base_schema, head_schema)
    # infer() also picks up columns qualified by CTE aliases (correct SQL --
    # `SELECT x FROM my_cte` qualifies x as my_cte.x); those aren't base
    # tables and must not get a synthetic table of their own, so restrict to
    # the base-table set computed above.
    table_schemas = {t: cols for t, cols in table_schemas.items() if t in all_tables}
    for t in all_tables:
        table_schemas.setdefault(t, {})

    try:
        table_schemas = catalog_module.apply(table_schemas, catalog)
    except catalog_module.CatalogTypeUnsupported:
        return None  # a referenced column is ARRAY/STRUCT-typed; can't synthesize that

    udf_names = catalog.udfs if catalog else set()
    base_rewritten, base_macros = udf_rewrite.rewrite(base_tree, udf_names)
    head_rewritten, head_macros = udf_rewrite.rewrite(head_tree, udf_names)
    needed_macros = base_macros | head_macros

    try:
        base_duck = execute.to_duckdb_sql(base_rewritten)
        head_duck = execute.to_duckdb_sql(head_rewritten)
    except Exception:
        return None

    for name, instance in data_gen.build_instances(table_schemas)[:MAX_INSTANCES]:
        con = duckdb.connect(":memory:")
        try:
            if needed_macros:
                execute.register_udf_macros(con, needed_macros)
            execute.load_instance(con, table_schemas, instance)
            base_rows = execute.run_query(con, base_duck)
            head_rows = execute.run_query(con, head_duck)
        except Exception:
            continue
        finally:
            con.close()

        if Counter(base_rows) != Counter(head_rows):
            return {
                "instance_name": name,
                "tables": instance,
                "table_schemas": table_schemas,
                "base_result": base_rows,
                "head_result": head_rows,
            }

    return None


def format_witness(witness: dict) -> str:
    lines = [f"instance: {witness['instance_name']}"]
    for table, rows in witness["tables"].items():
        cols = list(witness["table_schemas"].get(table, {}).keys())
        lines.append(f"  {table}({', '.join(cols) or '(no columns referenced)'})")
        for row in rows[:6]:
            lines.append(f"    {row}")
        if len(rows) > 6:
            lines.append(f"    ... ({len(rows) - 6} more rows)")
    lines.append(f"  base  result ({len(witness['base_result'])} rows): {witness['base_result'][:5]}")
    lines.append(f"  head  result ({len(witness['head_result'])} rows): {witness['head_result'][:5]}")
    return "\n".join(lines)
