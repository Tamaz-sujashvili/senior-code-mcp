# pi coding agent compared with the DeepSeek Harness fork

Date: 2026-10-18.

This document compares the pi coding agent with the DeepSeek Harness (DSH) fork. It lists the pi advantages, tells if the fork already has each one, and gives the academic evidence. It also records which items were ported to the fork, which were not, and why.

## 1. Sources

| Item | Value |
| --- | --- |
| pi upstream | `github.com/earendil-works/pi`, commit `c268154c` (2026-10-17). `github.com/badlogic/pi-mono` resolves to the same repository and commit. |
| pi installed locally | `@earendil-works/pi-coding-agent` 1.3.0 |
| Local pi setup | `~/.pi/agent` (structure only; no secrets were read or copied) |
| Fork | `github.com/Tamaz-sujashvili/deepseek-harness`, branch `feat/pi-ports` (commit `315b7d9f`), stacked on `feat/p2-outline-symbol` |
| Evidence search | `academic` MCP (Firecrawl paper index). Only IDs that were retrieved in this session are cited. |

pi paths are relative to the pi repository root. `CA` means `packages/coding-agent/src/core`. Fork paths are relative to the fork root.

## 2. The local pi setup

The user's `~/.pi/agent` directory has this structure:

- `settings.json`: default provider `openrouter`, default model `z-ai/glm-5.3-flash`, thinking level `low`, compaction on (`reserveTokens` 16384, `keepRecentTokens` 20000), and retry on (`maxRetries` 4, `baseDelayMs` 2000, `maxAgentDelayMs` 60000, provider `maxRetries` 2). The backup `settings.json.bak-20260930-032142-empty-response-retry` is the state before the retry block was added.
- `models.json`: provider overrides for `openrouter` (price-sorted routing, session-affinity headers, per-model context and output caps) and two custom OpenAI-compatible providers. The file holds API keys. They were not read.
- `models-store.json`: cached model catalogs for `deepseek`, `openai-codex`, `anthropic` and `openrouter`.
- `extensions/`: nine TypeScript extensions. Two are relevant here:
  - `provider-empty-response-retry.ts` marks "Provider returned an empty response" and empty assistant turns as retryable, because pi's retry pattern does not match that text.
  - `rtk.ts` rewrites `bash` commands through `rtk rewrite` to cut command output.
- `mcp.json`: one stdio MCP server (`academic`), bridged into pi by `extensions/mcp-bridge.ts`.
- `skills/` (4 skills), `prompts/` (1 prompt template), `sessions/` (351 MB of JSONL session trees).

## 3. Comparison by area

### 3.1 Tool set and tool output

| Area | pi | Fork | Gap |
| --- | --- | --- | --- |
| Default tools | `read`, `bash`, `edit`, `write`; optional `grep`, `find`, `ls` (`CA/tools/index.ts:95`, `CA/settings-manager.ts:85`) | `read`, `write`, `edit`, `glob`, `grep`, `bash`, and more (`docs/tool-catalog.md`) | No. The fork has no `ls` tool; `glob` covers most uses. |
| Read output | Raw text, no line numbers; 2000 lines or 50 KB; continuation hint `Use offset=M to continue.` (`CA/tools/read.ts:182-199`) | `N: text` lines in a `<path>/<content>` envelope; 2000 lines, 50 KB, 2000 characters for each line; the same continuation hint (`packages/fs/tool-fs/src/read-render.ts:152-170`) | No. The fork shows line numbers; pi does not. |
| Bash output | Tail kept; full output in a temp file; the path is in the notice (`CA/tools/bash.ts:348-352`) | Tail kept (64 KB for each stream); full output spilled; the path is in the notice (`packages/shell/tool-bash/src/render.ts:12-66`, `packages/shell/bash-local/src/index.ts:102-104`) | No. |
| Grep output | `path:N: text`; 100 matches; 500 characters for each line (`CA/tools/grep.ts:21-41`) | `Line N: text` grouped by file; 250 matches; full result spilled (`packages/fs/tool-fs-search/src/grep.ts:29-35`) | No. |
| General output cap | Per tool | `spill-policy` caps every tool result at 12500 tokens, head plus tail, full result stored (`packages/spill/spill-policy/src/index.ts:133-150`) | No. The fork is stronger here. |
| Edit matching | Exact match first, then a fuzzy match: NFKC, trailing whitespace removed, Unicode quotes, dashes and spaces mapped to ASCII; a unique match is required (`CA/tools/edit-diff.ts:34-55`, `:207-251`). Untouched lines keep their bytes. | Exact literal match after CRLF normalization only (`packages/fs/fs-local/src/fsio.ts:814-835`) | **Yes. Ported as `dsh-edit-near-miss` (section 4.1).** |
| Edit argument repair | Accepts `edits` as a JSON string, a single object, or legacy fields (`CA/tools/edit.ts:103-134`) | Not present | Small. DeepSeek models in the fork send the schema correctly in the runs of this session; not ported. |
| Multi-edit | One `edit` call takes `edits[]`; all edits match the original file; overlaps are refused (`CA/tools/edit-diff.ts:341-350`) | One replacement for each call | Not ported. It changes the model-facing schema; see section 5. |
| Edit result | One sentence; the diff goes only to the UI (`CA/tools/edit.ts:201-210`) | One sentence; the diff goes only to the UI (`packages/fs/tool-fs/src/edit.ts:65-69`) | No. |

