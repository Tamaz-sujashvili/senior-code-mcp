#!/usr/bin/env python3
"""validate.py [instance_id ...] [--workers N] [--out validation.json] [--keep-workdirs]

For each instance (default: ids in instances.txt): prepare a scratch workdir,
(a) evaluate with no change -> must NOT be resolved,
(b) apply the gold `patch` to the workdir and evaluate -> must be resolved.
Instances sharing a venv run sequentially; different venvs run in parallel.
Results are merged into validation.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from swe_common import ROOT, RUNS, env_dir_of, git, load_instance  # noqa: E402
from evaluate import evaluate  # noqa: E402

PY = ROOT / ".venv" / "bin" / "python"
_wlock = Lock()


def slim(r: dict) -> dict:
    keys = ["resolved", "f2p_passed", "f2p_total", "p2p_passed", "p2p_total", "patch_size",
            "errors", "f2p_failed", "p2p_failed", "test_seconds", "run_dir"]
    return {k: r.get(k) for k in keys}


def validate_one(iid: str, keep: bool) -> dict:
    inst = load_instance(iid)
    wd = RUNS / "_validate" / iid / "wd"
    if wd.exists():
        shutil.rmtree(wd)
    t0 = time.time()
    rec = {"instance_id": iid, "repo": inst["repo"], "version": inst["version"],
           "difficulty": inst["difficulty"], "valid": False}
    try:
        p = subprocess.run([str(PY), str(ROOT / "prepare.py"), iid, str(wd)], capture_output=True, text=True)
        if p.returncode != 0:
            rec["error"] = "prepare failed: " + (p.stderr or p.stdout)[-3000:]
            return rec
        prep = json.loads(p.stdout.strip().splitlines()[-1])
        rec["skipped_packages"] = prep["skipped_packages"]
        rec["skipped_pre_install"] = prep["skipped_pre_install"]
        a = evaluate(iid, wd)
        rec["no_change"] = slim(a)
        gold = RUNS / "_validate" / iid / "gold.patch"
        gold.write_text(inst["patch"])
        ap = git(wd, "apply", str(gold), check=False)
        if ap.returncode != 0:
            rec["error"] = "gold patch did not apply to workdir: " + ap.stderr
            return rec
        b = evaluate(iid, wd)
        rec["gold"] = slim(b)
        rec["valid"] = (not a["resolved"]) and a["f2p_passed"] < a["f2p_total"] and b["resolved"] and not a["errors"] and not b["errors"]
    except Exception:
        rec["error"] = traceback.format_exc()[-3000:]
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
        if not keep and wd.exists():
            shutil.rmtree(wd, ignore_errors=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "validation.json"))
    ap.add_argument("--keep-workdirs", action="store_true")
    a = ap.parse_args()
    ids = a.ids or [l.split()[0] for l in (ROOT / "instances.txt").read_text().splitlines()
                    if l.strip() and not l.startswith("#")]
    groups = defaultdict(list)
    for iid in ids:
        groups[env_dir_of(load_instance(iid)).name].append(iid)
    out = Path(a.out)

    def save(rec):
        with _wlock:
            data = json.loads(out.read_text()) if out.exists() else {}
            data[rec["instance_id"]] = rec
            out.write_text(json.dumps(dict(sorted(data.items())), indent=1))
            g = rec.get("gold", {}); n = rec.get("no_change", {})
            print(f"{'VALID  ' if rec['valid'] else 'INVALID'} {rec['instance_id']:<34} "
                  f"none f2p={n.get('f2p_passed')}/{n.get('f2p_total')} p2p={n.get('p2p_passed')}/{n.get('p2p_total')} | "
                  f"gold f2p={g.get('f2p_passed')}/{g.get('f2p_total')} p2p={g.get('p2p_passed')}/{g.get('p2p_total')} "
                  f"{rec['seconds']}s {(rec.get('error') or '')[:300]}", flush=True)

    def run_group(iids):
        for iid in iids:
            save(validate_one(iid, a.keep_workdirs))

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(run_group, groups.values()))


if __name__ == "__main__":
    main()
