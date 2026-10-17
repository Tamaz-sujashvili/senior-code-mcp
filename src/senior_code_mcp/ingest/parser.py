"""Source parser: stdlib ast for Python, tree-sitter for everything else.

Walks a repo, parses every file in a supported language (see
`ingest.languages`), and extracts symbol-level structure:

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
Other languages (JS, TS/TSX, Go, Rust, Java) have no stdlib parser, so they
use tree-sitter via `ingest.treesitter`; its walk is iterative and keeps
no Node objects past the parse of one file.
"""

from __future__ import annotations

import ast
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .languages import SUPPORTED_EXTENSIONS, detect_language

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
    "target",  # Rust / Java build output
    "vendor",  # Go / PHP vendored deps
}

# Kinds that produce chunkable definitions.
_DEFINITION_KINDS = {"function", "class", "method", "interface", "struct", "enum", "trait", "type"}

# Non-Python files above this size are skipped (generated / vendored /
# minified code). Python keeps its original no-limit behaviour.
MAX_FILE_BYTES = 1_000_000


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method | interface | struct | enum | trait | type | import | call
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
    language: str = "python"

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


def _relative(p: Path, root: str | Path | None) -> str:
    if root is not None:
        try:
            return str(p.resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass
    return str(p.resolve())


def _parse_python(p: Path, rel: str) -> list[Symbol]:
    source = p.read_text(encoding="utf-8", errors="replace")
    source_lines = source.splitlines(keepends=True)
    try:
        tree = ast.parse(source, filename=str(p))
    except SyntaxError:
        return []
    out: list[Symbol] = []
    _visit(tree, rel, source_lines, [], out)
    return out


def parse_file(path: str | Path, root: str | Path | None = None) -> list[Symbol]:
    """Parse a single source file into a list of Symbols.

    The language is picked from the file extension; unsupported files
    return []. If `root` is given, the symbol `path` is stored relative to
    it; otherwise the absolute resolved path is used.
    """
    p = Path(path)
    spec = detect_language(p)
    if spec is None:
        return []
    rel = _relative(p, root)
    if spec.grammar is None:
        return _parse_python(p, rel)
    from .treesitter import parse_source  # imports Symbol from this module

    return parse_source(p.read_bytes(), rel, spec)


def _skip_file(f: Path) -> bool:
    spec = detect_language(f)
    if spec is None or ".min." in f.name:
        return True
    if spec.grammar is None:  # Python
        return not f.is_file()
    try:
        return f.stat().st_size > MAX_FILE_BYTES
    except OSError:
        return True


def _git_files(root: Path) -> list[Path] | None:
    """Files git would track under `root` (tracked + untracked, minus ignored).

    Returns None when `root` is not inside a git work tree or git is missing,
    so the caller falls back to a plain directory walk.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    names = proc.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [root / n for n in names if n]


def _walk_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune hidden/venv/build/vendor dirs in place so os.walk never
        # descends into them (node_modules can hold 100k+ files)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        files.extend(Path(dirpath, name) for name in filenames)
    return files


def parse_repo(path: str | Path) -> list[Symbol]:
    """Walk path and parse every supported source file.

    Inside a git work tree the file list comes from git, so `.gitignore`
    rules apply. Hidden / venv / build / vendor dirs, minified files and
    non-Python files over MAX_FILE_BYTES are always skipped. Returns a flat
    list of Symbols across all files, with paths stored relative to the
    repo root.
    """
    root = Path(path).resolve()
    symbols: list[Symbol] = []
    if root.is_file():
        if not _skip_file(root):
            symbols.extend(parse_file(root, root=root.parent))
        return symbols
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    for f in sorted(files):
        rel_dirs = f.relative_to(root).parts[:-1]
        if any(d in _SKIP_DIRS or d.startswith(".") for d in rel_dirs):
            continue
        if not _skip_file(f):
            symbols.extend(parse_file(f, root=root))
    return symbols
