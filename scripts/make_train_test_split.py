#!/usr/bin/env python3
"""Make the project's train/test split ONCE, with scikit-learn, and register it.

The training split becomes the data for every later phase (EDA, modelling,
internal validation); the test split stays sealed until final_evaluation.
A second split is refused unless the user explicitly approved a 'resplit'
decision after the current split was made.

Requires pandas and scikit-learn: run it with the project's Python.

    make_train_test_split.py --source data/raw/full.csv --train-out data/processed/train.csv \\
        --test-out data/processed/test.csv --test-size 0.2 --seed 42 --decision D-001
    ... --method stratified --stratify churn      # keeps class proportions (classification)
    ... --method group --group customer_id        # a unit never appears in both splits
    ... --method temporal --time-col date         # test = most recent period (no shuffling)

Only sizes and training-side counts are printed; test values are never shown.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402
import ds_policy  # noqa: E402


class SplitError(Exception):
    pass


def read_table(path: Path):
    import pandas as pd
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        return pd.read_csv(path)
    if suffix in (".tsv", ".tab"):
        return pd.read_csv(path, sep="\t")
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise SplitError(f"unsupported format {suffix}: use CSV, TSV or Parquet")


def write_table(frame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False, sep="\t" if suffix in (".tsv", ".tab") else ",")


def split_frame(frame, method: str, test_size: float, seed: int | None, stratify=None, group=None, time_col=None):
    from sklearn.model_selection import GroupShuffleSplit, train_test_split
    details = {}
    if method in ("random", "stratified"):
        labels = None
        if method == "stratified":
            if stratify not in frame.columns:
                raise SplitError(f"stratify column {stratify!r} not found")
            labels = frame[stratify]
        try:
            train, test = train_test_split(frame, test_size=test_size, random_state=seed, shuffle=True, stratify=labels)
        except ValueError as exc:
            raise SplitError(f"scikit-learn refused the split: {exc}") from exc
        if method == "stratified":
            details["train_class_counts"] = {str(k): int(v) for k, v in train[stratify].value_counts().items()}
    elif method == "group":
        if group not in frame.columns:
            raise SplitError(f"group column {group!r} not found")
        splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        train_idx, test_idx = next(splitter.split(frame, groups=frame[group]))
        train, test = frame.iloc[train_idx], frame.iloc[test_idx]
        details.update(groups_train=int(train[group].nunique()), groups_test=int(test[group].nunique()))
    elif method == "temporal":
        if time_col not in frame.columns:
            raise SplitError(f"time column {time_col!r} not found")
        ordered = frame.sort_values(time_col, kind="mergesort")
        cut = len(ordered) - math.ceil(len(ordered) * test_size)
        if cut <= 0 or cut >= len(ordered):
            raise SplitError("test_size leaves an empty split")
        boundary = ordered[time_col].iloc[cut]
        train, test = ordered[ordered[time_col] < boundary], ordered[ordered[time_col] >= boundary]
        details.update(time_column=time_col, train_until=str(train[time_col].max()), test_from=str(boundary),
                       note="rows sharing the boundary timestamp all go to the test split")
    else:
        raise SplitError(f"unknown method {method}")
    return train, test, details


def authorise(project, source: Path, train_out: Path, test_out: Path, decision: str | None) -> dict:
    policy, state = project.policy(), project.state()
    roles = ds_policy.classify_path(project, policy, source)
    if "test_data" in roles:
        raise SplitError("source is TEST data")
    if "full_data" not in roles:
        raise SplitError(f"source {source} is not declared as full_data in the data policy")
    previous = state.get("split")
    if previous:
        approved = project.approved("resplit", since_index=previous.get("decision_events", 0))
        kind = "resplit"
        if not approved:
            raise SplitError(
                f"the train/test split was already made on {previous['at']} (decision {previous['decision']}). "
                "It is made once per project; keep using the training split. Only the user can authorise a "
                "different split, by approving a 'resplit' decision.")
    else:
        approved = project.approved("split_authorization")
        kind = "split_authorization"
        if not approved:
            raise SplitError("no 'split_authorization' decision approved by the user: propose one "
                             "(method, test size, seed, stratify/group/time column) and ask the user to approve it")
        for out in (train_out, test_out):
            if out.exists():
                raise SplitError(f"{out} already exists; never overwrite data silently")
    if decision:
        approved = [d for d in approved if d["id"] == decision]
        if not approved:
            raise SplitError(f"{decision} is not an approved {kind} decision")
    return {"decision": approved[-1]["id"], "kind": kind, "previous": previous}


def register(project, manifest: dict, train_rel: str, test_rel: str, keep_eda_sample: bool) -> None:
    policy = json.loads(project.policy_path.read_text(encoding="utf-8"))
    policy["development_data"] = [train_rel]
    policy["test_data"] = [test_rel]
    if not (keep_eda_sample and policy.get("eda_sample")):
        policy["eda_sample"] = [train_rel]  # the training split is the EDA data
    policy["split_status"] = "done"
    common.write_json_atomic(project.policy_path, policy)
    state = project.state()
    if state.get("split"):
        state.setdefault("split_history", []).append(state["split"])
    state["split"] = {"at": manifest["created_at"], "decision": manifest["authorization_decision"],
                      "manifest": str(project.split_manifest_path), "decision_events": len(project.events()),
                      "phase_at_split": state["phase"]}
    state["history"].append({"at": manifest["created_at"], "event": "train_test_split", "decision": manifest["authorization_decision"]})
    project.save_state(state)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", help="Full dataset (default: the single full_data entry of the policy).")
    parser.add_argument("--train-out", required=True)
    parser.add_argument("--test-out", required=True)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--method", choices=["random", "stratified", "group", "temporal"], default="random")
    parser.add_argument("--stratify")
    parser.add_argument("--group")
    parser.add_argument("--time-col")
    parser.add_argument("--seed", type=int, help="Required except for --method temporal.")
    parser.add_argument("--decision", help="Approved split_authorization (or resplit) decision id.")
    parser.add_argument("--keep-eda-sample", action="store_true",
                        help="Keep an already declared EDA sample instead of using the training split for EDA.")
    args = parser.parse_args(argv)
    try:
        if not 0 < args.test_size < 1:
            raise SplitError("--test-size must be between 0 and 1")
        if args.method != "temporal" and args.seed is None:
            raise SplitError("--seed is required for reproducibility")
        root = common.find_project_root(os.getcwd())
        if root is None:
            raise SplitError("no .ds-workflow/data-policy.json: run ds_policy.py init --split-pending --full <source> first")
        project = ds_policy.Project(root)
        policy = project.policy()
        if args.source:
            source = Path(args.source).resolve()
        elif len(policy["full_data"]) == 1:
            source = (project.root / policy["full_data"][0]).resolve()
        else:
            raise SplitError("give --source (the policy declares zero or several full_data entries)")
        train_out, test_out = Path(args.train_out).resolve(), Path(args.test_out).resolve()
        for out in (train_out, test_out):
            if not out.is_relative_to(project.root):
                raise SplitError(f"{out} must be inside the project")
        if not source.is_file():
            raise SplitError(f"{source} not found")
        auth = authorise(project, source, train_out, test_out, args.decision)
        try:
            import pandas
            import sklearn
        except ImportError as exc:
            raise SplitError(f"{exc.name} is not installed in this interpreter; run with the project's Python") from exc
        frame = read_table(source)
        train, test = None, None
        train, test, details = split_frame(frame, args.method, args.test_size, args.seed,
                                           args.stratify, args.group, args.time_col)
    except SplitError as exc:
        common.eprint(f"REFUSED: {exc}")
        return 1
    write_table(train, train_out)
    write_table(test, test_out)
    rel = lambda p: p.relative_to(project.root).as_posix()  # noqa: E731
    warnings = []
    previous = auth["previous"]
    if previous:
        warnings.append(
            f"re-split after a previous split made in phase '{previous.get('phase_at_split')}' (now '{project.state()['phase']}'): "
            "rows explored or modelled before may now be in the test split, so its estimate is not independent of "
            "earlier work unless that work is redone on the new training split")
    manifest = {
        "created_at": common.utc_now(), "source": str(source), "source_sha256": common.sha256_file(source),
        "n_source": int(len(frame)), "n_train": int(len(train)), "n_test": int(len(test)),
        "test_size_requested": args.test_size, "method": args.method, "stratify": args.stratify,
        "group": args.group, "time_col": args.time_col, "seed": args.seed,
        "train": str(train_out), "train_sha256": common.sha256_file(train_out),
        "test": str(test_out), "test_sha256": common.sha256_file(test_out),
        "authorization_decision": auth["decision"], "authorization_kind": auth["kind"],
        "library_versions": {"pandas": pandas.__version__, "scikit-learn": sklearn.__version__},
        "details": details, "warnings": warnings,
        "usage": "training split: EDA, modelling and internal validation; test split: sealed until final_evaluation",
    }
    common.write_json_atomic(project.split_manifest_path, manifest)
    register(project, manifest, rel(train_out), rel(test_out), args.keep_eda_sample)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("test_sha256",)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
