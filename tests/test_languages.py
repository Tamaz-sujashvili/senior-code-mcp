"""Parser + graph tests for every supported language (no Qdrant / Ollama)."""

from __future__ import annotations

from pathlib import Path

import pytest

from senior_code_mcp.ingest.chunker import chunk_repo
from senior_code_mcp.ingest.languages import LANGUAGES, SUPPORTED_EXTENSIONS, detect_language, language_family
from senior_code_mcp.ingest.parser import parse_file, parse_repo
from senior_code_mcp.store.graph import build_graph, expand

FIXTURES = Path(__file__).parent / "fixtures" / "polyglot"
SRC = Path(__file__).parents[1] / "src" / "senior_code_mcp"


def _defs(filename: str) -> dict[str, tuple[str, str | None]]:
    """qualified name -> (kind, docstring) for one fixture file."""
    syms = parse_file(FIXTURES / filename, root=FIXTURES)
    return {s.qualified: (s.kind, s.docstring) for s in syms if s.is_definition}


def _calls(filename: str) -> set[tuple[str | None, str | None]]:
    syms = parse_file(FIXTURES / filename, root=FIXTURES)
    return {(s.enclosing, s.callee) for s in syms if s.kind == "call"}


def _imports(filename: str) -> set[str | None]:
    syms = parse_file(FIXTURES / filename, root=FIXTURES)
    return {s.module for s in syms if s.kind == "import"}


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("a.py", "python"),
        ("a.js", "javascript"),
        ("a.mjs", "javascript"),
        ("a.ts", "typescript"),
        ("a.tsx", "tsx"),
        ("a.go", "go"),
        ("a.rs", "rust"),
        ("A.java", "java"),
        ("A.JAVA", "java"),
    ],
)
def test_detect_language(path: str, language: str) -> None:
    spec = detect_language(path)
    assert spec is not None and spec.name == language


def test_unsupported_extension() -> None:
    assert detect_language("notes.txt") is None
    assert parse_file(FIXTURES / ".." / "polyglot" / "missing.txt") == []


def test_every_grammar_loads() -> None:
    for spec in LANGUAGES.values():
        if spec.grammar is None:
            continue
        syms = parse_file(next(FIXTURES.glob(f"*{spec.extensions[0]}")))
        assert syms, spec.name


def test_javascript() -> None:
    defs = _defs("sample.js")
    assert defs["loadConfig"] == ("function", "Load and parse a JSON config file.")
    assert defs["greet"] == ("function", "Formats a user greeting.")  # arrow fn
    assert defs["UserStore"][0] == "class"
    assert defs["UserStore.find"] == ("method", "Find a user by id.")
    assert "path" not in defs  # `const path = require(...)` is not a function
    assert _imports("sample.js") == {"fs/promises", "path"}  # import + require
    assert ("UserStore.find", "loadConfig") in _calls("sample.js")


def test_typescript() -> None:
    defs = _defs("sample.ts")
    assert defs["User"] == ("interface", "A user record.")
    assert defs["UserId"][0] == "type"
    assert defs["Role"][0] == "enum"
    assert defs["UserService.getUser"] == ("method", "Fetch one user, or throw.")
    assert defs["handler"][0] == "function"
    assert _imports("sample.ts") == {"express", "./db"}
    calls = _calls("sample.ts")
    assert ("UserService.getUser", "validateUser") in calls
    assert ("UserService.getUser", "NotFoundError") in calls  # `new` expression


def test_tsx() -> None:
    defs = _defs("sample.tsx")
    assert defs["UserCard"] == ("function", "Renders a user's name.")
    assert ("UserCard", "formatTitle") in _calls("sample.tsx")


def test_go() -> None:
    defs = _defs("sample.go")
    assert defs["User"] == ("struct", "User is a stored user.")
    assert defs["Repo"] == ("interface", "Repo loads users.")
    assert defs["MemoryRepo.Find"] == ("method", "Find returns the user with id.")  # by receiver
    assert defs["Lookup"] == ("function", None)
    assert _imports("sample.go") == {"errors", "fmt"}
    assert ("Lookup", "NewMemoryRepo") in _calls("sample.go")


