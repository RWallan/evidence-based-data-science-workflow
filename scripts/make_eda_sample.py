#!/usr/bin/env python3
"""Draw a documented EDA sample from authorised development data (CSV/TSV).

Refuses to read test data, refuses to run without a user-approved
'sample_authorization' decision when a data policy exists, and records the
provenance (source hash, method, unit, seed, sizes, limitations) in
.ds-workflow/sample-manifest.json.

    make_eda_sample.py --source data/train.csv --out data/eda_sample.csv --n 5000 --seed 20261029 --decision D-001
    make_eda_sample.py ... --method stratified --strata region
    make_eda_sample.py ... --method group --group customer_id      # whole groups, no partial units
    make_eda_sample.py ... --method systematic                     # every k-th row (ordered data)

Methods are streaming and standard-library only. For other formats, convert
with the project's own tooling or extend this script (and its tests).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402
import ds_policy  # noqa: E402

csv.field_size_limit(sys.maxsize)


class SampleError(Exception):
    pass


def dialect_for(path: Path) -> str:
    return "excel-tab" if path.suffix.lower() in (".tsv", ".tab") else "excel"


def read_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, dialect=dialect_for(path))
        header = next(reader)
        for row in reader:
            yield header, row


def header_of(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle, dialect=dialect_for(path)))


def simple_random(path: Path, n: int, rng: random.Random):
    """Reservoir sampling (Algorithm R): exact n, one pass, row order kept."""
    reservoir, total = [], 0
    for _header, row in read_rows(path):
        if total < n:
            reservoir.append((total, row))
        else:
            j = rng.randint(0, total)
            if j < n:
                reservoir[j] = (total, row)
        total += 1
    return [row for _i, row in sorted(reservoir)], total, {}


def systematic(path: Path, n: int, rng: random.Random):
    """Every k-th row with a fractional step k = N/n and random start: spans the whole file."""
    total = sum(1 for _ in read_rows(path))
    if total == 0:
        return [], 0, {}
    n = min(n, total)
    step = total / n
    start = rng.uniform(0, step)
    chosen = {min(total - 1, int(start + k * step)) for k in range(n)}
    rows = [row for i, (_h, row) in enumerate(read_rows(path)) if i in chosen]
    return rows, total, {"step": round(step, 6), "start": round(start, 6)}


def stratified(path: Path, n: int, rng: random.Random, column: str):
    header = header_of(path)
    if column not in header:
        raise SampleError(f"strata column {column!r} not in header")
    idx = header.index(column)
    counts = Counter(row[idx] for _h, row in read_rows(path))
    total = sum(counts.values())
    if total == 0:
        return [], 0, {}
    # Proportional allocation with largest-remainder rounding (sums exactly to n).
    n = min(n, total)
    quotas = {k: n * c / total for k, c in counts.items()}
    alloc = {k: int(q) for k, q in quotas.items()}
    for k in sorted(quotas, key=lambda k: -(quotas[k] - alloc[k]))[: n - sum(alloc.values())]:
        alloc[k] += 1
    chosen = {k: set(rng.sample(range(counts[k]), min(alloc[k], counts[k]))) for k in counts}
    seen = Counter()
    rows = []
    for _h, row in read_rows(path):
        key = row[idx]
        if seen[key] in chosen[key]:
            rows.append(row)
        seen[key] += 1
    return rows, total, {"strata_population": dict(counts), "strata_sample": {k: len(v) for k, v in chosen.items()}}


def grouped(path: Path, n_groups: int, rng: random.Random, column: str):
    header = header_of(path)
    if column not in header:
        raise SampleError(f"group column {column!r} not in header")
    idx = header.index(column)
    groups = sorted({row[idx] for _h, row in read_rows(path)})
    chosen = set(rng.sample(groups, min(n_groups, len(groups))))
    total, rows = 0, []
    for _h, row in read_rows(path):
        total += 1
        if row[idx] in chosen:
            rows.append(row)
    return rows, total, {"groups_population": len(groups), "groups_sampled": len(chosen)}


def authorise(source: Path, out: Path, decision: str | None) -> dict:
    root = common.find_project_root(os.getcwd()) or common.find_project_root(source.parent)
    if root is None:
        return {"policy": None, "warning": "no data policy found: provenance recorded next to the sample only"}
    project = ds_policy.Project(root)
    policy = project.policy()
    roles = ds_policy.classify_path(project, policy, source)
    if "test_data" in roles:
        raise SampleError("source is TEST data: EDA samples must come from authorised development data")
    approved = project.approved("sample_authorization")
    if decision:
        approved = [d for d in approved if d["id"] == decision]
    if not approved:
        raise SampleError("no 'sample_authorization' decision approved by the user. Propose one with "
                          "ds_policy.py propose --kind sample_authorization and ask the user to approve it.")
    out_roles = ds_policy.classify_path(project, policy, out)
    if "eda_sample" not in out_roles:
        raise SampleError(f"output {out} is not declared as eda_sample in the data policy")
    return {"policy": str(project.policy_path), "project": project, "decision": approved[0]["id"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n", type=int, required=True, help="Rows (or groups for --method group).")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--method", choices=["simple", "stratified", "group", "systematic"], default="simple")
    parser.add_argument("--strata")
    parser.add_argument("--group")
    parser.add_argument("--unit", help="Sampling unit description (default: row, or the group column).")
    parser.add_argument("--decision", help="Approved sample_authorization decision id.")
    parser.add_argument("--limitations", action="append", default=[], help="Known representativeness limitations.")
    args = parser.parse_args(argv)
    source, out = Path(args.source).resolve(), Path(args.out).resolve()
    try:
        if not source.is_file():
            raise SampleError(f"{source} not found")
        if out.exists():
            raise SampleError(f"{out} exists; never overwrite a sample silently (remove it deliberately first)")
        auth = authorise(source, out, args.decision)
        rng = random.Random(args.seed)
        if args.method == "simple":
            rows, total, extra = simple_random(source, args.n, rng)
        elif args.method == "systematic":
            rows, total, extra = systematic(source, args.n, rng)
        elif args.method == "stratified":
            if not args.strata:
                raise SampleError("--method stratified needs --strata COLUMN")
            rows, total, extra = stratified(source, args.n, rng, args.strata)
        else:
            if not args.group:
                raise SampleError("--method group needs --group COLUMN")
            rows, total, extra = grouped(source, args.n, rng, args.group)
    except SampleError as exc:
        common.eprint(f"REFUSED: {exc}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, dialect=dialect_for(out))
        writer.writerow(header_of(source))
        writer.writerows(rows)
    manifest = {
        "created_at": common.utc_now(),
        "source": str(source), "source_sha256": common.sha256_file(source), "source_rows": total,
        "sample": str(out), "sample_sha256": common.sha256_file(out), "sample_rows": len(rows),
        "method": args.method, "unit": args.unit or (args.group if args.method == "group" else "row"),
        "strata_column": args.strata, "group_column": args.group, "seed": args.seed,
        "authorization_decision": auth.get("decision"), "details": extra,
        "limitations": args.limitations + [
            "A random sample is not automatically representative of the target population: "
            "it only mirrors the development data it was drawn from.",
        ],
    }
    target = auth["project"].sample_manifest_path if auth.get("project") else out.with_suffix(out.suffix + ".manifest.json")
    common.write_json_atomic(target, manifest)
    print(json.dumps({**manifest, "manifest_path": str(target)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
