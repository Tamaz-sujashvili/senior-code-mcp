"""JSON search CLI for agent harnesses that spawn a process per tool call.

Usage:
    senior-code-search context --query Q [--top-k N] [--hops H] [--language L] [--repo R]
    senior-code-search similar --query Q [--top-k N] [--language L] [--repo R]
    senior-code-search related --name A [--name B ...] [--hops H] [--language L]
    senior-code-search outline --path FILE
    senior-code-search symbol --path FILE --name QUALIFIED_OR_NAME

Prints one JSON document on stdout and exits 0. On any failure, including
invalid arguments, prints `{"error": "..."}` on stdout and exits 2, so
callers can show the reason without parsing stderr. Same data as the MCP
tools in `server.py`. `--repo` limits vector hits to one ingested repo name
(the directory name given to `senior-code-ingest`). `outline` and `symbol`
parse FILE at call time (no index needed): `outline` lists its definitions
with line ranges, `symbol` returns the exact source of one definition.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from pathlib import Path

from . import server
from .ingest.parser import parse_file


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

    outline = sub.add_parser("outline", help="definitions in one file, with line ranges")
    outline.add_argument("--path", required=True)

    symbol = sub.add_parser("symbol", help="exact source of one definition")
    symbol.add_argument("--path", required=True)
    symbol.add_argument("--name", required=True)
    return parser


def _definitions(path: str) -> list:
    """Definitions of one file inside the current directory.

    The real path (symlinks resolved) must stay inside the current
    directory: harnesses run this CLI from the session workspace and rely on
    it not to read files outside it.
    """
    root = Path.cwd().resolve()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise PermissionError(f"path is outside the workspace: {path}")
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    return [s for s in parse_file(p, root=root) if s.is_definition]


def outline(path: str) -> list[dict]:
    """Definitions of one file in source order (no source text)."""
    return [
        {
            "name": s.name,
            "qualified": s.qualified or s.name,
            "kind": s.kind,
            "language": s.language,
            "start_line": s.start_line,
            "end_line": s.end_line,
        }
        for s in sorted(_definitions(path), key=lambda s: (s.start_line, -s.end_line))
    ]


def _with_decorators(lines: list[str], start_line: int) -> int:
    """First line of a Python definition including its decorator lines."""
    i = start_line - 1
    while i > 0 and lines[i - 1].lstrip().startswith("@"):
        i -= 1
    return i + 1


def symbol(path: str, name: str) -> dict:
    """Exact source of the definition whose qualified name (else name) matches.

    Several definitions can share a name (TypeScript overload signatures,
    Python redefinitions): the one with the largest line span is returned,
    because it holds the implementation body, and `other_ranges` lists the
    rest. A Python result includes its decorator lines.
    """
    defs = _definitions(path)
    matches = [s for s in defs if s.qualified == name] or [s for s in defs if s.name == name]
    if not matches:
        known = sorted({s.qualified or s.name for s in defs})[:40]
        raise LookupError(f"no definition named {name!r} in {path}; known: {', '.join(known)}")
    match = max(matches, key=lambda s: (s.end_line - s.start_line, -s.start_line))
    start, source = match.start_line, match.source
    if match.language == "python":
        lines = (Path.cwd().resolve() / match.path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        start = _with_decorators(lines, match.start_line)
        source = "".join(lines[start - 1 : match.end_line])
    return {
        "path": match.path,
        "name": match.name,
        "qualified": match.qualified or match.name,
        "kind": match.kind,
        "language": match.language,
        "start_line": start,
        "end_line": match.end_line,
        "source": source,
        "other_ranges": [[s.start_line, s.end_line] for s in matches if s is not match],
    }


def run(argv: list[str] | None = None) -> object:
    args = _parser().parse_args(argv)
    if args.command == "context":
        return server.search_context(
            args.query, top_k=args.top_k, hops=args.hops, language=args.language, repo=args.repo
        )
    if args.command == "similar":
        return server.search_similar_code(args.query, top_k=args.top_k, language=args.language, repo=args.repo)
    if args.command == "outline":
        return outline(args.path)
    if args.command == "symbol":
        return symbol(args.path, args.name)
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
