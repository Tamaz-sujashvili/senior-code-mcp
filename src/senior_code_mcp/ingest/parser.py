"""Tree-sitter-based source parser.

Walks a repo, parses supported languages (Python first) into ASTs, and
extracts symbol-level structure: definitions (functions/classes/methods),
call sites, and import edges. Output feeds both the chunker (text spans)
and the graph builder (relationships).

Will use: tree-sitter + tree-sitter-python language bindings.
"""


def parse_repo(path: str):
    """Parse every supported file under `path`.

    Returns an iterable of parsed-file records (path, language, tree, source).
    Stub: not implemented yet.
    """
    raise NotImplementedError


def parse_file(path: str):
    """Parse a single source file into a tree-sitter tree.

    Stub: not implemented yet.
    """
    raise NotImplementedError
