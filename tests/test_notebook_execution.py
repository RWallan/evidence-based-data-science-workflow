"""The EDA notebook template really executes in a fresh Jupyter kernel on the training split, passes the
audit and the guard, and execution failures or test-data reads are caught."""

import json
import subprocess
import sys

import pytest

import guard_data_access as guard
import synthetic
from conftest import SKILL_DIR, human_approve, policy_cli, propose, run_script

pytest.importorskip("nbclient")
pytest.importorskip("matplotlib")

TRAIN, TEST = "data/processed/train.csv", "data/processed/test.csv"
NOTEBOOK = "notebooks/eda.ipynb"
EXECUTE = f"{sys.executable} -m jupyter nbconvert --to notebook --execute --inplace {NOTEBOOK}"


@pytest.fixture
def split_project(tmp_path, isolated_env):
    root = tmp_path / "proj"
    files = synthetic.build_project_data(root, seed=23, n_dev=300, n_test=1)
    for rel in (files["dev_rel"], files["test_rel"]):
        (root / rel).unlink()
    assert run_script("ds_policy.py", "init", "--full", files["full_rel"], "--split-pending", cwd=root).returncode == 0
    decision = propose(root, "split_authorization", "random 80/20 split, seed 5")
    human_approve(root, decision)
    made = run_script("make_train_test_split.py", "--train-out", TRAIN, "--test-out", TEST, "--test-size", "0.2",
                      "--seed", "5", "--decision", decision, cwd=root)
    assert made.returncode == 0, made.stderr
    assert policy_cli(root, "set-phase", "eda").returncode == 0
    return root


def notebook_from_template(root, data_path, extra_cells=()):
    nb = json.loads((SKILL_DIR / "templates" / "eda_notebook_template.ipynb").read_text(encoding="utf-8"))
    text = json.dumps(nb).replace("data/processed/train.csv", data_path)
    nb = json.loads(text)
    nb["cells"][-1:-1] = [synthetic.build_notebook([c])["cells"][0] for c in extra_cells]
    path = root / NOTEBOOK
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    return path


def guard_payload(root, tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}


def execute(root, *extra):
    return subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute",
                           "--inplace", *extra, NOTEBOOK], cwd=root, capture_output=True, text=True, timeout=300)


def test_template_executes_on_the_training_split_and_passes_the_audit(split_project):
    root = split_project
    path = notebook_from_template(root, TRAIN)
    assert guard.evaluate(guard_payload(root, "Write", file_path=str(path), content=path.read_text())) is None
    assert guard.evaluate(guard_payload(root, "Bash", command=EXECUTE)) is None

    result = execute(root)
    assert result.returncode == 0, result.stderr

    cells = json.loads(path.read_text(encoding="utf-8"))["cells"]
    code = [c for c in cells if c["cell_type"] == "code"]
    assert [c["execution_count"] for c in code] == list(range(1, len(code) + 1))
    outputs = [o for c in code for o in c["outputs"]]
    assert not [o for o in outputs if o["output_type"] == "error"]
    n_train = sum(1 for _ in (root / TRAIN).open(encoding="utf-8")) - 1
    shape = "".join(code[0]["outputs"][0]["data"]["text/plain"])
    assert shape.startswith(f"({n_train},")
    assert any("image/png" in o.get("data", {}) for o in outputs)  # the labelled figure rendered

    review = policy_cli(root, "set-phase", "eda_review", "--notebook", NOTEBOOK)
    assert review.returncode == 0, review.stderr + review.stdout


def test_notebook_reading_the_test_split_is_blocked_before_execution(split_project):
    root = split_project
    path = notebook_from_template(root, TEST)
    assert guard.evaluate(guard_payload(root, "Write", file_path=str(path), content=path.read_text()))
    assert guard.evaluate(guard_payload(root, "Bash", command=EXECUTE))


def test_failing_cell_is_caught_by_execution_and_by_the_audit(split_project):
    root = split_project
    notebook_from_template(root, TRAIN, extra_cells=[("code", "raw['column_that_does_not_exist']", None, [])])
    assert execute(root).returncode != 0  # strict execution stops on the error

    assert execute(root, "--allow-errors").returncode == 0
    review = policy_cli(root, "set-phase", "eda_review", "--notebook", NOTEBOOK)
    assert review.returncode == 1
    assert "error" in (review.stdout + review.stderr).lower()
