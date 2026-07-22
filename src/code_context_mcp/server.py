"""MCP server entrypoint.

Exposes tools to a coding agent (e.g. Claude Code) over the MCP protocol:

- `search_similar_code(query, top_k)` -> semantic search via Qdrant.
- `search_related_code(symbol, depth)` -> structural graph expansion
  (callers/callees/imports) around a starting symbol.
- `ingest_repo(path)` -> run the ingest pipeline on a repo path so its
  vectors + graph become searchable.

The server wires ingest -> (vectors, graph) and serves read tools backed
by the store modules. Run via the `code-context-mcp` console script.
"""


def main() -> None:
    """Start the MCP server (stdio transport).

    Stub: not implemented yet.
    """
    raise NotImplementedError