### 3.2 System prompt

pi builds its prompt from named sections (`CA/system-prompt.ts:155-191`): a preamble, a one-line snippet for each tool, deduplicated rules, documentation pointers, project context, skills and the working directory. The default text is about 420-450 words before tool schemas and project files. Mid-session changes are added as system-message patches (`CA/system-prompt.ts:217-229`), so the cached prefix stays the same.

The fork prompt is smaller. It is one opener, one persona line, and about 33 short sections. Each tool adds one sentence only when the tool is visible (`packages/core/system-prompt/src/index.ts:131-166`). Dynamic facts go into user-role snapshot messages, not into the system prompt. DeepSeek models declare `systemPromptUpdate: 'in-history'`, so prompt changes are appended and the prefix stays the same. Both projects load `AGENTS.md` and `CLAUDE.md` from the global directory and from the root down to the working directory (`packages/context/agent-instructions/src/index.ts`).

Gap: none. The fork prompt is already small and prefix-stable.

### 3.3 Context management and compaction

| Area | pi | Fork | Gap |
| --- | --- | --- | --- |
| Trigger | `contextTokens > contextWindow - reserveTokens` (16384) (`CA/compaction/compaction.ts:267-269`) | `min(W*0.8, W - O - headroom)` with headroom 65536 (`packages/compaction/compaction-basic/src/config.ts:191-194`) | No. |
| Cut point | Walk back until `keepRecentTokens` (20000); never cut at a tool result (`compaction.ts:446-499`) | Walk back until the retained budget (16% of the window); move the cut so tool calls and results stay paired (`packages/compaction/compaction-basic/src/region.ts:117-155`) | No. |
| Summary prompt | Goal / Constraints / Progress / Key Decisions / Next Steps / Critical Context; iterative update of the previous summary (`compaction.ts:507-583`) | Eight fixed sections; merge of an earlier `<compacted-summary>`; sent as the last user message after the replayed prefix, so the KV cache is reused (`packages/compaction/compaction-basic/src/summarizer.ts:26-67`) | No. The fork reuses the cache; pi sends summaries with `cacheRetention: "none"`. |
| Split turn | A separate turn-prefix summary when the cut is inside a turn (`compaction.ts:929-1019`) | The cut moves to keep pairs; no separate prefix summary | Small; not ported. |
| File tracking | `read`/`write`/`edit` paths are collected without the model and appended as `<read-files>` and `<modified-files>`; they carry forward across compactions (`CA/compaction/utils.ts:30-84`) | Only the model-written "Files and Code" section | **Yes. Ported as `dsh-compaction-file-tracking` (section 4.2).** |
| Overflow recovery | Remove the failed message, compact, retry once (`CA/agent-session.ts:3007-3076`) | Prune, compact with zero retention, retry; bounded by `maxOverflowRetries` (`packages/compaction/compaction-basic/src/index.ts:190-233`) | No. |
| Observation masking | Not present | `dsh-compaction-observation-mask` (`packages/compaction/compaction-observation-mask`) | The fork is stronger here. |

### 3.4 Session model

