"""Hard SWE-bench Verified instances, harness arms, official Docker grading (runs on the VPS).

Usage: python3 run_hard.py <out.jsonl> <arm>[,<arm>...] <seed>[,<seed>...] [--workers N] [instance ids...]
Arms: R = raw upstream harness; P = feat/pi-ports; H = feat/ahe-ports (see FLAGS).
Default instances: hard_ids.json (difficulty '1-4 hours' or '>4 hours', 45 instances).

Per instance: pull the prebuilt swebench image, copy its /testbed to a template, commit a baseline
in the template so build artefacts stay out of patches. Per (arm, seed): copy the template, run
`dsh --profile headless --json` in it on the host; tests run in the image through `envrun`.
Patch = `git add -A && git diff --cached <baseline>`, graded by swebench.harness.run_evaluation.
Resume: rows already in out.jsonl are skipped, except rows with `api_error` or `error`.
Model: every arm gets `--patch $BENCH_PATCH` (default openrouter-v4pro.patch.yml: provider openrouter,
model deepseek/deepseek-v4-pro, key from OPENROUTER_API_KEY in this process's environment).
Credit guard: before each job, OpenRouter credits are read; below BENCH_MIN_CREDIT (USD, default 0.60)
no new job starts. The agent environment keeps this process's variables except DSH_*; DSH_* names
listed in BENCH_PASS_ENV (comma-separated) are kept too. Run with Python >= 3.14 (compression.zstd).
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from compression import zstd
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

B = Path(os.environ.get("BENCH_ROOT", "/root/dshbench"))
SWEPY = B / "swe-venv/bin/python"
SCM = B / "scm"
HARNESS = {a: B / "arms" / a for a in "RPH"}
COMMON = {"DSH_CODE_SEARCH_COMMAND": str(SCM / ".venv/bin/senior-code-search"), "DSH_EDIT_SYNTAX_GUARD": "1",
          "DSH_OBSERVATION_MASK": "1", "DSH_SEARCH_ROUTING": "1", "DSH_STEP_BUDGET": "100"}
FLAGS = {
    "R": {},
    "P": COMMON | {"DSH_EDIT_NEAR_MISS": "1", "DSH_COMPACTION_FILE_TRACKING": "1"},
    "H": COMMON | {"DSH_EXECUTION_RISK_HINTS": "1", "DSH_VERIFY_BEFORE_FINISH": "1"},
}
AGENT_TIMEOUT = int(os.environ.get("BENCH_AGENT_TIMEOUT", "3600"))
EVAL_TIMEOUT = 1800
ABORT_AFTER_API_ERRORS = 3
PATCH = Path(os.environ.get("BENCH_PATCH", B / "openrouter-v4pro.patch.yml"))
MIN_CREDIT = float(os.environ.get("BENCH_MIN_CREDIT", "0.60"))

PROMPT = """You are fixing a GitHub issue in the repository in the current directory ({repo}).

<issue>
{problem}
</issue>

Change the non-test source files so that the issue is resolved. Make the smallest correct change. Do not add or edit tests.
The project's environment (its Python and dependencies) is not installed on this machine. Run tests and any other project command through:
  {envrun} <command>
For example: {envrun} {example}
(`envrun` runs the command in a container that mounts this directory as /testbed; plain `python`/`pytest` here do not have the dependencies.)"""

EXAMPLES = {
    "django/django": "./tests/runtests.py --settings=test_sqlite --parallel 1 <test labels, e.g. auth_tests.test_basic>",
    "sympy/sympy": "bin/test -C <test files>",
}
EXAMPLE_DEFAULT = "python -m pytest -rA <test files or test ids>"

ENVRUN = """#!/bin/bash
# Run a command in this task's SWE-bench environment; the working copy is mounted at /testbed.
# One argument is run as a shell command line; several arguments are run as one command.
exec docker run --rm -i --network none --memory 6g --cpus 2 -v {testbed}:/testbed -w /testbed {image} \\
  bash -lc 'source /opt/miniconda3/bin/activate && conda activate testbed && export LANG=C.UTF-8 LC_ALL=C.UTF-8
