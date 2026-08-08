# provensql: Sound, Catalog-Aware Detection of Behavior-Changing SQL Edits — and Why LLMs Can't Be Trusted To Do It

**Draft — SE / tools + demo track.** Author block, venue, and camera-ready
formatting TBD. All numbers below are reproducible from the harness in
`mining/` and `eval/`; see §9.

---

## Abstract

Data engineers change production SQL constantly, and the reviewer's question is
always the same: *does this edit change the result?* Getting it wrong in one
direction — approving an edit that silently alters output — turns a review into
a regression. That failure mode is exactly what the two available options make
easy: manual inspection misses NULL- and join-semantics corner cases, and Large
Language Models, now widely used as ad-hoc equivalence oracles, confidently
declare non-equivalent queries "the same." We present **provensql**, an
open-source tool that decides SQL edit-safety *soundly*: it returns
`EQUIVALENT` only with a proof, `DIFFERENT` only with a concrete database
instance on which the queries diverge, and otherwise abstains (`UNKNOWN`) rather
than guess. Soundness — never a false `EQUIVALENT` — is enforced structurally
and defended at runtime by a cross-engine backstop that downgrades any proof its
counterexample search can refute. On 213 hand-labeled real-world edits provensql
makes **zero** false `EQUIVALENT` claims; across 511 adversarial
equivalence-breaking mutations it again makes zero, giving a Clopper–Pearson 95%
upper bound of **0.58%** on its false-`EQUIVALENT` rate. A state-of-the-art LLM
judge (OpenAI gpt-5) on the same 213 edits reaches 85.9% accuracy but emits
**2 false `EQUIVALENT`s** — the precise error provensql is built to preclude. We
also characterize, honestly, where provensql does *not* apply: on the standard
Calcite/Cosette equivalence benchmarks it proves ~0.4%, because those sets
target aggregation and subquery rewrites outside its constraint-aware
conjunctive fragment. That negative result delineates the tool's niche —
production *change review*, not general query optimization — and yields a
concrete roadmap.

---

## 1. Introduction

A one-line change to a production query — `LEFT JOIN`→`JOIN`, `COUNT(x)`→
`COUNT(*)`, a moved `WHERE` predicate, a flipped `CASE` — either preserves the
result on every database instance or it does not. The reviewer must decide
which, and the cost of the two mistakes is asymmetric. Flagging a safe edit as
risky wastes time; **approving an unsafe edit as safe ships a silent data
regression** that surfaces days later in a dashboard or a downstream model.

Two things answer this question today, and both fail in the expensive
direction:

- **Manual review** relies on a human tracking three-valued NULL logic, bag vs.
  set semantics, and outer-join row multiplicities in their head. These are
  exactly the cases people get wrong.
- **LLM judges.** Asking a capable model "are these two queries equivalent?" is
  now common practice. As we show (§5.4), a state-of-the-art model is *good* —
  and still wrong in the one direction that matters, declaring behavior changes
  equivalent. An oracle that occasionally launders a regression as safe is worse
  than no oracle, because it is trusted.

There is a rich literature on *proving* SQL equivalence — Cosette/HoTTSQL [1],
EQUITAS [2], SPES [3], WeTune [4] — but it targets the query-*optimization*
setting: proving that aggregation and subquery rewrites preserve results, often
under bag semantics. Production change review is a different distribution of
edits and a different set of practical requirements: verdicts a reviewer can
act on (a witness, not just a boolean), reasoning about the integrity
constraints a real schema declares, and — above all — a hard guarantee against
the false-positive that ends trust.

**provensql** targets that setting. It is explicitly *not* a new decision
procedure — the proving frontier (QED [5], VeriEQL [6]) is well ahead on that
axis (§6). Its contributions are a tool, a safety study, and an engineering
pattern:

1. **A sound-by-construction edit-safety checker** for the constraint-aware
   conjunctive fragment, with four actionable verdicts (`EQUIVALENT`,
   `DIFFERENT`, `SCHEMA_CHANGE`, `UNKNOWN`). Soundness is structural, not
   conventional: `DIFFERENT` cannot be constructed without a witness, and
   `EQUIVALENT` only ever comes from a proof (§4.1).
