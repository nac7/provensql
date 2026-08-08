# provensql — paper positioning (two-track plan)

This is the framing document, not the paper. It fixes the contribution,
related-work stance, and evaluation story so the writing is mechanical.

Decision (2026-08-08): **two papers, sequenced.**

- **Paper 1 — SE / tools + demo track (now).** Built entirely from what
  already exists. Doubles as the citable NIW artifact and puts the ideas on
  record first.
- **Paper 2 — top-tier DB/PL (later).** Requires fragment expansion into
  aggregation + per-rule formal soundness + a fair benchmark comparison. See
  the appendix for exactly what's missing. Paper 1 existing de-risks it.

Why not one paper: a top-tier reviewer rejects the SE-framed version for thin
novelty/benchmarks; an SE/tools venue is satisfied with the current scope.
The requirements are disjoint enough that a single submission is the worst of
both.

---

# Paper 1 (SE / tools + demo)

**Working title:** *provensql: Sound, Catalog-Aware Detection of
Behavior-Changing SQL Edits — and Why LLMs Can't Be Trusted To Do It*

## Problem

Analysts and data engineers edit production SQL constantly. The reviewer's
question is binary and high-stakes: **does this edit change results?** Today
that question is answered by (a) manual eyeballing, or (b) increasingly, an
LLM asked "are these equivalent?". Both silently produce the one error that
matters — declaring a behavior change "safe." An equivalence checker that is
occasionally wrong in the "yes, same" direction is worse than useless: it
launders a regression as reviewed.

## Contributions (all implemented and evaluated)

1. **A sound-by-construction equivalence checker for the constraint-aware
   conjunctive fragment.** Four verdicts (`EQUIVALENT` / `DIFFERENT` /
   `SCHEMA_CHANGE` / `UNKNOWN`); `EQUIVALENT` only ever from a proof (canonical
   or SMT), `DIFFERENT` only ever with an executed witness. Soundness is
   enforced *structurally* (`Verdict.different()` cannot be built without a
   witness), not by convention.
2. **Executable, replayable witnesses.** Every `DIFFERENT` ships a concrete
   database instance, run in DuckDB, on which the two queries diverge — a
   reviewer artifact, not just a boolean.
3. **Catalog-aware refactor proofs.** `LEFT`↔`INNER` substitution,
   `DISTINCT` elimination, `WHERE`↔`ON` pushdown, and join reordering,
   justified by declared `NOT NULL`/`UNIQUE`/`FK` — with every verdict
   printing the assumption it relied on.
4. **Defense-in-depth against our own prover (the runtime backstop).** A
   test-time cross-engine invariant (Stage 3's proof must never contradict
   Stage 4's search) promoted to a *shipped runtime check*: a proven
   `EQUIVALENT` that Stage 4 can refute fails safe to `UNKNOWN`. An
   encoder/solver bug degrades coverage, never soundness. (Novel as an
   engineering contribution; no prior sound-checker paper ships this.)
5. **An honest, adversarial evaluation methodology** — hand-labeled corpus,
   full-corpus coverage ceiling, mutation testing with a Clopper-Pearson
   soundness bound, differential encoder validation, and an LLM baseline —
   including reported *negative* results (coverage ceiling; recall lost by
   removing an unsound normalizer).

## Positioning vs related work

Two camps, and provensql sits deliberately between them:

- **Sound academic checkers — Cosette/HoTTSQL, EQUITAS, SPES, WeTune.** These
  prove equivalence over *aggregation and subquery* rewrites (often under bag
  semantics). provensql does **not** compete on that fragment: measured on the
  standard "Calcite 232" set (§ benchmark_scope.md), 84% of pairs parse but
  only 0.4% fall in provensql's proof fragment — the set is 107/195 aggregation
  and 87/195 subquery rewrites. **This is scope delineation, not a loss.**
  provensql instead targets *change-safety review* with executable witnesses
  and catalog reasoning, which those systems don't emphasize.
  - **New sub-finding worth its own paragraph:** many Calcite "equivalences"
    hold *only under integrity constraints*. Catalog-free, provensql correctly
    returns `DIFFERENT` on 9/232 and names the missing constraint via its
    witness — a diagnostic those provers, which assume the schema's keys,
    don't surface.
- **LLM judges (the incumbent in practice).** Timely and damning: gpt-5
  reaches 85.9% accuracy and 98.1% coverage on real edits, yet emits **2 false
  `EQUIVALENT`** (of 30 equivalence claims) vs provensql's **0** — exactly the
  error class that makes an LLM unusable as a safety gate. This is the headline
  comparison.

## Evaluation (already run; fragment-matched)

- **Real corpus:** 213 hand-labeled + 1,242 full-corpus pairs from
  bigquery-etl. Finding: 76% of real edits are consequential; coverage ceiling
  (~10%) is corpus composition, not proof power.
- **Mutation eval = the fragment-matched capability benchmark.** By
  construction it exercises exactly provensql's rewrite classes; recall on the
  Stage-3 targets (pushdown 100%, join reorder 75%, `DISTINCT` 88%) and 0 false
  `EQUIVALENT` across 511 breaking mutations (Clopper-Pearson 95% upper bound
  ≤ 0.58%). **This replaces the ill-fitting Calcite comparison for the
  capability claim.**
- **Baselines:** string / sqlglot (vacuously sound) and the gpt-5 judge (2
  false `EQUIVALENT`).

## Scope & limitations (stated up front, honestly)

Conjunctive fragment; aggregates opaque; no subquery unnesting, no set-op
reasoning, no window functions; coverage on production ETL bounded by Stage-0
refusal of templated/scripted SQL. Each yields a clean `UNKNOWN`, never a
guess.

## Demo artifact

`provensql diff a.sql b.sql --json` → machine-checkable certificate;
`DIFFERENT` embeds the replayable witness. Live CLI + the audit certificate is
the demo.

---

# Appendix — what Paper 2 (top-tier DB/PL) additionally requires

The scoping made the gap concrete. To be competitive at SIGMOD/VLDB/ICDE/PLDI:

1. **Fragment expansion into aggregation reasoning.** The Calcite/SPES
   benchmark is ~half aggregation rewrites; without modeling `SUM`/`COUNT`/
   `GROUP BY` algebra (not as opaque atoms), coverage there stays ~0. This is
   the single biggest lift and directly overlaps SPES's contribution, so the
   novelty delta must be sharp (e.g. constraint-aware + witness-producing
   aggregation equivalence).
2. **Per-rule formal soundness.** A semi-formal statement of the 3-valued
   `(is_null, value)` encoding and a soundness argument per rewrite rule,
   replacing the current prose + differential-testing evidence.
3. **A fair, named benchmark comparison** — achievable *only after* (1):
   report on the Calcite 232 / Cosette / WeTune SPJ+agg subsets head-to-head
   with SPES/EQUITAS, with the fragment restriction stated.
4. **Subquery unnesting and set-operation reasoning** for breadth.
5. (Optional, strong) mechanized soundness (Lean/Coq) for the core encoding —
   the HoTTSQL lineage sets this bar.

Sequencing: ship Paper 1, then treat (1)+(2) as the Paper 2 research core;
(3) falls out once (1) exists.
