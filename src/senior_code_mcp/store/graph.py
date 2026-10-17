"""Code relationship graph (NetworkX DiGraph).

Built from parsed Symbols:
- nodes: definitions (function / class / method) with name, kind, path,
  line range as attributes. File nodes and module nodes are added too so
  import edges have endpoints.
- edges:
    * `calls`: from the enclosing definition to the callee, resolved
      best-effort to a known definition node (by simple name), only within
      the caller's language family, preferring a definition in the same file.
    * `imports`: from a file node to a module node.

Persisted as JSON (networkx node-link format) so it survives restarts.
`expand` does a bounded BFS (following edges in both directions) from
symbols given by name and returns neighbor nodes within a hop count.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import networkx as nx

from ..ingest.languages import language_family
from ..ingest.parser import Symbol


def _file_node_id(path: str) -> str:
    return f"file:{path}"


def _module_node_id(module: str) -> str:
    return f"module:{module}"


_SEPARATORS = re.compile(r"\.|::|->")


def _simple(name: str) -> str:
    """Last segment of a dotted / `::` / `->` name (best-effort callee matching)."""
    return _SEPARATORS.split(name)[-1]


def _pick(g: nx.DiGraph, ids: list[str] | None, path: str) -> str | None:
    """First candidate in the same file, else the first candidate."""
    if not ids:
        return None
    return next((i for i in ids if g.nodes[i].get("path") == path), ids[0])


_SELF_RECEIVERS = {"this", "self", "Self", "cls"}


def _owner(qualified: str | None) -> str | None:
    """Containing scope of a qualified name: `A.run` -> `A`, `run` -> None."""
    parts = _SEPARATORS.split(qualified or "")
    return parts[-2] if len(parts) >= 2 else None


def _pick_callee(g: nx.DiGraph, ids: list[str] | None, call: Symbol) -> str | None:
    """Resolve a call target.

    An explicit receiver that names a scope (`B.save`, `Foo::new`, or
    `this.save` -> the caller's own class) wins; otherwise prefer the
    caller's file, then the first candidate.
    """
    if not ids:
        return None
    receiver = _owner(call.callee)
    if receiver in _SELF_RECEIVERS:
        receiver = _owner(call.enclosing)
    if receiver:
        owned = [i for i in ids if _owner(g.nodes[i].get("qualified")) == receiver]
        if owned:
            return _pick(g, owned, call.path)
    return _pick(g, ids, call.path)


def build_graph(symbols: Iterable[Symbol]) -> nx.DiGraph:
    """Build a NetworkX DiGraph from parsed Symbols."""
    g: nx.DiGraph = nx.DiGraph()

    defs: list[Symbol] = []
    calls: list[Symbol] = []
    imports: list[Symbol] = []
    files: dict[str, str] = {}
    for s in symbols:
        files[s.path] = s.language
        if s.is_definition:
            defs.append(s)
        elif s.kind == "call":
            calls.append(s)
        elif s.kind == "import":
            imports.append(s)

    # definition nodes (keyed by qualified name, with path fallback);
    # lookups are keyed by (language family, name)
    qualified_to_ids: dict[tuple[str, str], list[str]] = {}
    simple_to_ids: dict[tuple[str, str], list[str]] = {}
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
            language=d.language,
        )
        family = language_family(d.language)
        if d.qualified:
            qualified_to_ids.setdefault((family, d.qualified), []).append(node_id)
        simple_to_ids.setdefault((family, _simple(d.name)), []).append(node_id)

    # file + module nodes
    for fpath, language in files.items():
        g.add_node(
            _file_node_id(fpath),
            name=Path(fpath).name,
            kind="file",
            path=fpath,
            language=language,
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
        family = language_family(c.language)
        src = _pick(g, qualified_to_ids.get((family, c.enclosing)), c.path)
        if src is None:
            # fall back to simple-name match of the enclosing qualified tail
            src = _pick(g, simple_to_ids.get((family, _simple(c.enclosing))), c.path)
        if src is None:
            continue
        dst = _pick_callee(g, simple_to_ids.get((family, _simple(c.callee or ""))), c)
        if dst is None or src == dst:
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


def _resolve_starts(
    graph: nx.DiGraph,
    names: Iterable[str],
    language: str | None = None,
    paths: Iterable[str] | None = None,
) -> list[str]:
    path_set = set(paths) if paths is not None else None
    nodes = [
        (nid, attr)
        for nid, attr in graph.nodes(data=True)
        if (language is None or attr.get("language") == language)
        and (path_set is None or attr.get("path") in path_set)
    ]
    starts: list[str] = []
    for nm in names:
        # exact qualified match anywhere beats a simple-name match, so
        # "MemoryRepo.find" never resolves to some other class's `find`
        exact = (
            (nid for nid, attr in nodes if attr.get("qualified") == nm),
            (nid for nid, attr in nodes if attr.get("name") == nm),
            (nid for nid, attr in nodes if attr.get("name") == _simple(nm)),
        )
        for candidates in exact:
            nid = next(candidates, None)
            if nid is not None:
                starts.append(nid)
                break
    return starts


def expand(
    graph: nx.DiGraph,
    names: Iterable[str],
    hops: int = 1,
    language: str | None = None,
    paths: Iterable[str] | None = None,
) -> list[dict]:
    """BFS (both directions) from the named symbols, up to `hops` away.

    `language` / `paths` restrict which nodes a name may resolve to (the
    neighbours themselves are not filtered). Returns neighbor nodes with
    their attributes and the hop distance. Start nodes that resolved are
    included with hop 0.
    """
    return expand_from(graph, _resolve_starts(graph, names, language=language, paths=paths), hops)


def expand_hits(graph: nx.DiGraph, hits: Iterable[dict], hops: int = 1) -> list[dict]:
    """Expand from vector hits: each hit seeds every definition with its
    name in its own file (and language), so same-named hits in different
    files all expand and no other file's symbol is picked up."""
    starts: list[str] = []
    for h in hits:
        if not (h.get("name") and h.get("path")):
            continue
        for nid, attr in graph.nodes(data=True):
            if (
                attr.get("name") == h["name"]
                and attr.get("path") == h["path"]
                and (h.get("language") is None or attr.get("language") == h["language"])
                and nid not in starts
            ):
                starts.append(nid)
    return expand_from(graph, starts, hops)


def expand_from(graph: nx.DiGraph, starts: list[str], hops: int = 1) -> list[dict]:
    """BFS (both directions) from resolved node ids, up to `hops` away."""
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
                "language": attr.get("language"),
                "path": attr.get("path"),
                "start_line": attr.get("start_line"),
                "end_line": attr.get("end_line"),
                "hops": hop,
            }
        )
    out.sort(key=lambda r: (r["hops"], r["name"] or ""))
    return out
