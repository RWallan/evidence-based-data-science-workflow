#!/usr/bin/env python3
"""Search the local library index (BM25) and print passages with provenance.

Standard library only: works from any directory with any python3.

    python3 search_references.py "heteroscedasticity residual plot"
    python3 search_references.py -q "intervalo de confiança" -q "confidence interval" --k 8
    python3 search_references.py "ratio estimation" --doc lohr_sampling --context 600 --json

Several -q queries (e.g. Portuguese and English phrasings) are fused with
reciprocal-rank fusion. Exit codes: 0 results, 1 no evidence found,
2 config problem, 3 index missing/corrupt, 4 library unavailable and --strict.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402

RRF_K = 60


class IndexMissing(Exception):
    pass
QUALITY_WARN = 0.75
MAX_CONTEXT = 1500  # keep quotations short (copyright): never print whole pages
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on",
    "or", "that", "the", "to", "with", "what", "how", "when", "which", "o", "os", "as", "um", "uma",
    "de", "do", "da", "dos", "das", "e", "em", "no", "na", "nos", "nas", "para", "por", "com", "que",
    "se", "ao", "aos", "como", "qual", "quando",
}


def fts_queries(query: str) -> list[tuple[str, str]]:
    """Build FTS5 MATCH expressions: quoted phrases kept, then AND, then OR."""
    phrases = re.findall(r'"([^"]+)"', query)
    rest = re.sub(r'"[^"]+"', " ", query)
    terms = [t for t in re.findall(r"[\w][\w\-']*", rest, re.UNICODE) if t.casefold() not in STOPWORDS]
    quoted = [f'"{p.replace(chr(34), "")}"' for p in phrases] + [f'"{t}"' for t in terms]
    if not quoted:
        return []
    attempts = [("all_terms", " AND ".join(quoted))]
    if len(quoted) > 1:
        attempts.append(("any_term", " OR ".join(quoted)))
    return attempts


def run_query(conn, query: str, k: int, doc_filter: list[str] | None):
    for mode, expr in fts_queries(query):
        sql = (
            "SELECT f.rowid, bm25(chunks_fts, 1.0, 0.4) AS score,"
            " snippet(chunks_fts, 0, '[', ']', ' ... ', 24) AS snip"
            " FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.rowid"
            " WHERE chunks_fts MATCH ?"
        )
        params: list = [expr]
        if doc_filter:
            sql += f" AND c.doc_id IN ({','.join('?' * len(doc_filter))})"
            params += doc_filter
        sql += " ORDER BY score LIMIT ?"
        params.append(k * 3)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"FTS query failed for {expr!r}: {exc}") from exc
        if rows:
            return mode, rows
    return None, []


def resolve_doc_filter(conn, doc_args) -> list[str] | None:
    if not doc_args:
        return None
    ids = []
    for doc_id, relpath, catalog_json in conn.execute("SELECT doc_id, relpath, catalog_json FROM documents"):
        key = (json.loads(catalog_json) or {}).get("key") if catalog_json else None
        for wanted in doc_args:
            if wanted in (doc_id, key) or wanted.casefold() in relpath.casefold():
                ids.append(doc_id)
    return sorted(set(ids)) or ["__none__"]


def describe_source(catalog: dict | None, pdf_meta: dict | None, relpath: str) -> dict:
    if catalog:
        return {
            "key": catalog.get("key"),
            "title": catalog.get("title"),
            "authors": catalog.get("authors"),
            "edition": catalog.get("edition"),
            "year": catalog.get("year"),
            "publisher": catalog.get("publisher"),
            "metadata_source": "verified catalog (metadata/catalog.json)",
            "out_of_methodological_catalog": bool(catalog.get("out_of_methodological_catalog")),
            "catalog_warning": catalog.get("catalog_warning"),
        }
    return {
        "key": None,
        "title": (pdf_meta or {}).get("title") or Path(relpath).stem,
        "authors": [(pdf_meta or {}).get("author")] if (pdf_meta or {}).get("author") else None,
        "edition": None, "year": None, "publisher": None,
        "metadata_source": "UNVERIFIED: embedded PDF metadata or file name; add a catalog entry before citing",
        "out_of_methodological_catalog": None,
    }


def context_window(text: str, snippet: str, size: int) -> str:
    size = min(size, MAX_CONTEXT)
    probe = re.sub(r"[\[\]]|\s*\.\.\.\s*", " ", snippet).split()
    anchor = text.find(" ".join(probe[:4])) if probe else -1
    if anchor < 0:
        anchor = 0
    start = max(0, anchor - size // 3)
    return ("... " if start else "") + text[start:start + size] + (" ..." if start + size < len(text) else "")


def search(config: dict, queries: list[str], k: int = 5, doc_args=None, context: int = 0) -> dict:
    layout = common.DataLayout(config["data_dir"])
    health = common.library_health(config)
    if not health["index_present"]:
        raise IndexMissing(f"Index not found at {layout.index_db}")
    conn = sqlite3.connect(f"file:{layout.index_db}?mode=ro", uri=True)
    try:
        doc_filter = resolve_doc_filter(conn, doc_args)
        fused: dict[int, dict] = {}
        modes = {}
        for query in queries:
            mode, rows = run_query(conn, query, k, doc_filter)
            modes[query] = mode
            for rank, (rowid, score, snip) in enumerate(rows, start=1):
                hit = fused.setdefault(rowid, {"rrf": 0.0, "snippet": snip, "bm25": score, "matched_queries": []})
                hit["rrf"] += 1.0 / (RRF_K + rank)
                hit["matched_queries"].append(query)
        ranked = sorted(fused.items(), key=lambda item: -item[1]["rrf"])[:k]
        results = []
        for position, (rowid, hit) in enumerate(ranked, start=1):
            row = conn.execute(
                "SELECT c.doc_id, d.relpath, c.page, c.half, c.printed_page, c.printed_page_method,"
                " c.section, c.section_method, c.quality, c.text, d.catalog_json, d.pdf_metadata_json"
                " FROM chunks c JOIN documents d ON d.doc_id = c.doc_id WHERE c.chunk_id = ?", (rowid,)).fetchone()
            (doc_id, relpath, page, half, printed, printed_method, section, section_method,
             quality, text, catalog_json, pdf_meta_json) = row
            catalog = json.loads(catalog_json) if catalog_json else None
            source = describe_source(catalog, json.loads(pdf_meta_json) if pdf_meta_json else None, relpath)
            warnings = []
            if quality is not None and quality < QUALITY_WARN:
                warnings.append(f"noisy OCR text (quality {quality}): check wording in the original before quoting")
            if printed_method and printed_method != "pdf_page_label" and printed_method.startswith(("inferred", "running_header_unconfirmed")):
                warnings.append(f"printed page {printed} is {printed_method}")
            if source["out_of_methodological_catalog"]:
                warnings.append("source is outside the methodological catalog: do not use it to justify statistical decisions")
            result = {
                "rank": position, "chunk_id": rowid, "rrf_score": round(hit["rrf"], 5), "bm25": round(hit["bm25"], 3),
                "matched_queries": hit["matched_queries"], "source": source,
                "locator": {
                    "relpath": relpath, "doc_id": doc_id, "pdf_page": page, "page_half": half,
                    "printed_page": printed, "printed_page_method": printed_method,
                    "section": section, "section_method": section_method,
                },
                "snippet": hit["snippet"], "text_quality": quality, "warnings": warnings,
            }
            if context:
                result["context"] = context_window(text, hit["snippet"], context)
            results.append(result)
    finally:
        conn.close()
    record = {
        "at": common.utc_now(), "cwd": os.getcwd(), "queries": queries, "doc_filter": doc_args,
        "match_modes": modes, "n_results": len(results),
        "hits": [{"doc_id": r["locator"]["doc_id"], "pdf_page": r["locator"]["pdf_page"],
                  "printed_page": r["locator"]["printed_page"], "chunk_id": r["chunk_id"]} for r in results],
    }
    try:
        common.append_jsonl(layout.retrieval_log, record)
    except OSError:
        health["warnings"].append("could not append to the retrieval log")
    return {"queries": queries, "match_modes": modes, "health": health, "results": results}


def format_text(payload: dict) -> str:
    lines = []
    for warning in payload["health"]["warnings"]:
        lines.append(f"WARNING: {warning}")
    if not payload["results"]:
        lines.append("NO EVIDENCE FOUND in the local library for: " + " | ".join(payload["queries"]))
        lines.append("This says nothing about the wider literature: consult references/scientific-bibliography.md "
                     "and external primary sources, and report the gap explicitly.")
        return "\n".join(lines)
    for mode_query, mode in payload["match_modes"].items():
        if mode == "any_term":
            lines.append(f"NOTE: no passage contains all terms of {mode_query!r}; results match some terms only.")
    for r in payload["results"]:
        src, loc = r["source"], r["locator"]
        authors = "; ".join(src["authors"] or []) or "unknown authors"
        page = f"pdf p.{loc['pdf_page']}{loc['page_half'] or ''}"
        if loc["printed_page"]:
            page += f", printed p.{loc['printed_page']} ({loc['printed_page_method']})"
        lines.append(f"[{r['rank']}] {authors}. {src['title']}. ed.={src['edition']} year={src['year']} key={src['key']}")
        lines.append(f"    {page} | section: {loc['section'] or 'unknown'} | file: {loc['relpath']} | chunk {r['chunk_id']}")
        lines.append(f"    metadata: {src['metadata_source']}")
        lines.append(f"    snippet: {r['snippet']}")
        if "context" in r:
            lines.append(f"    context: {r['context']}")
        for warning in r["warnings"]:
            lines.append(f"    WARNING: {warning}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", nargs="?", help="Search text (quote exact phrases with double quotes).")
    parser.add_argument("-q", "--query", dest="extra", action="append", default=[], help="Additional query (repeatable).")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--doc", action="append", help="Restrict to catalog key, doc_id or file-name substring (repeatable).")
    parser.add_argument("--context", type=int, default=0, help=f"Characters of surrounding text to show (max {MAX_CONTEXT}).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail (exit 4) if the library root is unavailable or the index is stale.")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    queries = ([args.query] if args.query else []) + args.extra
    if not queries:
        parser.error("give a query")
    try:
        config = common.load_config(args.config)
    except common.ConfigError as exc:
        common.eprint(f"ERROR: {exc}")
        return common.EXIT_CONFIG
    try:
        payload = search(config, queries, k=args.k, doc_args=args.doc, context=args.context)
    except IndexMissing as exc:
        common.eprint(f"ERROR: {exc}. Run scripts/index_local_books.py. No retrieval was performed.")
        return common.EXIT_INDEX
    except (sqlite3.DatabaseError, RuntimeError) as exc:
        common.eprint(f"ERROR: index unreadable: {exc}. Rebuild with scripts/index_local_books.py --rebuild.")
        return common.EXIT_INDEX
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else format_text(payload))
    health = payload["health"]
    if args.strict and (not health["root_accessible"] or (health["staleness"] or {}).get("is_stale")):
        return common.EXIT_SOURCE_UNAVAILABLE
    return common.EXIT_OK if payload["results"] else common.EXIT_NO_EVIDENCE


if __name__ == "__main__":
    sys.exit(main())
