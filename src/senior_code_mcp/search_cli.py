"""JSON search CLI for agent harnesses that spawn a process per tool call.

Usage:
    senior-code-search context --query Q [--top-k N] [--hops H] [--language L] [--repo R]
    senior-code-search similar --query Q [--top-k N] [--language L] [--repo R]
    senior-code-search related --name A [--name B ...] [--hops H] [--language L]

Prints one JSON document on stdout and exits 0. On any failure, including
invalid arguments, prints `{"error": "..."}` on stdout and exits 2, so
callers can show the reason without parsing stderr. Same data as the MCP
tools in `server.py`. `--repo` limits vector hits to one ingested repo name
(the directory name given to `senior-code-ingest`).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import server


class _UsageError(Exception):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    """Raise on invalid arguments instead of printing usage and exiting."""

    def error(self, message: str):  # type: ignore[override]
        raise _UsageError(f"{self.prog}: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="senior-code-search")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_JsonArgumentParser)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--language", default=None, help="python, javascript, typescript, tsx, go, rust, java")

    context = sub.add_parser("context", help="semantic hits + graph neighbours")
    context.add_argument("--query", required=True)
    context.add_argument("--top-k", type=int, default=5)
    context.add_argument("--hops", type=int, default=1)
    context.add_argument("--repo", default=None)
    common(context)

    similar = sub.add_parser("similar", help="semantic hits only")
    similar.add_argument("--query", required=True)
    similar.add_argument("--top-k", type=int, default=5)
    similar.add_argument("--repo", default=None)
    common(similar)

    related = sub.add_parser("related", help="graph neighbours of symbols")
    related.add_argument("--name", action="append", required=True, dest="names")
    related.add_argument("--hops", type=int, default=1)
    common(related)
    return parser


def run(argv: list[str] | None = None) -> object:
    args = _parser().parse_args(argv)
    if args.command == "context":
        return server.search_context(
            args.query, top_k=args.top_k, hops=args.hops, language=args.language, repo=args.repo
        )
    if args.command == "similar":
        return server.search_similar_code(args.query, top_k=args.top_k, language=args.language, repo=args.repo)
    return server.search_related_code(args.names, hops=args.hops, language=args.language)


def main(argv: list[str] | None = None) -> None:
    # httpx logs every request at INFO; keep stdout pure JSON, stderr quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = run(argv)
    except SystemExit as e:  # --help exits 0 after printing usage
        if e.code in (0, None):
            raise
        print(json.dumps({"error": f"senior-code-search exited with {e.code}"}))
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(2)
    if isinstance(result, list) and result and isinstance(result[0], dict) and "error" in result[0]:
        print(json.dumps(result[0]))
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
