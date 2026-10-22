# Hard SWE-bench Verified runs on the VPS

These scripts run DeepSeek Harness arms on the hard instances of SWE-bench Verified. The official SWE-bench Docker harness grades each patch. The runs use the VPS (`ssh brainvps`) because each instance image is approximately 4.5 GB.

## Instances and arms

The default set has the 45 instances that have the difficulty "1-4 hours" or ">4 hours" (`hard_ids.json`).

| Arm | Harness | Plugins |
| --- | --- | --- |
| R | upstream commit `687f282e` | none |
| P | branch `feat/pi-ports` | code search, syntax guard, observation mask, search routing, step budget 100, edit near-miss, compaction file tracking |
| H | branch `feat/ahe-ports` | code search, syntax guard, observation mask, search routing, step budget 100, execution risk hints, verify before finish |

## Layout on the VPS

All files are in `/root/dshbench`:

- `arms/R`, `arms/P`, `arms/H`: harness checkouts. `arms/R` is a clone. `arms/P` and `arms/H` are worktrees of that clone.
- `scm`: senior-code-mcp with its virtual environment.
- `dshbench-qdrant` and `dshbench-ollama`: Docker containers for the code index. They listen on 127.0.0.1 only.
- `index/<repo>.graph.json`: call graphs. The vectors are in the Qdrant collection `scm_hard`.
- `swe-venv`: Python 3.12 with `swebench==4.1.0`.
- `dataset.jsonl`, `hard_ids.json`: SWE-bench Verified rows and the hard instance IDs.
- `runs/<arm>/<seed>/<instance>/`: the agent events, the patch and the stderr of each run.
- `eval/logs/run_evaluation/`: the logs and reports of the official grader.
- `logs/`: build and run logs.

## Procedure for one run

For each instance, `run_hard.py` does these steps:

1. It pulls the prebuilt image `swebench/sweb.eval.x86_64.<id>` and copies `/testbed` from the image to a template.
2. It commits a baseline in the template. Thus, build files are not in the patch.
3. For each arm and seed, it copies the template and runs the harness in the copy.
4. The agent runs tests in the image with the `envrun` script. The script mounts the copy at `/testbed`.
5. It makes the patch with `git add -A` and `git diff --cached <baseline>`.
6. It grades the patch with `swebench.harness.run_evaluation`.
7. It removes the copy. When all runs of the instance are complete, it removes the template and the image.

## Results file

Each line of the results file is one run. Important fields:

- `resolved`: true or false from the official report. It is null when the run did not complete.
- `api_error`: the error line when the model API failed, for example "Insufficient Balance". The run is not graded.
- `error`: an infrastructure error.
- `steps`, `inputTokens`, `outputTokens`, `cacheReadTokens`, `wall_s`, `tools`: the cost of the run.
- `budget_notices`: the number of different step-budget notices. The script counts them in the session log, because the `--json` events do not show them. The session log records each notice more than one time, so the script counts each notice one time.
- `syntax_denials`, `routed`: how often each plugin acted. Observation masking is not counted, because the `--json` events show the tool results before masking.
- `credit_before`, `credit_after`: the OpenRouter credit in USD before and after the run. With one worker, the difference is the cost of the run.

When you start the script again with the same results file, it does not run the rows that are complete. It runs the rows with `api_error` or `error` again. After 3 API errors in sequence, the script stops new runs.

## Commands

Start a run from the Mac:

```bash
eval/harness-bench/swebench-docker/launch.sh hard1 hard.jsonl R,P,H 1,2 --workers 4
```

Monitor the run:

```bash
ssh brainvps tail -f /root/dshbench/logs/hard1.log
```

Move arms P and H to new branch commits and build them again:

```bash
ssh brainvps /root/dshbench/update_arms.sh
```

## Model and provider

All arms use the same model. `run_hard.py` gives each arm the overlay `openrouter-v4pro.patch.yml` with `--patch`. The overlay sets the provider `openrouter` and the model `deepseek/deepseek-v4-pro`. To use a different overlay, set `BENCH_PATCH`.

`launch.sh` reads the OpenRouter key with `withorkey.sh` and sends it through the ssh input. The key is not written to a file.

The shell of the agent gets the key in its environment. Thus the agent can show the key in a command result. For this reason, `run_hard.py` removes the key from the events, the error output and the patch before it writes them. It also deletes the session log after it counts the step-budget notices.

To send more variables to the agent, put their names in `BENCH_PASS_ENV`. Do not send secrets with `BENCH_PASS_ENV`, because their values go on the remote command line. `launch.sh` does not send names that contain KEY, TOKEN or SECRET.

## Credit limit

Before each run, the script reads the OpenRouter credit. When the credit is less than `BENCH_MIN_CREDIT` (default 0.60 USD), the script does not start more runs. Runs that started before continue to the end.