def test_rust() -> None:
    defs = _defs("sample.rs")
    assert defs["User"] == ("struct", "A stored user.")  # doc above #[derive]
    assert defs["Repo"] == ("trait", "Loads users.")
    assert defs["Repo.find"][0] == "method"  # trait signature
    assert defs["MemoryRepo.new"] == ("method", "Build an empty repo.")  # impl block
    assert defs["MemoryRepo.find"][0] == "method"  # trait impl
    assert defs["lookup"] == ("function", "Look a user up or fail.")
    assert _imports("sample.rs") == {"std::collections::HashMap", "crate::error::{Error,Result}"}
    assert ("lookup", "MemoryRepo::new") in _calls("sample.rs")


def test_java() -> None:
    defs = _defs("Sample.java")
    assert defs["Sample"] == ("class", "Keeps users in memory.")
    assert defs["Sample.Sample"][0] == "method"  # constructor
    assert defs["Sample.find"] == ("method", "Find a user name by id.")
    assert defs["Sample.Listener"][0] == "interface"
    assert _imports("Sample.java") == {"java.util.HashMap", "java.util.Map"}
    calls = _calls("Sample.java")
    assert ("Sample.find", "defaultName") in calls
    assert ("Sample", "HashMap") in calls  # generics stripped from `new HashMap<>()`


def test_line_ranges_and_source() -> None:
    syms = parse_file(FIXTURES / "sample.go", root=FIXTURES)
    lookup = next(s for s in syms if s.qualified == "Lookup")
    assert (lookup.start_line, lookup.end_line) == (38, 42)
    assert lookup.source.startswith("func Lookup(id string) string {")
    assert lookup.path == "sample.go"


def test_chunks_carry_language() -> None:
    chunks = chunk_repo(parse_repo(FIXTURES))
    langs = {c.language for c in chunks}
    assert langs == {"javascript", "typescript", "tsx", "go", "rust", "java"}
    assert all(c.to_dict()["language"] == c.language for c in chunks)


def test_graph_call_edges_across_languages() -> None:
    g = build_graph(parse_repo(FIXTURES))
    edges = {(u, v) for u, v, k in g.edges(data="kind") if k == "calls"}
    assert ("Lookup", "NewMemoryRepo") in edges  # go
    assert ("Sample.find", "Sample.defaultName") in edges  # java
    assert ("UserCard", "formatTitle") in edges  # tsx
    # rust `MemoryRepo::new` resolves through the `::` separator
    assert any(u == "lookup" and g.nodes[v]["name"] == "new" for u, v in edges)
    assert g.nodes["Lookup"]["language"] == "go"
    related = {r["name"] for r in expand(g, ["Lookup"], hops=1)}
    assert {"NewMemoryRepo", "Find"} <= related


def test_python_still_uses_ast() -> None:
    syms = parse_repo(SRC)
    defs = {s.qualified: s for s in syms if s.is_definition}
    assert defs["build_graph"].language == "python"
    assert defs["build_graph"].docstring == "Build a NetworkX DiGraph from parsed Symbols."
    assert "LanguageSpec" in defs


def test_skips_vendor_minified_and_large(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "index.js").write_text("function dep() {}\n")
    (tmp_path / "app.min.js").write_text("function minified() {}\n")
    (tmp_path / "big.js").write_text("function big() {}\n" + "//" * 600_000)
    (tmp_path / "app.js").write_text("function app() {}\n")
    names = {s.name for s in parse_repo(tmp_path) if s.is_definition}
    assert names == {"app"}


def test_supported_extensions_cover_registry() -> None:
    for spec in LANGUAGES.values():
        assert set(spec.extensions) <= SUPPORTED_EXTENSIONS


def test_calls_do_not_cross_language_families() -> None:
    g = build_graph(parse_repo(FIXTURES))
    for u, v, kind in g.edges(data="kind"):
        if kind == "calls":
            assert language_family(g.nodes[u]["language"]) == language_family(g.nodes[v]["language"]), (u, v)


def test_expand_prefers_exact_qualified_name() -> None:
    g = build_graph(parse_repo(FIXTURES))
    start = next(r for r in expand(g, ["MemoryRepo.find"], hops=0) if r["hops"] == 0)
    assert (start["language"], start["path"]) == ("rust", "sample.rs")


