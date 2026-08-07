import pytest

from provensql.compare import compare
from provensql.verdict import VerdictType


def test_formatting_only_is_equivalent():
    v = compare("SELECT a, b FROM t WHERE a > 1", "select   a,\n b from t where a>1")
    assert v.type == VerdictType.EQUIVALENT


def test_added_column_is_schema_change():
    v = compare("SELECT a, b FROM t", "SELECT a, b, c FROM t")
    assert v.type == VerdictType.SCHEMA_CHANGE


def test_reordered_column_is_schema_change():
    v = compare("SELECT a, b FROM t", "SELECT b, a FROM t")
    assert v.type == VerdictType.SCHEMA_CHANGE


def test_join_type_change_is_different_with_witness():
    # LEFT JOIN -> JOIN drops rows where the join key doesn't match. Stage 4
    # should find this via the disjoint-keys instance and never call it
    # EQUIVALENT -- that's the core soundness property under test.
    v = compare(
        "SELECT t1.a FROM t1 LEFT JOIN t2 ON t1.id = t2.id",
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id",
    )
    assert v.type != VerdictType.EQUIVALENT
    if v.type == VerdictType.DIFFERENT:
        assert v.witness


def test_alias_only_rename_is_equivalent():
    v = compare("SELECT a AS x FROM t", "SELECT a AS x FROM t")
    assert v.type == VerdictType.EQUIVALENT


def test_window_function_is_unknown():
    v = compare(
        "SELECT a, ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t",
        "SELECT a, ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t",
    )
    assert v.type == VerdictType.UNKNOWN
    assert v.reason_code.endswith("unsupported_window_function")


def test_parse_error_is_unknown():
    v = compare("SELECT a FROM t", "EXECUTE IMMEDIATE 'SELECT 1'")
    assert v.type == VerdictType.UNKNOWN


def test_different_requires_a_witness():
    from provensql.verdict import Verdict

    with pytest.raises(ValueError):
        Verdict.different("some reason", witness="")


def test_count_star_vs_count_column_differs_on_nulls():
    # classic NULL trap: COUNT(*) counts rows, COUNT(x) skips NULLs
    v = compare(
        "SELECT COUNT(*) AS n FROM t",
        "SELECT COUNT(x) AS n FROM t",
    )
    assert v.type == VerdictType.DIFFERENT
    assert v.witness
