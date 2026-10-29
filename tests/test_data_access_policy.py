"""The PreToolUse guard enforces sample-only EDA and the sealed test set."""

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import ds_policy
import guard_data_access as guard
from conftest import policy_cli, run_script

RESTRICTED_PHASES = ["setup", "eda", "eda_review", "awaiting_user_direction", "analysis_planning"]


def set_phase_raw(root: Path, phase: str):
    """Test helper: write the phase directly (transition rules are tested elsewhere)."""
    project = ds_policy.Project(root)
    state = project.state()
    state["phase"] = phase
    project.save_state(state)


def payload(root, tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(root), "hook_event_name": "PreToolUse"}


def denied(root, tool, **tool_input):
    return guard.evaluate(payload(root, tool, **tool_input))


@pytest.mark.parametrize("phase", RESTRICTED_PHASES + ["modeling"])
def test_test_data_is_sealed_before_final_evaluation(project, phase):
    root = project["root"]
    set_phase_raw(root, phase)
    test_abs = str(root / project["test_rel"])
    assert "TEST data" in denied(root, "Read", file_path=test_abs)
    assert denied(root, "Bash", command=f"head -5 {project['test_rel']}")
    assert denied(root, "Bash", command="wc -l holdout_split.csv")  # basename match
    assert denied(root, "Grep", pattern="r0001", path=str(root / "data" / "processed"))


def test_test_data_readable_in_final_evaluation(project):
    root = project["root"]
    set_phase_raw(root, "final_evaluation")
    assert denied(root, "Read", file_path=str(root / project["test_rel"])) is None


@pytest.mark.parametrize("phase", RESTRICTED_PHASES)
def test_full_and_development_data_blocked_while_sample_only(project, phase):
    root = project["root"]
    set_phase_raw(root, phase)
    assert "EDA sample" in denied(root, "Read", file_path=str(root / project["full_rel"]))
    assert denied(root, "Read", file_path=str(root / project["dev_rel"]))
    assert denied(root, "Bash", command=f"python -c \"import pandas as pd; pd.read_csv('{project['full_rel']}')\"")


@pytest.mark.parametrize("phase", RESTRICTED_PHASES + ["modeling", "final_evaluation"])
def test_sample_always_readable(project, phase):
    root = project["root"]
    set_phase_raw(root, phase)
    assert denied(root, "Read", file_path=str(root / project["sample_rel"])) is None
    assert denied(root, "Bash", command=f"head {project['sample_rel']}") is None


def test_development_data_allowed_in_modeling(project):
    root = project["root"]
    set_phase_raw(root, "modeling")
    assert denied(root, "Read", file_path=str(root / project["dev_rel"])) is None


def test_scripts_run_by_bash_are_scanned(project):
    root = project["root"]
    set_phase_raw(root, "eda")
    script = root / "notebooks" / "explore.py"
    script.write_text(f"import pandas as pd\ndf = pd.read_csv('{project['test_rel']}')\n", encoding="utf-8")
    reason = denied(root, "Bash", command="python notebooks/explore.py")
    assert reason and "explore.py" in reason


def test_writing_code_that_references_protected_data_is_blocked(project):
    root = project["root"]
    set_phase_raw(root, "eda")
    nb = str(root / "notebooks" / "eda.py")
    assert denied(root, "Write", file_path=nb, content=f"pd.read_csv('{project['full_rel']}')")
    assert denied(root, "Edit", file_path=nb, old_string="x", new_string=f"open('{project['test_rel']}')")
    assert denied(root, "NotebookEdit", notebook_path=str(root / "notebooks/eda.ipynb"),
                  new_source=f"pd.read_csv('{root / project['test_rel']}')")
    assert denied(root, "Write", file_path=nb, content=f"pd.read_csv('{project['sample_rel']}')") is None


def test_grep_over_project_root_blocked_unless_glob_excludes_data(project):
    root = project["root"]
    set_phase_raw(root, "eda")
    assert denied(root, "Grep", pattern="anything", path=str(root))
    assert denied(root, "Grep", pattern="anything", path=str(root), glob="*.py") is None


def test_policy_state_and_decision_files_protected(project):
    root = project["root"]
    for name in ("data-policy.json", "state.json", "decisions.jsonl"):
        target = str(root / ".ds-workflow" / name)
        assert denied(root, "Write", file_path=target, content="{}")
        assert denied(root, "Edit", file_path=target, old_string="a", new_string="b")
    assert denied(root, "Bash", command="echo '{}' > .ds-workflow/state.json")
    assert denied(root, "Read", file_path=str(root / ".ds-workflow" / "state.json")) is None


