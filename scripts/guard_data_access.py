#!/usr/bin/env python3
"""PreToolUse hook: enforce the project data policy deterministically.

Registered in SKILL.md frontmatter, so it runs for every tool call once the
skill has been invoked in a session. It does nothing in projects without
.ds-workflow/data-policy.json.

Denies, according to the phase in .ds-workflow/state.json:
* any access to test data, and to the full dataset (which contains the test
  rows), outside the final_evaluation phase; the one sanctioned reader is
  make_train_test_split.py, which splits once per project;
* development data while only the EDA data (by default the training split) is allowed;
* agent edits of the policy, state and decision log files;
* agent calls of human-only commands (ds_policy.py approve/reject/unfreeze/init --force).

Reads the hook JSON on stdin; prints a permissionDecision JSON to deny;
exits 0 with no output to defer to the normal permission flow.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402
import ds_policy  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent

PROTECTED_STATE_FILES = (common.POLICY_FILE, common.STATE_FILE, common.DECISIONS_FILE, "sample-manifest.json", "split-manifest.json")
# Names specific enough to flag in any shell command or script (state.json is too generic on its own).
DISTINCT_STATE_NAMES = (common.POLICY_FILE, common.DECISIONS_FILE, "sample-manifest.json", "split-manifest.json")
SCANNABLE_SUFFIXES = {".py", ".ipynb", ".sql", ".r", ".sh", ".qmd", ".rmd", ".jl"}
SKILL_HELPERS = ("make_eda_sample.py", "inspect_schema.py")
# Reads the full dataset and writes the test split itself; it refuses a second split without the user's approval.
SPLIT_HELPER = "make_train_test_split.py"
POLICY_AGENT_SUBCOMMANDS = {"propose", "status", "decisions", "set-phase", "freeze", "check", "init"}
HUMAN_ONLY_SUBCOMMANDS = {"approve", "reject", "unfreeze"}
PYTHON_EXE = re.compile(r"(^|/)python(3(\.\d+)?)?$")
DS_POLICY_INTERNALS = re.compile(r"\b(import\s+ds_policy|from\s+ds_policy|ds_policy\s*\.\s*(main|cmd_\w+|Project)|runpy|importlib)\b")
READ_ONLY_VIEWERS = {"cat", "head", "tail", "less", "more", "wc", "jq", "grep", "ls", "stat"}
MAX_SCAN_BYTES = 2_000_000


def deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"[evidence-based-data-science-workflow guard] {reason}",
    }}))
    return 0


def explain(role: str, phase: str, what: str) -> str:
    if role == "test_data":
        return (f"{what} touches TEST data, which stays sealed until the final_evaluation phase "
                f"(current phase: {phase}). Use development data / cross-validation instead.")
    return (f"{what} touches {role.replace('_', ' ')}, but phase '{phase}' allows only the authorised EDA sample. "
            f"Use the sample, or ask the user to advance the phase (ds_policy.py status shows requirements).")


def text_mentions(project, policy, text: str, roles: set[str]) -> str | None:
    return ds_policy.find_protected_mention(project, policy, text, roles)


def shell_segments(command: str) -> list[list[str]] | None:
    """Split a command into simple commands at shell operators (outside quotes).

    Returns None when the command cannot be parsed (unbalanced quotes): callers
    then treat it conservatively.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments, current = [], []
    for token in tokens:
        if token and all(ch in "();<>|&" for ch in token):
            if current:
                segments.append(current)
            current = []
            segments.append([token])  # keep operators visible as their own segment
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def file_redirection(segments) -> bool:
    """True when output is redirected into a file ('2>&1' and '> /dev/null' do not count)."""
    for i, seg in enumerate(segments):
        if is_operator(seg) and seg[0] in (">", ">>", ">|", "&>", "&>>"):
            target = segments[i + 1][0] if i + 1 < len(segments) and segments[i + 1] else ""
            if target != "/dev/null" and not target.isdigit():
                return True
    return False


def is_operator(segment: list[str]) -> bool:
    return len(segment) == 1 and all(ch in "();<>|&" for ch in segment[0])


def script_invocation(segment: list[str], script_name: str):
    """(index of the script token, positional args after it) if segment runs script_name."""
    for i, token in enumerate(segment):
        if token.endswith(script_name):
            if i == 0 or (i == 1 and PYTHON_EXE.search(segment[0])) or (i == 2 and PYTHON_EXE.search(segment[0]) and segment[1] in ("-I", "-u", "-B")):
                return i
    return None


def policy_subcommand(segment: list[str]) -> str | None:
    i = script_invocation(segment, "ds_policy.py")
    if i is None:
        return None
    rest, skip = segment[i + 1:], False
    for token in rest:
        if skip:
            skip = False
            continue
        if token == "--project":
            skip = True
            continue
        if token.startswith("-"):
            continue
        return token
    return ""


def single_simple_command(segments) -> list[str] | None:
    if segments is None:
        return None
    real = [seg for seg in segments if not is_operator(seg)]
    return real[0] if len(real) == 1 and len(segments) == 1 else None


def human_only_violation(command: str) -> bool:
    segments = shell_segments(command)
    if segments is None:
        return "ds_policy" in command and re.search(r"approve|reject|unfreeze|--force", command) is not None
    for segment in segments:
        sub = policy_subcommand(segment)
        if sub in HUMAN_ONLY_SUBCOMMANDS or (sub == "init" and "--force" in segment):
            return True
    return "ds_policy" in command and DS_POLICY_INTERNALS.search(command) is not None


def mentions_state_files(text: str) -> bool:
    if any(name in text for name in DISTINCT_STATE_NAMES):
        return True
    return common.POLICY_DIR_NAME in text and common.STATE_FILE in text


