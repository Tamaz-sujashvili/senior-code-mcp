# Agentic Harness Engineering compared with the DeepSeek Harness fork

## 1. Summary

This report compares the Agentic Harness Engineering (AHE) repository with the DeepSeek Harness (DSH) fork. AHE source: `github.com/china-qijizhifeng/agentic-harness-engineering`, paper arXiv:2604.25850. Fork source: `github.com/Tamaz-sujashvili/deepseek-harness`.

AHE is an offline loop that changes the harness of a coding agent. The base model does not change. The loop produced an evolved harness with three runtime parts: a shell tool with guards, a middleware that adds risk notes, and a long-term memory file. Two runtime ideas were not in the fork. We ported them as opt-in plugins on branch `feat/ahe-ports`:

- `@deepseek-ai/dsh-verify-before-finish` (`DSH_VERIFY_BEFORE_FINISH=1`): a gate at the end of a turn. It acts when no passing check ran after the last edit. The gate then sends one message, and the agent continues for one step.
- `@deepseek-ai/dsh-execution-risk-hints` (`DSH_EXECUTION_RISK_HINTS=1`): a port of the AHE `ExecutionRiskHintsMiddleware`. It adds notes to shell results. The notes are for repeated error lines, weak checks, and long commands.

We did not port the publish-state guard, the memory and prompt text, or the evolution loop. Section 6 gives the reasons.

## 2. AHE design

### 2.1 Agent loop

- The coding agent runs on the NexAU framework. The seed harness has one tool (`run_shell_command`), no middleware, no skills, and no sub-agents (paper §3.1, Appendix A).
- Operating point (paper Table 4, verified in `experiments/evolved_harness/code_agent.yaml`): GPT-5.4 with high reasoning effort, temperature 0.7, top-p 0.95, 200,000-token context, 32,000 tokens for each turn, 300 model turns.

### 2.2 Tools and output formats

- `run_shell_command` returns text blocks: `Output` (stdout and stderr together), `Exit Code` (only when not zero), `Error`, `Signal`, `Background PIDs`, and `Process Group PGID`.
- The evolved tool adds a `timeout_ms` parameter (default 300,000 ms) and appends `Execution note: …` lines to the output.
- The evolved tool has a publish-state guard. After a passing command with an "evaluator-style" description, it records the files and roots in that command as protected. A later destructive command on a protected target returns exit code 90 with error type `POST_SUCCESS_STATE_GUARD`. The token `ALLOW_POST_SUCCESS_RESET` changes some blocks to warnings.

### 2.3 Context and memory

- The evolved code agent has no compaction middleware. It relies on the 200,000-token context.
- `LongTermMEMORY.md` holds lessons that go into the prompt. The repository copy has 5 lessons. The paper states 12 lessons (§4.4.1).
- The evolve agent (not the code agent) has context compaction at 75% of the context, a long-tool-output middleware, an LLM failover middleware, and round and token reminders. These are NexAU parts, not AHE results.

### 2.4 Planning, verification, and reflection

- System prompt: 8 rule groups, for example "Contract first", "Mirror the evaluator before finishing", and "Finish only when the end state is ready now".
- Middleware `ExecutionRiskHintsMiddleware`: after each shell call, it updates counters and adds at most 2 notes. Before the next model call, it shows pending notes as a framework message. It has 13 detectors. Some are general (same error twice, budget, shallow check, non-fail-fast check). Others are specific to Terminal-Bench (localhost check, benchmark margin, model wrapper, clean layout).
- Outer loop (paper Algorithm 1): the loop runs k rollouts for each task and cleans the traces. It attributes the previous changes and rolls back the changes that failed their predictions. The Agent Debugger then distills the traces, and the Evolve Agent edits the workspace. Each edit has a manifest entry with evidence, root cause, fix, and predicted fixes and regressions.

### 2.5 Multi-agent and best-of-N

- Three role agents share one model: code agent, Agent Debugger, Evolve Agent. An explore agent runs one time to make seed skills.
- AHE does not use best-of-N at inference. It runs k = 2 rollouts for each task only to measure pass@1.

### 2.6 Evaluation and reported numbers

All numbers below were read from the PDF in the repository (`agentic_harness_engineering.pdf`), unless marked.

