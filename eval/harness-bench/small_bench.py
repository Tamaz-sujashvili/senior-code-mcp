"""Mini A/B benchmark: seeded bugs in senior-code-mcp, fixed by dsh headless.

Arm A: stock harness. Arm B: tool-code-search + edit-syntax-guard +
observation-mask. Success = the full pytest suite passes after the run.
Usage: python3 bench.py <out.jsonl> <arm> <seed> [task ids...]
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

S = Path(__file__).resolve().parent.parent  # scratchpad
SCM = Path("/Users/tazo/Desktop/code mcp/senior-code-mcp")
PY = SCM / ".venv/bin/python"
WS = S / "ab/bench_ws"
TEST_CMD = f"PYTHONPATH=src '{PY}' -m pytest -q -p no:cacheprovider"

TASKS = {
    "t1-receiver": dict(
        file="src/senior_code_mcp/store/graph.py",
        old='''    if receiver:
        owned = [i for i in ids if _owner(g.nodes[i].get("qualified")) == receiver]
        if owned:
            return _pick(g, owned, call.path)
    return _pick(g, ids, call.path)''',
        new='''    return _pick(g, ids, call.path)''',
        issue="In the call graph, a call like `B.save()` made from a method of class `A` (in the file that defines `A.save`) "
              "gets an edge to `A.save` instead of `B.save`. An explicit class receiver (and `this.`/`self.`) should decide the target "
              "before the same-file preference.",
    ),
    "t2-go-receiver": dict(
        file="src/senior_code_mcp/ingest/treesitter.py",
        old='''        return "method", name, f"{receiver}.{name}" if receiver else name''',
        new='''        return "method", name, name''',
        issue="Go methods are indexed without their receiver type: `func (r *MemoryRepo) Find(...)` shows up with qualified name "
              "`Find` instead of `MemoryRepo.Find`, so graph lookups by qualified name fail.",
    ),
    "t3-py-size": dict(
        file="src/senior_code_mcp/ingest/parser.py",
        old='''    if spec.grammar is None:  # Python
        return not f.is_file()
    try:''',
        new='''    try:''',
        issue="Since multi-language support landed, valid Python files larger than 1 MB are silently skipped by `parse_repo`. "
              "Before, every Python file was indexed regardless of size; restore that for Python only.",
    ),
    "t4-cli-json": dict(
        file="src/senior_code_mcp/search_cli.py",
        old="""    except SystemExit as e:  # --help exits 0 after printing usage
        if e.code in (0, None):
            raise
        print(json.dumps({"error": f"senior-code-search exited with {e.code}"}))
        sys.exit(2)
""",
        new="""    except SystemExit:
        raise
""",
        issue=" with an invalid argument such as  prints argparse usage to stderr "
              "and nothing on stdout. The documented contract is: any failure, including invalid arguments, prints a JSON "
              " on stdout and exits 2.",
    ),
    "t5-repo-filter": dict(
        file="src/senior_code_mcp/store/vectors.py",
        old='''    must = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in fields.items() if v]''',
        new='''    must = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in list(fields.items())[:1] if v]''',
        issue="Vector search ignores the `repo` filter: `senior-code-search context --repo app ...` still returns hits from other "
              "ingested repos. Only the language filter is applied.",
    ),
}

PROMPT = (
    "Fix this issue in the repository in the current directory.\n\nIssue: {issue}\n\n"
    "Make the smallest correct change in the source code. Do not edit the tests. "
    "Check your fix with this command: {test}"
)


def run(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def main():
    out, arm, seed = sys.argv[1], sys.argv[2], sys.argv[3]
    ids = sys.argv[4:] or list(TASKS)
    for tid in ids:
        t = TASKS[tid]
        name = f"{tid}-{arm}-{seed}"
        ws = WS / name
        shutil.rmtree(ws, ignore_errors=True)
        ws.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", str(SCM), str(ws)], check=True)
        src = (ws / t["file"]).read_text()
        assert src.count(t["old"]) == 1, tid
        (ws / t["file"]).write_text(src.replace(t["old"], t["new"]))
        run(["git", "-C", str(ws), "-c", "user.name=b", "-c", "user.email=b@b", "commit", "-qam", "seed bug"], check=True)
        pre = run(TEST_CMD, cwd=ws)
        assert pre.returncode != 0, f"{tid}: seeded bug does not fail tests"

        env = {k: v for k, v in os.environ.items() if not k.startswith("DSH_")}
        env["DSH_HOME"] = str(S / f"ab/dsh_home_{arm}")
        if arm == "B":
            graph = ws.parent / f"{name}.graph.json"
            env |= {"QDRANT_COLLECTION": "scm_bench", "GRAPH_PATH": str(graph),
                    "DSH_CODE_SEARCH_COMMAND": str(SCM / ".venv/bin/senior-code-search"),
                    "DSH_EDIT_SYNTAX_GUARD": "1", "DSH_OBSERVATION_MASK": "1"}
            ing = run([str(SCM / ".venv/bin/senior-code-ingest"), str(ws)], env=env)
            assert ing.returncode == 0, ing.stderr[-500:]
        prompt = PROMPT.format(issue=t["issue"], test=TEST_CMD)
        t0 = time.time()
        proc = run([str(S / "withkey.sh"), str(S / "dsh.sh"), "--profile", "headless", "--json", prompt],
                   cwd=ws, env=env, timeout=1200)
        wall = time.time() - t0
        events = [json.loads(l) for l in proc.stdout.splitlines() if l.strip().startswith("{")]
        usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
        steps = 0
        tools = {}
        for e in events:
            if e.get("type") == "status" and e.get("phase") == "step_end" and "usage" in e:
                steps += 1
                for k in usage:
                    usage[k] += e["usage"].get(k, 0)
            if e.get("type") == "tool_call":
                tools[e["tool"]] = tools.get(e["tool"], 0) + 1
        denials = sum(1 for e in events if e.get("type") == "tool_result" and "Edit rejected" in str(e.get("result", "")))
        post = run(TEST_CMD, cwd=ws)
        tests_touched = run(["git", "-C", str(ws), "diff", "--name-only", "HEAD", "--", "tests"]).stdout.strip()
        row = dict(task=tid, arm=arm, seed=seed, passed=post.returncode == 0 and not tests_touched, tests_touched=bool(tests_touched),
                   exit=proc.returncode, wall_s=round(wall, 1), steps=steps, tools=tools, syntax_denials=denials, **usage,
                   final=next((e.get("text", "")[:200] for e in events if e.get("type") == "final"), None),
                   stderr_tail=proc.stderr[-300:] if proc.returncode else "")
        with open(out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps({k: row[k] for k in ("task", "arm", "seed", "passed", "steps", "inputTokens", "cacheReadTokens", "wall_s", "tools")}), flush=True)


if __name__ == "__main__":
    main()
