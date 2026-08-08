"""
A cited corpus of arithmetic/nullability rewrite rules taken from real,
production SQL optimizers, for the M3 headline audit (see
docs/precision_research_scope.md). Each entry pairs a rule an optimizer actually
applies with the guard that optimizer places on it, so the audit can ask: does
the M1 (FP) / M2 (ERROR-NULL) engine independently arrive at the same guard the
optimizer's authors had to reason out by hand -- and, for tracked bugs, does it
flag the case the optimizer got wrong?

This is a *curated, cited* set, not an automatic scrape of optimizer source.
Every entry carries its provenance (`source`, `ref`, `url`) so a reader can
verify it. `status` is deliberately conservative:

  * "guard-confirmed" -- the optimizer restricts the rule (e.g. Spark's
    ReorderAssociativeOperator applies only to IntegralType); our engine should
    independently show the *unguarded* rule is unsound, i.e. the guard is
    necessary. A tool that reproduces a hand-written guard is a tool you can
    trust to synthesize the next one.
  * "known-defect" -- a case the optimizer's own issue tracker records as wrong
    (Apache Calcite JIRA); our engine should flag it. This is validation
    against ground truth, not a novel-bug claim.

We make no claim to have found new bugs in mature optimizers here; the obvious
unsound rewrites are already guarded or already tracked. The contribution is a
checker that derives those same soundness conditions automatically, on an axis
(FP rounding + ERROR/NULL outcome) the equivalence-proving frontier does not
model.

`axis` selects the engine: "fp" -> precision.fp_equivalent, "error" ->
error_semantics.error_equivalent. Each rule is valid over the mathematical
reals with total, error-free arithmetic -- the model every existing SQL
equivalence prover assumes.
"""

# fmt: off
RULES = [
    # --- Apache Spark, ReorderAssociativeOperator -------------------------
    # Spark reassociates and constant-folds Add/Multiply, but ONLY for
    # integral types: the rule body is gated on
    #   `a.deterministic && a.dataType.isInstanceOf[IntegralType]`
    # precisely because float reassociation is not value-preserving. Our FP
    # engine should confirm the unguarded (float) rule is DIVERGENT.
    {
        "id": "spark-reorder-add",
        "source": "Apache Spark (Catalyst)",
        "ref": "ReorderAssociativeOperator, expressions.scala",
        "url": "https://github.com/apache/spark/blob/master/sql/catalyst/src/main/scala/org/apache/spark/sql/catalyst/optimizer/expressions.scala",
        "lhs": "(a + b) + c", "rhs": "a + (b + c)",
        "guard": "IntegralType only", "axis": "fp", "status": "guard-confirmed",
        "note": "Spark reassociates Add only for integral columns; on FLOAT/DOUBLE it does not.",
    },
    {
        "id": "spark-reorder-mul",
        "source": "Apache Spark (Catalyst)",
        "ref": "ReorderAssociativeOperator, expressions.scala",
        "url": "https://github.com/apache/spark/blob/master/sql/catalyst/src/main/scala/org/apache/spark/sql/catalyst/optimizer/expressions.scala",
        "lhs": "(a * b) * c", "rhs": "a * (b * c)",
        "guard": "IntegralType only", "axis": "fp", "status": "guard-confirmed",
        "note": "Spark reassociates Multiply only for integral columns.",
    },

    # --- Apache Calcite, RexSimplify: tracked error/NULL defects ----------
    # CALCITE-7145: RexSimplify folds `IS NULL(10/0)` to false (and
    # `IS NOT NULL(10/0)` to true), evaluating a division-by-zero as if it
    # were a plain non-null value. Correct behaviour raises (Oracle/Postgres)
    # or yields NULL (MySQL/SQLite) -- never a definite false. Our ERROR
    # engine models the raising semantics, so it flags ERROR vs false.
    {
        "id": "calcite-7145-isnull-divzero",
        "source": "Apache Calcite (RexSimplify)",
        "ref": "CALCITE-7145",
        "url": "https://issues.apache.org/jira/browse/CALCITE-7145",
        "lhs": "(10/0) IS NULL", "rhs": "FALSE",
        "guard": "none (defect)", "axis": "error", "status": "known-defect",
        "note": "RexSimplify wrongly folds IS NULL over a division-by-zero to a definite false.",
    },
    {
        "id": "calcite-7145-isnotnull-divzero",
        "source": "Apache Calcite (RexSimplify)",
        "ref": "CALCITE-7145",
        "url": "https://issues.apache.org/jira/browse/CALCITE-7145",
        "lhs": "(10/0) IS NOT NULL", "rhs": "TRUE",
        "guard": "none (defect)", "axis": "error", "status": "known-defect",
        "note": "Sibling of the above: IS NOT NULL over a /0 wrongly folded to true.",
    },
    # CALCITE-7295: null-propagation through division. Folding `NULL + a/b`
    # to `NULL` is only sound when the division cannot raise. With a nonzero
    # constant divisor it is safe; with a variable divisor it drops a
    # possible division-by-zero ERROR. The pair below isolates exactly that.
    {
        "id": "calcite-7295-null-plus-safe-div",
        "source": "Apache Calcite (RexSimplify)",
        "ref": "CALCITE-7295",
        "url": "https://issues.apache.org/jira/browse/CALCITE-7295",
        "lhs": "NULL + a/4", "rhs": "NULL",
        "guard": "constant nonzero divisor", "axis": "error", "status": "guard-confirmed",
        "note": "Null-propagation is sound here: /4 can never raise, so the outcome is NULL either way.",
    },
    {
        "id": "calcite-7295-null-plus-unsafe-div",
        "source": "Apache Calcite (RexSimplify)",
        "ref": "CALCITE-7295",
        "url": "https://issues.apache.org/jira/browse/CALCITE-7295",
        "lhs": "NULL + a/b", "rhs": "NULL",
        "guard": "requires non-raising divisor", "axis": "error", "status": "guard-confirmed",
        "note": "Same fold with a variable divisor drops a division-by-zero ERROR at b=0.",
    },

    # --- Common algebraic simplifications (broadly applied) ---------------
    # These identities are real-valid and are exactly the shapes optimizers
    # and hand-refactors reach for. Spark gates the reassociating ones to
    # integral types (above); the point here is that on FLOAT columns the
    # rewrite is unsound, and the FP engine produces the witness.
    {
        "id": "distribute-mul-over-add",
        "source": "common algebraic simplification",
        "ref": "textbook / optimizer expression rules",
        "url": "",
        "lhs": "a * (b + c)", "rhs": "a * b + a * c",
        "guard": "unsound on FLOAT", "axis": "fp", "status": "guard-confirmed",
        "note": "Distributivity fails under IEEE-754 rounding.",
    },
    {
        "id": "cancel-add-sub",
        "source": "common algebraic simplification",
        "ref": "textbook / optimizer expression rules",
        "url": "",
        "lhs": "(a + b) - b", "rhs": "a",
        "guard": "unsound on FLOAT and across NULL", "axis": "fp", "status": "guard-confirmed",
        "note": "Cancellation loses low-order bits under FP; also changes NULL outcome (see error audit).",
    },
    {
        "id": "cancel-add-sub-error",
        "source": "common algebraic simplification",
        "ref": "textbook / optimizer expression rules",
        "url": "",
        "lhs": "(a + b) - b", "rhs": "a",
        "guard": "unsound across NULL", "axis": "error", "status": "guard-confirmed",
        "note": "At b=NULL the LHS is NULL but the RHS is a's value: outcome changes.",
    },
]
# fmt: on
