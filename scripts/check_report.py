#!/usr/bin/env python3
"""Quality gate for an analysis report/answer (Markdown) before it is shown.

Errors:
  * a 'Conclusions'/'Recommendations' item is neither marked PROPOSED nor
    linked to a decision approved by the user (APPROVED (D-012))
  * an APPROVED (D-xxx) tag points to a decision that is not approved
  * a claim that tests passed without 'evidence: <file>' pointing to a
    record_test_run.py record with exit_code 0
  * a citation key that is neither in references/bibliography.json nor in
    the local catalog
  * a quotation with a local locator that verify_citation.py cannot verify
Warnings:
  * missing Observations / Interpretations sections
  * causal wording outside an approved causal claim

Citation syntax understood:
  [@kutner2005, p. 537]                      (printed page)
  [@kutner2005, pdf p. 571]                  (PDF page)
  > "quoted words" [@kutner2005, pdf p. 571] (verified against the extracted text)

    check_report.py report.md [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402
import ds_policy  # noqa: E402

SECTION = re.compile(r"^#{1,4}\s+(.*)$", re.M)
CONCLUSION_HEAD = re.compile(r"conclus|recommend|recomenda", re.I)
OBS_HEAD = re.compile(r"observa", re.I)
INTERP_HEAD = re.compile(r"interpreta", re.I)
PROPOSED = re.compile(r"\b(PROPOSED|PROPOSTA|PROPOSTO|pending user approval|aguardando aprova[cç][aã]o)\b", re.I)
APPROVED_TAG = re.compile(r"APPROVED\s*\((D-\d+)\)")
TEST_CLAIM = re.compile(r"(all\s+)?tests?\s+(passed|pass|are passing|succeeded)|testes?\s+(passaram|aprovados?|passando)", re.I)
EVIDENCE = re.compile(r"evidence:\s*`?([^\s`)\]]+)`?", re.I)
CITATION = re.compile(r"\[@([A-Za-z0-9_:\-]+)(?:,\s*(pdf\s+)?pp?\.\s*([0-9ivxlcdm]+)(?:\s*[-–]\s*[0-9ivxlcdm]+)?)?\]", re.I)
QUOTE = re.compile(r'^>\s*["“](.+?)["”]\s*(\[@[^\]]+\])', re.M)
CAUSAL = re.compile(r"\b(causes|caused by|effect of|leads to|causa(m)?|efeito causal|leva a)\b", re.I)


def bibliography_keys() -> set[str]:
    path = common.SKILL_DIR / "references" / "bibliography.json"
    keys = {e["key"] for e in json.loads(path.read_text(encoding="utf-8"))["entries"]}
    try:
        layout = common.DataLayout(common.load_config()["data_dir"])
        if layout.catalog.is_file():
            keys |= {e["key"] for e in json.loads(layout.catalog.read_text(encoding="utf-8"))["entries"] if e.get("key")}
    except common.ConfigError:
        pass
    return keys


def sections(text: str) -> list[tuple[str, str]]:
    heads = list(SECTION.finditer(text))
    out = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((head.group(1), text[head.end():end]))
    return out


def items(body: str) -> list[str]:
    bullets = re.findall(r"^\s*(?:[-*]|\d+\.)\s+(.+(?:\n(?!\s*(?:[-*]|\d+\.)\s).+)*)", body, re.M)
    if bullets:
        return bullets
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def check(path: Path, verify_quotes: bool = True) -> dict:
    text = path.read_text(encoding="utf-8")
    findings = []

    def add(severity, message):
        findings.append({"severity": severity, "message": message})

    root = common.find_project_root(path.resolve().parent) or common.find_project_root(os.getcwd())
    decisions = ds_policy.Project(root).decisions() if root else {}
    secs = sections(text)
    if not any(OBS_HEAD.search(h) for h, _b in secs):
        add("warning", "no 'Observations' section: separate what the data show from interpretation")
    if not any(INTERP_HEAD.search(h) for h, _b in secs):
        add("warning", "no 'Interpretations' section")

    for head, body in secs:
        if not CONCLUSION_HEAD.search(head):
            continue
        section_marked = bool(PROPOSED.search(head))
        for item in items(body):
            tags = APPROVED_TAG.findall(item)
            if tags:
                for decision_id in tags:
                    decision = decisions.get(decision_id)
                    if not decision or decision["status"] != "approved" or decision.get("resolved_via") != "human_cli":
                        add("error", f"'{head}': {decision_id} is not a decision approved by the user: {item[:90]}")
            elif not (section_marked or PROPOSED.search(item)):
                add("error", f"'{head}': item neither PROPOSED nor APPROVED (D-xxx): {item[:90]}")

    for match in TEST_CLAIM.finditer(text):
        line = text[text.rfind("\n", 0, match.start()) + 1: text.find("\n", match.end()) if text.find("\n", match.end()) > 0 else len(text)]
        ev = EVIDENCE.search(line)
        if not ev:
            add("error", f"test claim without 'evidence: <record>' on the same line: {line.strip()[:90]}")
            continue
        record_path = Path(ev.group(1))
        record_path = record_path if record_path.is_absolute() else path.resolve().parent / record_path
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("exit_code") != 0:
                add("error", f"test claim contradicts evidence {record_path.name}: exit_code={record.get('exit_code')}")
        except (OSError, json.JSONDecodeError):
            add("error", f"test evidence {record_path} missing or unreadable")

    keys = bibliography_keys()
    for match in CITATION.finditer(text):
        if match.group(1) not in keys:
            add("error", f"citation key @{match.group(1)} not in references/bibliography.json or the local catalog")

    if verify_quotes:
        try:
            config = common.load_config()
            from verify_citation import verify
        except common.ConfigError:
            config = None
        for quote_match in QUOTE.finditer(text):
            cite = CITATION.search(quote_match.group(2))
            if not cite or not cite.group(3):
                add("error", f"quotation without page locator: {quote_match.group(1)[:60]}")
                continue
            if config is None:
                add("warning", "RAG config unavailable: quotation not verified")
                continue
            page = cite.group(3)
            kwargs = {"pdf_page": int(page)} if cite.group(2) and page.isdigit() else {"printed_page": page}
            result = verify(config, cite.group(1), quote_match.group(1), **kwargs)
            if result["status"] == "DOCUMENT_NOT_FOUND":
                add("warning", f"@{cite.group(1)} is not a local document: quotation must be checked against the external source")
            elif result["status"] != "VERIFIED":
                add("error", f"quotation not verified ({result['status']}): \"{quote_match.group(1)[:60]}\" {quote_match.group(2)}")

    approved_causal = any(d["kind"] == "causal_claim" and d["status"] == "approved" and d.get("resolved_via") == "human_cli"
                          for d in decisions.values())
    if CAUSAL.search(text) and not approved_causal:
        add("warning", "causal wording present but no approved causal_claim decision: check identification assumptions")

    errors = sum(1 for f in findings if f["severity"] == "error")
    return {"file": str(path), "errors": errors, "warnings": len(findings) - errors, "findings": findings, "passed": errors == 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-quote-verification", action="store_true")
    args = parser.parse_args(argv)
    report = check(Path(args.path), verify_quotes=not args.no_quote_verification)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for f in report["findings"]:
            print(f"{f['severity'].upper()}: {f['message']}")
        print(f"{'PASSED' if report['passed'] else 'FAILED'}: {report['errors']} error(s), {report['warnings']} warning(s)")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
