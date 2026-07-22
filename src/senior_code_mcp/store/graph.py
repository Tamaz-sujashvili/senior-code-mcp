"""Code relationship graph (NetworkX DiGraph).

Built from parsed Symbols:
- nodes: definitions (function / class / method) with name, kind, path,
  line range as attributes. File nodes and module nodes are added too so
  import edges have endpoints.
- edges:
    * `calls`: from the enclosing definition to the callee, resolved
      best-effort to a known definition node (by simple name).
    * `imports`: from a file node to a module node.

Persisted as JSON (networkx node-link format) so it survives restarts.
`expand` does a bounded BFS (following edges in both directions) from
symbols given by name and returns neighbor nodes within a hop count.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import networkx as nx

from ..ingest.parser import Symbol


def _file_node_id(path: str) -> str:
    return f"file:{path}"


def _module_node_id(module: str) -> str:
    return f"module:{module}"


def _simple(name: str) -> str:
    """Last segment of a dotted name (best-effort callee matching)."""
    return name.rsplit(".", 1)[-1]


def build_graph(symbols: Iterable[Symbol]) -> nx.DiGraph:
    """Build a NetworkX DiGraph from parsed Symbols."""
    g: nx.DiGraph = nx.DiGraph()

    defs: list[Symbol] = []
    calls: list[Symbol] = []
    imports: list[Symbol] = []
    files: set[str] = set()
    for s in symbols:
        files.add(s.path)
        if s.is_definition:
            defs.append(s)
        elif s.kind == "call":
            calls.append(s)
        elif s.kind == "import":
            imports.append(s)

    # definition nodes (keyed by qualified name, with path fallback)
    qualified_to_id: dict[str, str] = {}
    simple_to_ids: dict[str, list[str]] = {}
    for d in defs:
        node_id = d.qualified or f"{d.path}:{d.start_line}:{d.name}"
        if node_id in g:
            node_id = f"{node_id}@{d.path}:{d.start_line}"
        g.add_node(
            node_id,
            name=d.name,
            kind=d.kind,
            path=d.path,
            start_line=d.start_line,
            end_line=d.end_line,
            qualified=d.qualified or d.name,
        )
        if d.qualified:
            qualified_to_id.setdefault(d.qualified, node_id)
        simple_to_ids.setdefault(_simple(d.name), []).append(node_id)

    # file + module nodes
    for fpath in files:
        g.add_node(
            _file_node_id(fpath),
            name=Path(fpath).name,
            kind="file",
            path=fpath,
        )
    modules: set[str] = set()
    for imp in imports:
        if imp.module:
            modules.add(imp.module)
        for dotted in imp.imported:
            if not dotted.startswith(".") and "." not in dotted and not imp.module:
                modules.add(dotted)
    for m in modules:
        g.add_node(_module_node_id(m), name=m, kind="module")

    # call edges: enclosing -> callee (best-effort)
    for c in calls:
        if not c.enclosing:
            continue
        src = qualified_to_id.get(c.enclosing)
        if src is None:
            # fall back to simple-name match of the enclosing qualified tail
            src = (simple_to_ids.get(_simple(c.enclosing)) or [None])[0]
        if src is None:
            continue
        targets = simple_to_ids.get(_simple(c.callee or ""))
        if not targets:
            continue
        dst = targets[0]
        if src == dst:
            continue
        g.add_edge(src, dst, kind="calls")

    # import edges: file -> module
    for imp in imports:
        src = _file_node_id(imp.path)
        targets: list[str] = []
        if imp.module:
            targets.append(_module_node_id(imp.module))
        for dotted in imp.imported:
            if not dotted.startswith("."):
                targets.append(_module_node_id(dotted))
        for dst in targets:
            if g.has_node(dst):
                g.add_edge(src, dst, kind="imports")

    return g


def save_graph(graph: nx.DiGraph, path: str | Path) -> None:
    """Persist the graph as JSON (networkx node-link format)."""
    data = nx.node_link_data(graph)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_graph(path: str | Path) -> nx.DiGraph:
    """Load a graph previously saved by `save_graph`."""
    data = json.loads(Path(path).read_text())
    return nx.node_link_graph(data, directed=True)


def _resolve_starts(graph: nx.DiGraph, names: Iterable[str]) -> list[str]:
    starts: list[str] = []
    for nm in names:
        # exact qualified match first, then simple-name match
        for nid, attr in graph.nodes(data=True):
            q = attr.get("qualified")
            if (q and q == nm) or attr.get("name") == nm or _simple(nm) == attr.get("name"):
                starts.append(nid)
                break
    return starts


def expand(
    graph: nx.DiGraph,
    names: Iterable[str],
    hops: int = 1,
) -> list[dict]:
    """BFS (both directions) from the named symbols, up to `hops` away.

    Returns neighbor nodes (excluding the starts) with their attributes and
    the hop distance. Start nodes that resolved are included with hop 0.
    """
    starts = _resolve_starts(graph, names)
    seen: dict[str, int] = {s: 0 for s in starts}
    frontier: list[str] = list(starts)
    for hop in range(1, hops + 1):
        nxt: list[str] = []
        for nid in frontier:
            # successors + predecessors (treat as undirected for neighborhood)
            for nb in list(graph.successors(nid)) + list(graph.predecessors(nid)):
                if nb not in seen:
                    seen[nb] = hop
                    nxt.append(nb)
        frontier = nxt

    out: list[dict] = []
    for nid, hop in seen.items():
        attr = graph.nodes[nid]
        out.append(
            {
                "node": nid,
                "name": attr.get("name"),
                "kind": attr.get("kind"),
                "path": attr.get("path"),
                "start_line": attr.get("start_line"),
                "end_line": attr.get("end_line"),
                "hops": hop,
            }
        )
    out.sort(key=lambda r: (r["hops"], r["name"] or ""))
    return out
