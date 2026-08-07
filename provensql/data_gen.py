"""
Adversarial small-instance generation for Stage 4.

Each function below seeds one specific bug class from the project spec:
NULLs (three-valued logic traps), duplicate rows (JOIN fan-out / bag vs
set), and empty tables (COUNT(*) vs SUM(x) traps). Instances are kept
small and few in number by design -- this is meant to catch the common,
structural cases fast, not to be an exhaustive fuzzer.
"""

from provensql import schema_infer as si

Row = dict
Instance = dict  # table -> list[Row]


def domain_values(col_type: str, literals: set, salt: str = "col") -> list:
    """salt is the column name. It only affects the no-literals fallback --
    two different columns with no inferred literals must NOT get the same
    generic pool, or a zip-by-index instance would make them perfectly
    correlated in every row, silently hiding any real divergence between
    expressions that use one column vs. the other (a common real-world edit:
    swapping which column feeds a computation). Columns that share a name
    (typical join keys like `id`) deliberately keep sharing a pool, since
    that's what makes join conditions actually match sometimes."""
    literals = [v for v in literals if v is not None]
    salt_n = sum(ord(c) for c in salt) if salt else 0

    if col_type == si.NUMERIC:
        pool = set(v for v in literals if isinstance(v, (int, float)))
        for v in list(pool):
            pool.add(v + 1)
            pool.add(v - 1)
        if pool:
            pool.update([0, 1, -1])
        else:
            base = salt_n % 5
            pool.update([base, base + 1, base + 2])
        return sorted(pool)[:5]

    if col_type == si.DATE:
        pool = {v for v in literals if isinstance(v, str)}
        if pool:
            pool.update(["2020-01-01", "2020-01-02"])
        else:
            day = (salt_n % 3) + 1
            pool.update([f"2020-01-0{day}", f"2020-01-0{day + 1}"])
        return sorted(pool)[:4]

    if col_type == si.BOOLEAN:
        return [True, False]

    # VARCHAR / default identifier-like domain
    pool = {v for v in literals if isinstance(v, str)}
    if pool:
        pool.update(["a", "b"])
    else:
        base = (salt or "col").lower()[:6]
        pool.update([f"{base}_0", f"{base}_1", f"{base}_2"])
    return sorted(pool)[:5]


def _zipped_rows(cols: dict, n: int) -> list[Row]:
    if not cols:
        return [{} for _ in range(n)]
    domains = {c: domain_values(info["type"], info["literals"], salt=c) for c, info in cols.items()}
    rows = []
    for i in range(n):
        rows.append({c: dvals[i % len(dvals)] for c, dvals in domains.items()})
    return rows


def build_instances(table_schemas: dict[str, dict]) -> list[tuple[str, Instance]]:
    """table_schemas: {table: {column: {"type":..., "literals": set}}}"""
    instances: list[tuple[str, Instance]] = []
    tables = list(table_schemas.keys())

    normal = {t: _zipped_rows(table_schemas[t], 4) for t in tables}
    instances.append(("normal", normal))

    with_nulls = {}
    for t in tables:
        null_row = {c: None for c in table_schemas[t]}
        with_nulls[t] = normal[t] + [null_row]
    instances.append(("with_null_row", with_nulls))

    with_dupes = {}
    for t in tables:
        rows = normal[t]
        with_dupes[t] = rows + ([dict(rows[0])] if rows else [])
    instances.append(("with_duplicate_row", with_dupes))

    for t in tables:
        variant = {tt: (rows if tt != t else []) for tt, rows in normal.items()}
        instances.append((f"empty_{t}", variant))

    instances.append(("all_empty", {t: [] for t in tables}))

    # disjoint-keys variant: helps surface INNER vs LEFT JOIN differences when
    # a shared column name is the join key -- shift one table's values so
    # nothing lines up with any other table's.
    if len(tables) >= 2:
        shifted = dict(normal)
        first = tables[0]
        shifted[first] = [
            {c: (f"__disjoint_{i}__" if isinstance(v, str) else (v + 1000 if isinstance(v, (int, float)) else v))
             for c, v in row.items()}
            for i, row in enumerate(normal[first])
        ]
        instances.append((f"disjoint_{first}", shifted))

    return instances