2. **Executable, replayable witnesses.** Every `DIFFERENT` ships the database
   instance, run in DuckDB, on which the queries diverge — a reviewer artifact,
   not a claim (§4.2).
3. **Catalog-aware refactor proofs** — `LEFT`↔`INNER`, `DISTINCT` elimination,
   `WHERE`↔`ON` pushdown, join reordering — each justified by declared
   `NOT NULL`/`UNIQUE`/`FK` and each printing the assumption it relied on (§4.4).
4. **A runtime soundness backstop**: the invariant "the SMT proof engine and the
   counterexample engine must never contradict" is promoted from a test to a
   shipped runtime check, so a bug in our own prover degrades coverage, never
   soundness (§4.5). We are not aware of a prior sound checker that ships this.
5. **A safety comparison against LLM judges** — the oracle practitioners
   actually use — showing a state-of-the-art model attains high accuracy yet
   still emits false `EQUIVALENT`s, the error class provensql precludes (§5.4).
   This comparison is absent from the prover literature.
6. **An adversarial, honest evaluation** — hand-labeled corpus, coverage
   ceiling, mutation testing with a statistical soundness bound, and a negative
   benchmark-scope result — including reporting the recall we *lost* to remove
   an unsound dependency (§5).

## 2. A motivating example

Consider an edit that rewrites a safe-divide:

```sql
-- before
SELECT id, revenue / clicks AS rpc FROM ads
-- after
SELECT id, SAFE_DIVIDE(revenue, clicks) AS rpc FROM ads
```

An LLM judge, and many humans, will call these equivalent — "it's just the safe
version." They are not: on a row with `clicks = 0` the first errors and the
second returns `NULL`. provensql does not model division precisely enough to
assume either error-vs-NULL semantics, so it does not prove equivalence; its
counterexample search constructs the `clicks = 0` instance and returns
`DIFFERENT` with that row as the witness. The reviewer sees the exact instance
that breaks, not a verdict to trust on faith.

Conversely, a genuine refactor:

```sql
-- before
SELECT o.id FROM orders o LEFT JOIN customers c ON o.cust_id = c.id
-- after
SELECT o.id FROM orders o JOIN customers c ON o.cust_id = c.id
```

is equivalent *iff* every `orders.cust_id` matches a `customers.id` — i.e.
`cust_id` is `NOT NULL` and a foreign key to the unique `customers.id`. With a
catalog declaring those constraints, provensql proves `EQUIVALENT` and prints
the assumption it used; without one, it abstains rather than guess.

## 3. Design goals

- **Soundness is non-negotiable and one-directional.** A false `DIFFERENT`
  costs review time; a false `EQUIVALENT` ships a regression. The entire tool
  is organized to make the latter impossible, and the entire evaluation to try
  to elicit it.
- **Abstention is a first-class outcome.** `UNKNOWN` with a reason code is the
  designed-for failure mode, not an embarrassment. Coverage is a dial we turn
  up only when we can do so soundly.
- **Verdicts must be actionable.** A `DIFFERENT` carries a runnable witness; an
  `EQUIVALENT` carries the assumptions it depended on. Both are machine-readable
  (§8).

## 4. Approach

### 4.1 Verdicts and the soundness contract

provensql returns one of four verdicts:

| Verdict | Meaning | Backed by |
|---|---|---|
| `EQUIVALENT` | same output on **every** instance | a proof (canonical or SMT) |
| `SCHEMA_CHANGE` | output columns differ in name/order/type | static comparison |
| `DIFFERENT` | outputs diverge on some instance | an executed witness |
| `UNKNOWN` | outside the decidable fragment | explicit refusal + reason code |

The contract is enforced in the type, not by discipline: the constructor for
`DIFFERENT` raises unless given a non-empty witness, and no stage below the
counterexample search can produce one; `EQUIVALENT` is only ever emitted by a
stage that produced a proof.

### 4.2 Pipeline

```
parse (S0) → canonicalize (S1) → canonical match?      → EQUIVALENT
                               → schema differs?        → SCHEMA_CHANGE
                               → SMT proof (S3)          → EQUIVALENT
                               → counterexample (S4)     → DIFFERENT
                               → otherwise               → UNKNOWN
```

