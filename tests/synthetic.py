"""Programmatic, seeded generators for test data (no hand-written records).

* build_project_data: CSV files for an analysis project (rows generated
  from random draws; groups, strata and a numeric target).
* build_pdf_library: PDFs built with PyMuPDF from a generated vocabulary,
  with planted 'needle' sentences at known pages so retrieval and
  provenance can be asserted exactly.
* build_notebook: .ipynb JSON assembled from generated parts.
"""

from __future__ import annotations

import csv
import json
import random
import string
from pathlib import Path


def random_word(rng: random.Random, low=4, high=10) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(low, high)))


def vocabulary(rng: random.Random, size=400) -> list[str]:
    words = set()
    while len(words) < size:
        words.add(random_word(rng))
    return sorted(words)


def generate_rows(rng: random.Random, n_rows: int, n_groups: int, strata: list[str]):
    for i in range(n_rows):
        yield {
            "row_id": f"r{i:06d}",
            "group_id": f"g{rng.randrange(n_groups):04d}",
            "stratum": rng.choices(strata, weights=[0.6, 0.3, 0.1][: len(strata)] or None)[0],
            "x1": round(rng.gauss(0, 1), 4),
            "x2": round(rng.expovariate(1.0), 4),
            "y": round(rng.gauss(10, 3), 4),
            "day": rng.randrange(0, 730),
        }


def write_csv(path: Path, rows) -> int:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def build_project_data(root: Path, seed: int, n_dev=400, n_test=120, n_sample=80) -> dict:
    rng = random.Random(seed)
    strata = [f"s_{random_word(rng, 3, 5)}" for _ in range(3)]
    dev = list(generate_rows(rng, n_dev, n_groups=40, strata=strata))
    test = list(generate_rows(rng, n_test, n_groups=12, strata=strata))
    rels = {
        "sample_rel": "data/eda_sample/eda_sample.csv",
        "dev_rel": "data/processed/train_split.csv",
        "full_rel": "data/raw/full_dataset.csv",
        "test_rel": "data/processed/holdout_split.csv",
    }
    write_csv(root / rels["dev_rel"], dev)
    write_csv(root / rels["test_rel"], test)
    write_csv(root / rels["full_rel"], dev + test)
    (root / "notebooks").mkdir(parents=True, exist_ok=True)
    return {**rels, "strata": strata, "n_dev": n_dev, "n_test": n_test, "n_sample": n_sample}


def write_sample_from_dev(root: Path, rels: dict, n: int, seed: int) -> Path:
    """User-supplied sample scenario: rows drawn programmatically from dev data."""
    with (root / rels["dev_rel"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    chosen = random.Random(seed).sample(rows, n)
    write_csv(root / rels["sample_rel"], chosen)
    return root / rels["sample_rel"]


def build_notebook(cells: list[tuple[str, str, int | None, list]]) -> dict:
    """cells: (type, source, execution_count, outputs)."""
    out = []
    for i, (kind, source, count, outputs) in enumerate(cells):
        cell = {"cell_type": kind, "id": f"c{i}", "metadata": {}, "source": source}
        if kind == "code":
            cell.update(execution_count=count, outputs=outputs)
        out.append(cell)
    return {"cells": out, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


def write_notebook(path: Path, cells) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_notebook(cells)), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# PDFs
# --------------------------------------------------------------------------

def _paragraph(rng, vocab, n_words=60) -> str:
    return " ".join(rng.choice(vocab) for _ in range(n_words)) + "."


def _write_body(page, text, rect, fontsize=9):
    import pymupdf
    page.insert_textbox(pymupdf.Rect(rect), text, fontsize=fontsize, fontname="helv")


def build_pdf_library(library: Path, seed: int) -> dict:
    """Three generated PDFs:

    labelled.pdf  - page labels starting at 101, outline with chapters/sections
    headers.pdf   - no labels; page number printed in the top margin (offset 20)
    spread.pdf    - landscape two-page spreads, header numbers left/right
    Needles: unique generated phrases planted at known locations.
    """
    import pymupdf

    rng = random.Random(seed)
    vocab = vocabulary(rng)
    library.mkdir(parents=True, exist_ok=True)
    needles = {}

    def needle():
        return " ".join(random_word(rng, 9, 12) for _ in range(6))

    # labelled.pdf
    doc = pymupdf.open()
    for i in range(12):
        page = doc.new_page(width=420, height=595)
        body = _paragraph(rng, vocab) + " " + _paragraph(rng, vocab)
        if i == 7:
            needles["labelled"] = {"phrase": needle(), "pdf_page": 8, "printed_page": "108", "file": "labelled.pdf"}
            body = _paragraph(rng, vocab, 30) + " " + needles["labelled"]["phrase"] + " " + _paragraph(rng, vocab, 30)
        _write_body(page, body, (40, 80, 380, 560))
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 101}])
    toc = []
    for chapter in range(1, 4):
        start = (chapter - 1) * 4 + 1
        toc.append([1, f"Chapter {chapter} {random_word(rng).capitalize()}", start])
        toc.append([2, f"{chapter}.1 {random_word(rng).capitalize()}", start])
        toc.append([2, f"{chapter}.2 {random_word(rng).capitalize()}", start + 2])
        toc.append([2, f"{chapter}.3 {random_word(rng).capitalize()}", start + 3])
    doc.set_toc(toc)
    # pdf page 8 lies in chapter 2 (toc[4]), section 2.3 (toc[7], starts at page 8)
    needles["labelled"]["chapter"] = toc[4][1]
    needles["labelled"]["section_contains"] = toc[7][1]
    doc.save(library / "labelled.pdf")
    doc.close()

    # headers.pdf
    doc = pymupdf.open()
    chapter_title = random_word(rng).capitalize()
    for i in range(14):
        page = doc.new_page(width=420, height=595)
        printed = i + 21
        header = f"{printed} Chapter 4 {chapter_title}" if i % 2 else f"Chapter 4 {chapter_title} {printed}"
        page.insert_text((40, 30), header, fontsize=9, fontname="helv")
        body = _paragraph(rng, vocab) + " " + _paragraph(rng, vocab)
        if i == 9:
            needles["headers"] = {"phrase": needle(), "pdf_page": 10, "printed_page": "30", "file": "headers.pdf",
                                  "section_contains": f"Chapter 4 {chapter_title}"}
            body = _paragraph(rng, vocab, 30) + " " + needles["headers"]["phrase"] + " " + _paragraph(rng, vocab, 30)
        _write_body(page, body, (40, 70, 380, 560))
    doc.save(library / "headers.pdf")
    doc.close()

    # spread.pdf
    doc = pymupdf.open()
    for i in range(8):
        page = doc.new_page(width=840, height=595)
        left, right = 2 * i + 51, 2 * i + 52
        page.insert_text((40, 30), f"{left} Chapter 2 {chapter_title}", fontsize=9, fontname="helv")
        page.insert_text((640, 30), f"Section {random_word(rng)} {right}", fontsize=9, fontname="helv")
        left_body, right_body = _paragraph(rng, vocab), _paragraph(rng, vocab)
        if i == 5:
            needles["spread"] = {"phrase": needle(), "pdf_page": 6, "printed_page": str(right), "half": "R", "file": "spread.pdf"}
            right_body = _paragraph(rng, vocab, 20) + " " + needles["spread"]["phrase"] + " " + _paragraph(rng, vocab, 20)
        _write_body(page, left_body, (40, 70, 390, 560))
        _write_body(page, right_body, (450, 70, 800, 560))
    doc.save(library / "spread.pdf")
    doc.close()
    return {"needles": needles, "vocab": vocab}
