#!/usr/bin/env python3
"""Incrementally extract and index the local library (SQLite FTS5 / BM25).

Only new or changed documents are processed; removed documents are dropped
from the index. Originals are opened read-only. Requires PyMuPDF; when the
current interpreter lacks it, the script re-executes itself with the
interpreter recorded in config["python"].

    python3 index_local_books.py              # incremental update
    python3 index_local_books.py --rebuild    # re-extract everything
    python3 index_local_books.py --check      # report staleness only, no writes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402

INDEX_SCHEMA_VERSION = 1
CHUNK_TARGET = 1100
CHUNK_OVERLAP = 200
TOC_MIN_ENTRIES = 10
SPREAD_RATIO = 1.15
MARGIN_SHARE = 0.10
QUALITY_WARN = 0.75

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY, relpath TEXT UNIQUE, format TEXT, sha256 TEXT,
    size INTEGER, mtime_ns INTEGER, page_count INTEGER, status TEXT, error TEXT,
    catalog_json TEXT, pdf_metadata_json TEXT, quality_median REAL,
    printed_page_coverage REAL, section_coverage REAL, indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY, doc_id TEXT, page INTEGER, half TEXT,
    printed_page TEXT, printed_page_method TEXT, section TEXT, section_method TEXT,
    quality REAL, text TEXT
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_page ON chunks(doc_id, page);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, section, tokenize = 'porter unicode61 remove_diacritics 2'
);
"""


# --------------------------------------------------------------------------
# Interpreter bootstrap
# --------------------------------------------------------------------------

def ensure_pymupdf(config: dict, argv: list[str]):
    try:
        import pymupdf  # noqa: F401
        return pymupdf
    except ImportError:
        python = config.get("python")
        # venv interpreters are symlinks to the base binary, so compare unresolved
        # paths and use an env flag to avoid an exec loop.
        if python and Path(python).exists() and not os.environ.get("EBDSW_REEXEC"):
            os.environ["EBDSW_REEXEC"] = "1"
            os.execv(python, [python, str(Path(__file__).resolve()), *argv])
        common.eprint(
            "ERROR: PyMuPDF is not available. Install it in the isolated venv, e.g.\n"
            f"  uv venv {Path(config['data_dir']) / 'venv'} && "
            f"uv pip install --python {Path(config['data_dir']) / 'venv/bin/python'} pymupdf\n"
            "and record the interpreter with setup_rag.py --python <path> --force."
        )
        sys.exit(common.EXIT_CONFIG)


# --------------------------------------------------------------------------
# Printed page numbers and sections
# --------------------------------------------------------------------------

_ROMAN = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
_CHAPTER = re.compile(r"\b(chapter|cap[ií]tulo)\s+(\d{1,2})\b\s*[—–:\-.]?\s*([^\n|]{0,90})", re.I)
_SECTION = re.compile(r"(?:^|\n)\s*(\d{1,2}\.\d{1,2})\s+([A-ZÀ-Ý][^\n]{3,90})")


def _page_token(text: str):
    """Return a page number candidate from the first or last token of a line."""
    tokens = text.strip().split()
    for token in (tokens[:1] + tokens[-1:]) if tokens else []:
        token = token.strip(".,|")
        if token.isdigit() and 0 < int(token) < 5000 and len(token) <= 4:
            return token
        if _ROMAN.match(token) and len(token) <= 7:
            return token.lower()
    return None


def _despace(text: str) -> str:
    """'C A P Í T U L O 2' -> 'CAPÍTULO 2' (letter-spaced running headers)."""
    return re.sub(r"(?<=\b\w) (?=\w\b)", "", text)


def margin_texts(blocks, rect):
    """Texts of blocks in the top and bottom margins of a page region."""
    top_limit = rect.y0 + MARGIN_SHARE * rect.height
    bottom_limit = rect.y1 - MARGIN_SHARE * rect.height
    top = [b[4] for b in blocks if b[3] <= top_limit + 6]
    bottom = [b[4] for b in blocks if b[1] >= bottom_limit - 6]
    return top, bottom


