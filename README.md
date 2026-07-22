# Senior Code MCP

An MCP server that gives coding agents (Cursor, Claude Code, any MCP client)
two ways to search a codebase before writing code:

- **Semantic similarity** — symbol-level chunks embedded with a local model
  and stored in Qdrant.
- **Structural relationships** — a graph of calls and imports between
  symbols, expanded via NetworkX.

The point: the agent reuses existing patterns instead of guessing, and you
stop pasting whole files into the context window.

## How it works

```
repo -> parse (Python stdlib ast) -> symbol chunks
      -> embed (Ollama nomic-embed-text, 768d) -> Qdrant
      -> call/import edges -> NetworkX graph (graph.json)
```

Ingestion walks a repo, extracts functions/classes/methods with the Python
`ast` module, embeds each symbol's source, upserts into Qdrant, and builds a
directed graph of call and import relationships.

## MCP tools

| Tool | What it does |
| --- | --- |
| `search_similar_code` | Embeds a natural-language or code query, returns top-k matching symbols (path, lines, docstring, truncated source, score). |
| `search_related_code` | Given symbol names, expands N hops through the call/import graph and returns connected symbols. |
| `search_context` | One call, two-stage retrieval: vector search for the query, then graph expansion on the top hits' symbol names. Returns one combined result (`similar_chunks` + `related_symbols`). |
| `ingest_repo` | Runs the full pipeline on a local repo path (parse, embed, upsert, build graph). |
| `store_stats` | Diagnostic: vector count in Qdrant, node/edge counts in the graph. |
| `doctor` | Read-only prerequisite self-diagnosis (qdrant reachable, collection exists + vector count, ollama reachable + embed model present, graph file + node/edge counts). The onboarding agent's first call. |

Typical agent flow: `doctor` first to confirm the store is healthy, then
`search_similar_code` for a concept, then `search_related_code` on the hit
names to pull in callers/callees — or `search_context` to get both stages in
a single call.

## Quick start (~5 min)

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant
ollama pull nomic-embed-text
uv sync
senior-code-ingest /path/to/any/python/repo
```

Alternative: `docker compose up` brings up Qdrant + Ollama together — but on
macOS, native Ollama is preferred (faster, uses Metal GPU) over the container.

Then register in Cursor (`.cursor/mcp.json` in this repo is an example, or
add to global `~/.cursor/mcp.json`):

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

Restart Cursor, then ask it to "call store_stats" and
"search similar code for <something>".

## Config

| Setting | Default | Env var |
| --- | --- | --- |
| Qdrant URL | `http://localhost:6333` | `QDRANT_URL` |
| Collection | `code_chunks` | `QDRANT_COLLECTION` |
| Ollama URL | `http://localhost:11434` | `OLLAMA_URL` |
| Embed model | `nomic-embed-text` | `EMBED_MODEL` |

## Honest limitations

- Python files only (stdlib `ast`; tree-sitter multi-language is the planned
  upgrade).
- Fully local: no hosted embeddings, no cloud vector DB.
- Single shared collection; multi-tenant separation is future work.

Built at the Cursor x Superteam Georgia hackathon (Tbilisi, Jul 22 2026) —
built with Cursor, end to end.
