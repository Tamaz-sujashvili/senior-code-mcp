# AGENTS.md

For AI coding agents. Not humans. Auto-read on repo open. This is how the org onboards: connect this repo + their codebase to you, you set it all up yourself, methodically.

## 1. Prerequisites (verify before touching anything)

**Call the `store_stats` MCP tool first** — it reports vector count and graph node/edge counts, telling you whether an index already exists. Use its output plus the checks below to decide what actually needs fixing before asking the org anything.

- Docker daemon running: `docker info` succeeds.
- Qdrant reachable on 6333: `curl -s http://localhost:6333/healthz` → `healthz check passed`. If down, `docker compose up -d qdrant`.
- Ollama running on 11434: `curl -s http://localhost:11434/api/tags` returns JSON. If down, start it natively (preferred on macOS) or `docker compose up -d ollama`.
- Embedding model pulled: `ollama list` shows `nomic-embed-text`. If missing: `ollama pull nomic-embed-text`.

If any check fails, stop, report which, and wait — do not guess fixes.

## 2. Ask the org (before any write)

Get explicit answers to all three. No defaults assumed except where stated.

1. **Code perimeter.** "Is your code allowed to leave this machine?" Default = **fully local** (Ollama `nomic-embed-text`). A cloud embedding API (OpenAI/Voyage/etc.) sends code outside the perimeter — requires **explicit written approval** from the org. Without it, stay local. Record the answer.
2. **Which repos to index.** Get the repo paths. Confirm each exists and is readable. Python files only in this version.
3. **Re-index cadence.** On-demand only, nightly, or on git hook. Default = on-demand. Re-ingesting appends (chunks carry deterministic IDs); resetting a collection is a separate destructive action — see guardrails.

Do not proceed until all three answered.

## 3. Guardrails (hard)

- **Never reset/drop the Qdrant collection** without explicit confirmation from the org. Ingest appends by default; a reset is destructive — ask first, every time.
- **Never send code to any external endpoint** without the written approval from §2.1. Local-only is the default.
- **Secrets stay in env vars.** Never write keys to source, never commit `.env`. Respect `.gitignore`.
- Respect the org's `.gitignore` for any repo you index — skip what they ignore.

## 4. Setup (after prereqs pass + questions answered)

1. **Ingest repos:** `senior-code-ingest <repo-path>` — one call per repo. Wait for each to finish. Chunks are tagged with the repo name, so multi-repo indexes stay distinguishable.
2. **Register the MCP server** with the agent runtime: point it at the venv binary `.venv/bin/senior-code-mcp` (entry: `senior_code_mcp.server:main`). Stdio transport. `.cursor/mcp.json` in this repo is an example.
3. **Prove it works:**
   - `store_stats` → report vector count + graph node/edge counts.
   - One `search_similar_code` call with a concept from an indexed repo. Confirm non-empty, relevant result.
   - Optionally `search_related_code` on a hit, or `search_context` for the combined two-stage result.

## 5. Report back (short)

One-paragraph summary to the org: prereqs status, perimeter decision (local/cloud), repos indexed + vector counts, re-index cadence, test search result. Note anything skipped or pending approval. Do not narrate steps beyond this.