def detect_header_page(blocks, rect):
    top, bottom = margin_texts(blocks, rect)
    for text in top + bottom:
        for line in text.splitlines()[:2]:
            token = _page_token(line)
            if token:
                return token
    return None


def smooth_printed_pages(units: list[dict]) -> None:
    """Accept header page numbers that agree with neighbours; infer gaps."""
    offsets = []
    for seq, unit in enumerate(units):
        token = unit.get("header_token")
        offsets.append(int(token) - seq if token and token.isdigit() else None)
    window = 12
    for seq, unit in enumerate(units):
        if unit.get("printed_page_method") == "pdf_page_label":
            continue
        neighbours = [o for o in offsets[max(0, seq - window): seq + window + 1] if o is not None]
        mode, count = (Counter(neighbours).most_common(1)[0] if neighbours else (None, 0))
        token = unit.get("header_token")
        if token and not token.isdigit():
            unit["printed_page"], unit["printed_page_method"] = token, "running_header_roman"
        elif token and mode is not None and int(token) - seq == mode and count >= 2:
            unit["printed_page"], unit["printed_page_method"] = token, "running_header"
        elif mode is not None and count >= 3 and seq + mode > 0:
            unit["printed_page"] = str(seq + mode)
            unit["printed_page_method"] = "inferred_from_neighbouring_headers"
        elif token:
            unit["printed_page"], unit["printed_page_method"] = token, "running_header_unconfirmed"
        else:
            unit["printed_page"], unit["printed_page_method"] = None, None


def toc_sections(toc, page_count: int) -> dict[int, str]:
    """Map digital page -> 'Chapter > Section' path from the PDF outline."""
    entries = sorted((max(1, p), lvl, title.strip()) for lvl, title, p in toc if p and p > 0)
    result, path, idx = {}, {}, 0
    for page in range(1, page_count + 1):
        while idx < len(entries) and entries[idx][0] <= page:
            _, level, title = entries[idx]
            path = {k: v for k, v in path.items() if k < level}
            path[level] = title
            idx += 1
        if path:
            result[page] = " > ".join(path[k] for k in sorted(path))
    return result


