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


def _repo_name(path: str) -> str:
    """Derive a repo label from an ingest path (its directory/file name)."""
    return Path(path).resolve().name


def ingest_repo(path: str, reset: bool = False) -> dict:
    """Run the full ingest pipeline on `path`.

    By default this appends to the existing Qdrant collection (deterministic
    point ids keep upsert idempotent), so multiple repos can be ingested into
    one collection. Pass ``reset=True`` to drop + recreate the collection
    first (clean slate).

    Every chunk is tagged with the repo name (derived from `path`) as a
    payload field so search results show which repo they came from.
    """
    repo = _repo_name(path)
    symbols = parse_repo(path)
    chunks = chunk_repo(symbols)

    if reset:
        vectors.reset_collection()
    vectors.upsert_chunks(chunks, repo=repo)

    g = build_graph(symbols)
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_graph(g, GRAPH_PATH)

    return {
        "path": str(path),
        "repo": repo,
        "reset": reset,
        "symbols": len(symbols),
        "chunks": len(chunks),
        "collection": vectors.QDRANT_COLLECTION,
        "vector_size": vectors.vector_size(),
        "vector_count": vectors.count_vectors(),
        "graph_nodes": g.number_of_nodes(),
        "graph_edges": g.number_of_edges(),
        "graph_path": str(GRAPH_PATH),
    }
