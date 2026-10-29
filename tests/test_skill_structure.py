"""Structural checks of the skill package (static, deterministic)."""

import json
import os
import re

import pytest
import yaml

from conftest import SCRIPTS, SKILL_DIR, run_script

import validate_skill


def skill_text():
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def test_validate_skill_passes():
    report = validate_skill.validate()
    assert report["passed"], report["errors"]


def test_frontmatter_is_valid_yaml_with_guard_hook():
    raw, _body = validate_skill.frontmatter(skill_text())
    meta = yaml.safe_load(raw)
    assert meta["name"] == "evidence-based-data-science-workflow"
    assert 50 < len(meta["description"]) <= 1536
    groups = meta["hooks"]["PreToolUse"]
    matcher = groups[0]["matcher"]
    for tool in ("Read", "Write", "Edit", "NotebookEdit", "Grep", "Bash"):
        assert tool in matcher.split("|")
    command = groups[0]["hooks"][0]["command"]
    assert "guard_data_access.py" in command
    resolved = os.path.expandvars(re.search(r'"([^"]+)"', command).group(1))
    assert os.path.isfile(resolved)


@pytest.mark.parametrize("phrase", [
    "One split, sealed test set", "EDA on the authorised data only", "User gate", "once per project", "the user's explicit instructions override it", "initial prompt", "ask the user for permission to install", "Evidence trail", "No fabrication",
    "Silence is not approval", "Do not begin modelling", "ends with a question to the user",
    "NO EVIDENCE FOUND", "human only",
])
def test_skill_states_critical_rules(phrase):
    assert phrase.lower() in skill_text().lower()


def test_workflow_steps_have_completion_criteria():
    text = skill_text()
    steps = re.findall(r"^### (\d)\. ", text, re.M)
    assert steps == [str(i) for i in range(1, 9)]
    sections = re.split(r"^### \d\. ", text, flags=re.M)[1:]
    for section in sections:
        assert "**Done when**" in section


def test_every_script_has_working_help():
    for script in sorted(SCRIPTS.glob("*.py")):
        if script.name in ("ebdsw_common.py", "guard_data_access.py"):
            continue
        result = run_script(script.name, "--help")
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_bibliography_has_required_metadata_and_honest_verification():
    bib = json.loads((SKILL_DIR / "references" / "bibliography.json").read_text(encoding="utf-8"))
    for entry in bib["entries"]:
        status = entry["verification"]["status"]
        assert status in bib["verification_legend"] or status == "partial"
        if status == "crossref":
            assert entry["doi"].startswith("10.")
    keys = {e["key"] for e in bib["entries"]}
    for required in ("bussab_morettin_estatistica_basica", "kutner2005", "conover1999", "lohr_sampling",
                     "johnson_wichern2007", "hernan_robins2020", "hyndman_fpp3", "wasserstein_lazar2016"):
        assert required in keys


def test_skill_dir_contains_no_book_content():
    forbidden_suffixes = {".pdf", ".epub", ".sqlite", ".djvu"}
    for path in SKILL_DIR.rglob("*"):
        assert path.suffix.lower() not in forbidden_suffixes, path
        assert "extracted_text" not in path.parts


def test_scripts_do_not_hardcode_working_directory_paths():
    for script in SCRIPTS.glob("*.py"):
        text = script.read_text(encoding="utf-8")
        assert not re.search(r"/(home|Users)/[^/\s\"']+/", text), script.name
