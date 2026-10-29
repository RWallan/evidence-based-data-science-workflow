"""Phase gate, human-only approvals, notebook audit, report audit, test evidence."""

import json
import sys

import pytest

import synthetic
from conftest import human_approve, policy_cli, propose, run_script

from check_report import check
from audit_notebook import audit_paths
import ds_policy


def phase(root):
    return json.loads(policy_cli(root, "status").stdout)["phase"]


def clean_notebook(root, project):
    return synthetic.write_notebook(root / "notebooks" / "eda.ipynb", [
        ("markdown", "## Question 1: what does one row represent?", None, []),
        ("code", f"import pandas as pd\nSEED = 1\nraw = pd.read_csv('{project['sample_rel']}')", 1, []),
        ("markdown", "**Observation:** rows are unique per row_id.", None, []),
        ("code", "raw.shape", 2, [{"output_type": "execute_result", "data": {"text/plain": "(80, 6)"}, "metadata": {}, "execution_count": 2}]),
    ])


def tick_checklist(root):
    from conftest import SKILL_DIR
    text = (SKILL_DIR / "templates" / "exploratory-analysis-checklist.md").read_text(encoding="utf-8")
    (root / ".ds-workflow" / "eda-review-checklist.md").write_text(text.replace("- [ ]", "- [x]"), encoding="utf-8")


def reach_awaiting_user(project):
    root = project["root"]
    synthetic.write_sample_from_dev(root, project, project["n_sample"], seed=3)
    human_approve(root, propose(root, "sample_provenance"))
    assert policy_cli(root, "set-phase", "eda").returncode == 0
    nb = clean_notebook(root, project)
    assert policy_cli(root, "set-phase", "eda_review", "--notebook", nb).returncode == 0
    tick_checklist(root)
    assert policy_cli(root, "set-phase", "awaiting_user_direction").returncode == 0
    return root


def test_eda_blocked_without_authorised_sample(project):
    root = project["root"]
    result = policy_cli(root, "set-phase", "eda")
    assert result.returncode == 1
    assert "does not exist" in result.stderr and "authorisation" in result.stderr
    assert phase(root) == "setup"


def test_user_supplied_sample_needs_approved_provenance(project):
    root = project["root"]
    synthetic.write_sample_from_dev(root, project, project["n_sample"], seed=3)
    refused = policy_cli(root, "set-phase", "eda")
    assert refused.returncode == 1 and "provenance" in refused.stderr
    decision = propose(root, "sample_provenance")
    assert policy_cli(root, "set-phase", "eda").returncode == 1  # proposed is not approved
    human_approve(root, decision)
    assert policy_cli(root, "set-phase", "eda").returncode == 0


def test_cannot_skip_phases(project):
    result = policy_cli(project["root"], "set-phase", "modeling")
    assert result.returncode == 1 and "skip" in result.stderr


def test_eda_review_requires_clean_notebook_audit(project):
    root = project["root"]
    synthetic.write_sample_from_dev(root, project, project["n_sample"], seed=3)
    human_approve(root, propose(root, "sample_provenance"))
    policy_cli(root, "set-phase", "eda")
    bad = synthetic.write_notebook(root / "notebooks" / "bad.ipynb", [
        ("code", f"import pandas as pd\ntest = pd.read_csv('{project['test_rel']}')", 1, []),
    ])
    result = policy_cli(root, "set-phase", "eda_review", "--notebook", bad)
    assert result.returncode == 1 and "test_data" in result.stderr
    assert policy_cli(root, "set-phase", "eda_review", "--notebook", clean_notebook(root, project)).returncode == 0


def test_presentation_requires_completed_review_checklist(project):
    root = project["root"]
    synthetic.write_sample_from_dev(root, project, project["n_sample"], seed=3)
    human_approve(root, propose(root, "sample_provenance"))
    policy_cli(root, "set-phase", "eda")
    policy_cli(root, "set-phase", "eda_review", "--notebook", clean_notebook(root, project))
    missing = policy_cli(root, "set-phase", "awaiting_user_direction")
    assert missing.returncode == 1 and "missing" in missing.stderr
    from conftest import SKILL_DIR
    (root / ".ds-workflow" / "eda-review-checklist.md").write_text(
        (SKILL_DIR / "templates" / "exploratory-analysis-checklist.md").read_text(encoding="utf-8"), encoding="utf-8")
    open_items = policy_cli(root, "set-phase", "awaiting_user_direction")
    assert open_items.returncode == 1 and "open checklist item" in open_items.stderr
    tick_checklist(root)
    assert policy_cli(root, "set-phase", "awaiting_user_direction").returncode == 0