- **Stage 0 (parse + fragment check).** Refuses anything outside a supported
  fragment — window functions, recursive CTEs, nested `ARRAY`/`STRUCT` types,
  nondeterministic functions, DML/DDL/scripting — each with a machine-readable
  reason code. Refusing loudly here is what keeps the downstream stages sound.
- **Stage 1 (canonicalize).** Applies only *semantics-preserving* transforms:
  identifier qualification plus the renderer's normalization (whitespace,
  casing, quoting). Notably it does **not** run a general simplifier — see §5.5
  for why that decision was forced by a bug the harness caught.
- **Stage 2 (canonical match).** If both queries render to the same canonical
  string, they are equivalent. Sound precisely because Stage 1 is
  semantics-preserving.
- **Stage 3 (SMT proof).** Proves equivalence of the conjunctive fragment:
  scalar expressions under three-valued NULL logic, `GROUP BY` reordering,
  catalog-justified `LEFT`↔`INNER` substitution, inner-join reordering,
  `WHERE`↔`ON` pushdown, and `DISTINCT` elimination (§4.3, §4.4).
- **Stage 4 (counterexample search).** Synthesizes small adversarial database
  instances, runs both queries in DuckDB, and returns the first instance whose
  result multisets differ as a witness. Absence of a counterexample is *not*
  taken as proof — it falls through to `UNKNOWN`.

Stage 3 is tried before Stage 4 because it is both cheaper and a stronger claim
(a proof, not "we tried some instances").

### 4.3 Three-valued encoding

Scalar expressions are compiled to Z3 as `(is_null, value)` pairs implementing
SQL's Kleene logic: comparisons and boolean connectives propagate a third
"unknown" truth value, `CASE`/`COALESCE`/`IN`/`BETWEEN` are encoded directly,
and any construct outside the modeled fragment — notably every aggregate — is
represented as a fresh *opaque atom* keyed by its own syntax. Two expressions
are proven equivalent when their encodings are equal under all assignments; an
aggregate therefore matches only if it is syntactically identical on both sides.
Division abstains rather than assume error-vs-NULL semantics, and numeric
columns are modeled as exact reals (a boundary we state explicitly for `FLOAT`
reassociation). The encoding is validated differentially against DuckDB (§5.1).

### 4.4 Catalog-aware refactor proofs

Many real refactors are equivalent only relative to integrity constraints.
Given a catalog of `NOT NULL`/`UNIQUE`/`FK` declarations, Stage 3 justifies:
`LEFT`↔`INNER` substitution (the outer join adds no rows when the key is a
non-null FK to a unique column), `DISTINCT` elimination (a projected `UNIQUE`
column, or a `GROUP BY` whose keys are all projected, already yields distinct
rows), and predicate pushdown across inner joins. Every catalog-dependent
verdict prints the specific assumption it relied on, so a reviewer can confirm
it against the real schema.

### 4.5 Defense-in-depth: the runtime backstop

The one error the tool forbids could still arise from a bug in our own SMT
encoder or the solver. To make that fail safe, the invariant that Stage 3 and
Stage 4 must never contradict — normally only asserted in the test suite — is
promoted to a runtime check: on the catalog-free path, whenever Stage 3 proves
`EQUIVALENT`, Stage 4's counterexample search is run on the same pair, and if it
finds a diverging instance the verdict is withheld and downgraded to `UNKNOWN`
carrying that instance. An encoder or solver soundness bug thus costs coverage,
never soundness. (The backstop is skipped when a catalog is supplied, because
Stage 4 does not enforce catalog constraints and could otherwise "refute" a
legitimately constraint-justified proof; without a catalog, Stage 3's proofs
hold on every instance by construction, so any witness is a genuine
contradiction.)

## 5. Evaluation

### 5.1 Methodology

No single experiment answers every question, so we use several, each attacking
a different threat:

- **Hand-labeled corpus** — precision and soundness against human ground truth.
- **Full corpus** — where the coverage ceiling actually is.
- **Mutation testing** — recall on the targeted rewrite classes, and
  adversarial soundness, with a statistical bound.
