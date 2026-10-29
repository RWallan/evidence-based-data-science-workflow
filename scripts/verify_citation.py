#!/usr/bin/env python3
"""Check that a quotation really occurs at the cited location of a local book.

    python3 verify_citation.py --doc kutner2005 --pdf-page 571 --quote "did not suggest any serious heteroscedasticity"
    python3 verify_citation.py --doc lohr_sampling --printed-page 66 --quote "ratio estimator is biased"

Matching ignores case, accents, punctuation and hyphenated line breaks. If the
quote is absent from the cited page, the other pages of the same document are
scanned so a wrong locator can be corrected. Exit 0 = VERIFIED, 1 = not
verified (PARTIAL / NOT_FOUND / WRONG_PAGE), 2 = config, 3 = index/document missing.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402

PARTIAL_THRESHOLD = 0.85


def find_document(conn, doc: str):
    rows = conn.execute("SELECT doc_id, relpath, catalog_json FROM documents").fetchall()
    exact, partial = [], []
    for doc_id, relpath, catalog_json in rows:
        key = (json.loads(catalog_json) or {}).get("key") if catalog_json else None
        if doc in (doc_id, key, relpath):
            exact.append((doc_id, relpath))
        elif doc.casefold() in relpath.casefold():
            partial.append((doc_id, relpath))
    return exact or partial


def load_pages(layout: common.DataLayout, doc_id: str) -> list[dict]:
    path = layout.extracted / f"{doc_id}.jsonl"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def best_fuzzy(folded_quote: str, folded_text: str) -> float:
    """Best similarity of the quote against same-length windows of the text."""
    words, qwords = folded_text.split(), folded_quote.split()
    n = len(qwords)
    if not n or not words:
        return 0.0
    best = 0.0
    step = max(1, n // 4)
    for start in range(0, max(1, len(words) - n + 1), step):
        window = " ".join(words[start:start + n])
        best = max(best, difflib.SequenceMatcher(None, folded_quote, window).ratio())
        if best > 0.99:
            break
    return round(best, 3)


def verify(config: dict, doc: str, quote: str, pdf_page: int | None = None, printed_page: str | None = None) -> dict:
    layout = common.DataLayout(config["data_dir"])
    if not layout.index_db.is_file():
        return {"status": "INDEX_MISSING", "detail": f"index not found at {layout.index_db}"}
    conn = sqlite3.connect(f"file:{layout.index_db}?mode=ro", uri=True)
    try:
        docs = find_document(conn, doc)
    finally:
        conn.close()
    if len(docs) != 1:
        return {"status": "DOCUMENT_NOT_FOUND" if not docs else "AMBIGUOUS_DOCUMENT",
                "detail": [d[1] for d in docs]}
    doc_id, relpath = docs[0]
    pages = load_pages(layout, doc_id)
    if not pages:
        return {"status": "DOCUMENT_NOT_FOUND", "detail": f"no extracted text for {relpath}"}
    folded_quote = common.fold_for_matching(quote)
    if len(folded_quote.split()) < 3:
        return {"status": "QUOTE_TOO_SHORT", "detail": "quote at least 3 words so the check is meaningful"}

    def is_cited(unit):
        if pdf_page is not None and unit["page"] != pdf_page:
            return False
        if printed_page is not None and str(unit.get("printed_page")).casefold() != str(printed_page).casefold():
            return False
        return True

    cited = [u for u in pages if is_cited(u)]
    result = {"doc_id": doc_id, "relpath": relpath, "pdf_page": pdf_page, "printed_page": printed_page}
    if not cited:
        result.update(status="LOCATOR_NOT_FOUND", detail="no page region matches the cited page")
    else:
        text = " ".join(common.fold_for_matching(u["text"]) for u in cited)
        if folded_quote in text:
            result.update(status="VERIFIED", similarity=1.0)
            return result
        score = best_fuzzy(folded_quote, text)
        result.update(status="PARTIAL" if score >= PARTIAL_THRESHOLD else "NOT_FOUND", similarity=score)
    elsewhere = [
        {"pdf_page": u["page"], "page_half": u.get("half"), "printed_page": u.get("printed_page")}
        for u in pages if folded_quote in common.fold_for_matching(u["text"]) and not is_cited(u)
    ]
    if elsewhere:
        result["status"] = "WRONG_PAGE" if result["status"] != "PARTIAL" else "PARTIAL"
        result["found_on"] = elsewhere[:10]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc", required=True, help="Catalog key, doc_id or file-name substring.")
    parser.add_argument("--quote", required=True)
    parser.add_argument("--pdf-page", type=int)
    parser.add_argument("--printed-page")
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.pdf_page is None and args.printed_page is None:
        parser.error("give --pdf-page and/or --printed-page")
    try:
        config = common.load_config(args.config)
    except common.ConfigError as exc:
        common.eprint(f"ERROR: {exc}")
        return common.EXIT_CONFIG
    result = verify(config, args.doc, args.quote, args.pdf_page, args.printed_page)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{result['status']}: {json.dumps({k: v for k, v in result.items() if k != 'status'}, ensure_ascii=False)}")
    if result["status"] == "VERIFIED":
        return common.EXIT_OK
    if result["status"] in ("INDEX_MISSING", "DOCUMENT_NOT_FOUND", "AMBIGUOUS_DOCUMENT"):
        return common.EXIT_INDEX
    return common.EXIT_NO_EVIDENCE


if __name__ == "__main__":
    sys.exit(main())
