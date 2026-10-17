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
