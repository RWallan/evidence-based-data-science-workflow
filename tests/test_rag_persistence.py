"""Durable configuration, cwd independence, incremental updates and failure reporting."""

import hashlib
import json
import os
import shutil

import pytest

import synthetic
from conftest import run_script


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_search_works_from_unrelated_directories(synthetic_library, tmp_path):
    needle = synthetic_library["needles"]["labelled"]
    for other in (tmp_path / "some_project", tmp_path / "another" / "deep" / "dir"):
        other.mkdir(parents=True)
        result = run_script("search_references.py", needle["phrase"], "--json", env=synthetic_library["env"], cwd=other)
        assert result.returncode == 0
        assert json.loads(result.stdout)["results"][0]["locator"]["pdf_page"] == needle["pdf_page"]


def test_config_found_via_xdg_default_without_env_var(tmp_path, isolated_env):
    library = tmp_path / "books"
    synthetic.build_pdf_library(library, seed=5)
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg_config"), "XDG_DATA_HOME": str(tmp_path / "xdg_data")}
    assert run_script("setup_rag.py", "--library-root", library, env=env, cwd=library).returncode == 0
    config = json.loads((tmp_path / "xdg_config" / "evidence-based-data-science-workflow" / "config.json").read_text())
    assert os.path.isabs(config["library_root"]) and os.path.isabs(config["data_dir"])
    assert config["data_dir"].startswith(str(tmp_path / "xdg_data"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = run_script("discover_books.py", "--json", env=env, cwd=elsewhere)
    assert result.returncode == 0 and len(json.loads(result.stdout)["documents"]) == 3


def test_setup_rejects_data_dir_inside_library(tmp_path, isolated_env):
    library = tmp_path / "books"
    library.mkdir()
    result = run_script("setup_rag.py", "--library-root", library, "--data-dir", library / "rag",
                        "--config", tmp_path / "c.json")
    assert result.returncode == 2 and "must not live inside" in result.stderr


def test_relative_paths_in_config_are_rejected(tmp_path, isolated_env):
    config = tmp_path / "c.json"
    config.write_text(json.dumps({"library_root": "books", "data_dir": "rag"}), encoding="utf-8")
    result = run_script("search_references.py", "anything", env={"EBDSW_CONFIG": str(config)}, cwd=tmp_path)
    assert result.returncode == 2 and "absolute path" in result.stderr


def test_missing_config_reported(tmp_path, isolated_env):
    result = run_script("search_references.py", "anything", env={"EBDSW_CONFIG": str(tmp_path / "none.json")}, cwd=tmp_path)
    assert result.returncode == 2 and "not found" in result.stderr


def test_missing_index_reported_without_pretending(synthetic_library):
    (synthetic_library["data_dir"] / "index" / "references.sqlite").unlink()
    result = run_script("search_references.py", "anything", env=synthetic_library["env"], cwd=synthetic_library["library"].parent)
    assert result.returncode == 3 and "No retrieval was performed" in result.stderr


def test_unavailable_library_is_reported(synthetic_library, tmp_path):
    library = synthetic_library["library"]
    moved = tmp_path / "library_moved"
    library.rename(moved)
    try:
        needle = synthetic_library["needles"]["labelled"]
        result = run_script("search_references.py", needle["phrase"], env=synthetic_library["env"], cwd=tmp_path)
        assert "not accessible" in result.stdout and "only from the index" in result.stdout
        strict = run_script("search_references.py", needle["phrase"], "--strict", env=synthetic_library["env"], cwd=tmp_path)
        assert strict.returncode == 4
        health = run_script("validate_rag.py", env=synthetic_library["env"], cwd=tmp_path)
        assert health.returncode == 4 and "library unavailable" in health.stdout
        index = run_script("index_local_books.py", env=synthetic_library["env"], cwd=tmp_path)
        assert index.returncode == 4
    finally:
        moved.rename(library)


def test_stale_index_detected_and_incrementally_updated(synthetic_library):
    library, env = synthetic_library["library"], synthetic_library["env"]
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    phrase = " ".join(synthetic.random_word(__import__("random").Random(42), 9, 12) for _ in range(5))
    page.insert_textbox(pymupdf.Rect(40, 80, 380, 560), phrase + ". " + " ".join(synthetic_library["vocab"][:80]), fontsize=9)
    doc.save(library / "new_book.pdf")
    doc.close()
    (library / "spread.pdf").unlink()
    search = run_script("search_references.py", synthetic_library["needles"]["labelled"]["phrase"], env=env, cwd=library.parent)
    assert "new document(s): new_book.pdf" in search.stdout and "removed document(s): spread.pdf" in search.stdout
    check = json.loads(run_script("index_local_books.py", "--check", env=env, cwd=library.parent).stdout)
    assert check["new"] == ["new_book.pdf"] and check["removed"] == ["spread.pdf"]
    update = json.loads(run_script("index_local_books.py", env=env, cwd=library.parent).stdout)
    assert update["processed"] == ["new_book.pdf"] and update["removed"] == ["spread.pdf"]
    found = run_script("search_references.py", phrase, "--json", env=env, cwd=library.parent)
    assert json.loads(found.stdout)["results"][0]["locator"]["relpath"] == "new_book.pdf"
    gone = run_script("search_references.py", synthetic_library["needles"]["spread"]["phrase"], "--json", env=env, cwd=library.parent)
    assert all(r["locator"]["relpath"] != "spread.pdf" for r in json.loads(gone.stdout)["results"])


def test_touch_without_content_change_skips_reextraction(synthetic_library):
    library, env = synthetic_library["library"], synthetic_library["env"]
    target = library / "headers.pdf"
    os.utime(target, None)
    update = json.loads(run_script("index_local_books.py", env=env, cwd=library.parent).stdout)
    assert update["processed"] == [] and update["skipped_unchanged_content"] == ["headers.pdf"]
    assert json.loads(run_script("index_local_books.py", "--check", env=env, cwd=library.parent).stdout)["is_stale"] is False


def test_changed_content_reindexes_only_that_document(synthetic_library):
    library, env = synthetic_library["library"], synthetic_library["env"]
    import pymupdf
    doc = pymupdf.open(library / "headers.pdf")
    doc[0].insert_text((40, 580), " ".join(synthetic_library["vocab"][100:110]), fontsize=8)
    doc.save(library / "headers_tmp.pdf")
    doc.close()
    shutil.move(library / "headers_tmp.pdf", library / "headers.pdf")
    update = json.loads(run_script("index_local_books.py", env=env, cwd=library.parent).stdout)
    assert update["processed"] == ["headers.pdf"]


def test_indexing_never_modifies_originals(tmp_path, isolated_env):
    library = tmp_path / "books"
    synthetic.build_pdf_library(library, seed=9)
    before = {p.name: (sha(p), p.stat().st_mtime_ns) for p in library.iterdir()}
    env = {"EBDSW_CONFIG": str(tmp_path / "c.json")}
    run_script("setup_rag.py", "--library-root", library, "--data-dir", tmp_path / "rag", env=env)
    assert run_script("index_local_books.py", "--rebuild", env=env, cwd=tmp_path).returncode == 0
    after = {p.name: (sha(p), p.stat().st_mtime_ns) for p in library.iterdir()}
    assert before == after  # same files, same content, same mtimes; nothing added to the library


def test_reconfigure_requires_force(synthetic_library, tmp_path):
    other = tmp_path / "other_books"
    other.mkdir()
    env = synthetic_library["env"]
    refused = run_script("setup_rag.py", "--library-root", other, "--data-dir", synthetic_library["data_dir"], env=env)
    assert refused.returncode == 2 and "--force" in refused.stderr
    forced = run_script("setup_rag.py", "--library-root", other, "--data-dir", synthetic_library["data_dir"], "--force", env=env)
    assert forced.returncode == 0


def test_broken_symlink_in_library_does_not_break_discovery(synthetic_library):
    library, env = synthetic_library["library"], synthetic_library["env"]
    (library / "dangling.pdf").symlink_to(library / "does_not_exist.pdf")
    result = run_script("search_references.py", synthetic_library["needles"]["labelled"]["phrase"], env=env, cwd=library.parent)
    assert result.returncode == 0, result.stderr
    assert run_script("discover_books.py", env=env, cwd=library.parent).returncode == 0
