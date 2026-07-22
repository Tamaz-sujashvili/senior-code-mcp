"""CLI entrypoint for ingestion.

Usage:
    senior-code-ingest <path>

Runs the full ingest pipeline on `path`: parse (tree-sitter) -> chunk ->
embed + upsert to Qdrant -> build + persist graph.
"""

from __future__ import annotations

import argparse
import json

from . import pipeline


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="senior-code-ingest")
    parser.add_argument("path", help="Path to the repo (or .py file) to ingest")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop + recreate the collection first (default: append).",
    )
    args = parser.parse_args(argv)
    summary = pipeline.ingest_repo(args.path, reset=args.reset)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
