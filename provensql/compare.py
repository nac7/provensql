"""
compare(base_sql, head_sql) -> Verdict

Pipeline: Stage 0 (parse/fragment-check) -> Stage 1 (canonicalize) ->
Stage 2 (canonical-string equality) -> schema check -> Stage 3 (SMT proof)
-> Stage 4 (counterexample search) -> UNKNOWN.

Stage 3 is tried before Stage 4 because it's both cheaper (no DB instances
to synthesize or execute) and a stronger claim (an actual proof, not "we
tried some instances and found nothing"). Cases neither stage resolves
fall through to UNKNOWN with a reason code -- undercoverage there is
correct behavior, never a wrong answer.
"""

from provensql.canonicalize import (
    UnsupportedConstruct,
    canonical_string,
    canonicalize,
    output_schema,
    parse,
)
from provensql.catalog import Catalog
from provensql.counterexample import format_witness, search as search_counterexample
from provensql.stage3 import prove_equivalent
from provensql.verdict import Verdict

NO_CATALOG_ASSUMPTION = (
    "no catalog supplied -- witness assumes no NOT NULL/UNIQUE/FK constraints "
    "beyond what the query text itself implies; verify against your actual schema"
)
CATALOG_ASSUMPTION = (
    "catalog covers some but not necessarily all referenced tables/columns; "
    "uncovered ones still fall back to the no-constraints assumption above"
)


def compare(base_sql: str, head_sql: str, catalog: Catalog | None = None) -> Verdict:
    try:
        base_tree = parse(base_sql)
    except UnsupportedConstruct as e:
        return Verdict.unknown(f"base_{e.reason_code}", e.detail)

    try:
        head_tree = parse(head_sql)
    except UnsupportedConstruct as e:
        return Verdict.unknown(f"head_{e.reason_code}", e.detail)

    base_canon = canonicalize(base_tree)
    head_canon = canonicalize(head_tree)

    base_str = canonical_string(base_canon)
    head_str = canonical_string(head_canon)

    if base_str == head_str:
        return Verdict.equivalent(
            "canonical forms identical after qualify + normalize",
            assumptions=(
                "qualify() is best-effort without a real catalog; identifiers may be "
                "left unqualified if resolution failed",
            ),
        )

    base_schema = output_schema(base_canon)
    head_schema = output_schema(head_canon)
    if base_schema is not None and head_schema is not None and base_schema != head_schema:
        return Verdict.schema_change(f"output columns differ: {base_schema} vs {head_schema}")

    try:
        stage3_verdict = prove_equivalent(base_canon, head_canon, catalog=catalog)
    except Exception:
        stage3_verdict = None
    if stage3_verdict is not None:
        return stage3_verdict

    try:
        witness = search_counterexample(base_canon, head_canon, catalog=catalog)
    except Exception:
        witness = None

    if witness is not None:
        assumptions = (CATALOG_ASSUMPTION,) if catalog else (NO_CATALOG_ASSUMPTION,)
        return Verdict.different(
            f"counterexample found (instance '{witness['instance_name']}'): "
            f"base returned {len(witness['base_result'])} rows, "
            f"head returned {len(witness['head_result'])} rows",
            witness=format_witness(witness),
            assumptions=assumptions,
        )

    return Verdict.unknown(
        "no_stage2_match_no_proof_no_counterexample",
        "canonical forms differ; neither Stage 3's proof search nor Stage 4's "
        "counterexample search resolved this",
    )