- **Differential encoder validation** — ~69k random expression evaluations,
  Z3 encoding vs. DuckDB, zero mismatches.
- **Cross-validation / runtime backstop** — the proof and counterexample
  engines never contradict (§4.5).
- **Baselines** — trivial string checks and an LLM judge, on the same data.
- **Benchmark scope** — honest placement against the academic sets (§5.5).

The corpus is 1,242 real `(before, after)` pairs mined from the commit history
of `mozilla/bigquery-etl`, `GoogleCloudPlatform/bigquery-utils`, and
`dbt-labs/jaffle-shop-classic` — every commit touching a `.sql` file. A
213-pair stratified sample was hand-labeled independently of the tool. A corpus
finding in its own right: **76% of real SQL edits are semantically
consequential** (`DIFFERENT`/`SCHEMA_CHANGE`), and in the sample every touched
`WHERE`/`HAVING` predicate changed behavior — query edits are rarely "just
refactors."

### 5.2 Real corpus: precision and the coverage ceiling

On the 213 hand-labeled pairs:

| Metric | Value |
|---|---|
| Coverage (definitive verdict) | 10.8% |
| **False `EQUIVALENT`** | **0** |
| Precision on decided cases | 87.0% |

Running the full 1,242-pair corpus gives 10.1% coverage — nearly identical, so
sample size is not the bottleneck. The ceiling is **corpus composition**: ~46%+
of pairs are refused at Stage 0 (Jinja templating, BigQuery scripting, window
functions), and most of the remainder are genuine behavior changes, not
refactors. bigquery-etl is production ETL, not a refactoring playground — it
exercises soundness heavily and the refactor-proving machinery barely at all.
That motivates mutation testing.

### 5.3 Mutation testing: recall and a soundness bound

We apply transformations whose ground-truth answer is known by construction to
421 real single-`SELECT` queries.

**Equivalence-preserving rewrites (want `EQUIVALENT`; recall):**

| Rewrite | Recall |
|---|---|
| `WHERE`→`ON` pushdown (inner joins) | **100%** (6/6) |
| Reorder inner-join chain | **75%** (6/8) |
| Redundant `DISTINCT` elimination | **88%** (43/49) |
| Reorder `WHERE` conjuncts | 3% (2/79) |
| Swap `=` operands | 13% (46/348) |
| Double negation | 7% (11/160) |

The last three are honestly low, and the reason is instructive (§5.5): they
used to resolve at Stage 2 via a simplifier we removed for soundness, and now
route through the SMT prover, which abstains whenever an expression contains a
function outside its modeled fragment. The join/`DISTINCT` rows — Stage 3's
actual target capabilities — are unaffected.

**Equivalence-breaking mutations (must never be `EQUIVALENT`; soundness):**
across **511** mutations (flip a comparison, bump a literal, drop a conjunct,
add a deduplicating `DISTINCT`), **zero** were wrongly certified equivalent.
Under a one-sided Clopper–Pearson 95% bound (the exact rule of three), that
puts the true false-`EQUIVALENT` rate on this distribution at **≤ 0.58%**, and
the ceiling only tightens as the corpus grows.

### 5.4 Baselines, including an LLM judge

Scored on the same 213 pairs, reporting the metric that matters —
false-`EQUIVALENT` count:

| Baseline | Accuracy | False `EQUIVALENT` |
|---|---|---|
| String equality | 40.8% | 0 (vacuous) |
| `sqlglot`-normalized compare | 40.8% | 0 (vacuous) |
| LLM judge (OpenAI gpt-5) | 85.9% | **2** |
| **provensql** | — | **0** |

The two trivial baselines score zero only because they never claim equivalence
at all — soundness by refusing to play. The LLM judge is the informative case:
gpt-5 is genuinely good (85.9% exact-match accuracy, 98.1% coverage) and claims
equivalence liberally (30 of 213) — but 2 of those claims are on pairs the human
labeled `DIFFERENT`/`SCHEMA_CHANGE`. That is the one error class provensql is
built to preclude. The point is not that provensql is more cautious — the
trivial baselines are trivially cautious — it is that provensql claims
equivalence on real cases *and* is never wrong when it does.