if [ $# -eq 1 ]; then timeout 900 bash -c "$1"; else timeout 900 "$@"; fi' envrun "$@"
"""

API_ERROR = re.compile(r"insufficient balance|insufficient credits|more credits|available credits|in-flight requests|payment required|insufficient_quota|\bquota\b"
                       r"|authentication|api[ _-]?key|rate[ _-]?limit"
                       r"|overloaded|status(?: code)?:? ?(?:4(?:0[0-9]|29)|5\d\d)\b|\bHTTP (?:4\d\d|5\d\d)\b", re.I)

BUDGET_SUMMARY = re.compile(rb'"summary"\s*:\s*"step budget: (\d+)/\d+"')  # each notice is logged more than once

DATASET = {json.loads(l)["instance_id"]: json.loads(l) for l in open(B / "dataset.jsonl")}
LOCK = threading.Lock()
STATE = {"api_errors": 0, "abort": False, "why": ""}


def credit():
    """Remaining OpenRouter credit in USD, or None when it cannot be read."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    req = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)["data"]
        return round(float(d["total_credits"]) - float(d["total_usage"]), 4)
    except Exception:
        return None


def scrub(text):
    """Remove the API key from text that is written to disk (the agent's shell inherits it)."""
    key = os.environ.get("OPENROUTER_API_KEY")
    return text.replace(key, "[REDACTED]") if key and key in text else text


def budget_notices(arm, session_id):
    """Count step-budget notices in the session log (headless --json events do not carry them),
    then delete the session log, because the agent's shell output in it can hold the API key."""
    if not session_id:
        return None
    seen = set()
    for d in (B / f"dsh_home_{arm}" / "sessions").glob(f"*/{session_id}"):  # sessions/<cwd slug>/<session id>/
        for f in d.iterdir():
            if f.name.endswith((".zstd", ".zst")):
                seen |= set(BUDGET_SUMMARY.findall(zstd.decompress(f.read_bytes())))
            elif f.name.endswith(".jsonl"):
                seen |= set(BUDGET_SUMMARY.findall(f.read_bytes()))
        shutil.rmtree(d, ignore_errors=True)
    return len(seen)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def short(repo):
    return repo.split("/")[1]


def image_of(iid):
    return f"swebench/sweb.eval.x86_64.{iid.lower()}:latest".replace("__", "_1776_")


class Instance:
    """Image and template for one instance; the last job to release it removes both."""

    def __init__(self, iid, jobs):
        self.iid, self.jobs, self.lock, self.ready = iid, jobs, threading.Lock(), False
        self.tpl = B / "templates" / iid
        self.image = image_of(iid)

    def acquire(self):
        with self.lock:
            if self.ready:
                return
            if sh(["docker", "image", "inspect", self.image]).returncode != 0:
                p = sh(["docker", "pull", "-q", self.image], timeout=1800)
                if p.returncode != 0:
                    raise RuntimeError(f"pull {self.image}: {p.stderr[-300:]}")
            shutil.rmtree(self.tpl, ignore_errors=True)
            self.tpl.mkdir(parents=True)
            name = f"tpl_{self.iid.lower()}_{os.getpid()}"
            sh(["docker", "rm", "-f", name])
            sh(["docker", "create", "--name", name, self.image], check=True)
            try:
                sh(["docker", "cp", f"{name}:/testbed", str(self.tpl / "testbed")], check=True, timeout=1800)
            finally:
                sh(["docker", "rm", "-f", name])
            g = ["git", "-C", str(self.tpl / "testbed"), "-c", "user.name=bench", "-c", "user.email=bench@localhost"]
            sh(g + ["add", "-A"], check=True)
            sh(g + ["commit", "-q", "--allow-empty", "--no-verify", "-m", "bench baseline"], check=True)
            self.baseline = sh(g + ["rev-parse", "HEAD"], check=True).stdout.strip()
            self.ready = True

    def release(self):
        with self.lock:
            self.jobs -= 1
            if self.jobs == 0:
                shutil.rmtree(self.tpl, ignore_errors=True)
                sh(["docker", "rmi", self.image])


def agent_env(arm, repo):
    keep = {n.strip() for n in os.environ.get("BENCH_PASS_ENV", "").split(",") if n.strip()}
    env = {k: v for k, v in os.environ.items() if not k.startswith("DSH_") or k in keep}
    env |= {"DSH_HOME": str(B / f"dsh_home_{arm}"), "TSX_TSCONFIG_PATH": str(HARNESS[arm] / "tsconfig.json")}
    env |= FLAGS[arm]
    if FLAGS[arm]:
        env |= {"QDRANT_COLLECTION": os.environ.get("BENCH_QDRANT_COLLECTION", "scm_hard"),
                "GRAPH_PATH": str(B / "index" / f"{short(repo)}.graph.json")}
    return env


def run_agent(arm, work, prompt, env):
    h = HARNESS[arm]
    cmd = ["node", "--import", f"file://{h}/node_modules/tsx/dist/esm/index.mjs", f"{h}/apps/cli/src/bin.ts",
           "--patch", str(PATCH), "--profile", "headless", "--json", prompt]
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=AGENT_TIMEOUT)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        err, code = (err or "") + "\ntimeout", -1
    return out or "", err or "", code, time.time() - t0