pi stores a session as an append-only JSONL tree: each entry has `id` and `parentId` (`CA/session-manager.ts:277-283`). It can move the leaf in the same file (`/tree`) and fork a path into a new file (`/fork`, `/clone`). It can also label entries and summarize an abandoned branch when the user returns to another branch (`CA/compaction/branch-summarization.ts`). A `context_edit` entry hides a failed attempt from the model while the raw history stays.

The fork stores an append-only event log in one JSONL file for each session (zstd by default). The model-visible surface is derived from the log, and compaction appends replacements. It can fork a new session from a sequence number (`packages/core/session/src/fork.ts:21-30`), and `subagent_fork` inherits the parent history.

It cannot move to an earlier point in the same session or summarize an abandoned branch.

Gap: yes, in-place branching and branch summaries. Not ported: it changes the Session format, the agent loop and both SDKs, and needs a UI.

Evidence is mixed: rewinding with experience reuse helps (ReTRAIL, arXiv:2610.13058), but the same paper reports that a rewind primitive alone "provides limited benefit and can even degrade performance". SPROUT (arXiv:2610.14405) and TreeFlex (arXiv:2610.20334) show gains from branching, but in RL training and search settings, not in an interactive harness.

### 3.5 Provider handling and retries

| Area | pi | Fork | Gap |
| --- | --- | --- | --- |
| Layers | Provider retry (status 408/409/429/5xx, `retry-after`) and agent retry by error-text pattern (`packages/ai/src/utils/provider-retry.ts:107-128`, `packages/ai/src/utils/retry.ts:30-120`) | One retry plugin on `agent/request-error` that uses stable error codes (`packages/llm/llm-retry/src/index.ts`) | No. Codes are more exact than text patterns. |
| Defaults | 3 retries, 2 s, 4 s, 8 s, no jitter | 5 retries, 500 ms doubling to 10 s, 10% jitter (`packages/llm/llm/src/retry-policy.ts:14-24`) | No. |
| Empty response | No dedicated rule; the user's extension adds one | `EMPTY_RESPONSE` is a default retryable code (`packages/llm/llm-deepseek/src/translate.ts:149`) | No. The fork already covers the case that the user's pi extension fixes. |
| Stream ended early | Retried (`ended without`, `stream ended before message_stop`) | `STREAM_CLOSED` is thrown (`translate.ts:165`) but is not a default retryable code | Yes, small. No code change: add `STREAM_CLOSED` to `retryPolicy.retryableCodes` of the provider configuration (section 5). |
| Non-retryable quota | Pattern list wins over retry patterns | `QUOTA` is a separate code and not retryable | No. |

Evidence: transparent retry was the most important primitive in HiveMind (arXiv:2604.17111). In AgentChaos (arXiv:2608.06790), LLM API faults such as truncated responses reduced pass@1 by up to 50 points.

### 3.6 Extensions, skills and prompt templates

pi has an extension API with about 40 events, including `tool_call` (change arguments), `tool_result`, `message_end` (change the message), `context`, and `before_provider_request` (`CA/extensions/types.ts:1597-1664`).

Skills use progressive disclosure: only the name, description and location go in the prompt (`CA/skills.ts:358-388`). Markdown prompt templates become `/name` commands.

The fork is built only from Cordis plugins with typed events and waterfalls, and the bundle composes them in YAML. Skills also use progressive disclosure through the `skill` tool, with descriptions capped at 500 characters (`packages/skill/tool-skill/src/index.ts:28`). The fork has no Markdown prompt templates.

Gap: prompt templates are a user feature with no model effect. They were not ported.

Progressive disclosure is present in both. Evidence for it is positive but task-dependent: SkillReducer (arXiv:2603.29919), Skill Blocks (arXiv:2608.14943), SkillJuror (arXiv:2606.11543). SkillRouter (arXiv:2603.22455) reports that hiding the skill body lowers routing accuracy in very large registries.

### 3.7 Token efficiency

| Feature | pi | Fork |
| --- | --- | --- |
| Prompt cache control | Anthropic `cache_control`, OpenAI `prompt_cache_key`; optional 1-hour retention | DeepSeek caches on disk without request fields; the fork keeps the prefix stable (in-history prompt updates, addition-only tool updates, cache-reusing summaries) |
| Cache warming | `cache-warmer.ts` sends a 1-token request before the cache expires when expected savings are at least $0.05 | Not present. Not needed for the DeepSeek disk cache. |
| Code mode | Optional QuickJS script that calls tools and returns only its output (`docs/codemode.md`) | `run_code` (PTC mode) |
| Old tool output | Not removed except by compaction | Observation masking and the tool-result pruner |

