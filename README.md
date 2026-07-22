# Senior Code MCP

MCP server that lets a coding agent search a codebase two ways:

- **Semantic similarity** — Qdrant vector store over symbol-level chunks.
- **Structural relationships** — a graph of calls/imports/containment built
  with tree-sitter, expanded via NetworkX.

Point: agent reuses existing patterns instead of guessing, and we stop
pasting whole files into context.

## Layout

```
src/senior_code_mcp/
├── ingest/
│   ├── parser.py   # tree-sitter parse -> AST + symbols
│   └── chunker.py  # symbol-level chunking for embeddings
├── store/
│   ├── vectors.py   # Qdrant embed + upsert + search
│   └── graph.py     # NetworkX relationship graph + neighbor expansion
├── server.py        # MCP entrypoint (search_similar, search_related, ingest_repo)
└── cli.py           # `senior-code-ingest <path>`
```

## Status

Skeleton only — stubs with docstrings, no implementation yet.
