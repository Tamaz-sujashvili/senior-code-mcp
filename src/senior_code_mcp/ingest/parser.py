"""Source parser (Python only) using the stdlib ast module.

Walks a repo, parses each .py file, and extracts symbol-level structure:

- functions, classes, methods  -> definition symbols (chunkable)
- imports                       -> import symbols (graph only)
- calls                         -> call symbols (graph only), with the
                                  enclosing function/class recorded so the
                                  graph can draw caller -> callee edges.

Every Symbol carries: name, kind, file path, start/end line (1-based),
source text, and docstring (for definitions). Calls additionally carry
enclosing (qualified name of the containing function/class/method) and
callee (the called name). Imports carry imported (dotted names) and
module (from-module, or None).

Note: this originally used tree-sitter, but the tree-sitter 0.26 Node
binding is use-after-free prone on CPython 3.13/3.14 + macOS arm64 and
segfaulted nondeterministically mid-walk (even in a clean subprocess). We
fall back to stdlib ast for Python, which is stable and keeps the same
Symbol output shape so the chunker / graph / vector layers are unchanged.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


def _qualified(stack: list[tuple[str, str]], name: str) -> str:
    return ".".join(n for _, n in stack) + (("." + name) if stack else name)


def _callee_name(node: ast.Call) -> str:
    """Readable callee name (e.g. foo, obj.method, pkg.mod.fn)."""
    try:
        return ast.unparse(node.func)
    except Exception:
        return "<call>"


def _import_names(node: ast.Import | ast.ImportFrom) -> tuple[Optional[str], list[str]]:
    if isinstance(node, ast.ImportFrom):
        module = node.module
        names = [a.name for a in node.names]
    else:  # ast.Import
        module = None
        names = [a.name for a in node.names]
    return module, names


def _slice_source(source_lines: list[str], start_line: int, end_line: int) -> str:
    return "".join(source_lines[start_line - 1 : end_line])


def _visit(
    node: ast.AST,
    path: str,
    source_lines: list[str],
    stack: list[tuple[str, str]],
    out: list[Symbol],
) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = child.name
            kind = "method" if stack and stack[-1][0] == "class" else "function"
            q = _qualified(stack, name)
            out.append(
                Symbol(
                    name=name,
                    kind=kind,
                    path=path,
                    start_line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    source=_slice_source(source_lines, child.lineno, child.end_lineno or child.lineno),
                    docstring=ast.get_docstring(child),
                    qualified=q,
                )
            )
            _visit(child, path, source_lines, stack + [(kind, q)], out)

        elif isinstance(child, ast.ClassDef):
            name = child.name
            q = _qualified(stack, name)
            out.append(
                Symbol(
                    name=name,
                    kind="class",
                    path=path,
                    start_line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    source=_slice_source(source_lines, child.lineno, child.end_lineno or child.lineno),
                    docstring=ast.get_docstring(child),
                    qualified=q,
                )
            )
            _visit(child, path, source_lines, stack + [("class", q)], out)

        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            module, imported = _import_names(child)
            display = module or (imported[0] if imported else "<import>")
            out.append(
                Symbol(
                    name=display,
                    kind="import",
                    path=path,
                    start_line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    source=_slice_source(source_lines, child.lineno, child.end_lineno or child.lineno),
                    module=module,
                    imported=imported,
                )
            )

        elif isinstance(child, ast.Call):
            callee = _callee_name(child)
            out.append(
                Symbol(
                    name=callee,
                    kind="call",
                    path=path,
                    start_line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    source=_slice_source(source_lines, child.lineno, child.end_lineno or child.lineno),
                    enclosing=stack[-1][1] if stack else None,
                    callee=callee,
                )
            )
            _visit(child, path, source_lines, stack, out)

        else:
            _visit(child, path, source_lines, stack, out)


def parse_file(path: str | Path) -> list[Symbol]:
    """Parse a single Python file into a list of Symbols."""
    p = Path(path)
    source = p.read_text(encoding="utf-8", errors="replace")
    source_lines = source.splitlines(keepends=True)
    try:
        tree = ast.parse(source, filename=str(p))
    except SyntaxError:
        return []
    out: list[Symbol] = []
    _visit(tree, str(p.resolve()), source_lines, [], out)
    return out


def parse_repo(path: str | Path) -> list[Symbol]:
    """Walk path and parse every .py file (skipping hidden/venv/build dirs).

    Returns a flat list of Symbols across all files.
    """
    root = Path(path).resolve()
    symbols: list[Symbol] = []
    if root.is_file():
        if root.suffix == ".py":
            symbols.extend(parse_file(root))
        return symbols
    for f in sorted(root.rglob("*.py")):
        # skip hidden/venv/build dirs; ".." (parent) is allowed
        if any(part in _SKIP_DIRS or (part.startswith(".") and part not in ("..", ".")) for part in f.parts):
            continue
        symbols.extend(parse_file(f))
    return symbols
