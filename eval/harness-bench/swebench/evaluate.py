#!/usr/bin/env python3
"""evaluate.py <instance_id> <workdir> [--patch-file F] [--timeout S]

1. Model patch = every change in <workdir> (tracked, untracked, committed)
   relative to refs/swebench/setup, minus every file the test_patch touches
   and minus install/test artifacts.  (--patch-file F grades F instead.)
2. A fresh `git worktree` of the setup commit is made under runs/; the model
   patch is applied there (git apply -> git apply --reject -> patch --fuzz=5,
   as the official harness does), so <workdir> is never modified.
3. In the instance venv: spec eval_commands, spec install from the eval tree,
   reset the test files to base_commit, apply test_patch, run spec test_cmd +
   test directives (15 min timeout, process group killed on timeout).
4. Parse with swebench MAP_REPO_TO_PARSER, grade with swebench
   get_eval_tests_report / get_resolution_status.
5. Re-install the repo from <workdir> so the env points back at the agent's tree.

Prints one JSON line; artifacts go to runs/<instance_id>/<timestamp>/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from swe_common import (  # noqa: E402
    EVAL_TIMEOUT_S,
    RUNS,
    SETUP_REF,
    clean_env,
    env_dir_of,
    file_lock,
    git,
    grade,
    load_instance,
    modified_test_files_of,
    run,
    run_with_timeout,
    test_files_of,
    test_script,
)
from prepare import ensure_env, install_repo  # noqa: E402


def model_patch(wd: Path, exclude: list[str]) -> str:
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        git(wd, "read-tree", SETUP_REF, env=env)
        git(wd, "add", "-A", "--", ".", env=env)
        specs = [f":(exclude,literal){p}" for p in exclude]
        return git(wd, "diff", "--cached", "--binary", SETUP_REF, "--", ".", *specs, env=env).stdout


def apply_patch(tree: Path, patch_file: Path) -> tuple[bool, str]:
    attempts = [
        ["git", "apply", "--verbose", str(patch_file)],
        ["git", "apply", "--verbose", "--reject", str(patch_file)],
        ["patch", "--batch", "--fuzz=5", "-p1", "-i", str(patch_file)],
    ]
    log = []
    for argv in attempts:
        p = run(argv, cwd=tree, check=False)
        log.append(f"$ {' '.join(argv)} -> {p.returncode}\n{p.stdout[-1500:]}{p.stderr[-1500:]}")
        if p.returncode == 0:
            return True, "\n".join(log)
        # undo partial application before the next attempt
        git(tree, "checkout", "-q", "--", ".", check=False)
        git(tree, "clean", "-fdq", check=False)
    return False, "\n".join(log)


def evaluate(instance_id: str, wd: Path, patch_file: Path | None = None,
             timeout: int = EVAL_TIMEOUT_S, restore: bool = True) -> dict:
    inst = load_instance(instance_id)
    t0 = time.time()
    res = {"instance_id": instance_id, "resolved": False, "f2p_passed": 0,
           "f2p_total": len(json.loads(inst["FAIL_TO_PASS"])), "p2p_passed": 0,
           "p2p_total": len(json.loads(inst["PASS_TO_PASS"])), "patch_size": 0,
           "patch_files": [], "errors": []}
    if git(wd, "rev-parse", "-q", "--verify", SETUP_REF, check=False).returncode != 0:
        res["errors"].append(f"{wd} is not a prepared workdir (missing {SETUP_REF}); run prepare.py")
        return res
    out = RUNS / instance_id / (time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}-{int(time.time() * 1000) % 1000:03d}")
    out.mkdir(parents=True, exist_ok=True)
    res["run_dir"] = str(out)

    tfiles = test_files_of(inst)
    if patch_file is None:
        patch = model_patch(wd, tfiles)
    else:
        patch = Path(patch_file).read_text()
    (out / "model.patch").write_text(patch)
    res["patch_size"] = len(patch.encode())
    res["patch_files"] = [l[len("diff --git a/"):].split(" b/")[0] for l in patch.splitlines() if l.startswith("diff --git a/")]
    res["patch_lines_changed"] = sum(1 for l in patch.splitlines()
                                     if (l.startswith("+") or l.startswith("-")) and not l.startswith(("+++", "---")))

    env, _ = ensure_env(inst)
    tree = out / "tree"
    bound = [False]
    git(wd, "worktree", "add", "-q", "--detach", str(tree), SETUP_REF)
    try:
        if patch.strip():
            ok, alog = apply_patch(tree, out / "model.patch")
            (out / "apply.log").write_text(alog)
            if not ok:
                res["errors"].append("model patch failed to apply")
                return res
        script, skipped = test_script(inst)
        if skipped:
            res["skipped_eval_commands"] = skipped
        with file_lock(f"env_{env.name}"):
            try:
                bound[0] = True
                install_repo(inst, tree)
            except RuntimeError as e:
                res["errors"].append(str(e)[:2000])
                return res
            mod = modified_test_files_of(inst)
            if mod:
                git(tree, "checkout", "-q", inst["base_commit"], "--", *mod)
            (out / "test.patch").write_text(inst["test_patch"])
            p = git(tree, "apply", "-v", str(out / "test.patch"), check=False)
            if p.returncode != 0:
                res["errors"].append("test_patch failed to apply: " + p.stderr[-1000:])
                return res
            (out / "eval.sh").write_text(script)
            t1 = time.time()
            rc, timed_out = run_with_timeout(script, tree, clean_env(env), timeout, out / "test_output.txt")
            res["test_seconds"] = round(time.time() - t1, 1)
            res["test_exit_code"] = rc
            if timed_out:
                res["errors"].append(f"tests timed out after {timeout}s")
                return res
            g = grade(inst, (out / "test_output.txt").read_text(errors="replace"))
            (out / "report.json").write_text(json.dumps(g, indent=1))
            res.update({k: g[k] for k in ("resolved", "resolution", "f2p_passed", "f2p_total", "p2p_passed", "p2p_total")})
            res["f2p_failed"] = g["report"]["FAIL_TO_PASS"]["failure"][:20]
            res["p2p_failed"] = g["report"]["PASS_TO_PASS"]["failure"][:20]
            if g["status_map_size"] == 0:
                res["errors"].append("log parser found no test results (see test_output.txt)")
    finally:
        if restore and bound[0]:
            with file_lock(f"env_{env.name}"):
                try:
                    install_repo(inst, wd)
                except RuntimeError as e:
                    res["errors"].append("restore install failed: " + str(e)[:500])
        git(wd, "worktree", "remove", "--force", str(tree), check=False)
        git(wd, "worktree", "prune", check=False)
        if tree.exists():
            shutil.rmtree(tree, ignore_errors=True)
        res["seconds"] = round(time.time() - t0, 1)
        (out / "result.json").write_text(json.dumps(res, indent=1))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id")
    ap.add_argument("workdir")
    ap.add_argument("--patch-file", default=None, help="grade this patch instead of the workdir diff")
    ap.add_argument("--timeout", type=int, default=EVAL_TIMEOUT_S)
    a = ap.parse_args()
    res = evaluate(a.instance_id, Path(a.workdir).resolve(), a.patch_file, a.timeout)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
