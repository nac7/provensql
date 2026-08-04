from sqlsense.compare import compare
from sqlsense.verdict import VerdictType


def test_predicate_moved_from_where_to_on_is_equivalent():
    v = compare(
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id WHERE t2.status = 'active'",
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id AND t2.status = 'active'",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_predicate_moved_between_two_on_clauses_is_equivalent():
    v = compare(
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id JOIN t3 ON t1.id = t3.id WHERE t2.x = 1",
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id AND t2.x = 1 JOIN t3 ON t1.id = t3.id",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_pushdown_across_outer_join_is_not_falsely_equivalent():
    # the classic accidental-INNER-JOIN bug: moving a filter on the
    # nullable side's column into the ON clause of a LEFT JOIN changes the
    # result (it stops preserving unmatched left rows). Must never be
    # certified EQUIVALENT.
    v = compare(
        "SELECT t1.a FROM t1 LEFT JOIN t2 ON t1.id = t2.id WHERE t2.status = 'active'",
        "SELECT t1.a FROM t1 LEFT JOIN t2 ON t1.id = t2.id AND t2.status = 'active'",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_pushdown_requires_matching_table_set():
    # different tables entirely -- must not be certified EQUIVALENT just
    # because both happen to reduce to some provable conjunction.
    v = compare(
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id WHERE t2.status = 'active'",
        "SELECT t1.a FROM t1 JOIN t3 ON t1.id = t3.id WHERE t3.status = 'active'",
    )
    assert v.type != VerdictType.EQUIVALENT