def summarize(stdout):
    events = []
    for line in stdout.splitlines():
        if line.strip().startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
    steps, tools = 0, {}
    for e in events:
        if e.get("type") == "status" and e.get("phase") == "step_end" and "usage" in e:
            steps += 1
            for k in usage:
                usage[k] += e["usage"].get(k, 0)
        if e.get("type") == "tool_call":
            tools[e["tool"]] = tools.get(e["tool"], 0) + 1
    said = [str(e.get("result", "")) if e.get("type") == "tool_result" else str(e.get("text", ""))
            for e in events if e.get("type") in ("tool_result", "text")]
    signals = {
        "budget_text": sum(s.count("Step budget") for s in said),
        "syntax_denials": sum(s.count("Edit rejected") for s in said),
        "routed": sum(s.count("Shell search was not run") for s in said),
    }
    other = " ".join(json.dumps(e) for e in events if e.get("type") not in ("tool_result", "tool_call", "text", "thinking"))
    session = next((e.get("sessionId") for e in events if e.get("type") == "session"), None)
    return session, steps, tools, usage, signals, other


def grade(iid, arm, seed, patch):
    run_id = f"{arm}_s{seed}_{iid}"
    model = f"{arm}-s{seed}"
    ev = B / "eval"
    ev.mkdir(exist_ok=True)
    preds = ev / f"preds_{run_id}.jsonl"
    preds.write_text(json.dumps({"instance_id": iid, "model_name_or_path": model, "model_patch": patch}) + "\n")
    t0 = time.time()
    p = sh([str(SWEPY), "-m", "swebench.harness.run_evaluation", "--dataset_name", str(B / "dataset.jsonl"),
            "--predictions_path", str(preds), "--instance_ids", iid, "--run_id", run_id, "--max_workers", "1",
            "--cache_level", "instance", "--namespace", "swebench", "--timeout", str(EVAL_TIMEOUT)],
           cwd=ev, timeout=EVAL_TIMEOUT + 900)
    report = ev / "logs/run_evaluation" / run_id / model / iid / "report.json"
    if not report.exists():
        return {"resolved": False, "graded": False, "eval_error": (p.stdout + p.stderr)[-300:], "eval_s": round(time.time() - t0, 1)}
    r = json.loads(report.read_text())[iid]
    ts = r.get("tests_status", {})
    f2p, p2p = ts.get("FAIL_TO_PASS", {}), ts.get("PASS_TO_PASS", {})
    return {"resolved": bool(r.get("resolved")), "graded": True, "applied": r.get("patch_successfully_applied"),
            "f2p": f"{len(f2p.get('success', []))}/{len(f2p.get('success', [])) + len(f2p.get('failure', []))}",
            "p2p": f"{len(p2p.get('success', []))}/{len(p2p.get('success', [])) + len(p2p.get('failure', []))}",
            "eval_s": round(time.time() - t0, 1)}


