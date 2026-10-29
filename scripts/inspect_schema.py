#!/usr/bin/env python3
"""Inspect metadata of a data file without reading its values.

Allowed on development/full data in any phase (never on test data): prints
size, modification time, sha256 (optional) and the column header (CSV/TSV
first line, or the Parquet schema when pyarrow is installed). It does not
count rows or compute statistics, so it cannot leak the distribution.

    inspect_schema.py data/raw/full.csv [--hash]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402
import ds_policy  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--hash", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.path).resolve()
    root = common.find_project_root(os.getcwd())
    if root:
        project = ds_policy.Project(root)
        phase = project.state()["phase"]
        if "test_data" in ds_policy.classify_path(project, project.policy(), path) and phase != "final_evaluation":
            common.eprint("REFUSED: test data stays sealed, including its schema, until final_evaluation")
            return 1
    if not path.is_file():
        common.eprint(f"ERROR: {path} not found")
        return 1
    st = path.stat()
    info = {"path": str(path), "size_bytes": st.st_size, "modified": st.st_mtime}
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".tab", ".txt"):
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            info["columns"] = next(csv.reader(handle, dialect="excel-tab" if suffix in (".tsv", ".tab") else "excel"), [])
    elif suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            schema = pq.read_schema(path)
            info["columns"] = [f"{f.name}: {f.type}" for f in schema]
        except ImportError:
            info["columns"] = "pyarrow not installed: schema unavailable"
    else:
        info["columns"] = f"unsupported format {suffix}"
    if args.hash:
        info["sha256"] = common.sha256_file(path)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
