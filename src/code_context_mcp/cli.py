"""CLI entrypoint for ingestion.

Usage:
    code-context-ingest <path>

Runs the full ingest pipeline on `path`: parse (tree-sitter) -> chunk ->
embed + upsert to Qdrant -> build + persist graph. Intended to be run
once per repo (or on update) before the agent queries it.
"""

import argparse


def main(argv: list[str] | None = None) -> None:
    """Parse args and run the ingest pipeline on the given path.

    Stub: not implemented yet.
    """
    parser = argparse.ArgumentParser(prog="code-context-ingest")
    parser.add_argument("path", help="Path to the repo to ingest")
    parser.parse_args(argv)
    raise NotImplementedError
