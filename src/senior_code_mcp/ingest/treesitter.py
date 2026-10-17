"""Tree-sitter parser for every non-Python language in `ingest.languages`.

Produces the same Symbol stream as the Python `ast` parser, so the
chunker / graph / vector layers do not care which language a file is in:

- definitions (function / method / class / interface / struct / enum /
  trait / type) with qualified name, line range, source and doc comment
- calls, with the enclosing definition's qualified name
- imports, with the imported module / path in `module`

The walk is iterative (explicit stack), so deeply nested or generated
code cannot hit Python's recursion limit.
"""

from __future__ import annotations

import re
from typing import Optional

from tree_sitter import Node, Parser

from .languages import CONTAINER_KINDS, LanguageSpec, load_grammar
from .parser import Symbol

# Function-valued initialisers that make `const f = ...` a function definition.
_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}

# Declarators that define a symbol only when their value is a function.
_JS_VALUE_DEFINITIONS = {"variable_declarator", "field_definition", "public_field_definition"}

# Callee expressions longer than this (IIFEs, chained lambdas) are skipped:
# they never resolve to a definition and only bloat the graph.
_MAX_CALLEE_CHARS = 120

_COMMENT_PREFIX = re.compile(r"^(//[/!]?|\*+|#)\s?")


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _qualified(scope: tuple[tuple[str, str], ...], name: str) -> str:
    return f"{scope[-1][1]}.{name}" if scope else name


def _first_of_type(node: Node, node_type: str) -> Optional[Node]:
    """First descendant (pre-order, including `node`) of the given type."""
    todo = [node]
    while todo:
        n = todo.pop()
        if n.type == node_type:
            return n
        todo.extend(reversed(n.children))
    return None


def _type_name(source: bytes, node: Optional[Node]) -> Optional[str]:
    """Bare type name, without generics or pointers: `*Repo[T]` -> `Repo`."""
    if node is None:
        return None
    ident = _first_of_type(node, "type_identifier")
    return _text(source, ident if ident is not None else node)


def _anchor(node: Node, spec: LanguageSpec) -> Node:
    """Climb through single-child wrappers (export, const, type groups).

    The anchor sets the chunk's line range and is where the doc comment is
    looked up, so `/** doc */ export const f = () => ...` keeps its doc.
    """
    while node.parent is not None and node.parent.type in spec.wrappers:
        members = [c for c in node.parent.named_children if c.type not in spec.comment_types]
        if len(members) != 1:
            break
        node = node.parent
    return node


def _clean_comment(raw: str) -> str:
    s = raw.strip()
    if s.startswith("/*"):
        s = s[2:]
        if s.endswith("*/"):
            s = s[:-2]
    lines = [_COMMENT_PREFIX.sub("", line.strip()) for line in s.splitlines()]
    return "\n".join(lines).strip()


def _doc_comment(anchor: Node, spec: LanguageSpec, source: bytes) -> Optional[str]:
    """Contiguous comments directly above the definition (no blank line)."""
    parts: list[str] = []
    row = anchor.start_point.row
    prev = anchor.prev_named_sibling
    while prev is not None:
        if prev.type in spec.doc_skip:  # e.g. Rust #[derive(...)]
            row = prev.start_point.row
        elif prev.type in spec.comment_types and prev.end_point.row >= row - 1:
            parts.append(_clean_comment(_text(source, prev)))
            row = prev.start_point.row
        else:
            break
        prev = prev.prev_named_sibling
    doc = "\n".join(reversed(parts)).strip()
    return doc or None


def _definition(
    node: Node,
    spec: LanguageSpec,
    scope: tuple[tuple[str, str], ...],
    source: bytes,
) -> Optional[tuple[str, str, str]]:
    """Return (kind, name, qualified) if `node` defines a symbol, else None."""
    kind = spec.definitions[node.type]
    # JS class fields name the member in `property`, everything else in `name`.
    name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
    if name_node is None:
        return None
    name = _text(source, name_node)

    if node.type in _JS_VALUE_DEFINITIONS:
        value = node.child_by_field_name("value")
        if value is None or value.type not in _JS_FUNCTION_VALUES:
            return None
    elif node.type == "type_spec":  # Go
        body = node.child_by_field_name("type")
        kind = {"struct_type": "struct", "interface_type": "interface"}.get(
            body.type if body is not None else "", "type"
        )
    elif node.type == "method_declaration" and spec.name == "go":
        # Go methods live at top level; qualify by receiver type instead.
        receiver = _type_name(source, node.child_by_field_name("receiver"))
        return "method", name, f"{receiver}.{name}" if receiver else name

    if kind == "function" and scope and scope[-1][0] in CONTAINER_KINDS:
        kind = "method"
    return kind, name, _qualified(scope, name)


