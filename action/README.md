# provensql SQL equivalence check — GitHub Action

Fail a pull request when a SQL change is **not proven equivalence-preserving**.
The Action runs [provensql](https://github.com/nac7/provensql) on every modified
`.sql` file (base version vs. head version) and reports one of four verdicts per
file. It is **sound by construction: it never reports a false EQUIVALENT** — so a
green check is a real guarantee, and anything it can't prove is surfaced as
`UNKNOWN` for human review rather than waved through.

## Usage

```yaml
name: SQL equivalence
on: pull_request

jobs:
  provensql:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write   # only needed for the PR comment
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0       # required: the Action needs the base commit
      - uses: nac7/provensql-action@v1
        with:
          paths: "models/**/*.sql"
          fail-on: different
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `paths` | `**/*.sql` | Newline- or comma-separated glob(s) of SQL files to check. |
| `catalog` | *(none)* | Path to a catalog YAML (table/column types, known UDFs), passed to `provensql diff --catalog`. |
| `fail-on` | `different` | `different` — fail only on proven behavior changes. `unknown` — also fail on undecidable diffs. `never` — report only. |
| `comment` | `true` | Post/update a summary comment on the PR. |
| `provensql-version` | *(latest)* | Pin the PyPI version, e.g. `0.1.1`, for reproducible runs. |
| `python-version` | `3.13` | Python used to run provensql. |
| `github-token` | `${{ github.token }}` | Token used to post the PR comment. |

## Verdicts

| Verdict | Meaning | Fails the job? |
| --- | --- | --- |
| ✅ `EQUIVALENT` | Proven behavior-preserving. | No |
| ⚠️ `UNKNOWN` | Couldn't decide — needs human review. | Only with `fail-on: unknown` |
| ❌ `DIFFERENT` | Proven behavior change (a counterexample row is recorded). | Yes |
| ❌ `SCHEMA_CHANGE` | Output schema changed. | Yes |

## Notes

- **`fetch-depth: 0`** on `actions/checkout` is required — the Action compares the
  base commit against head and needs history for `git show`.
- v1 checks **modified** files (a base and head version at the same path). Added,
  deleted, and renamed files are skipped.
- The per-file JSON certificates (`provensql diff --json`) are printed to the log;
  a Markdown table is written to the job summary.

Part of the provensql project. Apache-2.0.