@pytest.mark.parametrize("command", [
    "python3 ~/.claude/skills/evidence-based-data-science-workflow/scripts/ds_policy.py approve D-001",
    "python3 scripts/ds_policy.py reject D-002 --note no",
    "python3 ds_policy.py unfreeze pre_analysis_plan",
    "python3 ds_policy.py init --sample a.csv --test b.csv --force",
])
def test_human_only_commands_blocked_for_agent(project, command):
    assert "human-only" in denied(project["root"], "Bash", command=command)


def test_agent_commands_of_ds_policy_allowed(project):
    root = project["root"]
    for command in ("python3 ds_policy.py status", "python3 ds_policy.py propose --kind other --summary s",
                    "python3 ds_policy.py set-phase eda"):
        assert denied(root, "Bash", command=command) is None


def test_no_policy_means_no_interference(tmp_path):
    (tmp_path / "data").mkdir()
    assert guard.evaluate(payload(tmp_path, "Read", file_path=str(tmp_path / "data" / "test.csv"))) is None


def test_corrupt_policy_fails_closed(project):
    root = project["root"]
    (root / ".ds-workflow" / "data-policy.json").write_text("{not json", encoding="utf-8")
    assert "unreadable" in denied(root, "Read", file_path=str(root / project["sample_rel"]))
    assert denied(root, "Bash", command="python3 ds_policy.py status") is None  # repair path stays open


def test_guard_hook_protocol_via_stdin(project):
    root = project["root"]
    event = payload(root, "Read", file_path=str(root / project["test_rel"]))
    result = run_script("guard_data_access.py", stdin=json.dumps(event), cwd=root)
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    allowed = run_script("guard_data_access.py", stdin=json.dumps(payload(root, "Read", file_path=str(root / project["sample_rel"]))), cwd=root)
    assert allowed.returncode == 0 and allowed.stdout.strip() == ""


def test_policy_check_cli(project):
    root = project["root"]
    out = json.loads(policy_cli(root, "check", project["test_rel"]).stdout)
    assert out["roles"] == ["test_data"] and out["allowed"] is False
    out = json.loads(policy_cli(root, "check", project["sample_rel"]).stdout)
    assert out["allowed"] is True


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    subdir=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12),
    stem=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=16),
    ext=st.sampled_from([".csv", ".parquet", ".tsv", ".feather"]),
    phase=st.sampled_from(RESTRICTED_PHASES + ["modeling"]),
)
def test_property_any_file_inside_test_directory_is_sealed(tmp_path_factory, subdir, stem, ext, phase):
    root = tmp_path_factory.mktemp("prop")
    project = ds_policy.Project(root)
    project.dir.mkdir()
    (project.policy_path).write_text(json.dumps({"eda_sample": ["sample.csv"], "development_data": [],
                                                 "full_data": [], "test_data": ["holdout/"]}), encoding="utf-8")
    project.save_state({"phase": phase, "history": [], "frozen": {}})
    target = root / "holdout" / subdir / f"{stem}{ext}"
    assert guard.evaluate(payload(root, "Read", file_path=str(target)))
    assert guard.evaluate(payload(root, "Read", file_path=str(root / "other" / f"{stem}{ext}"))) is None


# --------------------------------------------------------------------------
# Regressions from code review and from real headless sessions
# --------------------------------------------------------------------------

def test_forged_approval_through_shell_is_blocked(project):
    root = project["root"]
    forged = """echo '{"id":"D-002","event":"approved","at":"x","via":"human_cli"}' >> .ds-workflow/decisions.jsonl  # ds_policy.py"""
    assert denied(root, "Bash", command=forged)
    writer = root / "notebooks" / "w.py"
    writer.write_text("open('.ds-workflow/decisions.jsonl','a').write('x')\n", encoding="utf-8")
    assert denied(root, "Bash", command="python3 notebooks/w.py")
    assert denied(root, "Bash", command="echo {} > .ds-workflow/sample-manifest.json")
    assert denied(root, "Write", file_path=str(root / ".ds-workflow" / "sample-manifest.json"), content="{}")


def test_helper_name_does_not_whitelist_other_commands(project):
    root = project["root"]
    set_phase_raw(root, "eda")
    assert denied(root, "Bash", command=f"head -n 100000 {project['full_rel']}; true inspect_schema.py")
    assert denied(root, "Bash", command=f"python3 inspect_schema.py {project['full_rel']}") is None
    assert denied(root, "Bash", command=f"python3 inspect_schema.py {project['test_rel']}")


