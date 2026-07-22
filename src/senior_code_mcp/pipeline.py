"""Shared ingest pipeline used by both the MCP server and the CLI.

parse -> chunk -> embed + upsert to Qdrant -> build + save graph.
"""

from __future__ import annotations

import os
from pathlib import Path

from .ingest.chunker import chunk_repo
from .ingest.parser import parse_repo
from .store import vectors
from .store.graph import build_graph, save_graph

# Where the persisted graph lives. Default: <project_root>/graph.json
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = Path(os.getenv("GRAPH_PATH", _PROJECT_ROOT / "graph.json"))


def ingest_repo(path: str) -> dict:
    """Run the full ingest pipeline on `path`.

    Returns a summary dict. Resets the Qdrant collection first so each
    ingest is clean (deterministic point ids make upsert idempotent, but a
    fresh repo shouldn't mix with the previous one's vectors).
    """
    symbols = parse_repo(path)
    chunks = chunk_repo(symbols)

    vectors.reset_collection()
    vectors.upsert_chunks(chunks)

    g = build_graph(symbols)
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_graph(g, GRAPH_PATH)

    return {
        "path": str(path),
        "symbols": len(symbols),
        "chunks": len(chunks),
        "collection": vectors.QDRANT_COLLECTION,
        "vector_size": vectors.vector_size(),
        "graph_nodes": g.number_of_nodes(),
        "graph_edges": g.number_of_edges(),
        "graph_path": str(GRAPH_PATH),
    }
