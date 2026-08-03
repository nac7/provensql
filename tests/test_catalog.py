import tempfile
from pathlib import Path

from sqlsense import catalog as catalog_module
from sqlsense.compare import compare
from sqlsense.verdict import VerdictType

CATALOG_YAML = """
udfs:
  - mozfun.norm.diff_months
"""


def _write_catalog(tmp_path: Path) -> Path:
    p = tmp_path / "catalog.yml"
    p.write_text(CATALOG_YAML, encoding="utf-8")
    return p


def test_unknown_udf_without_catalog_is_unknown(tmp_path):
    # DuckDB has no idea what mozfun.norm.diff_months is, so without a
    # catalog Stage 4 can't execute this at all -- should degrade to
    # UNKNOWN, never a wrong guess.
    v = compare(
        "SELECT mozfun.norm.diff_months(a, b) AS m FROM t",
        "SELECT mozfun.norm.diff_months(a, c) AS m FROM t",
    )
    assert v.type == VerdictType.UNKNOWN


def test_catalog_udf_stub_unblocks_counterexample_search(tmp_path):
    cat = catalog_module.load(_write_catalog(tmp_path))
    # same UDF, but called on a different argument on the head side -- the
    # deterministic stub should make this resolvable as DIFFERENT.
    v = compare(
        "SELECT mozfun.norm.diff_months(a, b) AS m FROM t",
        "SELECT mozfun.norm.diff_months(a, c) AS m FROM t",
        catalog=cat,
    )
    assert v.type == VerdictType.DIFFERENT
    assert v.witness


def test_catalog_udf_identical_call_stays_equivalent_at_stage2(tmp_path):
    cat = catalog_module.load(_write_catalog(tmp_path))
    # identical UDF call on both sides never even needs Stage 4 --
    # canonical forms already match.
    v = compare(
        "SELECT mozfun.norm.diff_months(a, b) AS m FROM t",
        "select mozfun.norm.diff_months(a, b) as m from t",
        catalog=cat,
    )
    assert v.type == VerdictType.EQUIVALENT


def test_catalog_column_type_override(tmp_path):
    p = tmp_path / "catalog.yml"
    p.write_text(
        """
tables:
  t:
    columns:
      x: ARRAY<STRING>
""",
        encoding="utf-8",
    )
    cat = catalog_module.load(p)
    # x is catalog-declared as ARRAY -- Stage 4 must refuse rather than
    # synthesize nonsense flat data for a nested column.
    v = compare(
        "SELECT a FROM t WHERE x = 'foo'",
        "SELECT a FROM t WHERE x = 'bar'",
        catalog=cat,
    )
    assert v.type == VerdictType.UNKNOWN