def test_modelling_waits_for_user_direction_and_frozen_plan(project, tmp_path):
    root = reach_awaiting_user(project)
    refused = policy_cli(root, "set-phase", "analysis_planning")
    assert refused.returncode == 1 and "analysis_direction" in refused.stderr
    decision = propose(root, "analysis_direction", "target candidates presented to the user")
    assert policy_cli(root, "set-phase", "analysis_planning").returncode == 1
    human_approve(root, decision)
    assert policy_cli(root, "set-phase", "analysis_planning", "--decision", decision).returncode == 0
    plan = root / "docs" / "pre-analysis-plan.md"
    plan.parent.mkdir(parents=True)
    from conftest import SKILL_DIR
    plan.write_text((SKILL_DIR / "templates" / "pre-analysis-plan.md").read_text(encoding="utf-8"), encoding="utf-8")
    no_plan = policy_cli(root, "set-phase", "modeling")
    assert no_plan.returncode == 1 and "pre_analysis_plan" in no_plan.stderr
    assert policy_cli(root, "freeze", "pre_analysis_plan", plan).returncode == 0
    plan_decision = propose(root, "analysis_plan")
    human_approve(root, plan_decision)
    plan.write_text(plan.read_text(encoding="utf-8") + "\nsilent change after freezing\n", encoding="utf-8")
    tampered = policy_cli(root, "set-phase", "modeling")
    assert tampered.returncode == 1 and "changed after it was frozen" in tampered.stderr
    assert policy_cli(root, "freeze", "pre_analysis_plan", plan).returncode == 1  # re-freeze is human-only (unfreeze)


def test_going_back_is_always_allowed(project):
    root = reach_awaiting_user(project)
    assert policy_cli(root, "set-phase", "eda").returncode == 0
    assert phase(root) == "eda"


def test_decision_cannot_be_resolved_twice(project):
    root = project["root"]
    decision = propose(root, "conclusion")
    human_approve(root, decision)
    again = policy_cli(root, "reject", decision)
    assert again.returncode == 1 and "already approved" in again.stderr


def test_approval_written_without_human_cli_is_not_accepted(project):
    """An approval event forged without via=human_cli does not satisfy a gate."""
    root = project["root"]
    decision = propose(root, "analysis_direction")
    with (root / ".ds-workflow" / "decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": decision, "event": "approved", "at": "x", "via": "agent"}) + "\n")
    assert ds_policy.Project(root).approved("analysis_direction") == []


# --------------------------------------------------------------------------
# Notebook audit
# --------------------------------------------------------------------------

def test_audit_flags_out_of_order_execution_errors_and_unmarked_conclusions(project):
    root = project["root"]
    nb = synthetic.write_notebook(root / "notebooks" / "messy.ipynb", [
        ("markdown", "## Question 1: distribution of y", None, []),
        ("code", f"import pandas as pd\nraw = pd.read_csv('{project['sample_rel']}')", 3, []),
        ("code", "raw.y.mean()", 1, [{"output_type": "error", "ename": "KeyError", "evalue": "y", "traceback": []}]),
        ("markdown", "We conclude that region drives y.", None, []),
        ("code", "import numpy as np\nnp.random.choice([1, 2])", 2, []),
    ])
    report = audit_paths([nb], ds_policy.Project(root))
    messages = " | ".join(f["message"] for f in report["findings"])
    assert not report["passed"]
    assert "not 1..n" in messages and "KeyError" in messages and "PROPOSED" in messages
    assert "seed" in messages


def test_audit_accepts_marked_proposals(project):
    root = project["root"]
    nb = synthetic.write_notebook(root / "notebooks" / "ok.ipynb", [
        ("markdown", "## Question 1: y by stratum", None, []),
        ("code", f"import pandas as pd\nraw = pd.read_csv('{project['sample_rel']}')", 1, []),
        ("markdown", "Conclusion (PROPOSED, pending user approval): y differs by stratum.", None, []),
    ])
    assert audit_paths([nb], ds_policy.Project(root))["passed"]


# --------------------------------------------------------------------------
# Report audit and test evidence
# --------------------------------------------------------------------------

def write_report(root, body):
    path = root / "docs" / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


BASE = "# Report\n\n## Observations\n- n = 80 rows.\n\n## Interpretations\n- two readings.\n\n"


def test_unapproved_conclusions_are_rejected(project):
    root = project["root"]
    report = check(write_report(root, BASE + "## Conclusions\n- Stratum A has higher y.\n"), verify_quotes=False)
    assert not report["passed"]
    report = check(write_report(root, BASE + "## Conclusions (PROPOSED)\n- Stratum A has higher y.\n"), verify_quotes=False)
    assert report["passed"], report


def test_approved_tag_must_match_a_human_approval(project):
    root = project["root"]
    decision = propose(root, "conclusion")
    body = BASE + f"## Conclusions\n- Stratum A has higher y. APPROVED ({decision})\n"
    assert not check(write_report(root, body), verify_quotes=False)["passed"]
    human_approve(root, decision)
    assert check(write_report(root, body), verify_quotes=False)["passed"]


