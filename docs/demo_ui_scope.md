# Scope: provensql demo UI (SIGMOD 2027 demonstration)

**Purpose:** the one new artifact the SIGMOD demo submission needs (see `docs/sigmod2027_demo_outline.md`). The engine is done; this is a thin, attendee-facing front-end that makes verdicts, witnesses, and honest abstention *visible and interactive* at a booth. It must run **fully offline** on a single laptop.

**Design principle:** the UI adds **zero engine logic**. It shells out to the existing CLI (`provensql diff --json`) and renders the JSON audit certificate. Everything it shows already exists in the certificate (`verdict`, `reason`, `assumptions`, `witness`, `counterexample_backstop`). If the UI needs a field the certificate doesn't expose, add it to the certificate in `cli.py`, not to the UI.

## Form factor: minimal local web app (recommended)
A booth visitor will paste/click far more readily on a web page than in a terminal, and a browser renders the counterexample table and the side-by-side LLM race much better. A TUI (Textual/Rich) is the fallback if we want zero browser dependency, but web wins for approachability and the Act-4 split view.

- **Backend:** a small Flask app (`demo/app.py`) — one dependency, trivial. (Fallback: stdlib `http.server` to avoid any dep.)
- **Frontend:** a single self-contained `index.html` (inline CSS/JS, no CDN — booth is offline). Two SQL editors, a scenario dropdown, a Run button, a verdict panel, and an LLM-race panel.
- **Lives in** `demo/` alongside the existing `demo/showcase.py` / `demo/README.md`.

## Endpoints (backend)
| Route | Does |
|---|---|
| `GET /` | serve `index.html` |
| `POST /diff` | body `{base, head, catalog?}` → write temps, run `provensql diff --json`, return the certificate JSON + exit code |
| `POST /judge` | body `{base, head}` → return an LLM verdict; **reads from a cached responses file offline**, only calls a live model if a key is present and online |
| `GET /scenarios` | return the canned demo scenarios (the four acts) |

## Screen layout
- **Left:** two SQL text areas (Base / Head) + a scenario dropdown that loads a canned pair. A small "types" toggle for Act 2 (integer ↔ `DOUBLE`) that swaps in the typed catalog.
- **Right — verdict panel:** big color-coded verdict chip (green EQUIVALENT / red DIFFERENT / amber UNKNOWN / red SCHEMA_CHANGE), the `reason`, `assumptions`, and — on DIFFERENT — the **witness row rendered as a table** with a "copy to run" button. On EQUIVALENT, show the backstop line ("counterexample search found none").
- **Bottom — LLM race (Act 4):** two cards side by side, "provensql" vs "LLM judge", each showing its verdict; highlight when they disagree.

## What each act needs on screen
1. **Prove & disprove** — scenarios: join reorder (EQUIVALENT), predicate touch (UNKNOWN), extra column (SCHEMA_CHANGE), an executable DIFFERENT with witness. Nothing extra; core panel covers it.
2. **Turn on precision** — the types toggle flips the catalog to `DOUBLE`; re-run shows DIFFERENT with a Float32 witness. Show the witness values and (nice-to-have) the two computed results side by side so the divergence is concrete.
3. **Catch a real optimizer bug** — canned CALCITE-7145 pair + the Spark reassociation pair; verdict panel shows the error-axis divergence / the re-derived guard. Add a one-line caption tying it to the real JIRA/commit.
4. **Beat the LLM** — the gpt-5 finding pair (from Paper 1); `/judge` returns the cached wrong-EQUIVALENT while provensql refuses/disproves. The disagreement highlight is the payoff.

## Offline strategy (critical for a booth)
- **Bundle all scenarios** as static data (`demo/scenarios.json`) — no network to load examples.
- **Cache LLM responses** in `demo/llm_cache.json` keyed by (base, head); `/judge` serves from cache. A live call is opt-in only (env key + `--online`) and never on the critical path.
- No CDN assets; inline everything in `index.html`.

## Deliverables
- `demo/app.py` (Flask), `demo/index.html`, `demo/scenarios.json`, `demo/llm_cache.json`.
- `demo/README.md` update: `pip install -e ".[demo]"` (add a `demo` extra with Flask) then `python -m demo.app`, open `localhost:5000`.
- Screenshots for the paper's Figures 1–2.
- A "reset" affordance so each visitor starts clean.

## Effort estimate
- Backend + endpoints: ~½ day (thin wrapper over the CLI).
- Frontend single page: ~1–1.5 days (editors, verdict panel, witness table, LLM race).
- Scenario curation + LLM cache + offline hardening: ~½ day.
- **Total ≈ 2.5–3 focused days**, well inside the Jan 15 2027 runway. No engine changes required (except possibly surfacing the two computed FP results in the certificate for Act 2 — small addition to `cli.py`).

## Non-goals (v1)
- No multi-user/hosted deployment (single laptop only).
- No auth, no persistence beyond the session.
- No new equivalence features — this is a viewer over the existing engine.
