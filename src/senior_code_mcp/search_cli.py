"""JSON search CLI for agent harnesses that spawn a process per tool call.

Usage:
    senior-code-search context --query Q [--top-k N] [--hops H] [--language L]
    senior-code-search similar --query Q [--top-k N] [--language L]
    senior-code-search related --name A [--name B ...] [--hops H] [--language L]

Prints one JSON document on stdout and exits 0. On failure prints
`{"error": "..."}` on stdout and exits 2, so callers can show the reason
without parsing stderr. Same data as the MCP tools in `server.py`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="senior-code-search")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--language", default=None, help="python, javascript, typescript, tsx, go, rust, java")

    context = sub.add_parser("context", help="semantic hits + graph neighbours")
    context.add_argument("--query", required=True)
    context.add_argument("--top-k", type=int, default=5)
    context.add_argument("--hops", type=int, default=1)
    common(context)

    similar = sub.add_parser("similar", help="semantic hits only")
    similar.add_argument("--query", required=True)
    similar.add_argument("--top-k", type=int, default=5)
    common(similar)

    related = sub.add_parser("related", help="graph neighbours of symbols")
    related.add_argument("--name", action="append", required=True, dest="names")
    related.add_argument("--hops", type=int, default=1)
    common(related)
    return parser


def run(argv: list[str] | None = None) -> object:
    args = _parser().parse_args(argv)
    if args.command == "context":
        return server.search_context(args.query, top_k=args.top_k, hops=args.hops, language=args.language)
    if args.command == "similar":
        return server.search_similar_code(args.query, top_k=args.top_k, language=args.language)
    return server.search_related_code(args.names, hops=args.hops, language=args.language)


def main(argv: list[str] | None = None) -> None:
    # httpx logs every request at INFO; keep stdout pure JSON, stderr quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = run(argv)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(2)
    if isinstance(result, list) and result and isinstance(result[0], dict) and "error" in result[0]:
        print(json.dumps(result[0]))
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
