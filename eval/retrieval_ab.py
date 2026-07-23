"""Ablation: senior-code-mcp retrieval vs a grep/BM25 baseline.

CoIR measures the embedding core's retrieval quality in isolation. This
harness measures the two things the *tool* actually claims:

1. Quality  - does semantic (dense) retrieval find the right symbol more
   often than a lexical baseline (BM25 over the same function-chunk corpus)?
   Metrics: Hit@1, Hit@3, MRR on a hand-labelled query -> symbol gold set.

2. Token efficiency - how many tokens of code does the agent have to read
   to reach the answer? The tool returns a few ranked chunks; a grep-style
   workflow points the agent at whole files it must read. We approximate the
   grep workflow with BM25 top-k files read in full, and compare token cost.

Token counts are char/4 approximations (no tokenizer dependency); the ratio
is what matters and is robust to the constant. This is an offline proxy for
the real agent-loop token cost (confirm live with a Cursor A/B if desired).

Run:
    uv run --extra eval python eval/retrieval_ab.py
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from senior_code_mcp.store import vectors

# Repo roots for the two ingested corpora (used to read full files for the
# grep-baseline token cost). `repo` matches the payload field set at ingest.
HACKATHON = Path(__file__).resolve().parents[2]
REPO_ROOTS = {
    "llm-council": HACKATHON / "llm-council",
    "py-clean-arch": HACKATHON / "py-clean-arch",
}

# Gold set: natural-language query -> the symbol that answers it.
# `path_contains` disambiguates when a simple name repeats across repos.
GOLD = [
    {"query": "how are peer rankings aggregated across models",
     "symbol": "calculate_aggregate_rankings", "path_contains": "council.py"},
    {"query": "send a single chat request to a language model",
     "symbol": "query_model", "path_contains": "openrouter.py"},
    {"query": "query several models in parallel",
     "symbol": "query_models_parallel", "path_contains": "openrouter.py"},
    {"query": "synthesize the final chairman answer from all responses",
     "symbol": "stage3_synthesize_final", "path_contains": "council.py"},
    {"query": "parse the final ranking section out of the model text",
     "symbol": "parse_ranking_from_text", "path_contains": "council.py"},
    {"query": "collect the first round of individual model responses",
     "symbol": "stage1_collect_responses", "path_contains": "council.py"},
    {"query": "handle an incoming user message over http",
     "symbol": "send_message", "path_contains": "main.py"},
    {"query": "list all pokemons endpoint",
     "symbol": "get_pokemons", "path_contains": "pokemon/router.py"},
    {"query": "create a new pokemon record",
     "symbol": "create_pokemon", "path_contains": "pokemon/router.py"},
    {"query": "trade a pokemon between two trainers",
     "symbol": "trade_pokemon", "path_contains": "trainer/router.py"},
    {"query": "start a database transaction session",
     "symbol": "__aenter__", "path_contains": "unit_of_work.py"},
    {"query": "map a create request body to a domain entity",
     "symbol": "create_request_to_entity", "path_contains": "mapper.py"},
]

TOP_K = 5
_WORD = re.compile(r"[A-Za-z0-9_]+")


def _tok(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _fetch_corpus() -> list[dict]:
    """Pull every stored chunk's payload from Qdrant (identical corpus for
    dense + BM25, so the comparison is apples-to-apples)."""
    client = vectors._client()
    out: list[dict] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=vectors.QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
        )
        out.extend(p.payload for p in points)
        if offset is None:
            break
    return out


class BM25:
    """Minimal BM25-Okapi over the chunk corpus (no external dependency)."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.len = [len(d) for d in docs]
        self.avgdl = sum(self.len) / self.N if self.N else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter = Counter()
        for d in docs:
            for w in set(d):
                df[w] += 1
        self.idf = {
            w: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for w, n in df.items()
        }

    def top(self, query: list[str], k: int) -> list[int]:
        scores = [0.0] * self.N
        for i in range(self.N):
            tf, dl = self.tf[i], self.len[i]
            s = 0.0
            for w in query:
                if w not in tf:
                    continue
                idf = self.idf.get(w, 0.0)
                num = tf[w] * (self.k1 + 1)
                den = tf[w] + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                s += idf * num / den
            scores[i] = s
        return sorted(range(self.N), key=lambda i: scores[i], reverse=True)[:k]


