"""Shared fixtures. Synthetic data are always generated (seeded generators,
Hypothesis strategies, PyMuPDF-built PDFs), never hand-typed records."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "real_library: integration tests against the user's configured book library")


def run_script(name: str, *args, cwd=None, env=None, stdin: str | None = None, timeout=300):
    """Run a skill script as a subprocess with this interpreter."""
    full_env = {**os.environ, **(env or {})}
    full_env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)], cwd=cwd, env=full_env,
                          input=stdin, capture_output=True, text=True, timeout=timeout)


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """No accidental use of the user's real config or project env vars."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    monkeypatch.delenv("EBDSW_CONFIG", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


@pytest.fixture
def synthetic_library(tmp_path, isolated_env):
    """A generated PDF library + config + built index. Returns a dict of facts."""
    library = tmp_path / "library"
    facts = synthetic.build_pdf_library(library, seed=7)
    data_dir = tmp_path / "rag_data"
    config_path = tmp_path / "config" / "config.json"
    env = {"EBDSW_CONFIG": str(config_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg_config")}
    setup = run_script("setup_rag.py", "--library-root", library, "--data-dir", data_dir,
                       "--python", sys.executable, env=env, cwd=library)
    assert setup.returncode == 0, setup.stderr
    index = run_script("index_local_books.py", env=env, cwd=tmp_path)
    assert index.returncode == 0, index.stdout + index.stderr
    return {"library": library, "data_dir": data_dir, "config_path": config_path, "env": env, **facts}


@pytest.fixture
def project(tmp_path, isolated_env):
    """A generated analysis project with sample/dev/full/test CSVs and a data policy."""
    root = tmp_path / "proj"
    files = synthetic.build_project_data(root, seed=11)
    result = run_script("ds_policy.py", "init", "--sample", files["sample_rel"], "--development", files["dev_rel"],
                        "--full", files["full_rel"], "--test", files["test_rel"], cwd=root)
    assert result.returncode == 0, result.stderr
    return {"root": root, **files}


def policy_cli(project_root, *args):
    return run_script("ds_policy.py", *args, cwd=project_root)


def human_approve(project_root, decision_id: str, note="approved in test"):
    """Simulates the USER running the human-only command (outside the agent)."""
    result = policy_cli(project_root, "approve", decision_id, "--note", note)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def propose(project_root, kind: str, summary="generated proposal"):
    result = policy_cli(project_root, "propose", "--kind", kind, "--summary", summary)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["id"]
