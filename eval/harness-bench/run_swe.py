"""SWE-bench Verified subset (20 instances, local harness) across harness arms.

Usage: python3 run_swe.py <out.jsonl> <arm>[,<arm>...] [--workers N] [instance ids...]
Arms: R = raw upstream harness; P = feat/pi-ports + routing; H = feat/ahe-ports + routing
(P and H enable code search, syntax guard, observation mask, search routing and their own ports).
Jobs that share a (repo, version) venv run sequentially in one worker; groups run in parallel.
Each run: prepare a depth-1 checkout, run `dsh --profile headless --json` with the issue text,
grade with the harness evaluate.py, save the diff and the event stream, delete the checkout.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

HERE = Path(__file__).resolve().parent
S = HERE.parent
H = S / "swebench"
HPY = H / ".venv/bin/python"
SCM = Path("/Users/tazo/Desktop/code mcp/senior-code-mcp")

HARNESS = {
    "R": S / "arms/raw",
    "P": Path("/Users/tazo/Desktop/code mcp/deepseek-harness"),
    "H": Path("/private/tmp/claude-501/-Users-tazo-Desktop-code-mcp/732d79da-a63b-441c-84e4-d379d8ed30e4/scratchpad/dsh-ahe"),
}
COMMON = {"DSH_CODE_SEARCH_COMMAND": str(SCM / ".venv/bin/senior-code-search"), "DSH_EDIT_SYNTAX_GUARD": "1",
          "DSH_OBSERVATION_MASK": "1", "DSH_SEARCH_ROUTING": "1"}
FLAGS = {
    "R": {},
    "P": COMMON | {"DSH_EDIT_NEAR_MISS": "1", "DSH_COMPACTION_FILE_TRACKING": "1"},
    "H": COMMON | {"DSH_EXECUTION_RISK_HINTS": "1", "DSH_VERIFY_BEFORE_FINISH": "1"},
}

PROMPT = """You are fixing a GitHub issue in the repository in the current directory ({repo}).

<issue>
{problem}
</issue>

Change the non-test source files so that the issue is resolved. Make the smallest correct change. Do not add or edit tests.
To run existing tests use: {runner} <test file or test id> ...
(the runner uses the project's own virtualenv; plain `python`/`pytest` may not have the dependencies)."""

DATASET = {json.loads(l)["instance_id"]: json.loads(l) for l in open(H / "dataset.jsonl")}
INSTANCES = [l.split()[0:3] for l in open(H / "instances.txt") if l.strip() and not l.startswith("#")]
LOCK = Lock()


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def short(repo):
    return repo.split("/")[1]


def one(out, iid, repo, arm):
    run_dir = HERE / "runs" / arm / iid
    shutil.rmtree(run_dir, ignore_errors=True)
    work = run_dir / short(repo)  # basename = ingested repo name for the code-search repo filter
    run_dir.mkdir(parents=True)
    # pytest derives its version from git tags (setuptools_scm); a depth-1 clone has none,
    # pytest reports 0.1.dev and its tox.ini minversion check fails every test run.
    depth = [] if repo == "pytest-dev/pytest" else ["--depth", "1"]
    prep = sh([str(HPY), str(H / "prepare.py"), iid, str(work), *depth], timeout=1800)
    if prep.returncode != 0:
        raise RuntimeError(f"prepare {iid}: {prep.stderr[-500:]}")
    runner = f"{HPY} {H / 'run_tests.py'} {iid} {work}"
    prompt = PROMPT.format(repo=repo, problem=DATASET[iid]["problem_statement"], runner=runner)

    env = {k: v for k, v in os.environ.items() if not k.startswith("DSH_")}
    env |= {"DSH_HOME": str(HERE / f"dsh_home_{arm}"), "TSX_TSCONFIG_PATH": str(HARNESS[arm] / "tsconfig.json")}
    env |= FLAGS[arm]
    if FLAGS[arm]:
        env |= {"QDRANT_COLLECTION": "scm_swe", "GRAPH_PATH": str(HERE / "index" / f"{short(repo)}.graph.json")}
    h = HARNESS[arm]
    cmd = [str(S / "withkey.sh"), "node", "--import", f"file://{h}/node_modules/tsx/dist/esm/index.mjs",
           f"{h}/apps/cli/src/bin.ts", "--profile", "headless", "--json", prompt]
    t0 = time.time()
    try:
        proc = sh(cmd, cwd=work, env=env, timeout=2400)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr, code = "timeout", -1
    wall = time.time() - t0
    (run_dir / "events.jsonl").write_text(stdout)
    events = [json.loads(l) for l in stdout.splitlines() if l.strip().startswith("{")]
    usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
    steps, tools = 0, {}
    for e in events:
        if e.get("type") == "status" and e.get("phase") == "step_end" and "usage" in e:
            steps += 1
            for k in usage:
                usage[k] += e["usage"].get(k, 0)
        if e.get("type") == "tool_call":
            tools[e["tool"]] = tools.get(e["tool"], 0) + 1
    diff = sh(["git", "-C", str(work), "diff", DATASET[iid]["base_commit"]]).stdout
    (run_dir / "patch.diff").write_text(diff)
    ev = sh([str(HPY), str(H / "evaluate.py"), iid, str(work)], timeout=1800)
    try:
        grade = json.loads(ev.stdout.strip().splitlines()[-1])
    except Exception:
        grade = {"resolved": False, "errors": [ev.stderr[-300:]]}
    row = dict(instance=iid, repo=repo, arm=arm, resolved=bool(grade.get("resolved")),
               f2p=f"{grade.get('f2p_passed')}/{grade.get('f2p_total')}", p2p=f"{grade.get('p2p_passed')}/{grade.get('p2p_total')}",
               patch_lines=diff.count("\n"), exit=code, wall_s=round(wall, 1), steps=steps, tools=tools, **usage,
               stderr_tail=stderr[-300:] if code else "")
    with LOCK, open(out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: row[k] for k in ("instance", "arm", "resolved", "f2p", "steps", "inputTokens", "cacheReadTokens", "wall_s")}), flush=True)
    shutil.rmtree(work, ignore_errors=True)


def main():
    out, arms = sys.argv[1], sys.argv[2].split(",")
    rest = sys.argv[3:]
    workers = 4
    if rest[:1] == ["--workers"]:
        workers, rest = int(rest[1]), rest[2:]
    wanted = set(rest) if rest else None
    groups = {}
    for iid, repo, version in INSTANCES:
        if wanted is None or iid in wanted:
            groups.setdefault((repo, version), []).extend((iid, repo, arm) for arm in arms)

    def work(jobs):
        for job in jobs:
            try:
                one(out, *job)
            except Exception as e:  # keep the group going; record the failure
                with LOCK, open(out, "a") as f:
                    f.write(json.dumps(dict(instance=job[0], repo=job[1], arm=job[2], resolved=False, error=str(e)[:300])) + "\n")
                print(f"ERROR {job}: {e}", flush=True)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(work, sorted(groups.values(), key=len, reverse=True)))


if __name__ == "__main__":
    main()
