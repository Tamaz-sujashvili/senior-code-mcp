"""MCP server entrypoint (stdio transport).

Exposes three tools to a coding agent over the MCP protocol:

- `search_similar_code(query, top_k)` -> semantic search via Qdrant.
  Returns vector hits with full payload + cosine score.
- `search_related_code(names, hops)` -> structural graph expansion.
  Loads the persisted graph and returns neighbor nodes within `hops`.
- `ingest_repo(path)` -> run the full ingest pipeline (parse, chunk,
  embed, upsert, build + save graph).

Run via the `senior-code-mcp` console script.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import pipeline
from .store import vectors
from .store.graph import expand, load_graph

mcp = FastMCP("senior-code-mcp")


@mcp.tool()
def search_similar_code(query: str, top_k: int = 5) -> list[dict]:
    """Semantic search: embed `query`, return top-k matching code chunks.

    Each result carries score + payload (path, name, kind, line range,
    docstring, text).
    """
    return vectors.search_similar(query, top_k=top_k)


@mcp.tool()
def search_related_code(names: list[str], hops: int = 1) -> list[dict]:
    """Structural search: expand the graph from `names` up to `hops` away.

    Returns neighbor nodes (callers/callees/imports) with hop distance.
    Requires the repo to have been ingested (graph.json present).
    """
    if not pipeline.GRAPH_PATH.exists():
        return [{"error": f"graph not found at {pipeline.GRAPH_PATH}; run ingest_repo first"}]
    g = load_graph(pipeline.GRAPH_PATH)
    return expand(g, names, hops=hops)


@mcp.tool()
def ingest_repo(path: str) -> dict:
    """Ingest a repo: parse, chunk, embed + upsert to Qdrant, build + save graph."""
    return pipeline.ingest_repo(path)


@mcp.tool()
def store_stats() -> dict:
    """Return store stats: vector count (Qdrant) + node/edge counts (graph)."""
    stats: dict = {
        "collection": vectors.QDRANT_COLLECTION,
        "vector_count": vectors.count_vectors(),
        "graph_path": str(pipeline.GRAPH_PATH),
        "graph_nodes": 0,
        "graph_edges": 0,
    }
    if pipeline.GRAPH_PATH.exists():
        g = load_graph(pipeline.GRAPH_PATH)
        stats["graph_nodes"] = g.number_of_nodes()
        stats["graph_edges"] = g.number_of_edges()
    return stats


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
