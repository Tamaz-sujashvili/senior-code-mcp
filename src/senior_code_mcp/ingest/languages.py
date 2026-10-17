"""Language registry: which files we parse, and how.

Python goes through the stdlib `ast` parser (`ingest.parser`). Every other
language goes through tree-sitter (`ingest.treesitter`), driven by a
`LanguageSpec` that names the grammar node types for definitions, calls
and imports. Adding a language = one grammar package + one spec entry.

Layout mirrors codegraph-mcp's `code/languages.rs` (one config per
language, looked up by file extension), but uses node-type tables instead
of .scm queries so the walker can track the enclosing scope for each call.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Optional


# eq=False: hash by identity, so specs can key `load_grammar`'s cache.
@dataclass(frozen=True, eq=False)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    # tree-sitter grammar: (module, function returning the language pointer).
    # None = not parsed with tree-sitter (Python uses stdlib ast).
    grammar: Optional[tuple[str, str]] = None
    # node type -> symbol kind, for nodes that define a chunkable symbol.
    definitions: dict[str, str] = field(default_factory=dict)
    # node types that open a scope without being a symbol (e.g. Rust impl).
    # Value = field holding the scope name.
    scopes: dict[str, str] = field(default_factory=dict)
    # node type -> field holding the callee expression.
    calls: dict[str, str] = field(default_factory=dict)
    # node types that are imports.
    imports: tuple[str, ...] = ()
    # node types that wrap a definition (export, decorators); the doc comment
    # and the chunk range are taken from the outermost wrapper.
    wrappers: tuple[str, ...] = ()
    # node types skipped when looking backward for a doc comment.
    doc_skip: tuple[str, ...] = ()
    comment_types: tuple[str, ...] = ("comment",)


# Kinds whose children are members: a function defined inside one is a method.
CONTAINER_KINDS = {"class", "interface", "struct", "trait", "enum", "impl"}

_JS_DEFS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
    # `const f = () => ...` / `const f = function () {...}` and class fields
    # `save = () => ...`; kept only for function values (see the walker).
    "variable_declarator": "function",
    "field_definition": "method",
}
_JS_CALLS = {"call_expression": "function", "new_expression": "constructor"}
_JS_WRAPPERS = ("export_statement", "lexical_declaration", "variable_declaration")

_TS_DEFS = {
    **_JS_DEFS,
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "abstract_method_signature": "method",
    "method_signature": "method",  # interface members
    "public_field_definition": "method",  # `save = () => ...` in a class
}

LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(name="python", extensions=(".py",)),
    "javascript": LanguageSpec(
        name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        grammar=("tree_sitter_javascript", "language"),
        definitions=_JS_DEFS,
        calls=_JS_CALLS,
        imports=("import_statement",),
        wrappers=_JS_WRAPPERS,
    ),
    "typescript": LanguageSpec(
        name="typescript",
        extensions=(".ts", ".mts", ".cts"),
        grammar=("tree_sitter_typescript", "language_typescript"),
        definitions=_TS_DEFS,
        calls=_JS_CALLS,
        imports=("import_statement",),
        wrappers=_JS_WRAPPERS,
    ),
    "tsx": LanguageSpec(
        name="tsx",
        extensions=(".tsx",),
        grammar=("tree_sitter_typescript", "language_tsx"),
        definitions=_TS_DEFS,
        calls=_JS_CALLS,
        imports=("import_statement",),
        wrappers=_JS_WRAPPERS,
    ),
    "go": LanguageSpec(
        name="go",
        extensions=(".go",),
        grammar=("tree_sitter_go", "language"),
        definitions={
            "function_declaration": "function",
            "method_declaration": "method",
            "method_elem": "method",  # interface members
            # struct / interface / type, resolved in the walker.
            "type_spec": "type",
        },
        calls={"call_expression": "function"},
        imports=("import_spec",),
        wrappers=("type_declaration",),
    ),
    "rust": LanguageSpec(
        name="rust",
        extensions=(".rs",),
        grammar=("tree_sitter_rust", "language"),
        definitions={
            "function_item": "function",
            "function_signature_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "union_item": "struct",
            "trait_item": "trait",
            "type_item": "type",
        },
        scopes={"impl_item": "type", "mod_item": "name"},
        calls={"call_expression": "function", "macro_invocation": "macro"},
        imports=("use_declaration",),
        doc_skip=("attribute_item",),
        comment_types=("line_comment", "block_comment"),
    ),
    "java": LanguageSpec(
        name="java",
        extensions=(".java",),
        grammar=("tree_sitter_java", "language"),
        definitions={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "class",
            "annotation_type_declaration": "interface",
            "method_declaration": "method",
            "constructor_declaration": "method",
        },
        # method_invocation is handled in the walker (object + name fields).
        calls={"method_invocation": "name", "object_creation_expression": "type"},
        imports=("import_declaration",),
        comment_types=("line_comment", "block_comment"),
    ),
}

_BY_EXTENSION: dict[str, LanguageSpec] = {
    ext: spec for spec in LANGUAGES.values() for ext in spec.extensions
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_BY_EXTENSION)

# Languages that call each other directly share a family; call edges only
# resolve within one family, so `find` in Java never links to `find` in Go.
_FAMILIES = {"javascript": "ecmascript", "typescript": "ecmascript", "tsx": "ecmascript"}


def language_family(name: str) -> str:
    return _FAMILIES.get(name, name)


def detect_language(path: str | Path) -> Optional[LanguageSpec]:
    """Return the spec for a file by extension, or None if unsupported."""
    return _BY_EXTENSION.get(Path(path).suffix.lower())


@cache
def load_grammar(spec: LanguageSpec):
    """Import the grammar package and return a tree_sitter.Language.

    Imported lazily so a missing grammar only breaks its own language.
    """
    from tree_sitter import Language

    assert spec.grammar is not None, f"{spec.name} is not a tree-sitter language"
    module_name, func = spec.grammar
    module = importlib.import_module(module_name)
    return Language(getattr(module, func)())
