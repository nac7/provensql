# provensql showcase

What a *sound* checker does that an LLM judge can't: prove the safe edits,
catch the traps with an executable witness, flip its verdict with the declared
schema, and refuse rather than guess. Every block below is real output from:

```
python demo/showcase.py          # provensql only
python demo/showcase.py --llm    # add an OpenAI judge column (needs OPENAI_API_KEY)
```

## The one-screen summary

| SQL edit | What an LLM judge tends to say | provensql |
|---|---|---|
| Reorder `AND` conjuncts | equivalent ✓ | **EQUIVALENT** (proof) |
| `COALESCE(x,…)` → `CASE` | equivalent ✓ | **EQUIVALENT** (proof) |
| `COUNT(dept)` → `COUNT(*)` | "equivalent" ✗ | **DIFFERENT** (witness: a `NULL` row) |
| `a > 1` → `a >= 1` | "equivalent" ✗ | **DIFFERENT** (witness: `a = 1`) |
| add `DISTINCT` | "equivalent" ✗ | **DIFFERENT** (witness: a duplicate row) |
| `LEFT JOIN` → `JOIN` (no catalog) | "equivalent" ✗ | **DIFFERENT** (witness: an unmatched order) |
| `LEFT JOIN` → `JOIN` (+ catalog) | equivalent | **EQUIVALENT** (proof; prints the FK/UNIQUE it used) |
| window-function change | guesses | **UNKNOWN** (refuses — out of fragment) |

The safe edits and the traps are indistinguishable to a confident guesser. An
LLM judge is genuinely good — but on the traps it still says "equivalent," and
that is the one error that ships a silent regression. Measured on 213 real
edits, OpenAI `gpt-5` reached 85.9% accuracy yet returned **2 false
`EQUIVALENT`s**; provensql returned **0**. See [../docs/evaluation.md](../docs/evaluation.md).

## The trap: `COUNT(col)` vs `COUNT(*)`

An LLM (and many humans) call this "just counting rows." It isn't —
`COUNT(dept)` skips `NULL`s. provensql manufactures the exact instance that
breaks it:

```
COUNT(col) -> COUNT(*)   [classic trap]
  before: SELECT COUNT(dept) AS c FROM emp
  after : SELECT COUNT(*)    AS c FROM emp
  --> provensql: DIFFERENT
        witness: instance 'with_null_row'
            emp(dept) = { 'dept_0', 'dept_1', 'dept_2', 'dept_0', NULL }
            base  (COUNT(dept)) -> 4
            head  (COUNT(*))    -> 5
```

## Verdict flips with the schema: `LEFT JOIN` → `JOIN`

The *same* edit is unsafe in general and safe under an FK — and provensql says
which, printing the constraint it relied on:

```
LEFT JOIN -> JOIN, no catalog
  before: SELECT orders.id FROM orders LEFT JOIN customers ON orders.customer_id = customers.id
  after : SELECT orders.id FROM orders JOIN      customers ON orders.customer_id = customers.id
  --> provensql: DIFFERENT
        witness: an order whose customer_id matches no customer
                 base -> 4 rows, head -> 0 rows

LEFT JOIN -> JOIN, WITH catalog (demo/orders_catalog.yml)
  --> provensql: EQUIVALENT
        assuming: orders.customer_id is NOT NULL with a foreign key to
                  customers.id (declared UNIQUE) per catalog -- every row
                  always finds exactly one match, so LEFT JOIN and INNER
                  JOIN are identical here
```

## Honest refusal: out of fragment

```
Window-function change
  before: SELECT ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t
  after : SELECT RANK()       OVER (ORDER BY a) AS rn FROM t
  --> provensql: UNKNOWN  (base_unsupported_window_function)
```

No guess. `UNKNOWN` with a reason code is the designed-for outcome when a query
falls outside what provensql can decide soundly.
