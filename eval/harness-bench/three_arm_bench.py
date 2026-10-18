"""Three-arm benchmark: raw DeepSeek Harness vs fork (pi ports) vs fork (AHE ports).

R = upstream 687f282e, nothing enabled.
P = feat/pi-ports: code search + syntax guard + observation mask + edit-near-miss + compaction-file-tracking.
H = feat/ahe-ports: code search + syntax guard + observation mask + execution-risk-hints + verify-before-finish.

Usage: python3 bench3.py small|large <out.jsonl> <seed> <arm>[,<arm>...] [task ids...]
small = seeded bugs in senior-code-mcp (fresh clone per run, pytest).
large = seeded bugs in a DeepSeek Harness worktree (~44k chunks, shared, reset between runs, vitest).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE.parent
SCM = Path("/Users/tazo/Desktop/code mcp/senior-code-mcp")
PY = SCM / ".venv/bin/python"

HARNESS = {
    "R": HERE / "raw",
    "P": Path("/Users/tazo/Desktop/code mcp/deepseek-harness"),
    "H": Path("/private/tmp/claude-501/-Users-tazo-Desktop-code-mcp/732d79da-a63b-441c-84e4-d379d8ed30e4/scratchpad/dsh-ahe"),
}
COMMON = {"DSH_CODE_SEARCH_COMMAND": str(SCM / ".venv/bin/senior-code-search"), "DSH_EDIT_SYNTAX_GUARD": "1", "DSH_OBSERVATION_MASK": "1", "DSH_SEARCH_ROUTING": os.environ.get("BENCH_ROUTING", "1")}
FLAGS = {
    "R": {},
    "P": COMMON | {"DSH_EDIT_NEAR_MISS": "1", "DSH_COMPACTION_FILE_TRACKING": "1"},
    "H": COMMON | {"DSH_EXECUTION_RISK_HINTS": "1", "DSH_VERIFY_BEFORE_FINISH": "1"},
}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SMALL = load(S / "ab/bench.py", "bench_small")
LARGE = load(S / "bench2/bench2.py", "bench_large")
SMALL_TASKS = {k: v for k, v in SMALL.TASKS.items() if k != "t4-cli-json"}  # t4 seed does not fail tests
LARGE_TASKS = LARGE.TASKS


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def dsh_cmd(arm):
    h = HARNESS[arm]
    return ["node", "--import", f"file://{h}/node_modules/tsx/dist/esm/index.mjs", f"{h}/apps/cli/src/bin.ts"]


def run_agent(arm, cwd, prompt, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DSH_")}
    env |= {"DSH_HOME": str(HERE / f"dsh_home_{arm}"), "TSX_TSCONFIG_PATH": str(HARNESS[arm] / "tsconfig.json")}
    env |= FLAGS[arm] | (env_extra if FLAGS[arm] else {})
    t0 = time.time()
    try:
        proc = sh([str(S / "withkey.sh"), *dsh_cmd(arm), "--profile", "headless", "--json", prompt], cwd=cwd, env=env, timeout=1500)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err, code = "timeout", -1
    return out, err, code, time.time() - t0


def summarize(stdout):
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
    results = " ".join(str(e.get("result", "")) for e in events if e.get("type") == "tool_result")
    signals = {
        "syntax_denials": results.count("Edit rejected"),
        "masked": results.count("Older tool output elided"),
    }
    return steps, tools, usage, signals, next((e.get("text", "")[:160] for e in events if e.get("type") == "final"), None)


def small_one(out, tid, arm, seed):
    t = SMALL_TASKS[tid]
    name = f"{tid}-{arm}-{seed}"
    ws = HERE / "small_ws" / name
    shutil.rmtree(ws, ignore_errors=True)
    ws.parent.mkdir(parents=True, exist_ok=True)
    sh(["git", "clone", "-q", str(SCM), str(ws)], check=True)
    src = (ws / t["file"]).read_text()
    assert src.count(t["old"]) == 1, tid
    (ws / t["file"]).write_text(src.replace(t["old"], t["new"]))
    sh(["git", "-C", str(ws), "-c", "user.name=b", "-c", "user.email=b@b", "commit", "-qam", "seed bug"], check=True)
    assert sh(SMALL.TEST_CMD, cwd=ws).returncode != 0, f"{tid}: seed does not fail"
    extra = {}
    if FLAGS[arm]:
        extra = {"QDRANT_COLLECTION": "scm_bench3", "GRAPH_PATH": str(ws.parent / f"{name}.graph.json")}
        env = os.environ | extra
        assert sh([str(SCM / ".venv/bin/senior-code-ingest"), str(ws)], env=env).returncode == 0
    prompt = SMALL.PROMPT.format(issue=t["issue"], test=SMALL.TEST_CMD)
    stdout, err, code, wall = run_agent(arm, ws, prompt, extra)
    touched = sh(["git", "-C", str(ws), "diff", "--name-only", "HEAD", "--", "tests"]).stdout.strip()
    passed = sh(SMALL.TEST_CMD, cwd=ws).returncode == 0 and not touched
    record(out, "small", tid, arm, seed, passed, code, wall, stdout, err)


def large_one(out, tid, arm, seed):
    t = LARGE_TASKS[tid]
    LARGE.reset()
    path = LARGE.WT / t["file"]
    src = path.read_text()
    assert src.count(t["old"]) == 1, tid
    path.write_text(src.replace(t["old"], t["new"]))
    assert LARGE.vitest(t["pkg"]).returncode != 0, f"{tid}: seed does not fail"
    extra = {"QDRANT_COLLECTION": "scm_dsh", "GRAPH_PATH": str(S / "bench2/graph_dsh.json")}
    stdout, err, code, wall = run_agent(arm, LARGE.WT, LARGE.PROMPT.format(issue=t["issue"], pkg=t["pkg"]), extra)
    touched = sh(["git", "-C", str(LARGE.WT), "diff", "--name-only", "--", "*.spec.ts", "*/tests/*"]).stdout.strip()
    passed = LARGE.vitest(t["pkg"]).returncode == 0 and not touched
    record(out, "large", tid, arm, seed, passed, code, wall, stdout, err)
    LARGE.reset()


def record(out, suite, tid, arm, seed, passed, code, wall, stdout, err):
    steps, tools, usage, signals, final = summarize(stdout)
    row = dict(suite=suite, task=tid, arm=arm, seed=seed, passed=passed, exit=code, wall_s=round(wall, 1), steps=steps,
               tools=tools, **usage, **signals, final=final, stderr_tail=err[-300:] if code else "")
    with open(out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: row[k] for k in ("suite", "task", "arm", "seed", "passed", "steps", "inputTokens", "cacheReadTokens", "wall_s")}), flush=True)


def main():
    suite, out, seed, arms = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(",")
    tasks = sys.argv[5:] or list(SMALL_TASKS if suite == "small" else LARGE_TASKS)
    for tid in tasks:
        for arm in arms:
            (small_one if suite == "small" else large_one)(out, tid, arm, seed)


if __name__ == "__main__":
    main()
