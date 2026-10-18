"""Shared helpers for the local (no-Docker) SWE-bench Verified harness.

Reuses the official `swebench` package (pinned to 4.1.0, the last release that
ships MAP_REPO_VERSION_TO_SPECS / get_test_directives) for specs, test
directives, log parsers and grading.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from swebench.harness.constants import (
    MAP_REPO_TO_REQS_PATHS,
    MAP_REPO_VERSION_TO_SPECS,
)
from swebench.harness.grading import get_eval_tests_report, get_resolution_status
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
from swebench.harness.test_spec.python import get_test_directives
from swebench.harness.utils import get_modified_files

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset.jsonl"
MIRRORS = ROOT / "mirrors"
ENVS = ROOT / "envs"
RUNS = ROOT / "runs"
LOCKS = ROOT / ".locks"
SETUP_REF = "refs/swebench/setup"
EVAL_TIMEOUT_S = 15 * 60

# Untracked artifacts that installs / test runs create; never part of a patch.
ARTIFACT_EXCLUDES = [
    "__pycache__/",
    "*.py[cod]",
    "*.egg-info/",
    "/.eggs/",
    "/build/",
    "/dist/",
    ".pytest_cache/",
    "/.tox/",
    ".mypy_cache/",
    "/.coverage*",
    ".hypothesis/",
    "*.so",
]

# Base environment for every subprocess that runs repo code: no user
# PYTHONPATH, no shell profile, UTF-8 locale (the Docker images run as root
# with a UTF-8 locale).
def clean_env(venv: Path | None = None) -> dict:
    env = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
        "USER": os.environ.get("USER", "user"),
    }
    uv = shutil.which("uv")
    if uv:
        env["PATH"] = str(Path(uv).parent) + ":" + env["PATH"]
    for k in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "SSL_CERT_FILE"):
        if k in os.environ:
            env[k] = os.environ[k]
    if venv is not None:
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = f"{venv}/bin:" + env["PATH"]
    return env


# ---------------------------------------------------------------- dataset ---
_DS: dict | None = None


def load_instance(instance_id: str) -> dict:
    global _DS
    if _DS is None:
        _DS = {}
        with open(DATASET) as f:
            for line in f:
                r = json.loads(line)
                _DS[r["instance_id"]] = r
    if instance_id not in _DS:
        sys.exit(f"unknown instance_id: {instance_id}")
    return _DS[instance_id]


def spec_of(inst: dict) -> dict:
    return MAP_REPO_VERSION_TO_SPECS[inst["repo"]][inst["version"]]


def repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def mirror_of(repo: str) -> Path:
    return MIRRORS / f"{repo_slug(repo)}.git"


def env_dir_of(inst: dict) -> Path:
    return ENVS / f"{repo_slug(inst['repo'])}__{inst['version']}"


def test_files_of(inst: dict) -> list[str]:
    """Every path touched by test_patch (modified, added or deleted)."""
    return re.findall(r"^diff --git a/.* b/(.*)$", inst["test_patch"], flags=re.M)


def modified_test_files_of(inst: dict) -> list[str]:
    """Paths that exist at base_commit and are touched by test_patch (official)."""
    return get_modified_files(inst["test_patch"])


def directives_of(inst: dict) -> list[str]:
    return get_test_directives(inst)


def test_cmd_of(inst: dict, directives: list[str] | None = None) -> str:
    spec = spec_of(inst)
    tc = spec["test_cmd"]
    if isinstance(tc, list):
        tc = tc[-1]
    d = directives_of(inst) if directives is None else directives
    return " ".join([tc, *d])


# ------------------------------------------------------------------ shell ---
def run(cmd, cwd=None, env=None, check=True, capture=True, timeout=None, input=None):
    if isinstance(cmd, str):
        cmd = ["bash", "--noprofile", "--norc", "-c", cmd]
    p = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=input,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            f"command failed ({p.returncode}): {cmd}\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}"
        )
    return p


def git(wd, *args, check=True, input=None, env=None):
    return run(["git", "-C", str(wd), *args], check=check, input=input, env=env)


def run_with_timeout(script: str, cwd: Path, env: dict, timeout: int, log_path: Path) -> tuple[int, bool]:
    """Run a bash script in its own process group; kill the group on timeout."""
    with open(log_path, "w") as log:
        p = subprocess.Popen(
            ["bash", "--noprofile", "--norc", "-c", script],
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return p.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return -9, True


@contextlib.contextmanager
def file_lock(name: str):
    LOCKS.mkdir(exist_ok=True)
    with open(LOCKS / f"{name}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ----------------------------------------------------------------- mirror ---
def ensure_mirror(repo: str) -> Path:
    m = mirror_of(repo)
    with file_lock(f"mirror_{repo_slug(repo)}"):
        if not m.exists():
            MIRRORS.mkdir(exist_ok=True)
            run(["git", "clone", "--mirror", f"https://github.com/{repo}", str(m)])
    return m


def ensure_commit_in_mirror(repo: str, sha: str):
    m = ensure_mirror(repo)
    if git(m, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
        with file_lock(f"mirror_{repo_slug(repo)}"):
            git(m, "fetch", "--prune", "origin")


# -------------------------------------------------------- spec translation ---
BSD_SED = run("sed --version", check=False).returncode != 0


def translate_shell(cmd: str) -> str | None:
    """Make a spec pre_install command runnable on macOS. None = skip."""
    if re.search(r"\b(apt-get|apt|locale-gen|conda)\b", cmd):
        return None
    if BSD_SED:
        cmd = re.sub(r"\bsed -i ", "sed -i '' ", cmd)
    return cmd


def install_argv(spec_install: str, py: Path, reinstall: bool) -> list[str]:
    """Translate the spec `install` command to `uv pip install`.

    Returns argv to run inside the repo tree.  Non-editable installs are
    rebuilt with --reinstall --no-deps after the first time so edits to the
    tree are picked up (uv would otherwise reuse its cached wheel).
    """
    m = re.match(r"^\s*python -m pip install (.*)$", spec_install)
    if m:
        args = shlex.split(m.group(1))
    elif re.match(r"^\s*python setup.py install\s*$", spec_install):
        args = ["."]
    else:
        raise ValueError(f"unsupported install command: {spec_install}")
    editable = "-e" in args or "--editable" in args
    argv = ["uv", "pip", "install", "--python", str(py)]
    if editable:
        # path-based .pth like legacy `setup.py develop` (what pip did in the
        # official images for these old repos); the default setuptools
        # import-hook editables break astroid/pylint module discovery.
        argv += ["-C", "editable_mode=compat"]
    if reinstall and not editable:
        argv += ["--reinstall", "--no-deps"]
    return argv + args


def requirements_from_mirror(inst: dict) -> str:
    """Same logic as swebench get_requirements_by_commit, but read from the
    local mirror at environment_setup_commit instead of raw.githubusercontent."""
    repo = inst["repo"]
    commit = inst.get("environment_setup_commit") or inst["base_commit"]
    m = mirror_of(repo)
    ensure_commit_in_mirror(repo, commit)
    for req_path in MAP_REPO_TO_REQS_PATHS[repo]:
        p = git(m, "show", f"{commit}:{req_path}", check=False)
        if p.returncode == 0:
            text = p.stdout
            break
    else:
        raise ValueError(f"no requirements file for {repo}@{commit}")
    req_dir = "/".join(req_path.split("/")[:-1])
    exclude = lambda l: any(l.strip().startswith(x) for x in ["-e .", "#", ".[test"])
    original, extra = [], []
    for line in text.split("\n"):
        if line.strip().startswith("-r"):
            name = line[len("-r"):].strip()
            sub = f"{req_dir}/{name}" if req_dir else name
            p = git(m, "show", f"{commit}:{sub}", check=False)
            if p.returncode == 0:
                extra += [l for l in p.stdout.split("\n") if not exclude(l)]
        elif not exclude(line):
            original.append(line)
    extra.append("\n".join(original))
    text = "\n".join(extra)
    # swebench REPLACE_REQ_PACKAGES
    text = re.sub(r"^types-pkg_resources([<>=!~]=?.*|$)", "types-setuptools", text, flags=re.M)
    return text


# --------------------------------------------------------------- grading ---
def grade(inst: dict, log_text: str) -> dict:
    parser = MAP_REPO_TO_PARSER[inst["repo"]]
    ts = SimpleNamespace(
        instance_id=inst["instance_id"],
        repo=inst["repo"],
        version=inst["version"],
        FAIL_TO_PASS=json.loads(inst["FAIL_TO_PASS"]),
        PASS_TO_PASS=json.loads(inst["PASS_TO_PASS"]),
    )
    status_map = parser(log_text, ts)
    gold = {"FAIL_TO_PASS": ts.FAIL_TO_PASS, "PASS_TO_PASS": ts.PASS_TO_PASS}
    report = get_eval_tests_report(status_map, gold)
    status = get_resolution_status(report)
    return {
        "status_map_size": len(status_map),
        "report": report,
        "resolution": status,
        "resolved": status == "RESOLVED_FULL",
        "f2p_passed": len(report["FAIL_TO_PASS"]["success"]),
        "f2p_total": len(ts.FAIL_TO_PASS),
        "p2p_passed": len(report["PASS_TO_PASS"]["success"]),
        "p2p_total": len(ts.PASS_TO_PASS),
    }


def test_script(inst: dict, directives: list[str] | None = None) -> tuple[str, list[str]]:
    """Bash script = spec eval_commands (macOS-translated) + test command."""
    lines, skipped = ["set -u"], []
    for c in spec_of(inst).get("eval_commands", []):
        t = translate_shell(c)
        if t is None:
            skipped.append(c)
        else:
            lines.append(t)
    lines.append(test_cmd_of(inst, directives))
    return "\n".join(lines) + "\n", skipped
