"""A/B on a large repo: seeded bugs in a DeepSeek Harness worktree (~44k chunks).

Arm A: stock harness. Arm B: tool-code-search + edit-syntax-guard +
observation-mask. One shared worktree, reset between runs. Success = the
seeded package's vitest suite passes and no test file was edited.
Usage: python3 bench2.py <out.jsonl> <seed> [task ids...]   (runs A then B per task)
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

B = Path(__file__).resolve().parent
S = B.parent
WT = B / "deepseek-harness"
SCM = Path("/Users/tazo/Desktop/code mcp/senior-code-mcp")

TASKS = {
    "h1-reminder-reset": dict(
        file="packages/guard/repeat-tool-reminder/src/index.ts",
        old="    if (messages.some(message => message.source.kind === 'user')) chains.delete(agent)\n",
        new="",
        pkg="packages/guard/repeat-tool-reminder",
        issue="The repeat-tool reminder keeps counting identical tool calls across a new user message. "
              "A user interjection changes the context, so it should reset the repetition count for that agent.",
    ),
    "h3-brace-include": dict(
        file="packages/fs/tool-fs-search/src/grep.ts",
        old="    if (char === '{') braceDepth++\n    else if (char === '}')",
        new="    if (char === '}')",
        pkg="packages/fs/tool-fs-search",
        issue="The grep tool rejects an include glob with brace alternation such as `*.{ts,tsx}`, reporting that include "
              "must be one glob and not a comma-separated list.",
    ),
    "h4-read-footer": dict(
        file="packages/fs/tool-fs/src/read-render.ts",
        old="    footer = `(Showing lines ${outcome.offset}-${endLine} of ${outcome.totalLines}. Use offset=${endLine + 1} to continue.)`",
        new="    footer = `(Showing lines ${outcome.offset}-${endLine} of ${outcome.totalLines}. Use offset=${endLine} to continue.)`",
        pkg="packages/fs/tool-fs",
        issue="When the read tool shows only part of a file, its footer tells the model to continue at the last line it "
              "already showed instead of the next line, so the follow-up read repeats a line.",
    ),
}

PROMPT = (
    "Fix this issue in the repository in the current directory (a large TypeScript monorepo).\n\nIssue: {issue}\n\n"
    "Make the smallest correct change in the source code. Do not edit tests. "
    "Check your fix with: npx vitest run {pkg}"
)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def reset():
    sh(["git", "-C", str(WT), "checkout", "-q", "--", "."], check=True)
    sh(["git", "-C", str(WT), "clean", "-fdq", "packages", "docs", "scripts"], check=True)


def vitest(pkg):
    return sh(f"npx vitest run {pkg}", cwd=WT, timeout=600)


def one(out, tid, arm, seed):
    t = TASKS[tid]
    reset()
    path = WT / t["file"]
    src = path.read_text()
    assert src.count(t["old"]) == 1, tid
    path.write_text(src.replace(t["old"], t["new"]))
    if vitest(t["pkg"]).returncode == 0:
        raise SystemExit(f"{tid}: seeded bug does not fail tests")

    env = {k: v for k, v in os.environ.items() if not k.startswith("DSH_")}
    env["DSH_HOME"] = str(B / f"dsh_home_{arm}")
    if arm == "B":
        env |= {"QDRANT_COLLECTION": "scm_dsh", "GRAPH_PATH": str(B / "graph_dsh.json"),
                "DSH_CODE_SEARCH_COMMAND": str(SCM / ".venv/bin/senior-code-search"),
                "DSH_EDIT_SYNTAX_GUARD": "1", "DSH_OBSERVATION_MASK": "1"}
    t0 = time.time()
    try:
        proc = sh([str(S / "withkey.sh"), str(S / "dsh.sh"), "--profile", "headless", "--json",
                   PROMPT.format(issue=t["issue"], pkg=t["pkg"])], cwd=WT, env=env, timeout=1500)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout, stderr, code = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout", -1
    wall = time.time() - t0
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
    denials = sum(1 for e in events if e.get("type") == "tool_result" and "Edit rejected" in str(e.get("result", "")))
    masked = sum(1 for e in events if e.get("type") == "tool_result" and "Older tool output elided" in str(e.get("result", "")))
    tests_touched = sh(["git", "-C", str(WT), "diff", "--name-only", "--", "*.spec.ts", "*/tests/*"]).stdout.strip()
    post = vitest(t["pkg"])
    diff_files = sh(["git", "-C", str(WT), "diff", "--name-only"]).stdout.split()
    row = dict(task=tid, arm=arm, seed=seed, passed=post.returncode == 0 and not tests_touched, tests_touched=bool(tests_touched),
               fixed_file=t["file"] in diff_files, files_changed=len(diff_files), exit=code, wall_s=round(wall, 1), steps=steps,
               tools=tools, syntax_denials=denials, masked_seen=masked, **usage,
               final=next((e.get("text", "")[:200] for e in events if e.get("type") == "final"), None),
               stderr_tail=stderr[-300:] if code else "")
    with open(out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: row[k] for k in ("task", "arm", "seed", "passed", "steps", "inputTokens", "cacheReadTokens", "outputTokens", "wall_s", "tools")}), flush=True)


def main():
    out, seed = sys.argv[1], sys.argv[2]
    for tid in sys.argv[3:] or list(TASKS):
        for arm in ("A", "B"):
            one(out, tid, arm, seed)
    reset()


if __name__ == "__main__":
    main()