def _is_gold(payload: dict, g: dict) -> bool:
    return payload.get("name") == g["symbol"] and g["path_contains"] in (
        payload.get("path") or ""
    )


def _rank_of_gold(payloads: list[dict], g: dict) -> int | None:
    for rank, p in enumerate(payloads, start=1):
        if _is_gold(p, g):
            return rank
    return None


def _file_tokens(payload: dict) -> int:
    """Approx tokens of the full source file a grep hit would make the agent
    read (falls back to the chunk text if the file can't be located)."""
    repo = payload.get("repo")
    root = REPO_ROOTS.get(repo)
    rel = payload.get("path") or ""
    if root is not None:
        fp = root / rel
        if fp.exists():
            return _approx_tokens(fp.read_text(errors="ignore"))
    return _approx_tokens(payload.get("text") or "")


def main() -> None:
    corpus = _fetch_corpus()
    tokenized = [_tok(f"{c.get('name','')} {c.get('text','')}") for c in corpus]
    bm25 = BM25(tokenized)

    dense_hit1 = dense_hit3 = dense_mrr = 0.0
    bm25_hit1 = bm25_hit3 = bm25_mrr = 0.0
    dense_tokens_total = 0
    grep_tokens_total = 0
    n = len(GOLD)

    print(f"corpus chunks: {len(corpus)} | queries: {n} | top_k: {TOP_K}\n")
    print(f"{'query':44} {'dense':>6} {'bm25':>6}")

    for g in GOLD:
        # dense (the tool)
        dense_hits = vectors.search_similar(g["query"], top_k=TOP_K)
        d_rank = _rank_of_gold(dense_hits, g)
        # bm25 (lexical baseline) over identical corpus
        idxs = bm25.top(_tok(g["query"]), TOP_K)
        bm25_hits = [corpus[i] for i in idxs]
        b_rank = _rank_of_gold(bm25_hits, g)

        if d_rank:
            dense_hit1 += d_rank == 1
            dense_hit3 += d_rank <= 3
            dense_mrr += 1.0 / d_rank
        if b_rank:
            bm25_hit1 += b_rank == 1
            bm25_hit3 += b_rank <= 3
            bm25_mrr += 1.0 / b_rank

        # token cost to reach the answer:
        #  tool  -> the top_k ranked chunks it returns (already truncated)
        #  grep  -> full files behind the bm25 top-3 lexical matches (dedup)
        dense_tokens_total += sum(_approx_tokens(h.get("text") or "") for h in dense_hits)
        seen_files: set[str] = set()
        gtoks = 0
        for p in bm25_hits[:3]:
            key = f"{p.get('repo')}:{p.get('path')}"
            if key in seen_files:
                continue
            seen_files.add(key)
            gtoks += _file_tokens(p)
        grep_tokens_total += gtoks

        print(f"{g['query'][:44]:44} {str(d_rank or '-'):>6} {str(b_rank or '-'):>6}")

    def pct(x: float) -> str:
        return f"{100 * x / n:.0f}%"

    print("\n--- QUALITY (higher is better) ---")
    print(f"{'metric':10} {'dense(tool)':>12} {'bm25(grep)':>12}")
    print(f"{'Hit@1':10} {pct(dense_hit1):>12} {pct(bm25_hit1):>12}")
    print(f"{'Hit@3':10} {pct(dense_hit3):>12} {pct(bm25_hit3):>12}")
    print(f"{'MRR':10} {dense_mrr / n:>12.3f} {bm25_mrr / n:>12.3f}")

    print("\n--- TOKEN COST to reach answer (lower is better, ~char/4) ---")
    avg_dense = dense_tokens_total / n
    avg_grep = grep_tokens_total / n
    saved = 100 * (1 - avg_dense / avg_grep) if avg_grep else 0.0
    print(f"tool  (top-{TOP_K} chunks):   {avg_dense:8.0f} tokens/query")
    print(f"grep  (top-3 files read): {avg_grep:8.0f} tokens/query")
    print(f"reduction: {saved:.0f}%  ({avg_grep / max(avg_dense,1):.1f}x less context)")


if __name__ == "__main__":
    main()
