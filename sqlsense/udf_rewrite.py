"""
Rewrites calls to catalog-declared UDFs into calls DuckDB can actually run.

BigQuery lets you call a fully-qualified UDF two ways, and sqlglot parses
them into two different shapes:

  `project.dataset.udf_js.f`(a, b)   -> Anonymous(this="project.dataset.udf_js.f", ...)
  mozfun.norm.diff_months(a, b)      -> Dot(Dot(mozfun, norm), Anonymous(this="diff_months", ...))

Either way, DuckDB has no idea what the function is. Since we don't know
its real logic and don't need to -- the same UDF is called in both base and
head, so what matters for equivalence-checking is that our stand-in is
deterministic and (almost certainly) injective in its arguments -- each
matched call gets rewritten to a flat, sanitized name and paired with a
same-arity DuckDB macro (see execute.register_udf_macros) that returns a
string built from the function name and its stringified arguments. If the
UDF call is identical on both sides, the stub cancels out and contributes
nothing to a divergence. If the arguments differ, the stub will (almost
always) differ too, which is exactly the divergence-detection behavior
Stage 4 wants. A stub collision could only cause an under-detection (a
missed counterexample, falling through to UNKNOWN) -- never a false
DIFFERENT or a false EQUIVALENT.
"""

import re

from sqlglot import exp

_SANITIZE = re.compile(r"[^a-z0-9_]+")


def _qualified_name(anon: exp.Anonymous) -> tuple[str, exp.Expression]:
    """Returns (dotted_name_lowercase, node_to_replace)."""
    parent = anon.parent
    if isinstance(parent, exp.Dot) and parent.expression is anon:
        prefix = parent.this.sql(dialect="bigquery").strip("`")
        name = f"{prefix}.{anon.name}".lower()
        return name, parent
    raw = anon.name or ""
    return raw.strip("`").lower(), anon


def sanitize(qualified_name: str, arity: int) -> str:
    base = _SANITIZE.sub("_", qualified_name.lower()).strip("_")
    return f"udf_{base}_arity{arity}"


def rewrite(tree: exp.Expression, udf_names: set[str]) -> tuple[exp.Expression, set[tuple[str, int]]]:
    """Returns (rewritten_tree_copy, {(sanitized_macro_name, arity), ...})."""
    if not udf_names:
        return tree, set()

    tree = tree.copy()
    needed: set[tuple[str, int]] = set()

    for anon in list(tree.find_all(exp.Anonymous)):
        qualified, node_to_replace = _qualified_name(anon)
        if qualified not in udf_names:
            continue
        arity = len(anon.expressions)
        macro_name = sanitize(qualified, arity)
        needed.add((macro_name, arity))
        replacement = exp.Anonymous(this=macro_name, expressions=list(anon.expressions))
        node_to_replace.replace(replacement)

    return tree, needed
