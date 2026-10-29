#!/usr/bin/env python3
"""Static pre-presentation audit of analysis notebooks (and .py scripts).

Errors (block presentation and the eda -> eda_review transition):
  * code references data that is protected in the current phase (test set;
    full/development data during EDA)
  * error outputs stored in the notebook
  * execution counts not strictly 1..n in cell order (notebook not run top
    to bottom in a fresh kernel) or outputs without execution counts
  * conclusion wording in markdown not marked PROPOSED / pending user approval
Warnings:
  * causal wording in markdown
  * the EDA sample is never referenced
  * no exploratory questions stated in markdown
  * randomness without an explicit seed
  * plots without axis labels/titles

    audit_notebook.py notebooks/01_eda.ipynb [--json]
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

QUESTION_MARKERS = re.compile(r"(^|\n)\s*(#+\s*)?(\*\*)?(question|pergunta|q\d+\b|eda question)", re.I)
CONCLUSION_WORDS = re.compile(r"\b(we conclude|conclusions?|conclus[aãõ](o|es)|conclu[ií]mos|demonstra que|proves?|prova que)\b", re.I)
CAUSAL_WORDS = re.compile(r"\b(causes|caused by|effect of|leads to|impact of|causa(m)?|efeito d[eao]s?|leva a)\b", re.I)
PENDING_MARK = re.compile(r"\b(PROPOSED|PROPOSTA|pending user (approval|review)|aguardando (aprova[cç][aã]o|o usu[aá]rio))", re.I)
APPROVED_TAG = re.compile(r"APPROVED\s*\((D-\d+)\)")
RANDOM_CALL = re.compile(r"\b(np\.random\.(rand|randn|choice|permutation|shuffle|normal|uniform)|random\.(random|choice|shuffle|sample)|\.sample\(|train_test_split\(|KFold\(|StratifiedKFold\(|RandomForest\w*\(|KMeans\()")
SEED_HINT = re.compile(r"(random_state\s*=|seed\s*\(|default_rng\(\s*\d|np\.random\.seed|random\.seed|SEED\s*=)")
PLOT_CALL = re.compile(r"\b(plt\.(plot|scatter|hist|bar|boxplot)|sns\.\w+plot|\.plot\(|px\.\w+\(|ax\.(plot|scatter|hist|bar))")
LABEL_HINT = re.compile(r"(set_xlabel|xlabel\(|set_title|title\(|labels\s*=|set\(\s*xlabel|update_layout|x_label|title=)")


def load_cells(path: Path) -> list[dict]:
    if path.suffix == ".ipynb":
        nb = json.loads(path.read_text(encoding="utf-8"))
        cells = []
        for cell in nb.get("cells", []):
            source = cell.get("source", "")
            cells.append({**cell, "source": "".join(source) if isinstance(source, list) else source})
        return cells
    return [{"cell_type": "code", "source": path.read_text(encoding="utf-8"), "outputs": [], "execution_count": None, "_script": True}]


def audit_file(path: Path, project) -> list[dict]:
    findings = []

    def add(severity, message, cell=None):
        findings.append({"file": str(path), "severity": severity, "message": message, "cell": cell})

    try:
        cells = load_cells(path)
    except (OSError, json.JSONDecodeError) as exc:
        add("error", f"cannot read: {exc}")
        return findings
    code = [(i, c) for i, c in enumerate(cells) if c.get("cell_type") == "code"]
    markdown = [(i, c) for i, c in enumerate(cells) if c.get("cell_type") == "markdown"]
    all_code = "\n".join(c["source"] for _i, c in code)
    approved_ids = ({d["id"] for d in project.decisions().values()
                     if d["status"] == "approved" and d.get("resolved_via") == "human_cli"} if project else set())

    def marked(paragraph: str, cell_index) -> bool:
        if PENDING_MARK.search(paragraph):
            return True
        tags = APPROVED_TAG.findall(paragraph)
        for tag in tags:
            if tag not in approved_ids:
                add("error", f"APPROVED ({tag}) does not refer to a decision approved by the user", cell_index)
        return bool(tags)

    if project is not None:
        policy, phase = project.policy(), project.state()["phase"]
        protected = ds_policy.protected_roles_for_phase(phase)
        for i, cell in code:
            role = ds_policy.find_protected_mention(project, policy, cell["source"], protected)
            if role:
                add("error", f"references {role} which is protected in phase '{phase}'", i)
        if phase in ds_policy.SAMPLE_ONLY_PHASES and policy["eda_sample"]:
            sample_literals = [lit for lit, _r in ds_policy.path_strings(project, policy, {"eda_sample"})]
            if not any(lit in all_code for lit in sample_literals):
                add("warning", "the authorised EDA sample is never referenced; confirm which data the notebook reads")

    if path.suffix == ".ipynb":
        counts = [c.get("execution_count") for _i, c in code if c["source"].strip()]
        if any(c is None for c in counts) and any(c is not None for c in counts):
            add("error", "some code cells were never executed: run the notebook top to bottom in a fresh kernel")
        executed = [c for c in counts if c is not None]
        if executed and executed != list(range(1, len(executed) + 1)):
            add("error", f"execution counts {executed[:12]}{'...' if len(executed) > 12 else ''} are not 1..n in order: outputs may be stale or out of order")
        if not executed and code:
            add("error", "notebook has no executed cells: execute it before presenting")
        for i, cell in code:
            for output in cell.get("outputs", []):
                if output.get("output_type") == "error":
                    add("error", f"stored error output: {output.get('ename')}: {output.get('evalue', '')[:120]}", i)
                if output.get("output_type") == "stream" and output.get("name") == "stderr" and "Warning" in "".join(output.get("text", "")):
                    add("warning", "stderr warnings stored in outputs: review them", i)
        if not any(QUESTION_MARKERS.search(c["source"]) for _i, c in markdown):
            add("warning", "no exploratory questions found in markdown (e.g. '## Question 1: ...')")
        for i, cell in markdown:
            for paragraph in re.split(r"\n\s*\n", cell["source"]):
                if CONCLUSION_WORDS.search(paragraph) and not marked(paragraph, i):
                    add("error", "conclusion-like statement not marked PROPOSED / pending user approval: "
                                 + paragraph.strip()[:100], i)
                elif CAUSAL_WORDS.search(paragraph) and not marked(paragraph, i):
                    add("warning", "causal wording: EDA shows associations; keep causal language for an approved causal protocol: "
                                   + paragraph.strip()[:100], i)
    if RANDOM_CALL.search(all_code) and not SEED_HINT.search(all_code):
        add("warning", "randomness without an explicit seed/random_state")
    for i, cell in code:
        if PLOT_CALL.search(cell["source"]) and not LABEL_HINT.search(cell["source"]):
            add("warning", "plot without explicit axis labels/title in the same cell", i)
    return findings


def audit_paths(paths: list[Path], project=None) -> dict:
    if project is None:
        root = common.find_project_root(paths[0].resolve().parent if paths else os.getcwd()) or common.find_project_root(os.getcwd())
        project = ds_policy.Project(root) if root else None
    findings = []
    for path in paths:
        findings += audit_file(path, project)
    errors = sum(1 for f in findings if f["severity"] == "error")
    return {"files": [str(p) for p in paths], "policy": str(project.policy_path) if project else None,
            "errors": errors, "warnings": len(findings) - errors, "findings": findings, "passed": errors == 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = audit_paths([Path(p) for p in args.paths])
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for f in report["findings"]:
            cell = f" cell {f['cell']}" if f["cell"] is not None else ""
            print(f"{f['severity'].upper()}: {f['file']}{cell}: {f['message']}")
        print(f"{'PASSED' if report['passed'] else 'FAILED'}: {report['errors']} error(s), {report['warnings']} warning(s)")
        if report["policy"] is None:
            print("NOTE: no data policy found, data-access checks were skipped")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
