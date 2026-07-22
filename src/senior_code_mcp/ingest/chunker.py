"""Source chunker.

Takes the Symbols produced by `ingest.parser` and emits one chunk per
definition (function / class / method). Imports and calls are NOT chunked
-- they exist only to build graph edges.

Each chunk carries the metadata the store and the agent will need later:
id, file path, symbol name, kind, line range, source text, docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .parser import Symbol


@dataclass
class Chunk:
    id: str
    path: str
    name: str
    kind: str  # function | class | method
    start_line: int
    end_line: int
    text: str
    docstring: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
            "docstring": self.docstring,
        }


def chunk_symbol(sym: Symbol) -> Chunk:
    """Build a single Chunk from one definition Symbol."""
    cid = f"{sym.path}:{sym.start_line}:{sym.end_line}:{sym.name}"
    return Chunk(
        id=cid,
        path=sym.path,
        name=sym.name,
        kind=sym.kind,
        start_line=sym.start_line,
        end_line=sym.end_line,
        text=sym.source,
        docstring=sym.docstring,
    )


def chunk_file(symbols: Iterable[Symbol]) -> list[Chunk]:
    """Chunk the symbols of a single file (definitions only)."""
    return [chunk_symbol(s) for s in symbols if s.is_definition]


def chunk_repo(symbols: Iterable[Symbol]) -> list[Chunk]:
    """Chunk every definition symbol in a repo's symbol stream."""
    return [chunk_symbol(s) for s in symbols if s.is_definition]
