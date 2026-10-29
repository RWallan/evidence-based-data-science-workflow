#!/usr/bin/env python3
"""Structural validation of the skill itself (standard library only).

Checks: SKILL.md frontmatter (name, description length, guard hook command
resolving to an existing script); SKILL.md length; every file path mentioned
in SKILL.md exists and every reference/template file is mentioned; scripts
compile; bibliography.json schema; every citation key used in the docs exists
and every bibliography entry is listed in scientific-bibliography.md.

    python3 validate_skill.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
MAX_DESCRIPTION = 1536
MAX_SKILL_LINES = 500
REQUIRED_ENTRY_FIELDS = ("key", "type", "authors", "title", "verification")
CITATION = re.compile(r"\[@([A-Za-z0-9_:\-]+)|;\s*@([A-Za-z0-9_:\-]+)")
DOI = re.compile(r"^10\.\d{4,9}/\S+$")
PATH_MENTION = re.compile(r"`((?:references|templates|scripts)/[A-Za-z0-9_.\-/]+)`")
SCRIPT_MENTION = re.compile(r"\$S/([a-z_]+\.py)|`([a-z_]+\.py)\b")


def frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter")
    end = text.index("\n---", 4)
    return text[4:end], text[end + 4:]


def parse_frontmatter(raw: str) -> dict:
    try:
        import yaml  # optional; the minimal parser below covers the checks
        return yaml.safe_load(raw)
    except ImportError:
        data = {}
        for line in raw.splitlines():
            m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
            if m:
                data[m.group(1)] = m.group(2).strip() or {}
        data["_raw"] = raw
        return data


def hook_commands(meta: dict) -> list[str]:
    if "_raw" in meta:
        return re.findall(r'command:\s*"(.*)"', meta["_raw"])
    commands = []
    for group in (meta.get("hooks") or {}).get("PreToolUse", []):
        for hook in group.get("hooks", []):
            commands.append(hook.get("command", ""))
    return commands


def validate() -> dict:
    errors, warnings, info = [], [], {}
    skill_md = SKILL_DIR / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    raw, body = frontmatter(text)
    meta = parse_frontmatter(raw)
    if meta.get("name") != SKILL_DIR.name:
        errors.append(f"frontmatter name {meta.get('name')!r} != directory name {SKILL_DIR.name!r}")
    description = meta.get("description") or ""
    if not description:
        errors.append("frontmatter description missing")
    if len(description) > MAX_DESCRIPTION:
        errors.append(f"description is {len(description)} chars (> {MAX_DESCRIPTION}, truncated in the skill listing)")
    info["description_chars"] = len(description)
    commands = hook_commands(meta)
    if not commands:
        errors.append("no PreToolUse hook command in frontmatter (data guard missing)")
    for command in commands:
        expanded = os.path.expandvars(command.replace('\\"', '"'))
        paths = re.findall(r'"([^"]+\.py)"', expanded) or re.findall(r"(\S+\.py)", expanded)
        if not paths or not Path(paths[0]).is_file():
            errors.append(f"hook command does not resolve to an existing script: {command}")
        elif Path(paths[0]).resolve() != (SKILL_DIR / "scripts" / "guard_data_access.py").resolve():
            warnings.append(f"hook points outside this skill copy: {paths[0]} (expected when the skill is installed elsewhere)")
    lines = text.count("\n") + 1
    info["skill_md_lines"] = lines
    if lines > MAX_SKILL_LINES:
        errors.append(f"SKILL.md has {lines} lines (> {MAX_SKILL_LINES})")

    mentioned = set(PATH_MENTION.findall(text))
    for rel in sorted(mentioned):
        if not (SKILL_DIR / rel).exists():
            errors.append(f"SKILL.md mentions missing file {rel}")
    for folder in ("references", "templates"):
        for path in sorted((SKILL_DIR / folder).iterdir()):
            rel = f"{folder}/{path.name}"
            if path.name in ("bibliography.json",):
                continue
            if rel not in mentioned and path.name not in text:
                warnings.append(f"{rel} is never mentioned in SKILL.md (unreachable for the agent unless another file points to it)")
    scripts_mentioned = {a or b for a, b in SCRIPT_MENTION.findall(text)}
    for name in sorted(scripts_mentioned):
        if not (SKILL_DIR / "scripts" / name).is_file():
            errors.append(f"SKILL.md mentions missing script {name}")
    for script in sorted((SKILL_DIR / "scripts").glob("*.py")):
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except SyntaxError as exc:
            errors.append(f"{script.name} does not compile: {exc}")

    bib = json.loads((SKILL_DIR / "references" / "bibliography.json").read_text(encoding="utf-8"))
    legend = set(bib.get("verification_legend", {})) | {"partial"}
    keys = {}
    for entry in bib["entries"]:
        missing = [f for f in REQUIRED_ENTRY_FIELDS if not entry.get(f)]
        if missing:
            errors.append(f"bibliography entry {entry.get('key')!r} missing {missing}")
        key = entry.get("key")
        if key in keys:
            errors.append(f"duplicate bibliography key {key}")
        keys[key] = entry
        status = (entry.get("verification") or {}).get("status")
        if status not in legend:
            errors.append(f"{key}: verification status {status!r} not in legend {sorted(legend)}")
        if status == "crossref" and not entry.get("doi"):
            errors.append(f"{key}: status crossref without a DOI")
        if entry.get("doi") and not DOI.match(entry["doi"]):
            errors.append(f"{key}: malformed DOI {entry['doi']}")
        if status in ("crossref", "url_ok", "local_copy", "publisher_page", "partial") and not entry["verification"].get("date"):
            errors.append(f"{key}: verified status without a date")
    info["bibliography_entries"] = len(keys)
    info["verification_counts"] = {}
    for entry in bib["entries"]:
        s = entry["verification"]["status"]
        info["verification_counts"][s] = info["verification_counts"].get(s, 0) + 1

    cited_anywhere = set()
    for doc in list((SKILL_DIR / "references").glob("*.md")) + list((SKILL_DIR / "templates").glob("*.md")) + [skill_md]:
        for a, b in CITATION.findall(doc.read_text(encoding="utf-8")):
            key = a or b
            if key in ("key",):  # placeholder in templates/docs
                continue
            cited_anywhere.add(key)
            if key not in keys:
                errors.append(f"{doc.name} cites unknown key @{key}")
    listing = (SKILL_DIR / "references" / "scientific-bibliography.md").read_text(encoding="utf-8")
    for key in keys:
        if f"@{key}]" not in listing:
            errors.append(f"bibliography key {key} not listed in scientific-bibliography.md")
    info["keys_cited_in_docs"] = len(cited_anywhere)
    return {"skill_dir": str(SKILL_DIR), "errors": errors, "warnings": warnings, "info": info, "passed": not errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = validate()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for e in report["errors"]:
            print(f"ERROR: {e}")
        for w in report["warnings"]:
            print(f"WARNING: {w}")
        print(json.dumps(report["info"], ensure_ascii=False))
        print("PASSED" if report["passed"] else "FAILED")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
