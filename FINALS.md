# FINALS — demo playbook

Finalist demo strategy for Senior Code MCP (Cursor × Superteam Georgia hackathon).
Format per organizers: strictly timed · problem → product working → what was built + how Cursor was used · no team intros, no vision talk.

---

## Demo options (pick per situation)

### Option A — Live proven flow (PRIMARY)
The rehearsed, verified flow inside Cursor:
1. `call doctor` — server self-checks prerequisites live
2. `call store stats` — 394 vectors / 62 nodes / 73 edges, 2 repos
3. `search similar code for peer review ranking` — semantic hit on `stage2_collect_rankings`
4. `search related code on the top hit, 2 hops` — graph blast radius
5. Mention `search_context` = both in one call
- **Risk:** live deps (Docker, Ollama, Cursor MCP). **Reward:** judges see the real thing in the real tool.
- **Mitigation:** pre-flight checklist below, done BEFORE walking up. Option B as backup.

### Option B — Recorded fallback (INSURANCE — record TONIGHT)
Screen-record the exact Option A flow while everything works (macOS: Cmd+Shift+5).
If Wi-Fi/Docker/Cursor dies on stage, play the recording and narrate over it.
A finalist with a recording looks prepared; without one, looks unlucky. Record it now, not tomorrow.

### Option C — Judge's-choice query (HIGH RISK, only if invited)
Let a judge name a concept, search it live. Devastatingly impressive when it hits.
**Only** offer it framed by the corpus: "name anything around LLM orchestration or clean-architecture FastAPI — those are the two repos indexed." Never open-ended.
Use the proven alternates below if they hesitate.

### Option D — "Agent builds with it" (STRONGEST CLOSER if time allows)
Fresh Cursor chat, one prompt:
> "Build a small FastAPI CRUD with a repository + unit-of-work pattern — but first call search_similar_code for the repository pattern and use what you find."
Agent calls the tool, pulls py-clean-arch's real UoW/repository code, then writes new code *following that pattern*.
This demonstrates the product's **point** (reuse), not just its mechanics. Rehearse once before using.

### Option E — Org-onboarding story (only if asked about enterprise)
New chat simulating a fresh org: agent reads `AGENTS.md`, calls `doctor`, asks the perimeter question ("may code leave this machine?"), then ingests.
~3-4 min and ingest takes ~1 min — too slow as the main demo, perfect as a Q&A answer to "how would a company adopt this?"

---

## Recommendation

**A as the spine, D as the closer if ≥4 minutes are given, B recorded tonight as insurance.**
Run of show (3 min): problem (15s, slide 2) → A live (75s) → before/after (15s, slide 5) → built-with-Cursor + stack (20s, slides 6-7) → close (10s). With 4-5 min: insert D after A.

---

## Pre-flight checklist (10 min before stage)

- [ ] `docker ps` shows qdrant; `curl localhost:6333/healthz` passes
- [ ] `ollama list` shows nomic-embed-text
- [ ] `store_stats` answers in a Cursor chat (394 / 62 / 73)
- [ ] Cursor open on `senior-code-mcp/`, MCP green, demo chat history visible
- [ ] Slides open (`submission/senior-code-mcp-pitch.pptx`), recording cued
- [ ] Repo tab open: github.com/Tamaz-sujashvili/senior-code-mcp

## Proven queries (tested against the live store, Jul 22)

| Query | Top hit | Score | Use for |
|---|---|---|---|
| peer review ranking | `stage2_collect_rankings` (llm-council) | 0.64 | rehearsed main flow |
| how conversations are stored | `save_conversation` (llm-council) | **0.76** | strongest opener alternate |
| unit of work repository pattern | `provide_async_sqlalchemy_unit_of_work` (py-clean-arch) | 0.58 | multi-repo + Option D story |
| ranking and scoring | `calculate_aggregate_rankings` (llm-council) | 0.65 | alternate for step 3 |

## Emergency playbook

- **Tool call hangs/errors** → switch to the recording, narrate: "same run, recorded an hour ago."
- **MCP red in Cursor** → demo from the earlier chat history (tool chips visible) + repo README.
- **"Is it just RAG?"** → "RAG over text is common. Ours indexes code *structure* — functions as units — plus a call/import graph. The graph is the difference."
- **Numbers to memorize:** 394 vectors · 62 nodes · 73 edges · 2 repos · 6 tools · 3 hours · 1 person.
