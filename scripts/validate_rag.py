#!/usr/bin/env python3
"""Health check of the local-books RAG, runnable from any directory.

Checks: config resolvable and absolute; library root accessible; index and
manifest present; staleness (new/changed/removed books); failed extractions;
catalog coverage; and a retrieval smoke test executed from a *different*
working directory than the current one.

    python3 validate_rag.py            # exit 0 healthy, 1 warnings, 2 config, 3 index, 4 library unavailable
    python3 validate_rag.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402


def smoke_test_from_other_cwd(config_path: str, query: str) -> dict:
    """Run search_references.py in a fresh temporary directory."""
    script = Path(__file__).resolve().parent / "search_references.py"
    with tempfile.TemporaryDirectory(prefix="ebdsw-cwd-") as other:
        env = {**os.environ, common.CONFIG_ENV_VAR: config_path}
        proc = subprocess.run([sys.executable, str(script), query, "--k", "1", "--json"],
                              cwd=other, env=env, capture_output=True, text=True, timeout=120)
    ok = proc.returncode == 0
    detail = {}
    if ok:
        payload = json.loads(proc.stdout)
        top = payload["results"][0]
        detail = {"top_source": top["source"]["title"], "pdf_page": top["locator"]["pdf_page"],
                  "printed_page": top["locator"]["printed_page"]}
    return {"cwd": "fresh temporary directory", "exit_code": proc.returncode, "ok": ok,
            "detail": detail, "stderr": proc.stderr.strip()[-500:]}


def validate(config_arg: str | None, query: str) -> tuple[int, dict]:
    report = {"checks": [], "warnings": []}

    def check(name, ok, detail=None):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    try:
        config = common.load_config(config_arg)
    except common.ConfigError as exc:
        check("config", False, str(exc))
        return common.EXIT_CONFIG, report
    check("config", True, config["_config_path"])
    health = common.library_health(config)
    report["health"] = health
    report["warnings"].extend(health["warnings"])
    check("library_root_accessible", health["root_accessible"], health["library_root"])
    check("index_present", health["index_present"], str(common.DataLayout(config["data_dir"]).index_db))
    if not health["index_present"]:
        return common.EXIT_INDEX, report
    layout = common.DataLayout(config["data_dir"])
    try:
        conn = sqlite3.connect(f"file:{layout.index_db}?mode=ro", uri=True)
        docs = conn.execute("SELECT relpath, status, catalog_json, quality_median, printed_page_coverage FROM documents").fetchall()
        n_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        conn.close()
    except sqlite3.DatabaseError as exc:
        check("index_readable", False, str(exc))
        return common.EXIT_INDEX, report
    check("index_readable", True, f"{len(docs)} documents, {n_chunks} chunks")
    uncatalogued = [rel for rel, _s, cat, _q, _p in docs if not cat or cat == "null"]
    if uncatalogued:
        report["warnings"].append("documents without verified catalog metadata (cite with care): " + ", ".join(uncatalogued))
    report["documents"] = [
        {"relpath": rel, "status": status, "catalog_key": (json.loads(cat) or {}).get("key") if cat else None,
         "quality_median": q, "printed_page_coverage": p}
        for rel, status, cat, q, p in docs
    ]
    smoke = smoke_test_from_other_cwd(config["_config_path"], query)
    check("retrieval_from_other_cwd", smoke["ok"], smoke)
    if not health["root_accessible"]:
        return common.EXIT_SOURCE_UNAVAILABLE, report
    if not all(c["ok"] for c in report["checks"]):
        return common.EXIT_INDEX, report
    return (common.EXIT_NO_EVIDENCE if report["warnings"] else common.EXIT_OK), report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    parser.add_argument("--query", default="confidence interval", help="Smoke-test query.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    code, report = validate(args.config, args.query)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for c in report["checks"]:
            print(f"[{'OK' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail'] if not isinstance(c['detail'], dict) else json.dumps(c['detail'], ensure_ascii=False)}")
        for w in report["warnings"]:
            print(f"WARNING: {w}")
        print({0: "RAG healthy", 1: "RAG usable with warnings", 2: "config problem", 3: "index problem",
               4: "library unavailable"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
