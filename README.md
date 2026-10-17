# Senior Code MCP

Give your coding agent a memory of your codebase — so it **reuses proven patterns instead of guessing**, **cuts the tokens you burn pasting whole files into context**, and **ships higher-quality code**.

An MCP server that lets Cursor, Claude Code, or any MCP client search your repos two ways *before* writing a line of new code:

- **Semantic similarity** — symbol-level chunks embedded with a local model and stored in Qdrant.
- **Structural relationships** — a graph of calls and imports between symbols, expanded via NetworkX.

One `search_context` call does both: vector search for the concept, then graph expansion on the top hits, in one combined result.

> **Measured on our own corpus:** finds the right symbol in the top-3 **75% of the time vs 50% for grep**, while handing the agent **~3× less code to read** (66% fewer tokens). Retrieval core scores **0.266 NDCG@10** on the official CoIR `cosqa` benchmark with a free, fully-local embedder. See [Benchmarks](#benchmarks).

## Why this is different

Most code-search tools give an agent one lens. A plain vector store returns chunks of text that look similar to the query — useful, but blind to structure: it can't tell you that the `retry` helper it just found is called by 14 services, or that it implements an interface three other modules depend on. A pure code-graph tool gives the opposite lens — relationships without semantic ranking, so you have to already know the symbol name before you can expand from it.

Senior Code MCP runs both lenses on symbol-level chunks produced by a real AST parse:

| | Senior Code MCP | Generic vector memory (e.g. `mcp-server-qdrant`) | Code-graph MCPs (`codegraph`, `code-graph-mcp`) |
| --- | --- | --- | --- |
| Semantic vector search | yes | yes | yes |
| Call/import graph expansion | yes | no | yes |
| Chunk granularity | AST symbols (Python `ast`, tree-sitter for other languages) | raw text windows | AST symbols (Tree-sitter, many languages) |
| Default deployment | local; code stays on the machine | depends on config | local |
| Language coverage | Python, JavaScript, TypeScript/TSX, Go, Rust, Java | language-agnostic | 10+ languages |

The tradeoff is deliberate. The tools on the right do more; this one does less, in a slice small enough to read, run, and reason about in an evening. What that focus buys is a retrieval step that returns not just "here is similar code" but "here is similar code, and everything that calls it, imports it, or shares its shape" — the context an agent needs to reuse a proven implementation instead of writing a parallel one.

## How it works

```
repo -> parse (Python: stdlib ast; JS/TS/Go/Rust/Java: tree-sitter) -> symbol chunks
      -> embed (Ollama nomic-embed-text, 768d) -> Qdrant
      -> call/import edges -> NetworkX graph (graph.json)
```

Ingestion walks a repo, extracts functions/classes/methods (plus interfaces, structs, enums, traits and type aliases) with the Python `ast` module or tree-sitter, embeds each symbol's source, upserts into Qdrant, and builds a directed graph of call and import relationships. Multiple repos coexist in one collection, each chunk tagged with its repo name.

## Architecture

Full design document: [ARCHITECTURE.md](ARCHITECTURE.md). It has diagrams, module reference, runtime flows, data model, evaluation harness and known limitations. The summary follows.

```
src/senior_code_mcp/
├── __init__.py          # Package metadata (__version__)
├── server.py            # MCP server: FastMCP, stdio transport, 6 tools
├── cli.py               # senior-code-ingest entry point (--reset flag)
├── pipeline.py          # Shared ingest: parse → chunk → embed + upsert → graph
├── ingest/              # Code analysis
│   ├── languages.py     # Language registry: extensions, grammars, node types
│   ├── parser.py        # stdlib ast walk (Python) + dispatch by extension → Symbol
│   ├── treesitter.py    # tree-sitter walk for JS / TS / TSX / Go / Rust / Java
│   └── chunker.py       # One Chunk per definition
└── store/               # Persistence
    ├── vectors.py       # Ollama embed client + Qdrant collection, search
    └── graph.py         # NetworkX DiGraph: call + import edges, BFS expand

eval/
├── retrieval_ab.py      # Ablation: dense retrieval vs BM25/grep baseline
├── coir_bench.py        # CoIR runner (cosqa, codesearchnet-python)
└── results/             # cosqa.json, retrieval_ab.txt

AGENTS.md                # Onboarding runbook for AI coding agents
FINALS.md                # Hackathon demo playbook
docker-compose.yml       # Qdrant + Ollama services
.cursor/mcp.json         # Example MCP client registration
```

### Ingest path

`ingest_repo` (MCP tool) and `senior-code-ingest` (CLI) call the same function, `pipeline.ingest_repo`.

1. `parser.parse_repo` reads each file with a supported extension (`.py`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.mts`, `.cts`, `.tsx`, `.go`, `.rs`, `.java`). It skips hidden, virtual-environment, build and vendor directories, `*.min.*` files and files larger than 1 MB.
2. The parser emits one `Symbol` for each definition, import and call. A call symbol records its enclosing definition.
3. `chunker.chunk_repo` keeps only the definitions. Each chunk holds the source text, the line range, the docstring (or the doc comment above the definition) and the language.
4. `vectors.upsert_chunks` sends the chunk text to Ollama in batches of 32. Then it writes the vectors to Qdrant.
5. `graph.build_graph` makes the call and import edges. `save_graph` writes them to `graph.json`.

### Query path

| Tool | Data source | Result |
| --- | --- | --- |
| `search_similar_code` | Qdrant | Top-k chunks with score. The text is cut to 80 lines or 2,000 characters. |
| `search_related_code` | `graph.json` | Nodes within `hops` of the given names. The search follows edges in both directions. |
| `search_context` | Qdrant, then `graph.json` | Vector hits, then the graph neighbors of their names (hop ≥ 1). |
| `store_stats` | Both | Vector count, node count and edge count. |
| `doctor` | Qdrant, Ollama, `graph.json` | One status for each prerequisite. This tool does not write data. |

### Data model

**Qdrant collection** (`code_chunks`, cosine distance):

- One point for each function, class or method.
- The point ID is a UUIDv5 of `path:start_line:end_line:name`. A second ingest of the same code replaces the points. It does not add copies.
- The payload holds `repo`, `path`, `name`, `kind`, `start_line`, `end_line`, `text` and `docstring`.
- The first embedding response sets the vector size (768 for `nomic-embed-text`).

**Graph file** (`graph.json`, NetworkX node-link JSON):

- Definition nodes use the qualified name as the key, for example `Class.method`.
- File nodes use the key `file:<path>`. Module nodes use the key `module:<name>`.
- A `calls` edge goes from the enclosing definition to the callee. The resolver matches the callee by its last name segment and uses the first match.
- An `imports` edge goes from a file node to a module node.

## Benchmarks

Proof, not vibes. Two questions matter for a retrieval tool: **does it find the right code (quality)**, and **does it cost the agent fewer tokens than the alternative (efficiency)**. We measure both, plus an external leaderboard number for the embedding core. All reproducible (`eval/`).

**1. Ablation vs grep — the tool's actual job.** Same 2 ingested repos (394 symbol chunks, 12 hand-labelled query→symbol tasks), semantic retrieval vs a BM25/grep lexical baseline over the identical corpus:

| Metric | Senior Code MCP (semantic) | grep / BM25 baseline |
| --- | --- | --- |
| Hit@1 | **58%** | 33% |
| Hit@3 | **75%** | 50% |
| MRR | **0.69** | 0.43 |
| Tokens to reach answer | **~1,100 / query** | ~3,200 / query |
| | | **→ 66% fewer tokens (2.9× less context)** |

~1.6× higher top-1 hit-rate **and** ~3× less code fed into context — the token savings and quality lift the tool exists to deliver. (Token cost is a char/4 proxy; the *ratio* is the point. Baseline reads whole files a grep hit points at; the tool returns ranked symbol chunks.)

**2. CoIR `cosqa` — official external benchmark.** Same schema/metrics as the CoIR + MTEB leaderboards, run on our exact retrieval core (`nomic-embed-text`, local via Ollama), 500 queries vs a 20,604-doc corpus:

| NDCG@10 | Recall@10 | Recall@100 | MAP@10 |
| --- | --- | --- | --- |
| 0.266 | 0.47 | 0.81 | 0.20 |

A defensible baseline for a **free, offline, no-API** embedder (code-specialized cloud models score higher — and none of them ship the graph layer this tool adds on top).

**Reproduce:**

```bash
uv sync --extra eval
uv run --extra eval python eval/retrieval_ab.py      # ablation vs grep (needs repos ingested)
uv run --extra eval python eval/coir_bench.py --task cosqa   # official CoIR number
```

## MCP tools (6)

| Tool | What it does |
| --- | --- |
| `search_similar_code` | Embeds a natural-language or code query, returns top-k matching symbols (path, lines, language, docstring, truncated source, score). Optional `language` filter. |
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
- **No vendor lock-in.** Open Python, stdlib `ast`, tree-sitter, Qdrant, NetworkX. Read every file in an evening.

## Quick start (~5 min)

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 qdrant/qdrant
ollama pull nomic-embed-text
uv sync
senior-code-ingest /path/to/any/repo
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

- Seven languages: Python (stdlib `ast`), JavaScript, TypeScript, TSX, Go, Rust, Java (tree-sitter). Other files are skipped. `tree-sitter` is pinned below 0.26 because 0.26.0 segfaults on node access (CPython 3.13, macOS arm64).
- Fully local: no hosted embeddings, no cloud vector DB.
- Single shared collection; multi-tenant separation is future work.
- No incremental ingest yet — re-running re-embeds the repo (idempotent via deterministic IDs, but not free).
- Each ingest rewrites `graph.json` from the current repo only. Qdrant keeps all repos, but the graph holds only the last repo ingested.
- Call edges resolve by the last name segment, within the caller's language family (JS/TS/TSX are one family). An explicit receiver that names a class (`B.save`, `Foo::new`, `this.save`) wins, then a definition in the same file. A receiver held in a variable (`other.save()`) has no type information, so it can link to the wrong `save`.
- `.gitignore` is applied through `git ls-files`. Outside a git work tree, or without `git` on `PATH`, only the fixed skip list applies.
- The `language` filter on `search_context` limits the vector hits and the graph seeds. Graph neighbours can be in another language of the same family (a TS function that calls a JS helper).

---

Built at the Cursor x Superteam Georgia hackathon (Tbilisi, Jul 22 2026) — built with Cursor, end to end, in one night.
