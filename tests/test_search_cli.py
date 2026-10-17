"""senior-code-search CLI contract (graph-only path; no Qdrant / Ollama)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from senior_code_mcp import pipeline, search_cli
from senior_code_mcp.ingest.parser import parse_repo
from senior_code_mcp.store.graph import build_graph, save_graph

FIXTURES = Path(__file__).parent / "fixtures" / "polyglot"


def test_related_prints_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    graph_path = tmp_path / "graph.json"
    save_graph(build_graph(parse_repo(FIXTURES)), graph_path)
    monkeypatch.setattr(pipeline, "GRAPH_PATH", graph_path)
    search_cli.main(["related", "--name", "MemoryRepo.new", "--language", "rust"])
    rows = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in rows} == {"new", "lookup"}


def test_missing_graph_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(pipeline, "GRAPH_PATH", tmp_path / "missing.json")
    with pytest.raises(SystemExit) as exc:
        search_cli.main(["related", "--name", "x"])
    assert exc.value.code == 2
    assert "graph not found" in json.loads(capsys.readouterr().out)["error"]


def test_argument_errors_are_json(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["related"], ["context"], ["bogus"], ["similar", "--query", "q", "--top-k", "x"]):
        with pytest.raises(SystemExit) as exc:
            search_cli.main(argv)
        assert exc.value.code == 2, argv
        assert "senior-code-search" in json.loads(capsys.readouterr().out)["error"], argv


def test_payload_filter() -> None:
    from senior_code_mcp.store.vectors import _payload_filter

    assert _payload_filter(language=None, repo=None) is None
    f = _payload_filter(language="go", repo="app")
    assert f is not None and [c.key for c in f.must] == ["language", "repo"]


def test_outline_and_symbol(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(FIXTURES)
    search_cli.main(["outline", "--path", "sample.rs"])
    rows = json.loads(capsys.readouterr().out)
    assert [r["qualified"] for r in rows][:3] == ["User", "Repo", "Repo.find"]
    assert all({"start_line", "end_line", "kind"} <= r.keys() for r in rows)

    search_cli.main(["symbol", "--path", "sample.rs", "--name", "MemoryRepo.new"])
    sym = json.loads(capsys.readouterr().out)
    assert sym["source"].strip().startswith("pub fn new() -> Self {")
    assert (sym["start_line"], sym["end_line"]) == (26, 28)

    with pytest.raises(SystemExit) as exc:
        search_cli.main(["symbol", "--path", "sample.rs", "--name", "nope"])
    assert exc.value.code == 2
    assert "known: " in json.loads(capsys.readouterr().out)["error"]


def test_outline_rejects_paths_outside_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "ws").mkdir()
    (tmp_path / "secret.py").write_text("def s():\n    pass\n")
    (tmp_path / "ws" / "link.py").symlink_to(tmp_path / "secret.py")
    monkeypatch.chdir(tmp_path / "ws")
    for path in ("../secret.py", "link.py", str(tmp_path / "secret.py")):
        with pytest.raises(SystemExit):
            search_cli.main(["outline", "--path", path])
        assert "outside the workspace" in json.loads(capsys.readouterr().out)["error"], path


def test_symbol_prefers_body_and_keeps_decorators(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "o.ts").write_text(
        "class A {\n  foo(x: string): void\n  foo(x: number): void\n  foo(x: any) {\n    return x\n  }\n}\n"
    )
    (tmp_path / "d.py").write_text("import functools\n\n@functools.cache\n@staticmethod\ndef f():\n    return 1\n")
    monkeypatch.chdir(tmp_path)
    search_cli.main(["symbol", "--path", "o.ts", "--name", "A.foo"])
    sym = json.loads(capsys.readouterr().out)
    assert (sym["start_line"], sym["end_line"]) == (4, 6)
    assert sym["other_ranges"] == [[2, 2], [3, 3]]
    search_cli.main(["symbol", "--path", "d.py", "--name", "f"])
    sym = json.loads(capsys.readouterr().out)
    assert sym["start_line"] == 3 and sym["source"].startswith("@functools.cache\n@staticmethod\ndef f():")


def test_upsert_batch_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    from senior_code_mcp.store import vectors

    monkeypatch.setattr(vectors, "UPSERT_BATCH", 0)
    with pytest.raises(ValueError, match="UPSERT_BATCH"):
        vectors.upsert_chunks([])
