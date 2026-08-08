"""
Tests for the Calcite rule-ingestion pilot (mining/calcite_ingest,
docs/calcite_ingest_pilot.md): the Stage-A balanced-paren extractor and the
Stage-B DSL/RexNode-dump translator. These guard the pieces that turn Calcite's
test assertions into auditable SQL; the audit engine itself is covered by
test_error_semantics.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mining" / "calcite_ingest"))

from extract import _split_args, _unquote_java_string  # noqa: E402
from translate import translate_dsl, translate_dump  # noqa: E402
from provensql.error_semantics import error_equivalent  # noqa: E402


def test_split_args_respects_parens_and_strings():
    args = _split_args('isNull(plus(vInt(0), vInt(1))), "OR(a, b)"')
    assert args == ['isNull(plus(vInt(0), vInt(1)))', '"OR(a, b)"']
    assert _unquote_java_string('"null:INTEGER"') == "null:INTEGER"


def test_translate_dsl_arithmetic_and_types():
    # type + nullability carried in the column name
    assert translate_dsl("plus(vInt(), nullInt)") == "(int0 + NULL)"
    assert translate_dsl("div(vIntNotNull(0), literal(2))") == "(notNullInt0 / 2)"
    assert translate_dsl("isNull(plus(vIntNotNull(0), vIntNotNull(1)))") \
        == "((notNullInt0 + notNullInt1)) IS NULL"


def test_translate_dsl_cast_is_passthrough():
    # cast(expr, type) -> expr; the type constructor is ignored
    assert translate_dsl("cast(div(vIntNotNull(), literal(0)), tBigInt())") \
        == "(notNullInt0 / 0)"


def test_translate_dump_handles_type_suffixes_and_refs():
    assert translate_dump("null:INTEGER") == "NULL"
    assert translate_dump("+(?0.int0, 1)") == "(int0 + 1)"
    assert translate_dump("IS NULL(?0.int0)") == "(int0) IS NULL"
    assert translate_dump("CAST(/(?0.notNullInt0, 0)):BIGINT") == "(notNullInt0 / 0)"


def test_out_of_fragment_returns_none():
    # AND/OR/CASE/COALESCE/comparisons and unresolved local refs -> honest skip
    assert translate_dump("OR(IS NULL(?0.int0), IS NULL(?0.int1))") is None
    assert translate_dsl("case_(trueLiteral, vInt(0), vInt(1))") is None
    assert translate_dsl("div(a, one)") is None  # local refs not resolved


def test_translated_pair_audits_as_calcite_asserts():
    # end-to-end on a real pair: Calcite asserts (notNullInt0 + notNullInt1)
    # IS NULL == false; our engine must agree.
    lhs = translate_dsl("isNull(plus(vIntNotNull(0), vIntNotNull(1)))")
    rhs = translate_dump("false")
    assert error_equivalent(lhs, rhs)[0] == "EQUIVALENT_ERR"