| Claim | Value | Source | Status |
| --- | --- | --- | --- |
| Terminal-Bench 2 pass@1, seed to AHE (89 tasks, k = 2) | 69.7% to 77.0% | Table 1 | Verified in PDF |
| Codex CLI, Terminus-2, OpenCode | 71.9%, 62.9%, 47.2% | Table 1 | Verified in PDF |
| ACE, TF-GRPO | 68.9%, 72.3% | Table 1 | Verified in PDF |
| AHE on the Hard tier | 53.3% (Codex: 56.7%) | Table 1 | Verified in PDF |
| SWE-bench Verified success, seed to AHE (500 tasks) | 75.2% to 75.6% | Table 2 | Verified in PDF; +0.4 points is 2 tasks, no interval given |
| SWE-bench Verified tokens for each trial, seed to AHE | 526k to 461k (−12%) | Table 2 | Verified in PDF |
| Cross-model gains on Terminal-Bench 2 | +2.3 to +10.1 points; DeepSeek-V4-Flash 51.7% to 61.8% | Figure 3 | Verified in PDF |
| Ablation: memory only, tool only, middleware only, prompt only | 75.3%, 73.0%, 71.9%, 67.4% | Table 3 | Verified in PDF |
| Fix prediction precision and recall | 33.7%, 51.4% (random: 6.5%, 10.6%) | Figure 4 | Verified in PDF |
| Regression prediction precision and recall | 11.8%, 11.1% | Figure 4 | Verified in PDF |
| Terminal-Bench 2.0 leaderboard rank 3, 84.7% with GPT-5.5 | — | README "News" | Not verified; not in the paper |

Differences between the paper and the released files:

- The paper states that the evolved tool is a 1,364-line shell tool (§4.4.1). The released `run_shell_command.py` has 687 lines.
- The paper describes the middleware-only variant as "a finish-hook that forces one evaluator-isomorphic closure check" (§4.4.1). The released middleware is `ExecutionRiskHintsMiddleware`, which matches the changes chg-2 of iterations 6 and 8 in Appendix C.
- The paper states 12 memory lessons. The released file has 5.

Thus, the released `experiments/evolved_harness` is possibly not the exact artifact that the ablation measured. The ablation numbers apply to the paper artifact.

## 3. Comparison with the fork

Evidence IDs are papers that we retrieved and read with the academic tools in this session.

| AHE idea | In the fork? | Academic evidence | Decision |
| --- | --- | --- | --- |
| Shell `timeout_ms`, background runs | Yes: `packages/shell/tool-bash` (`timeoutMs`, `run_in_background`, job promotion), `packages/guard/timeout-policy` | — | No change |
| Repeated identical call reminder | Yes, for identical arguments only: `packages/guard/repeat-tool-reminder` | arXiv:2610.19376 (loops), arXiv:2608.23651 | Kept |
| Same error line in consecutive failures | No | arXiv:2608.23651: a runtime description of the failure reduced repetition, a "do not repeat" instruction did not (models of 135M to 1.7B only) | Ported (`execution-risk-hints`) |
| Notes for shallow and non-fail-fast checks | No | arXiv:2607.28871: 46.0% of passing validation events did not discriminate the bug; a generic reminder reduced closure on weak evidence by 3.2 points | Ported (`execution-risk-hints`) |
| Time-budget note | Partly: per-call timeouts only | AHE middleware ablation only | Ported (`execution-risk-hints`) |
| Closure check before finish | No. `packages/experimental/hooks-claude-code` can run user Stop hooks, but nothing ships | arXiv:2607.28815 (ECLoop): holding commitment actions raised SWE-bench Verified Pass@1 by 4.8 to 11.8 points; the commitment check alone was worth 10 points in its ablation. arXiv:2606.07682: poor self-verification and premature termination are frequent failures | Ported (`verify-before-finish`) |
| Publish-state guard (block destructive commands after a passing check) | No | AHE case study only. The paper states that the guard "closes the loop too early" on Hard tasks | Not ported |
| Long-term memory lessons in the prompt | Mechanism yes: `packages/context/agent-instructions` loads `AGENTS.md` | AHE: memory only +5.6 points on Terminal-Bench 2. The lessons are specific to Terminal-Bench | Not ported |
| Contract-first system prompt | Not applicable | AHE: prompt only −2.3 points. arXiv:2603.17973: procedural instructions increased regressions (6.08% to 9.94%) | Not ported |
| Context compaction, long output to file, LLM failover | Yes: `packages/compaction/*` (including `compaction-observation-mask`), `packages/spill/spill-policy`, `@deepseek-ai/dsh-llm-retry` | arXiv:2508.21433 (from the earlier proposal) | No change |
| Outer evolution loop with change manifest and rollback | No (process, not runtime) | AHE Figure 4; arXiv:2610.02616 (VERSE): optimizer self-evolution helped only with execution-based verification | Not ported; recommended for our A/B process |
| Best-of-N selection | Not in AHE | arXiv:2610.03984 (earlier proposal: arXiv:2407.01489, arXiv:2501.14723, arXiv:2504.07164) | Out of scope for this report |

### 3.1 Gaps ranked by expected effect

The ranking uses expected effect on SWE-bench-style resolve rate first, then token cost.

