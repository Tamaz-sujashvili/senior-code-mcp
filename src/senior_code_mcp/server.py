"""MCP server entrypoint (stdio transport).

Exposes six tools to a coding agent over the MCP protocol:

- `search_similar_code(query, top_k)` -> semantic search via Qdrant.
  Returns vector hits with full payload + cosine score.
- `search_related_code(names, hops)` -> structural graph expansion.
  Loads the persisted graph and returns neighbor nodes within `hops`.
- `ingest_repo(path)` -> run the full ingest pipeline (parse, chunk,
  embed, upsert, build + save graph).
- `doctor()` -> read-only prerequisite self-diagnosis (onboarding first call).

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
def doctor() -> dict:
    """Self-diagnose prerequisites before any org-facing setup.

    The onboarding agent calls this first to learn what is already healthy
    and what is missing, so it only asks the org about the things that
    actually need a human decision. Non-destructive: never writes, never
    creates a collection, never pulls a model.

    Checks:
      - Qdrant reachable on the configured URL.
      - Target collection exists and its vector count.
      - Ollama reachable and the configured embed model is pulled.
      - Graph file exists with node and edge counts.

    Returns:
        Per-check status (ok bool + detail) and an overall `healthy` flag.
    """
    import httpx

    report: dict = {"healthy": True, "checks": {}}

    # Qdrant reachable + collection exists + vector count
    qd_ok, coll_ok, vec_count, qd_detail = False, False, None, ""
    client = None
    try:
        client = vectors._client()
        client.get_collections()
        qd_ok = True
        if client.collection_exists(vectors.QDRANT_COLLECTION):
            coll_ok = True
            vec_count = vectors.count_vectors()
    except Exception as e:  # noqa: BLE001
        qd_detail = f"{type(e).__name__}: {e}"
    report["checks"]["qdrant_reachable"] = {
        "ok": qd_ok,
        "url": vectors.QDRANT_URL,
        **({"error": qd_detail} if not qd_ok else {}),
    }
    report["checks"]["collection"] = {
        "ok": coll_ok,
        "name": vectors.QDRANT_COLLECTION,
        **({"vectors": vec_count} if coll_ok else {}),
        **({"error": "collection missing" if qd_ok and not coll_ok else qd_detail}
           if not coll_ok else {}),
    }

    # Ollama reachable + embed model present
    ol_ok, model_present, ol_detail = False, False, ""
    try:
        with httpx.Client(timeout=10.0) as http:
            resp = http.get(f"{vectors.OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            # Ollama tags models as `name:tag`; compare on the base name.
            names = {m.get("name", "").split(":")[0] for m in resp.json().get("models", [])}
        ol_ok = True
        model_present = vectors.EMBED_MODEL in names
    except Exception as e:  # noqa: BLE001
        ol_detail = f"{type(e).__name__}: {e}"
    report["checks"]["ollama_reachable"] = {
        "ok": ol_ok,
        "url": vectors.OLLAMA_URL,
        **({"error": ol_detail} if not ol_ok else {}),
    }
    report["checks"]["embed_model_present"] = {
        "ok": ol_ok and model_present,
        "model": vectors.EMBED_MODEL,
        **({"error": "model not pulled" if ol_ok and not model_present else ol_detail}
           if not (ol_ok and model_present) else {}),
    }

    # Graph file exists + node/edge counts
    gpath = pipeline.GRAPH_PATH
    graph_ok = gpath.exists()
    gnodes, gedges = 0, 0
    if graph_ok:
        try:
            g = load_graph(gpath)
            gnodes, gedges = g.number_of_nodes(), g.number_of_edges()
        except Exception as e:  # noqa: BLE001
            graph_ok = False
            ol_detail = f"{type(e).__name__}: {e}"
    report["checks"]["graph_file"] = {
        "ok": graph_ok,
        "path": str(gpath),
        **({"nodes": gnodes, "edges": gedges} if graph_ok else {}),
        **({"error": "graph file missing" if not gpath.exists() else ol_detail}
           if not graph_ok else {}),
    }

    report["healthy"] = all(c["ok"] for c in report["checks"].values())
    return report


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
