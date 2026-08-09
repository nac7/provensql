# Scope: provensql GitHub Action

**Goal:** a drop-in GitHub Action that runs `provensql` on the SQL changed in a pull request and fails / comments when a change is not proven equivalence-preserving. This turns the library into something teams *install and depend on* — the strongest adoption signal for the NIW workstream, and a genuinely useful CI guard.

**Why it fits the tool:** the CLI already exposes exactly what CI needs (see `provensql/cli.py`):
- `provensql diff <base> <head> [--catalog cat.yml] [--json]`
- CI-friendly exit codes: **0 = proven equivalent (safe)**, **1 = UNKNOWN (needs human review)**, **2 = DIFFERENT / SCHEMA_CHANGE (behavior change)**.
- `--json` emits a machine-checkable audit certificate (with the replayable witness on DIFFERENT).

No engine changes are required. The Action is a thin wrapper: find changed SQL, resolve base vs head, call the CLI, aggregate, report.

---

## v1 (minimum viable, ship first)

**Trigger:** `pull_request` (and `workflow_dispatch` for manual runs).

**What it does:**
1. Check out the PR with history so `git diff` can compare base vs head.
2. Diff the PR to find changed `*.sql` files (configurable glob).
3. For each changed file, materialize the **base** version (`git show <base_sha>:<path>`) and the **head** version, then run `provensql diff base head --json`.
4. Aggregate the per-file verdicts and set the job status.

**Config surface (action inputs):**
| Input | Default | Purpose |
|---|---|---|
| `paths` | `**/*.sql` | glob(s) of SQL files to check |
| `catalog` | *(none)* | path to a catalog YAML passed through as `--catalog` |
| `fail-on` | `different` | `different` (exit 2 fails) \| `unknown` (exit 1 also fails) \| `never` (report only) |
| `comment` | `true` | post a PR comment summarizing verdicts |
| `provensql-version` | latest | pin the PyPI version for reproducibility |

**Outputs:** a summary table (file → verdict → reason), the JSON certificates as a build artifact, and a step-summary written to `$GITHUB_STEP_SUMMARY`.

**Failure policy:** default = fail only on proven behavior changes (exit 2); surface UNKNOWN as a warning (a review nudge, not a block) so the Action is adoptable without being noisy. Teams can tighten to `fail-on: unknown`.

**Packaging:** a **composite action** (`action.yml` + a small shell/python entrypoint) that `pip install provensql==<version>` and shells out — simplest to maintain, no Docker build. Ship in a dedicated repo `nac7/provensql-action` (Marketplace listing needs its own repo) or under `.github/` first to dogfood.

## Deliverables for v1
- `action.yml` (composite) with the inputs above.
- Entrypoint script: changed-file discovery, base/head materialization, per-file CLI invocation, aggregation, `$GITHUB_STEP_SUMMARY` + optional PR comment.
- `README.md` with a copy-paste `uses:` snippet and a screenshot of a failing check.
- An example workflow in the main provensql repo that runs the Action on its own test SQL (dogfooding = proof it works).
- Publish to the **GitHub Marketplace** (free; a real distribution channel + adoption metric).

## v2 (after adoption signal)
- Inline PR review comments anchored to the changed lines, carrying the counterexample witness for DIFFERENT.
- `pre-commit` hook variant (same entrypoint) for local use.
- Matrix over multiple catalogs / dialects.
- Badge: "SQL changes checked by provensql".

## Evidence value (NIW)
- Marketplace listing + installs = documentable third-party adoption.
- Each adopting repo is a usage citation of sorts; watch for stars/dependents on `provensql-action`.
- Pairs with the DBTest paper's §6 "artifact" claim: the tool is not just released, it is deployed in real CI.
