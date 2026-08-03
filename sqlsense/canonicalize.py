"""
Stage 0 (parse + fragment check) and Stage 1 (canonicalize) for sqlsense v0.

Scope is deliberately narrow (see project spec): single-statement SELECT
with FROM/JOIN, WHERE, GROUP BY/HAVING, ORDER BY/LIMIT, non-recursive CTEs,
UNION/INTERSECT/EXCEPT, subqueries, flat scalar types. Anything outside
that -- window functions, recursive CTEs, DML/DDL/scripting, nested
ARRAY/STRUCT types, nondeterministic functions -- raises UnsupportedConstruct
with a machine-readable reason_code, which the caller turns into an
UNKNOWN verdict. Refusing loudly here is what keeps Stage 2 sound: we would
rather say UNKNOWN than silently canonicalize something we don't actually
understand.
"""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.simplify import simplify

DIALECT = "bigquery"

# Functions whose value is not a pure function of their arguments -- comparing
# canonical forms says nothing about equivalence if either side calls one of
# these, so we refuse rather than risk a false EQUIVALENT.
NONDETERMINISTIC_FUNCS = {
    "RAND",
    "CURRENT_TIMESTAMP",
    "CURRENT_DATE",
    "CURRENT_TIME",
    "CURRENT_DATETIME",
    "SESSION_USER",
    "GENERATE_UUID",
    "GENERATE_ARRAY",  # deterministic actually, but excluded pending explicit support
}

UNSUPPORTED_STATEMENT_TYPES = (
    exp.Merge,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Command,
    exp.Alter,
)


class UnsupportedConstruct(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def parse(sql: str) -> exp.Expression:
    """Stage 0. Raises UnsupportedConstruct if the SQL doesn't parse or
    falls outside the supported fragment."""
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception as e:
        raise UnsupportedConstruct("parse_error", str(e)) from e

    if tree is None:
        raise UnsupportedConstruct("parse_error", "empty statement")

    if isinstance(tree, UNSUPPORTED_STATEMENT_TYPES):
        raise UnsupportedConstruct("unsupported_statement_type", type(tree).__name__)

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)):
        raise UnsupportedConstruct("unsupported_statement_type", type(tree).__name__)

    _check_fragment(tree)
    return tree


def _check_fragment(tree: exp.Expression) -> None:
    if list(tree.find_all(exp.Window)):
        raise UnsupportedConstruct("unsupported_window_function")

    for with_ in tree.find_all(exp.With):
        if with_.args.get("recursive"):
            raise UnsupportedConstruct("unsupported_recursive_cte")

    for dt in tree.find_all(exp.DataType):
        if dt.this in (exp.DataType.Type.ARRAY, exp.DataType.Type.STRUCT):
            raise UnsupportedConstruct("nested_type", str(dt.this))

    for func in tree.find_all(exp.Func):
        name = (func.sql_name() if hasattr(func, "sql_name") else type(func).__name__).upper()
        if name in NONDETERMINISTIC_FUNCS:
            raise UnsupportedConstruct("nondeterministic_function", name)

    for anon in tree.find_all(exp.Anonymous):
        name = (anon.name or "").upper()
        if name in NONDETERMINISTIC_FUNCS:
            raise UnsupportedConstruct("nondeterministic_function", name)


def canonicalize(tree: exp.Expression) -> exp.Expression:
    """Stage 1. Applies only transforms that are semantics-preserving on
    their own: identifier qualification, and sqlglot's constant-folding /
    boolean-simplification pass. Each step is independently best-effort --
    if qualify() can't resolve a schema it is silently skipped rather than
    guessed, and a skip on one side just means the two canonical strings
    won't match later (fails toward UNKNOWN, never toward a false
    EQUIVALENT).

    Caveat (tracked, not yet resolved): this trusts that sqlglot's
    simplify() correctly respects SQL's three-valued NULL logic in its
    boolean rewrites. That assumption should get its own targeted test
    before this is used to back any formal soundness claim.
    """
    tree = tree.copy()
    try:
        tree = qualify(
            tree,
            dialect=DIALECT,
            validate_qualify_columns=False,
            quote_identifiers=False,
        )
    except Exception:
        pass
    try:
        tree = simplify(tree)
    except Exception:
        pass
    return tree


def canonical_string(tree: exp.Expression) -> str:
    return tree.sql(dialect=DIALECT, normalize=True, pretty=False)


def output_schema(tree: exp.Expression) -> list[str] | None:
    """Best-effort output column list for schema-change detection.
    Returns None when it can't be determined (e.g. SELECT * without a
    resolved catalog) -- callers must treat None as "don't know", not as
    "no columns"."""
    if isinstance(tree, (exp.Union, exp.Intersect, exp.Except)):
        return output_schema(tree.this)
    if not isinstance(tree, exp.Select):
        return None
    names = []
    for e in tree.selects:
        if isinstance(e, exp.Star) or getattr(e, "is_star", False):
            return None
        names.append(e.alias_or_name)
    return names
