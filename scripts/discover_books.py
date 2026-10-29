#!/usr/bin/env python3
"""Read-only inventory of the configured library root.

Lists candidate documents, their size and whether the index knows them
(new / changed / removed / unchanged). Never opens files for writing.

    python3 discover_books.py            # human-readable
    python3 discover_books.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402


def discover(config: dict) -> dict:
    root = Path(config["library_root"])
    if not root.is_dir():
        return {"library_root": str(root), "root_accessible": False, "documents": []}
    layout = common.DataLayout(config["data_dir"])
    manifest = common.load_manifest(layout)
    snapshot = common.library_snapshot(config)
    status = common.compare_snapshot(manifest, snapshot)
    by_rel = {rel: kind for kind in ("new", "changed", "unchanged") for rel in status[kind]}
    documents = []
    for rel, st in sorted(snapshot.items()):
        indexed = manifest.get("documents", {}).get(rel, {})
        documents.append({
            "relpath": rel,
            "format": Path(rel).suffix.lower().lstrip("."),
            "size_bytes": st["size"],
            "index_status": by_rel.get(rel),
            "extraction_status": indexed.get("status"),
            "catalog_key": indexed.get("catalog", {}).get("key") if indexed.get("catalog") else None,
        })
    return {
        "library_root": str(root),
        "root_accessible": True,
        "documents": documents,
        "removed_from_library": status["removed"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = common.load_config(args.config)
    except common.ConfigError as exc:
        common.eprint(f"ERROR: {exc}")
        return common.EXIT_CONFIG
    result = discover(config)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if not result["root_accessible"]:
            print(f"ERROR: library root {result['library_root']} is not accessible.")
            return common.EXIT_SOURCE_UNAVAILABLE
        print(f"Library root: {result['library_root']}")
        for doc in result["documents"]:
            print(f"  [{doc['index_status'] or '?':9}] {doc['relpath']} "
                  f"({doc['size_bytes'] / 1e6:.1f} MB, catalog={doc['catalog_key']})")
        for rel in result["removed_from_library"]:
            print(f"  [removed  ] {rel} (still in index)")
    return common.EXIT_OK if result["root_accessible"] else common.EXIT_SOURCE_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
