"""Tree-sitter-based source parser (Python only).

Walks a repo, parses each `.py` file with the tree-sitter Python grammar,
and extracts symbol-level structure:

- functions, classes, methods  -> definition symbols (chunkable)
- imports                       -> import symbols (graph only)
- calls                         -> call symbols (graph only), with the
                                  enclosing function/class recorded so the
                                  graph can draw caller -> callee edges.

Every Symbol carries: name, kind, file path, start/end line (1-based),
source text, and docstring (for definitions). Calls additionally carry
`enclosing` (qualified name of the containing function/class/method) and
`callee` (the called name). Imports carry `imported` (dotted names) and
`module` (from-module, or None).
"""

from __future__ import annotations

import ast as _ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tree_sitter_python as _tspython
from tree_sitter import Language, Node, Parser

# Directories never descended into during a repo walk.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
    ".tox",
    "site-packages",
}

# Kinds that produce chunkable definitions.
_DEFINITION_KINDS = {"function", "class", "method"}

_PY_LANG = Language(_tspython.language())


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method | import | call
    path: str
    start_line: int  # 1-based
    end_line: int  # 1-based
    source: str
    docstring: Optional[str] = None
    # calls only:
    enclosing: Optional[str] = None  # qualified name of containing function/class/method
    callee: Optional[str] = None  # name being called
    # imports only:
    module: Optional[str] = None  # from-module for `from x import y`, else None
    imported: list[str] = field(default_factory=list)  # dotted names brought in
    # definitions only: qualified name (e.g. "Class.method", "func")
    qualified: Optional[str] = None

    @property
    def is_definition(self) -> bool:
        return self.kind in _DEFINITION_KINDS


def _new_parser() -> Parser:
    return Parser(_PY_LANG)


def _decode(text: bytes) -> str:
    return text.decode("utf-8", errors="replace")


def _string_literal(text: bytes) -> Optional[str]:
    """Best-effort decode of a python string-literal node's text to its value."""
    s = _decode(text)
    try:
        val = _ast.literal_eval(s)
        if isinstance(val, str):
            return val
    except Exception:
        pass
    return s


def _field_text(node: Node, field: str) -> str:
    child = node.child_by_field_name(field)
    return _decode(child.text) if child is not None else ""


def _qualified(stack: list[tuple[str, str]], name: str) -> str:
    return ".".join(n for _, n in stack) + (("." + name) if stack else name)


def _docstring(def_node: Node) -> Optional[str]:
    """Return the docstring of a function/class definition, if present."""
    body = def_node.child_by_field_name("body")
    if body is None:
        return None
    for child in body.children:
        if child.type == "expression_statement":
            inner = child.children[0] if child.children else None
            if inner is not None and inner.type == "string":
                return _string_literal(inner.text)
            break
        if child.is_named and child.type != "decorator":
            break
    return None


def _callee_name(call_node: Node) -> str:
    """Extract the called name from a `call` node (identifier or attribute)."""
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return "<call>"
    return _decode(fn.text)


def _import_names(node: Node) -> tuple[Optional[str], list[str]]:
    """Return (module, imported_names) for an import node."""
    module: Optional[str] = None
    imported: list[str] = []
    if node.type == "import_from_statement":
        mod = node.child_by_field_name("module_name")
        if mod is not None:
            module = _decode(mod.text)
    for child in node.children:
        t = child.type
        if t == "dotted_name":
            imported.append(_decode(child.text))
        elif t == "aliased_import":
            inner = child.child_by_field_name("name")
            if inner is not None:
                imported.append(_decode(inner.text))
        elif t == "wildcard_import":
            imported.append("*")
    return module, imported


def _make_def(node: Node, name: str, kind: str, path: str, qualified: str) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        path=path,
        start_line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        source=_decode(node.text),
        docstring=_docstring(node),
        qualified=qualified,
    )


def _visit(
    node: Node,
    path: str,
    stack: list[tuple[str, str]],
    out: list[Symbol],
) -> None:
    for child in node.children:
        t = child.type

        if t == "function_definition":
            name = _field_text(child, "name")
            kind = "method" if stack and stack[-1][0] == "class" else "function"
            q = _qualified(stack, name)
            sym = _make_def(child, name, kind, path, q)
            out.append(sym)
            _visit(child, path, stack + [(kind, q)], out)

        elif t == "class_definition":
            name = _field_text(child, "name")
            q = _qualified(stack, name)
            sym = _make_def(child, name, "class", path, q)
            out.append(sym)
            _visit(child, path, stack + [("class", q)], out)

        elif t in ("import_statement", "import_from_statement"):
            module, imported = _import_names(child)
            display = module or (imported[0] if imported else "<import>")
            sym = Symbol(
                name=display,
                kind="import",
                path=path,
                start_line=child.start_point.row + 1,
                end_line=child.end_point.row + 1,
                source=_decode(child.text),
                module=module,
                imported=imported,
            )
            out.append(sym)

        elif t == "call":
            callee = _callee_name(child)
            sym = Symbol(
                name=callee,
                kind="call",
                path=path,
                start_line=child.start_point.row + 1,
                end_line=child.end_point.row + 1,
                source=_decode(child.text),
                enclosing=stack[-1][1] if stack else None,
                callee=callee,
            )
            out.append(sym)
            _visit(child, path, stack, out)

        else:
            _visit(child, path, stack, out)


def parse_file(path: str | Path) -> list[Symbol]:
    """Parse a single Python file into a list of Symbols."""
    p = Path(path)
    source = p.read_bytes()
    parser = _new_parser()
    tree = parser.parse(source)
    out: list[Symbol] = []
    _visit(tree.root_node, str(p), [], out)
    return out


def parse_repo(path: str | Path) -> list[Symbol]:
    """Walk `path` and parse every `.py` file (skipping hidden/venv/build dirs).

    Returns a flat list of Symbols across all files.
    """
    root = Path(path)
    symbols: list[Symbol] = []
    if root.is_file():
        if root.suffix == ".py":
            symbols.extend(parse_file(root))
        return symbols
    for f in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS or part.startswith(".") for part in f.parts):
            continue
        symbols.extend(parse_file(f))
    return symbols