## 4. Ported items

Both items are opt-in plugins. They are off by default and turned on by an environment variable in `packages/bundle/base/cordis.patch.yml`.

### 4.1 `@deepseek-ai/dsh-edit-near-miss` (`DSH_EDIT_NEAR_MISS=1`)

Path: `packages/fs/edit-near-miss`.

Sometimes the literal `old_string` is not in the file, but exactly one passage matches it after pi's normalization. The normalization covers NFKC, trailing whitespace, and Unicode quotes, dashes and spaces. In that case, the plugin refuses the call at the `fs/edit-intent` slot. The file does not change.

The model receives the line range and the exact file text as a JSON string for the next `old_string`. Invisible characters in that string are `\uXXXX` escapes.

The live test (section 6) added one more case: when `old_string` contains literal escape text such as `\u00a0`, the search runs again with the escapes decoded.

The fork's plugin API cannot change tool arguments, so the plugin does not apply the normalized match itself as pi does. This costs one extra call, but the result is never a silent wrong edit. Evidence: arXiv:2609.11957 measured search/replace apply success under drift. It was 0.176 for exact matching and 0.571 for whitespace-normalized matching, with zero silent misapplications when ambiguous matches are refused. CODESTRUCT (arXiv:2604.05407) names string-matching edit failures "due to formatting drift" as a main failure source of code agents.

### 4.2 `@deepseek-ai/dsh-compaction-file-tracking` (`DSH_COMPACTION_FILE_TRACKING=1`)

Path: `packages/compaction/compaction-file-tracking`.

The engine is `dsh-compaction-basic` with one change at its documented `summarize()` hook. After the model writes the summary, the engine appends `<read-files>` and `<modified-files>` lists.

The lists come from two sources. The first is the file-path arguments of successful `read`, `code_outline`, `code_read_symbol`, `edit` and `write` calls in the compacted region. The second is the lists of an earlier summary.

A modified path is listed only once, under `<modified-files>`. The lists are part of the logged `compaction/summary` event. When the variable is set, the bundle disables `compaction-basic` and mounts this engine.

Evidence: CORVUS (arXiv:2607.22711) keeps a registry of the files an agent works on and reports 9-50% fewer input tokens per task at comparable pass rates. MemTrace (arXiv:2610.04838) anchors compacted memory to files and symbols so the agent can rebuild its state after a context refresh. No retrieved paper tests a file list inside a summary by itself, so this decision rests on pi's design and on these two related results.

## 5. Items not ported

| Item | Reason |
| --- | --- |
| Tree sessions, `/tree`, branch summaries | Needs a Session format change, agent-loop change, both SDKs and a UI. Evidence for a rewind primitive alone is weak (arXiv:2610.13058). |
| Multi-edit `edits[]` | Changes the model-facing `edit` schema and every recorded snapshot. No retrieved paper isolates its effect. |
| Edit argument repair | No failures of this kind in the fork's DeepSeek runs. |
| `STREAM_CLOSED` retry | Configuration only. Set `retryPolicy: { mode: normal, retryableCodes: [EMPTY_RESPONSE, RATE_LIMIT, SERVER, TIMEOUT, TRANSPORT, STREAM_CLOSED] }` in the `llm-deepseek` settings section. |
| Empty-response retry | Already in the fork (`EMPTY_RESPONSE`). |
| Cache warmer | DeepSeek uses a disk cache with no paid write and no short TTL. |
| Prompt templates, `ls` tool | No model-side effect; `glob` covers listing. |
| Split-turn prefix summary | The fork keeps tool pairs intact instead; low value. |

## 6. Live A/B test

Model: `deepseek-flash` (base bundle, `--profile headless`). Harness: `ab/bench.py` in the session scratchpad. Each run uses a new workspace and a new `DSH_HOME` under `/private/tmp`. A run passes when the target line changed and the other lines did not.

Tasks:

- `nbsp`: change one sentence in `README.md` that contains two U+00A0 no-break spaces. Line 4 has a third U+00A0 that must stay.
- `narrow`: change one French sentence in `notes.md` that contains U+202F narrow no-break spaces. The other lines must stay.

