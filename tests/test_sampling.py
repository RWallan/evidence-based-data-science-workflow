"""EDA sample creation: statistical properties, reproducibility and authorisation."""

import csv
import json
import random
from collections import Counter

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import make_eda_sample as mes
import synthetic
from conftest import human_approve, propose, run_script

SETTINGS = settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def generated_csv(tmp_path_factory, n_rows, n_groups, seed):
    path = tmp_path_factory.mktemp("csv") / "data.csv"
    rng = random.Random(seed)
    strata = [f"s{i}" for i in range(3)]
    synthetic.write_csv(path, synthetic.generate_rows(rng, n_rows, n_groups, strata))
    return path


def source_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return [tuple(r) for r in list(csv.reader(handle))[1:]]


@SETTINGS
@given(n_rows=st.integers(1, 300), n=st.integers(1, 350), seed=st.integers(0, 2**31))
def test_simple_random_exact_size_subset_and_reproducible(tmp_path_factory, n_rows, n, seed):
    path = generated_csv(tmp_path_factory, n_rows, 10, seed)
    rows, total, _ = mes.simple_random(path, n, random.Random(seed))
    assert total == n_rows
    assert len(rows) == min(n, n_rows)
    population = Counter(source_rows(path))
    assert not Counter(map(tuple, rows)) - population  # sample is a sub-multiset of the source
    again, _, _ = mes.simple_random(path, n, random.Random(seed))
    assert again == rows


@SETTINGS
@given(n_rows=st.integers(30, 400), frac=st.floats(0.05, 0.9), seed=st.integers(0, 2**31))
def test_stratified_allocation_is_proportional(tmp_path_factory, n_rows, frac, seed):
    path = generated_csv(tmp_path_factory, n_rows, 10, seed)
    n = max(1, int(frac * n_rows))
    rows, total, details = mes.stratified(path, n, random.Random(seed), "stratum")
    assert len(rows) == n
    for stratum, count in details["strata_population"].items():
        expected = n * count / total
        assert abs(details["strata_sample"][stratum] - expected) <= 1


@SETTINGS
@given(n_rows=st.integers(20, 300), n_groups=st.integers(2, 30), k=st.integers(1, 30), seed=st.integers(0, 2**31))
def test_group_sampling_keeps_whole_groups(tmp_path_factory, n_rows, n_groups, k, seed):
    path = generated_csv(tmp_path_factory, n_rows, n_groups, seed)
    rows, _total, details = mes.grouped(path, k, random.Random(seed), "group_id")
    chosen = {r[1] for r in rows}
    full = Counter(r[1] for r in source_rows(path))
    assert len(chosen) == details["groups_sampled"] == min(k, details["groups_population"])
    for group in chosen:
        assert sum(1 for r in rows if r[1] == group) == full[group]


def test_cli_refuses_without_user_authorisation(project):
    root = project["root"]
    result = run_script("make_eda_sample.py", "--source", project["dev_rel"], "--out", project["sample_rel"],
                        "--n", 50, "--seed", 1, cwd=root)
    assert result.returncode == 1 and "sample_authorization" in result.stderr
    assert not (root / project["sample_rel"]).exists()


def test_cli_refuses_test_data_as_source(project):
    root = project["root"]
    human_approve(root, propose(root, "sample_authorization"))
    result = run_script("make_eda_sample.py", "--source", project["test_rel"], "--out", project["sample_rel"],
                        "--n", 50, "--seed", 1, cwd=root)
    assert result.returncode == 1 and "TEST data" in result.stderr


def test_cli_writes_documented_sample_after_approval(project):
    root = project["root"]
    decision = propose(root, "sample_authorization", "simple random sample of 60 rows, seed 99")
    human_approve(root, decision)
    args = ["--source", project["dev_rel"], "--out", project["sample_rel"], "--n", 60, "--seed", 99,
            "--decision", decision, "--limitations", "development period only"]
    result = run_script("make_eda_sample.py", *args, cwd=root)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((root / ".ds-workflow" / "sample-manifest.json").read_text(encoding="utf-8"))
    for field in ("source", "source_sha256", "source_rows", "sample_rows", "method", "unit", "seed",
                  "authorization_decision", "limitations"):
        assert manifest[field] not in (None, "", [])
    assert manifest["sample_rows"] == 60 and manifest["source_rows"] == project["n_dev"]
    assert any("not automatically representative" in item for item in manifest["limitations"])
    again = run_script("make_eda_sample.py", *args, cwd=root)
    assert again.returncode == 1 and "never overwrite" in again.stderr


def test_cli_refuses_output_not_declared_as_sample(project):
    root = project["root"]
    human_approve(root, propose(root, "sample_authorization"))
    result = run_script("make_eda_sample.py", "--source", project["dev_rel"], "--out", "data/other.csv",
                        "--n", 10, "--seed", 1, cwd=root)
    assert result.returncode == 1 and "not declared as eda_sample" in result.stderr


@SETTINGS
@given(n_rows=st.integers(2, 400), n=st.integers(1, 400), seed=st.integers(0, 2**31))
def test_systematic_sample_spans_the_whole_file(tmp_path_factory, n_rows, n, seed):
    path = generated_csv(tmp_path_factory, n_rows, 5, seed)
    rows, total, details = mes.systematic(path, n, random.Random(seed))
    assert len(rows) == min(n, n_rows)
    order = {r: i for i, r in enumerate(source_rows(path))}
    last = max(order[tuple(r)] for r in rows)
    assert last >= total - details["step"] - 1  # reaches the end of the file