def header_section(top_text: str, state: dict) -> str | None:
    text = _despace(top_text)
    chapter = _CHAPTER.search(text)
    if chapter:
        label = f"{chapter.group(1).capitalize()} {chapter.group(2)}"
        title = re.sub(r"\s+\d+$", "", chapter.group(3).strip(" —–-:|"))
        new_chapter = f"{label} {title}".strip()
        if state.get("chapter_no") != chapter.group(2):
            state["section"] = None
        state["chapter"], state["chapter_no"] = new_chapter, chapter.group(2)
    section = _SECTION.search(text)
    if section:
        state["section"] = f"{section.group(1)} {re.sub(r'\s+\d+$', '', section.group(2).strip())}"
    parts = [p for p in (state.get("chapter"), state.get("section")) if p]
    return " > ".join(parts) if parts else None


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_pdf_like(pymupdf, path: Path, fmt: str) -> tuple[list[dict], dict]:
    doc = pymupdf.open(str(path))
    info = {"page_count": doc.page_count, "pdf_metadata": {k: v for k, v in (doc.metadata or {}).items() if v}}
    labels = [doc[i].get_label() for i in range(doc.page_count)] if fmt == "pdf" else []
    use_labels = labels and sum(1 for label in labels if label) > 0.5 * doc.page_count
    toc = doc.get_toc() if fmt == "pdf" else []
    toc_map = toc_sections(toc, doc.page_count) if len(toc) >= TOC_MIN_ENTRIES else {}
    units, header_state = [], {}
    for index in range(doc.page_count):
        page = doc[index]
        rect = page.rect
        halves = [(None, rect)]
        if fmt == "pdf" and rect.width > SPREAD_RATIO * rect.height:
            mid = rect.x0 + rect.width / 2
            halves = [("L", pymupdf.Rect(rect.x0, rect.y0, mid, rect.y1)),
                      ("R", pymupdf.Rect(mid, rect.y0, rect.x1, rect.y1))]
        for half, clip in halves:
            blocks = page.get_text("blocks", clip=clip, sort=True)
            text = "\n".join(b[4] for b in blocks if b[6] == 0)
            top, _bottom = margin_texts(blocks, clip)
            unit = {"page": index + 1, "half": half, "text": common.clean_extracted_text(text)}
            if fmt == "epub":
                unit["printed_page"], unit["printed_page_method"] = None, "not_applicable_reflowed_epub"
            elif use_labels:
                unit["printed_page"] = labels[index] or None
                unit["printed_page_method"] = "pdf_page_label" if labels[index] else None
            else:
                unit["header_token"] = detect_header_page(blocks, clip)
            if toc_map:
                unit["section"], unit["section_method"] = toc_map.get(index + 1), "pdf_outline"
            else:
                section = header_section("\n".join(top), header_state)
                unit["section"] = section
                unit["section_method"] = "running_header_carried_forward" if section else None
            units.append(unit)
    if fmt == "pdf" and not use_labels:
        smooth_printed_pages(units)
    for unit in units:
        unit.pop("header_token", None)
        if fmt == "epub":
            unit["printed_page_method"] = "not_applicable_reflowed_epub"
    doc.close()
    info["page_labels_used"] = bool(use_labels)
    info["outline_entries"] = len(toc)
    return units, info


def extract_plain_text(path: Path) -> tuple[list[dict], dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    units, section = [], None
    pseudo_pages = [raw[i:i + 3000] for i in range(0, len(raw), 3000)] or [""]
    for number, block in enumerate(pseudo_pages, start=1):
        headings = re.findall(r"^#{1,3}\s+(.+)$", block, re.M)
        if headings:
            section = headings[-1].strip()
        units.append({"page": number, "half": None, "text": common.clean_extracted_text(block),
                      "printed_page": None, "printed_page_method": "not_applicable_text_file",
                      "section": section, "section_method": "markdown_heading" if section else None})
    return units, {"page_count": len(units), "pdf_metadata": {}, "page_labels_used": False, "outline_entries": 0}


def chunk_text(text: str):
    """Overlapping windows cut on whitespace; chunks never cross pages."""
    if len(text) <= CHUNK_TARGET * 1.3:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_TARGET)
        if end < len(text):
            space = text.rfind(" ", start + CHUNK_TARGET // 2, end)
            end = space if space > 0 else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = text.find(" ", max(start + 1, end - CHUNK_OVERLAP))
        start = next_start + 1 if next_start > 0 else end
    return [c for c in chunks if c]


# --------------------------------------------------------------------------
# Catalog (verified bibliographic metadata kept by the user/agent)
# --------------------------------------------------------------------------

def load_catalog(layout: common.DataLayout) -> list[dict]:
    if not layout.catalog.is_file():
        return []
    return json.loads(layout.catalog.read_text(encoding="utf-8")).get("entries", [])


def match_catalog(entries: list[dict], relpath: str, sha256: str) -> dict | None:
    for entry in entries:
        if entry.get("match", {}).get("sha256") == sha256:
            return entry
    for entry in entries:
        if entry.get("match", {}).get("relpath") == relpath:
            return {**entry, "catalog_warning": "matched by relpath only: file content differs from catalogued sha256"}
    return None


# --------------------------------------------------------------------------
# Index maintenance
# --------------------------------------------------------------------------

def open_index(layout: common.DataLayout) -> sqlite3.Connection:
    layout.ensure()
    conn = sqlite3.connect(layout.index_db)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(INDEX_SCHEMA_VERSION),))
    return conn


