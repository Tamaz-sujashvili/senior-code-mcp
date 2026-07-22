"""Qdrant vector store wrapper.

Owns the embedding + vector DB lifecycle:
- embed chunks (via an httpx-backed embedding endpoint, pluggable later)
- ensure the Qdrant collection exists with the right dimension
- upsert chunk vectors with payload (path, symbol, line range, text)
- search by query embedding, return top-k with payload

Connection config (Qdrant URL, collection name, embedding endpoint) comes
from environment / config, not hardcoded here.
"""


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the configured embedding endpoint.

    Uses httpx. Stub: not implemented yet.
    """
    raise NotImplementedError


def upsert_chunks(chunks: list[dict]) -> None:
    """Upsert chunk vectors + payload into Qdrant.

    Stub: not implemented yet.
    """
    raise NotImplementedError


def search_similar(query: str, top_k: int = 10) -> list[dict]:
    """Embed `query` and return the top-k similar chunks from Qdrant.

    Stub: not implemented yet.
    """
    raise NotImplementedError
