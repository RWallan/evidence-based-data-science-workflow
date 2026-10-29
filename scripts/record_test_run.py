#!/usr/bin/env python3
"""Run a test command and keep a machine-readable record of what happened.

Claims such as "tests passed" in a report must cite the record
(evidence: .ds-workflow/test-evidence/<file>.json); check_report.py
verifies that it exists and that exit_code is 0.

    record_test_run.py -- python -m pytest tests/ -q
    record_test_run.py --out-dir .ds-workflow/test-evidence -- pytest -x
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebdsw_common as common  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", help="Default: <project>/.ds-workflow/test-evidence or ./test-evidence.")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the command after --")
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        root = common.find_project_root(os.getcwd())
        out_dir = (root / common.POLICY_DIR_NAME / "test-evidence") if root else Path("test-evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        exit_code, stdout, stderr = 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        out = out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out
        exit_code, stdout, stderr = 124, out, f"timeout after {args.timeout}s"
    record = {
        "command": command, "cwd": os.getcwd(), "started_at": common.utc_now(),
        "duration_s": round(time.time() - started, 2), "exit_code": exit_code,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-2000:],
    }
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = out_dir / f"test-run-{stamp}-{os.getpid()}.json"
    common.write_json_atomic(out, record)
    print(stdout[-4000:])
    if stderr.strip():
        print(stderr[-2000:], file=sys.stderr)
    print(f"evidence: {out} (exit_code={exit_code})")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