def drop_document(conn: sqlite3.Connection, layout: common.DataLayout, doc_id: str) -> None:
    ids = [row[0] for row in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))]
    conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    extracted = layout.extracted / f"{doc_id}.jsonl"
    if extracted.exists():
        extracted.unlink()


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else round((values[mid - 1] + values[mid]) / 2, 3)


def index_document(conn, layout, pymupdf, relpath: str, path: Path, st, sha256: str, catalog) -> dict:
    fmt = path.suffix.lower().lstrip(".")
    doc_id = common.doc_id_for(relpath)
    started = time.time()
    record = {"doc_id": doc_id, "relpath": relpath, "format": fmt, "sha256": sha256,
              "size": st.st_size, "mtime_ns": st.st_mtime_ns, "catalog": catalog}
    try:
        if fmt in ("pdf", "epub"):
            units, info = extract_pdf_like(pymupdf, path, fmt)
        else:
            units, info = extract_plain_text(path)
    except Exception as exc:  # extraction failures are recorded, never hidden
        drop_document(conn, layout, doc_id)
        record.update(status="extraction_failed", error=f"{type(exc).__name__}: {exc}", page_count=None)
        conn.execute(
            "INSERT OR REPLACE INTO documents (doc_id, relpath, format, sha256, size, mtime_ns, status, error, catalog_json, indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, relpath, fmt, sha256, st.st_size, st.st_mtime_ns, record["status"], record["error"],
             json.dumps(catalog, ensure_ascii=False), common.utc_now()))
        return record

    drop_document(conn, layout, doc_id)
    empty_pages = sum(1 for u in units if len(u["text"]) < 30)
    with (layout.extracted / f"{doc_id}.jsonl").open("w", encoding="utf-8") as out:
        for unit in units:
            unit["quality"] = common.text_quality(unit["text"])
            out.write(json.dumps({"doc_id": doc_id, **unit}, ensure_ascii=False) + "\n")
            for piece in chunk_text(unit["text"]):
                cur = conn.execute(
                    "INSERT INTO chunks (doc_id, page, half, printed_page, printed_page_method, section, section_method, quality, text)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, unit["page"], unit["half"], unit.get("printed_page"), unit.get("printed_page_method"),
                     unit.get("section"), unit.get("section_method"), unit["quality"], piece))
                conn.execute("INSERT INTO chunks_fts (rowid, text, section) VALUES (?,?,?)",
                             (cur.lastrowid, piece, unit.get("section") or ""))
    quality = median(u["quality"] for u in units)
    printed_cov = round(sum(1 for u in units if u.get("printed_page")) / max(1, len(units)), 3)
    section_cov = round(sum(1 for u in units if u.get("section")) / max(1, len(units)), 3)
    warnings = []
    if empty_pages > 0.2 * max(1, len(units)):
        warnings.append(f"{empty_pages} of {len(units)} page regions have (almost) no text layer: OCR may be required")
    if quality is not None and quality < QUALITY_WARN:
        warnings.append(f"median text quality {quality} < {QUALITY_WARN}: noisy OCR, verify quotes against the original")
    status = "indexed_with_warnings" if warnings else "indexed"
    record.update(status=status, error=None, warnings=warnings, page_count=info["page_count"],
                  page_regions=len(units), page_labels_used=info["page_labels_used"],
                  outline_entries=info["outline_entries"], quality_median=quality,
                  printed_page_coverage=printed_cov, section_coverage=section_cov,
                  pdf_metadata=info["pdf_metadata"], seconds=round(time.time() - started, 1))
    conn.execute(
        "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, relpath, fmt, sha256, st.st_size, st.st_mtime_ns, info["page_count"], status,
         "; ".join(warnings) or None, json.dumps(catalog, ensure_ascii=False),
         json.dumps(info["pdf_metadata"], ensure_ascii=False), quality, printed_cov, section_cov,
         common.utc_now()))
    return record


