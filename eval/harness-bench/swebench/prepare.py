#!/usr/bin/env python3
"""prepare.py <instance_id> <workdir> [--depth N] [--force]

Creates <workdir> as a plain git checkout of the instance's base_commit (only
history reachable from base_commit and tags that predate it; no remote), runs
the spec's pre_install edits and records them as a "SWE-bench setup" commit
(ref refs/swebench/setup, like the official image), then creates/reuses the
per-(repo,version) uv venv in envs/ and installs the repo from <workdir>.

Prints one JSON line: workdir, venv, python, test_cmd (what an agent may run),
raw_test_cmd, directives.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from swe_common import (  # noqa: E402
    ARTIFACT_EXCLUDES,
    ROOT,
    SETUP_REF,
    clean_env,
    directives_of,
    ensure_commit_in_mirror,
    env_dir_of,
    file_lock,
    git,
    install_argv,
    load_instance,
    mirror_of,
    repo_slug,
    requirements_from_mirror,
    run,
    spec_of,
    test_cmd_of,
    translate_shell,
)

ENV_META = ".swebench_env.json"


def log(msg):
    print(f"[prepare] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------- workdir ---
def make_workdir(inst: dict, wd: Path, depth: int | None, force: bool) -> dict:
    info = {"skipped_pre_install": []}
    if (wd / ".git").exists() and git(wd, "rev-parse", "-q", "--verify", SETUP_REF, check=False).returncode == 0:
        log(f"reusing existing workdir {wd}")
        info["reused"] = True
        return info
    if wd.exists() and any(wd.iterdir()):
        if not force:
            sys.exit(f"{wd} exists and is not a prepared workdir (use --force to wipe it)")
        shutil.rmtree(wd)
    wd.mkdir(parents=True, exist_ok=True)
    repo, sha = inst["repo"], inst["base_commit"]
    ensure_commit_in_mirror(repo, sha)
    m = mirror_of(repo)
    t0 = time.time()
    git(wd, "init", "-q")
    fetch = ["fetch", "-q", "--no-tags"]
    if depth:
        fetch += ["--depth", str(depth)]
    git(wd, *fetch, str(m), sha)
    git(wd, "checkout", "-q", "-B", "main", sha)
    if not depth:
        # Tags that point into base_commit's history (setuptools_scm etc. need
        # them); the official image likewise deletes only tags newer than base.
        tags = [t for t in git(m, "tag", "--merged", sha).stdout.split() if t]
        for i in range(0, len(tags), 300):
            chunk = tags[i : i + 300]
            git(wd, "fetch", "-q", "--no-tags", str(m), *[f"refs/tags/{t}:refs/tags/{t}" for t in chunk])
        info["tags"] = len(tags)
    git(wd, "config", "user.email", "setup@swebench.config")
    git(wd, "config", "user.name", "SWE-bench")
    git(wd, "config", "core.fileMode", "false")
    with open(wd / ".git" / "info" / "exclude", "a") as f:
        f.write("\n# swebench local harness: install/test artifacts\n" + "\n".join(ARTIFACT_EXCLUDES) + "\n")
    for cmd in spec_of(inst).get("pre_install", []):
        t = translate_shell(cmd)
        if t is None:
            info["skipped_pre_install"].append(cmd)
            log(f"skip pre_install (system-level): {cmd}")
            continue
        run(t, cwd=wd)
    git(wd, "commit", "-q", "--allow-empty", "-am", "SWE-bench")
    git(wd, "update-ref", SETUP_REF, "HEAD")
    git(wd, "reflog", "expire", "--expire=now", "--all")
    log(f"workdir ready in {time.time() - t0:.1f}s")
    return info


# ------------------------------------------------------------------- env ---
def _uv_install(py: Path, args: list[str], cwd=None) -> tuple[bool, str]:
    p = run(["uv", "pip", "install", "--python", str(py), *args], cwd=cwd, env=clean_env(), check=False)
    return p.returncode == 0, (p.stderr or "")[-3000:]


def _install_list(py: Path, pkgs: list[str], meta: dict, label: str):
    pkgs = [p for p in pkgs if p.strip()]
    if not pkgs:
        return
    ok, err = _uv_install(py, pkgs)
    if ok:
        return
    log(f"{label}: bulk install failed, retrying one by one")
    for p in pkgs:
        ok, err = _uv_install(py, [p])
        if not ok:
            log(f"{label}: SKIPPED {p}")
            meta["skipped_packages"].append({"pkg": p, "stage": label, "error": err.strip().splitlines()[-5:]})


def ensure_env(inst: dict) -> tuple[Path, dict]:
    env = env_dir_of(inst)
    spec = spec_of(inst)
    with file_lock(f"env_{env.name}"):
        meta_p = env / ENV_META
        if meta_p.exists():
            return env, json.loads(meta_p.read_text())
        if env.exists():
            shutil.rmtree(env)
        env.parent.mkdir(exist_ok=True)
        t0 = time.time()
        log(f"creating env {env.name} (python {spec['python']})")
        run(["uv", "venv", "-q", "--seed", "--managed-python", "--python", spec["python"], str(env)], env=clean_env())
        py = env / "bin" / "python"
        meta = {"repo": inst["repo"], "version": inst["version"], "python": spec["python"],
                "skipped_packages": [], "repo_installed": False, "bound_tree": None}
        pkgs = spec.get("packages", "")
        if pkgs == "requirements.txt":
            reqs = requirements_from_mirror(inst)
            (env / "swebench_requirements.txt").write_text(reqs)
            ok, err = _uv_install(py, ["-r", str(env / "swebench_requirements.txt")])
            if not ok:
                lines = [l.split("#")[0].strip() for l in reqs.splitlines()]
                lines = [l for l in lines if l and not l.startswith("-")]
                _install_list(py, lines, meta, "requirements.txt")
        elif pkgs == "environment.yml":
            sys.exit("environment.yml (conda) specs are not supported by this harness")
        elif pkgs:
            _install_list(py, pkgs.split(), meta, "packages")
        _install_list(py, spec.get("pip_packages", []), meta, "pip_packages")
        meta["created_s"] = round(time.time() - t0, 1)
        meta_p.write_text(json.dumps(meta, indent=1))
        log(f"env ready in {meta['created_s']}s; skipped={[s['pkg'] for s in meta['skipped_packages']]}")
        return env, meta


def install_repo(inst: dict, tree: Path) -> None:
    """(Re)install the repo from `tree` into its env. Caller must hold the env lock."""
    env = env_dir_of(inst)
    meta_p = env / ENV_META
    meta = json.loads(meta_p.read_text())
    spec = spec_of(inst)
    if "install" not in spec:
        return
    argv = install_argv(spec["install"], env / "bin" / "python", reinstall=meta["repo_installed"])
    p = run(argv, cwd=tree, env=clean_env(env), check=False)
    if p.returncode != 0 and "build_editable" in (p.stderr or ""):
        # backend pinned to setuptools<64 (no PEP 660): pip would fall back to
        # `setup.py develop`; we build with the env's own (seeded) setuptools.
        argv = argv[:5] + ["--no-build-isolation"] + argv[5:]
        p = run(argv, cwd=tree, env=clean_env(env), check=False)
    if p.returncode != 0:
        raise RuntimeError(f"repo install failed: {' '.join(argv)}\n{p.stderr[-4000:]}")
    meta["repo_installed"] = True
    meta["bound_tree"] = str(tree)
    meta_p.write_text(json.dumps(meta, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id")
    ap.add_argument("workdir")
    ap.add_argument("--depth", type=int, default=None, help="shallow history (default: full history up to base_commit)")
    ap.add_argument("--force", action="store_true", help="wipe a non-prepared, non-empty workdir")
    a = ap.parse_args()
    inst = load_instance(a.instance_id)
    wd = Path(a.workdir).resolve()
    winfo = make_workdir(inst, wd, a.depth, a.force)
    env, meta = ensure_env(inst)
    with file_lock(f"env_{env.name}"):
        install_repo(inst, wd)
    runner = ROOT / "run_tests.py"
    harness_py = ROOT / ".venv" / "bin" / "python"
    out = {
        "instance_id": inst["instance_id"],
        "repo": inst["repo"],
        "version": inst["version"],
        "workdir": str(wd),
        "base_commit": inst["base_commit"],
        "venv": str(env),
        "python": str(env / "bin" / "python"),
        "test_cmd": f"{harness_py} {runner} {inst['instance_id']} {wd}",
        "test_cmd_help": "append explicit test ids/paths to override the default directives",
        "raw_test_cmd": f"cd {wd} && PATH={env}/bin:$PATH {test_cmd_of(inst)}",
        "directives": directives_of(inst),
        "skipped_packages": [s["pkg"] for s in meta["skipped_packages"]],
        "skipped_pre_install": winfo.get("skipped_pre_install", []),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