def _callee(node: Node, spec: LanguageSpec, source: bytes) -> Optional[str]:
    if node.type == "method_invocation":  # Java: obj.name(...)
        name = node.child_by_field_name("name")
        obj = node.child_by_field_name("object")
        if name is None:
            return None
        callee = _text(source, name)
        if obj is not None:
            callee = f"{_text(source, obj)}.{callee}"
    elif node.type == "object_creation_expression":  # Java: new Foo<T>(...)
        callee = _type_name(source, node.child_by_field_name("type"))
        if callee is None:
            return None
    else:
        target = node.child_by_field_name(spec.calls[node.type])
        if target is None:
            return None
        callee = _text(source, target)
        if node.type == "macro_invocation":
            # Suffix keeps macros from resolving to same-named functions.
            callee += "!"
    callee = " ".join(callee.split())
    if not callee or len(callee) > _MAX_CALLEE_CHARS:
        return None
    return callee


def _string_value(source: bytes, node: Optional[Node]) -> Optional[str]:
    if node is None:
        return None
    return _text(source, node).strip("\"'`")


def _import_module(node: Node, source: bytes) -> Optional[str]:
    if node.type == "import_statement":  # JS / TS
        return _string_value(source, node.child_by_field_name("source"))
    if node.type == "import_spec":  # Go
        return _string_value(source, node.child_by_field_name("path"))
    if node.type == "use_declaration":  # Rust
        arg = node.child_by_field_name("argument")
        return "".join(_text(source, arg).split()) if arg is not None else None
    if node.type == "import_declaration":  # Java
        body = _text(source, node).removeprefix("import").strip().removesuffix(";")
        return body.removeprefix("static ").strip() or None
    return None


def _require_module(node: Node, source: bytes) -> Optional[str]:
    """`require("x")` -> "x" (CommonJS import), else None."""
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "identifier" or _text(source, fn) != "require":
        return None
    args = node.child_by_field_name("arguments")
    if args is None or args.named_child_count != 1 or args.named_children[0].type != "string":
        return None
    return _string_value(source, args.named_children[0])


def parse_source(source: bytes, path: str, spec: LanguageSpec) -> list[Symbol]:
    """Parse one file's bytes into Symbols. `path` is stored as given."""
    tree = Parser(load_grammar(spec)).parse(source)
    lines = source.decode("utf-8", errors="replace").splitlines(keepends=True)

    def symbol(node: Node, name: str, kind: str, **extra) -> Symbol:
        start, end = node.start_point.row + 1, node.end_point.row + 1
        return Symbol(
            name=name,
            kind=kind,
            path=path,
            start_line=start,
            end_line=end,
            source="".join(lines[start - 1 : end]),
            language=spec.name,
            **extra,
        )

    out: list[Symbol] = []
    todo: list[tuple[Node, tuple[tuple[str, str], ...]]] = [(tree.root_node, ())]
    while todo:
        node, scope = todo.pop()
        inner = scope
        t = node.type

        if t in spec.definitions and (d := _definition(node, spec, scope, source)):
            kind, name, qualified = d
            anchor = _anchor(node, spec)
            out.append(
                symbol(
                    anchor,
                    name,
                    kind,
                    docstring=_doc_comment(anchor, spec, source),
                    qualified=qualified,
                )
            )
            inner = scope + ((kind, qualified),)

        elif t in spec.scopes:
            name = _type_name(source, node.child_by_field_name(spec.scopes[t]))
            if name:
                kind = "impl" if t == "impl_item" else "module"
                inner = scope + ((kind, _qualified(scope, name)),)

        elif t in spec.imports:
            module = _import_module(node, source)
            if module:
                out.append(symbol(node, module, "import", module=module))

        elif t in spec.calls:
            if t == "call_expression" and (module := _require_module(node, source)):
                out.append(symbol(node, module, "import", module=module))
            elif callee := _callee(node, spec, source):
                out.append(
                    symbol(
                        node,
                        callee,
                        "call",
                        enclosing=scope[-1][1] if scope else None,
                        callee=callee,
                    )
                )

        todo.extend((child, inner) for child in reversed(node.children))
    return out
