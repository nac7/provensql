"""
provensql mining harness -- Stage A: extract (base, head) SQL pairs.

Walks the commit history of a git repo (already cloned, --filter=blob:none
--no-checkout is fine since we fetch blobs on demand via `git show`), finds
every commit that *modifies* (not adds/deletes) a .sql file, and pulls the
before/after file content for that file at that commit.

This is deliberately dumb: one commit = one parent comparison. We are not
trying to reconstruct GitHub PR boundaries (that needs the GitHub API and
rate limits); a modified-file-at-a-commit is a fine unit of "someone changed
this query" for a discovery-stage frequency table.

Output: one JSON object per line (jsonl) with repo, commit sha, file path,
base content, head content.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(args, cwd, check=True):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=False, check=False
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"cmd failed: {args}\n{r.stderr.decode('utf-8', 'replace')}"
        )
    return r


def decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def list_sql_modifying_commits(repo_path: Path, max_commits: int):
    """
    Returns list of (commit_sha, parent_sha, file_path) for commits that
    *modify* (status M) a .sql file, oldest-history-walk via git log.
    """
    # --diff-filter=M : modifications only (skip adds/deletes/renames --
    # renames-with-no-content-change would be noise for a v0 discovery pass)
    # -m --first-parent: keep this linear and cheap; skip merge commits'
    # multi-parent diffs to avoid double counting.
    fmt = "--pretty=format:__COMMIT__%H"
    out = run(
        [
            "git", "log", "--first-parent", "-m",
            fmt,
            "--name-status",
            "--diff-filter=M",
            f"-n{max_commits}" if max_commits else "--all",
            "--", "*.sql",
        ],
        cwd=repo_path,
    ).stdout
    text = decode(out)

    pairs = []
    current_sha = None
    for line in text.splitlines():
        if line.startswith("__COMMIT__"):
            current_sha = line[len("__COMMIT__"):].strip()
            continue
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        status, path = parts
        if status != "M":
            continue
        if not path.endswith(".sql"):
            continue
        pairs.append((current_sha, path))
    return pairs


def get_parent_sha(repo_path: Path, sha: str) -> str | None:
    r = run(["git", "rev-parse", f"{sha}^"], cwd=repo_path, check=False)
    if r.returncode != 0:
        return None
    return decode(r.stdout).strip()


def get_file_at(repo_path: Path, sha: str, path: str) -> str | None:
    r = run(["git", "show", f"{sha}:{path}"], cwd=repo_path, check=False)
    if r.returncode != 0:
        return None
    return decode(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path", type=Path)
    ap.add_argument("--repo-name", required=True)
    ap.add_argument("--max-commits", type=int, default=3000)
    ap.add_argument("--max-pairs", type=int, default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    commit_file_pairs = list_sql_modifying_commits(args.repo_path, args.max_commits)
    print(
        f"[{args.repo_name}] {len(commit_file_pairs)} (commit, .sql file) "
        f"modification events found",
        file=sys.stderr,
    )

    seen_parents = {}
    n_written = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for sha, path in commit_file_pairs:
            if args.max_pairs and n_written >= args.max_pairs:
                break

            key = sha
            if key not in seen_parents:
                seen_parents[key] = get_parent_sha(args.repo_path, sha)
            parent_sha = seen_parents[key]
            if parent_sha is None:
                continue

            head_content = get_file_at(args.repo_path, sha, path)
            base_content = get_file_at(args.repo_path, parent_sha, path)
            if head_content is None or base_content is None:
                continue
            if head_content.strip() == base_content.strip():
                continue  # whitespace-only-at-git-level, no actual diff

            record = {
                "repo": args.repo_name,
                "commit": sha,
                "parent": parent_sha,
                "path": path,
                "base": base_content,
                "head": head_content,
            }
            f.write(json.dumps(record) + "\n")
            n_written += 1

    print(f"[{args.repo_name}] wrote {n_written} pairs to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