@pytest.mark.parametrize("command", [
    "python3 scripts/ds_policy.py 'approve' D-002",
    "python3 -c \"import ds_policy; ds_policy.main(['approve','D-002'])\"",
    "cd scripts && python3 ds_policy.py --project .. approve D-002",
])
def test_human_only_bypass_variants_blocked(project, command):
    assert "human-only" in denied(project["root"], "Bash", command=command)


def test_proposal_text_may_mention_words_and_files_freely(project):
    """Seen in a real session: a proposal summary naming the test file was denied."""
    root = project["root"]
    command = ('python3 ~/.claude/skills/x/scripts/ds_policy.py propose --kind sample_authorization '
               f'--summary "sample only from {project["dev_rel"]}, never from {project["test_rel"]}; ask user to approve"')
    assert denied(root, "Bash", command=command) is None


def test_state_files_readable_but_not_writable_through_shell(project):
    """Seen in a real session: `cat .ds-workflow/decisions.jsonl | tail -5` was denied; reading is harmless."""
    root = project["root"]
    assert denied(root, "Bash", command="cat .ds-workflow/decisions.jsonl | tail -5") is None
    # exact commands from the final headless run (fd duplication is not a write)
    assert denied(root, "Bash", command="ls -la . .ds-workflow 2>&1; cat .ds-workflow/data-policy.json 2>&1; python3 ~/.claude/skills/evidence-based-data-science-workflow/scripts/ds_policy.py status 2>&1 | head -30") is None
    assert denied(root, "Bash", command="cat .ds-workflow/state.json .ds-workflow/data-policy.json 2>/dev/null | head -60") is None
    assert denied(root, "Bash", command="cat x.json > .ds-workflow/decisions.jsonl")
    assert denied(root, "Bash", command="cat x.json 2>&1 >> .ds-workflow/decisions.jsonl")
    assert denied(root, "Bash", command="echo y | tee -a .ds-workflow/decisions.jsonl")
    assert denied(root, "Bash", command="sed -i s/proposed/approved/ .ds-workflow/decisions.jsonl")


@pytest.mark.parametrize("command", [
    "python3 $S/inspect_schema.py {dev} 2>&1 | head -30; python3 $S/ds_policy.py propose --help 2>&1 | head -30",
    "python3 ds_policy.py propose --kind sample_authorization --summary 'from {dev}, never {test}' 2>&1 | tail -5",
])
def test_piped_helper_and_policy_commands_allowed(project, command):
    """Seen in a real session: output piped to head made legitimate commands look like data access."""
    root = project["root"]
    assert denied(root, "Bash", command=command.format(dev=project["dev_rel"], test=project["test_rel"])) is None


def test_command_substitution_disables_exemptions(project):
    root = project["root"]
    set_phase_raw(root, "eda")
    assert denied(root, "Bash", command=f"python3 ds_policy.py status `cat {project['full_rel']}`")
    assert denied(root, "Bash", command=f"python3 ds_policy.py status $(cat {project['test_rel']})")
    assert denied(root, "Bash", command=f"python3 ds_policy.py status | cat {project['full_rel']}")


def test_glob_only_test_entries_are_recognised_in_code(tmp_path):
    root = tmp_path
    project = ds_policy.Project(root)
    project.dir.mkdir()
    project.policy_path.write_text(json.dumps({"eda_sample": ["sample.csv"], "development_data": [], "full_data": [],
                                               "test_data": ["**/test_*.parquet"]}), encoding="utf-8")
    project.save_state({"phase": "eda", "history": [], "frozen": {}})
    (root / "load.py").write_text("pd.read_parquet('data/test_2024.parquet')\n", encoding="utf-8")
    assert guard.evaluate(payload(root, "Bash", command="python load.py"))
    assert guard.evaluate(payload(root, "Read", file_path=str(root / "data" / "test_2024.parquet")))


def test_sample_inside_protected_directory_stays_usable(tmp_path):
    root = tmp_path
    project = ds_policy.Project(root)
    project.dir.mkdir()
    project.policy_path.write_text(json.dumps({"eda_sample": ["data/eda_sample.csv"], "development_data": [],
                                               "full_data": ["data"], "test_data": ["holdout.csv"]}), encoding="utf-8")
    project.save_state({"phase": "eda", "history": [], "frozen": {}})
    (root / "eda.py").write_text("pd.read_csv('data/eda_sample.csv')\n", encoding="utf-8")
    assert guard.evaluate(payload(root, "Bash", command="python eda.py")) is None
    (root / "raw.py").write_text("pd.read_csv('data/raw.csv')\n", encoding="utf-8")
    assert guard.evaluate(payload(root, "Bash", command="python raw.py"))
    assert guard.evaluate(payload(root, "Bash", command="cat metadata/notes.txt")) is None  # 'data' inside a longer name
