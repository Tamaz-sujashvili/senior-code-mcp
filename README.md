# Senior Code MCP

Give your coding agent a memory of your codebase — so it **reuses proven patterns instead of guessing**, **cuts the tokens you burn pasting whole files into context**, and **ships higher-quality code**.

An MCP server that lets Cursor, Claude Code, or any MCP client search your repos two ways *before* writing a line of new code:

- **Semantic similarity** — symbol-level chunks embedded with a local model and stored in Qdrant.
- **Structural relationships** — a graph of calls and imports between symbols, expanded via NetworkX.

One `search_context` call does both: vector search for the concept, then graph expansion on the top hits, in one combined result.

## Why this is different

| | Senior Code MCP | Generic vector memory (e.g. `mcp-server-qdrant`) | Code-graph MCPs (`codegraph`, `code-graph-mcp`) |
| --- | --- | --- | --- |
| Semantic vector search | ✅ | ✅ | ✅ |
| Structural call/import graph | ✅ | ❌ | ✅ |
| Symbol-level AST chunking | ✅ Python `ast` | ❌ (raw text) | ✅ Tree-sitter, many languages |
| Fully local, code never leaves the machine | ✅ | depends on config | ✅ |
| One focused slice you can read end-to-end | ✅ | — | heavier, multi-language |
| Built + demoed + explained in a single night | ✅ | — | — |

The difference in one line: **vectors alone find similar text; vectors + a call/import graph find the similar text *and* how it's wired into the rest of the codebase.** That's what lets an agent pull in a battle-tested `retry` helper *and* see every caller of it — instead of inventing a new one from scratch.

## How it works

```
repo -> parse (Python stdlib ast) -> symbol chunks
      -> embed (Ollama nomic-embed-text, 768d) -> Qdrant
      -> call/import edges -> NetworkX graph (graph.json)
```

Ingestion walks a repo, extracts functions/classes/methods with the Python `ast` module, embeds each symbol's source, upserts into Qdrant, and builds a directed graph of call and import relationships. Multiple repos coexist in one collection, each chunk tagged with its repo name.

## MCP tools (6)

| Tool | What it does |
| --- | --- |
| `search_similar_code` | Embeds a natural-language or code query, returns top-k matching symbols (path, lines, docstring, truncated source, score). |
| `search_related_code` | Given symbol names, expands N hops through the call/import graph and returns connected symbols. |
| `search_context` | One call, two-stage retrieval: vector search for the query, then graph expansion on the top hits' symbol names. Returns one combined result (`similar_chunks` + `related_symbols`). |
| `ingest_repo` | Runs the full pipeline on a local repo path (parse, embed, upsert, build graph). |
| `store_stats` | Diagnostic: vector count in Qdrant, node/edge counts in the graph. |
| `doctor` | Read-only prerequisite self-diagnosis (qdrant reachable, collection exists + vector count, ollama reachable + embed model present, graph file + node/edge counts). The onboarding agent's first call. |

Typical agent flow: `doctor` first to confirm the store is healthy, then `search_similar_code` for a concept, then `search_related_code` on the hit names — or `search_context` to get both stages in a single call.

## Why organizations adopt it easily

- **Fully local by default.** Ollama embeddings + Qdrant on the machine. Code never leaves the perimeter — no cloud embedding API, no hosted vector DB, no surprise egress. (A cloud embedding API is supported only with explicit written approval, per `AGENTS.md`.)
- **Three commands to a working index.** `docker run qdrant`, `ollama pull nomic-embed-text`, `senior-code-ingest /your/repo`. No cluster, no accounts, no billing.
- **Self-onboarding agent.** `AGENTS.md` is a runbook an AI coding agent auto-reads on repo open: it calls `doctor`, asks the org the three questions that actually need a human (code perimeter, which repos, re-index cadence), respects hard guardrails (never reset a collection, never send code externally without approval, secrets in env), then ingests + registers + verifies on its own.
- **Drops into existing agent stacks.** Std MCP over stdio — Cursor, Claude Code, any MCP client. One entry in `mcp.json`.
- **No vendor lock-in.** Open Python, stdlib `ast`, Qdrant, NetworkX. Read every file in an evening.

## Quick start (~5 min)

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant
ollama pull nomic-embed-text
uv sync
senior-code-ingest /path/to/any/python/repo
```

Alternative: `docker compose up` brings up Qdrant + Ollama together — on macOS, native Ollama is preferred (faster, uses Metal GPU) over the container.

Then register in Cursor (`.cursor/mcp.json` in this repo is an example, or add to global `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "senior-code-mcp": {
      "command": "/absolute/path/to/.venv/bin/senior-code-mcp",
      "args": []
    }
  }
}
```

Restart Cursor, then ask it to "call `doctor`", "call `store_stats`", and "search similar code for <something>".

## Config

| Setting | Default | Env var |
| --- | --- | --- |
| Qdrant URL | `http://localhost:6333` | `QDRANT_URL` |
| Collection | `code_chunks` | `QDRANT_COLLECTION` |
| Ollama URL | `http://localhost:11434` | `OLLAMA_URL` |
| Embed model | `nomic-embed-text` | `EMBED_MODEL` |
| Graph path | `<project>/graph.json` | `GRAPH_PATH` |

## Honest limitations

- Python files only (stdlib `ast`; tree-sitter multi-language is the planned upgrade).
- Fully local: no hosted embeddings, no cloud vector DB.
- Single shared collection; multi-tenant separation is future work.
- No incremental ingest yet — re-running re-embeds the repo (idempotent via deterministic IDs, but not free).

---

Built at the Cursor x Superteam Georgia hackathon (Tbilisi, Jul 22 2026) — built with Cursor, end to end, in one night.
