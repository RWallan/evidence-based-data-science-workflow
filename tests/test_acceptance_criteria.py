"""Acceptance criteria (spec section 19) mapped to automated checks.

Criteria covered elsewhere are listed in ACCEPTANCE_MAP and checked to exist,
so a deleted test shows up as a failure here. The `real_library` tests run
against the user's configured library (skipped when it is not configured).
"""

import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SCRIPTS, SKILL_DIR, run_script

ACCEPTANCE_MAP = {
    "EDA precedes modelling": ["test_workflow_gates::test_modelling_waits_for_user_direction_and_frozen_plan",
                               "test_workflow_gates::test_cannot_skip_phases"],
    "EDA reviewed before presentation": ["test_workflow_gates::test_eda_review_requires_clean_notebook_audit",
                                         "test_workflow_gates::test_presentation_requires_completed_review_checklist"],
    "modelling depends on user direction": ["test_workflow_gates::test_modelling_waits_for_user_direction_and_frozen_plan"],
    "no conclusions without consultation": ["test_workflow_gates::test_unapproved_conclusions_are_rejected",
                                            "test_workflow_gates::test_approved_tag_must_match_a_human_approval",
                                            "test_data_access_policy::test_human_only_commands_blocked_for_agent"],
    "EDA uses only the authorised sample": ["test_data_access_policy::test_full_and_development_data_blocked_while_sample_only",
                                            "test_workflow_gates::test_eda_blocked_without_authorised_sample"],
    "test set isolated": ["test_data_access_policy::test_test_data_is_sealed_before_final_evaluation",
                          "test_sampling::test_cli_refuses_test_data_as_source"],
    "sample provenance documented": ["test_sampling::test_cli_writes_documented_sample_after_approval",
                                     "test_workflow_gates::test_user_supplied_sample_needs_approved_provenance"],
    "bibliography organised and verifiable": ["test_skill_structure::test_bibliography_has_required_metadata_and_honest_verification",
                                              "test_skill_structure::test_validate_skill_passes"],
    "RAG inventory from the authorised root, originals intact": ["test_rag_persistence::test_indexing_never_modifies_originals"],
    "durable config, retrieval from another cwd": ["test_rag_persistence::test_search_works_from_unrelated_directories",
                                                   "test_rag_persistence::test_config_found_via_xdg_default_without_env_var"],
    "citations keep provenance": ["test_reference_retrieval::test_needle_found_with_page_label_and_outline_section",
                                  "test_reference_retrieval::test_verify_citation_verified_wrong_page_and_fabricated"],
    "ingestion, retrieval and update tests": ["test_rag_persistence::test_stale_index_detected_and_incrementally_updated"],
    "absence of evidence reported": ["test_reference_retrieval::test_no_evidence_is_reported_not_invented"],
    "no false test claims": ["test_workflow_gates::test_test_claims_need_passing_evidence"],
    "unavailable library/index reported": ["test_rag_persistence::test_unavailable_library_is_reported",
                                           "test_rag_persistence::test_missing_index_reported_without_pretending"],
}


@pytest.mark.parametrize("criterion", sorted(ACCEPTANCE_MAP))
def test_acceptance_criterion_has_tests(criterion):
    for ref in ACCEPTANCE_MAP[criterion]:
        module_name, test_name = ref.split("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, test_name, None)), f"{criterion}: missing {ref}"


def test_gitignores_exclude_protected_artifacts():
    project_ignore = (SKILL_DIR / "templates" / "project-gitignore").read_text(encoding="utf-8")
    for pattern in ("data/", "extracted_text/", "*.sqlite", "*.csv", "models/"):
        assert pattern in project_ignore
    skill_ignore = (SKILL_DIR / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.pdf", "*.sqlite", "extracted_text/", "__pycache__/"):
        assert pattern in skill_ignore


# --------------------------------------------------------------------------
# Integration with the user's real library (read-only)
# --------------------------------------------------------------------------

def real_config():
    sys.path.insert(0, str(SCRIPTS))
    import ebdsw_common as common
    env_backup = {k: os.environ.pop(k, None) for k in ("XDG_CONFIG_HOME", "EBDSW_CONFIG")}
    try:
        return common.load_config()
    except common.ConfigError:
        return None
    finally:
        for key, value in env_backup.items():
            if value is not None:
                os.environ[key] = value


REAL = real_config()
real_library = pytest.mark.skipif(REAL is None, reason="no real RAG config on this machine")


def real_search(*args, cwd):
    env = {k: v for k, v in os.environ.items() if k not in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "EBDSW_CONFIG")}
    return subprocess.run([sys.executable, str(SCRIPTS / "search_references.py"), *args, "--json"],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.real_library
@real_library
@pytest.mark.parametrize("key, query", [
    ("kutner2005", "residuals heteroscedasticity"),
    ("johnson_wichern2007", "principal components"),
    ("bussab_morettin_estatistica_basica", "intervalo de confiança"),
    ("lohr_sampling", "stratified sampling"),
])
def test_real_library_retrieval_with_provenance_from_other_cwd(tmp_path, key, query):
    result = real_search(query, "--doc", key, "--k", "2", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    top = json.loads(result.stdout)["results"][0]
    assert top["source"]["key"] == key
    assert top["source"]["metadata_source"].startswith("verified catalog")
    assert top["locator"]["pdf_page"] >= 1 and top["locator"]["printed_page"]


@pytest.mark.real_library
@real_library
def test_real_library_books_unchanged_since_cataloguing():
    catalog = json.loads((Path(REAL["data_dir"]) / "metadata" / "catalog.json").read_text(encoding="utf-8"))
    for entry in catalog["entries"]:
        path = Path(REAL["library_root"]) / entry["match"]["relpath"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["match"]["sha256"], path


@pytest.mark.real_library
@real_library
def test_real_library_health_from_other_cwd(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "EBDSW_CONFIG")}
    result = subprocess.run([sys.executable, str(SCRIPTS / "validate_rag.py"), "--json"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=300)
    report = json.loads(result.stdout)
    assert result.returncode == 0, report["warnings"]
    assert all(check["ok"] for check in report["checks"])