### 5.5 What the harness caught (and the honest negatives)

The evaluation is not decoration; it has caught real defects, which is the
strongest evidence it measures something real.

- **A live false `EQUIVALENT` via a dependency.** A trust-boundary fuzz test
  found `sqlglot`'s `simplify()` collapsing
  `CASE WHEN flag THEN b WHEN TRUE THEN 2 ELSE 0 END` to `2`, which through
  canonicalization produced a real false `EQUIVALENT` at Stage 2. We removed
  `simplify()` from the pipeline entirely; the equivalences it used to catch
  now route through the SMT-validated Stage 3. The measured cost — the recall
  drop in §5.3 — is reported, not hidden: soundness bought with coverage.
- **A backwards `DISTINCT` rule** and **execution-layer bugs** (catalog-
  qualified names, query parameters, column-domain collisions) surfaced only by
  running against real queries.

### 5.6 Scope: placement against academic benchmarks (a negative result)

We ran provensql over the standard **Calcite 232** equivalence set (as
published with SPES, and independently in Cosette). Result: 84% of pairs parse,
but only **0.4% (1/232) are proven equivalent**, with **0 false `EQUIVALENT`**.
The reason is structural: of the parseable pairs, 107 contain aggregation/
`GROUP BY` and 87 contain a subquery — the set targets aggregation and subquery
rewrites, a *different fragment* from provensql's constraint-aware conjunctive
one. Nine pairs return `DIFFERENT`, and inspection shows these are *correct*:
they are Calcite rules (e.g. aggregate-pushdown-through-join) equivalent only
under integrity constraints, which provensql — given no catalog — rightly
declines, naming the missing constraint via its witness. Cosette's own
"sqlrewrites" set reproduces the picture (0/22 proven, 0 false either
direction): those rewrites are expressed via nested subqueries and comma-joins
that provensql does not flatten.

We report this deliberately. It is not a weakness disguised — it *delineates the
niche*: provensql is for production change-safety over flat SPJ SQL with
explicit joins and real constraints, not for optimizer-style aggregation/
subquery equivalence. It also yields a concrete roadmap (§7).

## 6. Related work

