"""DuckDB execution backend for Stage 4. BigQuery SQL is rendered in
DuckDB dialect via sqlglot; DATE-typed columns are stored as VARCHAR to
sidestep strict date parsing on synthetic string values (a documented v0
simplification -- equality/ordering comparisons still work correctly).

Real BigQuery queries almost always fully qualify tables as
`project.dataset.table`. Transpiled verbatim, that becomes DuckDB's
"project"."dataset"."table" catalog.schema.table addressing -- which won't
resolve, since our synthetic tables are created unqualified in DuckDB's
default catalog. So table references are stripped down to their bare leaf
name before rendering; schema_infer already keys everything by leaf name
for the same reason."""

from sqlglot import exp

from sqlsense import schema_infer as si

_DUCK_TYPE = {
    si.NUMERIC: "DOUBLE",
    si.VARCHAR: "VARCHAR",
    si.DATE: "VARCHAR",
    si.BOOLEAN: "BOOLEAN",
}


def to_duckdb_sql(tree: exp.Expression) -> str:
    tree = tree.copy()
    excluded = si.cte_names(tree)
    for t in tree.find_all(exp.Table):
        if t.name and t.name.lower() not in excluded:
            t.set("catalog", None)
            t.set("db", None)

    # bigquery-etl and friends heavily use query parameters (@submission_date
    # etc, parsed by sqlglot as exp.Parameter) for scheduled runs. DuckDB's
    # .execute() would otherwise treat these as unbound prepared-statement
    # params and refuse to run. Substituting a fixed literal is just what
    # unblocks execution for the counterexample search -- it has no bearing
    # on soundness, since we never conclude EQUIVALENT from an executed
    # query, only DIFFERENT from an observed divergence.
    for p in tree.find_all(exp.Parameter):
        p.replace(exp.Literal.string("2020-01-01"))

    return tree.sql(dialect="duckdb")


def load_instance(con, table_schemas: dict, instance: dict) -> None:
    for table, cols in table_schemas.items():
        col_names = list(cols.keys())
        if col_names:
            col_defs = ", ".join(f'"{c}" {_DUCK_TYPE[cols[c]["type"]]}' for c in col_names)
        else:
            # a referenced table with no inferred columns (e.g. only used via
            # SELECT *) -- give it one throwaway column so CREATE TABLE is valid
            col_defs = '"_sqlsense_placeholder" VARCHAR'
            col_names = ["_sqlsense_placeholder"]
        con.execute(f'CREATE OR REPLACE TABLE "{table}" ({col_defs})')

        rows = instance.get(table, [])
        if not rows:
            continue
        quoted_cols = ", ".join(f'"{c}"' for c in col_names)
        placeholders = ", ".join("?" for _ in col_names)
        for row in rows:
            values = [row.get(c) for c in col_names]
            con.execute(
                f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})',
                values,
            )


def run_query(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def register_udf_macros(con, macros: set[tuple[str, int]]) -> None:
    """Registers one deterministic stand-in per (name, arity) -- see
    udf_rewrite.py for why a string-concat stub is sound here even though
    it doesn't reproduce the UDF's real behavior."""
    for name, arity in macros:
        params = [f"p{i}" for i in range(arity)]
        if params:
            parts = " || '|' || ".join(f"COALESCE(CAST({p} AS VARCHAR), 'NULL')" for p in params)
            body = f"'{name}:' || {parts}"
        else:
            body = f"'{name}'"
        con.execute(f'CREATE OR REPLACE MACRO "{name}"({", ".join(params)}) AS {body}')