# --- regressions from the Codex review -------------------------------------


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_respects_gitignore(tmp_path: Path) -> None:
    import subprocess

    _write(tmp_path, {
        ".gitignore": "generated/\n",
        "generated/client.ts": "export function gen() {}\n",
        "src/app.ts": "export function app() {}\n",
    })
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    names = {s.name for s in parse_repo(tmp_path) if s.is_definition}
    assert names == {"app"}


def test_large_python_file_still_indexed(tmp_path: Path) -> None:
    _write(tmp_path, {"module.py": "def big():\n    pass\n" + "#" * 1_100_000 + "\n"})
    assert {s.name for s in parse_repo(tmp_path) if s.is_definition} == {"big"}


def test_interface_members(tmp_path: Path) -> None:
    _write(tmp_path, {
        "repo.ts": "interface Repo { find(id: string): string; }\n",
        "repo.go": "package p\ntype Repo interface { Find(id string) string }\n",
    })
    defs = {(s.language, s.qualified): s.kind for s in parse_repo(tmp_path) if s.is_definition}
    assert defs[("typescript", "Repo.find")] == "method"
    assert defs[("go", "Repo.Find")] == "method"


def test_class_field_arrow_functions(tmp_path: Path) -> None:
    _write(tmp_path, {
        "store.js": "class Store { save = () => 1; static n = 3; }\n",
        "store.ts": "class Store { private load = async (x: number) => x; }\n",
    })
    defs = {(s.language, s.qualified) for s in parse_repo(tmp_path) if s.is_definition}
    assert ("javascript", "Store.save") in defs
    assert ("typescript", "Store.load") in defs
    assert ("javascript", "Store.n") not in defs  # non-function field


def test_explicit_receiver_beats_same_file(tmp_path: Path) -> None:
    _write(tmp_path, {
        "a.js": "class B { static save() {} }\n",
        "z.js": "class A { save() {} run() { B.save(); this.save(); } }\n",
    })
    g = build_graph(parse_repo(tmp_path))
    targets = {v for u, v, k in g.edges(data="kind") if k == "calls" and u == "A.run"}
    assert targets == {"B.save", "A.save"}


def test_expand_restricted_to_language_and_paths(tmp_path: Path) -> None:
    _write(tmp_path, {
        "x.js": "function same() { jsOnly() }\nfunction jsOnly() {}\n",
        "x.go": "package p\nfunc same() { goOnly() }\nfunc goOnly() {}\n",
    })
    g = build_graph(parse_repo(tmp_path))
    for kwargs in ({"language": "go"}, {"paths": ["x.go"]}):
        related = {r["name"] for r in expand(g, ["same"], hops=1, **kwargs)}
        assert related == {"same", "goOnly"}, kwargs


def test_expand_hits_seeds_every_hit_file(tmp_path: Path) -> None:
    from senior_code_mcp.store.graph import expand_hits

    _write(tmp_path, {
        "a.js": "function same() { fromA() }\nfunction fromA() {}\n",
        "b.js": "function same() { fromB() }\nfunction fromB() {}\n",
        "c.js": "function same() { fromC() }\nfunction fromC() {}\n",
    })
    g = build_graph(parse_repo(tmp_path))
    hits = [
        {"name": "same", "path": "a.js", "language": "javascript"},
        {"name": "same", "path": "b.js", "language": "javascript"},
    ]
    related = {r["name"] for r in expand_hits(g, hits, hops=1) if r["hops"] > 0}
    assert related == {"fromA", "fromB"}  # c.js was not a hit


def test_receiver_match_prefers_same_file(tmp_path: Path) -> None:
    _write(tmp_path, {
        "a.js": "class B { static save() {} }\n",
        "z.js": "class B { static save() {} }\nclass A { run() { B.save(); } }\n",
    })
    g = build_graph(parse_repo(tmp_path))
    (target,) = [v for u, v, k in g.edges(data="kind") if k == "calls" and u == "A.run"]
    assert g.nodes[target]["path"] == "z.js"
