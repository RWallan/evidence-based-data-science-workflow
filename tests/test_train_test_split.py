"""The project's single train/test split: authorisation, once-only, registration, structure, leakage."""

import csv
import json
from collections import Counter

import pytest

import ds_policy
import guard_data_access as guard
import synthetic
from conftest import human_approve, policy_cli, propose, run_script

TRAIN, TEST = "data/processed/train.csv", "data/processed/test.csv"


@pytest.fixture
def raw_project(tmp_path, isolated_env):
    root = tmp_path / "proj"
    files = synthetic.build_project_data(root, seed=17, n_dev=500, n_test=1)
    for rel in (files["dev_rel"], files["test_rel"]):  # only the raw dataset exists in this scenario
        (root / rel).unlink()
    result = run_script("ds_policy.py", "init", "--full", files["full_rel"], "--split-pending", cwd=root)
    assert result.returncode == 0, result.stderr
    return {"root": root, **files}


def split(root, *extra, decision=None):
    args = ["--train-out", TRAIN, "--test-out", TEST, "--test-size", "0.25", "--seed", "42", *extra]
    if decision:
        args += ["--decision", decision]
    return run_script("make_train_test_split.py", *args, cwd=root)


def rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def authorised_split(root, *extra):
    decision = propose(root, "split_authorization", "random 75/25 split, seed 42")
    human_approve(root, decision)
    result = split(root, *extra, decision=decision)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def payload(root, tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}


def set_phase_raw(root, phase):
    project = ds_policy.Project(root)
    state = project.state()
    state["phase"] = phase
    project.save_state(state)


def test_eda_waits_for_the_split(raw_project):
    refused = policy_cli(raw_project["root"], "set-phase", "eda")
    assert refused.returncode == 1 and "split has not been made" in refused.stderr


def test_split_requires_user_authorisation(raw_project):
    root = raw_project["root"]
    result = split(root)
    assert result.returncode == 1 and "split_authorization" in result.stderr
    propose(root, "split_authorization")
    assert split(root).returncode == 1  # proposed is not approved
    assert not (root / TRAIN).exists() and not (root / TEST).exists()


def test_split_is_disjoint_complete_and_registered(raw_project):
    root = raw_project["root"]
    out = authorised_split(root)
    train, test = rows(root / TRAIN), rows(root / TEST)
    source = rows(root / raw_project["full_rel"])
    as_rows = lambda rs: Counter(tuple(r.values()) for r in rs)  # noqa: E731
    assert as_rows(train) + as_rows(test) == as_rows(source)  # an exact partition of the source rows
    assert len(test) == pytest.approx(0.25 * len(source), abs=1)
    assert out["n_train"] == len(train) and out["n_test"] == len(test) and "test_sha256" not in out
    policy = ds_policy.Project(root).policy()
    assert policy["development_data"] == [TRAIN] and policy["eda_sample"] == [TRAIN]
    assert policy["test_data"] == [TEST] and policy["split_status"] == "done"
    assert ds_policy.Project(root).state()["split"]["decision"] == out["authorization_decision"]
    assert out["library_versions"]["scikit-learn"]


def test_eda_starts_on_the_training_split(raw_project):
    root = raw_project["root"]
    authorised_split(root)
    assert policy_cli(root, "set-phase", "eda").returncode == 0
    assert guard.evaluate(payload(root, "Read", file_path=str(root / TRAIN))) is None
    assert guard.evaluate(payload(root, "Read", file_path=str(root / TEST)))
    assert guard.evaluate(payload(root, "Read", file_path=str(root / raw_project["full_rel"])))


def test_tampered_training_split_blocks_eda(raw_project):
    root = raw_project["root"]
    authorised_split(root)
    with (root / TRAIN).open("a", encoding="utf-8") as handle:
        handle.write("appended,row\n")
    refused = policy_cli(root, "set-phase", "eda")
    assert refused.returncode == 1 and "changed or missing" in refused.stderr


def test_second_split_refused_even_with_a_new_split_authorisation(raw_project):
    root = raw_project["root"]
    authorised_split(root)
    before = (root / TEST).read_bytes()
    again = propose(root, "split_authorization")
    human_approve(root, again)
    result = split(root, decision=None)
    assert result.returncode == 1 and "already made" in result.stderr and "resplit" in result.stderr
    assert (root / TEST).read_bytes() == before


def test_resplit_only_with_explicit_user_approval(raw_project):
    root = raw_project["root"]
    authorised_split(root)
    set_phase_raw(root, "modeling")
    resplit = propose(root, "resplit", "user asked for a 70/30 split")
    assert split(root).returncode == 1  # proposed, not approved
    human_approve(root, resplit)
    result = run_script("make_train_test_split.py", "--train-out", TRAIN, "--test-out", TEST, "--test-size", "0.3",
                        "--seed", "7", "--decision", resplit, cwd=root)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["authorization_kind"] == "resplit" and "not independent" in out["warnings"][0]
    state = ds_policy.Project(root).state()
    assert len(state["split_history"]) == 1
    assert split(root).returncode == 1  # the resplit approval is consumed: no third split