def run(config: dict, rebuild: bool = False, check_only: bool = False, argv=None) -> dict:
    layout = common.DataLayout(config["data_dir"])
    root = Path(config["library_root"])
    if not root.is_dir():
        common.eprint(f"ERROR: library root {root} is not accessible; index left untouched.")
        sys.exit(common.EXIT_SOURCE_UNAVAILABLE)
    manifest = common.load_manifest(layout)
    files = {rel: (path, st) for rel, path, st in common.iter_library_files(
        root, config["include_extensions"], config["exclude_dirs"])}
    status = common.compare_snapshot(manifest, {r: {"size": s.st_size, "mtime_ns": s.st_mtime_ns} for r, (_, s) in files.items()})
    if check_only:
        return status
    pymupdf = ensure_pymupdf(config, argv or [])
    conn = open_index(layout)
    catalog = load_catalog(layout)
    docs = manifest.setdefault("documents", {})
    todo = sorted(files) if rebuild else sorted(set(status["new"]) | set(status["changed"]) | set(status["failed_extraction"]) & set(files))
    summary = {"processed": [], "skipped_unchanged_content": [], "removed": [], "failed": []}
    for rel in status["removed"]:
        drop_document(conn, layout, docs[rel]["doc_id"])
        docs.pop(rel)
        summary["removed"].append(rel)
        common.append_jsonl(layout.ingest_log, {"at": common.utc_now(), "event": "removed", "relpath": rel})
    for rel in todo:
        path, st = files[rel]
        sha = common.sha256_file(path)
        previous = docs.get(rel)
        entry = match_catalog(catalog, rel, sha)
        if not rebuild and previous and previous.get("sha256") == sha and previous.get("status", "").startswith("indexed"):
            previous.update(size=st.st_size, mtime_ns=st.st_mtime_ns, catalog=entry)
            summary["skipped_unchanged_content"].append(rel)
            continue
        common.eprint(f"Indexing {rel} ...")  # progress on stderr; stdout stays machine-readable JSON
        record = index_document(conn, layout, pymupdf, rel, path, st, sha, entry)
        conn.commit()
        docs[rel] = record
        (summary["failed"] if record["status"] == "extraction_failed" else summary["processed"]).append(rel)
        common.append_jsonl(layout.ingest_log, {"at": common.utc_now(), "event": "indexed", **{k: record.get(k) for k in (
            "relpath", "status", "error", "warnings", "page_count", "quality_median", "seconds")}})
    for rel in status["unchanged"]:  # refresh catalog links without re-extraction
        if rel in docs and rel not in todo:
            docs[rel]["catalog"] = match_catalog(catalog, rel, docs[rel].get("sha256", ""))
            conn.execute("UPDATE documents SET catalog_json = ? WHERE doc_id = ?",
                         (json.dumps(docs[rel]["catalog"], ensure_ascii=False), docs[rel]["doc_id"]))
    conn.commit()
    conn.close()
    manifest.update(schema_version=1, library_root=str(root), updated_at=common.utc_now(),
                    index_schema_version=INDEX_SCHEMA_VERSION)
    common.write_json_atomic(layout.manifest, manifest)
    return summary


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config")
    parser.add_argument("--rebuild", action="store_true", help="Re-extract every document.")
    parser.add_argument("--check", action="store_true", help="Only report what an update would do.")
    args = parser.parse_args(argv)
    try:
        config = common.load_config(args.config)
    except common.ConfigError as exc:
        common.eprint(f"ERROR: {exc}")
        return common.EXIT_CONFIG
    result = run(config, rebuild=args.rebuild, check_only=args.check, argv=argv)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.check:
        return common.EXIT_OK
    return common.EXIT_OK if not result["failed"] else common.EXIT_INDEX


if __name__ == "__main__":
    sys.exit(main())
