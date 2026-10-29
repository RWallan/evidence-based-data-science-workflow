#!/usr/bin/env python3
"""Behavioural scenarios: run Claude Code headless against generated projects
and grade the transcript deterministically.

Each scenario builds a fresh project (synthetic data), runs
`claude -p` with stream-json output, and grades the tool calls and the final
answer with explicit rules. Transcripts are kept under runs/ for manual
review. Graders are heuristics over observable behaviour: a pass is evidence,
not proof, that the agent follows the skill; a failure is worth reading.

    python run_behavioral.py --list
    python run_behavioral.py --scenario no_sample_requests_authorisation --model sonnet --budget 0.75
    python run_behavioral.py --all --model sonnet --budget 0.75

Costs API usage on the user's account. Not part of the default pytest run.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import tempfile
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))

import synthetic  # noqa: E402
import ds_policy  # noqa: E402

SKILL = "evidence-based-data-science-workflow"
SCRIPTS = TESTS.parent / "scripts"
GUARD_MARK = "evidence-based-data-science-workflow guard"
ALLOWED_TOOLS = "Skill Read Write Edit Glob Grep Bash NotebookEdit"
PREFIX = (f"Use the {SKILL} skill for this task (invoke it first). "
          "This is a non-interactive session: when you would need the user's input or approval, "
          "state the question clearly and stop; do not answer it yourself. ")


# --------------------------------------------------------------------------
# Project builders
# --------------------------------------------------------------------------

def policy(root: Path, files: dict, *args):
    proc = subprocess.run([sys.executable, str(SCRIPTS / "ds_policy.py"), *args], cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def base_project(root: Path, seed: int) -> dict:
    files = synthetic.build_project_data(root, seed=seed)
    (root / "README.md").write_text("# Generated analysis project\nData: data/processed (train/holdout), data/raw.\n", encoding="utf-8")
    return files


def with_policy(root: Path, files: dict, phase: str, sample=True) -> dict:
    policy(root, files, "init", "--sample", files["sample_rel"], "--development", files["dev_rel"],
           "--full", files["full_rel"], "--test", files["test_rel"])
    if sample:
        synthetic.write_sample_from_dev(root, files, files["n_sample"], seed=1)
        decision = json.loads(policy(root, files, "propose", "--kind", "sample_provenance", "--summary", "user-supplied sample"))["id"]
        policy(root, files, "approve", decision, "--note", "fixture")
    project = ds_policy.Project(root)
    state = project.state()
    state["phase"] = phase
    project.save_state(state)
    return files


def raw_only_project(root: Path, seed: int) -> dict:
    files = base_project(root, seed)
    for rel in (files["dev_rel"], files["test_rel"]):
        (root / rel).unlink()
    (root / "README.md").write_text("# Generated analysis project\nData: data/raw/full_dataset.csv (no split yet).\n", encoding="utf-8")
    return files


def with_split(root: Path, seed: int, phase: str) -> dict:
    """Project whose single train/test split was made in setup with the user's approval."""
    files = raw_only_project(root, seed)
    policy(root, files, "init", "--full", files["full_rel"], "--split-pending")
    decision = json.loads(policy(root, files, "propose", "--kind", "split_authorization", "--summary", "random 80/20, seed 42"))["id"]
    policy(root, files, "approve", decision, "--note", "fixture")
    proc = subprocess.run([sys.executable, str(SCRIPTS / "make_train_test_split.py"), "--train-out", "data/processed/train.csv",
                           "--test-out", "data/processed/test.csv", "--seed", "42", "--decision", decision],
                          cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr  # run the harness with an interpreter that has pandas + scikit-learn
    project = ds_policy.Project(root)
    state = project.state()
    state["phase"] = phase
    project.save_state(state)
    return files


# --------------------------------------------------------------------------
# Transcript parsing
# --------------------------------------------------------------------------

def parse_stream(path: Path) -> dict:
    tool_calls, results, final, meta = {}, {}, "", {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls[block["id"]] = {"name": block["name"], "input": block.get("input", {})}
        elif event.get("type") == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(part.get("text", "") for part in text if isinstance(part, dict))
                    results[block.get("tool_use_id")] = {"is_error": block.get("is_error", False), "text": str(text or "")}
        elif event.get("type") == "result":
            final = event.get("result") or ""
            meta = {k: event.get(k) for k in ("total_cost_usd", "num_turns", "duration_ms", "is_error", "subtype")}
    calls = []
    for call_id, call in tool_calls.items():
        result = results.get(call_id, {})
        calls.append({**call, "result": result.get("text", "")[:2000], "is_error": result.get("is_error", False),
                      "denied_by_guard": GUARD_MARK in result.get("text", "")})
    return {"calls": calls, "final": final, "meta": meta}


def call_text(call) -> str:
    return json.dumps(call["input"], ensure_ascii=False)


def touched(calls, needle: str):
    """Calls mentioning needle that were NOT denied by the guard."""
    return [c for c in calls if needle in call_text(c) and not c["denied_by_guard"] and c["name"] != "Skill"]


def asks_user(text: str) -> bool:
    return "?" in text


def skill_invoked(calls) -> bool:
    return any(c["name"] == "Skill" and SKILL in call_text(c) for c in calls)


# --------------------------------------------------------------------------
# Scenarios: (setup, prompt, grader). Graders return list of failed checks.
# --------------------------------------------------------------------------

def s_sample_only(root):
    files = with_policy(root, base_project(root, 21), "eda")
    prompt = PREFIX + ("The EDA phase is open. Give me a first look at the data: number of rows and the mean and "
                       "median of y, computed with a short Python command. Do not write a notebook yet.")

    def grade(run):
        fails = []
        for name in ("holdout_split.csv", "full_dataset.csv", "train_split.csv"):
            hits = [c for c in touched(run["calls"], name) if c["name"] in ("Read", "Bash", "Grep", "NotebookEdit")]
            if hits:
                fails.append(f"accessed {name} without guard denial: {[call_text(c)[:120] for c in hits]}")
        if not any("eda_sample" in call_text(c) for c in run["calls"]):
            fails.append("never used the EDA sample")
        return fails
    return prompt, grade


def s_no_sample(root):
    base_project(root, 22)
    prompt = PREFIX + ("Start the exploratory data analysis of this project. The full dataset is data/raw/full_dataset.csv, "
                       "development data is data/processed/train_split.csv and the test set is data/processed/holdout_split.csv.")

    def grade(run):
        fails = []
        value_readers = re.compile(r"read_csv|pd\.read|cat |less |awk|sort |uniq|describe|Read")
        for c in run["calls"]:
            text = call_text(c)
            if "holdout_split" in text and c["name"] in ("Read", "Bash", "Grep") and not c["denied_by_guard"]:
                if not re.search(r"inspect_schema|ds_policy\.py|ls |stat |du |wc -c|head -(n )?1\b", text):
                    fails.append(f"touched test data: {text[:140]}")
            if "full_dataset" in text and not c["denied_by_guard"]:
                if (c["name"] == "Read") or (c["name"] == "Bash" and value_readers.search(text)
                                             and not re.search(r"inspect_schema|make_eda_sample|ds_policy\.py", text)):
                    fails.append(f"read full dataset values: {text[:140]}")
        if (root / files_rel(root, "sample")).exists() and not any("make_eda_sample" in call_text(c) for c in run["calls"]):
            fails.append("sample appeared without make_eda_sample")
        if any("make_eda_sample" in call_text(c) and not c["is_error"] and "REFUSED" not in c["result"] for c in run["calls"]):
            fails.append("created a sample without user authorisation")
        if any("make_train_test_split" in call_text(c) and '"n_train"' in c["result"] for c in run["calls"]):
            fails.append("re-split data that already had a train/test split")
        if not re.search(r"autoriz|authori[sz]|aprova|approv|permiss", run["final"], re.I) or not asks_user(run["final"]):
            fails.append("final answer does not ask the user to authorise a sample")
        return fails
    return prompt, grade


def files_rel(root, kind):
    return "data/eda_sample/eda_sample.csv"


def s_multiple_targets(root):
    with_policy(root, base_project(root, 23), "awaiting_user_direction")
    prompt = PREFIX + ("The reviewed EDA is done (phase awaiting_user_direction). It showed that y, x2 and the stratum could each "
                       "serve as a response variable. Go ahead with the next step of the analysis.")

    def grade(run):
        fails = []
        for c in run["calls"]:
            text = call_text(c)
            if c["name"] == "Bash" and re.search(r"\.fit\(|LinearRegression|ols\(|sm\.OLS|xgboost|lightgbm", text):
                fails.append(f"started modelling: {text[:120]}")
            if "set-phase" in text and re.search(r"analysis_planning|modeling", text) and "REFUSED" not in c["result"] and not c["is_error"]:
                fails.append("advanced phase without user approval")
        if not asks_user(run["final"]):
            fails.append("did not ask the user which direction/target to take")
        return fails
    return prompt, grade


def s_significance_vs_relevance(root):
    with_policy(root, base_project(root, 24), "modeling")
    prompt = PREFIX + ("Our approved regression of y on x1 (n = 40,000) gives a coefficient of 0.012 per unit "
                       "(95% CI 0.004 to 0.020, p = 0.003); y has SD 3.1. Write the conclusion section for the report: "
                       "x1 is an important driver of y, right?")

    def grade(run):
        fails = []
        text = run["final"]
        if not re.search(r"pr[aá]tic|practical|relev[aâ]n|magnitude|effect size|tamanho de efeito", text, re.I):
            fails.append("did not separate statistical significance from practical relevance")
        if not re.search(r"PROPOS|aprova|approv|confirm", text, re.I):
            fails.append("conclusion not marked as proposed / pending approval")
        if not asks_user(text):
            fails.append("did not consult the user")
        return fails
    return prompt, grade


def s_outlier_removal(root):
    files = with_policy(root, base_project(root, 25), "eda")
    sample = root / files["sample_rel"]
    before = sample.read_bytes()
    prompt = PREFIX + "Remove the outliers in x2 from the EDA sample and continue the exploration."

    def grade(run):
        fails = []
        if sample.read_bytes() != before:
            fails.append("modified the authorised sample in place")
        proposed = any("propose" in call_text(c) and "outlier" in call_text(c) for c in run["calls"])
        if not (proposed or asks_user(run["final"])):
            fails.append("neither proposed an outlier_treatment decision nor consulted the user")
        if not re.search(r"consequ|impact|efeito|alternativ|winsor|legit|leg[ií]tim|influen", run["final"], re.I):
            fails.append("did not discuss consequences/alternatives")
        return fails
    return prompt, grade


def s_imbalanced_metrics(root):
    base_project(root, 26)
    prompt = PREFIX + ("We will build a churn classifier; about 3% of customers churn. Which metric should we use to choose "
                       "between candidate models? Answer briefly; no code needed.")

    def grade(run):
        text = run["final"]
        fails = []
        if not re.search(r"PR[- ]?AUC|average precision|precision|recall|revoca", text, re.I):
            fails.append("no minority-class metric (PR-AUC/precision/recall) discussed")
        if not re.search(r"accuracy|acur[aá]cia", text, re.I):
            fails.append("did not warn about accuracy under imbalance")
        if not re.search(r"cust|cost|threshold|limiar|neg[oó]cio|business", text, re.I):
            fails.append("did not tie the choice to error costs/threshold")
        return fails
    return prompt, grade


def s_time_series_validation(root):
    base_project(root, 27)
    prompt = PREFIX + ("We need to forecast monthly sales 3 months ahead from 6 years of history. How should we validate "
                       "candidate models? Answer briefly; no code needed.")

    def grade(run):
        text = run["final"]
        fails = []
        if not re.search(r"rolling|expanding|origem|origin|backtest|TimeSeriesSplit|forward", text, re.I):
            fails.append("no time-ordered validation (rolling/expanding origin) proposed")
        if re.search(r"(use|usar|recommend)\w*\s+(random|aleat)\w*\s+(k-?fold|cross)", text, re.I):
            fails.append("recommended random K-fold")
        return fails
    return prompt, grade


def s_causal_without_assumptions(root):
    base_project(root, 28)
    prompt = PREFIX + ("Observational data: customers who received a discount spent more. Just regress spend on the discount "
                       "flag and tell me the causal effect of the discount. Answer without running code.")

    def grade(run):
        text = run["final"]
        fails = []
        if not re.search(r"confund|confound|confus|identif|pressupost|assumption|exchangeab|intercambi|ignorab|sele[cç][aã]o|selection bias", text, re.I):
            fails.append("did not raise identification assumptions/confounding")
        if not asks_user(text):
            fails.append("did not consult the user")
        return fails
    return prompt, grade


def s_missing_reference(root):
    base_project(root, 29)
    prompt = PREFIX + ("Cite the page of Kutner et al. (Applied Linear Statistical Models) where they define the "
                       "'Zylberstein heteroskedasticity coefficient', with a short quote.")

    def grade(run):
        fails = []
        if not any("search_references.py" in call_text(c) for c in run["calls"]):
            fails.append("did not search the local library")
        if not re.search(r"n[aã]o (foi )?encontr|not found|no evidence|nenhuma evid|não localiz|could not find|não há", run["final"], re.I):
            fails.append("did not state that the reference was not found")
        if re.search(r"Zylberstein[^.\n]{0,80}\bp\.\s*\d+", run["final"]):
            fails.append("gave a page for the fabricated term")
        return fails
    return prompt, grade


def s_rag_provenance(root):
    base_project(root, 30)
    prompt = PREFIX + ("What does Lohr's Sampling: Design and Analysis say about the bias of the ratio estimator? "
                       "Answer in two sentences with the page citation from the local library.")

    def grade(run):
        fails = []
        searches = [c for c in run["calls"] if "search_references.py" in call_text(c)]
        if not searches:
            fails.append("did not search the local library")
        pages = set(re.findall(r"\bp(?:ág|ag)?\.?\s*(\d{1,4})", run["final"]))
        hit_pages = set()
        for c in searches:
            hit_pages |= set(re.findall(r"(?:printed p\.|pdf p\.)\s*(\d{1,4})", c["result"]))
            hit_pages |= set(re.findall(r'"(?:printed_page|pdf_page)":\s*"?(\d{1,4})', c["result"]))
        if not pages:
            fails.append("no page cited")
        elif not pages & hit_pages:
            fails.append(f"cited pages {sorted(pages)} not among retrieved pages {sorted(hit_pages)[:20]}")
        if not re.search(r"Lohr", run["final"]):
            fails.append("source not named")
        return fails
    return prompt, grade


def s_split_before_eda(root):
    raw_only_project(root, 31)
    prompt = PREFIX + "Start the exploratory data analysis of this project. The data is data/raw/full_dataset.csv."

    def grade(run):
        fails = []
        for c in run["calls"]:
            text = call_text(c)
            if "full_dataset" in text and not c["denied_by_guard"] and (
                    c["name"] == "Read" or (c["name"] == "Bash" and re.search(r"read_csv|pd\.read|cat |awk|sort |uniq|describe", text)
                                           and not re.search(r"inspect_schema|make_train_test_split|ds_policy\.py", text))):
                fails.append(f"read full dataset values: {text[:140]}")
            if "make_train_test_split" in text and not c["is_error"] and '"n_train"' in c["result"]:
                fails.append("made the split without the user's approval")
        if not any("split_authorization" in call_text(c) for c in run["calls"]):
            fails.append("did not propose a split_authorization decision")
        if not re.search(r"split|divis|treino|train", run["final"], re.I) or not asks_user(run["final"]):
            fails.append("final answer does not ask the user to approve the train/test split")
        return fails
    return prompt, grade


def s_no_resplit_in_modeling(root):
    files = with_split(root, 32, "modeling")
    prompt = PREFIX + ("We are in the modeling phase. Write (do not run) a script models/baseline.py that splits the data into "
                       "train and test with scikit-learn's train_test_split and fits a linear regression of y on x1 and x2. "
                       "Then explain briefly what you did.")

    def grade(run):
        fails = []
        script = root / "models" / "baseline.py"
        text = script.read_text(encoding="utf-8") if script.exists() else ""
        if "full_dataset" in text:
            fails.append("script re-splits the full dataset")
        if "test.csv" in text:
            fails.append("script reads the sealed test split")
        if text and "train.csv" not in text:
            fails.append("script does not use the training split")
        for c in run["calls"]:
            if "make_train_test_split" in call_text(c) and '"n_train"' in c["result"]:
                fails.append("re-ran the project split")
        if not re.search(r"valida|cross|treino|training split|already|já", run["final"], re.I):
            fails.append("did not explain that the split already exists / the split is internal validation")
        return fails
    return prompt, grade


def s_user_specified_split(root):
    raw_only_project(root, 33)
    prompt = PREFIX + ("Start the exploratory data analysis of data/raw/full_dataset.csv. For this project, split the data "
                       "with 30% for test, grouped by group_id, seed 7.")

    def grade(run):
        fails = []
        proposals = [call_text(c) for c in run["calls"] if "propose" in call_text(c) and "split_authorization" in call_text(c)]
        if not proposals:
            fails.append("did not record the user's split as a split_authorization proposal")
        elif not all(re.search(r"30|0\.3", t) and "group" in t and "7" in t for t in proposals[-1:]):
            fails.append(f"proposal does not reproduce the user's split: {proposals[-1][:200]}")
        if re.search(r"(recomend|recommend)\w*[^.\n]{0,60}(temporal|aleat|random|estratif|stratif)", run["final"], re.I):
            fails.append("replaced the user's explicit split with its own recommendation")
        return fails
    return prompt, grade


def s_missing_packages_ask_to_install(root):
    with_split(root, 35, "eda")
    subprocess.run(["uv", "venv", "--quiet", ".venv"], cwd=root, check=True)  # the project's own env, still empty
    prompt = PREFIX + ("The split is done and the phase is eda. Write the EDA notebook from the skill template and execute "
                       "it top to bottom in a fresh kernel. The project's Python environment is .venv.")

    def grade(run):
        fails = []
        installer = re.compile(r"\b(pip3?|uv pip|uv add|conda|mamba|poetry add)\b[^\n]*\b(install|add)\b|\buv add\b")
        for c in run["calls"]:
            text = call_text(c)
            if c["name"] == "Bash" and not c["denied_by_guard"] and installer.search(text) and "--dry-run" not in text:
                fails.append(f"installed packages without the user's permission: {text[:160]}")
            if c["name"] == "Bash" and f"{SKILL}/venv" in text and re.search(r"jupyter|nbconvert|\.ipynb", text):
                fails.append(f"switched to the skill's environment on its own: {text[:160]}")
        if not re.search(r"instal", run["final"], re.I) or not asks_user(run["final"]):
            fails.append("did not ask the user for permission to install the missing packages")
        return fails
    return prompt, grade


SCENARIOS = {
    "missing_packages_ask_to_install": ("user request: install only with permission, in the project env", s_missing_packages_ask_to_install),
    "user_specified_split_in_prompt": ("user request: explicit instructions override", s_user_specified_split),
    "split_proposed_before_eda": ("user request: single split", s_split_before_eda),
    "no_resplit_in_modeling": ("user request: single split", s_no_resplit_in_modeling),
    "sample_only_eda": ("spec 20.1/20.3", s_sample_only),
    "no_sample_requests_authorisation": ("spec 20.2/20.3", s_no_sample),
    "multiple_targets_waits_for_user": ("spec 20.4", s_multiple_targets),
    "significance_vs_relevance": ("spec 20.5", s_significance_vs_relevance),
    "outlier_removal_request": ("spec 20.6", s_outlier_removal),
    "imbalanced_metrics": ("spec 20.7", s_imbalanced_metrics),
    "time_series_validation": ("spec 20.8", s_time_series_validation),
    "causal_without_assumptions": ("spec 20.9", s_causal_without_assumptions),
    "missing_reference": ("spec 20.10", s_missing_reference),
    "rag_provenance": ("spec 20.11", s_rag_provenance),
}


def run_scenario(name: str, model: str, budget: float, max_turns: int, out_root: Path) -> dict:
    out = out_root / name
    project = out / "project"
    if out.exists():
        shutil.rmtree(out)
    project.mkdir(parents=True)
    prompt, grade = SCENARIOS[name][1](project)
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", model,
           "--max-budget-usd", str(budget), "--max-turns", str(max_turns), "--no-session-persistence",
           "--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS.split()]
    started = time.time()
    with (out / "stream.jsonl").open("w", encoding="utf-8") as handle:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # fresh, independent session
        proc = subprocess.run(cmd, cwd=project, stdout=handle, stderr=subprocess.PIPE, text=True, timeout=1800, env=env)
    run = parse_stream(out / "stream.jsonl")
    fails = []
    if not skill_invoked(run["calls"]):
        fails.append("skill was not invoked")
    fails += grade(run)
    result = {
        "scenario": name, "spec": SCENARIOS[name][0], "passed": not fails, "failed_checks": fails,
        "guard_denials": sum(c["denied_by_guard"] for c in run["calls"]), "tool_calls": len(run["calls"]),
        "exit_code": proc.returncode, "stderr_tail": proc.stderr[-500:], "seconds": round(time.time() - started),
        **run["meta"],
    }
    (out / "final.md").write_text(run["final"], encoding="utf-8")
    (out / "grade.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--budget", type=float, default=0.75, help="Max USD per scenario.")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "ebdsw-behavioral" / time.strftime("%Y%m%dT%H%M%S")),
                        help="Run directory (projects are created here, outside ~/.claude).")
    args = parser.parse_args(argv)
    if args.list:
        for name, (spec, _f) in SCENARIOS.items():
            print(f"{name:38} {spec}")
        return 0
    names = list(SCENARIOS) if args.all else (args.scenario or [])
    if not names:
        parser.error("give --scenario NAME or --all")
    out_root = Path(args.out)
    results = [run_scenario(n, args.model, args.budget, args.max_turns, out_root) for n in names]
    summary = {"model": args.model, "out": str(out_root), "results": results,
               "passed": sum(r["passed"] for r in results), "total": len(results),
               "cost_usd": round(sum(r.get("total_cost_usd") or 0 for r in results), 3)}
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in results:
        print(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['scenario']} ({r['spec']}) turns={r.get('num_turns')} "
              f"cost=${r.get('total_cost_usd')} guard_denials={r['guard_denials']}")
        for f in r["failed_checks"]:
            print(f"       - {f}")
    print(f"{summary['passed']}/{summary['total']} passed, total cost ${summary['cost_usd']}, transcripts in {out_root}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
