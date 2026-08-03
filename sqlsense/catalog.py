"""
Optional user-supplied catalog: real column types and known UDF names,
loaded from a YAML file.

Without a catalog, sqlsense guesses column types from how the query uses
them (schema_infer.py) and refuses to execute any query calling a function
it doesn't recognize. A catalog is ground truth where it's provided: it
overrides the heuristic type guess for any table it covers, and its UDF
list lets Stage 4 register a deterministic stub for calls that would
otherwise make DuckDB error out (custom BigQuery UDFs like `mozfun.norm.x`
or `project.dataset.udf_js.y` don't exist in DuckDB).

File format:

    tables:
      my_table:
        columns:
          id: INT64
          name: STRING
          created_at: TIMESTAMP
    udfs:
      - mozfun.norm.diff_months
      - moz-fx-data-shared-prod.udf_js.parse_sponsored_interaction

Tables/columns not listed fall back to heuristic inference; this is meant
to be filled in incrementally, not to require a full schema dump.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sqlsense import schema_infer as si

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


def _map_bq_type(bq_type: str) -> str:
    base = bq_type.strip().upper().split("<")[0].split("(")[0]  # ARRAY<...>, STRUCT<...>, NUMERIC(10,2)
    if base in ("ARRAY", "STRUCT", "RECORD", "REPEATED"):
        return NESTED_TYPE
    return _BQ_TYPE_MAP.get(base, si.VARCHAR)


def load(path: str | Path) -> Catalog:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    tables = {}
    for table, spec in (data.get("tables") or {}).items():
        cols = (spec or {}).get("columns") or {}
        tables[table] = {col: _map_bq_type(str(bq_type)) for col, bq_type in cols.items()}

    udfs = {u.strip("`").lower() for u in (data.get("udfs") or [])}

    return Catalog(tables=tables, udfs=udfs)


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