| Task | Arm | Runs | Passed | Mean steps | Mean failed tool calls | Hints shown | Mean input tokens | Mean output tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| nbsp | stock | 5 | 3 | 11.2 | 2.2 | 0 | 162698 | 3056 |
| nbsp | near-miss | 5 | 5 | 7.6 | 1.4 | 6 | 104052 | 1689 |
| narrow | stock | 5 | 5 | 6.2 | 0.6 | 0 | 79679 | 728 |
| narrow | near-miss | 5 | 5 | 6.2 | 0.4 | 2 | 79826 | 841 |

Findings:

- On `nbsp`, the plugin arm passed 5 of 5 runs and used 36% fewer input tokens. Both stock failures were collateral damage: the model could not match the sentence, so it replaced every U+00A0 in the file with a Python script, which also changed line 4.
- On `narrow`, the arms are equal. The model usually matched the U+202F text on the first call.
- With 5 runs for each cell, these differences are not statistically significant. They show the mechanism, not an effect size.
- The model cannot reliably send U+00A0 in tool arguments. Its failed calls sent a plain space or the literal six characters `\u00a0`. In one plugin run the model received three hints and still did not send the character. It then used a script. A zero-call fix can remove this loop: apply the unique normalized match in the filesystem provider, as pi does.
- In earlier runs of two other tasks (curly quotes and en dashes typed as ASCII in the prompt, and multi-line edits over lines with trailing spaces), the stock arm made no failed edits in 6 runs. The model copied the characters from the `read` output. The plugin has no effect when the model copies correctly.

Two defects were found and fixed during the test, so earlier runs are not counted:

1. A new package is resolved from `src` by the source launcher, but the first runs had no effect because of defect 2.
2. `dsh-fs-observation-policy` occupies the `fs/edit-intent` slot without calling `next()`, and plugin load order does not follow the row order in `cordis.patch.yml`. The near-miss listener was behind the policy and never ran. The listener is now registered with `prepend: true`, and a unit test registers the policy first. A live probe confirms that the hint is shown.

The same defect makes `dsh-edit-syntax-guard` (branch `feat/p1-syntax-guard-observation-mask`) inactive in the live base bundle: a probe with `DSH_EDIT_SYNTAX_GUARD=1` applied a syntax-breaking Python edit. That fix is a follow-up.

`dsh-compaction-file-tracking` was not A/B tested. Its effect appears only after a compaction, and the 1,000,000-token DeepSeek window does not compact in short runs. A live run with `DSH_COMPACTION_FILE_TRACKING=1` booted the base bundle without errors.

## 7. Verification

- Unit tests: `edit-near-miss` 20 tests, `compaction-file-tracking` 12 tests, 100% statement and branch coverage for both `src/index.ts` files. `compaction-basic` still passes (183 tests in the three packages). `pnpm run typecheck` passes.
- Gates: `tsc -b`, `oxlint`, `test:docs` (21 of 21), `verify-cordis-config` (276 files), `verify-export-jsdoc`, regenerated `config-catalog` and `module-graph`. The tool catalog did not change.
- Review: Codex CLI. Two passes.

  - Pass 1 had five findings. Fixed: trailing whitespace on a partial last line of `old_string` could point at different text (`foo ` matched the `foo` of `foobar`). Also fixed: list tags in ordinary messages were carried forward, and tool path arguments were trimmed. Documented: quoted text uses LF line endings, and Web app presets do not mount either plugin.
  - Pass 2 had four findings. Fixed: carry-forward now requires a compaction-checkpoint message source, and paths with line endings are not listed. Not changed: the escape-decoding hint states its interpretation ("With those escapes decoded, it matches ..."), which is correct. Rejected after a check: U+200B and U+2060 are already shown as escapes.

## 8. Known gaps in this work

- No committed REAL-composition test that boots the base bundle with the variables set (rule in `packages/AGENTS.md`). The live runs boot the base bundle, but they are not a committed test.
- No keyless recorded-session snapshot for the new model-visible text.
- The near-miss hint costs one extra call, and the model cannot always send the character that the hint asks for (section 6). A zero-call version needs a change in `dsh-fs-local`.
- `dsh-edit-syntax-guard` is inactive in the live base bundle because of the slot-order defect (section 6).