def test_test_claims_need_passing_evidence(project, tmp_path):
    root = project["root"]
    assert not check(write_report(root, BASE + "All tests passed.\n"), verify_quotes=False)["passed"]
    ok = run_script("record_test_run.py", "--out-dir", root / "evidence", "--", sys.executable, "-c", "print('fine')", cwd=root)
    assert ok.returncode == 0
    ok_path = ok.stdout.strip().splitlines()[-1].split()[1]
    assert check(write_report(root, BASE + f"All tests passed (evidence: {ok_path}).\n"), verify_quotes=False)["passed"]
    bad = run_script("record_test_run.py", "--out-dir", root / "evidence", "--", sys.executable, "-c", "raise SystemExit(3)", cwd=root)
    assert bad.returncode == 3
    bad_path = bad.stdout.strip().splitlines()[-1].split()[1]
    assert json.loads(open(bad_path).read())["exit_code"] == 3
    report = check(write_report(root, BASE + f"All tests passed (evidence: {bad_path}).\n"), verify_quotes=False)
    assert not report["passed"] and "exit_code=3" in report["findings"][0]["message"]


def test_unknown_citation_keys_are_rejected(project):
    root = project["root"]
    assert not check(write_report(root, BASE + "Method per [@made_up_2099, p. 12].\n"), verify_quotes=False)["passed"]
    assert check(write_report(root, BASE + "Welch test per [@bussab_morettin_estatistica_basica, p. 340].\n"), verify_quotes=False)["passed"]


def test_final_evaluation_reentry_needs_a_fresh_approval(project):
    root = project["root"]
    project_obj = ds_policy.Project(root)
    state = project_obj.state()
    state["phase"] = "modeling"
    project_obj.save_state(state)
    protocol = root / "docs" / "evaluation-protocol.md"
    protocol.parent.mkdir(parents=True, exist_ok=True)
    protocol.write_text("frozen protocol generated for the test\n", encoding="utf-8")
    assert policy_cli(root, "freeze", "evaluation_protocol", protocol).returncode == 0
    human_approve(root, propose(root, "final_evaluation"))
    assert policy_cli(root, "set-phase", "final_evaluation").returncode == 0
    assert policy_cli(root, "set-phase", "modeling").returncode == 0
    again = policy_cli(root, "set-phase", "final_evaluation")
    assert again.returncode == 1 and "re-entry" in again.stderr
    human_approve(root, propose(root, "final_evaluation", "second evaluation, user informed the first was seen"))
    assert policy_cli(root, "set-phase", "final_evaluation").returncode == 0


def test_tampered_sample_manifest_is_rejected(project):
    root = project["root"]
    synthetic.write_sample_from_dev(root, project, project["n_sample"], seed=3)
    (root / ".ds-workflow" / "sample-manifest.json").write_text("{}", encoding="utf-8")
    refused = policy_cli(root, "set-phase", "eda")
    assert refused.returncode == 1 and "hash differs" in refused.stderr and "sample_authorization" in refused.stderr


def test_missing_frozen_file_is_reported_not_crashing(project):
    root = project["root"]
    plan = root / "plan.md"
    plan.write_text("plan generated for the test\n", encoding="utf-8")
    policy_cli(root, "freeze", "pre_analysis_plan", plan)
    plan.unlink()
    status = policy_cli(root, "status")
    assert status.returncode == 0


def test_notebook_approved_tag_must_be_real(project):
    root = project["root"]
    nb = synthetic.write_notebook(root / "notebooks" / "tag.ipynb", [
        ("markdown", "## Question 1: tenure", None, []),
        ("code", f"import pandas as pd\nraw = pd.read_csv('{project['sample_rel']}')", 1, []),
        ("markdown", "We conclude churn is driven by tenure APPROVED (D-999)", None, []),
    ])
    report = audit_paths([nb], ds_policy.Project(root))
    assert not report["passed"] and any("D-999" in f["message"] for f in report["findings"])


def test_page_range_citations_are_checked(project):
    root = project["root"]
    assert not check(write_report(root, BASE + "See [@made_up_2099, pp. 12-15].\n"), verify_quotes=False)["passed"]


def test_record_test_run_survives_timeout_with_output(project):
    root = project["root"]
    code = "import sys, time; print('partial output', flush=True); time.sleep(5)"
    result = run_script("record_test_run.py", "--timeout", 1, "--out-dir", root / "ev", "--", sys.executable, "-c", code, cwd=root)
    assert result.returncode == 124
    record = json.loads(open(result.stdout.strip().splitlines()[-1].split()[1]).read())
    assert record["exit_code"] == 124 and "partial output" in record["stdout_tail"]
