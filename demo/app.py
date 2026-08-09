"""Local demo server for provensql (SIGMOD 2027 demonstration).

A thin viewer over the existing engines -- it adds NO equivalence logic. Each
request dispatches to one of three engines and returns a unified verdict shape
the front-end renders:

  * query  -> provensql.compare.compare        (Stage 0-4: proof + counterexample)
  * fp     -> provensql.precision.fp_equivalent (IEEE-754 disproof with witness)
  * error  -> provensql.error_semantics.error_equivalent (ERROR/NULL/value lattice)

Runs fully offline: scenarios and the LLM-judge responses are bundled as static
JSON, so a booth needs no network. Start with `python -m demo.app`.
"""
import json
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from provensql.compare import compare
from provensql.precision import fp_equivalent
from provensql.error_semantics import error_equivalent

HERE = Path(__file__).resolve().parent
# Disproving under IEEE-754 is fast, but give the solver headroom so a booth run
# reliably lands on DIVERGENT rather than a timeout abstention.
FP_TIMEOUT_MS = 20000

app = Flask(__name__, static_folder=None)

SCENARIOS = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
LLM_CACHE = json.loads((HERE / "llm_cache.json").read_text(encoding="utf-8"))

# EQUIVALENT-family verdicts render green; divergences red; abstention amber.
GOOD = {"EQUIVALENT", "EQUIVALENT_FP", "EQUIVALENT_ERR"}
BAD = {"DIFFERENT", "DIVERGENT", "SCHEMA_CHANGE"}


def _kind(verdict):
    if verdict in GOOD:
        return "good"
    if verdict in BAD:
        return "bad"
    return "unknown"


def _run_query(base, head):
    v = compare(base, head)
    return {
        "engine": "query",
        "verdict": v.type.value,
        "reason": v.reason,
        "assumptions": list(v.assumptions),
        "witness": v.witness or None,
        "reason_code": v.reason_code or None,
    }


def _run_fp(base, head):
    verdict, witness = fp_equivalent(base, head, timeout_ms=FP_TIMEOUT_MS)
    reason = {
        "DIVERGENT": "Real-number-valid, but the two expressions round to different IEEE-754 results.",
        "EQUIVALENT_FP": "Proven equal for all finite floating-point inputs.",
        "UNKNOWN": "Solver did not resolve within the budget -- honest abstention.",
    }.get(verdict, "")
    return {"engine": "fp", "verdict": verdict, "reason": reason,
            "assumptions": ["inputs finite", "Float32 model"], "witness": witness}


def _run_error(base, head):
    verdict, witness = error_equivalent(base, head)
    reason = {
        "DIFFERENT": "The two expressions differ on ERROR vs NULL vs value for some input.",
        "EQUIVALENT_ERR": "Identical outcome (ERROR / NULL / value) on every input.",
        "UNKNOWN": "Outside the modeled error fragment -- honest abstention.",
    }.get(verdict, "")
    return {"engine": "error", "verdict": verdict, "reason": reason,
            "assumptions": [], "witness": witness}


ENGINES = {"query": _run_query, "fp": _run_fp, "error": _run_error}


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.get("/scenarios")
def scenarios():
    return jsonify(SCENARIOS)


@app.post("/run")
def run():
    data = request.get_json(force=True)
    engine = data.get("engine", "query")
    base = (data.get("base") or "").strip()
    head = (data.get("head") or "").strip()
    if engine not in ENGINES:
        return jsonify({"error": f"unknown engine '{engine}'"}), 400
    if not base or not head:
        return jsonify({"error": "both base and head are required"}), 400
    try:
        result = ENGINES[engine](base, head)
    except Exception as e:  # a demo should surface engine errors, not 500 silently
        return jsonify({"engine": engine, "verdict": "ERROR", "reason": str(e),
                        "assumptions": [], "witness": None, "kind": "bad"})
    result["kind"] = _kind(result["verdict"])
    return jsonify(result)


@app.post("/judge")
def judge():
    data = request.get_json(force=True)
    base = (data.get("base") or "").strip()
    head = (data.get("head") or "").strip()
    cached = LLM_CACHE.get(f"{base}||{head}")
    if cached:
        return jsonify({**cached, "source": "cached"})
    return jsonify({"verdict": "UNKNOWN", "note": "No cached judgment for this pair (offline).",
                    "model": "n/a", "source": "cached"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
