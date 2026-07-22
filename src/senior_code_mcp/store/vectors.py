"""Qdrant vector store + Ollama embedding client.

Embeds text via Ollama (`nomic-embed-text`, POST /api/embed with httpx),
manages a Qdrant collection (`code_chunks`) whose vector size is taken
from the actual embedding response (768 for nomic-embed-text), upserts
chunks with full metadata as payload, and searches by query embedding.

Config (overridable via env):
- OLLAMA_URL        default http://localhost:11434
- EMBED_MODEL       default nomic-embed-text
- QDRANT_URL        default http://localhost:6333
- QDRANT_COLLECTION default code_chunks
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict
from typing import Any

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from ..ingest.chunker import Chunk

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "code_chunks")

# Detected from the first embedding response; nomic-embed-text = 768.
_vector_size: int | None = None


def _point_id(chunk_id: str) -> str:
    """Deterministic UUID for a chunk id (Qdrant point ids must be UUID/uint)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Ollama. Returns one vector per text.

    Sets the detected vector size from the response on first call.
    """
    if not texts:
        return []
    out: list[list[float]] = []
    # Ollama accepts a list under "input"; embed in modest batches to keep
    # request bodies reasonable.
    batch = 32
    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            resp = client.post(
                f"{OLLAMA_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": chunk},
            )
            resp.raise_for_status()
            data = resp.json()
            vectors = data["embeddings"]
            if _vector_size is None and vectors:
                _set_vector_size(len(vectors[0]))
            out.extend(vectors)
    return out


def _set_vector_size(n: int) -> None:
    global _vector_size
    _vector_size = n


def vector_size() -> int:
    """Return the embedding dimension, embedding a probe if not yet known."""
    if _vector_size is None:
        embed_texts(["probe"])
    assert _vector_size is not None
    return _vector_size


def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


def ensure_collection() -> None:
    """Create the Qdrant collection if it doesn't exist."""
    client = _client()
    if client.collection_exists(QDRANT_COLLECTION):
        return
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=vector_size(), distance=Distance.COSINE),
    )


def upsert_chunks(chunks: list[Chunk]) -> None:
    """Embed + upsert a list of Chunks into Qdrant, metadata as payload."""
    if not chunks:
        return
    ensure_collection()
    texts = [c.text for c in chunks]
    vectors = embed_texts(texts)
    client = _client()
    points = [
        PointStruct(
            id=_point_id(c.id),
            vector=vectors[i],
            payload={
                "id": c.id,
                "path": c.path,
                "name": c.name,
                "kind": c.kind,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "text": c.text,
                "docstring": c.docstring,
            },
        )
        for i, c in enumerate(chunks)
    ]
    client.upsert(collection_name=QDRANT_COLLECTION, points=points)


def _truncate(text: str | None, max_lines: int = 80, max_chars: int = 2000) -> str | None:
    """Trim chunk text to the first `max_lines` lines or `max_chars` chars."""
    if text is None:
        return None
    lines = text.splitlines(keepends=True)
    truncated = "".join(lines[:max_lines])
    if len(truncated) > max_chars:
        truncated = truncated[:max_chars]
    if len(lines) > max_lines or len(text) > len(truncated):
        truncated = truncated + "\n…[truncated]"
    return truncated


def search_similar(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Embed `query` and return the top-k matching chunks with scores.

    Chunk `text` is truncated (first 80 lines / 2000 chars) to keep responses
    small; the full text stays in the stored payload.
    """
    ensure_collection()
    qvec = embed_texts([query])[0]
    client = _client()
    resp = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=qvec,
        limit=top_k,
        with_payload=True,
    )
    hits = resp.points
    results: list[dict[str, Any]] = []
    for h in hits:
        payload = h.payload or {}
        results.append(
            {
                "score": float(h.score),
                "id": payload.get("id"),
                "path": payload.get("path"),
                "name": payload.get("name"),
                "kind": payload.get("kind"),
                "start_line": payload.get("start_line"),
                "end_line": payload.get("end_line"),
                "docstring": payload.get("docstring"),
                "text": _truncate(payload.get("text")),
            }
        )
    return results


def count_vectors() -> int:
    """Return the number of vectors stored in the collection."""
    client = _client()
    if not client.collection_exists(QDRANT_COLLECTION):
        return 0
    return client.count(collection_name=QDRANT_COLLECTION, exact=True).count


def reset_collection() -> None:
    """Drop + recreate the collection (used by smoke tests / re-ingest)."""
    client = _client()
    if client.collection_exists(QDRANT_COLLECTION):
        client.delete_collection(QDRANT_COLLECTION)
    ensure_collection()
