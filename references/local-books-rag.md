# Local books RAG: setup, use, maintenance

## Three separate locations

| What | Where (this machine) | Notes |
|---|---|---|
| Library root (books) | recorded in config as `library_root` (`setup_rag.py --show`) | read-only; never moved, renamed or modified |
| Config | `$EBDSW_CONFIG`, else `$XDG_CONFIG_HOME/evidence-based-data-science-workflow/config.json`, else `~/.config/evidence-based-data-science-workflow/config.json` | absolute paths only |
| Data dir (artifacts) | `data_dir` in config (default `~/.local/share/evidence-based-data-science-workflow/`) | `manifest.json`, `extracted_text/`, `index/references.sqlite`, `metadata/catalog.json`, `logs/`, `venv/` |
| Skill | `~/.claude/skills/evidence-based-data-science-workflow/` | code and docs only, no book content |

No script depends on the current working directory, so the RAG works from any project.

## Commands (`S=~/.claude/skills/evidence-based-data-science-workflow/scripts`)

| Task | Command |
|---|---|
| First setup (run inside the books directory) | `python3 $S/setup_rag.py --library-root "$PWD"` |
| Move/rename the library or new machine | `python3 $S/setup_rag.py --library-root /new/path --force` (then `index_local_books.py`) |
| Show config | `python3 $S/setup_rag.py --show` |
| Inventory (read-only) | `python3 $S/discover_books.py` |
| Update index incrementally | `python3 $S/index_local_books.py` |
| What would change? | `python3 $S/index_local_books.py --check` |
| Full rebuild | `python3 $S/index_local_books.py --rebuild` |
| Search | `python3 $S/search_references.py -q "..." [-q "..."] [--doc KEY] [--k N] [--context CHARS] [--json] [--strict]` |
| Verify a quote | `python3 $S/verify_citation.py --doc KEY --pdf-page N --quote "..."` (or `--printed-page P`) |
| Health check | `python3 $S/validate_rag.py` |

Indexing needs PyMuPDF; `index_local_books.py` re-executes itself with `config["python"]` (the isolated venv in the data dir). Searching, verification and health checks use only the standard library.

## How retrieval works

- Extraction: PyMuPDF, page by page. Pages scanned as two-page spreads (landscape) are split into left/right halves.
- Printed page numbers: PDF page labels when present (`pdf_page_label`); otherwise numbers detected in running headers/footers and accepted only when consistent with neighbouring pages (`running_header`), inferred from neighbours for pages without a header (`inferred_from_neighbouring_headers`), or unconfirmed (`running_header_unconfirmed`). The method is shown with every result.
- Sections: PDF outline when it has ≥ 10 entries (`pdf_outline`), else chapter/section running headers carried forward (`running_header_carried_forward`).
- Chunks: ~1100 characters with 200 overlap, never crossing page boundaries, so every chunk has an exact page.
- Ranking: SQLite FTS5 BM25 (`porter unicode61 remove_diacritics 2`). A query first requires all terms, then falls back to any term (reported as a NOTE). Multiple `-q` queries are fused with reciprocal rank fusion — use it to query Portuguese and English books together.
- Text quality: share of clean word tokens per page; results below 0.75 carry a noisy-OCR warning.
- Every search is appended to `logs/retrievals.jsonl` (queries, cwd, hits).

Lexical search misses paraphrases and synonyms: try several phrasings and technical terms in the book's language. Semantic (embedding) search is a possible extension; it was deliberately not installed (it requires downloading a model) — ask the user before adding it.

## Metadata and the local catalog

Bibliographic metadata shown in results comes from `metadata/catalog.json`, written from each copy's title/copyright page and matched by SHA-256 (renames are harmless; a changed file is flagged `matched by relpath only`). Documents without a catalog entry show `UNVERIFIED` metadata (embedded PDF metadata can be wrong). Missing fields stay `null`.

Which editions are held locally is recorded per machine in `metadata/catalog.json` and shown in every search result header. When the local edition differs from the one in `bibliography.json`, cite the edition you actually consulted.

## Exit codes and what to tell the user

| Code | Meaning | Say |
|---|---|---|
| 0 | results | cite with locator; pass on warnings |
| 1 | no evidence in the local library | "not found in the local library"; consult external sources; never invent |
| 2 | config missing/invalid | RAG not configured on this machine; give the setup command |
| 3 | index missing/corrupt | run `index_local_books.py` (or `--rebuild`); no retrieval happened |
| 4 | `--strict` and library unavailable/stale | report it; do not present results as checked against the books |

Warnings that must reach the user: library root inaccessible (results come only from the previously built index), stale index (new/changed/removed books), failed extraction, noisy OCR, inferred printed pages, uncatalogued source, source outside the methodological catalog.

## Updating the library

Add, replace or remove files in the library root, then run `index_local_books.py`: only new/changed files are re-extracted, removed files are dropped. Add a catalog entry for new books (read the copyright page; leave unknown fields `null`) and run the indexer again to attach it. OCR is not installed: scanned books without a text layer are reported with a warning ("OCR may be required"); install OCR tooling only with the user's agreement.

## Privacy and copyright

Processing is local. Do not send book text to external services. Quote briefly (search snippets and `--context` are capped) and paraphrase with citation. The data dir contains a `.gitignore` that ignores everything; never commit extracted text or the index.
