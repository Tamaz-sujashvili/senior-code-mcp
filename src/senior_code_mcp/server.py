"""MCP server entrypoint (stdio transport).

Exposes five tools to a coding agent over the MCP protocol:

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
def ingest_repo(path: str, reset: bool = False) -> dict:
    """Ingest a repo: parse, chunk, embed + upsert to Qdrant, build + save graph.

    Appends to the existing collection by default so multiple repos coexist;
    pass `reset=True` to drop + recreate the collection first. Every chunk is
    tagged with its repo name.
    """
    return pipeline.ingest_repo(path, reset=reset)


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


@mcp.tool()
def search_context(query: str, top_k: int = 5, hops: int = 1) -> dict:
    """Two-stage retrieval in one call: semantic search + graph expansion.

    Runs vector search for `query` (top-k), takes the symbol names from those
    hits, expands the graph one hop from them, and returns a combined result:
    `similar_chunks` (the vector hits) plus `related_symbols` (graph neighbors).
    """
    similar = vectors.search_similar(query, top_k=top_k)
    seed_names = list({s["name"] for s in similar if s.get("name")})

    related: list[dict] = []
    if seed_names and pipeline.GRAPH_PATH.exists():
        g = load_graph(pipeline.GRAPH_PATH)
        # expand returns seeds (hop 0) + neighbors; keep only the neighbors.
        related = [r for r in expand(g, seed_names, hops=hops) if r.get("hops", 0) > 0]

    return {
        "query": query,
        "seed_names": seed_names,
        "similar_chunks": similar,
        "related_symbols": related,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