def referenced_files(command: str, cwd: Path) -> list[Path]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    files = []
    for token in tokens:
        candidate = Path(token).expanduser()
        if candidate.suffix.lower() in SCANNABLE_SUFFIXES:
            candidate = candidate if candidate.is_absolute() else cwd / candidate
            if candidate.is_file() and not str(candidate.resolve()).startswith(str(SCRIPT_DIR)):
                files.append(candidate)
    return files


def protects_state_file(project, path_text: str) -> bool:
    try:
        path = Path(path_text).expanduser()
        path = (path if path.is_absolute() else project.root / path).resolve()
    except (OSError, RuntimeError):
        return False
    return path.parent == project.dir.resolve() and path.name in PROTECTED_STATE_FILES


def evaluate(payload: dict) -> str | None:
    """Return a denial reason, or None to allow."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or ".")
    root = common.find_project_root(cwd)
    if root is None:
        return None
    project = ds_policy.Project(root)
    command = tool_input.get("command", "") if tool == "Bash" else ""
    if command and human_only_violation(command):
        return ("approve/reject/unfreeze/init --force (and direct use of ds_policy internals) are human-only "
                "decisions. Present the evidence and options, then ask the user to run the command themselves "
                "(prefix it with '!').")
    try:
        policy = project.policy()
        phase = project.state()["phase"]
    except (ds_policy.PolicyError, json.JSONDecodeError, KeyError) as exc:
        simple = single_simple_command(shell_segments(command)) if command else None
        if simple and policy_subcommand(simple) is not None:
            return None  # a plain ds_policy.py invocation may repair the policy
        return f"data policy unreadable ({exc}); fix .ds-workflow before touching data"
    protected = ds_policy.protected_roles_for_phase(phase)

    def path_denial(path_text: str, what: str) -> str | None:
        if not path_text:
            return None
        role = ds_policy.denied_role(ds_policy.classify_path(project, policy, path_text), phase)
        return explain(role, phase, what) if role else None

    if tool in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if tool != "Read" and protects_state_file(project, target):
            return (f"{Path(target).name} is maintained only through ds_policy.py; "
                    f"approvals and policy changes are made by the user.")
        reason = path_denial(target, f"{tool} of {target}")
        if reason:
            return reason
        new_text = "\n".join(str(tool_input.get(k, "")) for k in ("content", "new_string", "new_source"))
        for edit in tool_input.get("edits", []) or []:
            new_text += "\n" + str(edit.get("new_string", ""))
        role = text_mentions(project, policy, new_text, protected)
        if role:
            return explain(role, phase, f"code written by {tool} to {target}")
        return None

    if tool == "Grep":
        search_root = tool_input.get("path") or str(cwd)
        reason = path_denial(search_root, f"Grep in {search_root}")
        if reason:
            return reason
        root_path = Path(search_root).expanduser()
        root_path = (root_path if root_path.is_absolute() else cwd / root_path).resolve()
        glob = tool_input.get("glob")
        for role in protected:
            for entry in policy[role]:
                target = (project.root / entry.split("*")[0]).resolve()
                if target.is_relative_to(root_path) and target != root_path:
                    if glob and target.is_file() and not Path(target.name).match(glob):
                        continue
                    if glob and target.is_dir() and re.search(r"\.(py|ipynb|md|txt|toml|ya?ml|json)\}?$", glob):
                        continue
                    return explain(role, phase, f"Grep over {root_path} (it contains {entry}); narrow 'path' or add a 'glob' excluding data files")
        return None

    if tool == "Bash":
        segments = shell_segments(command)
        # Command substitution hides commands inside a segment: no exemptions then.
        exemptions = segments is not None and "`" not in command and "$(" not in command
        if not exemptions:
            segments = [[command]]
        writes = file_redirection(segments) or re.search(r"\btee\b|\bsed\s+-i|\bdd\b", command)
        for segment in (seg for seg in segments if not is_operator(seg)):
            text = " ".join(segment)
            sub = policy_subcommand(segment) if exemptions else None
            if sub in POLICY_AGENT_SUBCOMMANDS:
                continue  # ds_policy.py reads no data; free text (e.g. a proposal summary) may name protected files
            if exemptions and script_invocation(segment, SPLIT_HELPER) is not None:
                continue  # prints sizes only; enforces once-per-project and user authorisation itself
            if mentions_state_files(text):
                if exemptions and segment[0] in READ_ONLY_VIEWERS and not writes:
                    continue
                return ("the .ds-workflow policy, state, decision and sample-manifest files are maintained only by "
                        "ds_policy.py / make_eda_sample.py (read them with the Read tool, cat without redirection, "
                        "or `ds_policy.py status|decisions`).")
            if exemptions and any(script_invocation(segment, helper) is not None for helper in SKILL_HELPERS):
                # The helpers enforce the policy themselves; still never let them see test data.
                role = text_mentions(project, policy, text, {"test_data"} & protected)
                if role:
                    return explain(role, phase, "this command")
                continue
            role = text_mentions(project, policy, text, protected)
            if role:
                return explain(role, phase, "this shell command")
        for path in referenced_files(command, cwd):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:MAX_SCAN_BYTES]
            except OSError:
                continue
            if mentions_state_files(text):
                return f"{path.name} (run by this command) references .ds-workflow state files, which only ds_policy.py may change"
            role = text_mentions(project, policy, text, protected)
            if role:
                return explain(role, phase, f"{path.name} (run by this command)")
        return None
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    try:
        reason = evaluate(payload)
    except Exception as exc:  # fail closed only where a policy exists
        if common.find_project_root(payload.get("cwd")) is None:
            return 0
        reason = f"guard error ({type(exc).__name__}: {exc}); refusing data access until resolved"
    return deny(reason) if reason else 0


if __name__ == "__main__":
    sys.exit(main())
