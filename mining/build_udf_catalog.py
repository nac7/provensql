"""
Mines real UDF names out of mozilla/bigquery-etl's own repo structure and
writes a udfs-only provensql catalog.yml. This isn't guesswork: bigquery-etl
publishes each UDF as its own file at a fixed path convention
(sql/mozfun/<namespace>/<func>/udf.sql or sql/moz-fx-data-shared-prod/udf(_js)/<func>/udf.sql),
so the directory listing IS the UDF registry. Queries reference these
either fully project-qualified or with just the dataset prefix, so both
forms are emitted.
"""

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).parent / "repos" / "bigquery-etl"
PROJECT = "moz-fx-data-shared-prod"


def git_paths():
    out = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return out.splitlines()


def main():
    udfs = set()
    for path in git_paths():
        parts = path.split("/")
        if not path.endswith(("udf.sql", "udf.js")):
            continue

        if parts[0] == "sql" and parts[1] == "mozfun" and len(parts) == 5:
            _, _, namespace, func_dir, _ = parts
            udfs.add(f"mozfun.{namespace}.{func_dir}")

        elif parts[0] == "sql" and parts[1] == PROJECT and parts[2] in ("udf", "udf_js") and len(parts) == 5:
            dataset, func_dir = parts[2], parts[3]
            udfs.add(f"{dataset}.{func_dir}")
            udfs.add(f"{PROJECT}.{dataset}.{func_dir}")

    catalog = {"udfs": sorted(udfs)}
    out_path = Path(__file__).parent / "output" / "udf_catalog.yml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(catalog, f, default_flow_style=False)

    print(f"mined {len(udfs)} UDF names -> {out_path}")


if __name__ == "__main__":
    main()
