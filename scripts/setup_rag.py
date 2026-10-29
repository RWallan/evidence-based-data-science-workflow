#!/usr/bin/env python3
"""Create or update the durable RAG configuration.

Run once from (or pointing at) the directory that holds the books. The
library root is stored as an absolute path in the XDG config file, so later
sessions can start Claude Code anywhere.

    python3 setup_rag.py --library-root "$PWD"
    python3 setup_rag.py --library-root /new/location --force   # reconfigure
    python3 setup_rag.py --show
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402


def build_config(library_root: Path, data_dir: Path, python: str | None, cwd: str) -> dict:
    return {
        "schema_version": common.CONFIG_SCHEMA_VERSION,
        "library_root": str(library_root),
        "data_dir": str(data_dir),
        "python": python,
        "include_extensions": common.DEFAULT_EXTENSIONS,
        "exclude_dirs": common.DEFAULT_EXCLUDED_DIRS,
        "created_at": common.utc_now(),
        "created_from_cwd": cwd,
        "notes": (
            "library_root is read-only. Generated artifacts live in data_dir. "
            "Override this file's location with $EBDSW_CONFIG."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library-root", help="Directory with the books (absolute or relative to the current directory).")
    parser.add_argument("--data-dir", help=f"Artifacts directory (default: {common.default_data_dir()}).")
    parser.add_argument("--python", help="Interpreter with PyMuPDF for indexing (default: <data-dir>/venv/bin/python if present).")
    parser.add_argument("--config", help="Config file to write (default: $EBDSW_CONFIG or XDG path).")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    parser.add_argument("--show", action="store_true", help="Print the resolved config and exit.")
    args = parser.parse_args(argv)

    config_path = common.resolve_config_path(args.config)
    if args.show:
        try:
            print(json.dumps(common.load_config(args.config), indent=2, ensure_ascii=False))
            return common.EXIT_OK
        except common.ConfigError as exc:
            common.eprint(f"ERROR: {exc}")
            return common.EXIT_CONFIG

    if not args.library_root:
        parser.error("--library-root is required unless --show is used")
    library_root = Path(args.library_root).expanduser().resolve()
    if not library_root.is_dir():
        common.eprint(f"ERROR: library root {library_root} is not a directory.")
        return common.EXIT_CONFIG
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else common.default_data_dir()
    try:
        data_dir.relative_to(library_root)
        common.eprint("ERROR: data dir must not live inside the library root (keep books and artifacts apart).")
        return common.EXIT_CONFIG
    except ValueError:
        pass

    python = args.python
    if python is None:
        candidate = data_dir / "venv" / "bin" / "python"
        python = str(candidate) if candidate.exists() else None

    if config_path.exists() and not args.force:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if existing.get("library_root") != str(library_root) or existing.get("data_dir") != str(data_dir):
            common.eprint(
                f"ERROR: {config_path} already points to library_root={existing.get('library_root')} "
                f"data_dir={existing.get('data_dir')}. Use --force to reconfigure."
            )
            return common.EXIT_CONFIG

    config = build_config(library_root, data_dir, python, os.getcwd())
    common.write_json_atomic(config_path, config)
    layout = common.DataLayout(data_dir)
    layout.ensure()
    gitignore = data_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Generated RAG artifacts: never commit (copyrighted text).\n*\n", encoding="utf-8")
    print(f"Config written: {config_path}")
    print(json.dumps(config, indent=2, ensure_ascii=False))
    return common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
