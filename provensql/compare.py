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


def _equivalent_backstop(base_canon, head_canon, catalog: Catalog | None) -> Verdict | None:
    """Defense-in-depth for the one error the whole tool forbids: a false
    EQUIVALENT from a bug in Stage 3's SMT encoder or the solver.

    `test_cross_validation.py` already asserts, at test time, that Stage 3 and
    Stage 4 never contradict each other. This promotes that invariant to a
    *shipped runtime check*: whenever Stage 3 proves EQUIVALENT, we run Stage
    4's counterexample search on the same pair anyway. If Stage 4 hands back a
    real database instance on which the two queries diverge, the proof was
    wrong -- so we fail safe to UNKNOWN (with the contradicting witness)
    instead of returning the false EQUIVALENT. An encoder/solver soundness bug
    thus degrades coverage, never soundness.

    This only fires on the catalog-free path. Stage 4 generates instances over
    the *inferred* schema and does not enforce catalog NOT NULL/UNIQUE/FK
    constraints, so a catalog-justified proof (e.g. LEFT<->INNER via a NOT NULL
    key) could be "refuted" by a Stage-4 instance that violates those very
    constraints -- a spurious contradiction. Without a catalog, Stage 3's
    proofs hold on *every* instance by construction, so any Stage-4 witness is
    a genuine contradiction. Returns a downgrade Verdict on contradiction, else
    None (keep the EQUIVALENT).
    """
    if catalog is not None:
        return None
    try:
        witness = search_counterexample(base_canon, head_canon, catalog=None)
    except Exception:
        return None  # backstop couldn't run -> no refutation, keep the proof
    if witness is None:
        return None
    return Verdict.unknown(
        "stage3_stage4_contradiction",
        "Stage 3 proved EQUIVALENT but the runtime counterexample backstop found a "
        f"diverging instance ('{witness['instance_name']}') -- an internal soundness "
        "contradiction, so the EQUIVALENT was withheld and downgraded to UNKNOWN. "
        "This should never happen; please file a bug with the two queries. Witness:\n"
        + format_witness(witness),
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
        backstop = _equivalent_backstop(base_canon, head_canon, catalog)
        return backstop if backstop is not None else stage3_verdict

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
