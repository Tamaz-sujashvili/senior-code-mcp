"""CoIR benchmark runner for the senior-code-mcp retrieval core.

Wraps the project's Ollama embedding function (`nomic-embed-text`) as a
BEIR/CoIR-style dense retriever and runs it against CoIR tasks, producing
official, leaderboard-comparable metrics (NDCG@10, MRR, Recall@k).

CoIR handles corpus indexing + scoring; we only provide the encoder, so this
benchmarks the exact embedding model the MCP tools retrieve with.

Usage:
    uv run --extra eval python eval/coir_bench.py --task cosqa
    uv run --extra eval python eval/coir_bench.py --task codesearchnet-python

Requires the `eval` extra (`coir-eval`) and a running Ollama with the embed
model pulled (see EMBED_MODEL in senior_code_mcp.store.vectors).
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from senior_code_mcp.store import vectors


class OllamaNomicModel:
    """Dense retriever wrapper exposing the CoIR/BEIR encode interface.

    nomic-embed-text expects task-typed prefixes; adding them materially
    improves retrieval quality, so we apply them here rather than in the
    shared `vectors.embed_texts` used by the live tools.
    """

    @staticmethod
    def _embed(texts: list[str]) -> np.ndarray:
        return np.asarray(vectors.embed_texts(texts), dtype=np.float32)

    def encode_queries(self, queries: list[str], batch_size: int = 32, **kwargs) -> np.ndarray:
        return self._embed([f"search_query: {q}" for q in queries])

    def encode_corpus(
        self, corpus: list[dict] | list[str], batch_size: int = 32, **kwargs
    ) -> np.ndarray:
        texts: list[str] = []
        for doc in corpus:
            if isinstance(doc, dict):
                body = f"{doc.get('title', '')} {doc.get('text', '')}".strip()
            else:
                body = doc
            texts.append(f"search_document: {body}")
        return self._embed(texts)


def run(task_name: str, batch_size: int, output_folder: str) -> dict:
    import coir
    from coir.data_loader import get_tasks
    from coir.evaluation import COIR

    tasks = get_tasks(tasks=[task_name])
    evaluation = COIR(tasks=tasks, batch_size=batch_size)
    results = evaluation.run(OllamaNomicModel(), output_folder=output_folder)
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="coir-bench")
    parser.add_argument(
        "--task",
        default="cosqa",
        help="CoIR task id (e.g. cosqa, codesearchnet, codetrans-dl).",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--output", default="eval/results")
    args = parser.parse_args(argv)

    results = run(args.task, args.batch_size, args.output)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
