#!/usr/bin/env python3
"""run_tests.py <instance_id> <workdir> [test ids/paths ...] [--timeout S]

Agent-facing test runner: (re)installs the repo from <workdir> into the
instance's venv (holding the env lock) and runs the spec test_cmd with the
default directives from test_patch, or with the given ids/paths instead.
Output streams to stdout; exit code is the test command's.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from swe_common import clean_env, env_dir_of, file_lock, load_instance, test_script  # noqa: E402
from prepare import install_repo  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id")
    ap.add_argument("workdir")
    ap.add_argument("tests", nargs="*")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    inst = load_instance(a.instance_id)
    wd = Path(a.workdir).resolve()
    env = env_dir_of(inst)
    script, _ = test_script(inst, a.tests or None)
    with file_lock(f"env_{env.name}"):
        install_repo(inst, wd)
        print(f"+ {script.strip().splitlines()[-1]}", flush=True)
        try:
            p = subprocess.run(["bash", "--noprofile", "--norc", "-c", script], cwd=wd,
                               env=clean_env(env), timeout=a.timeout)
            sys.exit(p.returncode)
        except subprocess.TimeoutExpired:
            print(f"TIMEOUT after {a.timeout}s", flush=True)
            sys.exit(124)


if __name__ == "__main__":
    main()
