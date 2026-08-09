#!/usr/bin/env python3
"""Entrypoint for the provensql GitHub Action.

Finds the SQL files a pull request modifies, runs `provensql diff` on each
(base version vs head version), writes a job summary, optionally posts a PR
comment, and sets the job status according to `fail-on`. Standard library only
-- provensql itself is installed by the composite action before this runs.
"""
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

MARKER = "<!-- provensql-action -->"

# provensql CLI exit codes: 0 EQUIVALENT, 1 UNKNOWN, 2 DIFFERENT/SCHEMA_CHANGE.
STATUS = {
    "EQUIVALENT": ("✅", "Proven equivalent"),
    "UNKNOWN": ("⚠️", "Undecidable — needs review"),
    "DIFFERENT": ("❌", "Behavior change (counterexample found)"),
    "SCHEMA_CHANGE": ("❌", "Schema change"),
}
FAILING = {
    "different": {"DIFFERENT", "SCHEMA_CHANGE"},
    "unknown": {"DIFFERENT", "SCHEMA_CHANGE", "UNKNOWN"},
    "never": set(),
}


def sh(*args):
    """Run a git command, returning (returncode, stdout)."""
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, p.stdout


def parse_globs(raw):
    parts = []
    for line in raw.replace(",", "\n").splitlines():
        line = line.strip()
        if line:
            parts.append(line)
    return parts or ["**/*.sql"]


def matches(path, globs):
    # fnmatch doesn't treat "**" specially, so also try the basename pattern.
    for g in globs:
        if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.replace("**/", "")):
            return True
    return False


def resolve_base_sha(event):
    pr = event.get("pull_request") if event else None
    if pr and pr.get("base", {}).get("sha"):
        return pr["base"]["sha"]
    # push / manual fallback: compare against the previous commit.
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        rc, out = sh("git", "merge-base", "HEAD", f"origin/{base_ref}")
        if rc == 0 and out.strip():
            return out.strip()
    return "HEAD~1"


def changed_sql_files(base_sha, globs):
    # --diff-filter=M: modified files only -- both a base and a head version
    # exist at the same path, which is what an equivalence check needs.
    rc, out = sh("git", "diff", "--name-only", "--diff-filter=M", base_sha, "HEAD")
    if rc != 0:
        return []
    return [p for p in (line.strip() for line in out.splitlines()) if p and matches(p, globs)]


def run_provensql(base_sha, path, catalog):
    """Compare the base version of `path` (from git) with the working-tree head."""
    rc, base_content = sh("git", "show", f"{base_sha}:{path}")
    if rc != 0:
        return {"verdict": "SKIPPED", "reason": "no base version found"}
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as tf:
        tf.write(base_content)
        base_tmp = tf.name
    # Use the installed console script (defined in pyproject as
    # provensql = "provensql.cli:main"); it's on PATH after `pip install`.
    cmd = ["provensql", "diff", base_tmp, path, "--json"]
    if catalog:
        cmd += ["--catalog", catalog]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        os.unlink(base_tmp)
    try:
        cert = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"verdict": "ERROR", "reason": (p.stderr or p.stdout or "provensql failed").strip()[:300]}
    return cert


def build_summary(results):
    lines = [
        f"{MARKER}",
        "## provensql — SQL equivalence check",
        "",
        "| File | Verdict | Detail |",
        "| --- | --- | --- |",
    ]
    for path, cert in results:
        verdict = cert.get("verdict", "ERROR")
        icon, label = STATUS.get(verdict, ("❓", verdict))
        reason = cert.get("reason", "").replace("|", "\\|")
        detail = f"{label}. {reason}" if reason else label
        lines.append(f"| `{path}` | {icon} {verdict} | {detail} |")
    lines.append("")
    lines.append("_Sound by construction: provensql never reports a false EQUIVALENT._")
    return "\n".join(lines)


def post_comment(body):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not (token and repo and event_path):
        return
    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)
    number = (event.get("pull_request") or {}).get("number") or event.get("number")
    if not number:
        return
    api = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "provensql-action",
    }
    try:
        # Reuse an existing comment if we already posted one (keeps the PR tidy).
        req = urllib.request.Request(api, headers=headers)
        existing = json.load(urllib.request.urlopen(req))
        comment_id = next((c["id"] for c in existing if MARKER in (c.get("body") or "")), None)
        data = json.dumps({"body": body}).encode()
        if comment_id:
            url = f"https://api.github.com/repos/{repo}/issues/comments/{comment_id}"
            req = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
        else:
            req = urllib.request.Request(api, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        print(f"::warning::could not post PR comment: {e}")


def main():
    globs = parse_globs(os.environ.get("PROVENSQL_PATHS", "**/*.sql"))
    catalog = os.environ.get("PROVENSQL_CATALOG", "").strip() or None
    fail_on = os.environ.get("PROVENSQL_FAIL_ON", "different").strip().lower()
    do_comment = os.environ.get("PROVENSQL_COMMENT", "true").strip().lower() == "true"

    event = {}
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.isfile(event_path):
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)

    base_sha = resolve_base_sha(event)
    files = changed_sql_files(base_sha, globs)

    if not files:
        print("provensql: no modified SQL files to check.")
        _write_summary("## provensql — SQL equivalence check\n\nNo modified SQL files in this change.")
        sys.exit(0)

    results = [(path, run_provensql(base_sha, path, catalog)) for path in files]

    for path, cert in results:
        print(f"{cert.get('verdict', 'ERROR')}: {path} — {cert.get('reason', '')}")

    summary = build_summary(results)
    _write_summary(summary)
    if do_comment:
        post_comment(summary)

    failing_set = FAILING.get(fail_on, FAILING["different"])
    failed = [p for p, c in results if c.get("verdict") in failing_set]
    if failed:
        print(f"::error::provensql failed on {len(failed)} file(s): {', '.join(failed)}")
        sys.exit(1)
    sys.exit(0)


def _write_summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
