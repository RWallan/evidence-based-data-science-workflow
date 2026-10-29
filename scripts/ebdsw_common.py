"""Shared helpers for the evidence-based-data-science-workflow scripts.

Everything here is standard library only, so the search, guard and audit
scripts run with any Python >= 3.10. Only index_local_books.py needs PyMuPDF.

Path model (three separate locations, never derived from the current
working directory):

* library root  - the user's books, read-only, recorded in config.json
* data dir      - generated artifacts (manifest, extracted text, index, logs)
* skill dir     - this skill's code and documentation
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "evidence-based-data-science-workflow"
CONFIG_ENV_VAR = "EBDSW_CONFIG"
CONFIG_SCHEMA_VERSION = 1
SKILL_DIR = Path(__file__).resolve().parent.parent

DEFAULT_EXTENSIONS = [".pdf", ".epub", ".txt", ".md"]
DEFAULT_EXCLUDED_DIRS = [
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", ".ipynb_checkpoints", ".cache", ".mypy_cache",
    ".pytest_cache", ".tox", "site-packages",
]

# Exit codes shared by the RAG scripts (documented in references/local-books-rag.md).
EXIT_OK = 0
EXIT_NO_EVIDENCE = 1
EXIT_CONFIG = 2
EXIT_INDEX = 3
EXIT_SOURCE_UNAVAILABLE = 4


class ConfigError(Exception):
    """Raised when the RAG configuration is missing or invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def xdg_config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".config"


def xdg_data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".local" / "share"


def default_config_path() -> Path:
    return xdg_config_home() / APP_NAME / "config.json"


def default_data_dir() -> Path:
    return xdg_data_home() / APP_NAME


def resolve_config_path(explicit: str | None = None) -> Path:
    """Resolution order: --config flag, $EBDSW_CONFIG, XDG default."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    return default_config_path()


def load_config(explicit: str | None = None) -> dict:
    path = resolve_config_path(explicit)
    if not path.is_file():
        raise ConfigError(
            f"RAG config not found at {path}. Run scripts/setup_rag.py "
            f"--library-root <books dir> once (see references/local-books-rag.md)."
        )
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"RAG config at {path} is not valid JSON: {exc}") from exc
    for key in ("library_root", "data_dir"):
        value = config.get(key)
        if not value:
            raise ConfigError(f"RAG config at {path} is missing '{key}'.")
        if not os.path.isabs(value):
            raise ConfigError(
                f"RAG config '{key}' must be an absolute path, got {value!r}. "
                f"Relative paths break when Claude Code starts in another directory."
            )
    config.setdefault("include_extensions", DEFAULT_EXTENSIONS)
    config.setdefault("exclude_dirs", DEFAULT_EXCLUDED_DIRS)
    config["_config_path"] = str(path)
    return config


def write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class DataLayout:
    """Paths of the persistent RAG artifacts inside data_dir."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.manifest = self.root / "manifest.json"
        self.extracted = self.root / "extracted_text"
        self.index_dir = self.root / "index"
        self.index_db = self.index_dir / "references.sqlite"
        self.metadata = self.root / "metadata"
        self.catalog = self.metadata / "catalog.json"
        self.logs = self.root / "logs"
        self.ingest_log = self.logs / "ingest.jsonl"
        self.retrieval_log = self.logs / "retrievals.jsonl"

    def ensure(self) -> None:
        for path in (self.root, self.extracted, self.index_dir, self.metadata, self.logs):
            path.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Library discovery (read-only)
# --------------------------------------------------------------------------

def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def doc_id_for(relpath: str) -> str:
    """Stable id derived from the path relative to the library root."""
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:16]


def iter_library_files(root: Path, extensions, excluded_dirs):
    """Yield (relpath, absolute path, stat) for candidate documents under root.

    Never follows directory symlinks out of the authorised root.
    """
    root = root.resolve()
    exts = {e.lower() for e in extensions}
    excluded = set(excluded_dirs)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded and not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in exts or name.startswith("."):
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                st = path.stat()
            except (OSError, ValueError):
                continue  # broken symlink, unreadable file, or link pointing outside the authorised root
            yield path.relative_to(root).as_posix(), path, st


def library_snapshot(config: dict) -> dict:
    """Cheap stat-only snapshot {relpath: {size, mtime_ns}} of the library."""
    root = Path(config["library_root"])
    snapshot = {}
    for relpath, _path, st in iter_library_files(
        root, config["include_extensions"], config["exclude_dirs"]
    ):
        snapshot[relpath] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    return snapshot


def load_manifest(layout: DataLayout) -> dict:
    if not layout.manifest.is_file():
        return {"schema_version": 1, "documents": {}}
    return json.loads(layout.manifest.read_text(encoding="utf-8"))


