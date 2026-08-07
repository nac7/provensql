from pathlib import Path

from sqlsense import catalog as catalog_module
from sqlsense.compare import compare
from sqlsense.verdict import VerdictType


def test_distinct_redundant_over_full_group_key_is_equivalent():
    # GROUP BY a, b already yields one row per (a, b); projecting both keys
    # means DISTINCT can't remove anything.
    v = compare(
        "SELECT DISTINCT a, b FROM t GROUP BY a, b",
        "SELECT a, b FROM t GROUP BY a, b",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_distinct_with_aggregate_projection_is_equivalent():
    v = compare(
        "SELECT DISTINCT a, b, COUNT(*) AS n FROM t GROUP BY a, b",
        "SELECT a, b, COUNT(*) AS n FROM t GROUP BY a, b",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_distinct_over_partial_group_key_is_not_falsely_equivalent():
    # projecting only `a` from GROUP BY a, b DOES deduplicate (one `a` spans
    # many `b` groups), so DISTINCT is NOT redundant here.
    v = compare(
        "SELECT DISTINCT a FROM t GROUP BY a, b",
        "SELECT a FROM t GROUP BY a, b",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_distinct_without_group_or_unique_is_not_falsely_equivalent():
    # no GROUP BY, no uniqueness info -- DISTINCT genuinely deduplicates.
    v = compare(
        "SELECT DISTINCT a FROM t",
        "SELECT a FROM t",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_distinct_redundant_over_unique_column_is_equivalent(tmp_path):
    p = tmp_path / "cat.yml"
    p.write_text(
        """
tables:
  t:
    columns:
      id: INT64
    unique: [id]
""",
        encoding="utf-8",
    )
    cat = catalog_module.load(p)
    v = compare("SELECT DISTINCT id FROM t", "SELECT id FROM t", catalog=cat)
    assert v.type == VerdictType.EQUIVALENT
    assert any("UNIQUE" in a for a in v.assumptions)
