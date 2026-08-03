from sqlsense.compare import compare
from sqlsense.verdict import VerdictType


def test_de_morgan_predicate_rewrite_is_equivalent():
    # NOT(a <= 1) vs a > 1 -- true for reals under 3-valued NULL logic:
    # if a is NULL both sides are NULL; otherwise they agree since > and <=
    # partition every non-null real. (sqlglot's own simplify() already
    # normalizes this at Stage 2, so it doesn't reach Stage 3 -- that's a
    # cheaper stage correctly resolving it first, not a test bug.)
    v = compare(
        "SELECT x FROM t WHERE a > 1",
        "SELECT x FROM t WHERE NOT (a <= 1)",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_and_commutativity_is_equivalent():
    v = compare(
        "SELECT x FROM t WHERE a > 1 AND b > 2",
        "SELECT x FROM t WHERE b > 2 AND a > 1",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_coalesce_rewritten_as_case_is_equivalent():
    # 'none' (not a numeric literal) keeps both sides in the same inferred
    # sort as the untyped column `a` -- without a catalog, Stage 3's typing
    # is heuristic just like Stage 4's, and a genuinely type-ambiguous
    # column (compared against nothing, so no type hint) mixed with a
    # numeric literal elsewhere is exactly the case it should abstain on
    # rather than guess. That's a real, separate limitation, not this test's
    # concern.
    v = compare(
        "SELECT COALESCE(a, 'none') AS y FROM t",
        "SELECT CASE WHEN a IS NULL THEN 'none' ELSE a END AS y FROM t",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_case_branches_flipped_is_equivalent():
    # Naively flipping a CASE's branches without accounting for NULL is NOT
    # actually equivalent (when a IS NULL, neither condition matches, so
    # both fall to their own ELSE -- which differ). The correct NULL-safe
    # rewrite folds "a IS NULL" into the flipped condition.
    v = compare(
        "SELECT CASE WHEN a > 0 THEN 'pos' ELSE 'non_pos' END AS y FROM t",
        "SELECT CASE WHEN a <= 0 OR a IS NULL THEN 'non_pos' ELSE 'pos' END AS y FROM t",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_naive_case_flip_ignoring_null_is_not_falsely_proven_equivalent():
    # The tempting-but-wrong rewrite: diverges when a IS NULL (both branches
    # fall to their own ELSE). Must never be certified EQUIVALENT.
    v = compare(
        "SELECT CASE WHEN a > 0 THEN 'pos' ELSE 'non_pos' END AS y FROM t",
        "SELECT CASE WHEN a <= 0 THEN 'non_pos' ELSE 'pos' END AS y FROM t",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_off_by_one_boundary_is_not_falsely_proven_equivalent():
    # a > 1 vs a >= 1 disagree at a == 1 -- Stage 3 must abstain (never a
    # false EQUIVALENT), whatever Stage 4 goes on to decide.
    v = compare(
        "SELECT x FROM t WHERE a > 1",
        "SELECT x FROM t WHERE a >= 1",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_skeleton_mismatch_does_not_engage_stage3():
    # different join structure -- Stage 3 must not even attempt a proof here,
    # this only checks it doesn't crash or misfire into a wrong EQUIVALENT.
    v = compare(
        "SELECT a FROM t1 LEFT JOIN t2 ON t1.id = t2.id WHERE a > 1",
        "SELECT a FROM t1 JOIN t2 ON t1.id = t2.id WHERE a > 1",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_having_rewrite_is_equivalent():
    v = compare(
        "SELECT k, COUNT(*) AS n FROM t GROUP BY k HAVING COUNT(*) > 1 AND k > 0",
        "SELECT k, COUNT(*) AS n FROM t GROUP BY k HAVING k > 0 AND COUNT(*) > 1",
    )
    assert v.type == VerdictType.EQUIVALENT