def test_stratified_split_keeps_class_proportions(raw_project):
    root = raw_project["root"]
    authorised_split(root, "--method", "stratified", "--stratify", "stratum")
    full = Counter(r["stratum"] for r in rows(root / raw_project["full_rel"]))
    test = Counter(r["stratum"] for r in rows(root / TEST))
    n_test = sum(test.values())
    for stratum, count in full.items():
        assert abs(test[stratum] - n_test * count / sum(full.values())) <= 1.5


def test_group_split_never_shares_a_unit(raw_project):
    root = raw_project["root"]
    out = authorised_split(root, "--method", "group", "--group", "group_id")
    assert not {r["group_id"] for r in rows(root / TRAIN)} & {r["group_id"] for r in rows(root / TEST)}
    assert out["details"]["groups_train"] > 0 and out["details"]["groups_test"] > 0


def test_temporal_split_puts_the_future_in_test(raw_project):
    root = raw_project["root"]
    authorised_split(root, "--method", "temporal", "--time-col", "day")
    assert max(int(r["day"]) for r in rows(root / TRAIN)) < min(int(r["day"]) for r in rows(root / TEST))


def test_split_refuses_test_or_undeclared_sources(raw_project):
    root = raw_project["root"]
    human_approve(root, propose(root, "split_authorization"))
    other = root / "data" / "other.csv"
    other.write_text((root / raw_project["full_rel"]).read_text(encoding="utf-8"), encoding="utf-8")
    result = run_script("make_train_test_split.py", "--source", other, "--train-out", TRAIN, "--test-out", TEST,
                        "--seed", "1", cwd=root)
    assert result.returncode == 1 and "not declared as full_data" in result.stderr


def test_no_split_needs_explicit_user_approval(tmp_path, isolated_env):
    root = tmp_path / "proj"
    files = synthetic.build_project_data(root, seed=5)
    full = files["full_rel"]
    assert run_script("ds_policy.py", "init", "--full", full, "--development", full, "--sample", full,
                      "--test", "NONE", "--no-test-reason", "user evaluates on future data", cwd=root).returncode == 0
    refused = policy_cli(root, "set-phase", "eda")
    assert refused.returncode == 1 and "no_test_split" in refused.stderr


# --------------------------------------------------------------------------
# Guard: the split helper is allowed; re-splitting the full data is not
# --------------------------------------------------------------------------

def test_guard_allows_the_split_helper_in_setup(raw_project):
    root = raw_project["root"]
    command = f"python3 ~/.claude/skills/x/scripts/make_train_test_split.py --source {raw_project['full_rel']} --train-out {TRAIN} --test-out {TEST} --seed 42 2>&1 | tail -20"
    assert guard.evaluate(payload(root, "Bash", command=command)) is None


@pytest.mark.parametrize("phase", ["setup", "eda", "analysis_planning", "modeling"])
def test_agent_code_cannot_resplit_the_full_data(raw_project, phase):
    root = raw_project["root"]
    authorised_split(root)
    set_phase_raw(root, phase)
    script = root / "notebooks" / "resplit.py"
    script.write_text("import pandas as pd\nfrom sklearn.model_selection import train_test_split\n"
                      f"df = pd.read_csv('{raw_project['full_rel']}')\ntrain, test = train_test_split(df, test_size=0.2)\n",
                      encoding="utf-8")
    assert guard.evaluate(payload(root, "Bash", command="python notebooks/resplit.py"))


@pytest.mark.parametrize("phase", ["eda", "modeling"])
def test_internal_validation_split_of_training_data_allowed(raw_project, phase):
    root = raw_project["root"]
    authorised_split(root)
    set_phase_raw(root, phase)
    script = root / "notebooks" / "validate.py"
    script.write_text("import pandas as pd\nfrom sklearn.model_selection import train_test_split\n"
                      f"df = pd.read_csv('{TRAIN}')\nfit, val = train_test_split(df, test_size=0.2, random_state=0)\n",
                      encoding="utf-8")
    assert guard.evaluate(payload(root, "Bash", command="python notebooks/validate.py")) is None


def test_full_dataset_opens_only_in_final_evaluation(raw_project):
    root = raw_project["root"]
    authorised_split(root)
    set_phase_raw(root, "final_evaluation")
    assert guard.evaluate(payload(root, "Read", file_path=str(root / raw_project["full_rel"]))) is None
    assert guard.evaluate(payload(root, "Read", file_path=str(root / TEST))) is None


def test_split_manifest_is_protected(raw_project):
    root = raw_project["root"]
    assert guard.evaluate(payload(root, "Write", file_path=str(root / ".ds-workflow" / "split-manifest.json"), content="{}"))
    assert guard.evaluate(payload(root, "Bash", command="echo {} > .ds-workflow/split-manifest.json"))
