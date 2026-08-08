# CI integration

Gate pull requests (and local commits) on SQL edit-safety: provensql fails the
check only when an edit is a proven/likely behavior change (`DIFFERENT` or
`SCHEMA_CHANGE`), passes proven-safe refactors (`EQUIVALENT`), and treats
`UNKNOWN` as a warning by default. Every `DIFFERENT` prints a replayable
witness in the log.

## Option A — GitHub Action (recommended)

This repo ships a composite action. In your repository, add
`.github/workflows/sql-safety.yml`:

```yaml
name: SQL edit-safety
on: pull_request
jobs:
  provensql:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # required: the action diffs against the base branch
      - uses: nac7/provensql@v0.1.1
        with:
          # base-ref: origin/main   # optional; defaults to the PR base branch
          # catalog: schema.yml      # optional; enables constraint-aware proofs
          # fail-on-unknown: 'false' # optional; gate on UNKNOWN too
```

`fetch-depth: 0` matters — the action needs the base branch present to diff
against.

## Option B — plain workflow (no action)

If you'd rather not depend on the action, install the package and run the check
script:

```yaml
name: SQL edit-safety
on: pull_request
jobs:
  provensql:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install provensql
      - run: |
          git fetch --no-tags --depth=1 origin "${{ github.base_ref }}"
          curl -sSL https://raw.githubusercontent.com/nac7/provensql/v0.1.1/scripts/pr_check.py -o pr_check.py
          python pr_check.py --base "origin/${{ github.base_ref }}"
```

## Option C — pre-commit hook

Catch behavior-changing edits before they're even committed. In
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: provensql
        name: provensql SQL edit-safety
        entry: python scripts/pr_check.py --base HEAD
        language: system
        pass_filenames: false
        files: \.sql$
```

This compares each staged `.sql` file against its committed (`HEAD`) version, so
you learn a refactor is unsafe before it lands.

## Exit codes

`scripts/pr_check.py` (and `provensql diff`) use CI-friendly exit codes:

| Exit | Meaning |
|---|---|
| `0` | all edits proven safe (or only `UNKNOWN`, which is a warning by default) |
| `1` | at least one behavior-changing edit (`DIFFERENT`/`SCHEMA_CHANGE`); or, with `--fail-on-unknown`, an `UNKNOWN` |

## Reducing UNKNOWNs

Most `UNKNOWN`s on production SQL come from constructs outside provensql's
fragment (templating, window functions) or from missing schema facts. Supplying
a `--catalog` with `NOT NULL`/`UNIQUE`/foreign-key declarations lets Stage 3
prove catalog-dependent refactors (e.g. `LEFT JOIN`→`JOIN`) that otherwise
abstain. See the main README for the catalog format.
