# Adoption playbook

Concrete, honest steps to get provensql in front of the people who'd use it.
Lead with the guarantee (never a false `EQUIVALENT`) and the LLM contrast; be
upfront about the v0.1.x fragment limits — overclaiming loses the exact
audience (data/PL engineers) who'd otherwise trust it.

## Checklist (owner: you)

- [ ] **Show HN** post (draft below).
- [ ] **r/dataengineering** and **r/SQL** post (draft below).
- [ ] Submit a PR adding provensql to curated lists: `awesome-sql`,
      `awesome-database-tools`, `awesome-static-analysis` (SQL section).
- [ ] Mention in the **dbt** and **sqlglot** community channels where CI/SQL
      review comes up (as a helpful tool, not a drive-by ad).
- [ ] Open a couple of **"good first issue"**s (e.g. subquery unnesting,
      comma-join normalization) — contributors are an adoption signal.
- [ ] Add the GitHub Action to one real repo you control and screenshot a PR
      where it catches a behavior change — concrete proof it works in the wild.

## Draft: Show HN

**Title:**
`Show HN: provensql – prove whether a SQL edit changes results (with counterexamples)`

**Body:**

> I kept seeing SQL edits reviewed by eyeballing or by asking an LLM "are these
> equivalent?" — and LLMs confidently say "yes" to changes that alter results
> (COUNT(col)→COUNT(*), LEFT JOIN→JOIN on a nullable key, a flipped CASE). That
> false "equivalent" silently ships a data regression.
>
> provensql decides it soundly instead. It returns EQUIVALENT only with a proof
> (canonical-form or an SMT proof under 3-valued NULL logic), DIFFERENT only
> with a concrete database instance it executed to show the divergence, and
> otherwise UNKNOWN — it never guesses "equivalent." The rule "never a false
> EQUIVALENT" is enforced structurally and re-checked at runtime by a
> cross-engine backstop.
>
> On 213 real edits mined from bigquery-etl it makes 0 false EQUIVALENT; across
> 511 adversarial mutations, still 0 (Clopper–Pearson 95% upper bound 0.58%).
> A gpt-5 judge on the same edits: 85.9% accurate but 2 false EQUIVALENT.
>
> It's deliberately scoped to a constraint-aware conjunctive fragment (no
> aggregation algebra, no subquery unnesting yet), and it says so — on the
> academic Calcite/Cosette sets it proves ~0.4%, because those target
> aggregation/subquery rewrites it doesn't. It's for *change-review*, not
> optimizer proving.
>
> pip install provensql · GitHub Action for PR gating · Apache-2.0.
> Repo: https://github.com/nac7/provensql   Paper: https://doi.org/10.5281/zenodo.21853966

## Draft: r/dataengineering / r/SQL

**Title:** `A tool that proves whether a SQL edit changes the result (and shows you a counterexample when it does)`

> Sharing an open-source tool I built. Given two versions of a query it tells
> you EQUIVALENT (with a proof), DIFFERENT (with an actual row-level
> counterexample it executed in DuckDB), or UNKNOWN — and it's built so it can
> never wrongly say "equivalent." There's a GitHub Action to fail PRs on
> behavior-changing SQL edits, and a pre-commit hook.
>
> It's early and fragment-limited (no aggregation/subquery reasoning yet), which
> the README is honest about. Feedback and issues welcome, especially on the
> rewrite classes you'd want covered next.
>
> `pip install provensql` — https://github.com/nac7/provensql

## Tone guardrails

- Never claim it "verifies any SQL" — name the fragment.
- The headline is *soundness + witnesses vs. a confident LLM*, not "better than
  academic provers" (it isn't, on their turf — say so; see docs/benchmark_scope.md).
- Invite the failure mode: ask people to try to make it emit a false
  EQUIVALENT. Confidence in the guarantee is the whole pitch.