**Sound equivalence provers.** This is a mature, fast-moving line.
Cosette/HoTTSQL [1] proves rewrites via a mechanized denotational semantics;
EQUITAS [2] and SPES [3] use SMT and symbolic bag representations; WeTune [4]
discovers and verifies optimizer rewrite rules. The current frontier is
stronger still: **QED** [5] (VLDB'24) decides equivalence under bag semantics
with a normal-form calculus that handles integrity constraints and NULLs, and
**VeriEQL** [6] (OOPSLA'24) does bounded proving *and disproving* of a large
SQL fragment with rich integrity constraints, emitting counterexamples.

We state the relationship plainly: **provensql does not advance the
equivalence-proving frontier, and does not claim to.** Executable
counterexamples (VeriEQL, Polygon [7]), first-class integrity constraints
(QED, VeriEQL), and three-valued NULL logic (EQUITAS onward) are all prior
art. On the standard academic benchmarks provensql is deliberately far behind
these systems, because it targets a narrower fragment (§5.6). What is new here
is not an algorithm but (i) an *empirical safety comparison against LLM
judges*, the oracle practitioners actually reach for, which the prover
literature does not study; (ii) a *change-review tool* built around actionable
verdicts (printed assumptions, replayable witnesses) rather than optimizer
integration; and (iii) an engineering pattern — the runtime cross-engine
backstop (§4.5) — that makes a bug in the prover itself fail safe. The
contribution is a tool and a study, not a new decision procedure.

**LLMs as judges.** Using LLMs to assess semantic equivalence is increasingly
common; our §5.4 result quantifies why it is unsafe as a gate — high accuracy,
nonzero false `EQUIVALENT`.

**Bounded/testing approaches.** Query-fuzzing and differential-testing systems
find divergences by execution; provensql's Stage 4 is in this tradition, but
subordinated to a proof-first pipeline and used to *manufacture witnesses*, not
to assert equivalence.

## 7. Limitations and roadmap

- **Fragment.** Conjunctive; aggregates opaque; no subquery unnesting, no
  set-operation reasoning, no window functions. Each yields a clean `UNKNOWN`.
- **Coverage on production ETL is ~10%**, bounded by Stage-0 refusal of
  templated/scripted SQL, not by proof power.
- **Catalog-dependent proofs** need declarations the tool cannot infer; each
  prints its assumption.
- **Roadmap, prioritized by the scope study (§5.6):** (0) subquery unnesting +
  comma-join normalization — the highest-ROI extensions, which the Cosette
  study shows would unlock a slice of both academic sets; (1) aggregation
  reasoning beyond opaque atoms; (2) a fair, fragment-restricted benchmark
  comparison that only becomes meaningful after (1).
- **A research direction the abstentions point to.** provensql deliberately
  abstains on division (error-vs-NULL semantics) and models numerics as exact
  reals. Notably, the entire proving frontier shares this blind spot: QED [5],
  VeriEQL [6], and the linear-integer-arithmetic approach [8] all encode
  numbers as mathematical integers/reals and assume total, error-free
  arithmetic. **Sound equivalence under floating-point/decimal rounding and
  under runtime-error semantics (division-by-zero, overflow, CAST failure) is
  therefore an open problem** — and the one most relevant to financial SQL,
  where a "safe" rewrite can silently change rounding or turn an error into a
  NULL. We flag it as the most promising avenue for a subsequent, deeper
  contribution.

## 8. Demonstration

The demo is the CLI and its audit artifact. `provensql diff before.sql
after.sql` prints the verdict, reason, and assumptions; `--json` emits a
machine-checkable certificate — for `DIFFERENT`, the embedded replayable
witness instance; for `EQUIVALENT`, confirmation that the verdict survived the
runtime backstop. Attendees bring or write query edits and watch the tool prove
a refactor safe, manufacture a counterexample for a subtle break, or abstain
with a reason — and compare each to an LLM judge's answer live.

## 9. Reproducibility

```
pip install -e ".[dev]"
python -m pytest tests/ -q                       # 56 tests (unit + fuzz + backstop)
python eval/run_eval.py --catalog mining/output/udf_catalog.yml   # hand-labeled
python mining/full_corpus_eval.py                # coverage ceiling
python mining/mutation_eval.py                   # recall + soundness bound
python eval/baselines.py --openai-model gpt-5    # baselines (billed)
python eval/benchmark_triage.py eval/benchmarks/spes_calcite_tests.json  # scope
```

The single number to check across all of them is the false-`EQUIVALENT` count.
It is zero, and it is meant to stay zero.

## References

- [1] Chu, Weitz, Cheung, Suciu. *HoTTSQL: Proving Query Rewrites with Univalent
  SQL Semantics.* PLDI 2017. (See also Chu et al., *Cosette*, CIDR 2017.)
- [2] Zhou, Arulraj, Navathe, Harris, Zhang. *Automated Verification of Query
  Equivalence Using Satisfiability Modulo Theories* (EQUITAS). PVLDB 12(11),
  2019.
- [3] Zhou, Arulraj, Navathe, Harris, Wu. *SPES: A Symbolic Approach to Proving
  Query Equivalence Under Bag Semantics.* ICDE 2022.
- [4] Wang et al. *WeTune: Automatic Discovery and Verification of Query Rewrite
  Rules.* SIGMOD 2022.
- [5] Wang, Pan, Cheung. *QED: A Powerful Query Equivalence Decider for SQL.*
  PVLDB 17(11), 2024.
- [6] *VeriEQL: Bounded Equivalence Verification for Complex SQL Queries with
  Integrity Constraints.* PACMPL (OOPSLA) 2024. arXiv:2403.03193.
- [7] *Polygon: Symbolic Reasoning for SQL using Conflict-Driven
  Under-Approximation Search.* 2025. arXiv:2504.06542.
- [8] *Proving Query Equivalence Using Linear Integer Arithmetic.* PACMMOD 2023.

*(Author lists for [4],[6],[7] and exact page numbers to be completed against
the source venues before submission.)*