def one(out, inst, iid, arm, seed):
    repo = DATASET[iid]["repo"]
    base = B / "runs" / arm / str(seed) / iid
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    work = base / short(repo)  # basename = ingested repo name for the code-search repo filter
    inst.acquire()
    sh(["cp", "-a", str(inst.tpl / "testbed"), str(work)], check=True)
    envrun = base / "envrun"
    envrun.write_text(ENVRUN.format(testbed=work, image=inst.image))
    envrun.chmod(0o755)
    prompt = PROMPT.format(repo=repo, problem=DATASET[iid]["problem_statement"], envrun=envrun,
                           example=EXAMPLES.get(repo, EXAMPLE_DEFAULT))
    credit_before = credit()
    if credit_before is not None and credit_before < MIN_CREDIT:
        STATE["abort"], STATE["why"] = True, f"OpenRouter credit {credit_before} < {MIN_CREDIT}"
        print(f"STOP: OpenRouter credit {credit_before} < {MIN_CREDIT}; no new jobs", flush=True)
        shutil.rmtree(base, ignore_errors=True)
        return
    stdout, stderr, code, wall = run_agent(arm, work, prompt, agent_env(arm, repo))
    stdout, stderr = scrub(stdout), scrub(stderr)
    (base / "events.jsonl").write_text(stdout)
    (base / "stderr.txt").write_text(stderr[-20000:])
    session, steps, tools, usage, signals, other = summarize(stdout)
    signals["budget_notices"] = budget_notices(arm, session)
    g = ["git", "-C", str(work)]
    sh(g + ["add", "-A"])
    patch = scrub(sh(g + ["diff", "--cached", inst.baseline]).stdout)
    (base / "patch.diff").write_text(patch)
    row = dict(instance=iid, repo=repo, arm=arm, seed=seed, harness=sh(["git", "-C", str(HARNESS[arm]), "rev-parse", "--short", "HEAD"]).stdout.strip(),
               exit=code, wall_s=round(wall, 1), steps=steps, tools=tools, **usage, **signals, patch_lines=patch.count("\n"),
               patch_file=PATCH.name, session=session, credit_before=credit_before)
    m = API_ERROR.search(stderr[-5000:] + " " + other[-5000:]) if (code != 0 or steps == 0) else None
    if m:
        line = next((l for l in (stderr + "\n" + other).splitlines() if m.group(0) in l), m.group(0))
        row |= {"resolved": None, "api_error": line.strip()[:300]}
    elif not patch.strip():
        row |= {"resolved": False, "graded": False, "empty_patch": True}
    else:
        row |= grade(iid, arm, seed, patch)
    row["stderr_tail"] = stderr[-300:] if code else ""
    row["credit_after"] = credit()
    with LOCK:
        with open(out, "a") as f:
            f.write(json.dumps(row) + "\n")
        STATE["api_errors"] = STATE["api_errors"] + 1 if row.get("api_error") else 0
        if STATE["api_errors"] >= ABORT_AFTER_API_ERRORS:
            STATE["abort"], STATE["why"] = True, f"{ABORT_AFTER_API_ERRORS} consecutive API errors"
    print(json.dumps({k: row.get(k) for k in ("instance", "arm", "seed", "resolved", "f2p", "steps", "inputTokens",
                                               "cacheReadTokens", "wall_s", "api_error")}), flush=True)
    shutil.rmtree(work, ignore_errors=True)


def main():
    out, arms, seeds = sys.argv[1], sys.argv[2].split(","), sys.argv[3].split(",")
    rest = sys.argv[4:]
    workers = 4
    if rest[:1] == ["--workers"]:
        workers, rest = int(rest[1]), rest[2:]
    ids = rest or json.load(open(B / "hard_ids.json"))
    done = set()
    if Path(out).exists():
        for l in open(out):
            r = json.loads(l)
            if not r.get("api_error") and not r.get("error"):
                done.add((r["instance"], r["arm"], str(r["seed"])))
    jobs, insts = [], {}
    for iid in ids:  # instance-major order keeps few images on disk at a time
        todo = [(iid, a, s) for a in arms for s in seeds if (iid, a, s) not in done]
        if todo:
            insts[iid] = Instance(iid, len(todo))
            jobs += todo
    print(f"{len(jobs)} jobs, {len(insts)} instances, {len(done)} rows done", flush=True)

    def work(job):
        iid, arm, seed = job
        inst = insts[iid]
        try:
            if STATE["abort"]:
                return
            one(out, inst, iid, arm, seed)
        except Exception as e:  # record infrastructure failures; resume re-runs them
            with LOCK, open(out, "a") as f:
                f.write(json.dumps(dict(instance=iid, repo=DATASET[iid]["repo"], arm=arm, seed=seed, resolved=None, error=str(e)[:300])) + "\n")
            print(f"ERROR {job}: {e}", flush=True)
        finally:
            inst.release()

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(work, jobs))
    if STATE["abort"]:
        print(f"STOPPED early: {STATE['why']}; resume runs the remaining jobs and re-runs rows with api_error", flush=True)


if __name__ == "__main__":
    main()