def compare_snapshot(manifest: dict, snapshot: dict) -> dict:
    """Classify documents as new, changed, removed or unchanged (stat based)."""
    indexed = manifest.get("documents", {})
    new = sorted(set(snapshot) - set(indexed))
    removed = sorted(set(indexed) - set(snapshot))
    changed = sorted(
        rel for rel in set(snapshot) & set(indexed)
        if snapshot[rel]["size"] != indexed[rel].get("size")
        or snapshot[rel]["mtime_ns"] != indexed[rel].get("mtime_ns")
    )
    failed = sorted(
        rel for rel, doc in indexed.items()
        if doc.get("status") not in ("indexed", "indexed_with_warnings")
    )
    unchanged = sorted(set(snapshot) & set(indexed) - set(changed))
    return {
        "new": new, "changed": changed, "removed": removed,
        "failed_extraction": failed, "unchanged": unchanged,
        "is_stale": bool(new or changed or removed),
    }


def library_health(config: dict) -> dict:
    """Report root accessibility, index presence and staleness."""
    layout = DataLayout(config["data_dir"])
    root = Path(config["library_root"])
    health = {
        "library_root": str(root),
        "data_dir": str(layout.root),
        "root_accessible": root.is_dir() and os.access(root, os.R_OK | os.X_OK),
        "index_present": layout.index_db.is_file(),
        "manifest_present": layout.manifest.is_file(),
        "warnings": [],
    }
    manifest = load_manifest(layout)
    health["indexed_documents"] = len(manifest.get("documents", {}))
    health["built_at"] = manifest.get("updated_at")
    if not health["root_accessible"]:
        health["warnings"].append(
            f"Library root {root} is not accessible: the original books cannot be "
            f"checked. Any result comes only from the index built at "
            f"{health['built_at']} and must be reported as such."
        )
        health["staleness"] = None
    else:
        staleness = compare_snapshot(manifest, library_snapshot(config))
        health["staleness"] = staleness
        for kind in ("new", "changed", "removed"):
            if staleness[kind]:
                health["warnings"].append(
                    f"Index is stale: {kind} document(s): {', '.join(staleness[kind])}. "
                    f"Run scripts/index_local_books.py to update incrementally."
                )
        if staleness["failed_extraction"]:
            health["warnings"].append(
                "Extraction failed or incomplete for: "
                + ", ".join(staleness["failed_extraction"])
            )
    if not health["index_present"]:
        health["warnings"].append(
            f"Index not found at {layout.index_db}. Run scripts/index_local_books.py."
        )
    return health


# --------------------------------------------------------------------------
# Text normalisation (used by indexing, search and citation verification)
# --------------------------------------------------------------------------

_HYPHEN_BREAK = re.compile(r"(\w)[-­]\s*\n\s*(\w)")
_WS = re.compile(r"\s+")


def clean_extracted_text(text: str) -> str:
    """Join hyphenated line breaks and collapse whitespace."""
    text = text.replace("\r", "\n").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    return _WS.sub(" ", text).strip()


def fold_for_matching(text: str) -> str:
    """Casefold, strip diacritics and punctuation: used to compare quotes."""
    text = clean_extracted_text(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return _WS.sub(" ", text).strip()


_CLEAN_TOKEN = re.compile(r"^[^\W\d_]{2,}[.,;:!?)\]]*$")


def text_quality(text: str) -> float | None:
    """Share of letter-bearing tokens that look like clean words.

    Pure numbers and formulas are ignored (statistics books are full of them);
    OCR noise shows up as letters mixed with digits/symbols ('Ill/alysis', 'tl7(').
    """
    tokens = [t for t in text.split() if len(t) >= 2 and any(ch.isalpha() for ch in t)]
    if len(tokens) < 20:
        return None
    clean = sum(1 for t in tokens if _CLEAN_TOKEN.match(t.strip("(\"'")))
    return round(clean / len(tokens), 3)


# --------------------------------------------------------------------------
# Project data policy (used by the guard hook, sampling and audits)
# --------------------------------------------------------------------------

POLICY_DIR_NAME = ".ds-workflow"
POLICY_FILE = "data-policy.json"
STATE_FILE = "state.json"
DECISIONS_FILE = "decisions.jsonl"


def find_project_root(start: str | Path | None) -> Path | None:
    """Walk up from start looking for .ds-workflow/data-policy.json."""
    candidates = []
    if start:
        candidates.append(Path(start))
    env_project = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_project:
        candidates.append(Path(env_project))
    for candidate in candidates:
        try:
            current = candidate.expanduser().resolve()
        except OSError:
            continue
        for directory in (current, *current.parents):
            if (directory / POLICY_DIR_NAME / POLICY_FILE).is_file():
                return directory
    return None


def eprint(*args) -> None:
    print(*args, file=sys.stderr)
