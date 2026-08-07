"""
Cross-validation: the proof engine and the counterexample engine must never
contradict each other.

Stage 3 (SMT) proves EQUIVALENT; Stage 4 (DuckDB execution) tries to
disprove it by finding a database instance where the queries diverge. In
normal operation Stage 3 short-circuits before Stage 4 ever runs, so they
never actually check each other. Here we force it: for every pair Stage 3
proves EQUIVALENT, we independently run Stage 4's counterexample search and
assert it finds *nothing*. A witness for a proven-equivalent pair would be
a direct contradiction -- one of the two engines would be unsound, and this
turns that into a loud failure instead of a silent one.
"""

import pytest

from provensql.canonicalize import canonicalize, parse
from provensql.compare import compare
from provensql.counterexample import search as search_counterexample
from provensql.verdict import VerdictType

# Pairs that should be proven EQUIVALENT, spanning every Stage 3 capability.
EQUIVALENT_PAIRS = [
    # scalar / predicate rewrites
    ("SELECT x FROM t WHERE a > 1", "SELECT x FROM t WHERE NOT (a <= 1)"),
    ("SELECT x FROM t WHERE a > 1 AND b > 2", "SELECT x FROM t WHERE b > 2 AND a > 1"),
    ("SELECT COALESCE(a, 'none') AS y FROM t", "SELECT CASE WHEN a IS NULL THEN 'none' ELSE a END AS y FROM t"),
    (
        "SELECT CASE WHEN a > 0 THEN 'pos' ELSE 'np' END AS y FROM t",
        "SELECT CASE WHEN a <= 0 OR a IS NULL THEN 'np' ELSE 'pos' END AS y FROM t",
    ),
    # GROUP BY reordering
    ("SELECT a, b, COUNT(*) AS n FROM t GROUP BY a, b", "SELECT a, b, COUNT(*) AS n FROM t GROUP BY b, a"),
    # inner-join reordering
    (
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id JOIN t3 ON t1.id = t3.id",
        "SELECT t1.a FROM t1 JOIN t3 ON t1.id = t3.id JOIN t2 ON t1.id = t2.id",
    ),
    # WHERE <-> ON pushdown
    (
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id WHERE t2.status = 'active'",
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id AND t2.status = 'active'",
    ),
    # DISTINCT elimination
    ("SELECT DISTINCT a, b FROM t GROUP BY a, b", "SELECT a, b FROM t GROUP BY a, b"),
]


@pytest.mark.parametrize("base_sql, head_sql", EQUIVALENT_PAIRS)
def test_proven_equivalent_has_no_counterexample(base_sql, head_sql):
    verdict = compare(base_sql, head_sql)
    assert verdict.type == VerdictType.EQUIVALENT, f"expected EQUIVALENT, got {verdict.type}"

    # Independently run the counterexample engine on the same (canonicalized)
    # pair. If it finds a witness, the two engines contradict each other.
    base_canon = canonicalize(parse(base_sql))
    head_canon = canonicalize(parse(head_sql))
    witness = search_counterexample(base_canon, head_canon)
    assert witness is None, (
        f"CONTRADICTION: Stage 3 proved EQUIVALENT but Stage 4 found a counterexample:\n{witness}"
    )
