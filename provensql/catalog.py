"""
Optional user-supplied catalog: real column types and known UDF names,
loaded from a YAML file.

Without a catalog, provensql guesses column types from how the query uses
them (schema_infer.py) and refuses to execute any query calling a function
it doesn't recognize. A catalog is ground truth where it's provided: it
overrides the heuristic type guess for any table it covers, and its UDF
list lets Stage 4 register a deterministic stub for calls that would
otherwise make DuckDB error out (custom BigQuery UDFs like `mozfun.norm.x`
or `project.dataset.udf_js.y` don't exist in DuckDB).

File format:

    tables:
      orders:
        columns:
          id: INT64
          customer_id: INT64
        not_null: [customer_id]
        foreign_keys:
          customer_id: customers.id
      customers:
        columns:
          id: INT64
        unique: [id]
    udfs:
      - mozfun.norm.diff_months
      - moz-fx-data-shared-prod.udf_js.parse_sponsored_interaction

Tables/columns not listed fall back to heuristic inference; this is meant
to be filled in incrementally, not to require a full schema dump.

not_null/unique/foreign_keys are used by Stage 3 to justify treating a
LEFT JOIN as equivalent to an INNER JOIN: if orders.customer_id is NOT NULL
and has a foreign key to customers.id, and customers.id is UNIQUE, then
every orders row is guaranteed exactly one match in customers -- the two
join types can never actually produce different results, so a diff that
only changes the join type there is safe. This is exactly the same "print
what you assumed" discipline as everything else catalog-driven in this
project: the assumption is stated in the verdict, not hidden.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from provensql import schema_infer as si

_BQ_TYPE_MAP = {
    "STRING": si.VARCHAR,
    "BYTES": si.VARCHAR,
    "INT64": si.NUMERIC,
    "INTEGER": si.NUMERIC,
    "FLOAT64": si.NUMERIC,
    "FLOAT": si.NUMERIC,
    "NUMERIC": si.NUMERIC,
    "BIGNUMERIC": si.NUMERIC,
    "BOOL": si.BOOLEAN,
    "BOOLEAN": si.BOOLEAN,
    "DATE": si.DATE,
    "DATETIME": si.DATE,
    "TIMESTAMP": si.DATE,
    "TIME": si.DATE,
}

NESTED_TYPE = "NESTED_UNSUPPORTED"


class CatalogTypeUnsupported(Exception):
    """Raised when a query references a catalog column typed ARRAY/STRUCT --
    Stage 4 has no support for synthesizing nested data, so the caller
    should treat this as an UNKNOWN, not guess."""


@dataclass
class Catalog:
    tables: dict[str, dict[str, str]] = field(default_factory=dict)  # table -> {col: our ColumnType}
    udfs: set[str] = field(default_factory=set)  # lowercased fully-qualified names, backticks stripped
    not_null: dict[str, set[str]] = field(default_factory=dict)  # table -> {col, ...}
    unique: dict[str, set[str]] = field(default_factory=dict)  # table -> {col, ...}
    foreign_keys: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)  # table -> {col: (target_table, target_col)}

    def is_not_null(self, table: str, col: str) -> bool:
        return col in self.not_null.get(table, ())

    def is_unique(self, table: str, col: str) -> bool:
        return col in self.unique.get(table, ())

    def fk_target(self, table: str, col: str) -> tuple[str, str] | None:
        return self.foreign_keys.get(table, {}).get(col)


def _map_bq_type(bq_type: str) -> str:
    base = bq_type.strip().upper().split("<")[0].split("(")[0]  # ARRAY<...>, STRUCT<...>, NUMERIC(10,2)
    if base in ("ARRAY", "STRUCT", "RECORD", "REPEATED"):
        return NESTED_TYPE
    return _BQ_TYPE_MAP.get(base, si.VARCHAR)


def load(path: str | Path) -> Catalog:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    tables = {}
    not_null = {}
    unique = {}
    foreign_keys = {}
    for table, spec in (data.get("tables") or {}).items():
        spec = spec or {}
        cols = spec.get("columns") or {}
        tables[table] = {col: _map_bq_type(str(bq_type)) for col, bq_type in cols.items()}

        if spec.get("not_null"):
            not_null[table] = set(spec["not_null"])
        if spec.get("unique"):
            unique[table] = set(spec["unique"])
        if spec.get("foreign_keys"):
            fks = {}
            for col, target in spec["foreign_keys"].items():
                target_table, _, target_col = str(target).rpartition(".")
                if not target_table:
                    raise ValueError(f"foreign_keys.{col}: expected 'table.column', got {target!r}")
                fks[col] = (target_table, target_col)
            foreign_keys[table] = fks

    udfs = {u.strip("`").lower() for u in (data.get("udfs") or [])}

    return Catalog(tables=tables, udfs=udfs, not_null=not_null, unique=unique, foreign_keys=foreign_keys)


def apply(table_schemas: dict, catalog: Catalog | None) -> dict:
    """Override heuristic column types with catalog ground truth wherever
    the catalog covers a table. Raises CatalogTypeUnsupported if a
    referenced column is catalog-typed as ARRAY/STRUCT."""
    if catalog is None:
        return table_schemas

    result = {}
    for table, cols in table_schemas.items():
        catalog_cols = catalog.tables.get(table)
        if not catalog_cols:
            result[table] = cols
            continue
        new_cols = {}
        for col, entry in cols.items():
            if col in catalog_cols:
                if catalog_cols[col] == NESTED_TYPE:
                    raise CatalogTypeUnsupported(f"{table}.{col} is ARRAY/STRUCT-typed")
                new_cols[col] = {"type": catalog_cols[col], "literals": entry["literals"]}
            else:
                new_cols[col] = entry
        result[table] = new_cols
    return result
