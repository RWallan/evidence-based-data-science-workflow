"""Ingestion, retrieval, provenance and citation verification on generated PDFs."""

import json

from conftest import run_script


def search(lib, *args):
    result = run_script("search_references.py", *args, "--json", env=lib["env"], cwd=lib["library"].parent)
    return result, (json.loads(result.stdout) if result.stdout.strip().startswith("{") else None)


def test_needle_found_with_page_label_and_outline_section(synthetic_library):
    needle = synthetic_library["needles"]["labelled"]
    result, payload = search(synthetic_library, needle["phrase"], "--k", "3")
    assert result.returncode == 0
    top = payload["results"][0]["locator"]
    assert top["relpath"] == "labelled.pdf"
    assert top["pdf_page"] == needle["pdf_page"]
    assert top["printed_page"] == needle["printed_page"] and top["printed_page_method"] == "pdf_page_label"
    assert top["section_method"] == "pdf_outline"
    assert needle["chapter"] in top["section"] and needle["section_contains"] in top["section"]


def test_printed_page_from_running_headers_without_labels(synthetic_library):
    needle = synthetic_library["needles"]["headers"]
    _result, payload = search(synthetic_library, needle["phrase"])
    top = payload["results"][0]["locator"]
    assert top["relpath"] == "headers.pdf" and top["pdf_page"] == needle["pdf_page"]
    assert top["printed_page"] == needle["printed_page"]
    assert top["printed_page_method"] == "running_header"
    assert needle["section_contains"] in (top["section"] or "")


def test_two_page_spreads_are_split_into_halves(synthetic_library):
    needle = synthetic_library["needles"]["spread"]
    _result, payload = search(synthetic_library, needle["phrase"])
    top = payload["results"][0]["locator"]
    assert top["relpath"] == "spread.pdf" and top["pdf_page"] == needle["pdf_page"]
    assert top["page_half"] == needle["half"] and top["printed_page"] == needle["printed_page"]


def test_uncatalogued_sources_are_flagged_unverified(synthetic_library):
    needle = synthetic_library["needles"]["labelled"]
    _result, payload = search(synthetic_library, needle["phrase"])
    assert payload["results"][0]["source"]["metadata_source"].startswith("UNVERIFIED")


def test_no_evidence_is_reported_not_invented(synthetic_library):
    result = run_script("search_references.py", "zzqxjv wqpzkx", env=synthetic_library["env"], cwd=synthetic_library["library"].parent)
    assert result.returncode == 1
    assert "NO EVIDENCE FOUND" in result.stdout
    assert "says nothing about the wider literature" in result.stdout


def test_multiple_queries_are_fused(synthetic_library):
    n1, n2 = synthetic_library["needles"]["labelled"], synthetic_library["needles"]["headers"]
    _result, payload = search(synthetic_library, "-q", n1["phrase"], "-q", n2["phrase"], "--k", "4")
    files = {r["locator"]["relpath"] for r in payload["results"][:2]}
    assert files == {"labelled.pdf", "headers.pdf"}


def test_doc_filter_restricts_results(synthetic_library):
    needle = synthetic_library["needles"]["labelled"]
    result, payload = search(synthetic_library, needle["phrase"], "--doc", "headers")
    assert all(r["locator"]["relpath"] == "headers.pdf" for r in (payload or {}).get("results", []))


def test_context_is_capped(synthetic_library):
    needle = synthetic_library["needles"]["labelled"]
    _result, payload = search(synthetic_library, needle["phrase"], "--context", "50000")
    assert len(payload["results"][0]["context"]) <= 1500 + 8


def test_retrievals_are_logged(synthetic_library):
    needle = synthetic_library["needles"]["headers"]
    search(synthetic_library, needle["phrase"])
    log = (synthetic_library["data_dir"] / "logs" / "retrievals.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(log[-1])
    assert record["queries"] == [needle["phrase"]] and record["hits"][0]["pdf_page"] == needle["pdf_page"]


def verify(lib, *args):
    return run_script("verify_citation.py", *args, "--json", env=lib["env"], cwd=lib["library"].parent)


def test_verify_citation_verified_wrong_page_and_fabricated(synthetic_library):
    needle = synthetic_library["needles"]["labelled"]
    ok = verify(synthetic_library, "--doc", "labelled", "--pdf-page", needle["pdf_page"], "--quote", needle["phrase"].upper())
    assert ok.returncode == 0 and json.loads(ok.stdout)["status"] == "VERIFIED"
    by_printed = verify(synthetic_library, "--doc", "labelled", "--printed-page", needle["printed_page"], "--quote", needle["phrase"])
    assert json.loads(by_printed.stdout)["status"] == "VERIFIED"
    wrong = verify(synthetic_library, "--doc", "labelled", "--pdf-page", 2, "--quote", needle["phrase"])
    payload = json.loads(wrong.stdout)
    assert wrong.returncode == 1 and payload["status"] == "WRONG_PAGE"
    assert payload["found_on"][0]["pdf_page"] == needle["pdf_page"]
    vocab = synthetic_library["vocab"]
    fabricated = " ".join(vocab[i] for i in (3, 141, 59, 265, 35, 89))
    fake = verify(synthetic_library, "--doc", "labelled", "--pdf-page", needle["pdf_page"], "--quote", fabricated)
    assert fake.returncode == 1 and json.loads(fake.stdout)["status"] in ("NOT_FOUND", "PARTIAL")


def test_verify_citation_unknown_document(synthetic_library):
    result = verify(synthetic_library, "--doc", "no_such_book", "--pdf-page", 1, "--quote", "a b c d")
    assert result.returncode == 3 and json.loads(result.stdout)["status"] == "DOCUMENT_NOT_FOUND"


def test_validate_rag_healthy_on_synthetic_library(synthetic_library):
    result = run_script("validate_rag.py", "--query", synthetic_library["needles"]["labelled"]["phrase"],
                        env=synthetic_library["env"], cwd=synthetic_library["library"].parent)
    # exit 1 = usable with warnings (generated PDFs have no catalog entries)
    assert result.returncode == 1, result.stdout
    assert "[OK] retrieval_from_other_cwd" in result.stdout
    assert "without verified catalog metadata" in result.stdout
