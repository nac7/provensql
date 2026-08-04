import tempfile
from pathlib import Path

from sqlsense import catalog as catalog_module
from sqlsense.compare import compare
from sqlsense.verdict import VerdictType

FK_CATALOG_YAML = """
tables:
  orders:
    columns:
      customer_id: INT64
    not_null: [customer_id]
    foreign_keys:
      customer_id: customers.id
  customers:
    columns:
      id: INT64
    unique: [id]
"""


def _write_catalog(tmp_path: Path) -> Path:
    p = tmp_path / "fk_catalog.yml"
    p.write_text(FK_CATALOG_YAML, encoding="utf-8")
    return p


def test_left_join_to_inner_justified_by_fk_catalog(tmp_path):
    cat = catalog_module.load(_write_catalog(tmp_path))
    v = compare(
        "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id",
        "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id",
        catalog=cat,
    )
    assert v.type == VerdictType.EQUIVALENT
    assert any("UNIQUE" in a for a in v.assumptions)


def test_left_join_to_inner_without_catalog_is_not_falsely_equivalent():
    # same rewrite, no catalog -- there's no proof the join key is always
    # matched, so this must not be certified EQUIVALENT.
    v = compare(
        "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id",
        "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_left_join_to_inner_without_unique_target_is_not_falsely_equivalent(tmp_path):
    # NOT NULL + FK present, but the target column isn't declared UNIQUE --
    # a matching row could still fan out to multiple rows, so LEFT and INNER
    # aren't guaranteed identical. Must not be certified EQUIVALENT.
    p = tmp_path / "no_unique_catalog.yml"
    p.write_text(
        """
tables:
  orders:
    columns:
      customer_id: INT64
    not_null: [customer_id]
    foreign_keys:
      customer_id: customers.id
  customers:
    columns:
      id: INT64
""",
        encoding="utf-8",
    )
    cat = catalog_module.load(p)
    v = compare(
        "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id",
        "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id",
        catalog=cat,
    )
    assert v.type != VerdictType.EQUIVALENT


def test_on_condition_side_swap_is_equivalent(tmp_path):
    cat = catalog_module.load(_write_catalog(tmp_path))
    v = compare(
        "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id",
        "SELECT orders.id FROM orders JOIN customers ON customers.id = orders.customer_id",
        catalog=cat,
    )
    assert v.type == VerdictType.EQUIVALENT


def test_group_by_reordering_is_equivalent():
    v = compare(
        "SELECT a, b, COUNT(*) AS n FROM t GROUP BY a, b",
        "SELECT a, b, COUNT(*) AS n FROM t GROUP BY b, a",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_inner_join_chain_reordering_is_equivalent():
    v = compare(
        "SELECT t1.a FROM t1 JOIN t2 ON t1.id = t2.id JOIN t3 ON t1.id = t3.id",
        "SELECT t1.a FROM t1 JOIN t3 ON t1.id = t3.id JOIN t2 ON t1.id = t2.id",
    )
    assert v.type == VerdictType.EQUIVALENT


def test_reordering_with_an_outer_join_present_is_not_falsely_equivalent():
    # one side has t2 as LEFT, the other has it reordered past t3 -- outer
    # joins aren't associative with what follows, so this must not be
    # certified EQUIVALENT even though the same three tables/conditions appear.
    v = compare(
        "SELECT t1.a FROM t1 LEFT JOIN t2 ON t1.id = t2.id JOIN t3 ON t1.id = t3.id",
        "SELECT t1.a FROM t1 JOIN t3 ON t1.id = t3.id LEFT JOIN t2 ON t1.id = t2.id",
    )
    assert v.type != VerdictType.EQUIVALENT


def test_self_join_set_match_abstains_rather_than_guesses():
    # same table joined twice (self-join) collides in the table-name-keyed
    # dict the set-match path uses -- it must abstain (return no match)
    # rather than pick an arbitrary pairing and risk a wrong verdict.
    from sqlsense.canonicalize import canonicalize, parse
    from sqlsense.join_algebra import equivalent as join_equivalent

    base = canonicalize(parse(
        "SELECT a.x FROM t1 JOIN t2 AS a ON t1.id = a.id JOIN t2 AS b ON t1.id2 = b.id"
    ))
    head = canonicalize(parse(
        "SELECT a.x FROM t1 JOIN t2 AS b ON t1.id2 = b.id JOIN t2 AS a ON t1.id = a.id"
    ))
    matched, _ = join_equivalent(base, head, {}, None)
    assert matched is False


def test_different_join_sequence_is_not_falsely_equivalent(tmp_path):
    # v1 doesn't reorder across joins -- a genuinely different join sequence
    # (extra table, different structure) must not slip through as EQUIVALENT.
    cat = catalog_module.load(_write_catalog(tmp_path))
    v = compare(
        "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id",
        "SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id AND customers.id > 5",
        catalog=cat,
    )
    assert v.type != VerdictType.EQUIVALENT
