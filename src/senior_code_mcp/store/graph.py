"""Code relationship graph (NetworkX).

Built from tree-sitter parse data: nodes are symbols (functions/classes/
methods) and files; edges are calls, imports, and containment. Supports
neighbor expansion so the agent can pull in structurally related code
(callers, callees, imported defs) on top of a semantic hit.

Graph is persisted to disk (e.g. GraphML or pickle) and reloaded by the
server at startup.
"""


def build_graph(parsed_files) -> "Graph":
    """Build a NetworkX graph of symbols + relationships from parsed files.

    Stub: not implemented yet.
    """
    raise NotImplementedError


def load_graph(path: str) -> "Graph":
    """Load a persisted graph from disk.

    Stub: not implemented yet.
    """
    raise NotImplementedError


def expand_neighbors(graph, node_ids: list[str], depth: int = 1) -> list[dict]:
    """Return symbols related to `node_ids` within `depth` hops.

    Stub: not implemented yet.
    """
    raise NotImplementedError
