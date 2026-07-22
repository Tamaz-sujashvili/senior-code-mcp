"""Source chunker.

Turns parsed files into embeddable chunks. Strategy: split at symbol
boundaries (function/class/method) from the tree-sitter AST, falling back
to a sliding character window when a symbol is too large. Each chunk keeps
metadata: file path, symbol name, byte/line range, language.

Chunks are the unit stored in Qdrant (one vector per chunk) and referenced
by graph nodes.
"""


def chunk_file(parsed_file) -> list[dict]:
    """Produce chunks for one parsed file.

    Returns list of chunk dicts: {id, path, symbol, start_line, end_line, text}.
    Stub: not implemented yet.
    """
    raise NotImplementedError


def chunk_repo(parsed_files) -> list[dict]:
    """Chunk every parsed file in the repo.

    Stub: not implemented yet.
    """
    raise NotImplementedError
