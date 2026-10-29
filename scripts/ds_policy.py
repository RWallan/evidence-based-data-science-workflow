#!/usr/bin/env python3
"""Project data-access policy, analysis phase gate and decision log.

State lives in <project>/.ds-workflow/:
  data-policy.json   which files are the EDA sample, development, full and test data
  state.json         current phase, history, frozen (hashed) plans
  decisions.jsonl    append-only decision events (proposed / approved / rejected)

The agent may run: init, status, check, propose, set-phase, freeze, decisions.
Human-only (blocked for the agent by guard_data_access.py): approve, reject,
init --force, unfreeze. The user runs them with the `!` prefix in Claude Code
or in a separate terminal.

    ds_policy.py init --sample data/eda_sample.csv --development data/train.csv --full data/raw.csv --test data/test.csv
    ds_policy.py propose --kind analysis_direction --summary "Target = churn_90d; EDA questions 3-5 next"
    ds_policy.py approve D-002 --note "ok, use churn_90d"        # human only
    ds_policy.py set-phase analysis_planning --decision D-002
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402

PHASES = [
    "setup",                    # project inspection, requirements, sample authorisation
    "eda",                      # exploratory analysis on the authorised sample only
    "eda_review",               # self-review of notebook, code and claims
    "awaiting_user_direction",  # EDA presented; the user steers what comes next
    "analysis_planning",        # targets, hypotheses, metrics, validation plan
    "modeling",                 # development data; test set still sealed
    "final_evaluation",         # one-shot evaluation on the test set under a frozen protocol
]
# Phases in which only the EDA sample may be read.
SAMPLE_ONLY_PHASES = {"setup", "eda", "eda_review", "awaiting_user_direction", "analysis_planning"}
HUMAN_ONLY_SUBCOMMANDS = {"approve", "reject", "unfreeze"}
DECISION_KINDS = [
    "split_authorization", "resplit", "no_test_split",
    "sample_authorization", "sample_provenance", "analysis_direction", "target_definition",
    "variable_selection", "data_cleaning", "outlier_treatment", "missing_data_treatment",
    "analysis_plan", "model_selection", "metric_choice", "conclusion", "causal_claim",
    "recommendation", "final_evaluation", "other",
]
# Transition requirements: approved decision kinds needed to *enter* a phase.
REQUIRED_APPROVALS = {
    "analysis_planning": {"analysis_direction"},
    "modeling": {"analysis_plan"},
    "final_evaluation": {"final_evaluation"},
}
REQUIRED_FROZEN = {"modeling": "pre_analysis_plan", "final_evaluation": "evaluation_protocol"}
CHECKLIST_NAME = "eda-review-checklist.md"


class PolicyError(Exception):
    pass


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

class Project:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.dir = self.root / common.POLICY_DIR_NAME
        self.policy_path = self.dir / common.POLICY_FILE
        self.state_path = self.dir / common.STATE_FILE
        self.decisions_path = self.dir / common.DECISIONS_FILE
        self.checklist_path = self.dir / CHECKLIST_NAME
        self.sample_manifest_path = self.dir / "sample-manifest.json"
        self.split_manifest_path = self.dir / "split-manifest.json"

    @classmethod
    def locate(cls, start=None) -> "Project":
        root = common.find_project_root(start or os.getcwd())
        if root is None:
            raise PolicyError("no .ds-workflow/data-policy.json found in this directory or its parents; run ds_policy.py init")
        return cls(root)

    def policy(self) -> dict:
        try:
            policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyError(f"cannot read {self.policy_path}: {exc}") from exc
        for key in ("eda_sample", "development_data", "full_data", "test_data"):
            value = policy.get(key, [])
            policy[key] = [value] if isinstance(value, str) else list(value)
        policy.setdefault("match_basenames", True)
        return policy

    def state(self) -> dict:
        if not self.state_path.is_file():
            return {"phase": "setup", "history": [], "frozen": {}}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        common.write_json_atomic(self.state_path, state)

    def events(self) -> list[dict]:
        if not self.decisions_path.is_file():
            return []
        with self.decisions_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def decisions(self) -> dict[str, dict]:
        """Fold the event log into the current state of each decision."""
        current: dict[str, dict] = {}
        for index, event in enumerate(self.events()):
            if event["event"] == "proposed":
                current[event["id"]] = {**event, "status": "proposed", "history": [event["event"]]}
            elif event["id"] in current:
                decision = current[event["id"]]
                decision["history"].append(event["event"])
                if event["event"] in ("approved", "rejected"):
                    decision["status"] = event["event"]
                    decision["resolved_at"] = event["at"]
                    decision["resolved_via"] = event.get("via")
                    decision["user_note"] = event.get("note")
                    decision["resolved_index"] = index
        return current

    def approved(self, kind: str, since_index: int = 0) -> list[dict]:
        """Decisions of a kind approved by the user (human-only CLI), optionally
        only those resolved after a given position in the event log."""
        return [d for d in self.decisions().values()
                if d["kind"] == kind and d["status"] == "approved" and d.get("resolved_via") == "human_cli"
                and d.get("resolved_index", -1) >= since_index]


# --------------------------------------------------------------------------
# Path classification (shared with guard_data_access.py)
# --------------------------------------------------------------------------

def _has_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in "*?[")


def classify_path(project: Project, policy: dict, candidate: str | Path) -> set[str]:
    """Return the data roles ('test_data', 'full_data', ...) a path belongs to."""
    try:
        cand = Path(candidate).expanduser()
        cand = (cand if cand.is_absolute() else project.root / cand).resolve()
    except (OSError, RuntimeError):
        return set()
    roles = set()
    for role in ("test_data", "full_data", "development_data", "eda_sample"):
        for entry in policy[role]:
            if _has_glob(entry):
                rel = cand.relative_to(project.root).as_posix() if cand.is_relative_to(project.root) else None
                if fnmatch.fnmatch(str(cand), str(project.root / entry)) or (rel and fnmatch.fnmatch(rel, entry)):
                    roles.add(role)
                continue
            target = (project.root / entry).resolve()
            if cand == target or cand.is_relative_to(target):
                roles.add(role)
    return roles


def protected_roles_for_phase(phase: str) -> set[str]:
    """The full dataset contains the test rows, so it stays sealed exactly like the test set.

    Its only sanctioned reader before final_evaluation is make_train_test_split.py (once per project).
    """
    roles = set()
    if phase != "final_evaluation":
        roles |= {"test_data", "full_data"}
    if phase in SAMPLE_ONLY_PHASES:
        roles.add("development_data")
    return roles


def denied_role(roles: set[str], phase: str) -> str | None:
    """A path is denied if it carries any role protected in this phase.

    A sample that sits inside a protected directory stays readable.
    """
    if "eda_sample" in roles and not ("test_data" in roles and phase != "final_evaluation"):
        return None
    protected = roles & protected_roles_for_phase(phase)
    return sorted(protected)[0] if protected else None


def _literal_pattern(literal: str) -> str:
    # Not glued to a longer name on either side ('data' must not match 'metadata' or 'data.csv').
    return r"(?<![\w.-])" + re.escape(literal) + r"(?![\w-]|\.\w)"


def _glob_component_pattern(component: str) -> str:
    out = []
    for ch in component:
        out.append(r"[^\s'\"/\\]*" if ch == "*" else r"[^\s'\"/\\]" if ch == "?" else re.escape(ch))
    return r"(?<![\w.-])" + "".join(out) + r"(?![\w-])"


def text_matchers(project: Project, policy: dict, roles: set[str]) -> list[tuple[re.Pattern, str]]:
    """Regexes that recognise references to data of the given roles in code or commands."""
    matchers = []
    for role in sorted(roles):
        for entry in policy[role]:
            patterns = set()
            literal = re.split(r"[*?\[]", entry, maxsplit=1)[0].rstrip("/") if _has_glob(entry) else entry.rstrip("/")
            literal = literal[2:] if literal.startswith("./") else literal
            if literal:
                patterns.add(_literal_pattern(str((project.root / literal).resolve())))
                patterns.add(_literal_pattern(literal))
                base = Path(literal).name
                if not _has_glob(entry) and policy.get("match_basenames") and "." in base and len(base) >= 5:
                    patterns.add(_literal_pattern(base))
            if _has_glob(entry):
                last = entry.rstrip("/").split("/")[-1]
                if _has_glob(last) and last.strip("*?") and len(last.strip("*?")) >= 3:
                    patterns.add(_glob_component_pattern(last))
            matchers += [(re.compile(p), role) for p in patterns]
    return matchers


def find_protected_mention(project: Project, policy: dict, text: str, roles: set[str]) -> str | None:
    """Role of the first protected data reference in text, ignoring references to the EDA sample."""
    if not text or not roles:
        return None
    # Longest first: stripping the bare basename before 'data/sample.csv' would leave 'data/' behind.
    sample_patterns = sorted(text_matchers(project, policy, {"eda_sample"}), key=lambda m: -len(m[0].pattern))
    for pattern, _role in sample_patterns:
        text = pattern.sub(" ", text)
    for pattern, role in text_matchers(project, policy, roles):
        if pattern.search(text):
            return role
    return None


def path_strings(project: Project, policy: dict, roles: set[str]) -> list[tuple[str, str]]:
    """Literal strings that identify protected data inside commands or code."""
    found = []
    for role in roles:
        for entry in policy[role]:
            literal = entry.split("*")[0].rstrip("/") if _has_glob(entry) else entry.rstrip("/")
            if not literal:
                continue
            absolute = str((project.root / literal).resolve())
            variants = {absolute, literal.lstrip("./") if literal.startswith("./") else literal}
            base = Path(literal).name
            if policy.get("match_basenames") and "." in base and len(base) >= 5 and not _has_glob(entry):
                variants.add(base)
            found += [(v, role) for v in variants if v]
    return found


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def next_decision_id(project: Project) -> str:
    numbers = [int(m.group(1)) for e in project.events() if (m := re.match(r"D-(\d+)$", e["id"]))]
    return f"D-{(max(numbers) + 1) if numbers else 1:03d}"


def cmd_init(project_root: Path, args) -> dict:
    project = Project(project_root)
    if project.policy_path.exists() and not args.force:
        raise PolicyError(f"{project.policy_path} exists. Changing the data policy is a human decision: the user runs init --force.")
    test = args.test or []
    if args.split_pending:
        if test or not args.full:
            raise PolicyError("--split-pending declares --full SOURCE (to be split once by make_train_test_split.py) and no --test")
    elif not test:
        raise PolicyError("declare the test data (--test PATH); or --split-pending with --full when the train/test split "
                          "is still to be made; or --test NONE --no-test-reason '...' if the user explicitly wants no split")
    if test == ["NONE"] and not args.no_test_reason:
        raise PolicyError("--test NONE requires --no-test-reason explaining how performance will be evaluated")
    policy = {
        "schema_version": 1,
        "eda_sample": args.sample or [],
        "development_data": args.development or [],
        "full_data": args.full or [],
        "test_data": [] if test == ["NONE"] else test,
        "no_test_set_reason": args.no_test_reason,
        "split_status": "pending" if args.split_pending else ("none" if test == ["NONE"] else "declared"),
        "match_basenames": not args.no_basename_matching,
        "created_at": common.utc_now(),
    }
    project.dir.mkdir(parents=True, exist_ok=True)
    common.write_json_atomic(project.policy_path, policy)
    if not project.state_path.exists():
        project.save_state({"phase": "setup", "history": [{"at": common.utc_now(), "phase": "setup", "note": "init"}], "frozen": {}})
    gitignore = project.dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# keep policy/decisions under version control; ignore bulky evidence if desired\ntest-evidence/*.log\n", encoding="utf-8")
    return {"policy": policy, "path": str(project.policy_path)}


def cmd_propose(project: Project, args) -> dict:
    if args.kind not in DECISION_KINDS:
        raise PolicyError(f"unknown kind {args.kind!r}; choose from {DECISION_KINDS}")
    event = {
        "id": next_decision_id(project), "event": "proposed", "at": common.utc_now(), "kind": args.kind,
        "summary": args.summary, "options": args.option or [], "evidence": args.evidence or [],
        "proposed_by": "agent", "phase": project.state()["phase"],
    }
    common.append_jsonl(project.decisions_path, event)
    return {**event, "next_step": f"Ask the user. To approve, the USER runs: ! python3 {Path(__file__).resolve()} approve {event['id']}"}


def cmd_resolve(project: Project, args, verdict: str) -> dict:
    decisions = project.decisions()
    if args.id not in decisions:
        raise PolicyError(f"unknown decision {args.id}")
    if decisions[args.id]["status"] != "proposed":
        raise PolicyError(f"{args.id} is already {decisions[args.id]['status']}")
    event = {"id": args.id, "event": verdict, "at": common.utc_now(), "via": "human_cli", "note": args.note}
    common.append_jsonl(project.decisions_path, event)
    return event


def checklist_open_items(path: Path) -> list[str]:
    if not path.is_file():
        return [f"checklist {path} is missing (copy templates/exploratory-analysis-checklist.md there and work through it)"]
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if re.match(r"\s*[-*]\s*\[ \]", line)]


def split_train_paths(project: Project) -> set[Path]:
    try:
        manifest = json.loads(project.split_manifest_path.read_text(encoding="utf-8"))
        return {Path(manifest["train"]).resolve()}
    except (OSError, json.JSONDecodeError, KeyError):
        return set()


def split_manifest_problems(project: Project) -> list[str]:
    """The EDA runs on the training split: check it is the one produced by the authorised split."""
    try:
        manifest = json.loads(project.split_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["split-manifest.json missing or unreadable"]
    problems = []
    for part in ("train", "test"):
        path = Path(manifest.get(part, ""))
        if not path.is_file() or common.sha256_file(path) != manifest.get(f"{part}_sha256"):
            problems.append(f"{part} split changed or missing since make_train_test_split.py created it ({path})")
    authorised = {d["id"] for kind in ("split_authorization", "resplit") for d in project.approved(kind)}
    if manifest.get("authorization_decision") not in authorised:
        problems.append("split-manifest.json does not reference a split decision approved by the user")
    return problems


def sample_manifest_problems(project: Project, samples: list[Path]) -> list[str]:
    hint = ("sample provenance undocumented: make_eda_sample.py writes sample-manifest.json, or the user approves "
            "a 'sample_provenance' decision for a sample they supplied")
    if not project.sample_manifest_path.is_file():
        return [hint]
    try:
        manifest = json.loads(project.sample_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [f"sample-manifest.json is not valid JSON; {hint}"]
    problems = []
    current = {common.sha256_file(s) for s in samples}
    if manifest.get("sample_sha256") not in current:
        problems.append("sample-manifest.json does not match the current sample file (hash differs)")
    authorised = {d["id"] for d in project.approved("sample_authorization")}
    if manifest.get("authorization_decision") not in authorised:
        problems.append("sample-manifest.json does not reference a sample_authorization decision approved by the user")
    return problems


def transition_problems(project: Project, target: str, args) -> list[str]:
    state, policy = project.state(), project.policy()
    current = state["phase"]
    problems = []
    if target not in PHASES:
        return [f"unknown phase {target!r}; phases: {PHASES}"]
    if PHASES.index(target) <= PHASES.index(current):
        return []  # going back is always allowed (more restrictive or a redo)
    if PHASES.index(target) > PHASES.index(current) + 1:
        problems.append(f"cannot skip from {current} to {target}; advance one phase at a time")
    if target == "eda":
        status = policy.get("split_status", "declared")
        if status == "pending":
            problems.append("the train/test split has not been made: propose a 'split_authorization' decision, wait for the "
                            "user's approval, then run make_train_test_split.py")
        if status == "none" and not project.approved("no_test_split"):
            problems.append("no test set declared: the user must explicitly approve a 'no_test_split' decision")
        samples = [project.root / s for s in policy["eda_sample"]]
        if not samples and status != "pending":
            problems.append("no eda_sample declared in the data policy")
        missing = [str(s) for s in samples if not s.exists()]
        if missing:
            problems.append(f"declared EDA sample does not exist: {missing}. Ask the user for authorisation, then run make_eda_sample.py")
        if samples and not missing and not project.approved("sample_provenance"):
            if split_train_paths(project) == {s.resolve() for s in samples}:
                problems += split_manifest_problems(project)
            else:
                problems += sample_manifest_problems(project, samples)
    if target == "eda_review":
        from audit_notebook import audit_paths  # local import keeps the guard light
        notebooks = args.notebook or []
        if not notebooks:
            problems.append("give --notebook PATH (the EDA notebook) so it can be audited")
        else:
            report = audit_paths([Path(n) for n in notebooks], project)
            problems += [f"audit: {f['message']} ({f['file']})" for f in report["findings"] if f["severity"] == "error"]
    if target == "awaiting_user_direction":
        problems += [f"open checklist item: {item}" for item in checklist_open_items(project.checklist_path)]
    # Re-entering a phase (e.g. final_evaluation after going back) needs an approval newer than the last exit.
    exits = [h for h in state.get("history", []) if h.get("from") == target]
    since = exits[-1].get("decision_events", 0) if exits else 0
    for kind in REQUIRED_APPROVALS.get(target, ()):
        approved = project.approved(kind, since_index=since)
        if args.decision:
            approved = [d for d in approved if d["id"] == args.decision]
        if not approved:
            problems.append(f"entering {target} needs a '{kind}' decision approved by the user via the human-only approve command"
                            + (" (re-entry: an approval given after the last exit from this phase)" if since else ""))
    frozen_key = REQUIRED_FROZEN.get(target)
    if frozen_key:
        frozen = state.get("frozen", {}).get(frozen_key)
        if not frozen:
            problems.append(f"entering {target} needs a frozen {frozen_key}: run ds_policy.py freeze {frozen_key} PATH")
        elif not Path(frozen["path"]).is_file():
            problems.append(f"frozen {frozen_key} {frozen['path']} no longer exists")
        elif common.sha256_file(Path(frozen["path"])) != frozen["sha256"]:
            problems.append(f"{frozen_key} changed after it was frozen ({frozen['path']}); changes after freezing must be approved and re-frozen by the user")
    return problems


def cmd_set_phase(project: Project, args) -> dict:
    problems = transition_problems(project, args.phase, args)
    if problems:
        raise PolicyError("transition refused:\n  - " + "\n  - ".join(problems))
    state = project.state()
    state["history"].append({"at": common.utc_now(), "from": state["phase"], "phase": args.phase,
                             "decision": args.decision, "note": args.note,
                             "decision_events": len(project.events())})
    state["phase"] = args.phase
    project.save_state(state)
    return {"phase": args.phase, "allowed_data": sorted({"eda_sample", "full_data", "development_data", "test_data"} - protected_roles_for_phase(args.phase))}


def cmd_freeze(project: Project, args) -> dict:
    path = Path(args.path).resolve()
    if not path.is_file():
        raise PolicyError(f"{path} does not exist")
    state = project.state()
    if args.name in state.get("frozen", {}):
        raise PolicyError(f"{args.name} already frozen; unfreezing is a human decision (unfreeze)")
    state.setdefault("frozen", {})[args.name] = {"path": str(path), "sha256": common.sha256_file(path), "at": common.utc_now()}
    project.save_state(state)
    return state["frozen"][args.name]


def cmd_unfreeze(project: Project, args) -> dict:
    state = project.state()
    removed = state.get("frozen", {}).pop(args.name, None)
    state["history"].append({"at": common.utc_now(), "event": "unfreeze", "name": args.name, "via": "human_cli"})
    project.save_state(state)
    return {"unfrozen": removed}


def cmd_check(project: Project, args) -> dict:
    policy, phase = project.policy(), project.state()["phase"]
    roles = classify_path(project, policy, args.path)
    blocked = denied_role(roles, phase)
    return {"path": args.path, "roles": sorted(roles), "phase": phase, "allowed": blocked is None, "blocked_role": blocked}


def cmd_status(project: Project) -> dict:
    state, decisions = project.state(), project.decisions()
    return {
        "project_root": str(project.root), "phase": state["phase"],
        "protected_now": sorted(protected_roles_for_phase(state["phase"])),
        "policy": project.policy(),
        "pending_decisions": [{k: d[k] for k in ("id", "kind", "summary")} for d in decisions.values() if d["status"] == "proposed"],
        "approved_decisions": [{k: d[k] for k in ("id", "kind", "summary")} for d in decisions.values() if d["status"] == "approved"],
        "frozen": state.get("frozen", {}),
        "split": state.get("split"),
        "next_phase_requirements": transition_problems(project, PHASES[min(len(PHASES) - 1, PHASES.index(state["phase"]) + 1)], argparse.Namespace(decision=None, notebook=None)) if state["phase"] != "final_evaluation" else [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="Project root (default: search upwards from the current directory).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="Declare data roles for this project.")
    p.add_argument("--sample", action="append")
    p.add_argument("--development", action="append")
    p.add_argument("--full", action="append")
    p.add_argument("--test", action="append", help="Test data path(s); use NONE with --no-test-reason only if the user explicitly wants no split.")
    p.add_argument("--split-pending", action="store_true", help="The train/test split will be made once by make_train_test_split.py from --full.")
    p.add_argument("--no-test-reason")
    p.add_argument("--no-basename-matching", action="store_true")
    p.add_argument("--force", action="store_true", help="Human only: overwrite an existing policy.")
    sub.add_parser("status")
    p = sub.add_parser("check", help="Would reading PATH be allowed now?")
    p.add_argument("path")
    p = sub.add_parser("propose", help="Record a decision/conclusion awaiting the user.")
    p.add_argument("--kind", required=True, choices=DECISION_KINDS)
    p.add_argument("--summary", required=True)
    p.add_argument("--option", action="append", help="Alternative presented to the user (repeatable).")
    p.add_argument("--evidence", action="append", help="Notebook cell, file or citation supporting it (repeatable).")
    for verdict in ("approve", "reject"):
        p = sub.add_parser(verdict, help=f"Human only: {verdict} a proposed decision.")
        p.add_argument("id")
        p.add_argument("--note")
    p = sub.add_parser("set-phase")
    p.add_argument("phase", choices=PHASES)
    p.add_argument("--decision", help="Approved decision id that authorises the transition.")
    p.add_argument("--notebook", action="append", help="EDA notebook(s) to audit when entering eda_review.")
    p.add_argument("--note")
    p = sub.add_parser("freeze", help="Hash a plan/protocol so later edits are detectable.")
    p.add_argument("name", choices=["pre_analysis_plan", "evaluation_protocol"])
    p.add_argument("path")
    p = sub.add_parser("unfreeze", help="Human only.")
    p.add_argument("name")
    sub.add_parser("decisions", help="Print the folded decision log.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = cmd_init(Path(args.project or os.getcwd()), args)
        else:
            project = Project(Path(args.project)) if args.project else Project.locate()
            if not project.policy_path.is_file():
                raise PolicyError(f"no data policy at {project.policy_path}")
            result = {
                "status": lambda: cmd_status(project),
                "check": lambda: cmd_check(project, args),
                "propose": lambda: cmd_propose(project, args),
                "approve": lambda: cmd_resolve(project, args, "approved"),
                "reject": lambda: cmd_resolve(project, args, "rejected"),
                "set-phase": lambda: cmd_set_phase(project, args),
                "freeze": lambda: cmd_freeze(project, args),
                "unfreeze": lambda: cmd_unfreeze(project, args),
                "decisions": lambda: list(project.decisions().values()),
            }[args.command]()
    except PolicyError as exc:
        common.eprint(f"REFUSED: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