1. **Closure check before finish** (`verify-before-finish`). Best evidence on SWE-bench Verified (arXiv:2607.28815). Token cost: zero when the agent already checked. One extra step when the gate fires. ECLoop reported 1.4% to 12.1% fewer tokens overall. Risk: a passing check is weak evidence (arXiv:2607.28871, arXiv:2610.03984), so the gate can accept a wrong patch.
2. **Execution risk notes** (`execution-risk-hints`). Evidence is from Terminal-Bench 2 (AHE middleware only: +2.2 points) and a small reminder effect (arXiv:2607.28871). Token cost: zero when silent. 40 to 80 tokens for each note.
3. **Evolution loop with predictions**. High expected value for harness development, but it needs a benchmark run for each iteration. AHE reports that the loop cannot predict regressions (precision 11.8%).
4. **Publish-state guard**. Low frequency on SWE-bench, where the grader reads the final diff. Weak evidence.
5. **Memory and prompt text**. Transfer to other tasks is not shown, and the prompt-only result is negative.

## 4. Implementation

- Branch `feat/ahe-ports` of the fork, commit `6373ceeb`, on top of `feat/p2-outline-symbol`.
- Both plugins are in `packages/guard/` and use extension points only: `tools/pre-execute`, `tools/post-execute`, `agent/pre-step`, and `agent/turn-stopping` with `agent.steer()`. The agent loop is not changed.
- Both are off by default. Rows in `packages/bundle/base/cordis.patch.yml` enable them with `DSH_VERIFY_BEFORE_FINISH` and `DSH_EXECUTION_RISK_HINTS`.
- Tests: 29 unit tests through a real agent loop with a scripted model adapter. Gates: `vitest`, `tsc -b`, `oxlint`, full `pnpm run typecheck`, `test:docs` (21 of 21), `verify-cordis-config`, `verify-export-jsdoc`, translation pairing, regenerated configuration catalog and module graph.
- Codex CLI review found 5 issues. We confirmed 4 and fixed them:
  - An edit and a check in one shell command were applied in the wrong order.
  - A check that reached its timeout counted as a pass.
  - `pytest --help && pytest -q` counted as a help-only check.
  - The start-time map could keep entries when a later pre-execute listener failed.
- The fifth finding (`diff … && echo passed` flagged) was false. A test now covers it.
- A live probe run confirmed that the gate fires in the `headless` profile: the session log has a `user/message` with source `verify-before-finish`, and the agent answered it.

## 5. Live A/B result

Setup: the seeded-bug benchmark from the earlier proposal (`bench.py`, senior-code-mcp repository), DeepSeek through the `headless` profile of the fork. Arm A is the stock harness. Arm B sets `DSH_VERIFY_BEFORE_FINISH=1` and `DSH_EXECUTION_RISK_HINTS=1`. The prompt does not name the test command, so the agent must decide to run tests (hidden-test setting). Task `t4-cli-json` was removed because its seeded bug no longer fails the tests at the current repository head. Four tasks, two seeds, two arms: 16 runs.

| Arm | Runs | Resolved | Median steps | Mean total tokens | Median wall time | Gate messages | Risk notes | pytest calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A (stock) | 8 | 8 | 13.5 | 279,604 | 36.1 s | — | — | 34 |
| B (both plugins) | 8 | 8 | 12.5 | 299,674 | 36.3 s | 0 | 0 | 28 |

Result: no difference.

In all 16 runs the agent ran the tests without a prompt, so the gate and the notes never fired. When they are silent, the plugins add no model-visible text. Thus, the token difference between the arms comes from sampling noise.

This benchmark cannot measure the effect, because the tasks are small and the model already checks its work. A separate probe that told the agent not to run any command confirmed that the gate fires and that the agent answers it.

A useful measurement needs tasks on which the stock agent stops without a check: larger repositories, hidden tests, or a weaker model (AHE reports the largest gains on bases further from saturation). The comparison must count gate firings and resolve rate on the runs in which the gate fired.

## 6. Items not ported

- **Publish-state guard**: specific to tasks in which the evaluator inspects the live file system. On SWE-bench the grader applies the final diff. The paper itself reports that the guard can stop work too early on Hard tasks. No independent evidence.
- **Memory lessons and the contract-first prompt**: the lessons are about Terminal-Bench (web roots, benchmark margins, layout checks). The prompt-only ablation was negative. Users can add their own lessons to `AGENTS.md`, which `agent-instructions` already loads.
- **Terminal-Bench detectors of the middleware** (localhost, benchmark margin, model wrapper, clean layout, proxy validation): specific to that benchmark.
- **Evolution loop**: it is a development process, not a runtime plugin. For our own harness work, we recommend the manifest practice: for each change, write the predicted fixes and regressions before the A/B run, then compare.

## 7. References

Retrieved and read in this session: arXiv:2604.25850 (AHE), arXiv:2607.28815 (ECLoop), arXiv:2607.28871 (validation evidence), arXiv:2608.23651 (failure feedback and repetition). Retrieved as abstracts: arXiv:2603.17973 (TDAD), arXiv:2610.03984, arXiv:2606.07682 (SWE-Marathon), arXiv:2610.19376 (DeLoop), arXiv:2610.02616 (VERSE). From the earlier proposal: arXiv:2508.21433.
