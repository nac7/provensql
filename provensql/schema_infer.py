"""
Best-effort schema inference for Stage 4 (counterexample search).

There is no real catalog in v0, so column types and the pool of
"interesting" literal values are inferred purely from how each column is
used in the query text: what it's compared against, what it's cast to,
whether it appears in arithmetic. This is inherently a heuristic, not a
proof -- a generated instance is only guaranteed to be a valid *some*
schema consistent with the query, not necessarily the real production
schema. That's why every DIFFERENT verdict carries an explicit assumption
about this (see compare.py). Anything not pinned down by the query defaults
to VARCHAR, since most unclassified columns in real warehouses are
identifiers/labels.
"""

from sqlglot import exp

NUMERIC = "NUMERIC"
VARCHAR = "VARCHAR"
DATE = "DATE"
BOOLEAN = "BOOLEAN"

_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)


def _literal_value(lit: exp.Literal):
    if lit.is_string:
        return lit.this
    try:
        text = lit.this
        return float(text) if "." in text else int(text)
    except (TypeError, ValueError):
        return None


def cte_names(tree: exp.Expression) -> set[str]:
    names = set()
    for with_ in tree.find_all(exp.With):
        for cte in with_.expressions:
            if isinstance(cte, exp.CTE) and cte.alias:
                names.add(cte.alias.lower())
    return names


def base_tables(tree: exp.Expression) -> set[str]:
    """Tables that need synthetic data -- excludes CTE aliases, which the
    query computes itself and DuckDB will resolve from the WITH clause."""
    excluded = cte_names(tree)
    tables = set()
    for t in tree.find_all(exp.Table):
        name = t.name
        if name and name.lower() not in excluded:
            tables.add(name)
    return tables


def _type_from_cast(cast: exp.Cast) -> str | None:
    type_name = str(cast.to).upper()
    if any(k in type_name for k in ("INT", "FLOAT", "NUMERIC", "DOUBLE", "DECIMAL")):
        return NUMERIC
    if any(k in type_name for k in ("DATE", "TIME")):
        return DATE
    if "BOOL" in type_name:
        return BOOLEAN
    if any(k in type_name for k in ("STRING", "VARCHAR", "TEXT", "CHAR")):
        return VARCHAR
    return None


def infer(trees: list[exp.Expression]) -> dict[str, dict[str, dict]]:
    """Returns {table: {column: {"type": str, "literals": set}}}."""
    info: dict[str, dict[str, dict]] = {}

    def ensure(table, col):
        return info.setdefault(table, {}).setdefault(col, {"type": None, "literals": set()})

    def set_type(entry, t):
        if entry["type"] is None:
            entry["type"] = t

    for tree in trees:
        for col in tree.find_all(exp.Column):
            table, name = col.table, col.name
            if not name:
                continue
            # Unqualified columns (table == "") are registered too: qualify()
            # is best-effort without a catalog and often leaves HAVING/WHERE
            # columns bare, and those occurrences carry real type hints
            # (e.g. `k > 0`). They're folded into the qualified column below.
            entry = ensure(table, name)
            parent = col.parent

            if isinstance(parent, _COMPARISONS):
                sibling = parent.right if parent.left is col else parent.left
                if isinstance(sibling, exp.Literal):
                    val = _literal_value(sibling)
                    if val is not None:
                        entry["literals"].add(val)
                    set_type(entry, VARCHAR if sibling.is_string else NUMERIC)

            if isinstance(parent, exp.In):
                for e in parent.expressions:
                    if isinstance(e, exp.Literal):
                        val = _literal_value(e)
                        if val is not None:
                            entry["literals"].add(val)
                        set_type(entry, VARCHAR if e.is_string else NUMERIC)

            if isinstance(parent, exp.Cast) and parent.this is col:
                t = _type_from_cast(parent)
                if t:
                    set_type(entry, t)

            if isinstance(parent, _ARITHMETIC):
                set_type(entry, NUMERIC)

    # Fold hints from unqualified occurrences into the qualified column of the
    # same name, when that name belongs to exactly one table (unambiguous).
    # Ambiguous or unplaceable bare columns are dropped -- Stage 3 then
    # abstains on them, which is sound.
    unqualified = info.pop("", {})
    for name, u_entry in unqualified.items():
        holders = [t for t in info if name in info[t]]
        if len(holders) == 1:
            target = info[holders[0]][name]
            target["literals"] |= u_entry["literals"]
            if target["type"] is None:
                target["type"] = u_entry["type"]

    for cols in info.values():
        for entry in cols.values():
            set_type(entry, VARCHAR)

    return info


def merge_table_schemas(*schemas: dict) -> dict:
    """Union column info across base+head so both queries can run against
    one shared instance."""
    merged: dict[str, dict[str, dict]] = {}
    for schema in schemas:
        for table, cols in schema.items():
            merged_cols = merged.setdefault(table, {})
            for col, entry in cols.items():
                m = merged_cols.setdefault(col, {"type": None, "literals": set()})
                m["literals"] |= entry["literals"]
                if m["type"] is None:
                    m["type"] = entry["type"]
                elif entry["type"] and entry["type"] != m["type"]:
                    # conflicting inferred types across base/head usage -- fall back
                    # to the more permissive VARCHAR rather than guess wrong
                    m["type"] = VARCHAR
    return merged
